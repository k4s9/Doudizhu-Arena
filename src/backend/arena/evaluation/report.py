"""Evidence-first audits and offline reports. Ratios always retain denominators."""
from __future__ import annotations
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from ..engine.projection import digest, project
from .spec import ExperimentSpec


def rows(repo, sql, args=()):
    return [dict(r) for r in repo.conn.execute(sql, args)]


def ratio(n, d):
    return {"numerator": n, "denominator": d, "value": n/d if d else None}


def latency(values):
    values = sorted(v for v in values if v is not None)
    n = len(values)
    return {"n": n, "p50": values[math.ceil(n*.5)-1] if n else None,
            "p95": values[math.ceil(n*.95)-1] if n else None,
            "mean": sum(values)/n if n else None, "max": max(values) if n else None}


def replay_audit(repo, match_id):
    """Re-execute stored bids and plays with the same engine, verify every hash."""
    from ..engine.deck import Deck
    from ..engine.state import GameEngine, TableState, TablePhase
    from ..engine.card import Card
    from ..engine.scoring import calculate_hand_score, calculate_diff_score
    issues, hashes = [], {}
    total_red = total_blue = 0
    for hand in repo.get_hands_for_match(match_id):
        scores = {}
        for th in repo.get_table_hands_for_hand(hand["id"]):
            state = TableState(table=th["table"])
            GameEngine.init_hand(state, hand_num=hand["hand_num"], dealer=hand["dealer"], idle_seat=hand["idle_seat"], deal_result=Deck(hand["seed"]).deal())
            state.seat_teams = {"S":"red", "N":"red", "E":"blue", "W":"blue"} if th["table"] == "A" else {"S":"blue", "N":"blue", "E":"red", "W":"red"}
            try:
                GameEngine.start_bidding(state)
                decisions = rows(repo, "SELECT * FROM decisions WHERE table_hand_id=? AND action_id IS NOT NULL ORDER BY CASE phase WHEN 'bidding' THEN 0 ELSE 1 END, action_seq", (th["id"],))
                for d in decisions:
                    action = json.loads(d["actual_action"])
                    if d["phase"] == "bidding":
                        GameEngine.submit_bid(state, d["seat"], action["bid"], 0)
                    else:
                        if state.phase != TablePhase.PLAYING:
                            GameEngine.finalize_bidding(state)
                            GameEngine.start_playing(state)
                        GameEngine.submit_play(state, d["seat"], [Card.from_string(c) for c in action["cards"]], 0)
                    if digest(project(state)) != d["state_hash_after"]:
                        issues.append(f"state hash mismatch: {d['decision_id']}")
                if th["status"] == "finished":
                    if state.phase != TablePhase.FINISHED or not state.winner_seat:
                        issues.append(f"no normal terminal state: {th['id']}")
                    remaining = repo.get_remaining_hands_for_table(th["id"])
                    expected = {s:[str(c) for c in cards] for s,cards in GameEngine.get_remaining_hands(state).items()}
                    if expected != remaining:
                        issues.append(f"remaining hands mismatch: {th['id']}")
                if th['status'] in ('finished', 'void'):
                    is_void = state.phase == TablePhase.VOID
                    if is_void != (th['status'] == 'void') or bool(th['void']) != is_void:
                        issues.append(f"void terminal mismatch: {th['id']}")
                    score = calculate_hand_score(final_bid=state.final_bid,
                        winner_seat=state.winner_seat, winner_team=state.winner_team,
                        landlord_seat=state.landlord, bombs_played=state.bombs_played,
                        spring=state.winner_role == 'landlord' and state.farmer_has_not_played,
                        anti_spring=state.winner_role == 'farmer' and state.landlord_play_count <= 1,
                        void=is_void)
                    scores[th['table']] = score
                    fields = {'winner_team': score.winner_team, 'winner_role': score.winner_role,
                        'base_score': score.base_score, 'multiplier': score.multiplier,
                        'final_score': score.final_score, 'bombs_played': score.bombs,
                        'spring': int(score.spring), 'anti_spring': int(score.anti_spring)}
                    if any((th[k] or 0 if isinstance(v, int) else th[k] or '') != v for k, v in fields.items()):
                        issues.append(f"terminal score mismatch: {th['id']}")
                hashes[th["id"]] = digest(project(state))
            except Exception as exc:
                issues.append(f"replay failed {th['id']}: {exc}")
        if set(scores) == {'A', 'B'}:
            red, blue = calculate_diff_score(scores['A'], scores['B'])
            if (hand['diff_score_red'], hand['diff_score_blue']) != (red, blue):
                issues.append(f"hand score mismatch: {hand['id']}")
            total_red += red
            total_blue += blue
    match = repo.get_match(match_id)
    if match and match['status'] == 'finished' and (match['score_red'], match['score_blue']) != (total_red, total_blue):
        issues.append(f'match score mismatch: {match_id}')
    return issues, hashes


def audit_match(repo, match_id, spec):
    issues = []
    match = repo.get_match(match_id) if match_id else None
    if not match or match["status"] != "finished":
        return ["match missing or not finished"]
    hands = repo.get_hands_for_match(match_id)
    if not spec.total_hands <= len(hands) <= spec.total_hands + spec.max_tiebreaker_hands:
        issues.append("configured hand count not satisfied")
    for h in hands:
        tables = repo.get_table_hands_for_hand(h["id"])
        if h["status"] != "finished" or len(tables) != 2 or {t["table"] for t in tables} != {"A","B"}:
            issues.append(f"incomplete hand pair: {h['id']}")
        for t in tables:
            if t["status"] not in ("finished","void"):
                issues.append(f"nonterminal table: {t['id']}")
    stream = rows(repo, "SELECT * FROM match_events WHERE match_id=? ORDER BY seq", (match_id,))
    if [e['seq'] for e in stream] != list(range(1,len(stream)+1)):
        issues.append("event stream gap")
    linked = Counter(json.loads(e['event_json']).get('payload',{}).get('decision_id') for e in stream)
    for d in rows(repo,"SELECT decision_id FROM decisions WHERE match_id=? AND action_id IS NOT NULL",(match_id,)):
        if linked[d['decision_id']] != 1: issues.append(f"action event linkage: {d['decision_id']}")
    issues.extend(audit_decisions(repo, match_id=match_id))
    replay_issues, _ = replay_audit(repo, match_id)
    return issues + replay_issues


def audit_decisions(repo, *, match_id=None, run_id=None):
    key, value = ("match_id", match_id) if match_id else ("run_id", run_id)
    decisions = rows(repo, f"SELECT * FROM decisions WHERE {key}=?", (value,))
    issues = []
    for d in decisions:
        if d["resolution"] is None:
            issues.append(f"unresolved decision: {d['decision_id']}")
        committed = d["resolution"] in ("model_first","model_retry","system_fallback","system_autoplay")
        if committed:
            table = "bidding_records" if d["phase"] == "bidding" else "play_actions"
            a = repo.conn.execute(f"SELECT * FROM {table} WHERE id=?", (d["action_id"],)).fetchone()
            if not a:
                issues.append(f"missing committed action: {d['decision_id']}")
            else:
                action = {"bid": a["bid"]} if d["phase"] == "bidding" else {"type": a["action_type"], "cards": json.loads(a["cards"] or '[]')}
                if action != json.loads(d["actual_action"] or 'null') or a["seq"] != d["action_seq"] or a["seat"] != d["seat"]:
                    issues.append(f"actual action differs: {d['decision_id']}")
        calls = rows(repo, "SELECT l.*,e.provider_status,e.call_id AS evidence_id FROM llm_call_logs l LEFT JOIN call_evidence e ON e.call_id=l.id WHERE l.decision_id=? ORDER BY attempt", (d["decision_id"],))
        if d["resolution"] in ("model_first","model_retry") and not calls:
            issues.append(f"model success without call: {d['decision_id']}")
        if [c["attempt"] for c in calls] != list(range(1,len(calls)+1)):
            issues.append(f"noncontiguous attempts: {d['decision_id']}")
        for call in calls:
            if not call["evidence_id"]:
                issues.append(f"missing call evidence: {call['id']}")
            events = rows(repo, "SELECT * FROM decision_events WHERE decision_id=? AND attempt=?", (d["decision_id"],call["attempt"]))
            if call["success"] and len(events) != 1:
                issues.append(f"missing/duplicate validation: {call['id']}")
        from .audit import decision_issues
        issues.extend(decision_issues(repo, d, calls))
    calls = rows(repo, f"SELECT l.id FROM llm_call_logs l LEFT JOIN decisions d ON d.decision_id=l.decision_id WHERE l.{key}=? AND l.phase IN ('bidding','playing') AND d.decision_id IS NULL", (value,))
    issues += [f"orphan call: {c['id']}" for c in calls]
    # Every persisted action in this scope needs exactly one decision, including
    # actions whose resolution transaction might otherwise have been lost.
    for table in ("bidding_records","play_actions"):
        sql = f"""SELECT a.id,COUNT(d.decision_id) AS n FROM {table} a
            JOIN table_hands th ON th.id=a.table_hand_id JOIN hands h ON h.id=th.hand_id
            LEFT JOIN decisions d ON d.action_id=a.id
            WHERE h.match_id IN (SELECT DISTINCT match_id FROM decisions WHERE {key}=?)
            GROUP BY a.id HAVING n != 1"""
        issues += [f"action linkage: {r['id']}" for r in rows(repo,sql,(value,))]
    return issues


def metric_group(decisions, calls, events):
    by_call, by_event = defaultdict(list), defaultdict(list)
    for c in calls: by_call[c["decision_id"]].append(c)
    for e in events: by_event[e["decision_id"]].append(e)
    L = [d for d in decisions if by_call[d["decision_id"]]]
    first = {d["decision_id"]: min(by_call[d["decision_id"]],key=lambda c:c["attempt"]) for d in L}
    returned = {k for k,c in first.items() if c["success"]}
    valid = {k for k in returned if any(e["attempt"]==1 and not e["is_illegal"] and not e["error_code"] for e in by_event[k])}
    illegal = {k for k in returned if any(e["attempt"]==1 and e["is_illegal"] for e in by_event[k])}
    success = set()
    for d in L:
        cs = by_call[d['decision_id']]
        last = max(cs, key=lambda c: c['attempt'])
        expected = 'model_first' if len(cs) == 1 else 'model_retry'
        valid_final = any(e['attempt'] == last['attempt'] and not e['is_illegal'] and not e['error_code'] for e in by_event[d['decision_id']])
        if d['resolution'] == expected and last['success'] and valid_final:
            success.add(d['decision_id'])
    retried = {k for k,cs in by_call.items() if len(cs)>1}
    available_error = set(first)-returned
    counts = Counter(d["resolution"] or "running" for d in decisions)
    result = {"D":len(decisions),"L":len(L),"calls":len(calls),"resolutions":dict(counts),
        "first_availability":ratio(len(returned),len(L)),"first_output_legality":ratio(len(valid),len(returned)),
        "first_decision_success":ratio(len(valid),len(L)),"model_success":ratio(len(success),len(L)),
        "illegal_recovery":ratio(len(illegal & success),len(illegal)),
        "provider_error_recovery":ratio(len(available_error & success),len(available_error)),
        "fallback":ratio(counts['system_fallback'],len(decisions)),"autoplay":ratio(counts['system_autoplay'],len(decisions)),
        "retry":ratio(len(retried),len(L)),"attempt_distribution":dict(Counter(len(by_call[d['decision_id']]) for d in L)),
        "provider_latency_ms":latency([c['latency_ms'] for c in calls]),
        "decision_latency_ms":latency([d['latency_ms'] for d in decisions]),
        "success_latency_ms":latency([d['latency_ms'] for d in decisions if d['decision_id'] in success]),
        "failure_latency_ms":latency([d['latency_ms'] for d in decisions if d['decision_id'] not in success]),
        "timeout_latency_ms":latency([d['latency_ms'] for d in decisions if d['reason']=='timeout']),
        "usage_coverage":ratio(sum(c['prompt_tokens'] is not None and c['completion_tokens'] is not None for c in calls),len(calls)),
        "prompt_tokens_known":sum(c['prompt_tokens'] or 0 for c in calls),
        "completion_tokens_known":sum(c['completion_tokens'] or 0 for c in calls)}
    return result


def build_report(repo, run_id):
    run = repo.get_evaluation_run(run_id)
    manifest = json.loads(run['manifest_json'])
    spec = ExperimentSpec.model_validate(manifest['frozen_spec'])
    tasks = repo.get_evaluation_tasks(run_id)
    decisions = rows(repo,"SELECT * FROM decisions WHERE run_id=?",(run_id,))
    calls = rows(repo,"SELECT * FROM llm_call_logs WHERE run_id=?",(run_id,))
    events = repo.get_decision_events(run_id=run_id)
    attempts = rows(repo,"SELECT a.*,t.seed,t.variant_id FROM task_attempts a JOIN evaluation_tasks t ON t.id=a.task_id WHERE t.run_id=?",(run_id,))
    issues = audit_decisions(repo, run_id=run_id)
    from .audit import budget_issues
    issues.extend(budget_issues(repo, run_id, manifest))
    terminal_hashes = {}
    tables = []
    for attempt in attempts:
        if not attempt['match_id']: continue
        tables += rows(repo,"SELECT th.* FROM table_hands th JOIN hands h ON h.id=th.hand_id WHERE h.match_id=?",(attempt['match_id'],))
        if attempt['status']=='finished': issues += audit_match(repo,attempt['match_id'],spec)
        replay_issues, hashes = replay_audit(repo, attempt['match_id'])
        issues += replay_issues
        terminal_hashes.update(hashes)
    for task in tasks:
        if task['status']=='finished' and not any(a['task_id']==task['id'] and a['status']=='finished' for a in attempts):
            issues.append(f"finished task lacks audited attempt: {task['id']}")
    if any(t['status'] in ('planned','running') for t in tasks): issues.append('run contains nonterminal tasks')
    integrity = {'complete':not issues,'issues':sorted(set(issues)), 'run_status':run['status'], 'terminal_state_hashes':terminal_hashes}
    metrics = []
    for variant in manifest['variants']:
        for phase in ('bidding','playing'):
            ds=[d for d in decisions if d['variant_id']==variant['variant_id'] and d['phase']==phase]
            ids={d['decision_id'] for d in ds}
            metrics.append({'variant':variant['variant_id'],'phase':phase,**metric_group(ds,[c for c in calls if c['decision_id'] in ids],[e for e in events if e['decision_id'] in ids])})
    seed_level=[]
    for variant in manifest['variants']:
        for seed in manifest['seeds']:
            aids={a['id'] for a in attempts if a['seed']==seed and a['variant_id']==variant['variant_id']}
            ds=[d for d in decisions if d['task_attempt_id'] in aids]
            ids={d['decision_id'] for d in ds}
            seed_level.append({'variant':variant['variant_id'],'seed':seed,**metric_group(ds,[c for c in calls if c['decision_id'] in ids],[e for e in events if e['decision_id'] in ids])})
    ledger=rows(repo,'SELECT * FROM budget_ledger WHERE run_id=?',(run_id,))
    links = Counter(r['call_id'] for r in ledger)
    call_ids = {c['id'] for c in calls}
    covered = [r for r in ledger if r['call_id'] in call_ids and links[r['call_id']] == 1 and r['actual_usd'] is not None]
    normal=sum(t['status']=='finished' for t in tables)
    known=sum(r['actual_usd'] or 0 for r in ledger)
    summary={'metric_version':'reliability-v2','execution_kind':'mock' if manifest['models'][0]['provider']=='mock' else 'real',
        'run_id':run_id,'manifest_sha256':manifest['manifest_sha256'],'integrity_complete':integrity['complete'],
        'task_counts':dict(Counter(t['status'] for t in tasks)),'planned_tasks':len(tasks),'started_attempts':len(attempts),
        'planned_table_hands':len(tasks)*spec.total_hands*2,'started_table_hands':len(tables),
        'table_status_counts':dict(Counter(t['status'] for t in tables)),
        'normal_table_completion':ratio(normal,len(tables)),
        'match_latency_ms':latency([(a['finished_at']-a['started_at'])*1000 for a in attempts if a['finished_at']]),
        'known_cost_usd':known,'cost_coverage':ratio(len(covered),len(calls)),
        'budget_exposure_usd':sum(r['actual_usd'] if r['actual_usd'] is not None else r['reserved_usd'] for r in ledger),
        'cost_per_normal_table_usd':known/normal if normal and len(ledger)==len(calls) and all(r['actual_usd'] is not None for r in ledger) else None,
        'metrics':metrics,'limits':['mock results measure plumbing only','self-play does not establish competitive advantage',
        'seed is the independent cluster; decision counts are correlated','P95 uses nearest-rank; small samples are descriptive',
        'unknown usage retains reservation; known totals are lower bounds when coverage is incomplete']}
    failures=[]
    for d in decisions:
        if d['resolution'] not in ('model_first','system_autoplay') or any(e['decision_id']==d['decision_id'] and e['is_illegal'] for e in events):
            evidence=rows(repo,'SELECT l.*,e.system_prompt,e.user_prompt,e.raw_output,e.provider_status FROM llm_call_logs l LEFT JOIN call_evidence e ON e.call_id=l.id WHERE decision_id=? ORDER BY attempt',(d['decision_id'],))
            failures.append({'decision':d,'attempts':evidence,'validation':[e for e in events if e['decision_id']==d['decision_id']]})
    return manifest,integrity,summary,metrics,seed_level,failures


def export_report(repo,run_id,out):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    manifest,integrity,summary,metrics,seeds,failures=build_report(repo,run_id)
    for name,data in [('manifest',manifest),('integrity',integrity),('summary',summary)]:
        (out/f'{name}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,data in [('metrics',metrics),('seed_level',seeds)]:
        flattened=[]
        for row in data:
            flat={}
            for key,value in row.items():
                if isinstance(value,dict):
                    for sub,v in value.items(): flat[f'{key}_{sub}']=v
                else: flat[key]=value
            flattened.append(flat)
        with (out/f'{name}.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(dict.fromkeys(k for r in flattened for k in r)))
            writer.writeheader();writer.writerows(flattened)
    failure_dir=out/'failure-cases';failure_dir.mkdir(exist_ok=True)
    for failure in failures:
        (failure_dir/(failure['decision']['decision_id']+'.json')).write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=[f"# Reliability run {run_id}",f"Execution: {summary['execution_kind']}; integrity: {integrity['complete']}; status: {integrity['run_status']}",
        f"Manifest: {manifest['manifest_sha256']}",f"Tasks: {summary['task_counts']}; table-hands: {summary['table_status_counts']}",
        f"Known cost USD: {summary['known_cost_usd']}; coverage: {summary['cost_coverage']}",
        '', '| Variant | Phase | First availability | First output legality | Model success | Fallback |', '|---|---|---|---|---|---|']
    def fmt(r): return f"{r['numerator']}/{r['denominator']} ({r['value']:.1%})" if r['denominator'] else 'N/A (0/0)'
    for m in metrics: lines.append('| '+' | '.join([m['variant'],m['phase'],*[fmt(m[k]) for k in ('first_availability','first_output_legality','model_success','fallback')]])+' |')
    lines+=['','## Definitions','D: all started bidding/playing decisions including autoplay; L: decisions with an issued model call. R0: first provider returned (including empty text); V0: first returned output passed parser and rules. Availability R0/L; output legality V0/R0; first success V0/L. Model success counts model_first/model_retry only. Illegal recovery divides by all initially illegal returned outputs, even with no retry allowance. Fallback/autoplay divide by D. Retry divides by L. Normal table completion excludes void from the numerator but includes every started table in the denominator. Task attempts, cancellations, failures and unstarted plans are retained. Provider and end-to-end latency use nearest-rank, including failed calls. Unknown usage/cost is NULL, never zero.', '', '## Limits',*summary['limits'], '', '## Integrity issues', *(integrity['issues'] or ['None']), '', f'Failure traces: {len(failures)}; see failure-cases/.']
    # Table rows must be contiguous Markdown lines.
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary
