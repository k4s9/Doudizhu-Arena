import fs from 'node:fs';
import vm from 'node:vm';

const root = '/home/guozy/Doudizhu-Arena';
const out = '/tmp/doudizhu-assessment-20260922';
const create = fs.readFileSync(`${root}/src/frontend/src/views/EvaluationCreate.vue`, 'utf8');
const specFunction = create.slice(create.indexOf('function spec()'), create.indexOf('\nasync function check()'));
const spec = vm.runInNewContext(`${specFunction}\nspec()`, {
  model: { value: { name: 'review-mock', provider: 'mock', model: 'reliability-mock-v1', base_url: 'mock://local' } },
  hands: { value: 20 }, budget: { value: 100 }, rotations: { value: 1 },
});
fs.writeFileSync(`${out}/frontend-default-spec.json`, JSON.stringify(spec, null, 2));

const view = fs.readFileSync(`${root}/src/frontend/src/views/MatchView.vue`, 'utf8');
const eventFunction = view.slice(view.indexOf('function handleEvent(event)'), view.indexOf('\nasync function handleStart()'));
const state = {
  status: { value: 'running' }, score: { value: { red: 0, blue: 0 } }, currentHand: { value: 1 },
  tableA: { value: { phase: 'playing' } }, tableB: { value: { phase: 'playing' } },
};
vm.runInNewContext(`${eventFunction}\nhandleEvent({type: 'replay_available', payload: {match_id: 'completed-match'}});`, state);
const result = {
  evaluated_source: 'actual spec() and handleEvent() functions; isolated refs, no browser rendering',
  default_variants: spec.variants,
  default_has_model_pricing: Boolean(spec.models[0].pricing),
  default_has_memory_artifact: Boolean(spec.memory_artifact_path),
  state_after_completed_match_reconnect: state,
};
fs.writeFileSync(`${out}/frontend-boundary-results.json`, JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
