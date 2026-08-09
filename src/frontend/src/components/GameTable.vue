<script setup>
/**
 * GameTable — single table container that switches between BiddingView and PlayingView
 * based on the current phase. Also handles "void" (流局) display.
 */
import { computed } from 'vue';
import BiddingView from './BiddingView.vue';
import PlayingView from './PlayingView.vue';

const props = defineProps({
  table: { type: String, required: true }, // 'A' | 'B'
  tableData: { type: Object, default: null },
  /** Map of seat -> current thought for this table */
  seatThoughts: { type: Object, default: () => ({}) },
  /** Map of seat -> last persisted thought (shown alongside last play for non-active players) */
  lastThoughts: { type: Object, default: () => ({}) },
});

const phase = computed(() => props.tableData?.phase || '');

const PHASE_NAMES = {
  bidding: '叫分',
  playing: '出牌',
  finished: '已结束',
  void: '流局',
  dealing: '发牌',
};
const tableLabel = computed(() => props.table === 'A' ? 'A' : 'B');

/** Extract hand_cards per seat from table data */
const handCards = computed(() => {
  const cards = {};
  const players = props.tableData?.players || {};
  for (const seat of ['S', 'E', 'N', 'W']) {
    cards[seat] = players[seat]?.hand_cards || [];
  }
  return cards;
});
</script>

<template>
  <div :class="phase === 'bidding' ? '' : 'bg-slate-800 rounded-xl border border-slate-700 p-4'">
    <!-- No data state -->
    <div v-if="!tableData" class="text-center text-slate-500 py-8">
      <p>等待数据...</p>
    </div>

    <!-- Void (流局) state -->
    <div v-else-if="phase === 'void'" class="text-center py-8">
      <div class="text-4xl mb-2">🚫</div>
      <h3 class="text-lg font-semibold text-slate-300">{{ tableLabel }} 桌</h3>
      <p class="text-sm text-slate-500 mt-1">流局 — 三家均不叫</p>
    </div>

    <!-- Bidding phase -->
    <BiddingView
      v-else-if="phase === 'bidding'"
      :table-label="tableLabel"
      :dealer="tableData.dealer"
      :bidding-order="tableData.bidding_order || []"
      :players="tableData.players || {}"
      :idle-seat="tableData.effective_idle || tableData.idle_seat || ''"
      :bidding-history="tableData.bidding_history || []"
      :current-high-bid="tableData.current_high_bid || 0"
      :current-high-bidder="tableData.current_high_bidder || ''"
      :current-seat="tableData.current_seat || ''"
      :hand-cards="handCards"
      :thoughts="seatThoughts"
      :dizhu-cards="tableData.dizhu_cards || []"
    />

    <!-- Playing phase -->
    <PlayingView
      v-else-if="phase === 'playing'"
      :table-label="tableLabel"
      :dealer="tableData.dealer"
      :landlord="tableData.landlord"
      :final-bid="tableData.final_bid || 0"
      :idle-seat="tableData.effective_idle || tableData.idle_seat || ''"
      :players="tableData.players || {}"
      :current-seat="tableData.current_seat || ''"
      :current-pattern="tableData.current_pattern || null"
      :play-history="tableData.play_history || []"
      :hand-cards="handCards"
      :thoughts="seatThoughts"
      :last-thoughts="lastThoughts"
      :dizhu-cards="tableData.dizhu_cards || []"
      :turn-timer="tableData.turn_timer || null"
    />

    <!-- Finished / other state -->
    <div v-else class="text-center text-slate-400 py-6">
      <p>{{ tableLabel }} 桌 — {{ PHASE_NAMES[phase] || phase }}</p>
    </div>
  </div>
</template>
