<script setup>
const props = defineProps({
  score: { type: Object, default: () => ({ red: 0, blue: 0 }) },
  currentHand: { type: Number, default: 0 },
  totalHands: { type: Number, default: 20 },
  koStatus: { type: Object, default: null },
});
</script>

<template>
  <div class="bg-slate-800 rounded-xl border border-slate-700 p-4">
    <h3 class="text-sm font-semibold text-slate-400 mb-3">比赛比分</h3>
    <div class="flex items-center justify-between gap-4">
      <!-- Red Team -->
      <div class="flex-1 text-center p-3 rounded-lg bg-red-900/20 border border-red-800/30">
        <div class="text-3xl font-bold text-red-400">{{ score?.red || 0 }}</div>
        <div class="text-xs text-red-300/70 mt-1">红队</div>
      </div>

      <!-- VS -->
      <div class="text-slate-500 text-sm font-mono">
        <div>第 {{ currentHand }}/{{ totalHands }} 副</div>
        <div v-if="koStatus?.possible && koStatus?.lead > 0" class="text-amber-400 text-xs mt-1">
          KO 可能！
        </div>
      </div>

      <!-- Blue Team -->
      <div class="flex-1 text-center p-3 rounded-lg bg-blue-900/20 border border-blue-800/30">
        <div class="text-3xl font-bold text-blue-400">{{ score?.blue || 0 }}</div>
        <div class="text-xs text-blue-300/70 mt-1">蓝队</div>
      </div>
    </div>
  </div>
</template>
