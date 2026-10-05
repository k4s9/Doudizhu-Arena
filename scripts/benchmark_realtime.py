"""Measure the real browser socket client against an isolated local mock match.

Run with the doudizhu-arena conda environment. This is a protocol/transport
benchmark, not a Vue rendering, real-model, or production-capacity benchmark.
The separate pressure phase blocks ONE server-side relay, then really fills
its default 10,000-event queue. It does not pretend that a non-reading browser
necessarily creates TCP backpressure. All fixture data and failures are kept.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "src/frontend/src/api/index.js"
SOURCES = [Path(__file__).resolve(), CLIENT, *sorted((ROOT / "src/backend/arena").rglob("*.py"))]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in SOURCES}


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "p50": None, "p95": None, "min": None, "max": None}
    return {"n": len(ordered), "p50": statistics.median(ordered),
            "p95": ordered[math.ceil(.95 * len(ordered)) - 1],
            "min": ordered[0], "max": ordered[-1]}


PAGE = """<!doctype html><meta charset="utf-8"><title>Socket benchmark</title>
<p>Local socket benchmark: production connection module; no Vue rendering.</p>
<script type="module">
import {createMatchSocket} from '/__bench/client.js';
const now = () => performance.timeOrigin + performance.now();
window.rows = []; window.raw = []; window.sockets = []; window.errors = [];
window.session = 0; window.ready = false;
const Native = window.WebSocket;
window.WebSocket = class extends Native {
  constructor(...args) {
    super(...args); const connection = ++window.session; window.sockets.push(this);
    this.addEventListener('message', e => {
      const data = JSON.parse(e.data);
      window.raw.push({connection, received_ms: now(), event: data});
    });
    this.addEventListener('close', e => {
      window.raw.push({connection, received_ms: now(), close_code: e.code});
    });
  }
};
window.calibrate = async () => {
  const samples = [];
  for (let i = 0; i < 12; i++) {
    const before = now();
    const result = await (await fetch('/__bench/clock', {cache: 'no-store'})).json();
    const after = now();
    samples.push({before, after, server_ms: result.server_ms,
      offset_ms: result.server_ms - (before + after) / 2, rtt_ms: after - before});
  }
  return samples;
};
window.disconnect = () => {
  const before = window.rows.filter(r => Number.isInteger(r.event.seq)).at(-1);
  const point = {started_ms: now(), connection: window.session,
    before_seq: before?.event.seq ?? window.rows.at(-1).event.payload.watermark};
  window.sockets.at(-1).close();
  return point;
};
window.connect = (id, slow = false) => {
  if (slow) { window.slow = new WebSocket(`ws://${location.host}/ws/match/${id}?benchmark_slow=1`); }
  else { window.client = createMatchSocket(id, {
    onEvent(event) { window.rows.push({connection: window.session, received_ms: now(), event}); }
  }); }
};
window.ready = true;
</script>"""


def serve(args):
    """Test-only process: instrument production classes without editing them."""
    sys.path.insert(0, str(ROOT / "src/backend"))
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, Response
    import uvicorn
    from arena.agent.llm_agent import LLMAgent
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.mock import ReliabilityMockProvider
    from arena.llm.claude import ClaudeProvider
    from arena.llm.openai import OpenAIProvider
    from arena.tournament.match import MatchConfig, MatchRunner
    from arena.tournament.seating import assign_seating
    import arena.api.event_bus as event_module
    import arena.api.ws as ws_module

    metrics = {"phase": "startup", "published": [], "snapshots": [], "model_waits": [],
               "real_provider_attempts": 0, "slow": {}, "external_connect_attempts": []}
    # No provider configuration/credentials are loaded by this fixture app.
    async def deny(*unused, **kwargs):
        metrics["real_provider_attempts"] += 1
        raise AssertionError("Real providers are disabled in the realtime benchmark")
    for provider in (OpenAIProvider, ClaudeProvider):
        provider.generate = provider.generate_turn = deny
    original_connect = socket.socket.connect
    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
            metrics["external_connect_attempts"].append(str(address))
            raise AssertionError("Only loopback connections are allowed")
        return original_connect(sock, address)
    socket.socket.connect = local_only
    gate = asyncio.Event()
    gate.set()
    release_slow = asyncio.Event()
    state = {}

    class TimedMock(ReliabilityMockProvider):
        async def generate(self, *params, **kwargs):
            await gate.wait()
            start = time.perf_counter()
            await asyncio.sleep(args.mock_delay)
            delayed_ms = (time.perf_counter() - start) * 1000
            # A test-only checkpoint barrier, outside the measured mock wait.
            await gate.wait()
            result = await super().generate(*params, **kwargs)
            metrics["model_waits"].append({"delay_ms": delayed_ms, "phase": metrics["phase"]})
            return result

    class MeasuredBus(event_module.MatchEventBus):
        def snapshot(self):
            result = super().snapshot()
            metrics["snapshots"].append({"captured_ms": time.time_ns() / 1e6,
                                         "snapshot": result})
            return result

        async def put(self, event):
            event = {**event, "_benchmark": {"publish_ms": time.time_ns() / 1e6,
                                             "phase": metrics["phase"]}}
            await super().put(event)
            metrics["published"].append({"seq": self.seq, "type": event["type"],
                "phase": metrics["phase"], "publish_ms": event["_benchmark"]["publish_ms"]})
            slow = state.get("slow_sub")
            if slow:
                evidence = metrics["slow"]
                evidence["max_queue_size"] = max(evidence.get("max_queue_size", 0), slow.queue.qsize())
                if slow not in self._subscribers and not evidence.get("detached"):
                    evidence.update(detached=True, detached_at_seq=self.seq,
                                    remaining_queue_size=slow.queue.qsize())

    event_module.MatchEventBus = ws_module.MatchEventBus = MeasuredBus
    original_relay = ws_module._relay_events
    async def relay(ws, subscription, watermark=0):
        if ws.query_params.get("benchmark_slow") == "1":
            state["slow_sub"] = subscription
            metrics["slow"].update(capacity=subscription.queue.maxsize,
                initial_watermark=watermark, injection="server relay suspended before subscription.get")
            await release_slow.wait()
        await original_relay(ws, subscription, watermark)
    ws_module._relay_events = relay

    @asynccontextmanager
    async def lifespan(app):
        repo = DatabaseRepository(str(args.output / "realtime.db"))
        repo.init()
        app.state.db_repo = repo
        app.state.active_matches = {}
        config_id = repo.create_player_config("realtime fixture", "random", "mock", "")
        ids = [repo.create_player(config_id, f"fixture-{i}") for i in range(8)]
        seating = assign_seating(ids[:4], ids[4:])
        config = MatchConfig(total_hands=100, max_tiebreaker_hands=0, ko_enabled=False,
            seed=args.seed, enable_reflection=False, enable_summary=False,
            persist_long_term_memory=False)
        mid = repo.create_match("Realtime benchmark fixture", {"total_hands": 100, "seed": args.seed}, args.seed)
        for seat, pid in seating.table_a.items():
            repo.add_participant(mid, pid, seating.table_a_teams[seat], seat_table_a=seat)
        for seat, pid in seating.table_b.items():
            repo.add_participant(mid, pid, seating.table_b_teams[seat], seat_table_b=seat)
        agents = {pid: LLMAgent(pid, TimedMock("valid")) for pid in ids}
        runner = MatchRunner(config, seating, agents, db_repo=repo)
        runner.match_id = mid
        app.state.active_matches[mid] = runner
        state.update(runner=runner, repo=repo, match_id=mid)
        task = asyncio.create_task(runner.run())
        try:
            yield
        finally:
            gate.set()
            release_slow.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            metrics["fixture_shutdown_status"] = repo.get_match(mid)["status"]
            save(args.output / "server-samples.json", metrics)
            repo.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_module.router)

    @app.get("/__bench/page")
    async def page():
        return HTMLResponse(PAGE)

    @app.get("/__bench/client.js")
    async def client():
        return Response(CLIENT.read_text(), media_type="application/javascript")

    @app.get("/__bench/clock")
    async def clock():
        return {"server_ms": time.time_ns() / 1e6}

    @app.get("/__bench/state")
    async def status():
        runner = state["runner"]
        return {"match_id": state["match_id"], "status": state["repo"].get_match(state["match_id"])["status"],
                "seq": runner.event_bus.seq if runner.event_bus else 0, "slow": metrics["slow"]}

    @app.post("/__bench/pressure")
    async def pressure():
        bus = state["runner"].event_bus
        slow = state["slow_sub"]
        assert bus.maxsize == slow.queue.maxsize == 10000
        assert slow in bus._subscribers
        metrics["phase"] = "backpressure"
        first = bus.seq + 1
        # Actual production fanout and SQLite persistence, not a substitute queue.
        for index in range(bus.maxsize + 1):
            await bus.put({"type": "benchmark_pressure_probe", "payload": {"index": index}})
            if index % 20 == 0:
                await asyncio.sleep(0)
        assert slow not in bus._subscribers, "Default queue never overflowed"
        assert slow.queue.qsize() == 1
        notification = slow.queue._queue[0]
        assert notification == {"type": "resync_required", "payload": {"reason": "slow_client"}}
        metrics["pressure"] = {"first_seq": first, "last_seq": bus.seq,
            "probe_count": bus.maxsize + 1, "probes_persisted": True,
            "queued_notification": notification, "release_ms": time.time_ns() / 1e6}
        release_slow.set()
        return metrics["pressure"]

    @app.post("/__bench/normal")
    async def normal():
        metrics["phase"] = "normal"
        return {"ok": True}

    @app.post("/__bench/checkpoint")
    async def checkpoint():
        metrics["phase"] = "checkpoint"
        gate.clear()
        await asyncio.sleep(args.mock_delay * 2 + .1)
        bus = state["runner"].event_bus
        previous = bus.seq
        await asyncio.sleep(.1)
        assert bus.seq == previous, "Fixture failed to become quiescent"
        snapshot = bus.snapshot()
        assert snapshot["status"] == "running"
        return snapshot

    uvicorn.run(app, host="127.0.0.1", port=args.serve, log_level="warning")


def process_sample(backend_pid):
    """Read only this controller's process tree, including thread-owned children.

    Scanning every host PID made instrumentation v1 itself a material load.
    Traversal starts at this fixture controller; it does not read unrelated
    process statistics. RSS sums still count shared pages more than once.
    """
    started = time.monotonic()
    rows = {}
    pending, visited = [os.getpid()], set()
    while pending:
        pid = pending.pop()
        if pid in visited:
            continue
        visited.add(pid)
        directory = Path("/proc") / str(pid)
        try:
            raw = (directory / "stat").read_text()
            tail = raw[raw.rfind(")") + 2:].split()
            rows[pid] = {"pid": pid, "ppid": int(tail[1]),
                "name": raw[raw.find("(") + 1:raw.rfind(")")],
                "cpu_ticks": int(tail[11]) + int(tail[12]),
                "start_ticks": int(tail[19]), "rss_pages": int(tail[21])}
            # Chromium can spawn a child from an IO thread. Reading only the
            # main task's children would undercount its process tree.
            for children in (directory / "task").glob("*/children"):
                try:
                    pending.extend(int(child) for child in children.read_text().split())
                except (OSError, ValueError):
                    pass  # A thread/process may exit while it is sampled.
        except (OSError, ValueError, IndexError):
            pass
    def group(pid):
        if pid == backend_pid:
            return "backend"
        if pid == os.getpid():
            return "controller"
        chain, cursor = [], pid
        while cursor in rows and cursor not in chain:
            chain.append(cursor)
            if cursor == os.getpid():
                names = [rows[p]["name"].lower() for p in chain]
                return "chromium" if any("chrome" in n or "chromium" in n for n in names) else "browser_driver"
            cursor = rows[cursor]["ppid"]
        return None
    selected = []
    for pid, row in rows.items():
        label = group(pid)
        if label:
            selected.append({**row, "group": label})
    finished = time.monotonic()
    return {"monotonic": finished, "scan_ms": (finished - started) * 1000,
            "processes": selected}


def resource_summary(samples, phase):
    ticks = os.sysconf("SC_CLK_TCK")
    page_bytes = os.sysconf("SC_PAGE_SIZE")
    result = {}
    for group in ("backend", "chromium", "controller", "browser_driver"):
        cpu, memory, previous = [], [], None
        for sample in samples:
            if sample["phase"] != phase:
                # Do not turn two normal intervals separated by pressure into
                # a CPU delta which silently includes the intervening load.
                previous = None
                continue
            rows = {p["pid"]: p for p in sample["processes"] if p["group"] == group}
            memory.append(sum(p["rss_pages"] * page_bytes for p in rows.values()) / 1024 ** 2)
            if previous:
                dt = sample["monotonic"] - previous[0]
                delta = sum(max(0, p["cpu_ticks"] - previous[1][pid]["cpu_ticks"])
                    for pid, p in rows.items() if pid in previous[1]
                    and p["start_ticks"] == previous[1][pid]["start_ticks"])
                cpu.append(delta / ticks / dt * 100)
            previous = (sample["monotonic"], rows)
        result[group] = {"cpu_percent_one_core": distribution(cpu), "rss_sum_mib": distribution(memory)}
    return result


def validate_stream(rows, persisted):
    last = None
    seen = {}
    snapshots = []
    for row in rows:
        event = row["event"]
        if event["type"] == "match_state":
            last = event["payload"]["watermark"]
            snapshots.append(event["payload"])
        elif isinstance(event.get("seq"), int):
            seq = event["seq"]
            assert last is not None and seq == last + 1, f"Non-contiguous accepted stream: {last} -> {seq}"
            assert event == persisted[seq], f"Event differs from SQLite at {seq}"
            assert seq not in seen, f"Repeated accepted event {seq}"
            seen[seq] = event
            last = seq
    assert seen and snapshots
    return seen, snapshots


async def measure(args, backend_pid, result):
    import httpx
    from playwright.async_api import async_playwright
    base = f"http://127.0.0.1:{args.port}"
    pages, samples, stop = [], [], asyncio.Event()
    resource_phase = "startup"
    async def monitor():
        while not stop.is_set():
            started = time.monotonic()
            samples.append({**process_sample(backend_pid), "phase": resource_phase})
            await asyncio.sleep(max(0, args.resource_interval - (time.monotonic() - started)))
    monitoring = asyncio.create_task(monitor())
    def progress(stage):
        result["stage"] = stage
        save(args.output / "progress.json", result)
        print(stage, flush=True)
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=60) as http:
        for _ in range(200):
            try:
                response = await http.get("/__bench/state")
                if response.is_success and response.json()["seq"] > 0:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.1)
        else:
            raise RuntimeError("Fixture server did not become ready")
        mid = response.json()["match_id"]
        result["match_id"] = mid
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=args.chromium_arg)
            result["browser_version"] = browser.version
            errors = []
            try:
                progress("open-five-production-socket-clients")
                for index in range(5):
                    context = await browser.new_context()
                    page = await context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.goto(base + "/__bench/page")
                    await page.wait_for_function("window.ready")
                    await page.evaluate("id => window.connect(id)", mid)
                    await page.wait_for_function("window.rows.some(r => r.event.type === 'match_state')")
                    pages.append(page)
                calibrations = [await page.evaluate("window.calibrate()") for page in pages]
                result["clock_start"] = calibrations
                await http.post("/__bench/normal")
                resource_phase = "normal_game"
                await pages[0].wait_for_timeout(args.normal_seconds * 1000)
                recovery = []
                for index in range(args.reconnects):
                    page = pages[index % 5]
                    before = (await http.get("/__bench/state")).json()
                    assert before["status"] == "running"
                    point = await page.evaluate("window.disconnect()")
                    # The production 1-second reconnect timer stays unchanged.
                    await page.wait_for_function("p => window.rows.some(r => r.connection > p.connection && r.event.type === 'match_state' && r.event.payload.watermark > p.before_seq)", arg=point, timeout=15000)
                    fresh = await page.evaluate("p => window.rows.find(r => r.connection > p.connection && r.event.type === 'match_state')", point)
                    assert fresh["event"]["payload"]["status"] == "running"
                    await page.wait_for_function("p => window.rows.some(r => r.connection === p.connection && r.event.seq > p.watermark)", arg={"connection": fresh["connection"], "watermark": fresh["event"]["payload"]["watermark"]}, timeout=10000)
                    after = (await http.get("/__bench/state")).json()
                    assert after["status"] == "running"
                    recovery.append({"viewer": index % 5, **point,
                        "snapshot_received_ms": fresh["received_ms"],
                        "snapshot_watermark": fresh["event"]["payload"]["watermark"],
                        "snapshot_state_hash": fresh["event"]["payload"]["state_hash"],
                        "recovery_ms": fresh["received_ms"] - point["started_ms"],
                        "missed_event_count": fresh["event"]["payload"]["watermark"] - point["before_seq"],
                        "before_status": before["status"], "after_status": after["status"]})
                    result["reconnects_completed"] = len(recovery)
                    progress(f"running-reconnect-{index + 1}-of-{args.reconnects}")
                result["recoveries"] = recovery
                progress("separate-default-queue-backpressure-phase")
                resource_phase = "pressure_setup"
                slow_page = await browser.new_page()
                await slow_page.goto(base + "/__bench/page")
                await slow_page.wait_for_function("window.ready")
                await slow_page.evaluate("id => window.connect(id, true)", mid)
                await slow_page.wait_for_function("window.raw.some(r => r.event?.type === 'match_state')")
                for _ in range(100):
                    status = (await http.get("/__bench/state")).json()
                    if status["slow"].get("capacity"):
                        break
                    await asyncio.sleep(.01)
                resource_phase = "backpressure"
                pressure_response = await http.post("/__bench/pressure")
                pressure_response.raise_for_status()
                pressure = pressure_response.json()
                for page in pages:
                    await page.wait_for_function("seq => window.rows.some(r => r.event.seq >= seq)", arg=pressure["last_seq"], timeout=30000)
                await slow_page.wait_for_function("window.raw.some(r => r.event?.type === 'resync_required') && window.raw.some(r => r.close_code === 1013)", timeout=15000)
                result["pressure"] = pressure
                result["slow_client"] = await slow_page.evaluate("window.raw")
                result["slow_server"] = (await http.get("/__bench/state")).json()["slow"]
                assert result["slow_server"]["max_queue_size"] == 10000
                assert result["slow_server"]["detached"]
                await http.post("/__bench/normal")
                resource_phase = "normal_game"
                await pages[0].wait_for_timeout(args.normal_seconds * 1000)
                progress("authoritative-running-state-convergence")
                resource_phase = "checkpoint"
                checkpoint_response = await http.post("/__bench/checkpoint")
                checkpoint_response.raise_for_status()
                checkpoint = checkpoint_response.json()
                for page in pages:
                    await page.evaluate("window.client.send({type:'subscribe'})")
                    await page.wait_for_function("h => window.rows.some(r => r.event.type === 'match_state' && r.event.payload.state_hash === h)", arg=checkpoint["state_hash"])
                    actual = await page.evaluate("window.rows.filter(r => r.event.type === 'match_state').at(-1).event.payload")
                    assert actual == checkpoint
                result["checkpoint"] = checkpoint
                result["clock_end"] = [await page.evaluate("window.calibrate()") for page in pages]
                result["page_errors"] = errors
                assert not errors
                progress("capture-raw-samples")
            finally:
                resource_phase = "capture_and_cleanup"
                for index, page in enumerate(pages):
                    with suppress(Exception):
                        save(args.output / f"viewer-{index}.json", await page.evaluate("({accepted: window.rows, raw: window.raw})"))
                await browser.close()
                stop.set()
                await monitoring
                save(args.output / "resource-samples.json", samples)
                result["resources_by_phase"] = {
                    phase: resource_summary(samples, phase)
                    for phase in dict.fromkeys(s["phase"] for s in samples)}
                result["resource_sampling"] = {
                    "method": "controller descendants via /proc/PID/task/*/children",
                    "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                    "page_size_bytes": os.sysconf("SC_PAGE_SIZE"),
                    "target_interval_ms": args.resource_interval * 1000,
                    "scan_ms_by_phase": {
                        phase: distribution([s["scan_ms"] for s in samples if s["phase"] == phase])
                        for phase in result["resources_by_phase"]},
                    "actual_interval_ms_by_phase": {
                        phase: distribution([(b["monotonic"] - a["monotonic"]) * 1000
                            for a, b in zip(samples, samples[1:]) if a["phase"] == b["phase"] == phase])
                        for phase in result["resources_by_phase"]}}


def finalize(args, result):
    import sqlite3
    server = json.loads((args.output / "server-samples.json").read_text())
    with sqlite3.connect(f"file:{args.output / 'realtime.db'}?mode=ro", uri=True) as db:
        persisted = {seq: json.loads(raw) for seq, raw in db.execute("SELECT seq,event_json FROM match_events WHERE match_id=?", (result["match_id"],))}
    maps, latencies = [], {"normal_game": [], "backpressure_game": [], "pressure_probes": [],
                          "checkpoint_game": [], "startup_game": []}
    offsets = []
    server_snapshots = {(s["snapshot"]["watermark"], s["snapshot"]["state_hash"]): s["snapshot"] for s in server["snapshots"]}
    for index in range(5):
        data = json.loads((args.output / f"viewer-{index}.json").read_text())
        events, snapshots = validate_stream(data["accepted"], persisted)
        maps.append(events)
        for snapshot in snapshots:
            assert snapshot == server_snapshots[(snapshot["watermark"], snapshot["state_hash"])]
        start = min(result["clock_start"][index], key=lambda s: s["rtt_ms"])
        end = min(result["clock_end"][index], key=lambda s: s["rtt_ms"])
        uncertainty = max(start["rtt_ms"], end["rtt_ms"]) / 2 + abs(start["offset_ms"] - end["offset_ms"])
        offsets.append({"viewer": index, "offset_ms": start["offset_ms"], "uncertainty_ms": uncertainty,
                        "end_offset_ms": end["offset_ms"]})
        for row in data["accepted"]:
            event = row["event"]
            if "seq" not in event:
                continue
            stamp = event["_benchmark"]
            delay = row["received_ms"] + start["offset_ms"] - stamp["publish_ms"]
            assert delay >= -uncertainty - 1, "Clock calibration cannot explain negative delay"
            key = "pressure_probes" if event["type"] == "benchmark_pressure_probe" else (
                "backpressure_game" if stamp["phase"] == "backpressure" else (
                    "checkpoint_game" if stamp["phase"] == "checkpoint" else (
                        "startup_game" if stamp["phase"] == "startup" else "normal_game")))
            latencies[key].append({"viewer": index, "seq": event["seq"], "latency_ms": delay})
    common = set.intersection(*(set(events) for events in maps))
    natural = [seq for seq in common if persisted[seq]["type"] != "benchmark_pressure_probe"]
    probes = [seq for seq in common if persisted[seq]["type"] == "benchmark_pressure_probe"]
    assert len(natural) >= 20 and len(probes) == 10001
    assert all(maps[0][seq] == events[seq] for events in maps for seq in common)
    # Every reconnection skipped at least one naturally produced game event.
    for recovery in result["recoveries"]:
        missed = range(recovery["before_seq"] + 1, recovery["snapshot_watermark"] + 1)
        assert any(persisted[seq]["type"] != "benchmark_pressure_probe" for seq in missed)
    assert server["real_provider_attempts"] == 0 and not server["external_connect_attempts"]
    assert hashes() == result["source_sha256"], "Measured source changed during run"
    save(args.output / "latency-samples.json", latencies)
    result.update(stage="complete", source_hashes_unchanged=True,
        clock_calibration=offsets, max_clock_uncertainty_ms=max(row["uncertainty_ms"] for row in offsets),
        common_natural_events=len(natural), common_pressure_probes=len(probes),
        event_latency_ms={key: distribution([row["latency_ms"] for row in rows]) for key, rows in latencies.items()},
        recovery_ms=distribution([r["recovery_ms"] for r in result["recoveries"]]),
        mock_wait_ms=distribution([r["delay_ms"] for r in server["model_waits"]]),
        fixture_shutdown_status=server["fixture_shutdown_status"], real_provider_attempts=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reconnects", type=int, default=20)
    parser.add_argument("--normal-seconds", type=float, default=3)
    parser.add_argument("--mock-delay", type=float, default=.12)
    parser.add_argument("--resource-interval", type=float, default=.5)
    parser.add_argument("--seed", default="realtime-20261004-v1")
    parser.add_argument("--chromium-arg", action="append", default=[])
    parser.add_argument("--serve", type=int)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.serve:
        serve(args)
        return
    if args.reconnects < 1 or args.mock_delay <= 0 or args.normal_seconds < 1 or args.resource_interval <= 0:
        parser.error("reconnects >= 1, mock-delay > 0, normal-seconds >= 1 and resource-interval > 0 are required")
    args.output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        args.port = reserve.getsockname()[1]
    result = {"kind": "local-browser-production-socket-benchmark", "schema_version": 1,
        "instrumentation_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "viewers": 5,
        "requested_reconnects": args.reconnects, "mock_delay_seconds": args.mock_delay,
        "chromium_args": args.chromium_arg,
        "normal_seconds_each_side": args.normal_seconds, "seed": args.seed,
        "source_sha256": hashes(), "python": sys.version, "python_executable": sys.executable,
        "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "versions": {name: importlib.metadata.version(name) for name in ("fastapi", "uvicorn", "playwright", "httpx")},
        "notes": ["Real Chromium pages import the production socket module; Vue DOM/rendering is excluded.",
            "Publish-to-handler latency includes durable SQLite publication, event-loop scheduling, JSON, loopback WS and browser handler dispatch; mock waiting precedes publication and is excluded.",
            "Startup events before all five clients and initial clock calibration are ready are reported separately from normal_game.",
            "P95 is nearest-rank. Raw negative values within clock uncertainty are retained, not clamped.",
            "Maximum clock uncertainty is reported; latency differences near that bound do not establish an optimization benefit.",
            "Recovery includes the unchanged production 1-second reconnect timer and authoritative snapshot; every cycle skips natural events while running.",
            "Pressure phase really overflows one default 10000-event subscription using 10001 persisted artificial probes and an injected server relay suspension; not a slow-network throughput claim.",
            "The final consistency checkpoint gates mock returns only; the fixture stays running, then its own process is shut down and leaves an interrupted match.",
            "CPU is sampled from /proc, 100% equals one core. Chromium RSS is a process-tree sum with shared-page double counting; short-lived process CPU may be missed.",
            "Instrumentation v2 reads only the controller process tree through task children, targets a 500ms default interval, and records both scan cost and actual interval distributions.",
            "Resource samples and summaries separate normal_game, backpressure, startup, checkpoint and capture/cleanup phases; normal_game includes the intentional reconnect cycles.",
            "Single local host, one mock match, five normal viewers and one injected slow subscriber; no production capacity, SLA, real-model latency or strategy claim."]}
    log = (args.output / "backend.log").open("w")
    process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
        "--output", str(args.output), "--serve", str(args.port), "--mock-delay", str(args.mock_delay),
        "--seed", args.seed], stdout=log, stderr=subprocess.STDOUT)
    failed = None
    try:
        asyncio.run(measure(args, process.pid, result))
    except BaseException as exc:
        failed = exc
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
    if failed is None:
        try:
            finalize(args, result)
        except BaseException as exc:
            failed = exc
            result["error"] = f"{type(exc).__name__}: {exc}"
    save(args.output / "result.json", result)
    print(json.dumps({"output": str(args.output), "stage": result.get("stage"),
                      "error": result.get("error")}, ensure_ascii=False), flush=True)
    if failed:
        raise failed


if __name__ == "__main__":
    main()
