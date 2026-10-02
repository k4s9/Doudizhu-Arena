"""Verify an interrupted random-agent match across an external backend restart.

Run only against an isolated acceptance deployment. Creates eight random players
and two matches; never uses a model provider or changes existing players.
Use the doudizhu-arena conda Python. Output directories must be new.
"""
import argparse
import json
from pathlib import Path
import time
import uuid

import httpx


def verify(base_url, previous):
    with httpx.Client(base_url=base_url, trust_env=False, timeout=20) as client:
        def request(method, path, **kwargs):
            response = client.request(method, '/api/v1' + path, **kwargs)
            response.raise_for_status()
            return response.json()

        for _ in range(60):
            try:
                request('GET', '/health')
                break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            raise AssertionError('Backend did not become healthy')

        def make_match(players, hands):
            return request('POST', '/matches', json={
                'name': 'Recovery acceptance ' + uuid.uuid4().hex[:8],
                'team_red': {'agents': players[:4]}, 'team_blue': {'agents': players[4:]},
                'config': {'total_hands': hands, 'max_tiebreaker_hands': 0,
                           'ko_enabled': False, 'seed': 'recovery-acceptance-v1',
                           'enable_reflection': False, 'enable_summary': True,
                           'persist_long_term_memory': True},
            })['id']

        if previous is None:
            config = request('POST', '/configs', json={
                'name': 'Recovery acceptance ' + uuid.uuid4().hex[:8],
                'provider': 'random', 'model': 'random',
            })
            players = [request('POST', '/players', json={
                'config_id': config['id'], 'display_name': f'Recovery player {i}',
            })['id'] for i in range(8)]
            memory = '重启前保存的经验；随机基线不使用模型记忆。'
            request('PUT', '/players/' + players[0], json={'long_term_memory': memory})
            match_id = make_match(players, 20)
            request('POST', f'/matches/{match_id}/start')
            request('POST', f'/matches/{match_id}/pause')
            # Let the current action reach its pause boundary before sampling.
            time.sleep(.5)
            match = request('GET', '/matches/' + match_id)
            assert match['status'] in ('running', 'paused'), match['status']
            return {'kind': 'active-match-restart', 'prepared': True,
                    'match_id': match_id, 'players': players, 'memory': memory,
                    'score': match['score'], 'current_hand': match['current_hand']}

        assert previous.get('prepared'), 'Requires a successful pre-restart fixture'
        match = request('GET', '/matches/' + previous['match_id'])
        assert match['status'] == 'interrupted', match['status']
        assert match['score'] == previous['score']
        assert match['current_hand'] == previous['current_hand']
        player = request('GET', '/players/' + previous['players'][0])
        assert player['long_term_memory'] == previous['memory']
        assert player['matches_played'] == 0, 'Interrupted match must not settle a full match'
        next_id = make_match(previous['players'], 1)
        request('POST', f'/matches/{next_id}/start')  # Must not retain PLAYER_BUSY.
        for _ in range(600):
            next_match = request('GET', '/matches/' + next_id)
            if next_match['status'] in ('finished', 'interrupted'):
                break
            time.sleep(.1)
        assert next_match['status'] == 'finished'
        for player_id in previous['players']:
            assert request('GET', '/players/' + player_id)['matches_played'] == 1
        assert request('GET', '/players/' + previous['players'][0])['long_term_memory'] == previous['memory']
        return {**previous, 'complete': True, 'status_after_restart': match['status'],
                'next_match_id': next_id, 'next_match_finished': True,
                'preserved_score': True, 'preserved_memory': True,
                'released_player_claims': True, 'settled_matches_per_player': 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume-from', type=Path)
    args = parser.parse_args()
    previous = json.loads(args.resume_from.read_text()) if args.resume_from else None
    args.output.mkdir(parents=True, exist_ok=False)
    result = {'complete': False}
    try:
        result = verify(args.base_url.rstrip('/'), previous)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
