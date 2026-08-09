<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { useRoute } from 'vue-router';
import { createMatchSocket } from '../api/index.js';
import { useMatchStore } from '../stores/match.js';
import GameTable from '../components/GameTable.vue';
import MatchScoreDisplay from '../components/MatchScoreDisplay.vue';
import HandScoreBar from '../components/HandScoreBar.vue';

const route = useRoute();
const store = useMatchStore();
const matchId = route.params.id;

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
    // Jokers always sort first
    const rankA = RANK_ORDER[a.replace(/^[♠♥♣♦]/, '')] || 0;
    const rankB = RANK_ORDER[b.replace(/^[♠♥♣♦]/, '')] || 0;
    if (rankB !== rankA) return rankB - rankA;
    // Same rank: sort by suit
    const suitA = SUIT_ORDER[a[0]] || 0;
    const suitB = SUIT_ORDER[b[0]] || 0;
    return suitB - suitA;
  });
}

const tableA = ref(null);
const tableB = ref(null);
const score = ref({ red: 0, blue: 0 });
const currentHand = ref(0);
const totalHands = ref(20);
const status = ref('loading');
const koStatus = ref(null);

// Per-seat thought tracking (live, for current player)
const thoughtsA = ref({});
const thoughtsB = ref({});

// Per-seat LAST thought tracking (persisted, shown alongside last play for non-active players)
const lastThoughtsA = ref({});
const lastThoughtsB = ref({});

// Per-hand score records
const handScores = ref([]);

const errors = ref([]);
let socket = null;

onMounted(async () => {
  try {
    await store.fetchMatch(matchId);
    const m = store.currentMatch;
    status.value = m.status;
    score.value = m.score || { red: 0, blue: 0 };
    currentHand.value = m.current_hand || 0;
    totalHands.value = m.config?.total_hands || 20;

    if (m.status === 'running' || m.status === 'paused') {
      connectWS();
    }
  } catch (e) {
    status.value = 'error';
  }
});

function connectWS() {
  socket = createMatchSocket(matchId, {
    onOpen() {
      console.log('WS connected');
    },
    onEvent(event) {
      handleEvent(event);
    },
  });
}

function nextBiddingSeat(tableData, biddingHistory) {
  const completedSeats = new Set((biddingHistory || []).map(entry => entry.seat));
  return (tableData?.bidding_order || []).find(seat => !completedSeats.has(seat)) || '';
}

function clearTurnTimer(table) {
  const tableRef = table === 'A' ? tableA : tableB;
  if (tableRef.value?.turn_timer) {
    tableRef.value = { ...tableRef.value, turn_timer: null };
  }
}

function clearAllTurnTimers() {
  clearTurnTimer('A');
  clearTurnTimer('B');
}

function persistActionThought(table, seat, thought) {
  const lastThoughts = table === 'A' ? lastThoughtsA : lastThoughtsB;
  const liveThoughts = table === 'A' ? thoughtsA : thoughtsB;
  const attachedThought = thought || liveThoughts.value[seat];
  if (attachedThought) {
    lastThoughts.value = { ...lastThoughts.value, [seat]: attachedThought };
  }
  const updated = { ...liveThoughts.value };
  delete updated[seat];
  liveThoughts.value = updated;
}

function handleEvent(event) {
  const { type, payload } = event;

  switch (type) {
    case 'match_state':
      if (payload.tables?.A) tableA.value = payload.tables.A;
      if (payload.tables?.B) tableB.value = payload.tables.B;
      score.value = payload.score || { red: 0, blue: 0 };
      currentHand.value = payload.current_hand || 0;
      status.value = payload.status;
      break;

    case 'hand_started':
      // Reset per-seat thoughts for new hand
      thoughtsA.value = {};
      thoughtsB.value = {};
      lastThoughtsA.value = {};
      lastThoughtsB.value = {};
      // Initialize table data (now includes bidding_order, idle_seat, hand_cards)
      if (payload.tables?.A) tableA.value = payload.tables.A;
      if (payload.tables?.B) tableB.value = payload.tables.B;
      break;

    case 'bidding_update':
      // Update table with bidding progress
      if (payload.table === 'A' && tableA.value) {
        tableA.value = {
          ...tableA.value,
          // The event seat has just bid. Highlight the next unfinished bidder.
          current_seat: nextBiddingSeat(tableA.value, payload.bidding_history),
          phase: 'bidding',
          current_high_bid: payload.current_high_bid,
          current_high_bidder: payload.current_high_seat,
          bidding_history: payload.bidding_history || [],
        };
      }
      if (payload.table === 'B' && tableB.value) {
        tableB.value = {
          ...tableB.value,
          current_seat: nextBiddingSeat(tableB.value, payload.bidding_history),
          phase: 'bidding',
          current_high_bid: payload.current_high_bid,
          current_high_bidder: payload.current_high_seat,
          bidding_history: payload.bidding_history || [],
        };
      }
      break;

    case 'bidding_complete':
      if (payload.table === 'A' && tableA.value) {
        const newPhase = payload.void ? 'void' : 'playing';
        const players = { ...tableA.value.players };
        // Handle idle participation swap: if idle was triggered, swap hand_cards
        const idleInfo = payload.idle_participation || {};
        if (idleInfo.triggered && idleInfo.idle_seat && idleInfo.replaced_farmer) {
          // original_idle takes replaced_farmer's hand_cards
          const idleSeat = idleInfo.idle_seat;
          const replacedFarmer = idleInfo.replaced_farmer;
          if (players[idleSeat] && players[replacedFarmer]) {
            const farmerCards = players[replacedFarmer].hand_cards || [];
            players[idleSeat] = {
              ...players[idleSeat],
              hand_size: farmerCards.length,
              hand_cards: [...farmerCards],
              role: 'farmer',
            };
            players[replacedFarmer] = {
              ...players[replacedFarmer],
              hand_size: 0,
              hand_cards: [],
              role: 'idle',
            };
          }
        }
        // Landlord gets +3 hand_size and +3 hand_cards from dizhu cards
        if (!payload.void && payload.landlord_seat && players[payload.landlord_seat]) {
          const existingCards = players[payload.landlord_seat].hand_cards || [];
          const newCards = payload.dizhu_cards || [];
          const mergedCards = [...existingCards, ...newCards];
          // Sort merged hand: rank desc (大王→3), suit ♠→♥→♣→♦
          const sortedMerged = sortHandCards(mergedCards);
          players[payload.landlord_seat] = {
            ...players[payload.landlord_seat],
            hand_size: sortedMerged.length,
            hand_cards: sortedMerged,
            role: 'landlord',
          };
        }
        tableA.value = {
          ...tableA.value,
          phase: newPhase,
          landlord: payload.landlord_seat,
          dizhu_cards: payload.dizhu_cards,
          final_bid: payload.final_bid || 0,
          players,
          // Use effective_idle from idle_participation (correct even after idle swap)
          effective_idle: idleInfo.effective_idle || idleInfo.idle_seat || tableA.value.effective_idle || tableA.value.idle_seat,
        };
      }
      if (payload.table === 'B' && tableB.value) {
        const newPhase = payload.void ? 'void' : 'playing';
        const players = { ...tableB.value.players };
        // Handle idle participation swap
        const idleInfo = payload.idle_participation || {};
        if (idleInfo.triggered && idleInfo.idle_seat && idleInfo.replaced_farmer) {
          const idleSeat = idleInfo.idle_seat;
          const replacedFarmer = idleInfo.replaced_farmer;
          if (players[idleSeat] && players[replacedFarmer]) {
            const farmerCards = players[replacedFarmer].hand_cards || [];
            players[idleSeat] = {
              ...players[idleSeat],
              hand_size: farmerCards.length,
              hand_cards: [...farmerCards],
              role: 'farmer',
            };
            players[replacedFarmer] = {
              ...players[replacedFarmer],
              hand_size: 0,
              hand_cards: [],
              role: 'idle',
            };
          }
        }
        if (!payload.void && payload.landlord_seat && players[payload.landlord_seat]) {
          const existingCards = players[payload.landlord_seat].hand_cards || [];
          const newCards = payload.dizhu_cards || [];
          const mergedCards = [...existingCards, ...newCards];
          const sortedMerged = sortHandCards(mergedCards);
          players[payload.landlord_seat] = {
            ...players[payload.landlord_seat],
            hand_size: sortedMerged.length,
            hand_cards: sortedMerged,
            role: 'landlord',
          };
        }
        tableB.value = {
          ...tableB.value,
          phase: newPhase,
          landlord: payload.landlord_seat,
          dizhu_cards: payload.dizhu_cards,
          final_bid: payload.final_bid || 0,
          players,
          effective_idle: idleInfo.effective_idle || idleInfo.idle_seat || tableB.value.effective_idle || tableB.value.idle_seat,
        };
      }
      break;

    case 'play_turn_started': {
      const tableRef = payload.table === 'A' ? tableA : tableB;
      if (tableRef.value) {
        tableRef.value = {
          ...tableRef.value,
          current_seat: payload.seat,
          turn_timer: {
            seat: payload.seat,
            timeout_ms: payload.timeout_ms,
            deadline_ms: payload.deadline_ms,
          },
        };
      }
      break;
    }

    case 'card_played': {
      const tableRef = payload.table === 'A' ? tableA : tableB;
      if (tableRef.value) {
        const history = [...(tableRef.value.play_history || [])];
        history.push({
          round: payload.round,
          sub_round: payload.sub_round,
          seat: payload.seat,
          action: payload.action,
          timestamp_ms: payload.timestamp_ms,
        });
        // Decrement hand_size and remove played cards from hand_cards
        const players = { ...tableRef.value.players };
        const playedCards = payload.action?.cards || [];
        const playCount = playedCards.length;
        if (players[payload.seat] && playCount > 0) {
          const currentHandCards = players[payload.seat].hand_cards || [];
          const playedSet = new Set(playedCards);
          const remainingCards = currentHandCards.filter(c => !playedSet.has(c));
          players[payload.seat] = {
            ...players[payload.seat],
            hand_size: Math.max(0, (players[payload.seat].hand_size || 0) - playCount),
            hand_cards: remainingCards,
          };
        }
        tableRef.value = {
          ...tableRef.value,
          current_seat: payload.next_seat,
          current_pattern: payload.current_pattern,
          play_history: history,
          players,
          turn_timer: null,
        };
      }
      persistActionThought(payload.table, payload.seat, payload.thought);
      break;
    }

    case 'pass': {
      const tableRef = payload.table === 'A' ? tableA : tableB;
      if (tableRef.value) {
        const history = [...(tableRef.value.play_history || [])];
        history.push({
          round: payload.round,
          sub_round: payload.sub_round,
          seat: payload.seat,
          action: { type: 'pass' },
          timestamp_ms: payload.timestamp_ms,
        });
        tableRef.value = {
          ...tableRef.value,
          current_seat: payload.next_seat,
          play_history: history,
          turn_timer: null,
        };
      }
      persistActionThought(payload.table, payload.seat, payload.thought);
      break;
    }

    case 'trick_won':
      // Clear current_pattern when a trick is won (new round starts)
      if (payload.table === 'A' && tableA.value) {
        tableA.value = {
          ...tableA.value,
          current_pattern: null,
          current_seat: payload.new_leader,
        };
      }
      if (payload.table === 'B' && tableB.value) {
        tableB.value = {
          ...tableB.value,
          current_pattern: null,
          current_seat: payload.new_leader,
        };
      }
      break;

    case 'thought_update':
      // Playing thoughts arrive with card_played/pass so they render with the
      // action. This branch remains for live bidding analysis.
      if (payload.phase === 'playing') break;
      if (payload.table === 'A') {
        thoughtsA.value = {
          ...thoughtsA.value,
          [payload.seat]: payload,
        };
      } else {
        thoughtsB.value = {
          ...thoughtsB.value,
          [payload.seat]: payload,
        };
      }
      break;

    case 'hand_ended':
      // Per-table hand end — no-op for now, score_update has the diff
      break;

    case 'score_update':
      score.value = payload.running_total || { red: 0, blue: 0 };
      currentHand.value = payload.hand_num || 0;
      koStatus.value = payload.ko_status || null;
      // Add to hand score history
      if (payload.hand_diff?.details) {
        const existing = handScores.value.find(h => h.hand_num === payload.hand_num);
        if (!existing) {
          handScores.value.push({
            hand_num: payload.hand_num,
            red_diff: payload.hand_diff.red_diff,
            details: payload.hand_diff.details,
          });
        }
      }
      break;

    case 'match_paused':
      status.value = 'paused';
      clearAllTurnTimers();
      break;

    case 'match_resumed':
      status.value = 'running';
      break;

    case 'match_ended':
      status.value = 'finished';
      clearAllTurnTimers();
      score.value = payload.final_score || { red: 0, blue: 0 };
      // Transition both tables to finished so they don't show "playing" state
      if (tableA.value) {
        tableA.value = { ...tableA.value, phase: 'finished' };
      }
      if (tableB.value) {
        tableB.value = { ...tableB.value, phase: 'finished' };
      }
      break;

    case 'error':
      errors.value.push(payload);
      if (errors.value.length > 50) errors.value.shift();
      break;
  }
}

async function handleStart() {
  try {
    await store.startMatch(matchId);
    status.value = 'running';
    connectWS();
  } catch (e) {
    console.error(e);
  }
}

async function handlePause() {
  if (socket) socket.send({ type: 'pause_request' });
}

async function handleResume() {
  if (socket) socket.send({ type: 'resume_request' });
}

onUnmounted(() => {
  if (socket) socket.close();
});
</script>

<template>
  <div>
    <!-- Header -->
    <div class="flex items-center justify-between mb-4">
      <div>
        <h1 class="text-2xl font-bold text-white">{{ store.currentMatch?.name || '比赛' }}</h1>
        <div class="text-sm text-slate-400">
          状态:
          <span :class="{
            'text-green-400': status === 'running',
            'text-amber-400': status === 'paused',
            'text-blue-400': status === 'created',
            'text-slate-400': status === 'finished',
            'text-red-400': status === 'error',
          }" class="font-semibold">
            {{ { running: '进行中', paused: '已暂停', created: '已创建', finished: '已结束', error: '错误', loading: '加载中...' }[status] || status }}
          </span>
        </div>
      </div>

      <div class="flex gap-2">
        <button
          v-if="status === 'created'"
          @click="handleStart"
          class="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          开始比赛
        </button>
        <button
          v-if="status === 'running'"
          @click="handlePause"
          class="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          暂停
        </button>
        <button
          v-if="status === 'paused'"
          @click="handleResume"
          class="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          恢复
        </button>
      </div>
    </div>

    <!-- Top score bar -->
    <MatchScoreDisplay
      :score="score"
      :current-hand="currentHand"
      :total-hands="totalHands"
      :ko-status="koStatus"
    />

    <!-- Tables: vertical layout (better spacing for AB tables) -->
    <div class="flex flex-col gap-4 mt-4">
      <GameTable table="A" :table-data="tableA" :seat-thoughts="thoughtsA" :last-thoughts="lastThoughtsA" />
      <GameTable table="B" :table-data="tableB" :seat-thoughts="thoughtsB" :last-thoughts="lastThoughtsB" />
    </div>

    <!-- Per-hand score history -->
    <div class="mt-4">
      <HandScoreBar :hand-scores="handScores" />
    </div>

    <!-- Error log -->
    <div v-if="errors.length" class="mt-4 bg-red-900/20 border border-red-800 rounded-lg p-3">
      <h3 class="text-xs font-semibold text-red-400 mb-1">错误日志</h3>
      <div v-for="(e, i) in errors.slice(-5)" :key="i" class="text-xs text-red-300 font-mono">
        [{{ e.code }}] {{ e.message }}
      </div>
    </div>

    <!-- Match ended notice -->
    <div v-if="status === 'finished'" class="mt-6 text-center">
      <div class="text-xl font-bold mb-2">
        <span v-if="(score?.red || 0) > (score?.blue || 0)" class="text-red-400">红队获胜! 🏆</span>
        <span v-else-if="(score?.blue || 0) > (score?.red || 0)" class="text-blue-400">蓝队获胜! 🏆</span>
        <span v-else class="text-slate-400">平局! 🤝</span>
      </div>
      <router-link :to="`/replay/${matchId}`" class="text-amber-300 hover:text-amber-200 underline text-sm">
        查看回放 &rarr;
      </router-link>
    </div>
  </div>
</template>
