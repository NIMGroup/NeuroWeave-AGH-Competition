import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {readFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
const root=resolve(import.meta.dirname,'..');
const server=await createServer({root,configFile:false,server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try{
const {useLanguageStore}=await server.ssrLoadModule('/src/stores/languageStore.ts');
useLanguageStore.setState({language:'zh'});
const graph=await server.ssrLoadModule('/src/features/library/pipelineGraph.ts');
const method=JSON.parse(await readFile(resolve(root,'../methods/mrtrix3/mrtrix.command.mrconvert/method.json'),'utf8'));
method.runtime_status={available:true};
const definitions={[method.id]:method};
const node=id=>({id,method_id:method.id,parameters:{nthreads:1},inputs:{},position:{x:50,y:60}});
await test('pointer connections hit the editor shadow tree and refuse foreign or disabled ports', async()=>{
  const {canvasInputAtPoint}=await server.ssrLoadModule('/src/features/library/canvasHitTest.ts');
  const port={disabled:false}; let inside=true; const hits=[];
  const root={elementFromPoint:(x,y)=>{hits.push([x,y]);return {closest:()=>port};}};
  const viewport={getRootNode:()=>root,contains:element=>inside && element===port};
  assert.equal(canvasInputAtPoint(viewport,150,220),port);
  assert.deepEqual(hits,[[150,220]]);
  inside=false; assert.equal(canvasInputAtPoint(viewport,150,220),null);
  inside=true; port.disabled=true; assert.equal(canvasInputAtPoint(viewport,150,220),null);
  assert.equal(canvasInputAtPoint(null,150,220),null);
  root.elementFromPoint=()=>null; assert.equal(canvasInputAtPoint(viewport,150,220),null);
});
await test('HTTP crypto without randomUUID can create stable unique step IDs',()=>{
  const httpCrypto={getRandomValues:words=>words.fill(17)};
  assert.equal(httpCrypto.randomUUID,undefined);
  const ids=Array.from({length:1000},()=>graph.createNodeId(httpCrypto));
  assert.equal(new Set(ids).size,1000);
  assert.ok(ids.every(id=>/^[a-z][a-z0-9_]{0,50}$/.test(id)));
  assert.match(graph.createNodeId(null),/^[a-z][a-z0-9_]{0,50}$/);
});
await test('canvas ordering follows dependencies even when target was added first',()=>{
  const steps=graph.connectNodes([node('b'),node('a')],definitions,'a','output','b','input');
  assert.deepEqual(graph.executionDraft({name:'test',steps}).steps.map(s=>s.id),['a','b']);
  assert.ok(graph.executionDraft({name:'test',steps}).steps.every(s=>s.position),'source editor positions are saved; the backend compiler strips them from executable stages');
  assert.deepEqual(steps[0].position,{x:50,y:60});
  assert.deepEqual(steps[0].parameters,{nthreads:1});
  assert.throws(()=>graph.connectNodes(steps,definitions,'b','output','a','input'),/循环/);
  assert.deepEqual(steps[1].inputs,{},'failed connection cannot mutate existing graph');
});
await test('ports reject missing definitions, incompatible types and self loops',()=>{
  const other={...method,id:'other',input_slots:[{...method.input_slots[0],type:'tractogram'}]};
  assert.throws(()=>graph.connectNodes([node('a')],definitions,'a','output','a','input'),/自身/);
  assert.throws(()=>graph.connectNodes([node('a'),node('b')],{},'a','output','b','input'),/尚未加载/);
  assert.throws(()=>graph.connectNodes([node('a'),{...node('b'),method_id:'other'}],{...definitions,other},'a','output','b','input'),/类型不匹配/);
});
await test('deleting node releases its consumers as external inputs without losing parameters',()=>{
  const steps=graph.connectNodes([node('a'),node('b'),node('c')],definitions,'a','output','b','input');
  const next=graph.removeNode(steps,'a');
  assert.deepEqual(next.map(n=>n.id),['b','c']);assert.deepEqual(next[0].inputs,{});
  assert.equal(next[0].parameters.nthreads,1);
  const arranged=graph.arrangeNodes(steps,definitions);
  assert.ok(arranged[1].position.x>arranged[0].position.x);
});
await test('layout is restored per saved version and storage failure does not lose a saved pipeline',async()=>{
  const {rememberLayout,restoreLayout}=await server.ssrLoadModule('/src/features/library/pipelineLayout.ts');
  const records=new Map();const storage={getItem:key=>records.get(key)??null,setItem:(key,value)=>records.set(key,value)};
  const draft={name:'test',steps:[node('a')]};const saved={id:'p',version:'1',editor:graph.executionDraft(draft)};
  assert.deepEqual(rememberLayout(saved,draft,storage).editor.steps[0].position,{x:50,y:60});
  assert.deepEqual(restoreLayout(saved,storage).editor.steps[0].position,{x:50,y:60});
  assert.deepEqual(restoreLayout({...saved,version:'2'},storage).editor.steps[0].position,{x:50,y:60},'server source positions survive a new browser or version');
  const failed=rememberLayout(saved,draft,{setItem:()=>{throw new Error('quota');},getItem:()=>null});
  assert.equal(failed.id,'p');assert.match(failed.layout_warning,/已保存到服务器/);
});
await test('real canvas and editor render nodes, ports, links and parameters with cached definitions',async()=>{
  const {PipelineCanvas}=await server.ssrLoadModule('/src/features/library/PipelineCanvas.tsx');
  const {PipelineEditor}=await server.ssrLoadModule('/src/features/library/PipelineEditor.tsx');
  const steps=graph.connectNodes([node('a'),node('b')],definitions,'a','output','b','input');
  const noop=()=>{};
  const canvas=renderToStaticMarkup(createElement(PipelineCanvas,{steps,definitions,selected:'a',disabled:false,onSelect:noop,onMove:noop,onAdd:noop,onConnect:noop,onDisconnect:noop,onArrange:noop}));
  assert.equal((canvas.match(/data-pipeline-node=/g)??[]).length,2);
  assert.match(canvas,/marker-end="url\(#pipeline-arrow\)"/);
  assert.match(canvas,/data-pipeline-input="input"/);
  assert.match(canvas,/自动排列/);
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  client.setQueryData(['library-method',method.id,'en'],method);
  const html=renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(PipelineEditor,{initial:{id:'saved',version:'1',editor:{name:'saved graph',description:'',steps}},onClose:noop,onSaved:noop})));
  assert.match(html,/Pipeline 节点编排/);assert.match(html,/步骤参数/);assert.match(html,/nthreads/);
  assert.doesNotMatch(html,/Pipeline 编辑器遇到错误/);
  client.clear();
});
await test('save sends dependency-ordered editable source with durable coordinates',async()=>{
  const api=await server.ssrLoadModule('/src/features/library/libraryApi.ts');
  const prior=globalThis.fetch;let body;
  globalThis.fetch=async(_url,options)=>{body=JSON.parse(options.body);return new Response(JSON.stringify({id:'p',version:'1',editor:body}),{status:200,headers:{'Content-Type':'application/json'}});};
  try{
    const steps=graph.connectNodes([node('b'),node('a')],definitions,'a','output','b','input');
    const saved=await api.save({name:'test',description:'',steps});
    assert.equal(saved.id,'p');assert.deepEqual(body.steps.map(s=>s.id),['a','b']);
    assert.ok(body.steps.every(s=>s.position));
    assert.deepEqual(body.steps[1].inputs.input,{step_id:'a',output_slot_id:'output'});
  }finally{globalThis.fetch=prior;}
});
await test('WM, GM and CSF response ports connect; changing algorithm preserves links and reports inactive outputs',async()=>{
  const load=async name=>JSON.parse(await readFile(resolve(root,`../methods/mrtrix3/${name}/method.json`),'utf8'));
  const response=await load('mrtrix.dwi2response'),fod=await load('mrtrix.dwi2fod');
  const defs={[response.id]:response,[fod.id]:fod};
  let steps=[{id:'response',method_id:response.id,parameters:{},inputs:{}},{id:'fod',method_id:fod.id,parameters:{algorithm:'msmt_csd'},inputs:{}}];
  for(const slot of ['wm_response','gm_response','csf_response'])steps=graph.connectNodes(steps,defs,'response',slot,'fod',slot);
  assert.deepEqual(Object.keys(steps[1].inputs),['wm_response','gm_response','csf_response']);
  assert.deepEqual(graph.conditionalConnectionIssues(steps,defs),[]);
  steps[0]={...steps[0],parameters:{algorithm:'tournier'}};
  const issues=graph.conditionalConnectionIssues(steps,defs);
  assert.equal(issues.length,2);assert.ok(issues.every(s=>s.includes('Dhollander')));
  assert.equal(Object.keys(steps[1].inputs).length,3);
  steps[0]={...steps[0],parameters:{algorithm:'dhollander'}};
  assert.deepEqual(graph.conditionalConnectionIssues(steps,defs),[]);
});

await test('parameter changes update real response canvas ports while retaining inactive connected ports',async()=>{
  const response=JSON.parse(await readFile(resolve(root,'../methods/mrtrix3/mrtrix.dwi2response/method.json'),'utf8'));
  const {activeOutputSlots}=await server.ssrLoadModule('/src/features/workflows/workflowConditions.ts');
  const {PipelineCanvas}=await server.ssrLoadModule('/src/features/library/PipelineCanvas.tsx');
  const n={id:'response',method_id:response.id,parameters:{},inputs:{}};
  const noop=()=>{};
  const render=steps=>renderToStaticMarkup(createElement(PipelineCanvas,{steps,definitions:{[response.id]:response},selected:n.id,disabled:false,onSelect:noop,onMove:noop,onAdd:noop,onConnect:noop,onDisconnect:noop,onArrange:noop}));
  assert.equal(activeOutputSlots(response,{}).length,4);
  assert.match(render([n]),/灰质响应函数/);
  n.parameters={algorithm:'tournier'};
  assert.deepEqual(activeOutputSlots(response,n.parameters).map(s=>s.id),['wm_response','log']);
  assert.doesNotMatch(render([n]),/灰质响应函数/);
  const target={id:'target',method_id:'unknown',parameters:{},inputs:{gm:{step_id:n.id,output_slot_id:'gm_response'}}};
  assert.deepEqual(graph.canvasOutputSlots(n,response,[n,target]).map(s=>[s.id,s.available]),[['wm_response',true],['gm_response',false],['log',true]]);
  assert.match(render([n,target]),/当前不生成/);
  n.parameters={algorithm:'dhollander'};
  assert.equal(graph.canvasOutputSlots(n,response,[n,target]).length,4);
  assert.doesNotMatch(render([n,target]),/当前不生成/);
  assert.deepEqual(activeOutputSlots(response,{algorithm:null}).map(s=>s.id),['wm_response','log']);
  const inactive={...response,parameters:response.parameters.map(p=>p.id==='algorithm'?{...p,enabled_when:{missing:[true]}}:p)};
  assert.deepEqual(activeOutputSlots(inactive,{}).map(s=>s.id),['wm_response','log']);
});

await test('FOD inputs follow algorithm selection without losing optional inputs or saved connections',async()=>{
  const load=async id=>JSON.parse(await readFile(resolve(root,`../methods/mrtrix3/${id}/method.json`),'utf8'));
  const fod=await load('mrtrix.dwi2fod');
  const {displayedInputSlots}=await server.ssrLoadModule('/src/features/workflows/workflowConditions.ts');
  const {PipelineCanvas}=await server.ssrLoadModule('/src/features/library/PipelineCanvas.tsx');
  const noop=()=>{};
  const n={id:'fod',method_id:fod.id,parameters:{},inputs:{}};
  const render=(showConditionalInputs=false)=>renderToStaticMarkup(createElement(PipelineCanvas,{steps:[n],definitions:{[fod.id]:fod},selected:n.id,disabled:false,showConditionalInputs,onSelect:noop,onMove:noop,onAdd:noop,onConnect:noop,onDisconnect:noop,onArrange:noop}));
  assert.equal(displayedInputSlots(fod,{}).length,3);
  assert.deepEqual(displayedInputSlots(fod,{gradient_source:'fsl'}).filter(s=>s.requiredNow).map(s=>s.id),['dwi','mask','bvecs','bvals','wm_response']);
  assert.doesNotMatch(render(),/data-pipeline-input="gm_response"/);
  assert.match(render(true),/data-pipeline-input="gm_response"/);
  n.parameters={algorithm:'msmt_csd'};
  assert.equal(displayedInputSlots(fod,n.parameters).length,5);
  assert.match(render(),/data-pipeline-input="gm_response"/);
  assert.ok(displayedInputSlots(fod,n.parameters).filter(s=>['gm_response','csf_response'].includes(s.id)).every(s=>s.requiredNow));
  n.inputs={gm_response:{step_id:'response',output_slot_id:'gm_response'}};
  n.parameters={algorithm:'csd'};
  assert.match(render(),/data-pipeline-input="gm_response"/);
  assert.doesNotMatch(render(),/data-pipeline-input="csf_response"/);
  assert.match(render(),/当前非必需/);
  assert.deepEqual(n.inputs.gm_response,{step_id:'response',output_slot_id:'gm_response'});
  const sift=await load('mrtrix.command.tcksift2');
  assert.ok(displayedInputSlots(sift,{fd_scale_gm:false},{},true).some(s=>s.id==='act'&&!s.requiredNow),'conditionally required is still selectable when optional');
  assert.ok(displayedInputSlots(sift,{fd_scale_gm:true}).some(s=>s.id==='act'&&s.requiredNow));
  const unconditionalOptional={...fod,input_slots:[{...fod.input_slots[0],id:'optional',required:false,required_when:{}}]};
  assert.equal(displayedInputSlots(unconditionalOptional,{}).length,1,'ordinary optional inputs stay visible');
  assert.equal(displayedInputSlots(fod,{algorithm:null}).length,3,'explicit omission does not restore defaults');
});
await test('real workflow input cards react to parameters and retain historical file bindings',async()=>{
  const fod=JSON.parse(await readFile(resolve(root,'../methods/mrtrix3/mrtrix.dwi2fod/method.json'),'utf8'));
  const {StepArtifacts}=await server.ssrLoadModule('/src/features/workflows/StepArtifacts.tsx');
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  const noop=()=>{};
  const render=(selectedParameters,bindings={})=>renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(StepArtifacts,{method:fod,step:{id:'fod',input_bindings:bindings,missing_required_inputs:['gm_response','csf_response']},workflow:{workflow_id:'test',dataset:null},selectedParameters,busySlotId:null,busyCompleting:false,disabled:false,locked:true,onBind:noop,onBindHistory:noop,onCompleteDependencies:noop})));
  try{
    assert.doesNotMatch(render({algorithm:'csd'}),/灰质响应函数/);
    assert.match(render({algorithm:'msmt_csd'}),/灰质响应函数/);
    const bound=render({algorithm:'csd'},{gm_response:{source:'existing_path',history:{run_id:'old'},label:'历史灰质响应'}});
    assert.match(bound,/历史灰质响应/);assert.match(bound,/Not required with the current parameters/);
    assert.doesNotMatch(bound,/脑脊液响应函数/);
  }finally{client.clear();}
});
await test('input sharing merges groups without ordering steps and supports detach and node removal',async()=>{
  const load=async id=>JSON.parse(await readFile(resolve(root,`../methods/mrtrix3/${id}/method.json`),'utf8'));
  const response=await load('mrtrix.dwi2response'),fod=await load('mrtrix.dwi2fod');
  const defs={[response.id]:response,[fod.id]:fod};
  const port=(step_id,input_slot_id='dwi')=>({step_id,input_slot_id});
  const a=port('a'),b=port('b'),c=port('c');
  let draft={name:'Shared',description:'',steps:[{id:'a',method_id:response.id,parameters:{},inputs:{}},{id:'b',method_id:fod.id,parameters:{},inputs:{}},{id:'c',method_id:fod.id,parameters:{},inputs:{}}]};
  draft=graph.shareInputs(draft,defs,a,b);draft=graph.shareInputs(draft,defs,b,c);
  assert.deepEqual(draft.shared_inputs,[[a,b,c]]);
  assert.deepEqual(graph.executionDraft(draft).steps.map(s=>s.id),['a','b','c']);
  assert.ok(draft.steps.every(s=>!Object.keys(s.inputs).length));
  assert.deepEqual(graph.executionDraft(draft).shared_inputs,[[a,b,c]]);
  assert.deepEqual(graph.detachSharedInput(draft.shared_inputs,b),[[a,c]]);
  assert.deepEqual(graph.retainSharedNodes(draft.shared_inputs,draft.steps.slice(1)),[[b,c]]);
  assert.throws(()=>graph.shareInputs(draft,defs,a,port('b','mask')),/类型/);
  const connected={...draft,steps:draft.steps.map(s=>s.id==='b'?{...s,inputs:{dwi:{step_id:'a',output_slot_id:'out'}}}:s)};
  assert.throws(()=>graph.shareInputs(connected,defs,a,b),/上游输出/);
  const {PipelineCanvas}=await server.ssrLoadModule('/src/features/library/PipelineCanvas.tsx');const noop=()=>{};
  const html=renderToStaticMarkup(createElement(PipelineCanvas,{steps:draft.steps,definitions:defs,sharedInputs:draft.shared_inputs,selected:'a',disabled:false,onShareInputs:noop,onDetachSharedInput:noop,onSelect:noop,onMove:noop,onAdd:noop,onConnect:noop,onDisconnect:noop,onArrange:noop}));
  assert.equal((html.match(/aria-label="共享输入连线"/g)??[]).length,2);
  assert.match(html,/stroke-dasharray="6 4"/);
  const {inputRequired,displayedInputSlots}=await server.ssrLoadModule('/src/features/workflows/workflowConditions.ts');
  const slot={...fod.input_slots[0],required:false,required_when:{},required_when_any:[{a:[true]},{b:[true]}]};
  assert.equal(inputRequired(slot,{a:false,b:true}),true);assert.equal(inputRequired(slot,{a:false,b:false}),false);
  const sharedMethod={parameters:[{id:'a',default:false},{id:'b',default:false}],input_slots:[slot]};
  assert.equal(displayedInputSlots(sharedMethod,{}).length,0);assert.equal(displayedInputSlots(sharedMethod,{b:true}).length,1);
});

await test('method verification keeps runtime availability separate from execution proof', async()=>{
  const {MethodVerification}=await server.ssrLoadModule('/src/features/skills/MethodVerification.tsx');
  const value={contract:{status:'passed',errors:[]},documentation:{status:'complete',missing:[]},execution:{status:'not_recorded'}};
  const html=renderToStaticMarkup(createElement(MethodVerification,{value}));
  assert.match(html,/未记录当前版本运行验证|No execution evidence/);
  assert.doesNotMatch(html,/运行测试通过|Execution test passed/);
  value.execution={status:'stale'};
  assert.match(renderToStaticMarkup(createElement(MethodVerification,{value})),/历史证据已过期|Stale evidence/);
});

await test('list ports preserve connection order and deleting one wire retains the other sources',async()=>{
  const concat=JSON.parse(await readFile(resolve(root,'../methods/mrtrix3/mrtrix3.command.mrcat/method.json'),'utf8'));
  const defs={...definitions,[concat.id]:concat},port=concat.input_slots[0].id;
  let steps=[node('a'),node('b'),node('c'),{id:'merge',method_id:concat.id,parameters:{},inputs:{}}];
  for(const id of ['b','a','c'])steps=graph.connectNodes(steps,defs,id,'output','merge',port);
  assert.deepEqual(graph.linkSources(steps[3].inputs[port]).map(s=>s.step_id),['b','a','c']);
  steps=graph.disconnectInput(steps,'merge',port,{step_id:'a',output_slot_id:'output'});
  assert.deepEqual(graph.linkSources(steps[3].inputs[port]).map(s=>s.step_id),['b','c']);
  steps=graph.removeNode(steps,'b');
  assert.deepEqual(graph.linkSources(steps.find(s=>s.id==='merge').inputs[port]).map(s=>s.step_id),['c']);
  assert.equal(graph.orderedSteps(steps).at(-1).id,'merge');
});
await test('changing label-transform interpolation invalidates a previously legal typed wire',async()=>{
  const transform=JSON.parse(await readFile(resolve(root,'../methods/mrtrix3/mrtrix.command.mrtransform/method.json'),'utf8'));
  const labels={...method,id:'labels',output_slots:method.output_slots.map(o=>({...o,type:'parcellation_image'}))};
  const consumer={...method,id:'consumer',input_slots:method.input_slots.map(i=>({...i,type:'parcellation_image'}))};
  const defs={labels,consumer,[transform.id]:transform};
  let steps=[{...node('labels'),method_id:'labels'},{...node('transform'),method_id:transform.id,parameters:{interp:'nearest'}},{...node('consumer'),method_id:'consumer'}];
  steps=graph.connectNodes(steps,defs,'labels','output','transform','input');
  steps=graph.connectNodes(steps,defs,'transform','output','consumer','input');
  assert.deepEqual(graph.conditionalConnectionIssues(steps,defs),[]);
  steps=steps.map(n=>n.id==='transform'?{...n,parameters:{interp:'linear'}}:n);
  assert.equal(graph.conditionalConnectionIssues(steps,defs).length,1);
  assert.ok(steps[2].inputs.input,'retain the invalid wire so it can be reviewed');
});
}finally{await server.close();}
