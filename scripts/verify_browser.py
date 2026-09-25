"""Local mock browser acceptance; run with the doudizhu-arena conda Python.

Starts isolated local servers and a fresh database. Real provider generation is
disabled in this harness. The synthetic provider is delayed to exercise viewers.
"""
import argparse
import asyncio
import faulthandler
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def serve(output, port):
    os.environ.update(DATABASE_URL=f'sqlite:///{output}/browser.db',
                      AGENTS_YAML_PATH=str(output / 'empty-agents.yaml'),
                      LOG_DIR=str(output / 'logs'), LOG_LEVEL='WARNING')
    sys.path.insert(0, str(ROOT / 'src/backend'))
    from arena.evaluation.mock import ReliabilityMockProvider
    from arena.llm.openai import OpenAIProvider
    from arena.llm.claude import ClaudeProvider
    import uvicorn
    from main import app
    original = ReliabilityMockProvider.generate
    async def delayed(self, *args):
        self.last_usage = None
        await asyncio.sleep(0.06)
        return await original(self, *args)
    async def deny(*args, **kwargs):
        raise AssertionError('Real providers are disabled in browser acceptance')
    ReliabilityMockProvider.generate = delayed
    OpenAIProvider.generate = ClaudeProvider.generate = deny
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--serve-backend', type=int)
    args = parser.parse_args()
    if args.serve_backend:
        serve(args.output, args.serve_backend)
        return
    output = args.output or ROOT / 'data/browser-verification' / uuid.uuid4().hex
    output.mkdir(parents=True, exist_ok=False)
    faulthandler.dump_traceback_later(90, repeat=True)
    (output / 'empty-agents.yaml').write_text('agent_definitions: []\ndefault_players: []\n')
    backend_port, frontend_port = free_port(), free_port()
    backend_url = f'http://127.0.0.1:{backend_port}'
    frontend_url = f'http://127.0.0.1:{frontend_port}'
    node = Path(sys.executable).parent / 'node'
    if not node.exists():
        raise RuntimeError('Install Node.js 22 in the doudizhu-arena environment')
    logs = [(output / name).open('w') for name in ('backend.log', 'frontend.log')]
    processes = []
    viewers = []
    result = {'kind': 'mock-browser-acceptance', 'mock_delay_seconds': 0.06, 'real_providers_disabled': True}
    def progress(stage):
        result['stage'] = stage
        (output / 'progress.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    try:
        subprocess.run([str(node), 'node_modules/vite/bin/vite.js', 'build'],
            cwd=ROOT / 'src/frontend', stdout=logs[1], stderr=subprocess.STDOUT, check=True)
        processes.append(subprocess.Popen([sys.executable, '-I', '-B', __file__, '--serve-backend', str(backend_port), '--output', str(output)], stdout=logs[0], stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen([str(node), 'node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', str(frontend_port), '--strictPort'],
            cwd=ROOT / 'src/frontend', env={**os.environ, 'DOUDIZHU_DEV_BACKEND': backend_url}, stdout=logs[1], stderr=subprocess.STDOUT))
        import httpx
        from playwright.sync_api import sync_playwright, expect
        with httpx.Client(base_url=backend_url, trust_env=False, timeout=5) as client:
            for _ in range(100):
                try:
                    if client.get('/api/v1/health').is_success and httpx.get(frontend_url, trust_env=False).is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('Local servers did not become ready; inspect logs')
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                context.tracing.start(screenshots=True, snapshots=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(frontend_url + '/evaluations/new')
                page.get_by_label('模拟场景').select_option('play_error')
                page.get_by_role('button', name='运行预检').click()
                expect(page.get_by_text('预检通过', exact=True)).to_be_visible()
                page.get_by_role('button', name='创建运行计划').click()
                page.wait_for_url('**/evaluations/*')
                page.get_by_role('button', name='启动 mock').wait_for()
                rid = page.url.rsplit('/', 1)[-1]
                page.get_by_role('button', name='启动 mock').click()
                for _ in range(100):
                    tasks = client.get(f'/api/v1/evaluations/{rid}').json()['tasks']
                    # task.match_id is finalized on completion; the live match
                    # list exposes the match while the task is still running.
                    matches = client.get('/api/v1/matches?status=running').json()['matches']
                    if matches:
                        mid = matches[0]['id']; break
                    time.sleep(0.05)
                else: raise AssertionError('No active mock match')
                contexts, viewers = [], []
                capture = """(() => {
                  window.events = []; window.sockets = [];
                  const Native = window.WebSocket;
                  window.WebSocket = class extends Native {
                    constructor(...args) { super(...args);
                      if (String(args[0]).includes('/ws/match/')) {
                        window.sockets.push(this);
                        this.addEventListener('message', e => window.events.push(JSON.parse(e.data)));
                      }
                    }
                  };
                })();"""
                for index in range(2):
                    progress(f'open-viewer-{index}')
                    c = browser.new_context(viewport={'width': 1440, 'height': 1000})
                    c.add_init_script(capture)
                    v = c.new_page(); v.on('pageerror', lambda error: errors.append(str(error)))
                    v.goto(frontend_url + '/match/' + mid)
                    contexts.append(c); viewers.append(v)
                for v in viewers:
                    v.wait_for_function('window.events.filter(e => Number.isInteger(e.seq)).length >= 5')
                first, second = [v.evaluate('window.events') for v in viewers]
                a = {e['seq']: e for e in first if 'seq' in e}
                b = {e['seq']: e for e in second if 'seq' in e}
                common = sorted(set(a) & set(b))
                assert len(common) >= 3 and all(a[n] == b[n] for n in common)
                result['identical_live_events'] = len(common)
                progress('disconnect-viewer')
                contexts[1].set_offline(True)
                viewers[1].evaluate('window.sockets.forEach(s => s.close())')
                progress('wait-match-finish')
                for _ in range(600):
                    match = client.get('/api/v1/matches/' + mid).json()
                    if match['status'] == 'finished': break
                    viewers[0].wait_for_timeout(100)
                else: raise AssertionError('Match did not finish')
                progress('restore-network')
                contexts[1].set_offline(False)
                progress('wait-terminal-snapshot')
                try:
                    viewers[1].wait_for_function("window.events.some(e => e.type === 'match_state' && e.payload.status === 'finished')", timeout=40000)
                except Exception:
                    result['reconnect_diagnostics'] = viewers[1].evaluate("({events: window.events, sockets: window.sockets.map(s => s.readyState), online: navigator.onLine})")
                    viewers[1].screenshot(path=str(output / 'reconnect-failure.png'), full_page=True)
                    raise
                expect(viewers[1].get_by_text('已结束', exact=True)).to_be_visible()
                snapshot = viewers[1].evaluate("window.events.filter(e => e.type === 'match_state').at(-1).payload")
                assert snapshot['score'] == match['score']
                assert all(t['phase'] == 'finished' for t in snapshot['tables'].values())
                result['terminal_reconnect'] = {'match_id': mid, 'status': snapshot['status'], 'score': snapshot['score'], 'watermark': snapshot['watermark']}
                viewers[1].screenshot(path=str(output / 'terminal-reconnect.png'), full_page=True)
                progress('reload-terminal')
                viewers[1].reload()
                viewers[1].wait_for_function("window.events.some(e => e.type === 'match_state' && e.payload.status === 'finished')")
                result['terminal_reload'] = True
                progress('wait-run-finish')
                for _ in range(900):
                    run = client.get(f'/api/v1/evaluations/{rid}').json()
                    if run['run']['status'] in ('finished', 'failed', 'cancelled'): break
                    time.sleep(0.1)
                assert run['run']['status'] == 'finished', run
                expect(page.get_by_role('heading', name='可靠性报告')).to_be_visible(timeout=15000)
                progress('report')
                expect(page.get_by_text('完整性审计通过', exact=True)).to_be_visible()
                page.locator('details').first.click()
                page.locator('details').nth(1).click()
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(output / 'evaluation-report.png'), full_page=True)
                artifact = client.get(f'/api/v1/evaluations/{rid}/artifacts/integrity.json')
                assert artifact.is_success and artifact.json()['complete']
                page.get_by_role('link', name='查看对应比赛回放').click()
                progress('replay')
                expect(page.get_by_role('button', name='▶ 播放', exact=True)).to_be_visible(timeout=15000)
                page.screenshot(path=str(output / 'replay.png'), full_page=True)
                result.update(run_id=rid, task_counts=run['counts'], integrity_complete=True, page_errors=errors)
                assert not errors, errors
                progress('complete')
                context.tracing.stop(path=str(output / 'trace.zip'))
                browser.close()
    except BaseException as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        for index, viewer in enumerate(viewers):
            try:
                result[f'viewer_{index}'] = viewer.evaluate("({events: window.events, sockets: window.sockets.map(s => s.readyState), online: navigator.onLine})")
                viewer.screenshot(path=str(output / f'failure-{index}.png'), full_page=True)
            except Exception:
                pass
        raise
    finally:
        faulthandler.cancel_dump_traceback_later()
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        for process in processes:
            process.terminate()
        for process in processes:
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for log in logs: log.close()
        print(json.dumps({'output': str(output), **result}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
