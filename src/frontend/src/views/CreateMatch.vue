<script setup>
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../api/index.js';

const router = useRouter();
const agents = ref([]);
const loading = ref(false);
const error = ref(null);

const form = ref({
  name: '',
  total_hands: 20,
  ko_enabled: true,
  timeout_bidding_seconds: 60,
  timeout_individual_seconds: 360,
  timeout_team_seconds: 3600,
  team_red: { name: 'Red Team', agents: [] },
  team_blue: { name: 'Blue Team', agents: [] },
});

onMounted(async () => {
  try {
    const data = await api.listAgents();
    agents.value = data.agents;
  } catch (e) {
    error.value = 'Failed to load agents: ' + e.message;
  }
});

function toggleAgent(team, agentId) {
  const list = form.value[team].agents;
  if (list.includes(agentId)) {
    form.value[team].agents = list.filter(a => a !== agentId);
  } else {
    if (list.length < 4) {
      form.value[team].agents = [...list, agentId];
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

async function submit() {
  if (!canSubmit()) return;
  loading.value = true;
  error.value = null;
  try {
    const match = await api.createMatch(form.value);
    router.push(`/match/${match.id}`);
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="max-w-2xl mx-auto">
    <h1 class="text-2xl font-bold text-white mb-6">Create New Match</h1>

    <div v-if="error" class="bg-red-900/30 border border-red-700 rounded-lg p-3 mb-4 text-sm text-red-300">
      {{ error }}
    </div>

    <!-- Match name -->
    <div class="mb-4">
      <label class="block text-sm text-slate-400 mb-1">Match Name</label>
      <input
        v-model="form.name"
        class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:border-amber-500 focus:outline-none"
        placeholder="e.g. Claude vs GPT Showdown"
      />
    </div>

    <!-- Config -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="block text-sm text-slate-400 mb-1">Total Hands</label>
        <input v-model.number="form.total_hands" type="number" min="1" max="100"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm" />
      </div>
      <div class="flex items-end pb-2">
        <label class="flex items-center gap-2 text-sm text-slate-300">
          <input v-model="form.ko_enabled" type="checkbox" class="rounded" />
          Enable KO
        </label>
      </div>
    </div>

    <!-- Agent Selection -->
    <div class="grid grid-cols-2 gap-6 mt-6">
      <!-- Red Team -->
      <div>
        <h3 class="text-sm font-semibold text-red-400 mb-2">Red Team (4 agents)</h3>
        <div class="space-y-1">
          <div
            v-for="a in agents"
            :key="a.id"
            @click="toggleAgent('team_red', a.id)"
            :class="[
              'p-2 rounded border cursor-pointer text-sm transition-colors',
              form.team_red.agents.includes(a.id)
                ? 'border-red-500 bg-red-500/10 text-white'
                : 'border-slate-700 bg-slate-800 text-slate-400 hover:border-slate-600',
            ]"
          >
            {{ a.name }} ({{ a.provider }}/{{ a.model }})
          </div>
        </div>
        <div v-if="agents.length === 0" class="text-xs text-slate-500 italic">
          No agents available. Add agents via API or agents.yaml.
        </div>
      </div>

      <!-- Blue Team -->
      <div>
        <h3 class="text-sm font-semibold text-blue-400 mb-2">Blue Team (4 agents)</h3>
        <div class="space-y-1">
          <div
            v-for="a in agents"
            :key="a.id"
            @click="toggleAgent('team_blue', a.id)"
            :class="[
              'p-2 rounded border cursor-pointer text-sm transition-colors',
              form.team_blue.agents.includes(a.id)
                ? 'border-blue-500 bg-blue-500/10 text-white'
                : 'border-slate-700 bg-slate-800 text-slate-400 hover:border-slate-600',
            ]"
          >
            {{ a.name }} ({{ a.provider }}/{{ a.model }})
          </div>
        </div>
      </div>
    </div>

    <button
      @click="submit"
      :disabled="!canSubmit() || loading"
      class="mt-6 w-full py-3 rounded-lg text-white font-medium transition-colors"
      :class="canSubmit() && !loading ? 'bg-amber-600 hover:bg-amber-500' : 'bg-slate-700 text-slate-500 cursor-not-allowed'"
    >
      {{ loading ? 'Creating...' : 'Create Match' }}
    </button>
  </div>
</template>
