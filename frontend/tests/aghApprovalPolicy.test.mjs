import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,
  server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {prepareWorkflowCheck,confirmWorkflowForRun}=await server.ssrLoadModule('/src/features/workflows/workflowsApi.ts');
  const {setRequestTransport}=await server.ssrLoadModule('/src/api/http.ts');
  const base={workflow_id:'wf_test',revision:1,can_lock:true};
  await test('preflight advances method and parameter validation with current revisions, without computing',async()=>{
    const calls=[],updates=[];
    setRequestTransport(async(path,options)=>{
      const body=JSON.parse(options.body);calls.push({path,revision:body.expected_revision});
      const value=path.endsWith('/lock')?{...base,revision:2,can_lock:false,can_review_parameters:true}
        :{...base,revision:3,can_lock:false,can_review_parameters:false,can_dry_run:true};
      return {status:200,body:value};
    });
    const checked=await prepareWorkflowCheck(base,value=>updates.push(value.revision));
    assert.deepEqual(calls,[{path:'/api/workflows/wf_test/lock',revision:1},
      {path:'/api/workflows/wf_test/review-parameters',revision:2}]);
    assert.deepEqual(updates,[2,3]);assert.equal(checked.revision,3);
  });
  await test('missing inputs prevent preflight continuation and never dispatch a run',async()=>{
    let calls=0;
    setRequestTransport(async()=>{calls++;return{status:200,body:{...base,revision:2,can_lock:false,can_dry_run:false}}});
    await assert.rejects(prepareWorkflowCheck(base,()=>{}),/必需输入/);assert.equal(calls,1);
  });
  await test('Run confirms only a valid executable plan at its current revision',async()=>{
    const calls=[],ready={...base,revision:8,status:'ready',can_confirm:true,
      dry_run_report:{valid:true,execution_enabled:true,quality_review_required:true}};
    setRequestTransport(async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});
      return{status:200,body:{...ready,revision:9,status:'confirmed'}}});
    const confirmed=await confirmWorkflowForRun(ready);
    assert.equal(confirmed.revision,9);
    assert.deepEqual(calls,[{path:'/api/workflows/wf_test/confirm',body:{acknowledge_quality:true,expected_revision:8}}]);
    await confirmWorkflowForRun(confirmed);assert.equal(calls.length,1);
    await assert.rejects(confirmWorkflowForRun({...ready,dry_run_report:{valid:false,execution_enabled:true}}),/预检/);
    await assert.rejects(confirmWorkflowForRun({...ready,dry_run_report:{valid:true,execution_enabled:false}}),/预检/);
    assert.equal(calls.length,1);
  });
} finally {const {setRequestTransport}=await server.ssrLoadModule('/src/api/http.ts');setRequestTransport(null);await server.close();}
