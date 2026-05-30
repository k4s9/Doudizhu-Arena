import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { api } from '../api/index.js';

export const useMatchStore = defineStore('match', () => {
  const matches = ref([]);
  const currentMatch = ref(null);
  const loading = ref(false);
  const error = ref(null);

  async function fetchMatches(params = {}) {
    loading.value = true;
    error.value = null;
    try {
      const data = await api.listMatches(params);
      matches.value = data.matches;
      return data;
    } catch (e) {
      error.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  async function fetchMatch(id) {
    loading.value = true;
    error.value = null;
    try {
      currentMatch.value = await api.getMatch(id);
      return currentMatch.value;
    } catch (e) {
      error.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  async function createMatch(body) {
    loading.value = true;
    error.value = null;
    try {
      const match = await api.createMatch(body);
      return match;
    } catch (e) {
      error.value = e.message;
      throw e;
    } finally {
      loading.value = false;
    }
  }

  async function startMatch(id) {
    return api.startMatch(id);
  }

  async function pauseMatch(id) {
    return api.pauseMatch(id);
  }

  async function resumeMatch(id) {
    return api.resumeMatch(id);
  }

  return {
    matches,
    currentMatch,
    loading,
    error,
    fetchMatches,
    fetchMatch,
    createMatch,
    startMatch,
    pauseMatch,
    resumeMatch,
  };
});
