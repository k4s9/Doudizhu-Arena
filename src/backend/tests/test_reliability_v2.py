"""Acceptance boundaries: decisions, fanout, budgets, recovery and full E2E."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path
import pytest
from arena.agent.llm_agent import LLMAgent
from arena.agent.parser import ParseError, parse_play_response
from arena.api.event_bus import MatchEventBus
from arena.db.repository import DatabaseRepository
from arena.engine.deck import Deck
from arena.engine.rules import InvalidPlayError
from arena.engine.state import GameEngine
from arena.engine.timeout import TimeoutManager, TimeoutConfig
from arena.evaluation.budget import BudgetLedger, BudgetExceeded
from arena.evaluation.runner import EvaluationRunner
from arena.evaluation.spec import build_run_manifest, load_experiment_spec, PreflightError
from arena.evaluation.report import ratio, latency, metric_group, build_report
from arena.llm.base import AbstractLLMProvider
from arena.llm.logging import LoggingLLMProvider
from arena.tournament.table import TableRunner

ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture
def repo(tmp_path):
    repo=DatabaseRepository(str(tmp_path/'test.db'));repo.init()
    yield repo
    repo.close()

@pytest.fixture
def spec():
    return load_experiment_spec(ROOT/'src/backend/evaluation/experiments/mock-v2.yaml')

class Outputs(AbstractLLMProvider):
    provider_name='test';model='test'
    def __init__(self, outputs): self.outputs=iter(outputs);self.last_usage=None
    async def generate(self,user_prompt,system_prompt=''):
        value=next(self.outputs)
        if isinstance(value,BaseException): raise value
        return value

class Hangs(Outputs):
    def __init__(self): super().__init__([])
    async def generate(self,user_prompt,system_prompt=''): await asyncio.Event().wait()

def table(repo,provider):
    mid=repo.create_match('test',{},'v2');hid=repo.create_hand(mid,1,'S','W','v2/hand-1');thid=repo.create_table_hand(hid,'A')
    agent=LLMAgent('test',LoggingLLMProvider(provider,repo=repo,agent_id='test'),retry_limit=1)
    agent.set_observability_context(repo,match_id=mid,table_hand_id=thid)
    tm=TimeoutManager(TimeoutConfig(individual_play_seconds=.01,bidding_seconds=.01,max_consecutive_llm_failures=2))
    teams={'S':'red','N':'red','E':'blue','W':'blue'};tm.init_teams(teams)
    runner=TableRunner('A',{'S':agent},teams,timeout_manager=tm,db_repo=repo,match_id=mid)
    runner._current_table_hand_id=thid
    GameEngine.init_hand(runner._state,hand_num=1,dealer='S',idle_seat='W',deal_result=Deck('v2/hand-1').deal())
    runner._state.seat_teams=teams
    GameEngine.start_bidding(runner._state);GameEngine.submit_bid(runner._state,'S',3,0)
    GameEngine.finalize_bidding(runner._state);GameEngine.start_playing(runner._state)
    return runner,agent

def test_leader_pass_rejected_without_state_mutation(repo):
    runner,_=table(repo,Outputs([]));hand=list(runner._state.live_hands['S'].cards)
    with pytest.raises(ParseError,match='领出'): parse_play_response('{"action":{"type":"pass"}}',hand)
    with pytest.raises(InvalidPlayError,match='Leader'): GameEngine.submit_pass(runner._state,'S',0)
    assert not runner._state.play_history and list(runner._state.live_hands['S'].cards)==hand

def test_timeout_fallback_nonempty_counts_decisions(repo):
    runner,agent=table(repo,Hangs())
    async def run(): return [await runner._request_action('S','playing') for _ in range(3)]
    first,second,third=asyncio.run(run())
    assert first[1] and first[3:5]==('system_fallback','timeout')
    assert second[1] and agent.consecutive_failures==2 and third[3]=='system_autoplay'
    logged = [r[0] for r in repo.conn.execute('SELECT decision_id FROM llm_call_logs')]
    assert len(logged) == 2 and set(logged) == {first[0], second[0]}

def test_retry_success_resets_shared_failure_count(repo):
    runner,agent=table(repo,Outputs([]));card=str(runner._state.live_hands['S'].cards[-1])
    agent._provider._inner=Outputs(['bad','bad','bad',json.dumps({'action':{'type':'play','cards':[card]}})])
    async def run():
        a=await runner._request_action('S','playing');assert agent.consecutive_failures==1
        return a,await runner._request_action('S','playing')
    a,b=asyncio.run(run())
    assert a[3]=='system_fallback' and b[3]=='model_retry'
    assert agent.consecutive_failures==runner.timeout.consecutive_failures('S')==0

def test_cancel_records_no_action(repo):
    runner,_=table(repo,Hangs())
    async def run():
        task=asyncio.create_task(runner._request_action('S','playing'));await asyncio.sleep(.001);task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run());d=dict(repo.conn.execute('SELECT * FROM decisions').fetchone())
    assert d['resolution']=='cancelled' and d['action_id'] is None

def test_atomic_action_rolls_back_before_resolution(repo):
    runner,_=table(repo,Outputs([]))
    with pytest.raises(RuntimeError):
        with repo.atomic():
            repo.add_bidding_record(runner._current_table_hand_id,1,'S',1,0)
            raise RuntimeError('disk failure before resolution')
    assert not repo.conn.execute('SELECT * FROM bidding_records').fetchall()

def test_fanout_slow_subscriber_and_close_wakeup():
    async def run():
        bus=MatchEventBus('m',maxsize=2);slow=bus.subscribe();a=bus.subscribe();b=bus.subscribe()
        seen_a=[];seen_b=[]
        for i in range(5):
            await bus.put({'type':'action','payload':{'n':i}})
            seen_a.append(await a.get());seen_b.append(await b.get())
        assert seen_a==seen_b and [e['seq'] for e in seen_a]==[1,2,3,4,5]
        assert (await slow.get())['type']=='resync_required' and slow not in bus._subscribers
        bus.close();assert await a.get() is None
    asyncio.run(run())

def test_snapshot_watermark_and_durable_cursor(repo):
    async def run():
        bus=MatchEventBus('m',repo=repo);bus.snapshot_factory=lambda:{'tables':{}}
        await bus.put({'type':'one','payload':{}});sub=bus.subscribe();snapshot=bus.snapshot()
        await bus.put({'type':'two','payload':{}});event=await sub.get()
        assert snapshot['watermark']==1 and event['seq']==2
        assert MatchEventBus('m',repo=repo).seq==2
    asyncio.run(run())

def test_budget_concurrent_reservation_unknown_usage(repo):
    budget=BudgetLedger(repo,'r',.00202,10,2000,10,{'input_per_million':1,'output_per_million':1})
    async def call(): return budget.reserve('','')
    async def run(): return await asyncio.gather(call(),call(),return_exceptions=True)
    result=asyncio.run(run());assert isinstance(result[1],BudgetExceeded)
    budget.settle(result[0],'call',None)
    assert repo.conn.execute('SELECT actual_usd,status FROM budget_ledger').fetchone()[:]==(None,'unknown')

def test_metric_denominators_empty_return_errors_fallback_and_zero():
    ds=[{'decision_id':str(i),'resolution':r,'latency_ms':i*10,'reason':'timeout' if i==3 else None} for i,r in enumerate(['model_retry','system_fallback','system_autoplay','system_fallback'])]
    cs=[{'decision_id':str(i),'attempt':a,'success':ok,'latency_ms':5,'prompt_tokens':None,'completion_tokens':None} for i,a,ok in [(0,1,1),(0,2,1),(1,1,1),(3,1,0)]]
    es=[{'decision_id':'0','attempt':1,'is_illegal':1,'error_code':'json_format'}, {'decision_id':'0','attempt':2,'is_illegal':0,'error_code':None}, {'decision_id':'1','attempt':1,'is_illegal':1,'error_code':'json_format'}]
    m=metric_group(ds,cs,es)
    assert m['first_availability']==ratio(2,3) and m['first_output_legality']==ratio(0,2)
    assert m['model_success']==ratio(1,3) and m['fallback']==ratio(2,4)
    assert m['illegal_recovery']==ratio(1,2) and m['usage_coverage']==ratio(0,4)
    assert ratio(0,0)['value'] is None and latency([])['p95'] is None
    assert latency(list(range(1,21)))['p95']==19

def test_manifest_freezes_database_override_endpoint(repo,spec):
    m=spec.models[0].model_copy(update={'system_prompt':None,'base_url':None});spec=spec.model_copy(update={'models':[m]})
    cid=repo.create_player_config(m.config_name,'mock',m.model,'',base_url='https://example.test/v1',system_prompt='frozen')
    manifest=build_run_manifest(spec,ROOT,repo)
    repo.update_player_config(cid,system_prompt='changed',base_url='https://changed.test/v1')
    assert manifest.models[0].system_prompt=='frozen' and manifest.models[0].base_url=='https://example.test/v1'
    assert build_run_manifest(spec,ROOT,repo).manifest_sha256!=manifest.manifest_sha256

def test_run_identity_live_lease_exclusion(repo,spec):
    manifest=build_run_manifest(spec,ROOT,repo);a=EvaluationRunner(repo,str(ROOT));b=EvaluationRunner(repo,str(ROOT))
    rid=a.create_run(spec,manifest);assert rid!=b.create_run(spec,manifest)
    a._claim(rid)
    with pytest.raises(PreflightError,match='owned'): b._claim(rid)
    repo.conn.execute('UPDATE evaluation_leases SET expires_at=0');repo.conn.commit();b._claim(rid)

def test_full_mock_report_replay_resume_and_missing_call(repo,spec):
    manifest=build_run_manifest(spec,ROOT,repo);runner=EvaluationRunner(repo,str(ROOT));rid=runner.create_run(spec,manifest)
    asyncio.run(runner.run(rid,spec,manifest,mock=True))
    _,integrity,summary,metrics,seeds,failures=build_report(repo,rid)
    assert repo.get_evaluation_run(rid)['status']=='finished' and integrity['complete']
    assert summary['table_status_counts']=={'void':4,'finished':8} and summary['normal_table_completion']==ratio(8,12)
    assert len(metrics)==6 and len(seeds)==6 and failures
    count=repo.conn.execute('SELECT COUNT(*) FROM task_attempts').fetchone()[0]
    asyncio.run(runner.run(rid,spec,manifest,mock=True))
    assert repo.conn.execute('SELECT COUNT(*) FROM task_attempts').fetchone()[0]==count
    repo.conn.execute("UPDATE llm_call_logs SET decision_id='missing' WHERE id=(SELECT id FROM llm_call_logs LIMIT 1)");repo.conn.commit()
    assert not build_report(repo,rid)[1]['complete']

def test_fixed_observation_source_split_and_equal_retry_budget(repo,spec,tmp_path):
    from arena.evaluation.observations import validate_corpus,run_fixed
    from arena.engine.projection import digest
    item={'observation_id':'o','source_seed':'s','source_match':'m','split':'test','phase':'bidding',
          'observation':{'current_high_bid':0},'system_prompt':'bidding','user_prompt':'frozen'}
    corpus={'observations':[item]};corpus['sha256']=digest(corpus)
    validate_corpus(corpus)
    result=asyncio.run(run_fixed(corpus,lambda: Outputs(['invalid','invalid','{"bid":1}'])))
    assert [len(r['attempts']) for r in result]==[1,3,3]
    assert '上次回复有误' not in result[1]['attempts'][1]['user_prompt']
    assert '上次回复有误' in result[2]['attempts'][1]['user_prompt']
    leaked={'observations':[item,{**item,'split':'development'}]};leaked['sha256']=digest(leaked)
    with pytest.raises(ValueError,match='leakage'):validate_corpus(leaked)


def test_cancel_and_recover_whole_task_attempt(repo,spec,monkeypatch):
    from arena.evaluation.mock import ReliabilityMockProvider
    from arena.db import reliability
    original=ReliabilityMockProvider.generate
    async def hang(*args):await asyncio.Event().wait()
    monkeypatch.setattr(ReliabilityMockProvider,'generate',hang)
    manifest=build_run_manifest(spec,ROOT,repo);runner=EvaluationRunner(repo,str(ROOT));rid=runner.create_run(spec,manifest)
    async def cancel_run():
        job=asyncio.create_task(runner.run(rid,spec,manifest,mock=True))
        while not repo.conn.execute('SELECT 1 FROM decisions').fetchone():await asyncio.sleep(.001)
        runner.cancel();await asyncio.wait_for(job,spec.cancellation_deadline_seconds)
    asyncio.run(cancel_run())
    assert repo.get_evaluation_run(rid)['status']=='cancelled'
    assert repo.conn.execute("SELECT COUNT(*) FROM decisions WHERE resolution='cancelled'").fetchone()[0]==2
    monkeypatch.setattr(ReliabilityMockProvider,'generate',original)
    resumed=EvaluationRunner(repo,str(ROOT));asyncio.run(resumed.run(rid,spec,manifest,mock=True))
    assert repo.get_evaluation_run(rid)['status']=='finished'
    statuses=[r[0] for r in repo.conn.execute('SELECT status FROM task_attempts')]
    assert 'cancelled' in statuses and statuses.count('finished')==6
    # Persisted running attempt represents process loss; it must become interrupted.
    rid2=runner.create_run(spec,manifest);task=repo.get_evaluation_tasks(rid2)[0]
    aid=reliability.start_attempt(repo,task['id']);repo.update_evaluation_run_status(rid2,'running')
    resumed=EvaluationRunner(repo,str(ROOT));resumed._claim(rid2);resumed._recover(rid2)
    assert repo.conn.execute('SELECT status FROM task_attempts WHERE id=?',(aid,)).fetchone()[0]=='interrupted'
