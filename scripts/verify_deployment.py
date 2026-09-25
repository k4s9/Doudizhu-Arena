"""Verify a running deployment using mock data, then recheck after a restart.

Run with the doudizhu-arena conda Python and installed Playwright Chromium.
Use the frontend URL so HTTP and WebSocket requests exercise its proxy.
Each invocation requires a new output directory; existing evidence is preserved.
"""

import argparse
import json
from pathlib import Path
import time

import httpx
from playwright.sync_api import expect, sync_playwright


def verify(base_url, output, previous, chromium_args):
    result = {"kind": "deployment-mock-acceptance", "base_url": base_url,
              "restart_check": previous is not None, "chromium_args": chromium_args,
              "page_errors": []}
    try:
        with httpx.Client(base_url=base_url, trust_env=False, timeout=20) as client:
            for _ in range(60):
                try:
                    if client.get('/api/v1/health').is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Deployment did not become healthy')

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=chromium_args)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.tracing.start(screenshots=True, snapshots=True)
                try:
                    page = context.new_page()
                    page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
                    if previous:
                        rid = previous['run_id']
                    else:
                        page.goto(base_url + '/evaluations/new')
                        page.get_by_label('模拟场景').select_option('play_error')
                        page.get_by_role('button', name='运行预检').click()
                        expect(page.get_by_text('预检通过', exact=True)).to_be_visible()
                        page.get_by_role('button', name='创建运行计划').click()
                        page.wait_for_url('**/evaluations/*')
                        page.get_by_role('button', name='启动 mock').wait_for()
                        rid = page.url.rsplit('/', 1)[-1]
                        page.get_by_role('button', name='启动 mock').click()
                    result['run_id'] = rid

                    deadline = time.monotonic() + 180
                    while True:
                        response = client.get(f'/api/v1/evaluations/{rid}')
                        response.raise_for_status()
                        run = response.json()
                        if run['run']['status'] in ('finished', 'failed', 'cancelled'):
                            break
                        if time.monotonic() >= deadline:
                            raise AssertionError('Mock run did not finish before deadline')
                        time.sleep(.25)
                    assert run['run']['status'] == 'finished', run['counts']
                    assert run['tasks'] and all(t['status'] == 'finished' for t in run['tasks'])
                    manifest = run['run']['manifest']
                    assert all(m['provider'] == 'mock' for m in manifest['models'])
                    result['manifest_sha256'] = manifest['manifest_sha256']
                    result['task_counts'] = run['counts']
                    if previous:
                        assert result['manifest_sha256'] == previous['manifest_sha256']
                        assert result['task_counts'] == previous['task_counts']

                    for name in ('manifest.json', 'integrity.json', 'summary.json',
                                 'metrics.csv', 'seed_level.csv', 'report.md'):
                        artifact = client.get(f'/api/v1/evaluations/{rid}/artifacts/{name}')
                        artifact.raise_for_status()
                        assert artifact.content
                        (output / name).write_bytes(artifact.content)
                    assert json.loads((output / 'integrity.json').read_text())['complete']
                    result['integrity_complete'] = True

                    page.goto(base_url + '/evaluations/' + rid)
                    expect(page.get_by_text('完整性审计通过', exact=True)).to_be_visible(timeout=15000)
                    page.screenshot(path=str(output / 'report.png'), full_page=True)
                    page.get_by_role('link', name='查看对应比赛回放').click()
                    expect(page.get_by_role('button', name='▶ 播放', exact=True)).to_be_visible()
                    page.screenshot(path=str(output / 'replay.png'), full_page=True)

                    mid = run['tasks'][0]['match_id']
                    match = client.get('/api/v1/matches/' + mid)
                    match.raise_for_status()
                    hand = client.get(f'/api/v1/matches/{mid}/hands/1')
                    hand.raise_for_status()
                    assert hand.json()['table_a'] and hand.json()['table_b']
                    snapshot = page.evaluate('''mid => new Promise((resolve, reject) => {
                        const url = new URL('/ws/match/' + mid, location.href);
                        url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
                        const ws = new WebSocket(url);
                        const timer = setTimeout(() => { ws.close(); reject(new Error('WS timeout')); }, 10000);
                        ws.onerror = () => { clearTimeout(timer); ws.close(); reject(new Error('WS failed')); };
                        ws.onmessage = event => {
                            const message = JSON.parse(event.data);
                            if (message.type === 'match_state') {
                                clearTimeout(timer); ws.close(); resolve(message.payload);
                            }
                        };
                    })''', mid)
                    assert snapshot['status'] == 'finished'
                    assert snapshot['score'] == match.json()['score']
                    assert snapshot['watermark'] > 0
                    result['terminal_snapshot'] = {k: snapshot[k] for k in ('status', 'score', 'watermark')}
                    result['match_id'] = mid
                    if previous:
                        assert result['match_id'] == previous['match_id']
                        assert result['terminal_snapshot'] == previous['terminal_snapshot']
                    assert not result['page_errors'], result['page_errors']
                    result['complete'] = True
                finally:
                    context.tracing.stop(path=str(output / 'trace.zip'))
                    browser.close()
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='Frontend URL of a running deployment')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume-from', type=Path, help='Previous result.json; verify without creating another run')
    parser.add_argument('--chromium-arg', action='append', default=[],
                        help='Extra browser option, e.g. --chromium-arg=--js-flags=--jitless')
    args = parser.parse_args()
    previous = json.loads(args.resume_from.read_text()) if args.resume_from else None
    if previous and not previous.get('complete'):
        raise ValueError('Restart check requires a successful previous verification')
    args.output.mkdir(parents=True, exist_ok=False)
    print(json.dumps(verify(args.base_url.rstrip('/'), args.output, previous, args.chromium_arg),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
