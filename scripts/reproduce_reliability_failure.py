"""Run under doudizhu-arena conda; --backend can point to baseline checkout."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--backend',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
sys.path.insert(0,str(Path(args.backend).resolve()))
from arena.agent.parser import parse_play_response,ParseError
from arena.agent.llm_agent import LLMAgent
from arena.api.event_bus import MatchEventBus
from arena.engine.deck import Deck
from arena.engine.state import GameEngine,TableState
from arena.engine.rules import InvalidPlayError

state=TableState(table='A')
GameEngine.init_hand(state,hand_num=1,dealer='S',idle_seat='W',deal_result=Deck('failure-leader-pass-v1').deal())
state.seat_teams={'S':'red','N':'red','E':'blue','W':'blue'}
GameEngine.start_bidding(state);GameEngine.submit_bid(state,'S',3,0)
GameEngine.finalize_bidding(state);GameEngine.start_playing(state)
hand=list(state.live_hands['S'].cards)
raw='{"reasoning":"pass on lead","action":{"type":"pass"}}'
result={'kind':'executed-engine-regression-not-real-model','seed':'failure-leader-pass-v1',
        'observation':{'seat':'S','current_trick':None,'hand_cards':[str(c) for c in hand]},'raw_output':raw}
try:
    cards,_=parse_play_response(raw,hand,None);result['parser_accepted']=True
except ParseError as exc:
    result['parser_accepted']=False;result['error']=str(exc)
    result['feedback']=LLMAgent._append_retry_feedback('冻结观察',str(exc),1)
try:
    GameEngine.submit_play(state,'S',[],0);result['engine_accepted']=True
    result['actual_action']=[]
except InvalidPlayError as exc:
    result['engine_accepted']=False;result['engine_error']=str(exc)
    repaired=json.dumps({'reasoning':'lead requires a card','action':{'type':'play','cards':[str(hand[-1])]}},ensure_ascii=False)
    cards,_=parse_play_response(repaired,hand,None)
    GameEngine.submit_play(state,'S',cards,0)
    result['repair_output']=repaired;result['actual_action']=[str(c) for c in cards]
result['hand_size_before']=len(hand);result['hand_size_after']=state.live_hands['S'].size

async def fanout():
    bus=MatchEventBus('failure-broadcast-v1')
    if hasattr(bus,'subscribe'):
        a,b=bus.subscribe(),bus.subscribe()
        for i in range(2):await bus.put({'type':'action','payload':{'n':i}})
        return {'viewer_a':[(await a.get())['payload']['n'] for _ in range(2)],'viewer_b':[(await b.get())['payload']['n'] for _ in range(2)]}
    for i in range(2):await bus.put({'type':'action','payload':{'n':i}})
    return {'viewer_a':[(await bus.get())['payload']['n']],'viewer_b':[(await bus.get())['payload']['n']]}
result['broadcast']=asyncio.run(fanout())
Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k in ('parser_accepted','engine_accepted','actual_action','broadcast')},ensure_ascii=False))
