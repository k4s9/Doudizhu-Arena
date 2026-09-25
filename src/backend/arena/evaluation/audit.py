"""Semantic checks independent of the stored success labels and aggregates."""
import hashlib
import json
from collections import Counter
from decimal import Decimal

from ..engine.projection import digest


def decision_issues(repo, decision, calls):
    from .observations import validate_output
    d = decision
    issues = []
    name = d['decision_id']
    try:
        observation = json.loads(d['observation_json'])
        if digest(observation) != d['observation_hash']:
            issues.append(f'observation hash mismatch: {name}')
        valid_calls = []
        for call in calls:
            evidence = repo.conn.execute('SELECT * FROM call_evidence WHERE call_id=?', (call['id'],)).fetchone()
            if evidence is None:
                continue  # The structural audit reports this.
            if bool(call['success']) != (evidence['provider_status'] == 'returned'):
                issues.append(f'provider status mismatch: {call["id"]}')
            if not call['success']:
                continue
            raw = evidence['raw_output']
            events = repo.conn.execute('SELECT * FROM decision_events WHERE decision_id=? AND attempt=?', (name, call['attempt'])).fetchall()
            try:
                action = validate_output({'phase': d['phase'], 'observation': observation}, raw)
                valid = True
            except Exception:
                action, valid = None, False
            if len(events) == 1:
                event = events[0]
                if not isinstance(raw, str) or event['output_sha256'] != hashlib.sha256(raw.encode()).hexdigest():
                    issues.append(f'output hash mismatch: {call["id"]}')
                if valid != (not event['is_illegal'] and not event['error_code']):
                    issues.append(f'validation disagrees with replayed output: {call["id"]}')
            if valid:
                valid_calls.append((call['attempt'], action))
        if d['resolution'] in ('model_first', 'model_retry'):
            expected_attempt = 1 if d['resolution'] == 'model_first' else len(calls)
            if (not calls or (d['resolution'] == 'model_retry' and len(calls) < 2)
                    or len(valid_calls) != 1 or valid_calls[0][0] != expected_attempt
                    or calls[-1]['attempt'] != expected_attempt):
                issues.append(f'model resolution lacks matching valid final attempt: {name}')
            elif valid_calls:
                proposed = valid_calls[0][1]
                actual = {'bid': proposed} if d['phase'] == 'bidding' else {
                    'type': 'play' if proposed else 'pass', 'cards': proposed}
                if json.loads(d['proposed_action'] or 'null') != proposed or json.loads(d['actual_action'] or 'null') != actual:
                    issues.append(f'model output differs from committed action: {name}')
        elif d['resolution'] == 'system_autoplay' and calls:
            issues.append(f'autoplay unexpectedly contains model calls: {name}')
        if d['resolution'] in ('cancelled', 'failed_no_action') and d['action_id'] is not None:
            issues.append(f'noncommitted resolution has an action: {name}')
    except (ValueError, TypeError, KeyError) as exc:
        issues.append(f'invalid decision evidence {name}: {type(exc).__name__}')
    return issues


def budget_issues(repo, run_id, manifest):
    calls = {r['id']: dict(r) for r in repo.conn.execute('SELECT * FROM llm_call_logs WHERE run_id=?', (run_id,))}
    ledger = [dict(r) for r in repo.conn.execute('SELECT * FROM budget_ledger WHERE run_id=?', (run_id,))]
    links = Counter(r['call_id'] for r in ledger)
    issues = [f'budget call linkage: {cid}' for cid in calls if links[cid] != 1]
    pricing = manifest['models'][0]['pricing']
    for entry in ledger:
        call = calls.get(entry['call_id'])
        if call is None:
            issues.append(f'orphan budget reservation: {entry["reservation_id"]}')
            continue
        known = call['prompt_tokens'] is not None and call['completion_tokens'] is not None
        if known:
            expected = (Decimal(call['prompt_tokens']) * Decimal(str(pricing['input_per_million']))
                        + Decimal(call['completion_tokens']) * Decimal(str(pricing['output_per_million']))) / 1_000_000
            if entry['status'] != 'settled' or entry['actual_usd'] is None or abs(Decimal(str(entry['actual_usd'])) - expected) > Decimal('1e-12'):
                issues.append(f'budget amount/status mismatch: {entry["reservation_id"]}')
        elif entry['actual_usd'] is not None or entry['status'] != 'unknown':
            issues.append(f'unknown usage must retain reservation: {entry["reservation_id"]}')
    return issues
