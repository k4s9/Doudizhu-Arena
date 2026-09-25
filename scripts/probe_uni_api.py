"""Probe configured Uni API models without exposing credentials or error bodies.

Run explicitly with the doudizhu-arena conda Python and -I -B. Each selected
configuration makes one real request, with SDK retries disabled.
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/backend'))


async def probe(config, timeout, max_tokens):
    from openai import AsyncOpenAI

    result = {key: config[key] for key in ('name', 'provider', 'model', 'base_url')}
    result.update(timeout_seconds=timeout, max_tokens=max_tokens, sdk_retries=0)
    start = time.monotonic()
    try:
        async with AsyncOpenAI(api_key=config['api_key'], base_url=config['base_url'],
                               max_retries=0, timeout=timeout) as client:
            response = await asyncio.wait_for(client.chat.completions.create(
                model=config['model'], max_tokens=max_tokens, temperature=0, top_p=1,
                messages=[{'role': 'system', 'content': 'Return only valid JSON.'},
                          {'role': 'user', 'content': 'Reply with {"ok":true}.'}],
            ), timeout=timeout)
        choice = response.choices[0]
        content = choice.message.content or ''
        try:
            valid = json.loads(content) == {'ok': True}
        except (ValueError, TypeError):
            valid = False
        result.update(ok=valid, response_model=response.model,
                      finish_reason=choice.finish_reason, content_chars=len(content),
                      content=content.replace(config['api_key'], '[REDACTED]'),
                      reasoning_chars=len(getattr(choice.message, 'reasoning_content', '') or ''),
                      usage=response.usage.model_dump() if response.usage else None)
    except Exception as exc:
        result.update(ok=False, error_type=type(exc).__name__,
                      http_status=getattr(exc, 'status_code', None))
    result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    return result


async def main(args):
    from arena.config.settings import settings
    from arena.agent.loader import load_agents_from_yaml
    from arena.db.repository import DatabaseRepository
    from arena.security.credentials import has_usable_credential

    repo = DatabaseRepository(':memory:')
    repo.init()
    try:
        load_agents_from_yaml(repo, settings.agents_yaml_path)
        configs = [repo.get_player_config_by_name(name) for name in args.config]
        if any(not c or c['provider'] != 'openai' or not has_usable_credential(c['api_key']) for c in configs):
            raise ValueError('Selected OpenAI-compatible configuration unavailable')
        # Claim the output before making requests; never overwrite old evidence.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            results = await asyncio.gather(*(probe(c, args.timeout, args.max_tokens) for c in configs))
            report = {'kind': 'real-uni-api-probe', 'ok': all(r['ok'] for r in results), 'results': results}
            text = json.dumps(report, ensure_ascii=False, indent=2)
            handle.write(text + '\n')
        print(text, flush=True)
        return 0 if report['ok'] else 1
    finally:
        repo.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', action='append', required=True)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--max-tokens', type=int, default=2048)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_tokens <= 0:
        parser.error('timeout and max-tokens must be positive')
    logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(main(args)))
