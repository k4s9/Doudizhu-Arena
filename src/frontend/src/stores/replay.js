import { defineStore } from 'pinia';
import { ref } from 'vue';
import { api } from '../api/index.js';

export const useReplayStore = defineStore('replay', () => {
  const hands = ref([]);
  const handDetail = ref(null);
  const tableHand = ref(null);
  const loading = ref(false);
  const error = ref(null);

  async function fetchHands(matchId) {
    loading.value = true;
    error.value = null;
    try {
      const data = await api.getHands(matchId);
      hands.value = data.hands;
      return data;
    } catch (e) {
      error.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  async function fetchHandDetail(matchId, handNum) {
    loading.value = true;
    try {
      handDetail.value = await api.getHandDetail(matchId, handNum);
      return handDetail.value;
    } catch (e) {
      error.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  async function fetchTableHand(matchId, handNum, table) {
    loading.value = true;
    try {
      tableHand.value = await api.getTableHand(matchId, handNum, table);
      return tableHand.value;
    } catch (e) {
      error.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  return {
    hands,
    handDetail,
    tableHand,
    loading,
    error,
    fetchHands,
    fetchHandDetail,
    fetchTableHand,
  };
});
