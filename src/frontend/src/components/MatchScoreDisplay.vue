<script setup>
/**
 * MatchScoreDisplay — top-of-page score bar showing:
 *   红队 N  —  第 X/Y 副  —  蓝队 N
 * Pure numeric, no bar style.
 */
defineProps({
  score: { type: Object, default: () => ({ red: 0, blue: 0 }) },
  currentHand: { type: Number, default: 0 },
  totalHands: { type: Number, default: 20 },
  koStatus: { type: Object, default: null },
});
</script>

<template>
  <div class="bg-slate-800 rounded-xl border border-slate-700 px-6 py-4">
    <div class="flex items-center justify-center gap-8">
      <!-- Red Team -->
      <div class="flex items-center gap-3">
        <span class="text-lg font-bold text-red-400">红队</span>
        <span class="text-4xl font-bold text-red-400 tabular-nums">{{ score?.red || 0 }}</span>
      </div>

      <!-- Center divider -->
      <div class="flex flex-col items-center gap-1">
        <span class="text-sm text-slate-500">第 {{ currentHand }}/{{ totalHands }} 副</span>
        <span
          v-if="koStatus?.possible && koStatus?.lead > 0"
          class="text-xs text-amber-400 font-semibold animate-pulse"
        >
          ⚡ KO 可能!
        </span>
      </div>

      <!-- Blue Team -->
      <div class="flex items-center gap-3">
        <span class="text-4xl font-bold text-blue-400 tabular-nums">{{ score?.blue || 0 }}</span>
        <span class="text-lg font-bold text-blue-400">蓝队</span>
      </div>
    </div>
  </div>
</template>
