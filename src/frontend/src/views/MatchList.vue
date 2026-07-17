<script setup>
import { ref, onMounted } from 'vue';
import { useMatchStore } from '../stores/match.js';

const store = useMatchStore();
const deleting = ref(null);

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

function statusLabel(status) {
  switch (status) {
    case 'running': return '进行中';
    case 'finished': return '已结束';
    case 'paused': return '已暂停';
    case 'created': return '已创建';
    default: return status;
  }
}

async function handleDelete(match) {
  const isCreated = match.status === 'created';
  const message = isCreated
    ? `确定要删除比赛「${match.name}」吗？此操作不可撤销。`
    : `确定要删除比赛「${match.name}」吗？比赛状态为「${statusLabel(match.status)}」，所有数据将被永久删除，不可恢复。`;
  if (!confirm(message)) return;
  deleting.value = match.id;
  try {
    await store.deleteMatch(match.id);
    await store.fetchMatches();
  } catch (e) {
    alert('删除失败: ' + e.message);
  } finally {
    deleting.value = null;
  }
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-white">比赛列表</h1>
      <router-link to="/create" class="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-sm font-medium transition-colors">
        新建比赛
      </router-link>
    </div>

    <div v-if="store.loading" class="text-slate-400 text-center py-12">加载中...</div>
    <div v-else-if="store.error" class="text-red-400 text-center py-12">{{ store.error }}</div>

    <div v-else class="space-y-3">
      <div
        v-for="m in store.matches"
        :key="m.id"
        class="bg-slate-800 border border-slate-700 rounded-lg p-4 hover:border-slate-600 transition-colors"
      >
        <div class="flex items-center justify-between">
          <router-link :to="m.status === 'finished' ? `/replay/${m.id}` : `/match/${m.id}`" class="block flex-1">
            <div class="flex items-center justify-between">
              <div>
                <h3 class="font-semibold text-white">{{ m.name }}</h3>
                <div class="flex items-center gap-3 mt-1 text-xs text-slate-400">
                  <span :class="['px-2 py-0.5 rounded-full text-xs font-medium', statusClass(m.status)]">
                    {{ statusLabel(m.status) }}
                  </span>
                  <span>第 {{ m.current_hand }}/{{ m.total_hands }} 副</span>
                  <span v-if="m.created_at">{{ new Date(m.created_at).toLocaleDateString() }}</span>
                </div>
              </div>
              <div class="flex items-center gap-4">
                <div class="text-center">
                  <div class="text-xl font-bold text-red-400">{{ m.score?.red || 0 }}</div>
                  <div class="text-xs text-slate-500">红队</div>
                </div>
                <div class="text-slate-500">vs</div>
                <div class="text-center">
                  <div class="text-xl font-bold text-blue-400">{{ m.score?.blue || 0 }}</div>
                  <div class="text-xs text-slate-500">蓝队</div>
                </div>
              </div>
            </div>
          </router-link>
          <button
            @click.prevent="handleDelete(m)"
            :disabled="deleting === m.id || m.status === 'running'"
            class="ml-4 px-3 py-1.5 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 hover:text-red-300 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
            :title="m.status === 'running' ? '请先暂停比赛再删除' : '删除比赛'"
          >
            {{ deleting === m.id ? '删除中...' : '删除' }}
          </button>
        </div>
      </div>

      <div v-if="store.matches.length === 0" class="text-center py-12 text-slate-500">
        暂无比赛，创建一个开始吧！
      </div>
    </div>
  </div>
</template>
