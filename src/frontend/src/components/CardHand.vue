<script setup>
/**
 * Displays a hand of cards in sorted order (rank desc, suit S>H>C>D).
 */
defineProps({
  cards: { type: Array, default: () => [] },
  size: { type: String, default: 'md' }, // sm | md | lg
  faceDown: { type: Boolean, default: false },
  highlight: { type: Boolean, default: false },
});

function suitColor(card) {
  if (!card) return '';
  if (card.startsWith('♠')) return 'text-slate-900';
  if (card.startsWith('♥')) return 'text-red-600';
  if (card.startsWith('♣')) return 'text-green-700';
  if (card.startsWith('♦')) return 'text-orange-600';
  return 'text-slate-700';
}

const sizeMap = {
  sm: 'w-8 h-12 text-xs',
  md: 'w-10 h-14 text-sm',
  lg: 'w-12 h-16 text-base',
};
</script>

<template>
  <div class="flex flex-wrap gap-0.5" :class="{ 'ring-2 ring-amber-400 rounded-lg p-1': highlight }">
    <div
      v-for="(card, i) in faceDown ? cards : cards"
      :key="i"
      :class="[
        sizeMap[size] || sizeMap.md,
        'rounded border bg-white flex items-center justify-center font-medium shadow-sm',
        faceDown ? 'bg-slate-700 border-slate-600 text-transparent' : suitColor(card),
      ]"
      :style="{ marginLeft: i > 0 ? '-0.25rem' : '0' }"
    >
      <span v-if="!faceDown">{{ card }}</span>
    </div>
    <div v-if="!cards || cards.length === 0" class="text-slate-500 text-sm italic py-2">
      no cards
    </div>
  </div>
</template>
