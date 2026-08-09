<script setup>
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { api } from '../api/index.js';

const route = useRoute();
const player = ref(null);
const error = ref('');
const loading = ref(true);

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

watch(() => route.params.id, loadPlayer, { immediate: true });
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
      <router-link to="/players/leaderboard" class="text-sm text-amber-400 hover:text-amber-300">查看选手榜 &rarr;</router-link>
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
          <h2 class="text-base font-semibold text-white">已进化的长期记忆</h2>
          <span class="text-xs text-slate-500">每场比赛总结后更新，并注入后续提示词</span>
        </div>
        <pre class="max-h-72 overflow-auto whitespace-pre-wrap border-l-2 border-amber-500/70 bg-amber-500/5 px-4 py-3 text-sm leading-6 text-slate-300">{{ player.long_term_memory || '尚无比赛总结。完成一场比赛后，这里将显示该选手积累的策略经验。' }}</pre>
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
