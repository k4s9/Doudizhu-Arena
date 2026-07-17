<script setup>
/**
 * BiddingSeat — a single player during the bidding phase.
 * Shows full hand cards (god mode), bidding status, and thought bubble.
 */
import { computed } from 'vue';
import CardHand from './CardHand.vue';
import PlayerSeat from './PlayerSeat.vue';

const props = defineProps({
  seat: { type: String, required: true },
  player: { type: Object, default: null },
  /** Position in bidding order: 0=首家, 1=二家, 2=尾家 */
  bidPosition: { type: Number, default: 0 },
  /** Whether this seat is currently bidding */
  isCurrentBidder: { type: Boolean, default: false },
  /** Whether this seat has finished bidding */
  hasBid: { type: Boolean, default: false },
  /** The bid this seat made (0 = pass) */
  bidAmount: { type: Number, default: null },
  /** Current highest bid across all bidders */
  currentHighBid: { type: Number, default: 0 },
  /** Seat of current highest bidder */
  currentHighBidder: { type: String, default: '' },
  /** Hand cards for this player */
  handCards: { type: Array, default: () => [] },
  /** Thought bubble data */
  thought: { type: Object, default: null },
});

const BID_POSITIONS = { 0: '首家', 1: '二家', 2: '尾家' };
const SEAT_NAMES = { S: '南', E: '东', N: '北', W: '西' };

const positionLabel = computed(() => BID_POSITIONS[props.bidPosition] || '');
const seatName = computed(() => SEAT_NAMES[props.seat] || props.seat);
const isIdle = computed(() => props.player?.role === 'idle');

const statusText = computed(() => {
  if (props.isCurrentBidder) return '叫分中...';
  if (props.hasBid) {
    if (props.bidAmount === 0) return '不叫';
    return `已叫: ${props.bidAmount} 分`;
  }
  return '等待叫分...';
});

const statusClass = computed(() => {
  if (props.isCurrentBidder) return 'text-amber-400';
  if (props.hasBid) return 'text-slate-500';
  return 'text-slate-600';
});
</script>

<template>
  <div
    :class="[
      'rounded-xl border p-3 transition-all duration-300',
      isCurrentBidder
        ? 'border-amber-400 bg-amber-400/10 ring-1 ring-amber-400/50 shadow-lg shadow-amber-400/10'
        : hasBid
          ? 'border-slate-600 bg-slate-800/40'
          : 'border-slate-700 bg-slate-800/30',
    ]"
  >
    <!-- Header: position + seat + status -->
    <div class="flex items-center justify-between mb-2">
      <div class="flex items-center gap-2">
        <span class="text-xs text-slate-400">{{ positionLabel }}</span>
        <PlayerSeat
          :seat="seat"
          :player="player"
          :show-hand-size="false"
        />
      </div>
      <div class="flex items-center gap-2">
        <span
          v-if="hasBid && bidAmount > 0"
          class="text-xs text-amber-400 font-semibold"
        >
          ✓
        </span>
        <span
          :class="['text-xs font-semibold', statusClass]"
        >
          {{ statusText }}
        </span>
      </div>
    </div>

    <!-- Hand cards (god mode — all active players show full hand) -->
    <div class="mb-2">
      <CardHand
        :cards="handCards"
        :size="isCurrentBidder ? 'sm' : 'xs'"
        :highlight="isCurrentBidder"
        :dimmed="hasBid && !isCurrentBidder"
      />
    </div>

    <!-- Current high bid info (only show for current bidder) -->
    <div v-if="isCurrentBidder && currentHighBid > 0" class="text-[11px] text-slate-500 mb-1">
      当前最高: {{ currentHighBid }} 分 ({{ SEAT_NAMES[currentHighBidder] || currentHighBidder }})
    </div>

    <!-- Thought bubble (only for current bidder) -->
    <div v-if="isCurrentBidder && thought" class="mt-2">
      <div class="bg-amber-900/70 border border-amber-500/40 rounded-xl p-2.5 shadow-lg relative">
        <!-- Cloud bumps -->
        <div class="absolute -top-1.5 left-3 w-3 h-3 bg-amber-900/70 border-t border-l border-amber-500/40 rounded-tl-xl" />
        <div class="absolute -top-1.5 right-3 w-3 h-3 bg-amber-900/70 border-t border-r border-amber-500/40 rounded-tr-xl" />
        <div class="absolute -top-2 left-6 w-4 h-2 bg-amber-900/70 border-t border-amber-500/40 rounded-t-lg" />
        <!-- Content -->
        <div class="text-[10px] text-amber-400 font-semibold mb-0.5">
          💭 {{ thought.agent_id || seatName }} · 叫分思考
        </div>
        <p class="text-slate-300 text-[10px] leading-relaxed whitespace-pre-wrap line-clamp-3">
          {{ thought.reasoning }}
        </p>
      </div>
    </div>
  </div>
</template>
