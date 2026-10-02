<script setup>
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { api } from '../api/index.js';

const route = useRoute();
const player = ref(null);
const error = ref('');
const loading = ref(true);
const editingMemory = ref(false);
const memoryDraft = ref('');
const memoryBeforeEdit = ref('');
const savingMemory = ref(false);
const memoryError = ref('');
const memoryLength = computed(() => Array.from(memoryDraft.value.trim()).length);

const statistics = computed(() => player.value?.statistics || {
  hands_played: 0,
  wins: 0,
  losses: 0,
  win_rate: null,
  landlord: { hands: 0, wins: 0, losses: 0, win_rate: null },
  farmer: { hands: 0, wins: 0, losses: 0, win_rate: null },
  idle_hands: 0,
  void_hands: 0,
  score_total: 0,
  score_history: [],
});

const latestHands = computed(() => [...statistics.value.score_history].reverse());
const memoryVersions = computed(() => player.value?.memory_versions || []);
const pendingMemoryFailures = computed(() => (player.value?.memory_failures || []).filter(f => !f.recovered));

function previousMemory(version) {
  return memoryVersions.value.find(item => item.id === version.previous_id);
}

function learningFailureLabel(failure) {
  if (failure.stage === 'persistence') return '经验保存失败，仍保留之前的有效记忆';
  return failure.phase === 'reflection' ? '本副复盘未完成' : '赛后经验总结未完成';
}

function editMemory() {
  memoryDraft.value = player.value.long_term_memory || '';
  memoryBeforeEdit.value = memoryDraft.value;
  memoryError.value = '';
  editingMemory.value = true;
}

async function saveMemory() {
  if (savingMemory.value || memoryLength.value === 0 || memoryLength.value > 8000) return;
  savingMemory.value = true;
  memoryError.value = '';
  try {
    await api.updatePlayer(player.value.id, {
      long_term_memory: memoryDraft.value.trim(),
      expected_long_term_memory: memoryBeforeEdit.value,
    });
    editingMemory.value = false;
    await loadPlayer();
  } catch (e) {
    memoryError.value = e.message;
  } finally {
    savingMemory.value = false;
  }
}

function formatRate(value) {
  return value == null ? '—' : `${value}%`;
}

function formatScore(value) {
  return `${value > 0 ? '+' : ''}${value}`;
}

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function roleLabel(role) {
  return { landlord: '地主', farmer: '农民', idle: '闲家', void: '流局' }[role] || role;
}

function outcomeLabel(outcome) {
  return { win: '胜', loss: '负', idle: '闲置', void: '流局' }[outcome] || '未计入';
}

async function loadPlayer() {
  loading.value = true;
  error.value = '';
  try {
    player.value = await api.getPlayer(route.params.id);
  } catch (e) {
    error.value = e.message;
    player.value = null;
  } finally {
    loading.value = false;
  }
}

watch(() => route.params.id, () => {
  editingMemory.value = false;
  memoryError.value = '';
  loadPlayer();
}, { immediate: true });
</script>

<template>
  <div class="max-w-6xl mx-auto">
    <div class="flex items-start justify-between gap-4 mb-6">
      <div>
        <router-link to="/players" class="text-sm text-slate-400 hover:text-white">&larr; 返回选手管理</router-link>
        <h1 class="mt-2 text-2xl font-bold text-white">{{ player?.display_name || '选手详情' }}</h1>
        <p v-if="player" class="mt-1 text-sm text-slate-400">
          {{ player.config_name }} · {{ player.provider }}/{{ player.model }}
        </p>
      </div>
      <div class="flex items-center gap-4">
        <button type="button" :disabled="loading || savingMemory" class="text-sm text-slate-400 hover:text-white disabled:opacity-50" @click="loadPlayer">刷新</button>
        <router-link to="/players/leaderboard" class="text-sm text-amber-400 hover:text-amber-300">查看选手榜 &rarr;</router-link>
      </div>
    </div>

    <div v-if="loading" class="py-16 text-center text-slate-500">加载选手数据...</div>
    <div v-else-if="error" class="border border-red-700 bg-red-900/20 px-4 py-3 text-sm text-red-300">{{ error }}</div>

    <template v-else-if="player">
      <section class="border-y border-slate-800 py-5">
        <div class="grid grid-cols-2 gap-px overflow-hidden border border-slate-800 bg-slate-800 sm:grid-cols-4">
          <div class="bg-slate-950 px-4 py-3">
            <div class="text-xs text-slate-500">总体胜率</div>
            <div class="mt-1 text-2xl font-semibold" :class="statistics.win_rate >= 50 ? 'text-emerald-400' : statistics.win_rate != null ? 'text-rose-400' : 'text-slate-400'">{{ formatRate(statistics.win_rate) }}</div>
            <div class="mt-1 text-xs text-slate-500">{{ statistics.wins }} 胜 {{ statistics.losses }} 负</div>
          </div>
          <div class="bg-slate-950 px-4 py-3">
            <div class="text-xs text-slate-500">地主胜率</div>
            <div class="mt-1 text-2xl font-semibold text-amber-400">{{ formatRate(statistics.landlord.win_rate) }}</div>
            <div class="mt-1 text-xs text-slate-500">{{ statistics.landlord.wins }}/{{ statistics.landlord.hands }} 胜</div>
          </div>
          <div class="bg-slate-950 px-4 py-3">
            <div class="text-xs text-slate-500">农民胜率</div>
            <div class="mt-1 text-2xl font-semibold text-sky-400">{{ formatRate(statistics.farmer.win_rate) }}</div>
            <div class="mt-1 text-xs text-slate-500">{{ statistics.farmer.wins }}/{{ statistics.farmer.hands }} 胜</div>
          </div>
          <div class="bg-slate-950 px-4 py-3">
            <div class="text-xs text-slate-500">累计积分</div>
            <div class="mt-1 text-2xl font-semibold" :class="statistics.score_total > 0 ? 'text-emerald-400' : statistics.score_total < 0 ? 'text-rose-400' : 'text-slate-300'">{{ formatScore(statistics.score_total) }}</div>
            <div class="mt-1 text-xs text-slate-500">已赛 {{ statistics.hands_played }} 副</div>
          </div>
        </div>
        <div class="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
          <span>闲家 {{ statistics.idle_hands }} 副</span>
          <span>流局 {{ statistics.void_hands }} 副</span>
          <span>已结算 {{ statistics.total_hands }} 副</span>
        </div>
      </section>

      <section class="border-b border-slate-800 py-6">
        <div class="mb-3 flex items-baseline justify-between gap-3">
          <h2 class="text-base font-semibold text-white">策略 Prompt</h2>
          <span class="text-xs text-slate-500">配置基础策略</span>
        </div>
        <pre class="max-h-72 overflow-auto whitespace-pre-wrap border-l-2 border-slate-700 bg-slate-900/50 px-4 py-3 text-sm leading-6 text-slate-300">{{ player.system_prompt || '未设置自定义基础 Prompt。' }}</pre>
      </section>

      <section class="border-b border-slate-800 py-6">
        <div class="mb-3 flex items-baseline justify-between gap-3">
          <h2 class="text-base font-semibold text-white">长期策略记忆</h2>
          <span class="text-xs text-slate-500">每场比赛总结后更新，并注入后续提示词</span>
        </div>
        <pre class="max-h-72 overflow-auto whitespace-pre-wrap border-l-2 border-amber-500/70 bg-amber-500/5 px-4 py-3 text-sm leading-6 text-slate-300">{{ player.long_term_memory || '尚无比赛总结。完成一场比赛后，这里将显示该选手积累的策略经验。' }}</pre>
        <button v-if="!editingMemory" type="button" class="mt-3 text-sm text-amber-400 hover:text-amber-300" @click="editMemory">整理长期记忆</button>
        <form v-else class="mt-4 space-y-3" @submit.prevent="saveMemory">
          <label for="memory-draft" class="block text-sm text-slate-300">整理后的长期记忆</label>
          <p id="memory-help" class="text-xs text-slate-500">保存为新版本，保留修改前的内容。正在更新经验的比赛结束后才能保存。</p>
          <textarea id="memory-draft" v-model="memoryDraft" :disabled="savingMemory" aria-describedby="memory-help memory-length" rows="8" class="w-full rounded border border-slate-700 bg-slate-900 p-3 text-sm text-slate-200" />
          <p id="memory-length" class="text-xs" :class="memoryLength > 8000 ? 'text-rose-400' : 'text-slate-500'">{{ memoryLength }} / 8000 字符</p>
          <p v-if="memoryError" role="alert" class="text-sm text-rose-400">{{ memoryError }}</p>
          <div class="flex gap-4">
            <button type="submit" :disabled="savingMemory || !memoryLength || memoryLength > 8000" class="text-sm text-amber-400 disabled:opacity-40">{{ savingMemory ? '保存中…' : '保存新版本' }}</button>
            <button type="button" :disabled="savingMemory" class="text-sm text-slate-400" @click="editingMemory = false">取消</button>
          </div>
        </form>
        <div v-if="pendingMemoryFailures.length" role="status" class="mt-4 border-l-2 border-rose-500 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
          <p class="font-medium">有 {{ pendingMemoryFailures.length }} 次经验更新未完成</p>
          <p class="mt-1 text-xs">比赛结果已独立保存。可查看来源对局；后续成功更新会保留失败记录并标记恢复。</p>
          <ul class="mt-2 space-y-1">
            <li v-for="failure in pendingMemoryFailures" :key="failure.id">
              {{ learningFailureLabel(failure) }} · {{ formatDate(failure.created_at) }}
              <router-link :to="`/match/${failure.match_id}`" class="ml-2 underline">查看比赛</router-link>
            </li>
          </ul>
        </div>
        <div v-if="memoryVersions.length" class="mt-4 space-y-3">
          <h3 class="text-sm font-medium text-slate-300">经验版本与来源</h3>
          <details v-for="version in memoryVersions" :key="version.id" class="border-l border-slate-700 pl-4">
            <summary class="cursor-pointer text-sm text-slate-300">版本 {{ version.revision }} · {{ formatDate(version.created_at) }}</summary>
            <router-link v-if="version.match_id" :to="`/replay/${version.match_id}`" class="mt-2 inline-block text-xs text-amber-400 underline">查看本次更新来源对局</router-link>
            <p v-else class="mt-2 text-xs text-slate-500">{{ version.source?.kind === 'manual_update' ? '手工整理的策略记忆' : '比赛外保存的策略快照' }}</p>
            <p class="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-300">{{ version.content || '（无记忆）' }}</p>
            <details v-if="previousMemory(version)" class="mt-2 text-xs text-slate-500">
              <summary class="cursor-pointer">对照更新前的版本 {{ previousMemory(version).revision }}</summary>
              <p class="mt-2 whitespace-pre-wrap leading-6">{{ previousMemory(version).content || '（无记忆）' }}</p>
            </details>
          </details>
        </div>
        <details v-if="player.memory_usage?.length" class="mt-4 border-t border-slate-800 pt-3">
          <summary class="cursor-pointer text-sm text-slate-400">查看后续比赛使用的经验版本</summary>
          <ul class="mt-3 space-y-2 text-xs text-slate-400">
            <li v-for="usage in player.memory_usage" :key="usage.match_id">
              {{ formatDate(usage.created_at) }} · 使用版本 {{ usage.revision }}
              <router-link :to="`/match/${usage.match_id}`" class="ml-2 text-amber-400 underline">查看比赛</router-link>
            </li>
          </ul>
        </details>
        <details v-if="player.long_term_memory_history?.length" class="mt-4 border-t border-slate-800 pt-3">
          <summary class="cursor-pointer text-sm text-slate-400 hover:text-white">查看 {{ player.long_term_memory_history.length }} 条历史总结版本</summary>
          <div class="mt-3 space-y-3">
            <article v-for="memory in [...player.long_term_memory_history].reverse()" :key="`${memory.match_id}-${memory.created_at}`" class="border-l border-slate-700 pl-4">
              <div class="text-xs text-slate-500">{{ formatDate(memory.created_at) }} · 比赛 {{ memory.match_id || '—' }}</div>
              <p class="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-300">{{ memory.content }}</p>
            </article>
          </div>
        </details>
      </section>

      <section class="py-6">
        <div class="mb-3 flex items-baseline justify-between gap-3">
          <div>
            <h2 class="text-base font-semibold text-white">逐副牌积分</h2>
            <p class="mt-1 text-xs text-slate-500">积分使用比赛已结算的红/蓝差分分；胜率仅统计实际担任地主或农民的非流局对局。</p>
          </div>
          <span class="shrink-0 text-xs text-slate-500">最新在前</span>
        </div>
        <div class="overflow-x-auto border border-slate-800">
          <table class="w-full min-w-[720px] text-sm">
            <thead class="border-b border-slate-800 bg-slate-900/70 text-xs text-slate-400">
              <tr>
                <th class="px-4 py-2 text-left font-medium">比赛 / 副数</th>
                <th class="px-4 py-2 text-left font-medium">桌次 / 席位</th>
                <th class="px-4 py-2 text-center font-medium">角色</th>
                <th class="px-4 py-2 text-center font-medium">结果</th>
                <th class="px-4 py-2 text-right font-medium">本副积分</th>
                <th class="px-4 py-2 text-right font-medium">累计积分</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="hand in latestHands" :key="`${hand.match_id}-${hand.hand_num}-${hand.table}`" class="border-b border-slate-800/70 last:border-0">
                <td class="px-4 py-3 text-slate-300">{{ hand.match_name }}<span class="ml-2 text-slate-500">第 {{ hand.hand_num }} 副</span></td>
                <td class="px-4 py-3 text-slate-400">{{ hand.table }}桌 · {{ hand.seat }}位</td>
                <td class="px-4 py-3 text-center text-slate-300">{{ roleLabel(hand.role) }}</td>
                <td class="px-4 py-3 text-center" :class="hand.outcome === 'win' ? 'text-emerald-400' : hand.outcome === 'loss' ? 'text-rose-400' : 'text-slate-500'">{{ outcomeLabel(hand.outcome) }}</td>
                <td class="px-4 py-3 text-right font-medium" :class="hand.score_delta > 0 ? 'text-emerald-400' : hand.score_delta < 0 ? 'text-rose-400' : 'text-slate-400'">{{ formatScore(hand.score_delta) }}</td>
                <td class="px-4 py-3 text-right text-white">{{ formatScore(hand.cumulative_score) }}</td>
              </tr>
              <tr v-if="latestHands.length === 0">
                <td colspan="6" class="px-4 py-10 text-center text-slate-500">尚无已结算的逐副牌数据。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
