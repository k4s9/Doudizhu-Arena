import { mount, flushPromises } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EvaluationCreate from './EvaluationCreate.vue';
import EvaluationDetail from './EvaluationDetail.vue';
import { api } from '../api/index.js';

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../api/index.js', () => ({ api: {
  getEvaluationTemplate: vi.fn(), listConfigs: vi.fn(), preflightEvaluation: vi.fn(), createEvaluation: vi.fn(),
  getEvaluation: vi.fn(), getEvaluationReport: vi.fn(), startEvaluation: vi.fn(), resumeEvaluation: vi.fn(), cancelEvaluation: vi.fn(),
} }));

let wrapper;
afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); });
const variants = ['single_generation', 'generic_retry', 'rule_feedback'].map((variant_id, i) => ({variant_id, retry_limit: i ? 2 : 0, rule_feedback: i === 2, enable_reflection: false, memory_mode: 'disabled'}));
const template = { models: [{provider: 'mock', model: 'mock'}], variants, mock_scenario: 'valid_first' };

describe('evaluation workflows', () => {
  it('preflights the server protocol and invalidates approval after controls change', async () => {
    api.getEvaluationTemplate.mockResolvedValue(template);
    api.listConfigs.mockResolvedValue({configs: []});
    api.preflightEvaluation.mockResolvedValue({ok: true, errors: [], seed_count: 2, task_count: 6});
    wrapper = mount(EvaluationCreate); await flushPromises();
    await wrapper.findAll('button')[0].trigger('click'); await flushPromises();
    const spec = api.preflightEvaluation.mock.calls[0][0];
    expect(spec.variants).toEqual(variants);
    expect(spec.models[0].provider).toBe('mock');
    expect(spec.total_hands).toBe(1);
    expect(wrapper.findAll('button')[1].element.disabled).toBe(false);
    await wrapper.find('input[type="number"]').setValue(2);
    expect(wrapper.findAll('button')[1].element.disabled).toBe(true);
  });

  it.each(['mock', 'openai'])('preserves explicit real invocation confirmation (%s)', async provider => {
    const run = {status: 'planned', manifest: {experiment_id: 'demo', models: [{provider, model:'example'}], frozen_spec: {budget_limit_usd: 1}}};
    api.getEvaluation.mockResolvedValue({run, counts: {planned: 1, finished: 0, failed: 0, cancelled: 0}, tasks: []});
    const confirm = vi.fn().mockReturnValue(false);
    vi.stubGlobal('confirm', confirm);
    wrapper = mount(EvaluationDetail, {props: {id: 'run-1'}, global: {stubs: {RouterLink: true}}});
    await flushPromises(); await wrapper.find('button').trigger('click'); await flushPromises();
    if (provider === 'mock') {
      expect(confirm).not.toHaveBeenCalled();
      expect(api.startEvaluation).toHaveBeenCalledWith('run-1', false);
    } else {
      expect(api.startEvaluation).not.toHaveBeenCalled();
      confirm.mockReturnValue(true);
      await wrapper.find('button').trigger('click'); await flushPromises();
      expect(api.startEvaluation).toHaveBeenCalledWith('run-1', true);
    }
  });

  it('submits explicit free pricing and a zero budget, but rejects an empty budget', async () => {
    api.getEvaluationTemplate.mockResolvedValue(template);
    api.listConfigs.mockResolvedValue({configs: [{id: 'free', name: 'free', provider: 'openai', model: 'qwen3.5'}]});
    api.preflightEvaluation.mockResolvedValue({ok: true, errors: [], seed_count: 1, task_count: 3});
    wrapper = mount(EvaluationCreate); await flushPromises();
    await wrapper.find('select').setValue('real'); await flushPromises();
    const inputs = wrapper.findAll('input[type="number"]');
    await inputs[0].setValue(0); await inputs[1].setValue(0);
    await wrapper.find('input[placeholder]').setValue('User-confirmed free account');
    await wrapper.find('input[type="date"]').setValue('2026-09-24');
    await inputs[4].setValue(0);
    await wrapper.findAll('button')[0].trigger('click'); await flushPromises();
    const spec = api.preflightEvaluation.mock.calls[0][0];
    expect(spec.budget_limit_usd).toBe(0);
    expect(spec.models[0].pricing.input_per_million).toBe(0);
    expect(spec.models[0].pricing.output_per_million).toBe(0);
    await inputs[4].setValue('');
    await wrapper.findAll('button')[0].trigger('click'); await flushPromises();
    expect(api.preflightEvaluation).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[role="alert"]').text()).toContain('预算');
    await inputs[4].setValue(0);
    await inputs[0].setValue('');
    await wrapper.findAll('button')[0].trigger('click'); await flushPromises();
    expect(api.preflightEvaluation).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[role="alert"]').text()).toContain('价格');
  });
});
