<script setup>
import { RouterView } from 'vue-router';
</script>

<template>
  <div class="min-h-screen bg-slate-950 text-slate-200">
    <header class="border-b border-slate-800 bg-slate-900/50 backdrop-blur sticky top-0 z-50">
      <div class="max-w-7xl mx-auto px-4 py-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <router-link to="/" class="text-xl font-bold text-white hover:text-amber-400 transition-colors">
          🃏 斗地主竞技场
        </router-link>
        <nav class="flex flex-wrap gap-x-4 gap-y-2 text-sm sm:justify-end">
          <router-link to="/" class="text-slate-400 hover:text-white transition-colors">比赛列表</router-link>
          <router-link to="/create" class="text-slate-400 hover:text-white transition-colors">新建比赛</router-link>
          <router-link to="/players" class="text-slate-400 hover:text-white transition-colors">选手管理</router-link>
          <router-link to="/players/leaderboard" class="text-slate-400 hover:text-white transition-colors">选手榜</router-link>
          <router-link to="/configs" class="text-slate-400 hover:text-white transition-colors">配置管理</router-link>
          <router-link to="/evaluations" class="text-slate-400 hover:text-white transition-colors">可靠性实验</router-link>
        </nav>
      </div>
    </header>
    <main class="max-w-7xl mx-auto px-4 py-6">
      <RouterView />
    </main>
  </div>
</template>
