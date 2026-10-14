import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const root = resolve(import.meta.dirname, '..');
const server = await createServer({ root, configFile: false,
  server: { middlewareMode: true, hmr: false }, appType: 'custom' });
try {
const load = path => server.ssrLoadModule('/src/' + path);
const { restoreConversationContext, mergeConversationMessages } = await load('features/conversations/conversationContext.ts');
const { translations } = await load('lib/i18n.ts');
const { executionErrors } = await load('features/workflows/executionValidation.ts');
const t = translations.zh;

await test('legacy history restores mode, user skills and dataset; explicit null overrides metadata', () => {
  const messages = [{role:'user', metadata:{interaction_mode:'task',selected_skills:['inspect'],dataset_id:'A'}},
    {role:'assistant', metadata:{workflow_id:'wf'}}, {role:'assistant',metadata:{workflow_id:null}}];
  assert.deepEqual(restoreConversationContext({messages}), {interaction_mode:'task',selected_skills:['inspect'],dataset_id:'A',workflow_id:'wf'});
  assert.equal(restoreConversationContext({messages,context:{dataset_id:null}}).dataset_id, null);
  assert.deepEqual(restoreConversationContext({messages,context:{selected_skills:[]}}).selected_skills, []);
});
await test('authoritative history retires a completed optimistic pair without deduplicating text', () => {
  const stored = [{id:'stored-1',role:'user',content:'same'}, {id:'stored-2',role:'assistant',content:'same'}];
  const transient = [{id:'local-1',role:'user',content:'same',persistedAfterRead:4},
    {id:'local-2',role:'assistant',content:'same',persistedAfterRead:4}];
  assert.equal(mergeConversationMessages(stored, transient, 5).length, 2);
  assert.equal(mergeConversationMessages(stored, transient, 4).length, 4, 'in-flight stale GET must not retire a later turn');
  assert.equal(mergeConversationMessages(stored, [{id:'new-user',role:'user',content:'same'}], 5).length, 3);
});
await test('older history pages deduplicate only stable IDs and retain ordering', async () => {
  const { prependUniqueMessages } = await load('features/chat/chatMessages.ts');
  assert.deepEqual(prependUniqueMessages([{id:'b'}, {id:'c'}], [{id:'a'},{id:'b'}]).map(m=>m.id), ['a','b','c']);
});
await test('lost-response optimistic messages reconcile by persisted request identity, keeping repeated user text', async () => {
  const { toChatMessage } = await load('features/chat/chatMessages.ts');
  const persisted = ['user','assistant'].map((role, index) => toChatMessage({message_id:'p'+index,
    role, content:role==='user'?'接受':'方案已准备', created_at:'now', sequence:index+1,
    metadata:{client_turn_id:'recovered-request'}}));
  const local = [{id:'old-local',role:'user',content:'接受',clientTurnId:'recovered-request'},
    {id:'new-local',role:'user',content:'接受',clientTurnId:'different-request'}];
  const merged = mergeConversationMessages(persisted,local,0);
  assert.deepEqual(merged.map(message=>message.id),['stored-p0','stored-p1','new-local']);
  assert.equal(merged.filter(message=>message.content==='接受').length,2);
  assert.equal(mergeConversationMessages(persisted.slice(0,1),[
    {id:'reply-local',role:'assistant',content:'方案已准备',clientTurnId:'recovered-request'}],0).length,2,
    'persisted user identity must not retire an assistant reply before it is stored');
});
await test('workflow reads limit concurrency and release failed or aborted slots', async () => {
  const { createReadQueue } = await load('api/requestQueue.ts');
  const run = createReadQueue(2);
  const releases = [];
  let active=0, peak=0, calls=0;
  const read = () => run(async () => {
    calls++; active++; peak=Math.max(peak,active);
    await new Promise(resolve => releases.push(resolve)); active--; return calls;
  });
  const a=read(), b=read(), c=read();
  assert.equal(calls,2); releases.shift()(); await a;
  assert.equal(calls,3); releases.splice(0).forEach(fn=>fn()); await Promise.all([b,c]);
  assert.equal(peak,2);
  await assert.rejects(run(async()=>{throw new Error('offline');}), /offline/);
  const controller=new AbortController(); controller.abort();
  await assert.rejects(run(async()=>assert.fail('aborted read started'),controller.signal), /Aborted/);
  assert.equal(await run(async()=>42),42);
});
const node = {node:'node03',cpus:4,memory_gb:2,gpus:0,time_minutes:10,parallel_samples:4};
const backends = {slurm:{available:true,enabled:true,partitions:['all'],gpu_available:false,
  nodes:[{name:'node03',partition:'all',state:'idle',schedulable:true,cpus:4,memory_mb:8192,gpus:0}]}};
const options = {backend:'slurm',partition:'all',sample_mode:'per_sample',nodes:[node]};
const workflow = {workflow_id:'one',dataset:{inspection:{sample_count:1}},steps:[]};
await test('one sample supports per-sample Slurm and uses effective concurrency', () => {
  assert.deepEqual(executionErrors(options,backends,workflow,t),[]);
  assert(executionErrors({...options,nodes:[{...node,cpus:8}]},backends,workflow,t).length);
});
await test('partial historical coverage runs only common samples and rejects local or empty scope', () => {
  const scope={total:500,eligible_sample_ids:['B'],skipped_samples:[{sample_id:'A',label:'A',reasons:['5TT missing']}]};
  const partial={...workflow,dataset:{inspection:{sample_count:500}},sample_scope:scope};
  assert.deepEqual(executionErrors(options,backends,partial,t),[]);
  assert(executionErrors({...options,backend:'local'},backends,partial,t).some(x=>x.includes('按样本')));
  assert(executionErrors(options,backends,{...partial,sample_scope:{...scope,eligible_sample_ids:[]}},t).some(x=>x.includes('没有共同匹配')));
  assert(executionErrors({...options,nodes:[node,node]},backends,partial,t).some(x=>x.includes('节点数')));
});
await test('allocation validation enforces parallel capacity, thread count and CUDA resources', () => {
  const batch={...workflow,dataset:{inspection:{sample_count:10}}};
  assert(executionErrors(options,backends,batch,t).length>0);
  assert(executionErrors({...options,nodes:[{...node,parallel_samples:1}]},backends,
    {...workflow,steps:[{selected_parameters:{threads:8,device:'cuda'},input_bindings:{}}]},t).length>=2);
});
await test('MRtrix thread allocation allows serial zero and rejects oversubscription', () => {
  for (const count of [0,1,4]) assert.deepEqual(executionErrors(options,backends,
    {...workflow,steps:[{selected_parameters:{nthreads:count},input_bindings:{}}]},t),[]);
  assert(executionErrors(options,backends,
    {...workflow,steps:[{selected_parameters:{nthreads:8},input_bindings:{}}]},t).length>0);
});
await test('competition settings fix sample mode and retain node and resource controls', async () => {
  const { ExecutionSettings }=await load('features/workflows/ExecutionSettings.tsx');
  const html=renderToStaticMarkup(createElement(ExecutionSettings,{workflow,value:options,backends,disabled:false,onChange(){}}));
  assert.match(html, /Per sample \(1 samples\)|按样本运行（1 个样本）/);
  assert(!html.includes('id="samples-one"') && !html.includes('value="single"'));
  assert(html.includes('id="partition-one"') && html.includes('type="checkbox"'));
  assert(html.includes('one' + 'node03-parallel') && html.includes('CPU'));
  assert(html.includes('node03'));
});
const { DEFAULT_EXECUTION_OPTIONS, competitionExecutionOptions, executionOptionsFingerprint } = await load('features/workflows/workflowTypes.ts');
await test('competition defaults and existing local drafts use Slurm without changing resources', () => {
  assert.equal(DEFAULT_EXECUTION_OPTIONS.backend, 'slurm');
  assert.equal(DEFAULT_EXECUTION_OPTIONS.sample_mode, 'per_sample');
  const legacy = {...DEFAULT_EXECUTION_OPTIONS, backend:'local', sample_mode:'single',
    cpus:16, memory_gb:32, time_minutes:90, output_layout:'sample_steps', nodes:[]};
  const migrated = competitionExecutionOptions(legacy);
  assert.deepEqual(migrated, {...legacy, backend:'slurm', sample_mode:'per_sample', node:null});
  assert.equal(legacy.backend, 'local');
});
await test('existing node selection, allocations and concurrency survive fixed-mode migration', () => {
  const legacy = {...DEFAULT_EXECUTION_OPTIONS, sample_mode:'single', nodes:[node], node:null};
  assert.deepEqual(competitionExecutionOptions(legacy), {...legacy, sample_mode:'per_sample'});
  const singleNode = {...DEFAULT_EXECUTION_OPTIONS, sample_mode:'single', node:'node03'};
  const migrated = competitionExecutionOptions(singleNode);
  assert.deepEqual(migrated.nodes, [{node:'node03',cpus:4,memory_gb:8,gpus:0,time_minutes:60,parallel_samples:1}]);
  assert.equal(migrated.node,null);
  assert.deepEqual(competitionExecutionOptions(migrated), migrated);
});
await test('saved migration is stable when the server reorders fields or fills optional defaults', () => {
  const current = competitionExecutionOptions({...DEFAULT_EXECUTION_OPTIONS,nodes:[{...node,parallel_samples:undefined}]});
  const saved = Object.fromEntries(Object.entries({...current,output_layout:null,
    nodes:[{parallel_samples:1,...node}]}).reverse());
  saved.nodes[0].parallel_samples = 1;
  assert.equal(executionOptionsFingerprint(current), executionOptionsFingerprint(saved));
  assert.notEqual(executionOptionsFingerprint(current),executionOptionsFingerprint({...current,backend:'local'}));
  assert.notEqual(executionOptionsFingerprint(current),executionOptionsFingerprint({...current,nodes:[{...node,cpus:8}]}));
});
const step=(sample,index,status='queued',extra={})=>({step_id:sample+'-'+index,sample_id:sample,sample_label:sample,title:'step '+index,
  runner_id:'fixture',method_id:'fixture',node_name:'node01',status,progress:status==='succeeded'?100:0,
  message:'',artifacts:[],error_code:'',error_message:'',started_at:null,finished_at:null,...extra});
const { groupRunSteps, filterRunGroups, monitorPage }=await load('features/runs/runMonitor.ts');
await test('monitor summaries retain distinct identities, failures and partial progress', () => {
  const groups=groupRunSteps([step('a',0,'succeeded'),step('a',1,'running',{progress:50}),step('a',2),
    step('b',0,'failed'),step('b',1),step('c',0,'cancelled'),step('d',0,'succeeded'),step('e',0,'queued',{sample_label:'a'})]);
  assert.equal(groups.length,5);
  assert.deepEqual(groups.map(g=>g.status),['running','failed','cancelled','succeeded','queued']);
  assert.equal(groups[0].progress,50);
});
await test('monitor filtering searches jobs and nodes without reordering and clamps pages', () => {
  const groups=groupRunSteps([step('a',0,'running',{scheduler:{job_id:98765}}),step('b',0,'failed',{node_name:'node02'})]);
  assert.equal(filterRunGroups(groups,'all','98765')[0].sampleId,'a');
  assert.equal(filterRunGroups(groups,'failed','NODE02')[0].sampleId,'b');
  const items=Array.from({length:1001},(_,i)=>i);
  assert.deepEqual(monitorPage(items,125),{page:125,pages:126,items:[1000]});
  assert.deepEqual(monitorPage([],125),{page:0,pages:1,items:[]});
});
await test('legacy runs without sample IDs retain all steps', () => {
  assert.equal(groupRunSteps([step('a',0,'running',{sample_id:null}),step('a',1,'queued',{sample_id:null})]).length,2);
});
await test('incremental monitor retains unchanged samples and combines outputs', async () => {
  const { mergeRunUpdate }=await load('features/runs/runsTypes.ts');
  const a={step_id:'a',status:'succeeded',artifacts:[{artifact_id:'a'}]};
  const b={step_id:'b',status:'running',artifacts:[]};
  const previous={run_id:'one',steps:[a,b],artifacts:a.artifacts,plan_snapshot:{large:true}};
  const merged=mergeRunUpdate(previous,{run:{run_id:'one',artifacts:[],state_revision:2},reset:false,
    steps:[{...b,status:'succeeded',artifacts:[{artifact_id:'b'}]}]});
  assert.deepEqual(merged.steps.map(s=>s.step_id),['a','b']);
  assert.deepEqual(merged.artifacts.map(a=>a.artifact_id),['a','b']);
  assert.deepEqual(mergeRunUpdate(merged,{run:{run_id:'one',artifacts:[]},reset:true,steps:[b]}).steps,[b]);
});
await test('real task monitor bounds 3000 steps, 1000 outputs and leaves details collapsed', async () => {
  const { RunPanel }=await load('features/runs/RunPanel.tsx');
  const steps=Array.from({length:1000},(_,i)=>[0,1,2].map(j=>step('sample-'+i,j,'succeeded'))).flat();
  const artifacts=Array.from({length:1000},(_,i)=>({artifact_id:String(i),label:'result '+i,absolute_path:'/synthetic/'+i+'/result.mif',sha256:'a'.repeat(64)}));
  const run={run_id:'fixture',status:'succeeded',progress:100,steps,artifacts,output_directory:'/synthetic/output',error_message:''};
  const html=renderToStaticMarkup(createElement(RunPanel,{run,events:[]}));
  assert.equal((html.match(/group\/sample/g)||[]).length,8);
  assert(!/<details[^>]*\sopen(?:[\s=>])/.test(html));
  assert(!html.includes('/synthetic/8/result.mif'));
});
await test('real logs render a bounded page and allow fetching earlier logs', async () => {
  const { RunLogs }=await load('features/runs/RunLogs.tsx');
  const events=Array.from({length:1250},(_,i)=>({event_id:i+1,level:'info',message:'event-'+(i+1)}));
  const html=renderToStaticMarkup(createElement(RunLogs,{runId:'one',events}));
  assert(html.includes('[1250]')); assert(!html.includes('[1230]'));
  assert.equal((html.match(/<p(?:\s|>)/g)||[]).length,20);
});
await test('parameter visibility and conditional requirements honor original typed definitions', async () => {
  const {conditionMatches,inputRequired,visibleParameters}=await load('features/workflows/workflowConditions.ts');
  assert.equal(conditionMatches({act:[true]},{act:false}),false);
  assert.equal(conditionMatches({n:[1]},{n:'1'}),false);
  assert.equal(inputRequired({required:false,required_when:{act:[true]}},{act:false}),false);
  assert.equal(inputRequired({required:false,required_when:{act:[true]}},{act:true}),true);
  assert.deepEqual(visibleParameters({parameters:[{id:'act',default:false},{id:'seed',default:1,visible_when:{act:[true]}}]},{}).map(s=>s.id),['act']);
});
await test('filesystem and output-binding APIs preserve structured scope and revision contracts', async () => {
  const api=await load('features/workspace/workspaceApi.ts');
  const previous=globalThis.fetch;const requests=[];
  globalThis.fetch=async (url,init)=>{requests.push({url:String(url),...init,body:init?.body?JSON.parse(init.body):null});return Response.json({});};
  try {
    await api.listFs({datasetId:'ds',path:'.',sort:'name',filter:'',showHidden:false});
    await api.readFsText(null,'notes.txt',100,'ds');
    await api.runFsAction('mkdir',{workspaceId:null,rootPath:'/allowed/parent',conversationId:'c',path:'/allowed/parent/new'});
    await api.saveWorkflowOutput('wf','.',7,{workspaceId:'out',conversationId:'c'});
    await api.createWorkspace('output','/allowed/new');
    await api.confirmFsOperation('operation','c');
    assert(requests[0].url.startsWith('/api/datasets/ds/files/list?'));
    assert(requests[1].url.includes('/api/datasets/ds/files/read-text?'));
    assert.equal(requests[2].body.root_path,'/allowed/parent');
    assert.deepEqual(requests[3].body,{expected_revision:7,relative_path:'.',workspace_id:'out',conversation_id:'c'});
    assert.equal(requests[4].url,'/api/output-directories');
    assert.deepEqual(requests[5].body,{confirmed:true,conversation_id:'c'});
  } finally { globalThis.fetch=previous; }
});
await test('readonly dataset browser cannot offer output-management actions', async () => {
  const {WorkspacePanel}=await load('features/workspace/WorkspacePanel.tsx');
  const html=renderToStaticMarkup(createElement(WorkspacePanel,{workspaceId:null,conversationId:null,datasetId:'ds',datasetOnly:true,onSelect:async()=>{},onClose(){}}));
  assert(!html.includes(translations.en.workspaceActionMkdir));
  assert(!html.includes(translations.en.workspaceRegisterCurrent));
  assert(html.includes(translations.en.datasetFilesTitle));
});


await test('FastAPI validation errors include actionable field locations', async () => {
  const { request } = await load('api/http.ts');
  const previous = globalThis.fetch;
  globalThis.fetch = async () => Response.json({detail:[{loc:['body','execution','partition'],msg:'Invalid partition'}]}, {status:422});
  try { await assert.rejects(request('/fixture'), /body.execution.partition: Invalid partition/); }
  finally { globalThis.fetch = previous; }
});
await test('monitor mutations cannot be repeated while another action is pending', async () => {
  const { RunPanel } = await load('features/runs/RunPanel.tsx');
  const run={run_id:'pending',status:'failed',progress:0,steps:[step('a',0,'failed')],artifacts:[],output_directory:'/outputs/run'};
  const html=renderToStaticMarkup(createElement(RunPanel,{run,events:[],onRetry(){},onResume(){},retryPending:true,controlsPending:true}));
  assert(!html.includes(translations.en.runRetryStep));
  assert(!html.includes(translations.en.runResumeMonitor));
  assert.match(html, /<button[^>]*disabled=""[^>]*>[\s\S]*?Retry unfinished steps<\/button>/);
});
await test('resume is available only for active Slurm runs; bulk retry only for failed or cancelled runs', async () => {
  const { RunPanel } = await load('features/runs/RunPanel.tsx');
  for (const backend of ['local', 'slurm']) for (const status of ['queued','running','cancel_requested','failed','cancelled','succeeded']) {
    const run={run_id:'matrix',status,execution:{backend,partition:'all'},progress:0,steps:[],artifacts:[],output_directory:'/outputs/test'};
    const html=renderToStaticMarkup(createElement(RunPanel,{run,events:[],onRetry(){},onResume(){},onCancel(){}}));
    assert.equal(html.includes(translations.en.runResumeMonitor), backend==='slurm' && ['queued','running','cancel_requested'].includes(status), `${backend}/${status}`);
    assert.equal(html.includes(translations.en.runRetryIncomplete), ['failed','cancelled'].includes(status), `${backend}/${status}`);
  }
});
await test('an unrelated pending control cannot disable stopping an active run', async () => {
  const { RunPanel } = await load('features/runs/RunPanel.tsx');
  const run={run_id:'active',status:'running',execution:{backend:'local'},progress:10,steps:[],artifacts:[],output_directory:'/outputs/test'};
  const html=renderToStaticMarkup(createElement(RunPanel,{run,events:[],onCancel(){},controlsPending:true}));
  const buttons=html.match(/<button[^>]*>[\s\S]*?<\/button>/g)??[];
  const cancel=buttons.find(button=>button.includes(translations.en.runCancel));
  assert(cancel&&!cancel.includes('disabled=""'));
});
await test('bulk retry preserves the existing empty-step selection API contract', async () => {
  const { retryRun } = await load('features/runs/runsApi.ts');
  const previous=globalThis.fetch; const requests=[];
  globalThis.fetch=async (url,init)=>{requests.push({url:String(url),method:init.method,body:JSON.parse(init.body)});return Response.json({run_id:'retried'});};
  try {
    await retryRun('failed/run',[]);
    assert.deepEqual(requests,[{url:'/api/runs/failed%2Frun/retry',method:'POST',body:{step_ids:[]}}]);
  } finally { globalThis.fetch=previous; }
});
await test('task replies avoid repetitive no-workflow badges and retain a concise clarification cue', async () => {
  const { MessageArea } = await load('features/chat/MessageArea.tsx');
  const base={id:'message',role:'assistant',content:'Fixture reply',interactionMode:'task',workflowRecommended:false};
  const client=new QueryClient({defaultOptions:{queries:{gcTime:Infinity}}});
  const render=extra=>renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(MessageArea,{t,messages:[{...base,...extra}],activeWorkflowId:null,activeTask:null,sending:false,loadingOlder:false,hasOlder:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null},bottomRef:{current:null}})));
  assert(render({clarificationRequired:true}).includes('More information needed'));
  assert(!render({clarificationRequired:false}).includes(t.chatNoWorkflowNeeded));
  for(const extra of [{interactionMode:'chat'}, {role:'user'}, {workflowRecommended:true}, {workflowRecommended:undefined}, {workflowId:'deferred'}]) {
    const html=render(extra);
    assert(!html.includes(t.chatNeedsClarification) && !html.includes(t.chatNoWorkflowNeeded));
  }
  client.clear();
});

const { positionPopup } = await load('ui/popupPosition.ts');
await test('workflow launch requires dry-run execution approval and no active run', async () => {
  const { WorkflowCard } = await load('features/workflows/WorkflowCard.tsx');
  for (const scenario of [
    {status:'queued',enabled:true,valid:true,start:false},
    {status:'running',enabled:true,valid:true,start:false},
    {status:'cancel_requested',enabled:true,valid:true,start:false},
    {status:'failed',enabled:true,valid:true,start:true},
    {status:'succeeded',enabled:true,valid:true,start:true},
    {status:null,enabled:false,valid:true,start:false},
    {status:null,enabled:true,valid:false,start:false},
    {status:null,enabled:true,valid:true,start:true},
    {status:null,enabled:true,valid:true,start:true,wfStatus:'ready',canConfirm:true},
    {status:null,enabled:true,valid:false,start:false,wfStatus:'awaiting_parameter_review',canConfirm:false}
  ]) {
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    const wf={workflow_id:'launch',revision:1,title:'Fixture',status:scenario.wfStatus??'confirmed',can_confirm:scenario.canConfirm,execution_mode:'runner',steps:[],dataset:null,
      workspace_id:'ws',workspace_root_path:'/synthetic',requested_capabilities:[],warnings:[],locked_at:'now',dry_run_report:{valid:scenario.valid,execution_enabled:scenario.enabled,commands:[],warnings:[],quality_review_required:true}};
    const run=scenario.status?{run_id:'run',status:scenario.status,execution:{backend:'slurm'},progress:0,steps:[],artifacts:[],output_directory:'/synthetic'}:null;
    client.setQueryData(['workflow-run','launch'],run);
    try {
      const html=renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(WorkflowCard,{workflowId:'launch',initialWorkflow:wf})));
      const buttons=[...html.matchAll(/<button\b[^>]*>([\s\S]*?)<\/button>/g)].map(m=>m[1].replace(/<[^>]+>/g,''));
      assert.equal(buttons.some(text=>text===translations.en.workflowStartRun||text===translations.en.workflowRunAgain),scenario.start,JSON.stringify(scenario));
      assert(!buttons.includes(translations.en.workflowConfirm));
      assert(!buttons.includes(translations.en.workflowReviewParameters));
      assert(!html.includes('I have reviewed'));
      if (scenario.start) {
        assert(html.includes('Server：Slurm'));
        assert(!html.includes('id="execution-launch"') && !html.includes('id="samples-launch"'));
        assert(!html.includes('<option value="local"') && !html.includes('<option value="single"'));
      }
    } finally { client.clear(); }
  }
});
const viewport = {left:0,top:0,width:390,height:500};
await test('menu flips above a bottom-edge trigger and stays inside the viewport', () => {
  const p=positionPopup({left:352,right:376,top:458,bottom:482},{width:144,height:120},viewport);
  assert.equal(p.side,'top'); assert.equal(p.top,332); assert.equal(p.left,232);
});
await test('menu shifts right at the left edge while retaining bottom placement', () => {
  const p=positionPopup({left:12,right:36,top:40,bottom:64},{width:144,height:120},viewport);
  assert.equal(p.side,'bottom'); assert.equal(p.left,8); assert.equal(p.top,70);
});
await test('long confirmation is capped for scrolling and honors a reduced visible viewport', () => {
  const p=positionPopup({left:85,right:109,top:310,bottom:334},{width:240,height:700},{left:20,top:150,width:300,height:240});
  assert.equal(p.side,'top'); assert.equal(p.maxHeight,146); assert.equal(p.top,158);
  assert(p.left>=28 && p.left+240<=312);
});
await test('rail label flips left and shifts vertically near the bottom-right edge', () => {
  const p=positionPopup({left:350,right:386,top:458,bottom:494},{width:180,height:40},viewport,'right','center');
  assert.equal(p.side,'left'); assert.equal(p.left,164); assert.equal(p.top,452);
});
  const {parameterValues,parameterEnabled,parameterRequired,visibleParameters}=await server.ssrLoadModule('/src/features/workflows/workflowConditions.ts');
  await test('conditional parameter requirements do not confuse zero, false and omission',()=>{
    const mode={id:'mode',type:'string',required:true,default:'auto'};
    const abs={id:'abs',type:'number',default:null,required:false,visible_when:{mode:['absolute']},required_when:{mode:['absolute']}};
    const method={parameters:[mode,abs]};
    const values=parameterValues(method,{mode:'absolute',abs:0});
    assert.equal(values.abs,0);assert.equal(parameterRequired(abs,values),true);
    assert.equal(parameterEnabled(abs,{mode:'auto'}),false);
    assert.equal(visibleParameters(method,{mode:'auto',abs:5}).some(p=>p.id==='abs'),false);
    assert.equal(parameterValues({parameters:[{id:'optional',default:42}]},{optional:null}).optional,undefined);
  });

await test('Skill catalog distinguishes atomic methods, pipelines and unavailable capabilities', async () => {
  const { SkillMethods } = await load('features/skills/SkillMethods.tsx');
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  client.setQueryData(['skill-methods','dwi-skill','','method','en'],{pages:[{items:[{entry_kind:'method',method_id:'mrtrix.tckgen',capability:'dwi.tractography',label:'独立追踪',role:'复用已有 FOD',runner_id:'mrtrix.tckgen',execution_mode:'runner',input_count:3,output_count:1,parameter_count:10}],total:1,next_cursor:null}],pageParams:['']});
  const html=renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(SkillMethods,{skillId:'dwi-skill'})));
  for(const label of ['Methods','Pipelines','Planning capabilities','复用已有 FOD','mrtrix.tckgen']) assert.ok(html.includes(label),label);
  client.clear();
});
await test('method detail API uses the canonical identity and distinct pipeline endpoint', async () => {
  const {fetchSkillMethod,fetchSkillMethods}=await load('features/skills/skillsApi.ts');
  const original=globalThis.fetch;const urls=[];
  globalThis.fetch=async url=>{urls.push(String(url));return new Response('{}',{status:200,headers:{'content-type':'application/json'}});};
  try {
    await fetchSkillMethod('method','mrtrix.tckgen');
    await fetchSkillMethod('pipeline','act-sift2');
    await fetchSkillMethods('dwi-skill','','',undefined,'pipeline');
    assert.ok(urls[0].endsWith('/api/methods/mrtrix.tckgen'));
    assert.ok(urls[1].endsWith('/api/pipelines/act-sift2'));
    assert.ok(urls[2].includes('kind=pipeline'));
  } finally {globalThis.fetch=original;}
});
} finally { await server.close(); }
