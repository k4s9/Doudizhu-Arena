<script setup>
/**
 * DizhuCards — displays the 3 bottom cards (底牌) that the landlord receives.
 */
defineProps({
  cards: { type: Array, default: () => [] },
  size: { type: String, default: 'md' },
});

function suitColor(card) {
  if (!card) return '';
  if (card.startsWith('♠')) return 'text-slate-900';   // black
  if (card.startsWith('♥')) return 'text-red-600';      // red
  if (card.startsWith('♣')) return 'text-slate-900';    // black
  if (card.startsWith('♦')) return 'text-orange-600';   // orange
  if (card === '大王') return 'text-red-600';
  if (card === '小王') return 'text-slate-900';
  return 'text-slate-700';
}

const sizeMap = {
  sm: 'w-6 h-9 text-[10px]',
  md: 'w-8 h-11 text-xs',
  lg: 'w-10 h-14 text-sm',
};
</script>

<template>
  <div class="flex items-center gap-1.5">
    <span class="text-xs text-slate-500">底牌:</span>
    <div
      v-for="(card, i) in cards"
      :key="i"
      :class="[
        sizeMap[size] || sizeMap.md,
        'rounded border bg-white flex items-center justify-center font-medium shadow-sm',
        suitColor(card),
      ]"
    >
      <span>{{ card }}</span>
    </div>
    <span v-if="!cards || cards.length === 0" class="text-xs text-slate-600">—</span>
  </div>
</template>
