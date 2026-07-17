<script setup>
/**
 * HandScoreBar — horizontal bar of per-hand score results.
 * Colored by which team scored: red = red gain, blue = blue gain, grey = draw.
 */
defineProps({
  handScores: { type: Array, default: () => [] },
});
</script>

<template>
  <div v-if="handScores.length" class="bg-slate-800 rounded-xl border border-slate-700 p-3">
    <h3 class="text-xs font-semibold text-slate-400 mb-2">每副得分</h3>
    <div class="flex flex-wrap gap-1.5">
      <div
        v-for="hs in [...handScores].sort((a, b) => a.hand_num - b.hand_num)"
        :key="hs.hand_num"
        class="flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border"
        :class="{
          'bg-red-900/20 border-red-800/40': (hs.red_diff || 0) > 0,
          'bg-blue-900/20 border-blue-800/40': (hs.red_diff || 0) < 0,
          'bg-slate-700/30 border-slate-700': (hs.red_diff || 0) === 0,
        }"
      >
        <span class="text-slate-500">第{{ hs.hand_num }}副</span>
        <span
          :class="{
            'text-red-400': (hs.red_diff || 0) > 0,
            'text-blue-400': (hs.red_diff || 0) < 0,
            'text-slate-500': (hs.red_diff || 0) === 0,
          }"
          class="font-mono font-semibold"
        >
          {{ (hs.red_diff || 0) > 0 ? '+' : '' }}{{ hs.red_diff || 0 }}
        </span>
        <span class="text-[9px] text-slate-600 ml-0.5">
          A:{{ hs.details?.table_a_score?.red || 0 }}/{{ hs.details?.table_a_score?.blue || 0 }}
          B:{{ hs.details?.table_b_score?.red || 0 }}/{{ hs.details?.table_b_score?.blue || 0 }}
        </span>
      </div>
    </div>
  </div>
</template>
