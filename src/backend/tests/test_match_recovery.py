"""Offline API ownership and restart/shutdown regression tests."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from arena.api import match_lifecycle
from arena.api.routes import match as match_api
from arena.api.routes import player as player_api
from arena.db.repository import DatabaseRepository
from arena.evaluation.execution import ExecutionRevoked
from arena.tournament.seating import assign_seating


@pytest.fixture
def repo(tmp_path):
    repository = DatabaseRepository(str(tmp_path / "recovery.db"))
    repository.init()
    yield repository
    repository.close()


def make_players(repo, provider="random"):
    config = repo.create_player_config("Offline", provider, "offline-model", "unused")
    return [repo.create_player(config, f"Player {index}") for index in range(8)]


def make_match(repo, players, config=None):
    match_id = repo.create_match("Offline match", {"total_hands": 1, **(config or {})}, "seed")
    seating = assign_seating(players[:4], players[4:], seed="seating")
    for player_id, team in seating.agent_teams.items():
        table, seat = seating.agent_seats[player_id]
        repo.add_participant(match_id, player_id, team,
                             seat_table_a=seat if table == "A" else None,
                             seat_table_b=seat if table == "B" else None)
    return match_id


def request_for(repo):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db_repo=repo, active_matches={}, active_evaluations={},
    )))


def fake_runner(monkeypatch, behavior):
    instances = []

    class FakeRunner:
        def __init__(self, config, seating, agents, db_repo, match_name):
            self.config, self.agents, self.db_repo = config, agents, db_repo
            self.table_a, self.table_b = SimpleNamespace(), SimpleNamespace()
            self.event_bus = None
            instances.append(self)

        async def run(self):
            return await behavior(self)

    monkeypatch.setattr(match_api, "MatchRunner", FakeRunner)
    return instances


def test_concurrent_start_creates_only_one_active_runner(repo, monkeypatch):
    async def scenario():
        release = asyncio.Event()

        async def behavior(runner):
            await release.wait()

        instances = fake_runner(monkeypatch, behavior)
        match_id = make_match(repo, make_players(repo))
        request = request_for(repo)
        results = await asyncio.gather(
            match_api.start_match(match_id, request, BackgroundTasks()),
            match_api.start_match(match_id, request, BackgroundTasks()),
            return_exceptions=True,
        )
        assert len(instances) == 1
        assert sum(isinstance(item, HTTPException) and item.status_code == 409 for item in results) == 1
        assert repo.get_match(match_id)["status"] == "running"
        assert len(request.app.state.match_tasks) == 1
        task = request.app.state.match_tasks[match_id]
        release.set()
        await task
        assert not match_lifecycle.match_is_claimed(repo, match_id)
        assert not request.app.state.active_matches

    asyncio.run(scenario())


def test_player_claim_lasts_until_summary_finishes_even_after_match_is_finished(repo, monkeypatch):
    async def scenario():
        summary_entered, finish_summary = asyncio.Event(), asyncio.Event()

        async def behavior(runner):
            runner.db_repo.update_match_status(runner.match_id, "finished")
            summary_entered.set()
            await finish_summary.wait()

        fake_runner(monkeypatch, behavior)
        players = make_players(repo)
        first, second = make_match(repo, players), make_match(repo, players)
        request = request_for(repo)
        await match_api.start_match(first, request, BackgroundTasks())
        first_task = request.app.state.match_tasks[first]
        await summary_entered.wait()
        assert repo.get_match(first)["status"] == "finished"
        with pytest.raises(HTTPException) as busy:
            await match_api.start_match(second, request, BackgroundTasks())
        assert busy.value.status_code == 409
        assert busy.value.detail["error"]["code"] == "PLAYER_BUSY"
        assert repo.get_match(second)["status"] == "created"
        finish_summary.set()
        await first_task
        await match_api.start_match(second, request, BackgroundTasks())
        await request.app.state.match_tasks[second]
        assert not request.app.state.match_tasks

    asyncio.run(scenario())


def test_disabled_persistence_allows_independent_matches_for_same_players(repo, monkeypatch):
    async def scenario():
        release = asyncio.Event()

        async def behavior(runner):
            await release.wait()

        fake_runner(monkeypatch, behavior)
        players = make_players(repo)
        first = make_match(repo, players, {"persist_long_term_memory": False})
        second = make_match(repo, players, {"persist_long_term_memory": False})
        request = request_for(repo)
        await match_api.start_match(first, request, BackgroundTasks())
        await match_api.start_match(second, request, BackgroundTasks())
        assert len(request.app.state.match_tasks) == 2
        tasks = list(request.app.state.match_tasks.values())
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_runner_exception_marks_interrupted_and_releases_players(repo, monkeypatch):
    async def scenario():
        async def behavior(runner):
            raise RuntimeError("offline failure")

        fake_runner(monkeypatch, behavior)
        match_id = make_match(repo, make_players(repo))
        request = request_for(repo)
        await match_api.start_match(match_id, request, BackgroundTasks())
        task = request.app.state.match_tasks[match_id]
        with pytest.raises(RuntimeError):
            await task
        assert repo.get_match(match_id)["status"] == "interrupted"
        assert not match_lifecycle.match_is_claimed(repo, match_id)
        assert not request.app.state.active_matches
        result = await match_api.list_matches(request, status="interrupted")
        assert result["total"] == 1

    asyncio.run(scenario())


def test_cleanup_retries_transient_sqlite_failure_after_revoking_scope(repo, monkeypatch):
    async def scenario():
        async def behavior(runner):
            runner.db_repo.update_match_status(runner.match_id, "finished")

        fake_runner(monkeypatch, behavior)
        request = request_for(repo)
        match_id = make_match(repo, make_players(repo))
        original = match_lifecycle.release_match_claims
        attempts = []

        def fail_twice(repository, mid, reason):
            assert not request.app.state.match_scopes[mid].active
            attempts.append(mid)
            if len(attempts) < 3:
                raise sqlite3.OperationalError("temporary write failure")
            return original(repository, mid, reason)

        monkeypatch.setattr(match_lifecycle, "release_match_claims", fail_twice)
        await match_api.start_match(match_id, request, BackgroundTasks())
        await request.app.state.match_tasks[match_id]
        assert len(attempts) == 3
        assert not match_lifecycle.match_is_claimed(repo, match_id)
        assert not request.app.state.match_cleanup_pending
        assert not request.app.state.match_scopes

    asyncio.run(scenario())


def test_failed_cleanup_can_recover_on_next_start_without_server_restart(repo, monkeypatch):
    async def scenario():
        async def behavior(runner):
            runner.db_repo.update_match_status(runner.match_id, "finished")

        fake_runner(monkeypatch, behavior)
        request = request_for(repo)
        players = make_players(repo)
        first, second = make_match(repo, players), make_match(repo, players)
        original = match_lifecycle.release_match_claims

        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("temporary write failure")

        monkeypatch.setattr(match_lifecycle, "release_match_claims", fail)
        await match_api.start_match(first, request, BackgroundTasks())
        await request.app.state.match_tasks[first]
        pending = request.app.state.match_cleanup_pending[first]
        assert not pending["scope"].active
        assert match_lifecycle.match_is_claimed(repo, first)
        assert not request.app.state.match_tasks
        assert not request.app.state.match_scopes

        monkeypatch.setattr(match_lifecycle, "release_match_claims", original)
        await match_api.start_match(second, request, BackgroundTasks())
        await request.app.state.match_tasks[second]
        assert not match_lifecycle.match_is_claimed(repo, first)
        assert not match_lifecycle.match_is_claimed(repo, second)
        assert not request.app.state.match_cleanup_pending

    asyncio.run(scenario())


def test_pending_cleanup_never_releases_active_or_still_running_task(repo):
    from arena.evaluation.execution import ExecutionScope

    async def scenario():
        request = request_for(repo)
        app = request.app
        match_lifecycle.ensure_task_registry(app)
        match_id = make_match(repo, make_players(repo))
        match_lifecycle.claim_match_start(repo, match_id, [], writes_memory=False)
        scope = ExecutionScope()
        app.state.match_scopes[match_id] = scope
        app.state.match_cleanup_pending[match_id] = {"scope": scope, "reason": "completed"}
        assert match_lifecycle.recover_pending_match_cleanups(app) == 0
        foreign_scope = ExecutionScope()
        assert not await match_lifecycle.cleanup_match_claims(app, match_id, foreign_scope, "completed")
        assert foreign_scope.active

        scope.revoke()
        release = asyncio.Event()
        task = asyncio.create_task(release.wait())
        app.state.match_tasks[match_id] = task
        assert match_lifecycle.recover_pending_match_cleanups(app) == 0
        assert match_lifecycle.match_is_claimed(repo, match_id)
        release.set()
        await task
        assert match_lifecycle.recover_pending_match_cleanups(app) == 1
        assert not match_lifecycle.match_is_claimed(repo, match_id)

    asyncio.run(scenario())


def test_startup_recovers_before_missing_master_key_and_keeps_history_readable(repo, monkeypatch):
    import httpx
    import main
    from arena.agent import loader
    from arena.db import repository

    monkeypatch.delenv("DOUDIZHU_CREDENTIAL_MASTER_KEY", raising=False)
    players = make_players(repo, provider="openai")
    repo.conn.execute("UPDATE player_configs SET api_key='enc:v1:unavailable'")
    repo.conn.commit()
    orphan = make_match(repo, players)
    match_lifecycle.claim_match_start(repo, orphan, players, writes_memory=True)
    next_match = make_match(repo, players)
    monkeypatch.setattr(repository, "DatabaseRepository", lambda path: repo)
    monkeypatch.setattr(repo, "init", lambda: None)
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(loader, "load_agents_from_yaml", lambda repository: repository.list_player_configs())

    async def scenario():
        app = main.create_app()
        async with main.lifespan(app):
            assert repo.get_match(orphan)["status"] == "interrupted"
            assert not match_lifecycle.match_is_claimed(repo, orphan)
            assert repo.conn.execute("SELECT api_key FROM player_configs").fetchone()[0] == "enc:v1:unavailable"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                history = await client.get(f"/api/v1/matches/{orphan}")
                assert history.status_code == 200
                assert history.json()["status"] == "interrupted"
                response = await client.post(f"/api/v1/matches/{next_match}/start")
                assert response.status_code == 503
                assert response.json()["error"]["code"] == "CREDENTIAL_UNAVAILABLE"
                assert "enc:v1" not in response.text
                assert repo.get_match(next_match)["status"] == "created"
                assert not match_lifecycle.match_is_claimed(repo, next_match)

    asyncio.run(scenario())


def test_restart_recovers_all_pages_of_running_and_paused_matches(repo):
    with repo.atomic():
        for index in range(1003):
            match_id = repo.create_match(f"Orphan {index}", {}, str(index))
            repo.update_match_status(match_id, "running" if index % 2 else "paused")
    assert match_lifecycle.recover_interrupted_matches(repo, page_size=17) == 1003
    assert repo.list_matches(status="running")[1] == 0
    assert repo.list_matches(status="paused")[1] == 0
    assert repo.list_matches(status="interrupted")[1] == 1003
    assert match_lifecycle.recover_interrupted_matches(repo, page_size=17) == 0


def test_restart_preserves_finished_scores_and_reports_only_unfinished_summaries(repo):
    players = make_players(repo, provider="openai")
    match_id = make_match(repo, players)
    match_lifecycle.claim_match_start(repo, match_id, players, writes_memory=True)
    repo.update_match_status(match_id, "finished", score_red=12, score_blue=-12)
    repo.save_long_term_memory(players[0], "saved before restart", match_id, expected_content="")
    assert match_lifecycle.recover_interrupted_matches(repo, page_size=2) == 1
    assert repo.get_match(match_id)["status"] == "finished"
    assert repo.get_match(match_id)["score_red"] == 12
    failures = repo.list_memory_failures(match_id=match_id)
    assert len(failures) == 7
    assert all(item["phase"] == "summary" and item["error_type"] == "MatchExecutionInterrupted" for item in failures)
    assert players[0] not in {item["player_id"] for item in failures}
    assert not match_lifecycle.match_is_claimed(repo, match_id)
    assert match_lifecycle.recover_interrupted_matches(repo) == 0
    assert len(repo.list_memory_failures(match_id=match_id)) == 7


def test_restart_does_not_report_intentional_random_agent_summary_noops(repo):
    players = make_players(repo)
    match_id = make_match(repo, players)
    match_lifecycle.claim_match_start(repo, match_id, players, writes_memory=True)
    repo.update_match_status(match_id, "finished", score_red=3, score_blue=-3)
    assert match_lifecycle.recover_interrupted_matches(repo) == 1
    assert repo.get_match(match_id)["status"] == "finished"
    assert repo.list_memory_failures(match_id=match_id) == []
    assert not match_lifecycle.match_is_claimed(repo, match_id)


def test_restart_recovery_does_not_require_decrypting_provider_credentials(repo, monkeypatch):
    monkeypatch.setenv("DOUDIZHU_CREDENTIAL_MASTER_KEY", "offline-test-key")
    players = make_players(repo, provider="openai")
    match_id = make_match(repo, players)
    match_lifecycle.claim_match_start(repo, match_id, players, writes_memory=True)
    repo.update_match_status(match_id, "finished")
    monkeypatch.delenv("DOUDIZHU_CREDENTIAL_MASTER_KEY")
    assert match_lifecycle.recover_interrupted_matches(repo) == 1
    assert len(repo.list_memory_failures(match_id=match_id)) == 8
    assert not match_lifecycle.match_is_claimed(repo, match_id)


def test_create_match_participant_failure_rolls_back_match_and_all_participants(repo, monkeypatch):
    async def scenario():
        players = make_players(repo)
        request = request_for(repo)

        async def body():
            return {"name": "Atomic match", "team_red": {"agents": players[:4]},
                    "team_blue": {"agents": players[4:]}}

        request.json = body
        original = repo.add_participant
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise sqlite3.OperationalError("simulated insert failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(repo, "add_participant", fail_second)
        with pytest.raises(sqlite3.OperationalError):
            await match_api.create_match(request)
        assert repo.list_matches()[1] == 0
        assert repo.conn.execute("SELECT COUNT(*) FROM match_participants").fetchone()[0] == 0

    asyncio.run(scenario())


def test_player_memory_api_repairs_legacy_value_and_rejects_stale_edit(repo):
    async def scenario():
        player = make_players(repo)[0]
        original = "x" * 8001
        repo.update_player_long_term_memory(player, original)
        request = request_for(repo)
        payload = {"long_term_memory": "  corrected lesson  ", "expected_long_term_memory": original}

        async def body():
            return payload

        request.json = body
        updated = await player_api.update_player(player, request)
        assert updated["long_term_memory"] == "corrected lesson"
        assert updated["memory_revision"] == 2
        assert repo.list_memory_versions(player)[1]["content"] == original
        payload["long_term_memory"] = "stale replacement"
        with pytest.raises(HTTPException) as error:
            await player_api.update_player(player, request)
        assert error.value.status_code == 409
        assert error.value.detail["error"]["code"] == "MEMORY_CONFLICT"
        assert repo.get_player(player)["long_term_memory"] == "corrected lesson"
        assert len(repo.list_memory_versions(player)) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("memory", ["", " " * 10, "x" * 8001, 7, None])
def test_player_memory_api_rejects_invalid_replacement_without_changing_player(repo, memory):
    async def scenario():
        player = make_players(repo)[0]
        request = request_for(repo)

        async def body():
            return {"display_name": "should not change", "long_term_memory": memory}

        request.json = body
        with pytest.raises(HTTPException) as error:
            await player_api.update_player(player, request)
        assert error.value.status_code == 422
        assert repo.get_player(player)["display_name"] == "Player 0"
        assert repo.list_memory_versions(player) == []

    asyncio.run(scenario())


def test_player_memory_api_blocks_edit_until_finished_match_releases_summary_claim(repo):
    async def scenario():
        players = make_players(repo)
        match_id = make_match(repo, players)
        match_lifecycle.claim_match_start(repo, match_id, players, writes_memory=True)
        repo.update_match_status(match_id, "finished")
        request = request_for(repo)

        async def body():
            return {"long_term_memory": "manual replacement"}

        request.json = body
        with pytest.raises(HTTPException) as error:
            await player_api.update_player(players[0], request)
        assert error.value.status_code == 409
        assert error.value.detail["error"]["code"] == "PLAYER_BUSY"
        assert repo.list_memory_versions(players[0]) == []
        match_lifecycle.release_match_claims(repo, match_id)
        updated = await player_api.update_player(players[0], request)
        assert updated["long_term_memory"] == "manual replacement"

    asyncio.run(scenario())


def test_shutdown_cancels_pending_match_before_closing_database(repo, monkeypatch):
    async def scenario():
        async def behavior(runner):
            await asyncio.Event().wait()

        fake_runner(monkeypatch, behavior)
        match_id = make_match(repo, make_players(repo))
        request = request_for(repo)
        await match_api.start_match(match_id, request, BackgroundTasks())
        task = request.app.state.match_tasks[match_id]
        pending = await match_lifecycle.shutdown_matches(request.app, timeout_seconds=.05)
        assert pending == set()
        assert task.cancelled()
        assert repo.get_match(match_id)["status"] == "interrupted"
        assert not match_lifecycle.match_is_claimed(repo, match_id)
        assert not request.app.state.active_matches
        match_lifecycle.close_database_after_cleanup(repo, pending)
        assert repo._conn is None

    asyncio.run(scenario())


def test_cancellation_resistant_match_cannot_write_after_shutdown_fence(repo, monkeypatch):
    async def scenario():
        entered, allow_late_result = asyncio.Event(), asyncio.Event()
        fenced = []

        async def behavior(runner):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await allow_late_result.wait()
                try:
                    runner.db_repo.update_match_status(runner.match_id, "finished")
                except ExecutionRevoked:
                    fenced.append(True)

        fake_runner(monkeypatch, behavior)
        match_id = make_match(repo, make_players(repo))
        request = request_for(repo)
        await match_api.start_match(match_id, request, BackgroundTasks())
        task = request.app.state.match_tasks[match_id]
        await entered.wait()
        pending = await match_lifecycle.shutdown_matches(request.app, timeout_seconds=.01)
        assert not task.done()
        assert repo.get_match(match_id)["status"] == "interrupted"
        match_lifecycle.close_database_after_cleanup(repo, pending)
        assert repo._conn is None
        allow_late_result.set()
        await task
        assert fenced == [True]

    asyncio.run(scenario())


def test_deferred_database_close_waits_for_outer_cleanup(repo):
    async def scenario():
        release = asyncio.Event()

        async def cleanup():
            await release.wait()
            repo.create_match("Final cleanup", {}, "seed")

        task = asyncio.create_task(cleanup())
        match_lifecycle.close_database_after_cleanup(repo, {task})
        assert repo._conn is not None
        release.set()
        await task
        await asyncio.sleep(0)
        assert repo._conn is None

    asyncio.run(scenario())


@pytest.mark.parametrize("config", [
    {"enable_summary": "false"}, {"persist_long_term_memory": 0},
    {"reflection_timeout_seconds": 0}, {"summary_timeout_seconds": float("inf")},
])
def test_invalid_learning_controls_are_client_errors(config):
    with pytest.raises(HTTPException) as error:
        match_api._match_config_from_dict(config)
    assert error.value.status_code == 400


def test_oversized_legacy_memory_returns_422_without_claim_or_provider(repo, monkeypatch):
    async def scenario():
        import arena.llm.openai as openai_provider

        def forbid_provider(*args, **kwargs):
            raise AssertionError("Invalid memory must be rejected before provider construction")

        monkeypatch.setattr(openai_provider, "OpenAIProvider", forbid_provider)
        players = make_players(repo)
        config = repo.create_player_config("Legacy LLM", "openai", "offline-model", "unused")
        original_memory = "x" * 8001
        player_id = repo.create_player(config, "Legacy player", long_term_memory=original_memory)
        players[0] = player_id
        match_id = make_match(repo, players)
        request = request_for(repo)
        with pytest.raises(HTTPException) as error:
            await match_api.start_match(match_id, request, BackgroundTasks())
        assert error.value.status_code == 422
        assert error.value.detail["error"]["code"] == "PLAYER_MEMORY_INVALID"
        assert error.value.detail["error"]["player_id"] == player_id
        assert repo.get_player(player_id)["long_term_memory"] == original_memory
        assert repo.get_match(match_id)["status"] == "created"
        assert not match_lifecycle.match_is_claimed(repo, match_id)
        assert not request.app.state.match_tasks
        assert not request.app.state.active_matches

    asyncio.run(scenario())


def test_create_and_start_preserve_learning_controls(repo, monkeypatch):
    async def scenario():
        release = asyncio.Event()

        async def behavior(runner):
            await release.wait()

        instances = fake_runner(monkeypatch, behavior)
        players = make_players(repo)
        request = request_for(repo)
        controls = {
            "enable_reflection": False,
            "enable_summary": True,
            "persist_long_term_memory": False,
            "reflection_timeout_seconds": 2.5,
            "summary_timeout_seconds": 3.5,
        }

        async def body():
            return {
                "name": "Learning controls", "config": {"total_hands": 1, **controls},
                "team_red": {"agents": players[:4]}, "team_blue": {"agents": players[4:]},
            }

        request.json = body
        created = await match_api.create_match(request)
        match_id = created["id"]
        persisted = repo.get_match(match_id)["config"]
        assert {key: persisted[key] for key in controls} == controls
        await match_api.start_match(match_id, request, BackgroundTasks())
        assert len(instances) == 1
        assert {key: getattr(instances[0].config, key) for key in controls} == controls
        task = request.app.state.match_tasks[match_id]
        release.set()
        await task

    asyncio.run(scenario())
