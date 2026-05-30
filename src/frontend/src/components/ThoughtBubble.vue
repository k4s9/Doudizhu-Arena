<script setup>
import { ref, watch } from 'vue';

const props = defineProps({
  thought: { type: Object, default: null },
});

const showing = ref(false);

watch(() => props.thought, (val) => {
  if (val) {
    showing.value = true;
    // Auto-hide after a delay for live thoughts
  }
}, { immediate: true });
</script>

<template>
  <div v-if="thought" class="relative">
    <div class="bg-amber-900/30 border border-amber-700/50 rounded-lg p-3 text-sm max-w-md">
      <div class="flex items-center gap-2 mb-1">
        <span class="text-xs font-semibold text-amber-400">{{ thought.seat }} — {{ thought.phase }}</span>
        <span v-if="thought.round" class="text-xs text-slate-500">R{{ thought.round }}</span>
      </div>
      <p class="text-slate-300 leading-relaxed whitespace-pre-wrap text-xs">
        {{ thought.reasoning }}
      </p>
      <div v-if="thought.decision" class="mt-1 text-xs text-slate-500 font-mono">
        Decision: {{ JSON.stringify(thought.decision) }}
      </div>
    </div>
  </div>
</template>
