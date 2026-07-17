<script setup>
/**
 * Displays the play history timeline.
 */
import { computed } from 'vue';

const props = defineProps({
  actions: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
  showCards: { type: Boolean, default: true },
});

const groupedByRound = computed(() => {
  const groups = [];
  let current = null;
  for (const a of props.actions) {
    if (!current || current.round !== a.round) {
      current = { round: a.round, plays: [] };
      groups.push(current);
    }
    current.plays.push(a);
  }
  return groups;
});

function actionIcon(a) {
  return a.action?.type === 'pass' ? '⏭' : '▶';
}

function actionSummary(a) {
  if (a.action?.type === 'pass') return '不出';
  const cards = a.action?.cards || [];
  return cards.join('');
}

const PATTERN_NAMES = {
  single: '单张',
  pair: '对子',
  triple: '三张',
  triple_with_one: '三带一',
  triple_with_pair: '三带二',
  straight: '顺子',
  straight_pairs: '连对',
  airplane: '飞机',
  airplane_with_wings: '飞机带翼',
  bomb: '炸弹',
  rocket: '火箭',
  four_with_two: '四带二',
};

function suitColor(cards) {
  if (!cards?.length) return '';
  const c = cards[0];
  if (c.startsWith('♠')) return 'text-slate-300';
  if (c.startsWith('♥')) return 'text-red-400';
  if (c.startsWith('♣')) return 'text-green-400';
  if (c.startsWith('♦')) return 'text-orange-400';
  return 'text-slate-300';
}
</script>

<template>
  <div class="overflow-y-auto" :class="compact ? 'max-h-48' : 'max-h-96'">
    <div v-if="!actions.length" class="text-slate-500 text-sm italic py-2">暂无出牌记录</div>
    <div v-for="group in groupedByRound" :key="group.round">
      <div class="text-xs text-slate-600 font-mono mb-1 mt-2">第 {{ group.round }} 轮</div>
      <div
        v-for="a in group.plays"
        :key="a.seat + a.timestamp_ms"
        class="flex items-center gap-2 py-1 px-2 rounded hover:bg-slate-800/50 text-sm"
      >
        <span class="w-6 text-center text-xs font-mono text-slate-500">{{ actionIcon(a) }}</span>
        <span class="w-6 text-center font-semibold text-slate-300">{{ a.seat }}</span>
        <span :class="[a.action?.type === 'pass' ? 'text-slate-500 italic' : suitColor(a.action?.cards), 'font-mono']">
          {{ actionSummary(a) }}
        </span>
        <span v-if="a.action?.pattern" class="text-xs text-slate-500">({{ PATTERN_NAMES[a.action.pattern] || a.action.pattern }})</span>
      </div>
    </div>
  </div>
</template>
