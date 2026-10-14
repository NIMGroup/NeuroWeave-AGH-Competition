import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,
  server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {installTransport,subscribeWorkbench}=await server.ssrLoadModule('/src/features/agh/host.ts');
  const {request,setRequestTransport}=await server.ssrLoadModule('/src/api/http.ts');
  const {mergeRunSummary}=await server.ssrLoadModule('/src/features/runs/runsTypes.ts');
  const path='/api/workflows/wf_test/runs', init={method:'POST',body:JSON.stringify({expected_revision:9})};
  function fixture() {
    const state={session:'one',prepares:0,submits:0,prompts:0,queries:0,opens:[],
      action:{action_id:'action_test',action_digest:'a'.repeat(64),state:'prepared'}};
    const host={session:{getSnapshot:()=>state.session,commands:{prompt:async()=>{state.prompts++;throw Error('Model must not gate submission');}}},
      commands:{registerEffect(){},execute:async(id)=>{
        if(id==='nw-action-prepare'){state.prepares++;await state.onPrepare?.();return{ok:true,result:state.action};}
        assert.equal(id,'nw-action-submit');state.submits++;await state.onSubmit?.();
        return{ok:true,result:state.action};
      }},agnes:{services:{call:async(name)=>{assert.equal(name,'nw.action.status');state.queries++;return{ok:true,result:state.action};}}},
      effect:factory=>{state.dispose=factory();}};
    installTransport(host);state.off=subscribeWorkbench(open=>state.opens.push(open));return state;
  }
  const complete=state=>{state.action={...state.action,state:'completed',result:{status:200,body:{run_id:'run_actual',status:'queued'}}};};
  const close=state=>{state.off();state.dispose();setRequestTransport(null);};
  await test('Run uses the official AGH effect and its persisted receipt without a model or closing the workbench',async()=>{
    const state=fixture();state.onSubmit=async()=>complete(state);
    try {const run=await request(path,init);assert.equal(run.run_id,'run_actual');assert.equal(state.submits,1);
      assert.equal(state.prompts,0);assert.deepEqual(state.opens,[]);} finally {close(state);}
  });
  await test('a lost AGH receipt reconciles the same action without another submission',async()=>{
    const state=fixture();state.onSubmit=async()=>{complete(state);throw Error('Lost effect response');};
    try {assert.equal((await request(path,init)).run_id,'run_actual');assert.equal(state.queries,1);
      assert.equal(state.prepares,1);assert.equal(state.submits,1);} finally {close(state);}
  });
  await test('concurrent Run clicks share the same frozen submission',async()=>{
    const state=fixture();let release;state.onSubmit=()=>new Promise(resolve=>{release=()=>{complete(state);resolve();};});
    try {const first=request(path,init),second=request(path,init);
      while(!release) await new Promise(resolve=>setTimeout(resolve,1));release();
      assert.equal((await first).run_id,(await second).run_id);assert.equal(state.prepares,1);assert.equal(state.submits,1);
    } finally {close(state);}
  });
  await test('switching during preparation cannot submit the action into a different session',async()=>{
    const state=fixture();state.onPrepare=async()=>{state.session='two';};
    try {await assert.rejects(request(path,init),/尚未提交/);assert.equal(state.submits,0);} finally {close(state);}
  });
  await test('uncertain dispatch stays attached to its original action and cannot be repeated by clicking again',async()=>{
    const state=fixture();state.onSubmit=async()=>{state.action={...state.action,state:'unknown'};};
    try {await assert.rejects(request(path,init),/待核对/);await assert.rejects(request(path,init),/待核对/);
      assert.equal(state.prepares,1);assert.equal(state.submits,1);} finally {close(state);}
  });
  await test('run discovery retains monitored details, ignores stale summaries, and adopts newly submitted runs',()=>{
    const previous={run_id:'old',state_revision:4,status:'running',steps:[{step_id:'sample-one',scheduler:{job_id:123}}],artifacts:[{artifact_id:'output'}]};
    assert.equal(mergeRunSummary(null,null),null);assert.equal(mergeRunSummary(previous,null),previous);
    assert.equal(mergeRunSummary(previous,{...previous,state_revision:3,status:'queued',steps:[]}),previous);
    const fresh=mergeRunSummary(previous,{...previous,state_revision:5,status:'succeeded',steps:[],artifacts:[]});
    assert.equal(fresh.status,'succeeded');assert.deepEqual(fresh.steps,previous.steps);assert.deepEqual(fresh.artifacts,previous.artifacts);
    const next={run_id:'new',steps:[],artifacts:[],status:'queued'};assert.equal(mergeRunSummary(previous,next),next);
  });
} finally {await server.close();}
