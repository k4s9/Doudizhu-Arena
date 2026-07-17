<script setup>
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../api/index.js';

const router = useRouter();
const players = ref([]);
const configs = ref([]);
const loading = ref(false);
const error = ref(null);
const creatingPlayer = ref(false);
const newPlayerName = ref('');
const newPlayerConfig = ref('');

const form = ref({
  name: '',
  total_hands: 20,
  ko_enabled: true,
  timeout_bidding_seconds: 60,
  timeout_individual_seconds: 360,
  timeout_team_seconds: 3600,
  team_red: { name: '红队', agents: [] },
  team_blue: { name: '蓝队', agents: [] },
});

function winRate(p) {
  if (!p.matches_played) return '—';
  return Math.round((p.matches_won / p.matches_played) * 100) + '%';
}

// Group players by config
const playersByConfig = computed(() => {
  const map = {};
  for (const p of players.value) {
    const key = p.config_name || p.config_id || 'Unknown';
    if (!map[key]) map[key] = [];
    map[key].push(p);
  }
  return map;
});

onMounted(async () => {
  try {
    await api.reloadAgents();
    const [playersData, configsData] = await Promise.all([
      api.listPlayers(),
      api.listConfigs(),
    ]);
    players.value = playersData.players;
    configs.value = configsData.configs;
  } catch (e) {
    error.value = '加载玩家失败: ' + e.message;
  }
});

function toggleAgent(team, playerId) {
  const list = form.value[team].agents;
  if (list.includes(playerId)) {
    form.value[team].agents = list.filter(a => a !== playerId);
  } else {
    if (list.length < 4) {
      form.value[team].agents = [...list, playerId];
    }
  }
}

function canSubmit() {
  return (
    form.value.name &&
    form.value.team_red.agents.length === 4 &&
    form.value.team_blue.agents.length === 4
  );
}

async function createPlayer() {
  if (!newPlayerConfig.value || !newPlayerName.value.trim()) return;
  // Validate config exists
  const valid = configs.value.some(c => c.name === newPlayerConfig.value);
  if (!valid) {
    error.value = '所选配置不存在。';
    return;
  }
  creatingPlayer.value = true;
  error.value = '';
  try {
    await api.createPlayer({
      config_name: newPlayerConfig.value,
      display_name: newPlayerName.value.trim(),
    });
    const data = await api.listPlayers();
    players.value = data.players;
    newPlayerName.value = '';
    newPlayerConfig.value = '';
  } catch (e) {
    error.value = '创建玩家失败: ' + e.message;
  } finally {
    creatingPlayer.value = false;
  }
}

async function submit() {
  if (!canSubmit()) return;
  loading.value = true;
  error.value = null;
  try {
    // Wrap config fields as expected by the backend
    const payload = {
      name: form.value.name,
      team_red: form.value.team_red,
      team_blue: form.value.team_blue,
      config: {
        total_hands: form.value.total_hands,
        ko_enabled: form.value.ko_enabled,
        timeout_bidding_seconds: form.value.timeout_bidding_seconds,
        timeout_individual_seconds: form.value.timeout_individual_seconds,
        timeout_team_seconds: form.value.timeout_team_seconds,
      },
    };
    const match = await api.createMatch(payload);
    router.push(`/match/${match.id}`);
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="max-w-3xl mx-auto">
    <h1 class="text-2xl font-bold text-white mb-6">新建比赛</h1>

    <div v-if="error" class="bg-red-900/30 border border-red-700 rounded-lg p-3 mb-4 text-sm text-red-300">
      {{ error }}
    </div>

    <!-- Match name -->
    <div class="mb-4">
      <label class="block text-sm text-slate-400 mb-1">比赛名称</label>
      <input
        v-model="form.name"
        class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:border-amber-500 focus:outline-none"
        placeholder="例如: Qwen vs Minimax 对决"
      />
    </div>

    <!-- Config -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="block text-sm text-slate-400 mb-1">总副数</label>
        <input v-model.number="form.total_hands" type="number" min="1" max="100"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm" />
      </div>
      <div class="flex items-end pb-2">
        <label class="flex items-center gap-2 text-sm text-slate-300">
          <input v-model="form.ko_enabled" type="checkbox" class="rounded" />
          启用 KO</label>
      </div>
    </div>

    <!-- Create new player -->
    <details class="mb-4 bg-slate-800/50 rounded-lg border border-slate-700 p-3">
      <summary class="text-sm text-slate-300 cursor-pointer hover:text-white">
        创建新玩家
      </summary>
      <div class="mt-2 flex gap-2">
        <select v-model="newPlayerConfig" class="flex-1 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
          <option value="">— 选择配置 —</option>
          <option v-for="c in configs" :key="c.id" :value="c.name">
            {{ c.name }} ({{ c.provider }}/{{ c.model }})
          </option>
        </select>
        <input
          v-model="newPlayerName"
          class="flex-1 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white placeholder-slate-500"
          placeholder="玩家显示名称"
        />
        <button
          @click="createPlayer"
          :disabled="!newPlayerConfig || !newPlayerName.trim() || creatingPlayer"
          class="px-3 py-1 text-sm bg-amber-600 hover:bg-amber-500 disabled:bg-slate-600 disabled:text-slate-400 rounded text-white"
        >
          {{ creatingPlayer ? '...' : '创建' }}
        </button>
      </div>
    </details>

    <!-- Player Selection -->
    <div class="grid grid-cols-2 gap-6 mt-4">
      <!-- Red Team -->
      <div>
        <h3 class="text-sm font-semibold text-red-400 mb-2">
          红队
          <span class="text-slate-500 font-normal">({{ form.team_red.agents.length }}/4)</span>
        </h3>
        <div v-for="(groupPlayers, configName) in playersByConfig" :key="configName" class="mb-3">
          <div class="text-xs text-slate-500 mb-1 px-1">{{ configName }}</div>
          <div class="space-y-0.5">
            <div
              v-for="p in groupPlayers"
              :key="p.id"
              @click="toggleAgent('team_red', p.id)"
              :class="[
                'p-2 rounded border cursor-pointer text-sm transition-colors flex justify-between items-center',
                form.team_red.agents.includes(p.id)
                  ? 'border-red-500 bg-red-500/10 text-white'
                  : 'border-slate-700 bg-slate-800 text-slate-400 hover:border-slate-600',
              ]"
            >
              <span>{{ p.display_name }}</span>
              <span class="text-xs text-slate-500">{{ winRate(p) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Blue Team -->
      <div>
        <h3 class="text-sm font-semibold text-blue-400 mb-2">
          蓝队
          <span class="text-slate-500 font-normal">({{ form.team_blue.agents.length }}/4)</span>
        </h3>
        <div v-for="(groupPlayers, configName) in playersByConfig" :key="configName" class="mb-3">
          <div class="text-xs text-slate-500 mb-1 px-1">{{ configName }}</div>
          <div class="space-y-0.5">
            <div
              v-for="p in groupPlayers"
              :key="p.id"
              @click="toggleAgent('team_blue', p.id)"
              :class="[
                'p-2 rounded border cursor-pointer text-sm transition-colors flex justify-between items-center',
                form.team_blue.agents.includes(p.id)
                  ? 'border-blue-500 bg-blue-500/10 text-white'
                  : 'border-slate-700 bg-slate-800 text-slate-400 hover:border-slate-600',
              ]"
            >
              <span>{{ p.display_name }}</span>
              <span class="text-xs text-slate-500">{{ winRate(p) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="players.length === 0" class="text-sm text-slate-500 italic text-center mt-4">
      没有可用玩家。请先创建玩家或配置 agents.yaml。
    </div>

    <button
      @click="submit"
      :disabled="!canSubmit() || loading"
      class="mt-6 w-full py-3 rounded-lg text-white font-medium transition-colors"
      :class="canSubmit() && !loading ? 'bg-amber-600 hover:bg-amber-500' : 'bg-slate-700 text-slate-500 cursor-not-allowed'"
    >
      {{ loading ? '创建中...' : '创建比赛' }}
    </button>
  </div>
</template>
