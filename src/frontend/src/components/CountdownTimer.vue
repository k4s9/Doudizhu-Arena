<script setup>
/**
 * CountdownTimer — displays a countdown timer ring + digital readout.
 * Only shown for the currently active player.
 */
import { computed, onUnmounted, ref } from 'vue';

const props = defineProps({
  /** Total seconds allotted */
  totalSeconds: { type: Number, default: 60 },
  /** Remaining seconds (will be driven by WS time_remaining in future) */
  remainingSeconds: { type: Number, default: 0 },
  /** Unix timestamp supplied by the match server for the active play turn */
  deadlineMs: { type: Number, default: 0 },
  /** Duration supplied with deadlineMs; used for the progress ring */
  timeoutMs: { type: Number, default: 0 },
  /** Whether to show a simplified text-only version */
  compact: { type: Boolean, default: false },
});

const nowMs = ref(Date.now());
const ticker = setInterval(() => {
  nowMs.value = Date.now();
}, 250);

onUnmounted(() => clearInterval(ticker));

const effectiveRemainingSeconds = computed(() => {
  if (props.deadlineMs > 0) {
    return Math.max(0, Math.ceil((props.deadlineMs - nowMs.value) / 1000));
  }
  return props.remainingSeconds;
});

const effectiveTotalSeconds = computed(() => {
  return props.timeoutMs > 0 ? props.timeoutMs / 1000 : props.totalSeconds;
});

const display = computed(() => {
  if (props.deadlineMs <= 0 && props.remainingSeconds <= 0) return '';
  const secs = Math.max(0, effectiveRemainingSeconds.value);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
});

const pct = computed(() => {
  if (effectiveTotalSeconds.value <= 0) return 100;
  return Math.max(0, Math.min(100, (effectiveRemainingSeconds.value / effectiveTotalSeconds.value) * 100));
});

const ringColor = computed(() => {
  if (pct.value <= 15) return 'text-red-400';
  if (pct.value <= 30) return 'text-amber-400';
  return 'text-green-400';
});
</script>

<template>
  <div v-if="display" class="flex items-center gap-1.5">
    <!-- Simple text timer -->
    <span
      v-if="compact"
      :class="[
        'font-mono text-xs font-semibold tabular-nums',
        pct <= 15 ? 'text-red-400' : pct <= 30 ? 'text-amber-400' : 'text-slate-300',
      ]"
    >
      ⏱ {{ display }}
    </span>
    <!-- Ring + text timer -->
    <div v-else class="relative inline-flex items-center justify-center">
      <svg class="w-8 h-8 transform -rotate-90" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15" fill="none" stroke="currentColor"
          class="text-slate-700" stroke-width="3" />
        <circle cx="18" cy="18" r="15" fill="none" stroke="currentColor"
          :class="ringColor"
          stroke-width="3" stroke-linecap="round"
          :stroke-dasharray="`${pct * 94.2 / 100} 94.2`" />
      </svg>
      <span class="absolute text-[9px] font-mono font-semibold tabular-nums text-slate-300">
        {{ display }}
      </span>
    </div>
  </div>
</template>
