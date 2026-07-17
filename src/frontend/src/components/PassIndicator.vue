<script setup>
/**
 * PassIndicator — shows a "不出" (pass) marker.
 */
defineProps({
  size: { type: String, default: 'sm' },
});

const sizeMap = {
  sm: 'w-10 h-10 text-xs',
  md: 'w-14 h-14 text-sm',
  lg: 'w-20 h-20 text-base',
};
</script>

<template>
  <div
    :class="[
      sizeMap[size] || sizeMap.sm,
      'rounded-full border-2 border-slate-500 bg-slate-700/50 flex items-center justify-center text-slate-400 font-semibold',
    ]"
  >
    不出
  </div>
</template>
