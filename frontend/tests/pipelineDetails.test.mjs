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
try {
  const {useLanguageStore}=await server.ssrLoadModule('/src/stores/languageStore.ts');
  const language=useLanguageStore.getInitialState().language;
  const method=JSON.parse(await readFile(resolve(root,'../methods/mrtrix3/mrtrix.command.mrconvert/method.json'),'utf8'));
  method.runtime_status={available:true};
  method.parameters=method.parameters.map(p=>({options:[],...p}));
  const source={name:'Original',description:'Description',expected_version:5,
    steps:[{id:'first',method_id:method.id,parameters:{nthreads:2},inputs:{},position:{x:80,y:80}},
      {id:'second',method_id:method.id,parameters:{},inputs:{input:{step_id:'first',output_slot_id:'output'}},position:{x:580,y:80}}]};
  const pipeline={id:'builtin',version:'1',label:source.name,description:source.description,
    draft_id:'original-recovery',draft_revision:9,parameters:[],input_slots:[],output_slots:[],
    steps:source.steps.map(s=>({id:s.id,method_ref:s.method_id,title:s.id}))};
  await test('copy gets independent identity and recovery while keeping the complete graph',async()=>{
    const {copyPipeline}=await server.ssrLoadModule('/src/features/library/pipelineCopy.ts');
    const copied=copyPipeline(pipeline,source);
    assert.equal(copied.id,'');assert.equal(copied.version,'');
    assert.equal(copied.draft_id,undefined);assert.equal(copied.draft_revision,undefined);
    assert.equal(copied.editor.expected_version,undefined);
    assert.deepEqual(copied.editor.steps,source.steps);
    copied.editor.steps[0].parameters.nthreads=7;
    copied.editor.steps[1].inputs.input.output_slot_id='changed';
    assert.equal(source.steps[0].parameters.nthreads,2);
    assert.equal(source.steps[1].inputs.input.output_slot_id,'output');
  });
  await test('read-only detail canvas retains selection, ports and links without editing actions',async()=>{
    const {PipelineDetails}=await server.ssrLoadModule('/src/features/library/PipelineDetails.tsx');
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    client.setQueryData(['pipeline-source','builtin','1'],{editor:source});
    client.setQueryData(['library-method',method.id,language],method);
    client.setQueryData(['pipeline-versions','builtin'],{items:[]});
    const noop=()=>{};
    const html=renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(PipelineDetails,{pipeline,onClose:noop,onSelectVersion:noop,onEdit:noop,onLaunch:noop})));
    assert.equal((html.match(/data-pipeline-node=/g)??[]).length,2);
    assert.match(html,/marker-end="url\(#pipeline-arrow\)"/);
    assert.match(html,/查看步骤：|Inspect step:/);
    assert.match(html,/data-pipeline-input="input"[^>]*disabled|disabled[^>]*data-pipeline-input="input"/);
    assert.match(html,/基于此 Pipeline 创建|Create from this Pipeline/);
    assert.match(html,/data-parameter-id="nthreads"/);
    assert.match(html,/方法默认值：|Method default:/);
    assert.match(html,/Pipeline 预设值：2|Pipeline preset: 2/);
    assert.doesNotMatch(html,/自动排列|移动或选择|保存草稿|发布可用版本|移除节点及连线|Auto arrange|Move or select|Save draft|Publish usable version|Delete Pipeline:|删除 Pipeline：/);
    client.clear();
  });
  await test('user detail exposes deletion while task selection remains read-only',async()=>{
    const {PipelineDetails}=await server.ssrLoadModule('/src/features/library/PipelineDetails.tsx');
    for(const taskPicker of [false,true]){
      const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
      const user={...pipeline,id:'user-publication',deletion_revision:2};
      client.setQueryData(['pipeline-source',user.id,'1'],{editor:source});
      client.setQueryData(['library-method',method.id,language],method);
      client.setQueryData(['pipeline-versions',user.id],{items:[]});
      const noop=()=>{};
      const html=renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(PipelineDetails,{pipeline:user,taskPicker,onClose:noop,onSelectVersion:noop,onEdit:noop,onLaunch:noop})));
      if(taskPicker)assert.doesNotMatch(html,/Delete Pipeline:|删除 Pipeline：/);
      else assert.match(html,/Delete Pipeline: Original|删除 Pipeline：Original/);
      client.clear();
    }
  });
  await test('draft and read-only method parameters share choices and defaults without changing the snapshot',async()=>{
    const {PipelineMethodParameters}=await server.ssrLoadModule('/src/features/library/PipelineMethodParameters.tsx');
    const dcm=JSON.parse(await readFile(resolve(root,'../methods/dcm2niix/dcm2niix.convert/method.json'),'utf8'));
    const node={id:'execute',method_id:dcm.id,parameters:{compression:'n',bids_sidecar:false,filename_template:null},inputs:{}};
    const before=structuredClone(node);let calls=0;
    for(const readOnly of [false,true]){
      const html=renderToStaticMarkup(createElement(PipelineMethodParameters,{method:dcm,node,readOnly,fixedParameters:['compression'],onChange:()=>calls++}));
      assert.match(html,/<option value="&quot;n&quot;" selected="">/);
      assert.match(html,/<option value="false" selected="">/);
      assert.match(html,/GZip <code>\(y\)<\/code>/);
      assert.match(html,/\(n\)/);assert.match(html,/\(i\)/);
      const filename=html.split('data-parameter-id="filename_template"')[1];
      assert.match(filename,/<input[^>]*value=""/,'explicit omission must not fall back to the method default');
      assert.match(filename,/%p_%s/,'the default is still documented');
      if(readOnly){
        assert.ok([...html.matchAll(/<(?:input|select)\b[^>]*>/g)].every(([tag])=>tag.includes('disabled=""')));
        assert.match(html,/固定参数|Fixed parameter/);
        assert.match(html,/Pipeline 预设值：未指定|Pipeline preset: Not specified/);
      }else assert.doesNotMatch(html,/disabled=""/);
    }
    assert.deepEqual(node,before);assert.equal(calls,0);
  });
  await test('inspection exposes inactive parameter contracts while drafts keep conditional visibility',async()=>{
    const {PipelineMethodParameters}=await server.ssrLoadModule('/src/features/library/PipelineMethodParameters.tsx');
    const conditional={...method,parameters:[
      {...method.parameters[0],id:'mode',type:'boolean',default:false,options:[],visible_when:{},enabled_when:{}},
      {...method.parameters[0],id:'conditional',default:0,visible_when:{mode:[true]},minimum:0,maximum:10}
    ]};
    const node={id:'conditional-step',method_id:method.id,parameters:{mode:false,conditional:0},inputs:{}};
    const view=readOnly=>renderToStaticMarkup(createElement(PipelineMethodParameters,{method:conditional,node,readOnly}));
    assert.doesNotMatch(view(false),/data-parameter-id="conditional"/);
    const html=view(true);assert.match(html,/data-parameter-id="conditional"/);
    assert.match(html,/当前参数下不生效|Inactive for the current parameters/);
    assert.match(html,/value="0"/);assert.match(html,/取值范围：0 至 10|Value range: 0 to 10/);
  });
} finally {await server.close();}
