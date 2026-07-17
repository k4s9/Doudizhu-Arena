<script setup>
import { ref, computed } from 'vue';

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  max: { type: Number, default: 0 },
});

const emit = defineEmits(['update:modelValue', 'play', 'pause']);

const playing = ref(false);
const speed = ref(1);
let timer = null;

const progress = computed(() => (props.max > 0 ? (props.modelValue / props.max) * 100 : 0));

function togglePlay() {
  playing.value = !playing.value;
  if (playing.value) {
    emit('play');
    startAutoPlay();
  } else {
    clearInterval(timer);
    emit('pause');
  }
}

function startAutoPlay() {
  clearInterval(timer);
  timer = setInterval(() => {
    if (props.modelValue < props.max) {
      emit('update:modelValue', props.modelValue + 1);
    } else {
      playing.value = false;
      clearInterval(timer);
    }
  }, 1000 / speed.value);
}

function stop() {
  playing.value = false;
  clearInterval(timer);
  emit('update:modelValue', 0);
  emit('pause');
}

function stepForward() {
  if (props.modelValue < props.max) {
    emit('update:modelValue', props.modelValue + 1);
  }
}

function stepBack() {
  if (props.modelValue > 0) {
    emit('update:modelValue', props.modelValue - 1);
  }
}

function setSpeed(s) {
  speed.value = s;
  if (playing.value) {
    clearInterval(timer);
    startAutoPlay();
  }
}
</script>

<template>
  <div class="flex items-center gap-3 bg-slate-800 rounded-lg border border-slate-700 p-3">
    <!-- Playback controls -->
    <div class="flex items-center gap-1">
      <button @click="stop" class="px-2 py-1 text-xs rounded bg-slate-700 hover:bg-slate-600 text-slate-300">
        ⏹
      </button>
      <button @click="stepBack" class="px-2 py-1 text-xs rounded bg-slate-700 hover:bg-slate-600 text-slate-300">
        ⏮
      </button>
      <button @click="togglePlay" class="px-3 py-1 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white font-medium">
        {{ playing ? '⏸ 暂停' : '▶ 播放' }}
      </button>
      <button @click="stepForward" class="px-2 py-1 text-xs rounded bg-slate-700 hover:bg-slate-600 text-slate-300">
        ⏭
      </button>
    </div>

    <!-- Progress bar -->
    <div class="flex-1 bg-slate-700 rounded-full h-2 overflow-hidden">
      <div
        class="bg-amber-500 h-full rounded-full transition-all duration-300"
        :style="{ width: progress + '%' }"
      ></div>
    </div>

    <span class="text-xs text-slate-400 font-mono min-w-[5rem]">
      {{ modelValue }} / {{ max }}
    </span>

    <!-- Speed -->
    <div class="flex items-center gap-1">
      <button
        v-for="s in [0.5, 1, 2, 4]"
        :key="s"
        @click="setSpeed(s)"
        :class="[
          'px-2 py-0.5 text-xs rounded',
          speed === s ? 'bg-amber-600 text-white' : 'bg-slate-700 text-slate-400 hover:bg-slate-600',
        ]"
      >
        {{ s }}x
      </button>
    </div>
  </div>
</template>
