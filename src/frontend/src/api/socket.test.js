import { afterEach, expect, it, vi } from 'vitest';
import { createMatchSocket } from './index.js';

let client;
afterEach(() => { client?.close(); vi.useRealTimers(); vi.unstubAllGlobals(); });
it('deduplicates events and requires a new snapshot after a gap or slow-client resync', () => {
  vi.useFakeTimers();
  const sockets = [];
  class FakeSocket {
    static OPEN = 1;
    constructor() { this.readyState = 1; this.close = vi.fn(() => this.onclose?.()); sockets.push(this); }
    message(data) { this.onmessage({data: JSON.stringify(data)}); }
  }
  vi.stubGlobal('WebSocket', FakeSocket);
  const onEvent = vi.fn(); client = createMatchSocket('m', {onEvent});
  sockets[0].message({type: 'match_state', payload: {watermark: 10}});
  sockets[0].message({type: 'card_played', seq: 11});
  sockets[0].message({type: 'card_played', seq: 11});
  expect(onEvent).toHaveBeenCalledTimes(2);
  sockets[0].message({type: 'card_played', seq: 13});
  expect(sockets[0].close).toHaveBeenCalledOnce();
  vi.advanceTimersByTime(1000);
  sockets[1].message({type: 'card_played', seq: 12});
  expect(onEvent).toHaveBeenCalledTimes(2);
  sockets[1].message({type: 'match_state', payload: {watermark: 12}});
  sockets[1].message({type: 'card_played', seq: 13});
  expect(onEvent).toHaveBeenCalledTimes(4);
  sockets[1].message({type: 'resync_required'});
  expect(sockets[1].close).toHaveBeenCalledOnce();
});
