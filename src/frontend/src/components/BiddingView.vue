<script setup>
/**
 * BiddingView — the bidding phase layout for a single table.
 * Shows 3 active players in vertical bidding order + idle seat on the right.
 * All active players show full hand cards (god mode).
 */
import { computed } from 'vue';
import BiddingSeat from './BiddingSeat.vue';
import IdleSeat from './IdleSeat.vue';
import DizhuCards from './DizhuCards.vue';

const props = defineProps({
  tableLabel: { type: String, default: 'A' },
  /** The dealing seat (首家) */
  dealer: { type: String, default: '' },
  /** Ordered list of 4 seats in bidding order */
  biddingOrder: { type: Array, default: () => [] },
  /** Map of seat -> player info */
  players: { type: Object, default: () => ({}) },
  /** The idle seat */
  idleSeat: { type: String, default: '' },
  /** Bidding history: [{seat, bid}] */
  biddingHistory: { type: Array, default: () => [] },
  /** Current high bid */
  currentHighBid: { type: Number, default: 0 },
  /** Seat of current high bidder */
  currentHighBidder: { type: String, default: '' },
  /** Current bidding seat */
  currentSeat: { type: String, default: '' },
  /** Map of seat -> hand cards */
  handCards: { type: Object, default: () => ({}) },
  /** Map of seat -> thought */
  thoughts: { type: Object, default: () => ({}) },
  /** Dizhu cards (shown in header) */
  dizhuCards: { type: Array, default: () => [] },
});

const BID_POSITIONS = { 0: '首家', 1: '二家', 2: '尾家' };

/** Active bidders = bidding_order minus idle_seat */
const activeBidders = computed(() => {
  return props.biddingOrder.filter(s => s !== props.idleSeat);
});

/** Build bid map from bidding history */
const bidMap = computed(() => {
  const m = {};
  for (const h of props.biddingHistory) {
    m[h.seat] = h.bid;
  }
  return m;
});

function isCurrentBidder(seat) {
  return props.currentSeat === seat;
}

function hasBid(seat) {
  return seat in bidMap.value;
}
</script>

<template>
  <div class="relative">
    <!-- Header -->
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-3">
        <h3 class="text-lg font-semibold text-slate-200">{{ tableLabel }} 桌</h3>
        <span class="text-xs px-2 py-1 rounded-full bg-amber-500/20 text-amber-400 font-semibold">叫分中</span>
        <span v-if="dealer" class="text-xs text-slate-400">首家: {{ {S:'南',E:'东',N:'北',W:'西'}[dealer] || dealer }}</span>
        <span v-if="idleSeat" class="text-xs text-slate-500">闲家: {{ {S:'南',E:'东',N:'北',W:'西'}[idleSeat] || idleSeat }}</span>
      </div>
      <DizhuCards :cards="dizhuCards" size="sm" />
    </div>

    <!-- Bidding layout: active bidders left in vertical column, idle seat right -->
    <div class="flex gap-4">
      <!-- Active bidders (vertical, in bidding order) -->
      <div class="flex-1 flex flex-col gap-3">
        <BiddingSeat
          v-for="(seat, idx) in activeBidders"
          :key="seat"
          :seat="seat"
          :player="players[seat]"
          :bid-position="idx"
          :is-current-bidder="isCurrentBidder(seat)"
          :has-bid="hasBid(seat)"
          :bid-amount="bidMap[seat] ?? null"
          :current-high-bid="currentHighBid"
          :current-high-bidder="currentHighBidder"
          :hand-cards="handCards[seat] || []"
          :thought="thoughts[seat] || null"
        />
      </div>

      <!-- Idle seat (right side) -->
      <div class="flex items-start pt-2">
        <IdleSeat
          :seat="idleSeat"
          :player="players[idleSeat]"
        />
      </div>
    </div>
  </div>
</template>
