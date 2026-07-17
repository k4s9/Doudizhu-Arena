<script setup>
/**
 * Displays a hand of cards in sorted order (rank desc, suit S>H>C>D).
 * Enhanced with more size options and dimmed variant.
 */
const props = defineProps({
  cards: { type: Array, default: () => [] },
  size: { type: String, default: 'md' }, // xs | sm | md | lg | xl
  faceDown: { type: Boolean, default: false },
  highlight: { type: Boolean, default: false },
  /** Dim the cards (used for non-active players during bidding) */
  dimmed: { type: Boolean, default: false },
  /** Max cards to show before compacting with a "+N" badge */
  maxVisible: { type: Number, default: 99 },
});

function suitColor(card) {
  if (!card) return '';
  if (card.startsWith('♠')) return 'text-slate-900';   // black
  if (card.startsWith('♥')) return 'text-red-600';      // red
  if (card.startsWith('♣')) return 'text-slate-900';    // black (same as spades)
  if (card.startsWith('♦')) return 'text-orange-600';   // orange/red
  // Jokers: 大王 red, 小王 black
  if (card === '大王') return 'text-red-600';
  if (card === '小王') return 'text-slate-900';
  return 'text-slate-700';
}

const sizeMap = {
  xs: 'w-5 h-8 text-[9px]',
  sm: 'w-7 h-10 text-[10px]',
  md: 'w-9 h-12 text-xs',
  lg: 'w-11 h-16 text-sm',
  xl: 'w-14 h-20 text-base',
};
</script>

<template>
  <div
    class="flex flex-wrap items-center"
    :class="{
      'ring-2 ring-amber-400 rounded-lg p-1': highlight,
      'opacity-70': dimmed && !highlight,
      'opacity-40': dimmed && !highlight && !faceDown,
    }"
  >
    <template v-if="faceDown">
      <div
        v-for="(_, i) in cards"
        :key="i"
        :class="[
          sizeMap[size] || sizeMap.md,
          'rounded border bg-slate-700 border-slate-600 shadow-sm',
        ]"
        :style="{ marginLeft: i > 0 ? '-0.25rem' : '0' }"
      />
    </template>
    <template v-else-if="cards.length > maxVisible">
      <div
        v-for="(card, i) in cards.slice(0, maxVisible)"
        :key="i"
        :class="[
          sizeMap[size] || sizeMap.md,
          'rounded border bg-white flex items-center justify-center font-medium shadow-sm',
          suitColor(card),
        ]"
        :style="{ marginLeft: i > 0 ? '-0.25rem' : '0' }"
      >
        <span>{{ card }}</span>
      </div>
      <span class="text-xs text-slate-400 ml-1 font-semibold">
        +{{ cards.length - maxVisible }}
      </span>
    </template>
    <template v-else>
      <div
        v-for="(card, i) in cards"
        :key="i"
        :class="[
          sizeMap[size] || sizeMap.md,
          'rounded border bg-white flex items-center justify-center font-medium shadow-sm',
          suitColor(card),
        ]"
        :style="{ marginLeft: i > 0 ? '-0.25rem' : '0' }"
      >
        <span>{{ card }}</span>
      </div>
    </template>
    <div v-if="!cards || cards.length === 0" class="text-slate-500 text-xs italic py-2">
      无牌
    </div>
  </div>
</template>
