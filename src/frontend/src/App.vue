<script setup>
import { RouterView } from 'vue-router';
</script>

<template>
  <div class="min-h-screen bg-slate-950 text-slate-200">
    <header class="border-b border-slate-800 bg-slate-900/50 backdrop-blur sticky top-0 z-50">
      <div class="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
        <router-link to="/" class="text-xl font-bold text-white hover:text-amber-400 transition-colors">
          🃏 Doudizhu Arena
        </router-link>
        <nav class="flex gap-4 text-sm">
          <router-link to="/" class="text-slate-400 hover:text-white transition-colors">Matches</router-link>
          <router-link to="/create" class="text-slate-400 hover:text-white transition-colors">New Match</router-link>
        </nav>
      </div>
    </header>
    <main class="max-w-7xl mx-auto px-4 py-6">
      <RouterView />
    </main>
  </div>
</template>
