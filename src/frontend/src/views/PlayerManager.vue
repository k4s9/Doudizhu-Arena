<script setup>
import { ref, onMounted, computed } from 'vue';
import { api } from '../api/index.js';

const players = ref([]);
const configs = ref([]);
const error = ref('');
const creating = ref(false);
const deleting = ref(null);

const newPlayer = ref({
  config_name: '',
  display_name: '',
});
const configDropdownOpen = ref(false);

// Filter configs based on what user types
const configFilter = ref('');

const filteredConfigs = computed(() => {
  if (!configFilter.value) return configs.value;
  const q = configFilter.value.toLowerCase();
  return configs.value.filter(c =>
    c.name.toLowerCase().includes(q) ||
    c.provider.toLowerCase().includes(q) ||
    c.model.toLowerCase().includes(q)
  );
});

function openConfigDropdown() {
  if (!newPlayer.value.config_name) {
    configFilter.value = '';
  }
  configDropdownOpen.value = true;
}

function selectConfig(name) {
  newPlayer.value.config_name = name;
  configFilter.value = name;
  configDropdownOpen.value = false;
}

function winRate(statistics) {
  if (statistics?.win_rate == null) return '—';
  return `${statistics.win_rate}%`;
}

async function loadAll() {
  try {
    const [playersData, configsData] = await Promise.all([
      api.getPlayerLeaderboard(),
      api.listConfigs(),
    ]);
    players.value = playersData.players;
    configs.value = configsData.configs;
  } catch (e) {
    error.value = e.message;
  }
}

async function createPlayer() {
  if (!newPlayer.value.config_name || !newPlayer.value.display_name.trim()) return;
  // Validate config exists
  const valid = configs.value.some(c => c.name === newPlayer.value.config_name);
  if (!valid) {
    error.value = '所选配置不存在。';
    return;
  }
  creating.value = true;
  error.value = '';
  try {
    await api.createPlayer({
      config_name: newPlayer.value.config_name,
      display_name: newPlayer.value.display_name.trim(),
    });
    newPlayer.value.display_name = '';
    newPlayer.value.config_name = '';
    configFilter.value = '';
    configDropdownOpen.value = false;
    await loadAll();
  } catch (e) {
    error.value = e.message;
  } finally {
    creating.value = false;
  }
}

async function deletePlayer(id) {
  if (!confirm('确定要删除该玩家吗？此操作不可撤销。')) return;
  deleting.value = id;
  error.value = '';
  try {
    await api.deletePlayer(id);
    await loadAll();
  } catch (e) {
    error.value = e.message;
  } finally {
    deleting.value = null;
  }
}

onMounted(async () => {
  await api.reloadAgents();
  await loadAll();
});
</script>

<template>
  <div class="max-w-4xl mx-auto">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-white">选手管理</h1>
      <div class="flex items-center gap-4 text-sm">
        <router-link to="/players/leaderboard" class="text-amber-400 hover:text-amber-300">
          胜率与积分榜 &rarr;
        </router-link>
        <router-link to="/configs" class="text-slate-400 hover:text-slate-200">
          管理配置 &rarr;
        </router-link>
      </div>
    </div>

    <div v-if="error" class="bg-red-900/30 border border-red-700 rounded-lg p-3 mb-4 text-sm text-red-300">
      {{ error }}
    </div>

    <!-- Quick create -->
    <div class="mb-6 bg-slate-800/50 border border-slate-700 rounded-lg p-3">
      <div class="text-sm text-slate-300 mb-2">创建新玩家</div>
      <div class="flex gap-2">
        <!-- Config name dropdown with search -->
        <div class="flex-1 relative">
          <input
            v-model="configFilter"
            @focus="openConfigDropdown"
            @blur="setTimeout(() => configDropdownOpen = false, 150)"
            class="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white placeholder-slate-500"
            :class="newPlayer.config_name ? 'border-green-600' : ''"
            placeholder="搜索配置..."
            autocomplete="off"
          />
          <!-- Dropdown: only visible when input is focused and selection not finalized -->
          <div
            v-if="configDropdownOpen && newPlayer.config_name !== configFilter"
            class="absolute z-10 mt-1 w-full bg-slate-700 border border-slate-600 rounded shadow-lg max-h-48 overflow-y-auto"
          >
            <div v-if="filteredConfigs.length === 0" class="px-2 py-1.5 text-sm text-slate-500">
              无匹配配置
            </div>
            <div
              v-for="c in filteredConfigs"
              :key="c.id"
              @mousedown.prevent="selectConfig(c.name)"
              class="px-2 py-1.5 text-sm text-slate-300 hover:bg-slate-600 hover:text-white cursor-pointer flex justify-between"
            >
              <span>{{ c.name }}</span>
              <span class="text-xs text-slate-500">{{ c.provider }}/{{ c.model }}</span>
            </div>
          </div>
        </div>

        <input
          v-model="newPlayer.display_name"
          class="flex-1 bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white placeholder-slate-500"
          placeholder="玩家显示名称"
        />
        <button
          @click="createPlayer"
          :disabled="!newPlayer.config_name || !newPlayer.display_name.trim() || creating"
          class="px-4 py-1.5 text-sm bg-amber-600 hover:bg-amber-500 disabled:bg-slate-600 disabled:text-slate-400 rounded text-white font-medium"
        >
          {{ creating ? '...' : '创建' }}
        </button>
      </div>
    </div>

    <!-- Players table -->
    <div class="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-700 text-slate-400">
            <th class="text-left px-4 py-2 font-medium">显示名称</th>
            <th class="text-left px-4 py-2 font-medium">配置</th>
            <th class="text-left px-4 py-2 font-medium">提供商/模型</th>
            <th class="text-center px-4 py-2 font-medium">已赛副数</th>
            <th class="text-center px-4 py-2 font-medium">胜场</th>
            <th class="text-center px-4 py-2 font-medium">胜率</th>
            <th class="text-center px-4 py-2 font-medium">累计积分</th>
            <th class="text-right px-4 py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in players"
            :key="p.id"
            class="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors"
          >
            <td class="px-4 py-2">
              <router-link :to="`/players/${p.id}`" class="text-white hover:text-amber-300 transition-colors">
                {{ p.display_name }}
              </router-link>
            </td>
            <td class="px-4 py-2 text-slate-400">{{ p.config_name }}</td>
            <td class="px-4 py-2 text-slate-400">{{ p.provider }}/{{ p.model }}</td>
            <td class="px-4 py-2 text-center text-slate-300">{{ p.statistics.hands_played }}</td>
            <td class="px-4 py-2 text-center text-slate-300">{{ p.statistics.wins }}</td>
            <td class="px-4 py-2 text-center" :class="p.statistics.win_rate >= 50 ? 'text-green-400' : p.statistics.win_rate != null ? 'text-red-400' : 'text-slate-500'">
              {{ winRate(p.statistics) }}
            </td>
            <td class="px-4 py-2 text-center font-medium" :class="p.statistics.score_total > 0 ? 'text-green-400' : p.statistics.score_total < 0 ? 'text-red-400' : 'text-slate-400'">
              {{ p.statistics.score_total > 0 ? '+' : '' }}{{ p.statistics.score_total }}
            </td>
            <td class="px-4 py-2 text-right">
              <router-link :to="`/players/${p.id}`" class="mr-3 text-xs text-amber-400 hover:text-amber-300">详情</router-link>
              <button
                @click="deletePlayer(p.id)"
                :disabled="deleting === p.id"
                class="text-xs text-red-400 hover:text-red-300 disabled:text-slate-600"
              >
                {{ deleting === p.id ? '...' : '删除' }}
              </button>
            </td>
          </tr>
          <tr v-if="players.length === 0">
            <td colspan="8" class="px-4 py-8 text-center text-slate-500">
              暂无选手。请在上方创建或在 agents.yaml 中配置 default_players。
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
