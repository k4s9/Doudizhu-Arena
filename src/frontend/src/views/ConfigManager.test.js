import { mount, flushPromises } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import ConfigManager from './ConfigManager.vue';
import { api } from '../api/index.js';

vi.mock('../api/index.js', () => ({ api: { listConfigs: vi.fn() } }));

it('shows credential availability and opens an empty password field', async () => {
  api.listConfigs.mockResolvedValue({ configs: [
    { id: 'configured', name: 'Qwen', provider: 'openai', model: 'qwen3.5', has_api_key: true, player_count: 1 },
    { id: 'missing', name: 'Minimax', provider: 'openai', model: 'minimax-m27', has_api_key: false, player_count: 1 },
    { id: 'random', name: 'Random', provider: 'random', has_api_key: false, player_count: 1 },
  ] });
  const wrapper = mount(ConfigManager, { global: { stubs: { RouterLink: true } } });
  try {
    await flushPromises();
    const rows = wrapper.findAll('tbody tr');
    expect(rows[0].text()).toContain('已配置');
    expect(rows[1].text()).toContain('未配置');
    expect(rows[2].text()).toContain('无需密钥');
    await rows[0].find('button').trigger('click');
    expect(wrapper.find('input[type="password"]').element.value).toBe('');
  } finally { wrapper.unmount(); }
});
