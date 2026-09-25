<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../api/index.js';

const router = useRouter();
const mode = ref('mock');
const scenario = ref('valid_first');
const template = ref(null);
const configs = ref([]);
const selected = ref('');
const hands = ref(1);
const budget = ref(0.01);
const maxCalls = ref(2000);
const inputPrice = ref(null);
const outputPrice = ref(null);
const priceSource = ref('');
const priceDate = ref('');
const preflight = ref(null);
const error = ref('');
const busy = ref(false);
const model = computed(() => configs.value.find(c => c.name === selected.value));
const controls = computed(() => JSON.stringify([mode.value, scenario.value, selected.value, hands.value, budget.value, maxCalls.value, inputPrice.value, outputPrice.value, priceSource.value, priceDate.value]));
watch(controls, () => { preflight.value = null; });

async function loadTemplate() {
  template.value = null;
  preflight.value = null;
  error.value = '';
  const requested = mode.value;
  try {
    const value = await api.getEvaluationTemplate(requested);
    if (mode.value !== requested) return;
    template.value = value;
    budget.value = requested === 'mock' ? 0.01 : null;
  } catch (e) { error.value = e.message; }
}
watch(mode, loadTemplate);
function spec() {
  if (!template.value) throw new Error('实验模板尚未加载');
  const value = JSON.parse(JSON.stringify(template.value));
  value.experiment_id = `reliability-${Date.now()}`;
  value.total_hands = Number(hands.value);
  value.budget_limit_usd = Number(budget.value);
  value.max_calls = Number(maxCalls.value);
  if (mode.value === 'mock') value.mock_scenario = scenario.value;
  if (mode.value === 'real') {
    const c = model.value;
    if (!c) throw new Error('请选择实际模型配置');
    if ([inputPrice.value, outputPrice.value, budget.value].some(value => value === null || value === '') || !priceSource.value.trim() || !priceDate.value) throw new Error('请填写价格、计价来源、生效日期和预算');
    value.models = [{ config_name: c.name, provider: c.provider, model: c.model, base_url: c.base_url || null,
      parameters: { max_tokens: 512, temperature: 0, top_p: 1, seed: null },
      pricing: { input_per_million: Number(inputPrice.value), output_per_million: Number(outputPrice.value), source: priceSource.value, effective_date: priceDate.value, currency: 'USD' } }];
  }
  return value;
}
async function check() {
  busy.value = true; error.value = '';
  const stamp = controls.value;
  try { const result = await api.preflightEvaluation(spec()); if (stamp === controls.value) preflight.value = result; }
  catch (e) { error.value = e.message; }
  finally { busy.value = false; }
}
async function create() {
  busy.value = true; error.value = '';
  try { const result = await api.createEvaluation(spec()); router.push(`/evaluations/${result.run_id}`); }
  catch (e) { error.value = e.message; }
  finally { busy.value = false; }
}
onMounted(async () => {
  await loadTemplate();
  try { configs.value = (await api.listConfigs()).configs.filter(c => ['openai', 'claude'].includes(c.provider)); selected.value = configs.value[0]?.name || ''; }
  catch (e) { if (mode.value === 'real') error.value = e.message; }
});
</script>

<template>
  <div class="max-w-3xl space-y-6">
    <div><h1 class="text-2xl font-bold text-white">新建可靠性实验</h1><p class="mt-1 text-sm text-slate-400">固定种子、冻结输入，对照单次生成、通用重试与规则反馈。</p></div>
    <div v-if="error" role="alert" class="border border-red-700 p-3 text-red-300 rounded">{{ error }}</div>
    <div class="border border-slate-700 bg-slate-900 p-5 rounded space-y-4">
      <label class="block">运行方式<select v-model="mode" class="field"><option value="mock">本地 mock 演示 · 无模型费用</option><option value="real">真实模型实验</option></select></label>
      <label v-if="mode === 'mock'" class="block">模拟场景<select v-model="scenario" class="field"><option value="valid_first">正常完成</option><option value="play_error">出牌错误与重试</option><option value="initial_error">首次错误与流局</option></select></label>
      <p class="text-sm text-slate-400">三组分别最多生成 1 / 3 / 3 次；两组重试使用相同调用与输出上限。关闭反思、记忆与加赛。</p>
      <template v-if="mode === 'real'">
        <p v-if="template?.timeout_config" class="text-sm text-slate-400">每次叫分决策限时 {{ template.timeout_config.bidding_seconds }} 秒，出牌决策限时 {{ template.timeout_config.individual_play_seconds }} 秒（含重试）；每个任务最多 {{ template.task_timeout_seconds / 60 }} 分钟。</p>
        <label class="block">实际模型<select v-model="selected" class="field"><option v-for="c in configs" :key="c.id" :value="c.name">{{ c.name }} · {{ c.model }}</option></select></label>
        <p v-if="!configs.length" class="text-amber-300">请先在模型配置页添加模型和凭据。</p>
        <div class="grid sm:grid-cols-2 gap-4">
          <label>输入价格（USD / 百万 token）<input v-model.number="inputPrice" type="number" min="0" step="any" class="field"></label>
          <label>输出价格（USD / 百万 token）<input v-model.number="outputPrice" type="number" min="0" step="any" class="field"></label>
          <label>计价来源<input v-model="priceSource" placeholder="供应商价格页或合同" class="field"></label>
          <label>价格生效日期<input v-model="priceDate" type="date" class="field"></label>
        </div>
      </template>
      <div class="grid sm:grid-cols-3 gap-4">
        <label>每场手数<input v-model.number="hands" type="number" min="1" max="200" class="field"></label>
        <label>调用总上限<input v-model.number="maxCalls" type="number" min="1" max="1000000" class="field"></label>
        <label>预算上限（USD）<input v-model.number="budget" type="number" min="0" step="any" class="field" :disabled="mode === 'mock'"></label>
      </div>
      <p class="text-sm text-slate-400">{{ mode === 'mock' ? '2 个合成种子，仅用于工程验证。' : '1 个固定种子起步；预检与创建计划不会调用模型，启动时再次确认费用。' }}</p>
    </div>
    <div v-if="preflight" class="border border-slate-700 p-4 rounded">
      <div :class="preflight.ok ? 'text-emerald-400' : 'text-red-400'">{{ preflight.ok ? '预检通过' : '预检未通过' }}</div>
      <p>{{ preflight.seed_count }} 个种子，{{ preflight.task_count }} 个任务</p>
      <p v-for="item in preflight.errors" :key="item" class="text-red-300">{{ item }}</p>
    </div>
    <div class="flex justify-end gap-3"><button @click="check" :disabled="busy || !template" class="px-4 py-2 bg-slate-700 rounded disabled:opacity-40">运行预检</button><button @click="create" :disabled="busy || !preflight?.ok" class="px-4 py-2 bg-amber-600 rounded disabled:opacity-40">创建运行计划</button></div>
  </div>
</template>
<style scoped>
.field { display: block; width: 100%; margin-top: .4rem; padding: .5rem .75rem; color: white; background: #1e293b; border: 1px solid #475569; border-radius: .25rem; }
</style>
