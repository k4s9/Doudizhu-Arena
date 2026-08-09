<script setup>
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../api/index.js';

const router = useRouter();
const configs = ref([]);
const selected = ref('');
const hands = ref(20);
const budget = ref(100);
const rotations = ref(1);
const preflight = ref(null);
const error = ref('');
const busy = ref(false);
const model = computed(() => configs.value.find(c => c.name === selected.value));
function spec() {
  const c = model.value;
  return { experiment_id: `reliability-${Date.now()}`, seed_set_path: 'src/backend/evaluation/seed_sets/v1.json', total_hands: Number(hands.value), ko_enabled: false, seat_rotations: Number(rotations.value), timeout_config: { bidding_seconds: 60, individual_play_seconds: 360, team_pool_seconds: 3600, max_consecutive_llm_failures: 3 }, pricing_version: 'pricing-v1', budget_limit_usd: Number(budget.value), models: [{ config_name: c.name, provider: c.provider, model: c.model, base_url: c.base_url || null, parameters: { max_tokens: 4096, temperature: 0, top_p: 1, seed: null } }], variants: [
    { variant_id: 'baseline_no_feedback', retry_limit: 0, rule_feedback: false, enable_reflection: false, memory_mode: 'disabled' },
    { variant_id: 'rule_feedback', retry_limit: 3, rule_feedback: true, enable_reflection: false, memory_mode: 'disabled' },
    { variant_id: 'reflection_memory', retry_limit: 3, rule_feedback: true, enable_reflection: true, memory_mode: 'read_only' },
  ] };
}
async function check() { busy.value = true; error.value = ''; try { preflight.value = await api.preflightEvaluation(spec()); } catch (e) { error.value = e.message; } finally { busy.value = false; } }
async function create() { busy.value = true; try { const result = await api.createEvaluation(spec()); router.push(`/evaluations/${result.run_id}`); } catch (e) { error.value = e.message; } finally { busy.value = false; } }
onMounted(async () => { try { configs.value = (await api.listConfigs()).configs.filter(c => c.provider !== 'random'); selected.value = configs.value[0]?.name || ''; } catch (e) { error.value = e.message; } });
</script>

<template>
  <div class="max-w-3xl space-y-6"><div><h1 class="text-2xl font-bold text-white">新建可靠性实验</h1><p class="mt-1 text-sm text-slate-400">三种策略共享同一冻结种子集和模型快照。</p></div>
    <div v-if="error" class="border border-red-700 bg-red-950/40 p-3 text-red-300 rounded">{{ error }}</div>
    <div class="border border-slate-700 bg-slate-900 p-5 rounded space-y-4">
      <label class="block text-sm text-slate-300">模型配置<select v-model="selected" class="mt-1 w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-white"><option v-for="c in configs" :key="c.id" :value="c.name">{{ c.name }} · {{ c.provider }} · {{ c.model }}</option></select></label>
      <div class="grid sm:grid-cols-3 gap-4"><label class="text-sm text-slate-300">每场手数<input v-model.number="hands" type="number" min="1" max="200" class="mt-1 w-full bg-slate-800 border border-slate-600 rounded px-3 py-2"></label><label class="text-sm text-slate-300">座位轮换<input v-model.number="rotations" type="number" min="1" max="8" class="mt-1 w-full bg-slate-800 border border-slate-600 rounded px-3 py-2"></label><label class="text-sm text-slate-300">预算上限 USD<input v-model.number="budget" type="number" min="1" class="mt-1 w-full bg-slate-800 border border-slate-600 rounded px-3 py-2"></label></div>
      <div class="text-sm text-slate-400">固定包含：无规则反馈、规则反馈、复盘记忆。正式调用只会在运行详情页二次确认后开始。</div>
    </div>
    <div v-if="preflight" class="border border-slate-700 p-4 rounded"><div :class="preflight.ok ? 'text-emerald-400' : 'text-red-400'">{{ preflight.ok ? '预检通过' : '预检未通过' }}</div><div class="mt-2 text-sm text-slate-400">{{ preflight.seed_count }} 个种子，{{ preflight.task_count }} 个任务</div><div v-for="item in preflight.errors" :key="item" class="text-sm text-red-300">{{ item }}</div></div>
    <div class="flex justify-end gap-3"><button @click="check" :disabled="busy || !selected" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded">运行预检</button><button @click="create" :disabled="busy || !preflight?.ok" class="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 rounded font-medium">创建运行计划</button></div>
  </div>
</template>
