const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../route.js'), 'utf8'), context);
const route = context.codexPatchAppServerParams;
for (const provider of [undefined, null, 'openai']) {
  const params = {model: 'deepseek-flash', modelProvider: provider, serviceTier: 'priority',
                  config: {keep: 42}, dynamicTools: [{name: 'example'}]};
  const result = route('thread/start', params, 'local');
  assert.equal(result.modelProvider, 'deepseek-desktop');
  assert.equal(result.serviceTier, null);
  assert.equal(result.config.keep, 42);
  assert.equal(result.config.web_search, 'disabled');
  assert.equal(result.dynamicTools, params.dynamicTools);
  assert.equal(params.modelProvider, provider);
  assert.equal(params.config.web_search, undefined);
}
for (const [method, params, host] of [
  ['thread/start', {model: 'gpt-6.1-sol'}, 'local'],
  ['thread/start', {model: 'deepseek-v4-pro'}, 'local'],
  ['thread/start', {model: 'deepseek-flash'}, 'remote'],
  ['thread/start', {model: 'deepseek-flash'}, 'durable'],
  ['thread/start', {model: 'deepseek-flash', modelProvider: 'custom'}, 'local'],
  ['thread/resume', {model: 'deepseek-flash'}, 'local'],
  ['turn/start', {model: 'deepseek-flash'}, 'local'],
  ['thread/list', {modelProviders: ['custom']}, 'local'],
  ['thread/start', null, 'local'],
]) assert.equal(route(method, params, host), params);
assert.equal(route('thread/list', {}, 'local').modelProviders.length, 0);

async function actualBundleTest(filename) {
  const source = fs.readFileSync(filename, 'utf8');
  const start = source.indexOf('async sendRequest(e,t,n){if(this.dispatchMessage==null)');
  const end = source.indexOf('async sendAppServerExtensionRequest(', start);
  assert(start > 0 && end > start);
  vm.runInContext('class Client {' + source.slice(start, end) + '}; globalThis.Client=Client', context);
  for (const hostId of ['local', 'remote']) {
    const client = new context.Client();
    client.hostId = hostId; client.dispatchMessage = () => {};
    client.enqueueRequest = async (method, params) => ({method, params});
    const result = await client.sendRequest('thread/start', {model: 'deepseek-flash'}, {});
    assert.equal(result.params.modelProvider, hostId === 'local' ? 'deepseek-desktop' : undefined);
  }
  assert(source.includes('this.params.hostId!==`local`&&f.canUsePrewarmedThread&&'));
  assert(source.includes('e=codexPatchAppServerParams(`thread/start`,e,this.hostId);'));
}
(async () => {
  if (process.argv[2]) await actualBundleTest(process.argv[2]);
  console.log('PASS: local Flash routing, GPT/remote/existing/custom provider preservation' +
              (process.argv[2] ? ', actual patched request method' : ''));
})().catch(error => {console.error(error); process.exitCode = 1;});
