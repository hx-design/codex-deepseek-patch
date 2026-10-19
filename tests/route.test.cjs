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

async function savedChatSwitchingTests() {
  const isolated = vm.createContext({});
  const routes = {'deepseek-flash':{provider:'deepseek-desktop',context_window:1048576,max_context_window:1048576,compact_limit:943718}};
  const native = {'gpt-6.1-sol':{window:400000,maximum:1000000}};
  const source = fs.readFileSync(path.join(__dirname,'../route.js'),'utf8')
    .replace(/(\/\* MODEL_ROUTES_BEGIN \*\/).*?(\/\* MODEL_ROUTES_END \*\/)/s, (_,a,b)=>a+JSON.stringify(routes)+b)
    .replace(/(\/\* NATIVE_CONTEXT_BEGIN \*\/).*?(\/\* NATIVE_CONTEXT_END \*\/)/s, (_,a,b)=>a+JSON.stringify(native)+b);
  vm.runInContext(source,isolated);
  const manage = isolated.codexPatchManagedRequest;
  const callsFirst=[];
  const fresh={hostId:'local',enqueueRequest:async(method)=>{
    callsFirst.push(method);
    if(method==='thread/read')return {thread:{id:'new',modelProvider:'deepseek-desktop',preview:'',ephemeral:false,status:{type:'idle'}}};
    if(method==='thread/resume')throw new Error('no rollout found for thread id new');
    assert.equal(method,'turn/start');return {sent:true};
  }};
  await manage(fresh,'turn/start',{threadId:'new',model:'deepseek-flash'},{});
  assert.deepEqual(callsFirst,['thread/read','thread/resume','turn/start']);
  let current = {id:'saved-gpt',model:'gpt-6.1-sol',modelProvider:'openai',ephemeral:false,status:{type:'idle'}};
  const retained = {approvalPolicy:'never',approvalsReviewer:'user',sandbox:{type:'readOnly'},
    activePermissionProfile:{id:':read-only'},cwd:'/safe/work',runtimeWorkspaceRoots:['/safe/work','/safe/assets']};
  const calls = [];
  const client = {hostId:'local',
    async sendConfigReadRequest() {return {config:{model_context_window:200000,model_auto_compact_token_limit:170000,web_search:'live'}};},
    async enqueueRequest(method,params) {
      calls.push({method,params});
      if(method==='thread/read') return {thread:{...current}};
      if(method==='thread/unsubscribe') return {status:'unsubscribed'};
      if(method==='thread/resume') {
        current={...current,model:params.model??current.model,modelProvider:params.modelProvider??current.modelProvider};
        return {thread:{...current},model:current.model,modelProvider:current.modelProvider,...retained};
      }
      return {sent:true};
    }};
  const dsTurn={threadId:current.id,collaborationMode:{settings:{model:'deepseek-flash',reasoning_effort:'max'}},serviceTier:'priority'};
  await manage(client,'turn/start',dsTurn,{});
  assert.deepEqual(calls.map(v=>v.method),['thread/read','thread/resume','thread/unsubscribe','thread/resume','turn/start']);
  assert.equal(calls[3].params.threadId,'saved-gpt');
  assert.equal(calls[3].params.modelProvider,'deepseek-desktop');
  assert.equal(calls[3].params.config.model_context_window,1048576);
  assert.equal(calls[3].params.config.model_auto_compact_token_limit,943718);
  for(const key of ['approvalPolicy','approvalsReviewer','cwd','runtimeWorkspaceRoots'])
    assert.equal(calls[3].params[key],retained[key]);
  assert.equal(calls[3].params.permissions,':read-only');
  assert.equal(calls.at(-1).params.collaborationMode,dsTurn.collaborationMode);
  assert.equal(calls.at(-1).params.serviceTier,null);
  calls.length=0;
  await manage(client,'turn/start',dsTurn,{});
  assert.deepEqual(calls.map(v=>v.method),['turn/start']);
  calls.length=0;
  await manage(client,'turn/start',{threadId:current.id,model:'gpt-6.1-sol',serviceTier:'priority'},{});
  assert.equal(calls[3].params.modelProvider,'openai');
  assert.equal(calls[3].params.config.model_context_window,200000);
  assert.equal(calls[3].params.config.model_auto_compact_token_limit,170000);
  assert.equal(calls[3].params.config.web_search,'live');
  assert.equal(calls.at(-1).params.serviceTier,'priority');
  calls.length=0;
  await manage(client,'turn/start',{threadId:current.id,model:'deepseek-flash'},{});
  assert.equal(calls[3].params.config.model_context_window,1048576);
  // An explicit reload invalidates the model cache.
  assert.equal(manage(client,'thread/resume',{threadId:current.id},{}),null);
  calls.length=0;
  await manage(client,'turn/start',dsTurn,{});
  assert.equal(calls[0].method,'thread/read');
  const active={hostId:'local',enqueueRequest:async method=>{
    if(method==='thread/read') return {thread:{...current,status:{type:'active'}}};
    assert.fail('A running chat must stay loaded');
  }};
  await assert.rejects(manage(active,'turn/start',dsTurn,{}),/finish/);
  assert.equal(manage({...client,hostId:'remote'},'turn/start',dsTurn,{}),null);
  assert.equal(manage(client,'turn/start',{threadId:current.id,model:'unregistered'},{}),null);
  const outsider={hostId:'local',enqueueRequest:async(method,params)=>{
    if(method==='thread/read')return {thread:{...current,modelProvider:'outside'}};
    assert.equal(method,'turn/start');assert.equal(params.serviceTier,'priority');return {};
  }};
  await manage(outsider,'turn/start',dsTurn,{});
  await manage(outsider,'turn/start',dsTurn,{});
  const rollback=[];
  const broken={hostId:'local',enqueueRequest:async(method,params)=>{
    rollback.push({method,params});
    if(method==='thread/read')return {thread:{...current,modelProvider:'openai'}};
    if(method==='thread/unsubscribe')return {};
    if(method==='thread/resume') {
      if(params.model==='deepseek-flash')throw new Error('provider unavailable');
      return {thread:{...current},model:'gpt-6.1-sol',modelProvider:'openai',...retained};
    }
    assert.fail('A failed switch must not send a turn');
  }};
  await assert.rejects(manage(broken,'turn/start',dsTurn,{}),/unavailable/);
  assert.equal(rollback.at(-2).method,'thread/unsubscribe');
  assert.equal(rollback.at(-1).params.model,'gpt-6.1-sol');
  assert.equal(rollback.at(-1).params.modelProvider,'openai');
  // Older permission responses retain workspace roots and network policy.
  const workspace={...retained,activePermissionProfile:null,sandbox:{type:'workspaceWrite',writableRoots:['/safe/assets'],networkAccess:true,excludeSlashTmp:true,excludeTmpdirEnvVar:true}};
  const legacyCalls=[];
  const legacy={hostId:'local',enqueueRequest:async(method,params)=>{
    legacyCalls.push({method,params});
    if(method==='thread/read')return {thread:{...current,modelProvider:'openai'}};
    if(method==='thread/resume')return {thread:{...current},model:params.model??'gpt-6.1-sol',modelProvider:params.modelProvider??'openai',...workspace};
    return {};
  }};
  await manage(legacy,'turn/start',dsTurn,{});
  assert.equal(legacyCalls[3].params.sandbox,'workspace-write');
  assert.deepEqual(JSON.parse(JSON.stringify(legacyCalls[3].params.config.sandbox_workspace_write)),{
    writable_roots:['/safe/assets'],network_access:true,exclude_slash_tmp:true,exclude_tmpdir_env_var:true});
  console.log('PASS: saved GPT ↔ DS switching, context/permission retention, caching, busy guard and rollback');
}
savedChatSwitchingTests().catch(error=>{console.error(error);process.exitCode=1;});
