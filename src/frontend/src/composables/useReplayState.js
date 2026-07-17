/**
 * useReplayState — reconstructs table state at a given step in the replay timeline.
 *
 * Takes the raw API table detail and a current step index, and returns
 * a tableData object compatible with the GameTable component, plus
 * truncated play history, seat thoughts, and phase metadata.
 *
 * Usage:
 *   const state = useReplayState({ tableDetail, currentStep });
 *   // state.tableData -> pass to GameTable :table-data
 *   // state.seatThoughts -> pass to GameTable :seat-thoughts
 *   // state.lastThoughts -> pass to GameTable :last-thoughts
 *   // state.visiblePlayHistory -> pass to PlayHistory :actions
 */

import { computed, toValue } from 'vue';

const TURN_CYCLE = ['S', 'E', 'N', 'W'];

/** Card rank for sorting (higher = bigger card) */
const RANK_ORDER = {
  '大王': 17, '小王': 16,
  '2': 15, 'A': 14, 'K': 13, 'Q': 12, 'J': 11,
  '10': 10, '9': 9, '8': 8, '7': 7, '6': 6, '5': 5, '4': 4, '3': 3,
};
const SUIT_ORDER = { '♠': 4, '♥': 3, '♣': 2, '♦': 1 };

/**
 * Sort hand cards: rank descending (大王 first, 3 last),
 * then suit descending (♠ > ♥ > ♣ > ♦).
 */
function sortHandCards(cards) {
  return [...cards].sort((a, b) => {
    const rankA = RANK_ORDER[a.replace(/^[♠♥♣♦]/, '')] || 0;
    const rankB = RANK_ORDER[b.replace(/^[♠♥♣♦]/, '')] || 0;
    if (rankB !== rankA) return rankB - rankA;
    const suitA = SUIT_ORDER[a[0]] || 0;
    const suitB = SUIT_ORDER[b[0]] || 0;
    return suitB - suitA;
  });
}

/**
 * Build the bidding order: starting from dealer, cycle through TURN_CYCLE,
 * excluding idle_seat.
 */
function buildBiddingOrder(dealer, idleSeat) {
  const startIdx = TURN_CYCLE.indexOf(dealer);
  if (startIdx < 0) return TURN_CYCLE.filter(s => s !== (idleSeat || ''));
  const order = [];
  for (let i = 0; i < 4; i++) {
    const seat = TURN_CYCLE[(startIdx + i) % 4];
    if (seat !== idleSeat) order.push(seat);
  }
  return order;
}

/**
 * Build merged timeline from bidding + play_history + agent_thoughts.
 * Each step: { type, seat, timestamp_ms, ...originalFields }
 */
function buildTimeline(tableDetail) {
  const steps = [];

  const bidding = tableDetail?.bidding || [];
  for (const b of bidding) {
    steps.push({ type: 'bid', seat: b.seat, timestamp_ms: b.timestamp_ms, bid: b.bid });
  }

  const plays = tableDetail?.play_history || [];
  for (const p of plays) {
    steps.push({
      type: 'play',
      seat: p.seat,
      timestamp_ms: p.timestamp_ms,
      round: p.round,
      sub_round: p.sub_round,
      action: p.action,
    });
  }

  const thoughts = tableDetail?.agent_thoughts || [];
  for (const t of thoughts) {
    steps.push({
      type: 'thought',
      seat: t.seat,
      timestamp_ms: t.timestamp_ms,
      phase: t.phase,
      round: t.round,
      sub_round: t.sub_round,
      reasoning: t.reasoning,
      decision: t.decision,
    });
  }

  steps.sort((a, b) => (a.timestamp_ms || 0) - (b.timestamp_ms || 0));
  return steps;
}

/**
 * Compute the current phase and how many bids/plays have been completed.
 */
function computePhase(timeline, currentStep, tableDetail) {
  const biddingCount = (tableDetail?.bidding || []).length;
  const totalPlays = (tableDetail?.play_history || []).length;
  const isVoid = tableDetail?.is_void || false;
  const hasResult = !!tableDetail?.result;

  let completedBids = 0;
  let completedPlays = 0;
  for (let i = 0; i < currentStep; i++) {
    const step = timeline[i];
    if (step.type === 'bid') completedBids++;
    if (step.type === 'play') completedPlays++;
  }

  // Determine phase
  if (isVoid) return { phase: 'void', completedBids, completedPlays };
  if (completedBids < biddingCount) return { phase: 'bidding', completedBids, completedPlays };
  // Bidding done, hand is playing or finished
  if (hasResult && completedPlays >= totalPlays) return { phase: 'finished', completedBids, completedPlays };
  return { phase: 'playing', completedBids, completedPlays };
}

/**
 * Reconstruct hand_cards for each seat at the current step.
 * Starts from initial_hands, adds dizhu_cards to landlord after bidding completes,
 * then removes cards as plays occur.
 */
function buildHandCards(tableDetail, completedBids, completedPlays) {
  const initial = tableDetail?.initial_hands || {};
  const handCards = {};
  for (const seat of TURN_CYCLE) {
    handCards[seat] = [...(initial[seat] || [])];
  }

  // If bidding is complete and we have a landlord, give them the dizhu cards
  const biddingCount = (tableDetail?.bidding || []).length;
  const landlord = tableDetail?.landlord || '';
  if (completedBids >= biddingCount && landlord && tableDetail?.dizhu_cards) {
    const merged = [...handCards[landlord], ...tableDetail.dizhu_cards];
    handCards[landlord] = sortHandCards(merged);
  }

  // Remove cards that have been played up to completedPlays
  const playHistory = tableDetail?.play_history || [];
  for (let i = 0; i < completedPlays && i < playHistory.length; i++) {
    const p = playHistory[i];
    if (p.action?.type === 'play' && p.action?.cards) {
      const seat = p.seat;
      const playedCards = p.action.cards;
      const currentHand = handCards[seat] || [];
      // Remove each played card (first occurrence to handle duplicates)
      const remaining = [...currentHand];
      for (const card of playedCards) {
        const idx = remaining.indexOf(card);
        if (idx >= 0) remaining.splice(idx, 1);
      }
      handCards[seat] = remaining;
    }
  }

  return handCards;
}

/**
 * Determine current_pattern (what the next player must beat).
 * Scans play_history up to completedPlays to find the active trick's
 * lead play that hasn't been beaten yet, or returns null if the round ended.
 */
function computeCurrentPattern(tableDetail, completedPlays) {
  const playHistory = tableDetail?.play_history || [];
  const visible = playHistory.slice(0, completedPlays);
  if (visible.length === 0) return null;

  // Group plays by (round, sub_round)
  // Within a sub_round, if someone played (not passed), that sets the pattern.
  // The pattern is cleared when ALL 3 active players in that sub_round have acted
  // (either play or pass), meaning the round is won by the last player who played.
  //
  // Strategy: find the last sub_round that has a 'play' action.
  // Check if all 3 active (non-idle) players have acted in that sub_round.
  // If not all have acted, the pattern from the first 'play' in that sub_round is active.

  const idleSeat = tableDetail?.idle_seat || '';
  const activeCount = 3; // 4 players minus 1 idle

  // Build map: (round, sub_round) -> { seatsActed: Set, firstPlay: action }
  const subRounds = new Map();
  for (const p of visible) {
    const key = `${p.round}-${p.sub_round}`;
    if (!subRounds.has(key)) {
      subRounds.set(key, { seatsActed: new Set(), firstPlay: null, plays: [] });
    }
    const sr = subRounds.get(key);
    sr.seatsActed.add(p.seat);
    if (p.action?.type === 'play' && !sr.firstPlay) {
      sr.firstPlay = p.action;
    }
    sr.plays.push(p);
  }

  if (subRounds.size === 0) return null;

  // Get the last sub_round
  const lastKey = [...subRounds.keys()].pop();
  const lastSR = subRounds.get(lastKey);

  // If all active players have acted in this sub_round, the round is over
  if (lastSR.seatsActed.size >= activeCount) {
    return null;
  }

  // Otherwise the pattern to beat is the first play in this sub_round
  if (lastSR.firstPlay) {
    return {
      pattern: lastSR.firstPlay.pattern || null,
      cards: lastSR.firstPlay.cards || [],
      display: lastSR.firstPlay.display || (lastSR.firstPlay.cards || []).join(''),
    };
  }

  return null;
}

/**
 * Build players dict in the format PlayingView/PlayingSeat expect.
 */
function buildPlayers(tableDetail, handCards, currentStep, timeline) {
  const apiPlayers = tableDetail?.players || {};
  const result = {};
  for (const seat of TURN_CYCLE) {
    const apiPlayer = apiPlayers[seat] || {};
    const cards = handCards[seat] || [];
    result[seat] = {
      agent_name: apiPlayer.agent_id || seat,
      player_id: apiPlayer.agent_id || '',
      team: apiPlayer.team || '',
      role: apiPlayer.role || '',
      hand_size: cards.length,
      hand_cards: cards,
    };
  }
  return result;
}

/**
 * Compute seat thought maps for current step.
 * - seatThoughts: live thought for the current step (if type === 'thought')
 * - lastThoughts: for each seat, the last thought up to and including current step
 */
function buildThoughts(timeline, currentStep) {
  const seatThoughts = {};
  const lastThoughts = {};

  if (currentStep > 0 && currentStep <= timeline.length) {
    const currentItem = timeline[currentStep - 1];
    if (currentItem.type === 'thought') {
      seatThoughts[currentItem.seat] = currentItem;
    }
  }

  // For each seat, find the last thought up to currentStep
  const seatLastThought = {};
  for (let i = 0; i < currentStep; i++) {
    const item = timeline[i];
    if (item.type === 'thought') {
      seatLastThought[item.seat] = item;
    }
  }
  // Only include thoughts for seats that have a last action (play or pass) before the thought
  for (const seat of TURN_CYCLE) {
    const thought = seatLastThought[seat];
    if (thought) {
      // Check if this seat had an action (play/pass) whose timestamp is before this thought
      // In practice, thoughts come right before or during the action, so we include them
      lastThoughts[seat] = thought;
    }
  }

  return { seatThoughts, lastThoughts };
}

/**
 * Compute visible play history (truncated to completedPlays).
 */
function buildVisiblePlayHistory(tableDetail, completedPlays) {
  const playHistory = tableDetail?.play_history || [];
  return playHistory.slice(0, completedPlays);
}

/**
 * Main composable entry point.
 *
 * @param {Object} params
 * @param {Object|null} params.tableDetail - raw API table detail (_build_table_detail return)
 * @param {number} params.currentStep - current step index (0 = initial, 1 = first step)
 * @returns {Object} reconstructed state
 */
export function useReplayState({ tableDetail, currentStep }) {
  return computed(() => {
    const td = toValue(tableDetail);
    const step = toValue(currentStep);

    if (!td) {
      return {
        tableData: null,
        seatThoughts: {},
        lastThoughts: {},
        visiblePlayHistory: [],
        phase: '',
        isFinished: false,
      };
    }

    const timeline = buildTimeline(td);
    const { phase, completedBids, completedPlays } = computePhase(timeline, step, td);
    const handCards = buildHandCards(td, completedBids, completedPlays);
    const currentPattern = computeCurrentPattern(td, completedPlays);
    const players = buildPlayers(td, handCards, step, timeline);
    const { seatThoughts, lastThoughts } = buildThoughts(timeline, step);
    const visiblePlayHistory = buildVisiblePlayHistory(td, completedPlays);

    // Current seat: the seat of the event at this step
    let currentSeat = '';
    if (step > 0 && step <= timeline.length) {
      currentSeat = timeline[step - 1].seat || '';
    }

    // For finished state with no more steps, clear current_seat
    if (phase === 'finished') {
      currentSeat = '';
    }

    // Bidding data
    const bidding = td?.bidding || [];
    let currentHighBid = 0;
    let currentHighBidder = '';
    for (let i = 0; i < completedBids && i < bidding.length; i++) {
      if (bidding[i].bid > currentHighBid) {
        currentHighBid = bidding[i].bid;
        currentHighBidder = bidding[i].seat;
      }
    }

    const dealer = td?.dealer || '';
    const idleSeat = td?.idle_seat || '';
    const biddingOrder = buildBiddingOrder(dealer, idleSeat);

    // Check void
    const isVoid = td?.is_void || false;

    // Build tableData compatible with GameTable
    const tableData = {
      phase,
      hand_num: td?.hand_num || 0,
      dealer,
      landlord: td?.landlord || '',
      final_bid: td?.final_bid || 0,
      idle_seat: idleSeat,
      effective_idle: idleSeat, // replay doesn't have idle participation swap (covered in data)
      dizhu_cards: td?.dizhu_cards || [],
      current_seat: currentSeat,
      current_pattern: currentPattern,
      players,
      play_history: visiblePlayHistory,
      bidding_order: biddingOrder,
      bidding_history: bidding.slice(0, completedBids),
      current_high_bid: currentHighBid,
      current_high_bidder: currentHighBidder,
    };

    return {
      tableData,
      seatThoughts,
      lastThoughts,
      visiblePlayHistory,
      phase,
      isFinished: phase === 'finished',
      isVoid,
    };
  });
}
