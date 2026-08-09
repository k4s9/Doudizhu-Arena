<script setup>
/**
 * Observer-oriented bidding table. It derives all display state from the
 * existing match snapshot so live play and replay share the same presentation.
 */
import { computed } from 'vue';
import BiddingSeat from './BiddingSeat.vue';
import IdleSeat from './IdleSeat.vue';
import DizhuCards from './DizhuCards.vue';

const props = defineProps({
  tableLabel: { type: String, default: 'A' },
  dealer: { type: String, default: '' },
  biddingOrder: { type: Array, default: () => [] },
  players: { type: Object, default: () => ({}) },
  idleSeat: { type: String, default: '' },
  biddingHistory: { type: Array, default: () => [] },
  currentHighBid: { type: Number, default: 0 },
  currentHighBidder: { type: String, default: '' },
  currentSeat: { type: String, default: '' },
  handCards: { type: Object, default: () => ({}) },
  thoughts: { type: Object, default: () => ({}) },
  dizhuCards: { type: Array, default: () => [] },
});

const SEAT_NAMES = { S: '南', E: '东', N: '北', W: '西' };

const activeBidders = computed(() => props.biddingOrder.filter(seat => seat !== props.idleSeat));

const bidMap = computed(() => Object.fromEntries(
  props.biddingHistory.map(entry => [entry.seat, entry.bid]),
));

const currentPlayer = computed(() => props.players[props.currentSeat] || null);
const lastBidder = computed(() => props.biddingHistory.at(-1)?.seat || '');
const highBidderLabel = computed(() => {
  if (!props.currentHighBidder) return '尚未产生';
  return `${SEAT_NAMES[props.currentHighBidder] || props.currentHighBidder}位`;
});

function hasBid(seat) {
  return Object.hasOwn(bidMap.value, seat);
}
</script>

<template>
  <section class="bid-table">
    <header class="bid-table__header">
      <div class="flex min-w-0 items-center gap-3">
        <span class="bid-table__label">{{ tableLabel }}</span>
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="text-sm font-semibold text-white">叫分阶段</h3>
            <span class="border border-amber-300/35 bg-amber-300/10 px-2 py-0.5 text-[10px] font-semibold text-amber-100">进行中</span>
          </div>
          <p class="mt-0.5 text-[11px] text-slate-400">
            首家 {{ SEAT_NAMES[dealer] || dealer || '—' }}位 · {{ biddingHistory.length }}/{{ activeBidders.length }} 人已完成叫分
          </p>
        </div>
      </div>
      <DizhuCards :cards="dizhuCards" size="sm" />
    </header>

    <div class="bid-table__summary">
      <div>
        <p class="text-[10px] font-semibold text-slate-500">当前最高叫分</p>
        <p class="mt-1 text-2xl font-semibold tabular-nums text-amber-200">
          {{ currentHighBid > 0 ? `${currentHighBid} 分` : '待叫分' }}
        </p>
      </div>
      <div class="border-l border-slate-700/80 pl-4">
        <p class="text-[10px] font-semibold text-slate-500">最高叫分席</p>
        <p class="mt-1 text-sm font-semibold text-slate-200">{{ highBidderLabel }}</p>
      </div>
      <div class="border-l border-slate-700/80 pl-4">
        <p class="text-[10px] font-semibold text-slate-500">当前行动席</p>
        <p class="mt-1 truncate text-sm font-semibold text-slate-200">{{ currentPlayer?.agent_name || `${SEAT_NAMES[currentSeat] || currentSeat || '等待'}位` }}</p>
      </div>
    </div>

    <div class="bid-table__layout">
      <div class="bid-table__seats">
        <BiddingSeat
          v-for="(seat, index) in activeBidders"
          :key="seat"
          :seat="seat"
          :player="players[seat]"
          :bid-position="index"
          :is-current-bidder="currentSeat === seat"
          :has-bid="hasBid(seat)"
          :bid-amount="bidMap[seat]"
          :current-high-bid="currentHighBid"
          :current-high-bidder="currentHighBidder"
          :hand-cards="handCards[seat] || []"
          :thought="thoughts[seat] || null"
          :show-recent-thought="lastBidder === seat"
        />
      </div>

      <aside class="bid-table__idle">
        <p class="text-[10px] font-semibold uppercase text-slate-500">旁观席</p>
        <IdleSeat :seat="idleSeat" :player="players[idleSeat]" />
        <p class="text-[10px] leading-4 text-slate-500">该席不参与本轮叫分，手牌不公开。</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.bid-table {
  border: 1px solid rgb(51 65 85);
  background: #0d1f1d;
  box-shadow: 0 18px 48px rgb(2 6 23 / 0.28);
}

.bid-table__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid rgb(51 65 85 / 0.8);
  background: #111827;
  padding: 0.875rem 1rem;
}

.bid-table__label {
  display: inline-flex;
  height: 2rem;
  width: 2rem;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 1px solid rgb(125 211 252 / 0.55);
  background: rgb(14 116 144 / 0.25);
  color: rgb(224 242 254);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.875rem;
  font-weight: 700;
}

.bid-table__summary {
  display: grid;
  grid-template-columns: minmax(8rem, 1fr) minmax(8rem, 1fr) minmax(10rem, 1.5fr);
  gap: 1rem;
  border-bottom: 1px solid rgb(51 65 85 / 0.75);
  background: rgb(2 6 23 / 0.3);
  padding: 0.875rem 1rem;
}

.bid-table__layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 10.5rem;
  gap: 1rem;
  padding: 1rem;
}

.bid-table__seats {
  display: grid;
  gap: 0.75rem;
}

.bid-table__idle {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.625rem;
  border-left: 1px solid rgb(71 85 105 / 0.7);
  padding-left: 1rem;
}

@media (max-width: 640px) {
  .bid-table__header { align-items: flex-start; }
  .bid-table__header :deep(.flex.items-center.gap-1\.5) { flex-wrap: wrap; justify-content: flex-end; }
  .bid-table__summary { grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  .bid-table__summary > :last-child { grid-column: span 2; border-left: 0; border-top: 1px solid rgb(51 65 85 / 0.8); padding-left: 0; padding-top: 0.75rem; }
  .bid-table__layout { grid-template-columns: 1fr; }
  .bid-table__idle { border-left: 0; border-top: 1px solid rgb(71 85 105 / 0.7); padding-left: 0; padding-top: 0.875rem; }
}
</style>
