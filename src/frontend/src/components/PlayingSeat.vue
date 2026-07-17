<script setup>
/**
 * PlayingSeat — a farmer (non-landlord) player during the playing phase.
 * Shows player info, remaining hand count, last played cards + thought, countdown.
 *
 * Fixes:
 * - Last thought shown alongside last play for non-current players (via persisted thought)
 * - Compact layout with less clutter
 */
import { computed } from 'vue';
import PlayerSeat from './PlayerSeat.vue';
import PassIndicator from './PassIndicator.vue';
import CountdownTimer from './CountdownTimer.vue';
import CardHand from './CardHand.vue';

const props = defineProps({
  seat: { type: String, required: true },
  player: { type: Object, default: null },
  /** Whether this seat is the current active player */
  isCurrentPlayer: { type: Boolean, default: false },
  /** The last played action (cards) for this seat */
  lastPlayed: { type: Object, default: null },
  /** The last action (play or pass) for this seat */
  lastAction: { type: Object, default: null },
  /** Hand cards to show (only for landlord; farmers show face-down count) */
  handCards: { type: Array, default: () => [] },
  /** Whether to show full hand cards */
  showHandCards: { type: Boolean, default: false },
  /** Thought bubble data (for current player = live; for non-current = persisted) */
  thought: { type: Object, default: null },
  /** Remaining seconds for countdown (0 = not shown) */
  remainingSeconds: { type: Number, default: 0 },
  /** Total timeout seconds */
  totalSeconds: { type: Number, default: 360 },
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

/** Show thought for this player (current or persisted) */
const showThought = computed(() => {
  return props.thought && (props.isCurrentPlayer || props.lastPlayed || props.lastWasPass);
});
</script>

<template>
  <div
    :class="[
      'rounded-xl border p-2.5 transition-all duration-300',
      isCurrentPlayer
        ? 'border-amber-400 bg-amber-400/10 ring-1 ring-amber-400/50 shadow-lg shadow-amber-400/10'
        : 'border-slate-600 bg-slate-800/50',
    ]"
  >
    <!-- Player info header (compact) -->
    <div class="flex items-center justify-between gap-2 mb-1.5">
      <PlayerSeat
        :seat="seat"
        :player="player"
        :show-hand-size="true"
      />
      <CountdownTimer
        v-if="isCurrentPlayer && remainingSeconds > 0"
        :remaining-seconds="remainingSeconds"
        :total-seconds="totalSeconds"
        compact
      />
    </div>

    <!-- Hand cards: show real cards in god mode, face-down backs otherwise -->
    <div v-if="showHandCards && handCards.length" class="mb-1.5">
      <CardHand
        :cards="handCards"
        size="xs"
        :highlight="isCurrentPlayer"
      />
    </div>
    <div v-else class="flex flex-wrap gap-px mb-1.5">
      <div
        v-for="i in Math.min(player?.hand_size || 0, 13)"
        :key="i"
        class="w-4 h-7 rounded-sm border bg-slate-700 border-slate-600"
        :style="{ marginLeft: i > 1 ? '-0.15rem' : '0' }"
      />
      <span v-if="(player?.hand_size || 0) > 13" class="text-[9px] text-slate-500 ml-1 self-center">
        +{{ player.hand_size - 13 }}
      </span>
      <span v-if="!player?.hand_size" class="text-[9px] text-slate-600 italic">0 张</span>
    </div>

    <!-- Last played action + thought together -->
    <div v-if="lastPlayedCards.length > 0" class="border-t border-slate-700/50 pt-1.5">
      <div class="text-[9px] text-slate-500 mb-0.5">
        上次出牌<span v-if="lastPlayedPattern"> · {{ lastPlayedPattern }}</span>
      </div>
      <CardHand :cards="lastPlayedCards" size="xs" />
    </div>
    <div v-else-if="lastWasPass" class="border-t border-slate-700/50 pt-1.5 flex justify-center">
      <PassIndicator size="sm" />
    </div>

    <!-- Thought bubble (for current player or persisted alongside last play) -->
    <div v-if="showThought" class="mt-1.5">
      <div class="bg-amber-900/60 border border-amber-500/30 rounded-lg p-2 shadow-lg relative">
        <div class="text-[9px] text-amber-400 font-semibold mb-0.5">
          💭 {{ thought.agent_id || seatName }}
          <span v-if="thought.round" class="text-slate-500">第{{ thought.round }}轮</span>
        </div>
        <p class="text-slate-300 text-[9px] leading-relaxed whitespace-pre-wrap line-clamp-3">
          {{ thought.reasoning }}
        </p>
      </div>
    </div>
  </div>
</template>
