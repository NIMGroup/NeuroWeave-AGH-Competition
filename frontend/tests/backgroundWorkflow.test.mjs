import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const descriptors=Object.fromEntries(['localStorage','fetch'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>null,setItem(){}}});
const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,
  server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {runDryRun,runWorkflowLifecycleAction,completeDependencies}=await server.ssrLoadModule('/src/features/workflows/workflowsApi.ts');
  const {runOperation}=await server.ssrLoadModule('/src/api/operations.ts');
  const {startRun}=await server.ssrLoadModule('/src/features/runs/runsApi.ts');
  const {adoptConflict,adoptWorkflow}=await server.ssrLoadModule('/src/features/workflows/useWorkflows.ts');
  const {QueryClient}=await import('@tanstack/react-query');
  const workflow={workflow_id:'wf-A',revision:5};
  const calls=[];
  globalThis.fetch=async(path,init={})=>{
    calls.push({path,method:init.method??'GET',body:init.body});
    return new Response(JSON.stringify(init.method==='POST'
      ? {operation_id:'op-A',status:'queued',message:'checking'}
      : {operation_id:'op-A',status:'succeeded',message:'done',result:workflow}));
  };
  await test('dry-run starts one backend job, reports progress and never submits computation',async()=>{
    const progress=[];
    assert.deepEqual(await runDryRun('wf-A',4,{onProgress:row=>progress.push(row.status)}),workflow);
    assert.deepEqual(progress,['queued','succeeded']);
    assert.equal(calls[0].path,'/api/workflows/wf-A/dry-run-jobs');
    assert.equal(calls.filter(row=>row.method==='POST').length,1);
    assert(!calls.some(row=>row.path.includes('/runs')));
  });
  await test('replanning and dependency preparation use the same detached job protocol',async()=>{
    calls.length=0;
    assert.deepEqual(await runWorkflowLifecycleAction('wf-A','replan',4),workflow);
    assert.deepEqual(await completeDependencies('wf-A',4),workflow);
    assert.deepEqual(calls.filter(row=>row.method==='POST').map(row=>row.path),[
      '/api/workflows/wf-A/replan-jobs','/api/workflows/wf-A/complete-dependencies-jobs']);
  });
  await test('late completion updates its own workflow and cannot overwrite a newer revision',()=>{
    const client=new QueryClient();
    client.setQueryData(['workflow','wf-A'],{workflow_id:'wf-A',revision:9});
    client.setQueryData(['workflow','wf-B'],{workflow_id:'wf-B',revision:12});
    adoptWorkflow(client,workflow);
    assert.equal(client.getQueryData(['workflow','wf-A']).revision,9);
    assert.equal(client.getQueryData(['workflow','wf-B']).revision,12);
  });
  await test('manual local Run preserves the preview layout and uses local resource defaults',async()=>{
    calls.length=0;
    await startRun('wf-A',4,{backend:'local',output_layout:'sample_steps',cpus:128,memory_gb:512});
    assert.equal(calls[0].path,'/api/workflows/wf-A/runs');
    assert.deepEqual(JSON.parse(calls[0].body).execution,{backend:'local',output_layout:'sample_steps'});
  });
  await test('background revision errors preserve the authoritative workflow snapshot',async()=>{
    globalThis.fetch=async()=>new Response(JSON.stringify({operation_id:'op-conflict',status:'failed',
      message:'revision changed',error:{message:'revision changed',current:{workflow_id:'wf-A',revision:10}}}));
    const client=new QueryClient();
    let error;
    try { await runOperation('/job',{method:'POST'}); } catch(failure) {error=failure;}
    assert.equal(error.status,409);
    assert(adoptConflict(client,error));
    assert.equal(client.getQueryData(['workflow','wf-A']).revision,10);
  });
  await test('an explicitly stopped check finishes as cancellation',async()=>{
    globalThis.fetch=async()=>new Response(JSON.stringify({operation_id:'op-stopped',status:'cancelled',message:'检查已停止'}));
    await assert.rejects(runOperation('/job',{method:'POST'}),error=>error.name==='AbortError'&&error.message==='检查已停止');
  });
} finally {
  await server.close();
  for(const [key,value] of Object.entries(descriptors)) {
    if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];
  }
}
