<script setup>
import { ref, watch, computed, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { useReplayStore } from '../stores/replay.js';
import { api } from '../api/index.js';
import GameTable from '../components/GameTable.vue';
import PlayHistory from '../components/PlayHistory.vue';
import ThoughtBubble from '../components/ThoughtBubble.vue';
import PlaybackControls from '../components/PlaybackControls.vue';
import ScoreBoard from '../components/ScoreBoard.vue';

const route = useRoute();
const store = useReplayStore();

const matchId = route.params.matchId;
const handNum = ref(parseInt(route.params.handNum) || 1);
const activeTable = ref('A');
const currentStep = ref(0);
const allSteps = ref([]);
const matchInfo = ref(null);

onMounted(async () => {
  try {
    const data = await store.fetchHands(matchId);
    // Fetch match info for score
    try {
      matchInfo.value = await api.getMatch(matchId);
    } catch (e) { /* fallback */ }
    // Load first hand by default
    if (data?.hands?.length) {
      handNum.value = data.hands[0].hand_num;
      await loadHand();
    }
  } catch (e) {
    console.error(e);
  }
});

async function loadHand() {
  await store.fetchHandDetail(matchId, handNum.value);
  if (store.handDetail) {
    const h = store.handDetail;
    // Build step list from table data
    const tableData = h[`table_${activeTable.value.toLowerCase()}`] || h.table_a;
    const steps = [];
    // Add bidding steps
    const bidding = tableData?.bidding || [];
    bidding.forEach(b => steps.push({ type: 'bid', data: b }));

    // Add play steps
    const plays = tableData?.play_history || [];
    plays.forEach(p => steps.push({ type: 'play', data: p }));

    // Add thoughts
    const thoughts = tableData?.agent_thoughts || [];
    thoughts.forEach(t => steps.push({ type: 'thought', data: t }));

    steps.sort((a, b) => (a.data.timestamp_ms || 0) - (b.data.timestamp_ms || 0));
    allSteps.value = steps;
    currentStep.value = 0;
  }
}

watch(handNum, loadHand);
watch(activeTable, loadHand);

const currentTableData = computed(() => {
  if (!store.handDetail) return null;
  return store.handDetail[`table_${activeTable.value.toLowerCase()}`] || store.handDetail.table_a;
});

const currentAction = computed(() => {
  if (currentStep.value > 0 && currentStep.value <= allSteps.value.length) {
    return allSteps.value[currentStep.value - 1];
  }
  return null;
});

const currentPlay = computed(() => {
  if (currentStep.value > 0) {
    const plays = allSteps.value.filter(s => s.type === 'play');
    let idx = currentStep.value - 1;
    for (const p of plays) {
      if (allSteps.value.indexOf(p) <= idx) continue;
      break;
    }
    return plays[Math.min(plays.length - 1, currentStep.value - 1)];
  }
  return null;
});

const handList = computed(() => {
  return store.hands || [];
});
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-2xl font-bold text-white">Replay</h1>
      <div class="text-sm text-slate-400">
        Match: {{ matchInfo?.name || matchId }}
      </div>
    </div>

    <!-- Score overview -->
    <ScoreBoard
      v-if="matchInfo"
      :score="matchInfo.score"
      :current-hand="handNum"
      :total-hands="matchInfo.config?.total_hands || 20"
    />

    <!-- Hand selector -->
    <div class="flex items-center gap-2 mt-4 mb-4">
      <label class="text-sm text-slate-400">Hand:</label>
      <select
        v-model="handNum"
        class="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm text-white"
      >
        <option v-for="h in handList" :key="h.hand_num" :value="h.hand_num">
          Hand {{ h.hand_num }} ({{ h.dealer }} deals)
          <template v-if="h.diff_result?.red_diff">— Red +{{ h.diff_result.red_diff }}</template>
          <template v-if="h.diff_result?.blue_diff">— Blue +{{ h.diff_result.blue_diff }}</template>
        </option>
      </select>

      <!-- Table toggle -->
      <div class="flex bg-slate-800 rounded-lg border border-slate-700 overflow-hidden ml-4">
        <button
          @click="activeTable = 'A'"
          :class="['px-3 py-1 text-xs font-medium transition-colors', activeTable === 'A' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white']"
        >Table A</button>
        <button
          @click="activeTable = 'B'"
          :class="['px-3 py-1 text-xs font-medium transition-colors', activeTable === 'B' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white']"
        >Table B</button>
      </div>
    </div>

    <!-- Diff score -->
    <div v-if="store.handDetail?.diff_score" class="bg-slate-800 rounded-lg border border-slate-700 p-3 mb-4 text-sm">
      <span class="text-slate-400">Diff:</span>
      <span class="text-red-400 ml-2">Red +{{ store.handDetail.diff_score.red_net }}</span>
      <span class="text-blue-400 ml-2">Blue +{{ store.handDetail.diff_score.blue_net }}</span>
      <span v-if="store.handDetail.diff_score.capped" class="text-amber-400 ml-2">(capped at 12)</span>
    </div>

    <!-- Table view -->
    <GameTable
      v-if="currentTableData"
      :table="activeTable"
      :table-data="{
        landlord: currentTableData.landlord,
        dizhu_cards: currentTableData.dizhu_cards,
        players: {
          S: { role: currentTableData.players?.S?.role, agent_name: currentTableData.players?.S?.agent_id || 'S', hand_size: 17 },
          E: { role: currentTableData.players?.E?.role, agent_name: currentTableData.players?.E?.agent_id || 'E', hand_size: 17 },
          N: { role: currentTableData.players?.N?.role, agent_name: currentTableData.players?.N?.agent_id || 'N', hand_size: 17 },
          W: { role: currentTableData.players?.W?.role, agent_name: currentTableData.players?.W?.agent_id || 'W', hand_size: 17 },
        },
      }"
      :show-cards="true"
    />

    <!-- Current thought -->
    <ThoughtBubble
      v-if="currentAction?.type === 'thought'"
      :thought="{
        seat: currentAction.data.seat,
        phase: currentAction.data.phase,
        round: currentAction.data.round,
        reasoning: currentAction.data.reasoning,
        decision: currentAction.data.decision,
      }"
      class="mt-4"
    />

    <!-- Playback controls -->
    <PlaybackControls
      v-if="allSteps.length > 0"
      v-model="currentStep"
      :max="allSteps.length"
      class="mt-4"
    />

    <!-- Play History -->
    <div class="mt-4 bg-slate-800 rounded-xl border border-slate-700 p-4">
      <h3 class="text-sm font-semibold text-slate-400 mb-2">
        Play History — Table {{ activeTable }}
      </h3>
      <PlayHistory :actions="currentTableData?.play_history || []" show-cards />
    </div>

    <!-- Remaining hands + Result -->
    <div v-if="currentTableData?.result" class="mt-4 bg-slate-800 rounded-xl border border-slate-700 p-4">
      <h3 class="text-sm font-semibold text-slate-400 mb-2">Hand Result</h3>
      <div class="grid grid-cols-2 gap-4 text-sm">
        <div>
          <div class="text-slate-400">Winner</div>
          <div class="text-white">{{ currentTableData.result.winner_team }} / {{ currentTableData.result.winner_role }}</div>
        </div>
        <div>
          <div class="text-slate-400">Score</div>
          <div class="text-white">
            {{ currentTableData.result.base_score }} × {{ currentTableData.result.multiplier }} = {{ currentTableData.result.final_score }}
          </div>
        </div>
        <div>
          <div class="text-slate-400">Bombs</div>
          <div class="text-white">{{ currentTableData.result.bombs_played }}</div>
        </div>
        <div>
          <div class="text-slate-400">Spring</div>
          <div class="text-white">{{ currentTableData.result.spring ? 'Yes' : 'No' }} / {{ currentTableData.result.anti_spring ? 'Anti-Spring' : '—' }}</div>
        </div>
      </div>

      <div class="mt-3">
        <div class="text-sm text-slate-400 mb-1">Remaining Hands</div>
        <div v-for="(cards, seat) in currentTableData.remaining_hands || {}" :key="seat" class="text-xs mb-1">
          <span class="text-slate-500 font-mono w-6 inline-block">{{ seat }}:</span>
          <span class="text-white font-mono">{{ cards?.join(' ') || '(none)' }}</span>
        </div>
      </div>
    </div>

    <!-- Reflections -->
    <div v-if="currentTableData?.reflections?.length" class="mt-4 bg-slate-800 rounded-xl border border-slate-700 p-4">
      <h3 class="text-sm font-semibold text-slate-400 mb-2">Reflections</h3>
      <div v-for="(r, i) in currentTableData.reflections" :key="i" class="mb-3 border-b border-slate-700 pb-2 last:border-0">
        <div class="text-xs text-amber-400 font-semibold">
          {{ r.seat }} ({{ r.actual_role }})
        </div>
        <p class="text-xs text-slate-300 mt-1 whitespace-pre-wrap">{{ r.reflection }}</p>
      </div>
    </div>
  </div>
</template>
