/**
 * API client — wraps REST calls to the backend.
 */

const BASE = '/api/v1';

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (res.status === 204) return null;
  const data = await res.json();
  if (!res.ok) {
    const err = new Error(data?.error?.message || res.statusText);
    err.code = data?.error?.code || 'UNKNOWN';
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  // ── matches ──
  listMatches(params = {}) {
    const qs = new URLSearchParams(params).toString();
    return request(`/matches${qs ? '?' + qs : ''}`);
  },
  createMatch(body) {
    return request('/matches', { method: 'POST', body: JSON.stringify(body) });
  },
  getMatch(id) {
    return request(`/matches/${id}`);
  },
  startMatch(id) {
    return request(`/matches/${id}/start`, { method: 'POST' });
  },
  pauseMatch(id) {
    return request(`/matches/${id}/pause`, { method: 'POST' });
  },
  resumeMatch(id) {
    return request(`/matches/${id}/resume`, { method: 'POST' });
  },
  deleteMatch(id) {
    return request(`/matches/${id}`, { method: 'DELETE' });
  },

  // ── hands / replay ──
  getHands(matchId) {
    return request(`/matches/${matchId}/hands`);
  },
  getHandDetail(matchId, handNum) {
    return request(`/matches/${matchId}/hands/${handNum}`);
  },
  getTableHand(matchId, handNum, table) {
    return request(`/matches/${matchId}/hands/${handNum}/table/${table.toLowerCase()}`);
  },

  // ── agents ──
  listAgents() {
    return request('/agents');
  },
  createAgent(body) {
    return request('/agents', { method: 'POST', body: JSON.stringify(body) });
  },
  updateAgent(id, body) {
    return request(`/agents/${id}`, { method: 'PUT', body: JSON.stringify(body) });
  },
  deleteAgent(id) {
    return request(`/agents/${id}`, { method: 'DELETE' });
  },
};

/**
 * WebSocket client for live match streaming.
 */
export function createMatchSocket(matchId, handlers = {}) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws/match/${matchId}`;
  let ws = null;
  let reconnectTimer = null;
  let reconnectDelay = 1000;
  let closed = false;

  function connect() {
    if (closed) return;
    ws = new WebSocket(url);

    ws.onopen = () => {
      reconnectDelay = 1000;
      handlers.onOpen?.();
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const type = data.type;
      if (type === 'pong') {
        handlers.onPong?.();
      } else if (handlers.onEvent) {
        handlers.onEvent(data);
      } else if (handlers[type]) {
        handlers[type](data.payload);
      }
    };

    ws.onclose = () => {
      if (!closed) {
        reconnectTimer = setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
      }
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  function send(msg) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  connect();

  return {
    send,
    close() {
      closed = true;
      clearTimeout(reconnectTimer);
      ws?.close();
    },
  };
}
