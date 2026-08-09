<script setup>
import { onMounted, ref, watch } from 'vue';
import { api } from '../api/index.js';

const mode = ref('win_rate');
const players = ref([]);
const error = ref('');
const loading = ref(true);

function formatRate(value) {
  return value == null ? '—' : `${value}%`;
}

function formatScore(value) {
  return `${value > 0 ? '+' : ''}${value}`;
}

async function loadLeaderboard() {
  loading.value = true;
  error.value = '';
  try {
    const data = await api.getPlayerLeaderboard(mode.value);
    players.value = data.players;
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

watch(mode, loadLeaderboard);
onMounted(loadLeaderboard);
</script>

<template>
  <div class="max-w-6xl mx-auto">
    <div class="flex items-start justify-between gap-4 mb-6">
      <div>
        <router-link to="/players" class="text-sm text-slate-400 hover:text-white">&larr; 返回选手管理</router-link>
        <h1 class="mt-2 text-2xl font-bold text-white">选手榜</h1>
        <p class="mt-1 text-sm text-slate-400">按已结算的单副牌数据实时统计</p>
      </div>
      <div class="flex overflow-hidden border border-slate-700 text-sm" role="tablist" aria-label="排行榜排序方式">
        <button type="button" role="tab" :aria-selected="mode === 'win_rate'" @click="mode = 'win_rate'" class="min-w-20 px-3 py-2 transition-colors" :class="mode === 'win_rate' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'">胜率榜</button>
        <button type="button" role="tab" :aria-selected="mode === 'score'" @click="mode = 'score'" class="min-w-20 border-l border-slate-700 px-3 py-2 transition-colors" :class="mode === 'score' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'">积分榜</button>
      </div>
    </div>

    <div v-if="error" class="border border-red-700 bg-red-900/20 px-4 py-3 text-sm text-red-300">{{ error }}</div>
    <div v-else-if="loading" class="py-16 text-center text-slate-500">加载排行榜...</div>
    <div v-else class="overflow-x-auto border border-slate-800">
      <table class="w-full min-w-[780px] text-sm">
        <thead class="border-b border-slate-800 bg-slate-900/70 text-xs text-slate-400">
          <tr>
            <th class="w-16 px-4 py-3 text-center font-medium">排名</th>
            <th class="px-4 py-3 text-left font-medium">选手</th>
            <th class="px-4 py-3 text-left font-medium">配置 / 模型</th>
            <th class="px-4 py-3 text-center font-medium">总体胜率</th>
            <th class="px-4 py-3 text-center font-medium">地主胜率</th>
            <th class="px-4 py-3 text-center font-medium">农民胜率</th>
            <th class="px-4 py-3 text-center font-medium">已赛副数</th>
            <th class="px-4 py-3 text-right font-medium">累计积分</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(player, index) in players" :key="player.id" class="border-b border-slate-800/70 last:border-0 hover:bg-slate-900/50">
            <td class="px-4 py-3 text-center font-semibold" :class="index < 3 ? 'text-amber-400' : 'text-slate-500'">{{ index + 1 }}</td>
            <td class="px-4 py-3"><router-link :to="`/players/${player.id}`" class="font-medium text-white hover:text-amber-300">{{ player.display_name }}</router-link></td>
            <td class="px-4 py-3 text-slate-400">{{ player.config_name }} <span class="text-slate-600">·</span> {{ player.provider }}/{{ player.model }}</td>
            <td class="px-4 py-3 text-center" :class="player.statistics.win_rate >= 50 ? 'text-emerald-400' : player.statistics.win_rate != null ? 'text-rose-400' : 'text-slate-500'">{{ formatRate(player.statistics.win_rate) }}</td>
            <td class="px-4 py-3 text-center text-amber-400">{{ formatRate(player.statistics.landlord.win_rate) }}</td>
            <td class="px-4 py-3 text-center text-sky-400">{{ formatRate(player.statistics.farmer.win_rate) }}</td>
            <td class="px-4 py-3 text-center text-slate-300">{{ player.statistics.hands_played }}</td>
            <td class="px-4 py-3 text-right font-medium" :class="player.statistics.score_total > 0 ? 'text-emerald-400' : player.statistics.score_total < 0 ? 'text-rose-400' : 'text-slate-400'">{{ formatScore(player.statistics.score_total) }}</td>
          </tr>
          <tr v-if="players.length === 0">
            <td colspan="8" class="px-4 py-10 text-center text-slate-500">暂无选手数据。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
