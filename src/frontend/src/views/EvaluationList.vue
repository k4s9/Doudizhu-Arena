<script setup>
import { onMounted, ref } from 'vue';
import { api } from '../api/index.js';

const runs = ref([]);
const error = ref('');
async function load() {
  try { runs.value = (await api.listEvaluations()).runs; } catch (e) { error.value = e.message; }
}
onMounted(load);
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between gap-4">
      <div><h1 class="text-2xl font-bold text-white">可靠性实验</h1><p class="mt-1 text-sm text-slate-400">固定种子、模型快照与策略变体的批量评测</p></div>
      <router-link to="/evaluations/new" class="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded text-sm font-medium">新建实验</router-link>
    </div>
    <div v-if="error" class="border border-red-700 bg-red-950/40 p-3 text-red-300 rounded">{{ error }}</div>
    <div class="border border-slate-700 bg-slate-900 overflow-x-auto rounded">
      <table class="w-full text-sm"><thead class="text-slate-400 border-b border-slate-700"><tr><th class="text-left p-3">实验</th><th class="text-left p-3">状态</th><th class="text-right p-3">进度</th><th class="text-left p-3">创建时间</th><th></th></tr></thead>
      <tbody><tr v-for="run in runs" :key="run.id" class="border-b border-slate-800"><td class="p-3 text-white">{{ run.experiment_name }}</td><td class="p-3"><span class="px-2 py-1 bg-slate-800 rounded">{{ run.status }}</span></td><td class="p-3 text-right tabular-nums">{{ run.finished_count || 0 }} / {{ run.task_count || 0 }}</td><td class="p-3 text-slate-400">{{ run.created_at }}</td><td class="p-3 text-right"><router-link :to="`/evaluations/${run.id}`" class="text-amber-400 hover:text-amber-300">查看</router-link></td></tr>
      <tr v-if="!runs.length"><td colspan="5" class="p-10 text-center text-slate-500">尚未创建实验</td></tr></tbody></table>
    </div>
  </div>
</template>
