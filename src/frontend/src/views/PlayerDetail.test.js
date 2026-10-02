import { mount, flushPromises } from '@vue/test-utils';
import { afterEach, expect, it, vi } from 'vitest';
import PlayerDetail from './PlayerDetail.vue';
import { api } from '../api/index.js';

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' } }) }));
vi.mock('../api/index.js', () => ({ api: { getPlayer: vi.fn(), updatePlayer: vi.fn() } }));
let wrapper;
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks(); });

it('connects memory versions, previous text, source games and usage while exposing unresolved failures', async () => {
  api.getPlayer.mockResolvedValue({
    id: 'p1', display_name: '牌手', long_term_memory: '保留对子',
    memory_versions: [
      { id: 'v2', revision: 2, previous_id: 'v1', content: '保留对子', match_id: 'source-game' },
      { id: 'v1', revision: 1, content: '先出单牌' },
    ],
    memory_usage: [{ match_id: 'next-game', version_id: 'v2', revision: 2 }],
    memory_failures: [
      { id: 'f1', match_id: 'failed-game', phase: 'summary', stage: 'persistence', recovered: false },
      { id: 'f2', match_id: 'recovered-game', phase: 'summary', stage: 'generation', recovered: true },
    ],
  });
  wrapper = mount(PlayerDetail, { global: { stubs: {
    RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  } } });
  await flushPromises();
  expect(wrapper.text()).toContain('对照更新前的版本 1');
  expect(wrapper.text()).toContain('先出单牌');
  expect(wrapper.find('a[href="/replay/source-game"]').exists()).toBe(true);
  expect(wrapper.find('a[href="/match/next-game"]').exists()).toBe(true);
  expect(wrapper.get('[role="status"]').text()).toContain('有 1 次经验更新未完成');
  expect(wrapper.get('[role="status"]').text()).toContain('仍保留之前的有效记忆');
  await wrapper.get('button').trigger('click');
  await flushPromises();
  expect(api.getPlayer).toHaveBeenCalledTimes(2);
});

it('lets oversized legacy memory be shortened and saves against the original text', async () => {
  const original = '旧'.repeat(8001);
  api.getPlayer.mockResolvedValue({ id: 'p1', long_term_memory: original });
  api.updatePlayer.mockResolvedValue({ id: 'p1' });
  wrapper = mount(PlayerDetail, { global: { stubs: { RouterLink: true } } });
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === '整理长期记忆').trigger('click');
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined();
  await wrapper.get('textarea').setValue(' 优先整理单牌，保留对子。 ');
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined();
  api.getPlayer.mockResolvedValue({ id: 'p1', long_term_memory: '优先整理单牌，保留对子。' });
  await wrapper.get('form').trigger('submit');
  await flushPromises();
  expect(api.updatePlayer).toHaveBeenCalledWith('p1', {
    long_term_memory: '优先整理单牌，保留对子。', expected_long_term_memory: original,
  });
  expect(wrapper.find('textarea').exists()).toBe(false);
  expect(wrapper.text()).toContain('优先整理单牌，保留对子。');
});

it('keeps the draft and saved memory when a concurrent update rejects saving', async () => {
  api.getPlayer.mockResolvedValue({ id: 'p1', long_term_memory: '已有经验' });
  api.updatePlayer.mockRejectedValue(new Error('经验版本已变化，请刷新后重试'));
  wrapper = mount(PlayerDetail, { global: { stubs: { RouterLink: true } } });
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === '整理长期记忆').trigger('click');
  await wrapper.get('textarea').setValue('新经验');
  await wrapper.get('form').trigger('submit');
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain('经验版本已变化');
  expect(wrapper.get('textarea').element.value).toBe('新经验');
  expect(wrapper.text()).toContain('已有经验');
});
