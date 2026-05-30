<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { useRoute } from 'vue-router';
import { createMatchSocket } from '../api/index.js';
import { useMatchStore } from '../stores/match.js';
import GameTable from '../components/GameTable.vue';
import PlayHistory from '../components/PlayHistory.vue';
import ThoughtBubble from '../components/ThoughtBubble.vue';
import ScoreBoard from '../components/ScoreBoard.vue';

const route = useRoute();
const store = useMatchStore();
const matchId = route.params.id;

const tableA = ref(null);
const tableB = ref(null);
const score = ref({ red: 0, blue: 0 });
const currentHand = ref(0);
const status = ref('loading');
const latestThought = ref(null);
const thoughtHistory = ref([]);
const errors = ref([]);
let socket = null;

onMounted(async () => {
  try {
    await store.fetchMatch(matchId);
    const m = store.currentMatch;
    status.value = m.status;
    score.value = m.score;
    currentHand.value = m.current_hand;

    // Connect WebSocket for live updates if match is running
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

function handleEvent(event) {
  const { type, payload } = event;

  switch (type) {
    case 'match_state':
      if (payload.tables?.A) tableA.value = payload.tables.A;
      if (payload.tables?.B) tableB.value = payload.tables.B;
      score.value = payload.score;
      currentHand.value = payload.current_hand;
      status.value = payload.status;
      break;

    case 'bidding_update':
      // Could show bidding panel — currently just update tables
      break;

    case 'bidding_complete':
      break;

    case 'card_played':
      if (payload.table === 'A' && tableA.value) {
        tableA.value = {
          ...tableA.value,
          current_seat: payload.next_seat,
          current_pattern: payload.current_pattern,
        };
      }
      if (payload.table === 'B' && tableB.value) {
        tableB.value = {
          ...tableB.value,
          current_seat: payload.next_seat,
          current_pattern: payload.current_pattern,
        };
      }
      break;

    case 'pass':
      if (payload.table === 'A' && tableA.value) {
        tableA.value = { ...tableA.value, current_seat: payload.next_seat };
      }
      if (payload.table === 'B' && tableB.value) {
        tableB.value = { ...tableB.value, current_seat: payload.next_seat };
      }
      break;

    case 'trick_won':
      break;

    case 'thought_update':
      latestThought.value = payload;
      thoughtHistory.value.push(payload);
      break;

    case 'score_update':
      score.value = payload.running_total;
      currentHand.value = payload.hand_num;
      break;

    case 'match_paused':
      status.value = 'paused';
      break;

    case 'match_resumed':
      status.value = 'running';
      break;

    case 'match_ended':
      status.value = 'finished';
      score.value = payload.final_score;
      // Redirect to replay after a delay
      setTimeout(() => {
        window.location.href = `/replay/${matchId}`;
      }, 5000);
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
        <h1 class="text-2xl font-bold text-white">{{ store.currentMatch?.name || 'Match' }}</h1>
        <div class="text-sm text-slate-400">
          Status:
          <span :class="{
            'text-green-400': status === 'running',
            'text-amber-400': status === 'paused',
            'text-blue-400': status === 'created',
            'text-slate-400': status === 'finished',
          }" class="font-semibold">
            {{ status }}
          </span>
        </div>
      </div>

      <div class="flex gap-2">
        <button
          v-if="status === 'created'"
          @click="handleStart"
          class="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Start Match
        </button>
        <button
          v-if="status === 'running'"
          @click="handlePause"
          class="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Pause
        </button>
        <button
          v-if="status === 'paused'"
          @click="handleResume"
          class="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Resume
        </button>
      </div>
    </div>

    <!-- Score -->
    <ScoreBoard :score="score" :current-hand="currentHand" :total-hands="store.currentMatch?.config?.total_hands || 20" />

    <!-- Tables -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
      <GameTable table="A" :table-data="tableA" />
      <GameTable table="B" :table-data="tableB" />
    </div>

    <!-- Live thought bubble -->
    <div v-if="latestThought" class="mt-4">
      <ThoughtBubble :thought="latestThought" />
    </div>

    <!-- Thought history -->
    <div v-if="thoughtHistory.length" class="mt-4 bg-slate-800 rounded-xl border border-slate-700 p-4">
      <h3 class="text-sm font-semibold text-slate-400 mb-2">Agent Thoughts ({{ thoughtHistory.length }})</h3>
      <div class="space-y-2 max-h-64 overflow-y-auto">
        <div v-for="(t, i) in [...thoughtHistory].reverse().slice(0, 20)" :key="i"
          class="text-xs border-b border-slate-700 pb-2 last:border-0">
          <span class="text-amber-400 font-semibold">{{ t.seat }}</span>
          <span class="text-slate-500 ml-2">{{ t.phase }}</span>
          <span v-if="t.round" class="text-slate-500 ml-1">R{{ t.round }}</span>
          <p class="text-slate-300 mt-0.5 line-clamp-2">{{ t.reasoning }}</p>
        </div>
      </div>
    </div>

    <!-- Error log -->
    <div v-if="errors.length" class="mt-4 bg-red-900/20 border border-red-800 rounded-lg p-3">
      <h3 class="text-xs font-semibold text-red-400 mb-1">Errors</h3>
      <div v-for="(e, i) in errors.slice(-5)" :key="i" class="text-xs text-red-300 font-mono">
        [{{ e.code }}] {{ e.message }}
      </div>
    </div>

    <!-- Match ended notice -->
    <div v-if="status === 'finished'" class="mt-6 text-center">
      <div class="text-xl font-bold text-amber-400 mb-2">Match Complete!</div>
      <router-link :to="`/replay/${matchId}`" class="text-amber-300 hover:text-amber-200 underline text-sm">
        View Replay &rarr;
      </router-link>
    </div>
  </div>
</template>
