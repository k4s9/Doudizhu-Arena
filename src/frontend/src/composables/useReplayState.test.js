import { ref } from 'vue';
import { describe, expect, it } from 'vitest';
import { buildReplayTimeline, useReplayState } from './useReplayState.js';

describe('useReplayState', () => {
  it('sorts bidding hands and advances to the next bidder', () => {
    const tableDetail = ref({
      dealer: 'S',
      idle_seat: 'W',
      initial_hands: {
        S: ['♦3', '♠A', '♣2', '大王', '♥2', '小王', '♠2'],
        E: ['♠3'],
        N: ['♠4'],
      },
      players: {
        S: { agent_id: 'south-id', agent_name: 'Qwen-Balanced R1', team: 'red' },
        E: { agent_id: 'east-id', agent_name: 'Minimax-Flexible B4', team: 'blue' },
        N: { agent_id: 'north-id', agent_name: 'Qwen-Strategic R2', team: 'red' },
      },
      bidding: [
        { seat: 'S', bid: 1, timestamp_ms: 1 },
        { seat: 'E', bid: 2, timestamp_ms: 2 },
        { seat: 'N', bid: 0, timestamp_ms: 3 },
      ],
    });
    const currentStep = ref(0);
    const state = useReplayState({ tableDetail, currentStep });

    expect(state.value.tableData.current_seat).toBe('S');
    expect(state.value.tableData.players.S.agent_name).toBe('Qwen-Balanced R1');
    expect(state.value.tableData.players.S.hand_cards).toEqual([
      '大王', '小王', '♠2', '♥2', '♣2', '♠A', '♦3',
    ]);

    currentStep.value = 1;
    expect(state.value.tableData.current_seat).toBe('E');
  });

  it('keeps a playing thought on its play step and excludes bidding thoughts', () => {
    const tableDetail = ref({
      dealer: 'S',
      idle_seat: 'W',
      landlord: 'S',
      dizhu_cards: [],
      initial_hands: {
        S: ['♠A', '♦3'], E: ['♠4'], N: ['♠5'], W: [],
      },
      players: {
        S: { agent_id: 'south-id', agent_name: '南家', team: 'red' },
        E: { agent_id: 'east-id', agent_name: '东家', team: 'blue' },
        N: { agent_id: 'north-id', agent_name: '北家', team: 'red' },
      },
      bidding: [
        { seat: 'S', bid: 1, timestamp_ms: 1 },
        { seat: 'E', bid: 0, timestamp_ms: 2 },
        { seat: 'N', bid: 0, timestamp_ms: 3 },
      ],
      play_history: [
        { round: 1, sub_round: 0, seat: 'S', timestamp_ms: 20, action: { type: 'play', cards: ['♠A'], pattern: 'single' } },
        { round: 1, sub_round: 0, seat: 'E', timestamp_ms: 30, action: { type: 'pass' } },
      ],
      agent_thoughts: [
        { seat: 'N', phase: 'bidding', timestamp_ms: 3, reasoning: '叫分分析', decision: { bid: 0 } },
        { seat: 'S', phase: 'playing', round: 1, sub_round: 0, timestamp_ms: 20, reasoning: '先出 A 控制牌权', decision: { cards: ['♠A'] } },
      ],
    });
    const currentStep = ref(4);
    const state = useReplayState({ tableDetail, currentStep });

    expect(buildReplayTimeline(tableDetail.value)).toHaveLength(5);
    expect(state.value.phase).toBe('playing');
    expect(state.value.visiblePlayHistory).toHaveLength(1);
    expect(state.value.tableData.current_seat).toBe('E');
    expect(state.value.lastThoughts.S.reasoning).toBe('先出 A 控制牌权');
    expect(state.value.lastThoughts.N).toBeUndefined();
  });
});
