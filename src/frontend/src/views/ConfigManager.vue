<script setup>
import { ref, onMounted } from 'vue';
import { api } from '../api/index.js';

const configs = ref([]);
const error = ref('');

const editing = ref(null);
const editForm = ref({
  name: '',
  provider: '',
  model: '',
  api_key: '',
  base_url: '',
  system_prompt: '',
});

async function loadConfigs() {
  try {
    const data = await api.listConfigs();
    configs.value = data.configs;
  } catch (e) {
    error.value = e.message;
  }
}

async function reloadConfigs() {
  try {
    await api.reloadConfigs();
    await loadConfigs();
  } catch (e) {
    error.value = e.message;
  }
}

function startEdit(c) {
  editing.value = c.id;
  editForm.value = {
    name: c.name || '',
    provider: c.provider || '',
    model: c.model || '',
    api_key: '',
    base_url: c.base_url || '',
    system_prompt: c.system_prompt || '',
  };
}

async function saveEdit() {
  if (!editing.value) return;
  try {
    await api.updateConfig(editing.value, editForm.value);
    editing.value = null;
    await loadConfigs();
  } catch (e) {
    error.value = e.message;
  }
}

function cancelEdit() {
  editing.value = null;
}

async function deleteConfig(id) {
  if (!confirm('确定删除该配置吗？只有没有被玩家引用时才能删除。')) return;
  try {
    await api.deleteConfig(id);
    await loadConfigs();
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(loadConfigs);
</script>

<template>
  <div class="max-w-4xl mx-auto">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-white">玩家配置</h1>
      <div class="flex gap-2">
        <button
          @click="reloadConfigs"
          class="px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors"
        >
          从 YAML 重新加载
        </button>
        <router-link to="/players" class="text-sm text-amber-400 hover:text-amber-300">
          管理玩家 →
        </router-link>
      </div>
    </div>

    <div v-if="error" class="bg-red-900/30 border border-red-700 rounded-lg p-3 mb-4 text-sm text-red-300">
      {{ error }}
    </div>

    <!-- Configs list -->
    <div class="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-700 text-slate-400">
            <th class="text-left px-4 py-2 font-medium">名称</th>
            <th class="text-left px-4 py-2 font-medium">提供商</th>
            <th class="text-left px-4 py-2 font-medium">模型</th>
            <th class="text-left px-4 py-2 font-medium">接口地址</th>
            <th class="text-center px-4 py-2 font-medium">玩家数</th>
            <th class="text-right px-4 py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="c in configs"
            :key="c.id"
            class="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors"
          >
            <td class="px-4 py-2 text-white">{{ c.name }}</td>
            <td class="px-4 py-2 text-slate-400">
              <span :class="{
                'text-green-400': c.provider === 'openai',
                'text-purple-400': c.provider === 'claude',
                'text-slate-400': c.provider === 'random',
              }">{{ c.provider }}</span>
            </td>
            <td class="px-4 py-2 text-slate-400">{{ c.model }}</td>
            <td class="px-4 py-2 text-slate-500 text-xs max-w-[200px] truncate">{{ c.base_url || '默认' }}</td>
            <td class="px-4 py-2 text-center text-slate-300">{{ c.player_count }}</td>
            <td class="px-4 py-2 text-right space-x-2">
              <button
                @click="startEdit(c)"
                class="text-xs text-amber-400 hover:text-amber-300"
              >
                编辑
              </button>
              <button
                @click="deleteConfig(c.id)"
                :disabled="c.player_count > 0"
                class="text-xs text-red-400 hover:text-red-300 disabled:text-slate-600 disabled:cursor-not-allowed"
                :title="c.player_count > 0 ? '请先删除关联的玩家' : '删除配置'"
              >
                删除
              </button>
            </td>
          </tr>
          <tr v-if="configs.length === 0">
            <td colspan="6" class="px-4 py-8 text-center text-slate-500">
              暂无配置。请通过 API 创建或添加到 agents.yaml 后重新加载。
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Edit modal -->
    <div v-if="editing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50" @click.self="cancelEdit">
      <div class="bg-slate-800 border border-slate-600 rounded-lg p-6 w-full max-w-md">
        <h2 class="text-lg font-semibold text-white mb-4">编辑配置</h2>
        <div class="space-y-3">
          <div>
            <label class="block text-xs text-slate-400 mb-1">名称</label>
            <input v-model="editForm.name"
              class="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white" />
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">提供商</label>
            <select v-model="editForm.provider"
              class="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white">
              <option value="openai">openai</option>
              <option value="claude">claude</option>
              <option value="random">random</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">模型</label>
            <input v-model="editForm.model"
              class="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white" />
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">API 密钥</label>
            <input v-model="editForm.api_key" type="password"
              class="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white" />
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">接口地址</label>
            <input v-model="editForm.base_url"
              class="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white" />
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">系统提示词</label>
            <textarea v-model="editForm.system_prompt" rows="3"
              class="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white" />
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-4">
          <button @click="cancelEdit"
            class="px-4 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-slate-300 rounded">取消</button>
          <button @click="saveEdit"
            class="px-4 py-1.5 text-sm bg-amber-600 hover:bg-amber-500 text-white rounded font-medium">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
