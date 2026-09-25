<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { api } from '../api/index.js';
const props = defineProps({ id: String });
const data = ref(null);
const report = ref(null);
const error = ref('');
const busy = ref(false);
const selectedFailure = ref('');
let timer;
let lastReportStatus = '';
const isMock = computed(() => data.value?.run.manifest.models[0].provider === 'mock');
const progress = computed(() => { const c = data.value?.counts; const n = data.value?.tasks.length || 0; return n ? Math.round(((c.finished + c.failed + c.cancelled) / n) * 100) : 0; });
const failure = computed(() => report.value?.failures.find(f => f.decision.decision_id === selectedFailure.value));
const files = ['manifest.json', 'integrity.json', 'summary.json', 'metrics.csv', 'seed_level.csv', 'report.md'];
function ratio(value) { return value?.denominator ? `${value.numerator}/${value.denominator} (${(value.value * 100).toFixed(1)}%)` : 'N/A (0/0)'; }
async function loadReport() {
  try { report.value = await api.getEvaluationReport(props.id); selectedFailure.value ||= report.value.failures[0]?.decision.decision_id || ''; }
  catch (e) { error.value = e.message; }
}
async function load() {
  try {
    data.value = await api.getEvaluation(props.id);
    const status = data.value.run.status;
    if (['finished', 'failed', 'cancelled'].includes(status) && lastReportStatus !== status) { lastReportStatus = status; await loadReport(); }
  } catch (e) { error.value = e.message; }
}
async function action(kind) {
  busy.value = true; error.value = '';
  try {
    if (kind !== 'cancel' && !isMock.value && !confirm(`将调用 ${data.value.run.manifest.models[0].model}，预算上限 $${data.value.run.manifest.frozen_spec.budget_limit_usd}，确定启动？`)) return;
    if (kind === 'start') await api.startEvaluation(props.id, !isMock.value);
    else if (kind === 'resume') await api.resumeEvaluation(props.id, !isMock.value);
    else await api.cancelEvaluation(props.id);
    lastReportStatus = ''; report.value = null; await load();
  } catch (e) { error.value = e.message; }
  finally { busy.value = false; }
}
onMounted(() => { load(); timer = setInterval(load, 3000); });
onBeforeUnmount(() => clearInterval(timer));
</script>
<template>
  <div class="space-y-5">
    <p v-if="error" role="alert" class="text-red-300">{{ error }}</p>
    <template v-if="data">
      <div class="flex justify-between gap-4 flex-wrap">
        <div><router-link to="/evaluations" class="text-slate-400">返回实验列表</router-link><h1 class="text-2xl font-bold mt-2">{{ data.run.manifest.experiment_id }}</h1><p class="font-mono text-sm text-slate-400">{{ data.run.id }}</p><p>{{ isMock ? 'Mock · 合成数据，无模型效果结论' : '真实模型实验' }}</p></div>
        <div class="flex gap-2 items-start">
          <button v-if="data.run.status === 'planned'" @click="action('start')" :disabled="busy" class="px-4 py-2 bg-amber-600 rounded">{{ isMock ? '启动 mock' : '确认并启动' }}</button>
          <button v-if="['failed','cancelled'].includes(data.run.status)" @click="action('resume')" :disabled="busy" class="px-4 py-2 bg-amber-600 rounded">恢复</button>
          <button v-if="data.run.status === 'running'" @click="action('cancel')" :disabled="busy" class="px-4 py-2 bg-red-700 rounded">取消</button>
        </div>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-5 gap-3"><div v-for="s in ['planned','running','finished','failed','cancelled']" :key="s" class="border border-slate-700 p-3 rounded"><div class="text-xs uppercase text-slate-400">{{ s }}</div><div class="text-2xl">{{ data.counts[s] }}</div></div></div>
      <p>总体进度 {{ progress }}%</p>
      <div class="overflow-x-auto border border-slate-700 rounded"><table class="w-full text-sm"><thead><tr><th>变体</th><th>种子</th><th>状态</th><th>比赛</th><th>失败原因</th></tr></thead><tbody><tr v-for="task in data.tasks" :key="task.id"><td>{{ task.variant_id }}</td><td>{{ task.seed }}</td><td>{{ task.status }}</td><td><template v-if="task.match_id"><router-link :to="`/match/${task.match_id}`" class="text-sky-400">观战</router-link> · <router-link :to="`/replay/${task.match_id}`" class="text-sky-400">回放</router-link></template></td><td class="text-red-300">{{ task.failure_reason }}</td></tr></tbody></table></div>
      <section v-if="report" class="space-y-4 border border-slate-700 rounded p-4">
        <h2 class="text-xl">可靠性报告</h2>
        <p :class="report.integrity.complete ? 'text-emerald-400' : 'text-red-400'">{{ report.integrity.complete ? '完整性审计通过' : '完整性审计未通过：以下指标不能作为可信实验结论' }}</p>
        <p v-for="issue in report.integrity.issues" :key="issue" class="text-red-300 break-all">{{ issue }}</p>
        <p>正常桌终局 {{ ratio(report.summary.normal_table_completion) }} · 已知成本 ${{ report.summary.known_cost_usd }} · 成本覆盖 {{ ratio(report.summary.cost_coverage) }}</p>
        <div class="flex flex-wrap gap-3"><a v-for="file in files" :key="file" :href="api.evaluationArtifactUrl(id, file)" class="text-sky-400 underline">{{ file }}</a></div>
        <div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr><th>变体 / 阶段</th><th>首次可用</th><th>首次输出合法</th><th>模型成功</th><th>兜底</th><th>调用</th></tr></thead><tbody><tr v-for="m in report.summary.metrics" :key="m.variant + m.phase"><td>{{ m.variant }} / {{ m.phase }}</td><td>{{ ratio(m.first_availability) }}</td><td>{{ ratio(m.first_output_legality) }}</td><td>{{ ratio(m.model_success) }}</td><td>{{ ratio(m.fallback) }}</td><td>{{ m.calls }}</td></tr></tbody></table></div>
        <p class="text-sm text-slate-400">模型成功不含系统兜底；流局单列，零分母为 N/A。自对弈结果不能证明策略间竞技优势。</p>
        <h3 class="text-lg">失败与重试记录</h3>
        <select v-if="report.failures.length" v-model="selectedFailure" aria-label="选择决策记录" class="w-full bg-slate-800 p-2 rounded"><option v-for="f in report.failures" :key="f.decision.decision_id" :value="f.decision.decision_id">{{ f.decision.phase }} · {{ f.decision.resolution }} · {{ f.decision.decision_id }}</option></select>
        <p v-else>没有失败记录。</p>
        <div v-if="failure" class="space-y-3">
          <p>结果：{{ failure.decision.resolution }} · 原因：{{ failure.decision.reason || '—' }} · <router-link :to="`/replay/${failure.decision.match_id}`" class="text-sky-400 underline">查看对应比赛回放</router-link></p>
          <details><summary>观察与实际动作</summary><pre class="trace">{{ JSON.stringify(failure.decision, null, 2) }}</pre></details>
          <details v-for="attempt in failure.attempts" :key="attempt.id"><summary>第 {{ attempt.attempt }} 次生成 · {{ attempt.provider_status }}</summary><h4>原始输出</h4><pre class="trace">{{ attempt.raw_output ?? '无返回输出' }}</pre><h4>用户提示词与反馈</h4><pre class="trace">{{ attempt.user_prompt }}</pre><h4>规则校验</h4><pre class="trace">{{ JSON.stringify(failure.validation.filter(v => v.attempt === attempt.attempt), null, 2) }}</pre></details>
        </div>
      </section>
    </template>
    <p v-else class="text-slate-400">加载中…</p>
  </div>
</template>
<style scoped>
th, td { padding: .7rem; text-align: left; border-bottom: 1px solid #334155; }
.trace { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 24rem; overflow: auto; font-size: .8rem; padding: 1rem; background: #0f172a; }
summary { cursor: pointer; color: #94a3b8; }
</style>
