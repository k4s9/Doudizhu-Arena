<script setup>
/**
 * One active player in the bidding phase. The hand stays visible because the
 * arena is an observer view; visual priority is given to the player in turn.
 */
import { computed } from 'vue';
import CardHand from './CardHand.vue';

const props = defineProps({
  seat: { type: String, required: true },
  player: { type: Object, default: null },
  bidPosition: { type: Number, default: 0 },
  isCurrentBidder: { type: Boolean, default: false },
  hasBid: { type: Boolean, default: false },
  bidAmount: { type: Number, default: null },
  currentHighBid: { type: Number, default: 0 },
  currentHighBidder: { type: String, default: '' },
  handCards: { type: Array, default: () => [] },
  thought: { type: Object, default: null },
  showRecentThought: { type: Boolean, default: false },
});

const BID_POSITIONS = ['首家', '二家', '尾家'];
const SEAT_NAMES = { S: '南', E: '东', N: '北', W: '西' };

const positionLabel = computed(() => BID_POSITIONS[props.bidPosition] || '叫分席');
const seatName = computed(() => SEAT_NAMES[props.seat] || props.seat);
const teamLabel = computed(() => props.player?.team === 'red' ? '红队' : props.player?.team === 'blue' ? '蓝队' : '未分队');
const teamDotClass = computed(() => props.player?.team === 'red' ? 'bg-rose-400' : props.player?.team === 'blue' ? 'bg-sky-400' : 'bg-slate-500');
const isHighBidder = computed(() => props.currentHighBidder === props.seat && props.currentHighBid > 0);
const showThought = computed(() => props.isCurrentBidder || props.showRecentThought);
const thoughtLabel = computed(() => props.isCurrentBidder ? '正在生成叫分分析' : '最近完成的叫分分析');

const statusText = computed(() => {
  if (props.isCurrentBidder) return '正在叫分';
  if (props.hasBid) return props.bidAmount === 0 ? '不叫' : `叫 ${props.bidAmount} 分`;
  return '等待叫分';
});

const statusClass = computed(() => {
  if (props.isCurrentBidder) return 'border-amber-300/50 bg-amber-300/15 text-amber-200';
  if (props.hasBid && props.bidAmount > 0) return 'border-emerald-300/35 bg-emerald-300/10 text-emerald-200';
  if (props.hasBid) return 'border-slate-500/50 bg-slate-800/80 text-slate-400';
  return 'border-slate-700 bg-slate-900/50 text-slate-500';
});
</script>

<template>
  <section
    :class="[
      'bid-seat border transition-colors duration-300',
      isCurrentBidder
        ? 'bid-seat--active border-amber-300/80 bg-slate-900'
        : hasBid
          ? 'border-slate-600/70 bg-slate-900/75'
          : 'border-slate-800 bg-slate-950/35',
    ]"
  >
    <div class="flex items-start justify-between gap-3">
      <div class="flex min-w-0 items-center gap-2.5">
        <span :class="['h-2.5 w-2.5 shrink-0 rounded-full', teamDotClass]" aria-hidden="true" />
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span class="text-[11px] font-semibold text-slate-500">{{ positionLabel }}</span>
            <span class="text-xs text-slate-500">{{ seatName }}位</span>
            <span v-if="isHighBidder" class="text-[10px] font-semibold text-emerald-300">当前最高</span>
          </div>
          <p class="truncate text-sm font-semibold text-slate-100">{{ player?.agent_name || '等待选手' }}</p>
          <p class="text-[10px] text-slate-500">{{ teamLabel }} · {{ handCards.length || player?.hand_size || 0 }} 张手牌</p>
        </div>
      </div>

      <span :class="['shrink-0 border px-2 py-1 text-[11px] font-semibold', statusClass]">
        {{ statusText }}
      </span>
    </div>

    <div class="mt-3 min-h-10 overflow-hidden">
      <CardHand
        :cards="handCards"
        size="sm"
        :highlight="isCurrentBidder"
        :dimmed="hasBid && !isCurrentBidder"
      />
    </div>

    <div v-if="isCurrentBidder && currentHighBid > 0" class="mt-3 border-t border-slate-700/70 pt-2 text-[11px] text-slate-400">
      当前门槛
      <span class="ml-1 font-semibold text-amber-200">{{ currentHighBid }} 分</span>
      <span class="ml-1 text-slate-500">由 {{ SEAT_NAMES[currentHighBidder] || currentHighBidder }} 位保持</span>
    </div>

    <div v-if="isCurrentBidder" class="mt-3 flex items-center gap-2 border-t border-slate-700/70 pt-2.5">
      <span class="bid-seat__signal h-2 w-2 rounded-full bg-amber-300" aria-hidden="true" />
      <span class="text-[11px] font-medium text-amber-100">模型正在评估牌力与叫分风险</span>
    </div>

    <div v-if="showThought && thought?.reasoning" class="mt-2.5 border-l-2 border-amber-300/70 bg-slate-950/55 px-3 py-2.5">
      <p class="text-[10px] font-semibold text-amber-200">{{ player?.agent_name || seatName }} · {{ thoughtLabel }}</p>
      <p class="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-slate-300 line-clamp-4">{{ thought.reasoning }}</p>
    </div>
  </section>
</template>

<style scoped>
.bid-seat {
  min-width: 0;
  padding: 0.875rem;
}

.bid-seat--active {
  box-shadow: inset 3px 0 0 rgb(252 211 77), 0 10px 30px rgb(0 0 0 / 0.16);
}

.bid-seat__signal {
  animation: bidding-signal 1.7s ease-in-out infinite;
}

@keyframes bidding-signal {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

@media (prefers-reduced-motion: reduce) {
  .bid-seat__signal { animation: none; }
}
</style>
