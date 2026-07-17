<script setup>
/**
 * CenterBoard — central information area during the playing phase.
 * Shows: dealer, landlord, current pattern, last round's play.
 */
import { computed } from 'vue';
import CardHand from './CardHand.vue';

const props = defineProps({
  dealer: { type: String, default: '' },
  landlord: { type: String, default: '' },
  finalBid: { type: Number, default: 0 },
  currentPattern: { type: Object, default: null },
  /** The most recent play action (from play_history) */
  lastPlay: { type: Object, default: null },
  /** The most recent pass action */
  lastPass: { type: Object, default: null },
  /** Whether a new round just started (clear center) */
  newRound: { type: Boolean, default: false },
});

const SEAT_NAMES = { S: '南', E: '东', N: '北', W: '西' };
const PATTERN_NAMES = {
  single: '单张', pair: '对子', triple: '三条',
  triple_plus_one: '三带一', triple_plus_two: '三带二',
  straight: '单顺', consecutive_pairs: '连对',
  airplane: '飞机（不带）', airplane_plus_ones: '飞机带单',
  airplane_plus_pairs: '飞机带对',
  four_plus_two_singles: '四带二单', four_plus_two_pairs: '四带二对',
  four_plus_one_pair: '四带二同单',
  bomb: '💣 炸弹', rocket: '🚀 火箭',
};

const patternName = computed(() => {
  if (!props.currentPattern?.pattern) return null;
  return PATTERN_NAMES[props.currentPattern.pattern] || props.currentPattern.pattern;
});

const patternDetail = computed(() => {
  if (!props.currentPattern) return '';
  const parts = [patternName.value];
  if (props.currentPattern.length) parts.push(`${props.currentPattern.length}张`);
  if (props.currentPattern.max_rank) parts.push(`最大 ${props.currentPattern.max_rank}`);
  return parts.join(' · ');
});

const showLastPlay = computed(() => {
  return !props.newRound && (props.lastPlay || props.lastPass);
});

const lastPlayedCards = computed(() => {
  return props.lastPlay?.action?.cards || [];
});

const lastPlayedPattern = computed(() => {
  if (props.lastPlay?.action?.pattern) {
    return PATTERN_NAMES[props.lastPlay.action.pattern] || props.lastPlay.action.pattern;
  }
  return null;
});
</script>

<template>
  <div class="bg-slate-800/80 rounded-xl border border-slate-700 p-3 text-center min-w-[200px]">
    <!-- Dealer & Landlord info -->
    <div class="flex items-center justify-center gap-4 text-xs text-slate-400 mb-2">
      <span v-if="dealer">首家: {{ SEAT_NAMES[dealer] || dealer }}</span>
      <span v-if="landlord">地主: {{ SEAT_NAMES[landlord] || landlord }} 👑</span>
      <span v-if="finalBid > 0" class="text-amber-400 font-semibold">叫 {{ finalBid }} 分</span>
    </div>

    <!-- Current pattern (what needs to be beaten) -->
    <div v-if="currentPattern" class="mb-2">
      <div class="text-xs text-slate-500">当前牌型</div>
      <div class="text-sm text-amber-400 font-semibold">{{ patternDetail }}</div>
    </div>
    <div v-else class="mb-2 text-xs text-slate-500">
      自由出牌
    </div>

    <!-- Last round play display (persistent until new round) -->
    <div v-if="showLastPlay" class="pt-2 border-t border-slate-700/50">
      <div v-if="lastPlay && lastPlayedCards.length > 0" class="flex flex-col items-center gap-1">
        <div class="text-[10px] text-slate-500">
          {{ SEAT_NAMES[lastPlay.seat] || lastPlay.seat }}
          <span v-if="lastPlayedPattern" class="text-amber-300">→ {{ lastPlayedPattern }}</span>
        </div>
        <CardHand :cards="lastPlayedCards" size="xs" />
      </div>
      <div v-else-if="lastPass" class="text-xs text-slate-500">
        {{ SEAT_NAMES[lastPass.seat] || lastPass.seat }} → 不出
      </div>
    </div>

    <!-- New round / initial state -->
    <div v-else-if="!currentPattern" class="text-xs text-slate-600">
      等待出牌...
    </div>
  </div>
</template>
