<script setup>
import { onMounted } from 'vue';
import { useMatchStore } from '../stores/match.js';

const store = useMatchStore();

onMounted(() => {
  store.fetchMatches();
});

function statusClass(status) {
  switch (status) {
    case 'running': return 'text-green-400 bg-green-400/10';
    case 'finished': return 'text-slate-400 bg-slate-400/10';
    case 'paused': return 'text-amber-400 bg-amber-400/10';
    default: return 'text-blue-400 bg-blue-400/10';
  }
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-white">Matches</h1>
      <router-link to="/create" class="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-sm font-medium transition-colors">
        + New Match
      </router-link>
    </div>

    <div v-if="store.loading" class="text-slate-400 text-center py-12">Loading...</div>
    <div v-else-if="store.error" class="text-red-400 text-center py-12">{{ store.error }}</div>

    <div v-else class="space-y-3">
      <div
        v-for="m in store.matches"
        :key="m.id"
        class="bg-slate-800 border border-slate-700 rounded-lg p-4 hover:border-slate-600 transition-colors"
      >
        <router-link :to="m.status === 'finished' ? `/replay/${m.id}` : `/match/${m.id}`" class="block">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="font-semibold text-white">{{ m.name }}</h3>
              <div class="flex items-center gap-3 mt-1 text-xs text-slate-400">
                <span :class="['px-2 py-0.5 rounded-full text-xs font-medium', statusClass(m.status)]">
                  {{ m.status }}
                </span>
                <span>Hand {{ m.current_hand }}/{{ m.total_hands }}</span>
                <span v-if="m.created_at">{{ new Date(m.created_at).toLocaleDateString() }}</span>
              </div>
            </div>
            <div class="flex items-center gap-4">
              <div class="text-center">
                <div class="text-xl font-bold text-red-400">{{ m.score?.red || 0 }}</div>
                <div class="text-xs text-slate-500">Red</div>
              </div>
              <div class="text-slate-500">vs</div>
              <div class="text-center">
                <div class="text-xl font-bold text-blue-400">{{ m.score?.blue || 0 }}</div>
                <div class="text-xs text-slate-500">Blue</div>
              </div>
            </div>
          </div>
        </router-link>
      </div>

      <div v-if="store.matches.length === 0" class="text-center py-12 text-slate-500">
        No matches yet. Create one to get started!
      </div>
    </div>
  </div>
</template>
