"""Frozen-observation experiment, separate from closed-loop match outcomes.

Source splits are by seed AND match, never by individual decisions. A corpus
freezes the exact initial prompt and visible observation. Retry text alone varies.
"""
import argparse
import asyncio
import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from ..agent.parser import parse_bid_response, parse_play_response, ParseError
from ..agent.llm_agent import LLMAgent
from ..engine.card import Card, Rank, Suit
from ..engine.trick import Trick, PatternType
from ..engine.projection import digest
from ..llm.base import LLMError
from .report import ratio, latency, rows


def validate_corpus(corpus):
    if corpus.get('sha256') != digest({k:v for k,v in corpus.items() if k!='sha256'}):
        raise ValueError('corpus hash mismatch')
    seen_seeds,seen_matches={},{}
    for item in corpus['observations']:
        for key,seen in [('source_seed',seen_seeds),('source_match',seen_matches)]:
            source=item[key]
            if source in seen and seen[source]!=item['split']:
                raise ValueError(f'source leakage: {key}')
            seen[source]=item['split']
        if item['split'] not in ('development','test'): raise ValueError('invalid split')


def export_corpus(repo,run_id,path,development_seeds,per_seed=8):
    source=rows(repo,"""SELECT d.*,t.seed FROM decisions d
        JOIN task_attempts a ON a.id=d.task_attempt_id JOIN evaluation_tasks t ON t.id=a.task_id
        WHERE d.run_id=? AND d.variant_id='rule_feedback' ORDER BY t.seed,d.phase,d.action_seq,d.seat""",(run_id,))
    counts=defaultdict(int);items=[]
    for d in source:
        if counts[d['seed']]>=per_seed: continue
        call=repo.conn.execute("""SELECT e.system_prompt,e.user_prompt FROM llm_call_logs l JOIN call_evidence e ON e.call_id=l.id
            WHERE l.decision_id=? AND l.attempt=1""",(d['decision_id'],)).fetchone()
        if not call: continue
        counts[d['seed']]+=1
        obs=json.loads(d['observation_json'])
        items.append({'observation_id':d['observation_hash'],'source_seed':d['seed'],'source_match':d['match_id'],
            'source_decision':d['decision_id'],'phase':d['phase'],'split':'development' if d['seed'] in development_seeds else 'test',
            'category':'bidding' if d['phase']=='bidding' else ('lead' if obs['current_trick'] is None else 'follow'),
            'observation':obs,'system_prompt':call[0],'user_prompt':call[1]})
    corpus={'version':'fixed-observation-v1','source_run':run_id,'provenance':'mock' if json.loads(repo.get_evaluation_run(run_id)['manifest_json'])['models'][0]['provider']=='mock' else 'real',
            'sampling':f'first {per_seed} ordered decisions per seed from rule_feedback; engineering fixture, not representative benchmark','observations':items}
    corpus['sha256']=digest(corpus)
    validate_corpus(corpus)
    Path(path).write_text(json.dumps(corpus,ensure_ascii=False,indent=2),encoding='utf-8')
    return corpus


def validate_output(item,raw):
    obs=item['observation']
    if item['phase']=='bidding': return parse_bid_response(raw,obs['current_high_bid'])[0]
    target=obs['current_trick']
    if target:
        def card(c): return Card(rank=Rank(c['rank']),suit=Suit(c['suit']) if c['suit'] is not None else None)
        target=Trick(pattern=PatternType(target['pattern']),main_cards=tuple(card(c) for c in target['main_cards']),
            attached=tuple(card(c) for c in target['attached']),main_rank=Rank(target['main_rank']) if target['main_rank'] else None,length=target['length'])
    return [str(c) for c in parse_play_response(raw,[Card.from_string(c) for c in obs['hand_cards']],target)[0]]


async def run_fixed(corpus,provider_factory,*,split='test',retries=2):
    validate_corpus(corpus)
    results=[]
    for item in corpus['observations']:
        if item['split']!=split: continue
        for variant in ('single_generation','generic_retry','rule_feedback'):
            provider=provider_factory();user=item['user_prompt'];attempts=[];action=None
            start=time.monotonic()
            for index in range(1,2 if variant=='single_generation' else retries+2):
                raw=None;error=None;returned=False;valid=False
                before=time.monotonic()
                try:
                    raw=await asyncio.wait_for(provider.generate(user,item['system_prompt']),10)
                    returned=True;action=validate_output(item,raw);valid=True
                except (ParseError,LLMError,asyncio.TimeoutError) as exc: error=str(exc)
                attempts.append({'attempt':index,'returned':returned,'valid':valid,'raw_output':raw,'error':error,'user_prompt':user,
                    'latency_ms':(time.monotonic()-before)*1000})
                if valid: break
                user=LLMAgent._append_retry_feedback(user,error,index) if variant=='rule_feedback' else user+'\n\n请重新独立生成一个完整 JSON 回复。'
            results.append({'observation_id':item['observation_id'],'source_seed':item['source_seed'],'source_match':item['source_match'],
                'variant':variant,'phase':item['phase'],'success':attempts[-1]['valid'],'actual_action':action,
                'latency_ms':(time.monotonic()-start)*1000,'attempts':attempts})
    return results


def export_fixed(corpus,results,path):
    out=Path(path);out.mkdir(parents=True,exist_ok=True)
    summary=[]
    for variant in ('single_generation','generic_retry','rule_feedback'):
        for phase in ('bidding','playing'):
            ds=[r for r in results if r['variant']==variant and r['phase']==phase]
            returned=sum(d['attempts'][0]['returned'] for d in ds)
            summary.append({'variant':variant,'phase':phase,'n':len(ds),
                'first_availability':ratio(returned,len(ds)),
                'first_legality':ratio(sum(d['attempts'][0]['valid'] for d in ds),returned),
                'model_success':ratio(sum(d['success'] for d in ds),len(ds)),
                'calls':sum(len(d['attempts']) for d in ds),'latency_ms':latency([d['latency_ms'] for d in ds])})
    payload={'kind':'fixed_observation','provenance':corpus['provenance'],'corpus_sha256':corpus['sha256'],
             'summary':summary,'decisions':results,'limitations':['test split only; development excluded','mock behavior is synthetic; no model improvement claim','no fallback actions are executed in fixed observation mode']}
    (out/'fixed-observation.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    with (out/'paired-seed.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(['source_seed','variant','n','model_successes'])
        for seed in sorted({r['source_seed'] for r in results}):
            for variant in ('single_generation','generic_retry','rule_feedback'):
                ds=[r for r in results if r['source_seed']==seed and r['variant']==variant]
                writer.writerow([seed,variant,len(ds),sum(d['success'] for d in ds)])
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--mock',action='store_true',required=True)
    args=parser.parse_args()
    corpus=json.loads(Path(args.corpus).read_text())
    from .mock import ReliabilityMockProvider
    results=asyncio.run(run_fixed(corpus,ReliabilityMockProvider))
    export_fixed(corpus,results,args.output)
    print(json.dumps({'corpus_sha256':corpus['sha256'],'decisions':len(results),'provenance':'mock'}))


if __name__=='__main__': main()
