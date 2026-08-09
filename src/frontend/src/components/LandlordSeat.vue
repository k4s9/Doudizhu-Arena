<script setup>
/**
 * LandlordSeat — the landlord player during the playing phase.
 * Main display: full hand cards (god mode), crown, countdown, thought bubble.
 * Placed at the bottom center of the playing view.
 */
import { computed } from 'vue';
import CardHand from './CardHand.vue';
import CountdownTimer from './CountdownTimer.vue';
import PassIndicator from './PassIndicator.vue';

const props = defineProps({
  seat: { type: String, required: true },
  player: { type: Object, default: null },
  /** Whether this is the current active player */
  isCurrentPlayer: { type: Boolean, default: false },
  /** Full hand cards */
  handCards: { type: Array, default: () => [] },
  /** The last played action (cards) */
  lastPlayed: { type: Object, default: null },
  /** The last action (play or pass) */
  lastAction: { type: Object, default: null },
  /** Thought bubble data */
  thought: { type: Object, default: null },
  /** Server-authoritative deadline for this active play turn */
  turnTimer: { type: Object, default: null },
});

const SEAT_NAMES = { S: '南', E: '东', N: '北', W: '西' };
const PATTERN_NAMES = {
  single: '单张', pair: '对子', triple: '三条',
  triple_plus_one: '三带一', triple_plus_two: '三带二',
  straight: '单顺', consecutive_pairs: '连对',
  airplane: '飞机', airplane_plus_ones: '飞机带单',
  airplane_plus_pairs: '飞机带对',
  four_plus_two_singles: '四带二单', four_plus_two_pairs: '四带二对',
  four_plus_one_pair: '四带二同单',
  bomb: '💣炸弹', rocket: '🚀火箭',
};
const seatName = computed(() => SEAT_NAMES[props.seat] || props.seat);

const teamColorClass = computed(() => {
  if (props.player?.team === 'red') return 'text-red-400';
  if (props.player?.team === 'blue') return 'text-blue-400';
  return 'text-slate-300';
});

const lastPlayedCards = computed(() => {
  return props.lastPlayed?.cards || [];
});

const lastPlayedPattern = computed(() => {
  if (props.lastPlayed?.pattern) {
    return PATTERN_NAMES[props.lastPlayed.pattern] || props.lastPlayed.pattern;
  }
  return null;
});

const lastWasPass = computed(() => {
  return props.lastAction?.action?.type === 'pass' && !props.lastPlayed;
});
</script>

<template>
  <div
    :class="[
      'rounded-xl border p-4 transition-all duration-300',
      isCurrentPlayer
        ? 'border-amber-400 bg-amber-400/10 ring-2 ring-amber-400/50 shadow-xl shadow-amber-400/20'
        : 'border-amber-500/30 bg-amber-500/5',
    ]"
  >
    <!-- Header: Crown + Name + Countdown -->
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2">
        <span class="text-2xl">👑</span>
        <div>
          <div class="flex items-center gap-2">
            <span :class="['text-base font-bold', teamColorClass]">
              {{ player?.agent_name || seatName }}
            </span>
            <span class="text-xs text-slate-500">{{ seatName }}</span>
          </div>
          <div class="flex items-center gap-2 text-xs text-slate-400">
            <span class="px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 text-[10px] font-semibold">地主</span>
            <span>{{ player?.hand_size || 0 }} 张</span>
          </div>
        </div>
      </div>
      <CountdownTimer
        v-if="isCurrentPlayer && turnTimer"
        :deadline-ms="turnTimer.deadline_ms"
        :timeout-ms="turnTimer.timeout_ms"
      />
    </div>

    <!-- Full hand cards (god mode) -->
    <div class="mb-3">
      <CardHand
        :cards="handCards"
        size="md"
        :highlight="isCurrentPlayer"
      />
    </div>

    <div v-if="isCurrentPlayer && turnTimer && !thought" class="mb-3 text-xs font-medium text-amber-300">
      正在思考
    </div>

    <!-- Last played / pass -->
    <div v-if="lastPlayedCards.length > 0" class="pt-2 border-t border-slate-700/50">
      <div class="text-[10px] text-slate-500 mb-1">
        上次出牌<span v-if="lastPlayedPattern"> · {{ lastPlayedPattern }}</span>
      </div>
      <CardHand :cards="lastPlayedCards" size="sm" />
    </div>
    <div v-else-if="lastWasPass" class="pt-2 border-t border-slate-700/50 flex justify-center">
      <PassIndicator size="md" />
    </div>

    <!-- Thought bubble (only for current player) -->
    <div v-if="isCurrentPlayer && thought" class="mt-3">
      <div class="bg-amber-900/70 border border-amber-500/40 rounded-xl p-3 shadow-lg relative">
        <div class="absolute -top-1.5 left-4 w-3 h-3 bg-amber-900/70 border-t border-l border-amber-500/40 rounded-tl-xl" />
        <div class="absolute -top-1.5 right-4 w-3 h-3 bg-amber-900/70 border-t border-r border-amber-500/40 rounded-tr-xl" />
        <div class="absolute -top-2 left-8 w-5 h-2 bg-amber-900/70 border-t border-amber-500/40 rounded-t-lg" />
        <div class="text-[10px] text-amber-400 font-semibold mb-1">
          💭 {{ player?.agent_name || seatName }} · 出牌思考
          <span v-if="thought.round">第{{ thought.round }}轮</span>
        </div>
        <p class="text-slate-300 text-xs leading-relaxed whitespace-pre-wrap line-clamp-5">
          {{ thought.reasoning }}
        </p>
      </div>
    </div>
  </div>
</template>
