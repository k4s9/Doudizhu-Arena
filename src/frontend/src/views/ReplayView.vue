<script setup>
import { ref, watch, computed, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { useReplayStore } from '../stores/replay.js';
import { api } from '../api/index.js';
import { buildReplayTimeline, useReplayState } from '../composables/useReplayState.js';
import GameTable from '../components/GameTable.vue';
import PlayHistory from '../components/PlayHistory.vue';
import PlaybackControls from '../components/PlaybackControls.vue';
import MatchScoreDisplay from '../components/MatchScoreDisplay.vue';

const route = useRoute();
const store = useReplayStore();

const matchId = route.params.matchId;
const handNum = ref(parseInt(route.params.handNum) || 1);
const activeTable = ref('A');
const currentStep = ref(0);
const allSteps = ref([]);
const matchInfo = ref(null);

const SEAT_NAMES = { S: '南', E: '东', N: '北', W: '西' };
const TEAM_NAMES = { red: '红队', blue: '蓝队' };
const ROLE_NAMES = { landlord: '地主', farmer: '农民', idle: '闲家' };
const TABLE_NAMES = { A: 'A', B: 'B' };

onMounted(async () => {
  try {
    const data = await store.fetchHands(matchId);
    try {
      matchInfo.value = await api.getMatch(matchId);
    } catch (e) { /* fallback */ }
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
  currentStep.value = 0;
  allSteps.value = [];
  if (store.handDetail) {
    const h = store.handDetail;
    const tableData = h[`table_${activeTable.value.toLowerCase()}`] || h.table_a;
    allSteps.value = buildReplayTimeline(tableData);
  }
}

watch(handNum, loadHand);
watch(activeTable, loadHand);

const handDetail = computed(() => store.handDetail);

/**
 * Get the raw table detail for the active table, augmented with hand-level
 * fields (dealer, idle_seat) that the composable needs.
 */
const currentTableDetail = computed(() => {
  if (!store.handDetail) return null;
  const td = store.handDetail[`table_${activeTable.value.toLowerCase()}`] || store.handDetail.table_a;
  if (!td) return null;
  // Augment with hand-level fields
  return {
    ...td,
    dealer: store.handDetail.dealer,
    idle_seat: store.handDetail.idle_seat,
    hand_num: store.handDetail.hand_num,
    // Detect void: no landlord + no play_history + has bidding
    is_void: !td.landlord && (!td.play_history || td.play_history.length === 0) && (td.bidding && td.bidding.length > 0),
  };
});

/**
 * Step-aware replay state — reconstructs tableData, thoughts, visible history
 * at the current step. This is the core of the replay system.
 */
const replayState = useReplayState({
  tableDetail: currentTableDetail,
  currentStep: currentStep,
});

const handList = computed(() => {
  return store.hands || [];
});

const replayTableData = computed(() => replayState.value?.tableData || null);

function seatLabel(seat) {
  return SEAT_NAMES[seat] || '未知席位';
}

function teamLabel(team) {
  return TEAM_NAMES[team] || '未知队伍';
}

function roleLabel(role) {
  return ROLE_NAMES[role] || '未知身份';
}

function tableLabel(table) {
  return `${TABLE_NAMES[table] || '未知'}桌`;
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-2xl font-bold text-white">回放</h1>
      <div class="text-sm text-slate-400">
        比赛: {{ matchInfo?.name || matchId }}
      </div>
    </div>

    <!-- Score overview -->
    <MatchScoreDisplay
      v-if="matchInfo"
      :score="matchInfo.score"
      :current-hand="handNum"
      :total-hands="matchInfo.config?.total_hands || 20"
    />

    <!-- Hand selector + Table toggle -->
    <div class="flex items-center gap-2 mt-4 mb-4">
      <label class="text-sm text-slate-400">副数:</label>
      <select
        v-model="handNum"
        class="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm text-white"
      >
        <option v-for="h in handList" :key="h.hand_num" :value="h.hand_num">
          第 {{ h.hand_num }} 副（{{ seatLabel(h.dealer) }}位发牌）
          <template v-if="h.diff_result?.red_diff">— 红队 +{{ h.diff_result.red_diff }}</template>
          <template v-if="h.diff_result?.blue_diff">— 蓝队 +{{ h.diff_result.blue_diff }}</template>
        </option>
      </select>

      <!-- Table toggle -->
      <div class="flex bg-slate-800 rounded-lg border border-slate-700 overflow-hidden ml-4">
        <button
          @click="activeTable = 'A'"
          :class="['px-3 py-1 text-xs font-medium transition-colors', activeTable === 'A' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white']"
        >A桌</button>
        <button
          @click="activeTable = 'B'"
          :class="['px-3 py-1 text-xs font-medium transition-colors', activeTable === 'B' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white']"
        >B桌</button>
      </div>
    </div>

    <!-- Diff score -->
    <div v-if="store.handDetail?.diff_score" class="bg-slate-800 rounded-lg border border-slate-700 p-3 mb-4 text-sm">
      <span class="text-slate-400">差分:</span>
      <span class="text-red-400 ml-2">红队 +{{ store.handDetail.diff_score.red_net }}</span>
      <span class="text-blue-400 ml-2">蓝队 +{{ store.handDetail.diff_score.blue_net }}</span>
      <span v-if="store.handDetail.diff_score.capped" class="text-amber-400 ml-2">(12 分封顶)</span>
    </div>

    <!-- Table view using new GameTable -->
    <GameTable
      v-if="replayTableData"
      :table="activeTable"
      :table-data="replayTableData"
      :seat-thoughts="replayState.seatThoughts"
      :last-thoughts="replayState.lastThoughts"
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
        出牌历史 — {{ tableLabel(activeTable) }}
      </h3>
      <PlayHistory :actions="replayState.visiblePlayHistory" show-cards />
    </div>

    <!-- Remaining hands + Result -->
    <div v-if="currentTableDetail?.result" class="mt-4 bg-slate-800 rounded-xl border border-slate-700 p-4">
      <h3 class="text-sm font-semibold text-slate-400 mb-2">手牌结果</h3>
      <div class="grid grid-cols-2 gap-4 text-sm">
        <div>
          <div class="text-slate-400">获胜方</div>
          <div class="text-white">{{ teamLabel(currentTableDetail.result.winner_team) }}胜（{{ roleLabel(currentTableDetail.result.winner_role) }}）</div>
        </div>
        <div>
          <div class="text-slate-400">得分</div>
          <div class="text-white">
            {{ currentTableDetail.result.base_score }} × {{ currentTableDetail.result.multiplier }} = {{ currentTableDetail.result.final_score }}
          </div>
        </div>
        <div>
          <div class="text-slate-400">炸弹</div>
          <div class="text-white">{{ currentTableDetail.result.bombs_played }}</div>
        </div>
        <div>
          <div class="text-slate-400">春天</div>
          <div class="text-white">{{ currentTableDetail.result.spring ? '是' : '否' }} / {{ currentTableDetail.result.anti_spring ? '反春' : '—' }}</div>
        </div>
      </div>

      <div class="mt-3">
        <div class="text-sm text-slate-400 mb-1">剩余手牌</div>
        <div v-for="(cards, seat) in currentTableDetail.remaining_hands || {}" :key="seat" class="text-xs mb-1">
          <span class="text-slate-500 w-10 inline-block">{{ seatLabel(seat) }}位：</span>
          <span class="text-white font-mono">{{ cards?.join(' ') || '(无)' }}</span>
        </div>
      </div>
    </div>

    <!-- Reflections -->
    <div v-if="currentTableDetail?.reflections?.length" class="mt-4 bg-slate-800 rounded-xl border border-slate-700 p-4">
      <h3 class="text-sm font-semibold text-slate-400 mb-2">复盘反思</h3>
      <div v-for="(r, i) in currentTableDetail.reflections" :key="i" class="mb-3 border-b border-slate-700 pb-2 last:border-0">
        <div class="text-xs text-amber-400 font-semibold">
          {{ seatLabel(r.seat) }}位（{{ roleLabel(r.actual_role) }}）
        </div>
        <p class="text-xs text-slate-300 mt-1 whitespace-pre-wrap">{{ r.reflection }}</p>
      </div>
    </div>
  </div>
</template>
