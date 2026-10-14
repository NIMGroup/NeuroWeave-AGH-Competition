import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const descriptors = Object.fromEntries(['localStorage','fetch'].map(k => [k,Object.getOwnPropertyDescriptor(globalThis,k)]));
Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=> 'zh',setItem(){}}});
const server = await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,
  server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const load = p => server.ssrLoadModule('/src/'+p);
  const {useLanguageStore} = await load('stores/languageStore.ts');
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.setState({language:'zh'});
  useLanguageStore.getInitialState = () => ({...initial,language:'zh'});
  const {StepArtifacts} = await load('features/workflows/StepArtifacts.tsx');
  const {WorkflowStepSection} = await load('features/workflows/WorkflowStepSection.tsx');
  const {AgentTaskStatus} = await load('features/chat/AgentTaskStatus.tsx');
  const api = await load('features/workflows/workflowsApi.ts');
  const dataset = {dataset_id:'ds',root_id:'root',relative_path:'study',display_name:'study',inspection:{samples:[],sample_count:2}};
  const slot = {id:'dwi',type:'preprocessed_dwi',label:'DWI',description:'用于建模的扩散影像',required:true,
    required_when:{},formats:['.mif','.nii.gz'],source_options:['existing_path'],binding:'file',cardinality:'one',validation:[],companions:[]};
  const method = {id:'method',label:'计算',description:'模型方法',runner_id:'runner',execution_mode:'runner',
    input_types:['preprocessed_dwi'],output_types:['fod_image'],requires_features:[],compatible:true,blocked_reasons:[],
    parameters:[],input_slots:[slot],output_slots:[],skill_id:'skill',recommended:true,recommendation_reason:''};
  const step = {id:'s',title:'计算',description:'计算',capability:'fod',order:1,status:'method_selected',selection:'auto',
    depends_on:[],selected_method_id:'method',selected_parameters:{},method_options:[method],
    missing_required_inputs:['dwi'],input_bindings:{},
    input_suggestions:{dwi:{path:'sub-01/dwi/image.mif',relative_path:'study/sub-01/dwi/image.mif',root_id:'root',binding_mode:'per_sample'}}};
  const workflow = {workflow_id:'wf',revision:1,input_selection:'user',status:'awaiting_input_binding',dataset,steps:[step]};
  const render = element => {
    const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
    const html = renderToStaticMarkup(createElement(QueryClientProvider,{client},element));
    client.clear();
    return html;
  };
  await test('an unselected suggestion has an explicit adoption action and does not claim a binding', () => {
    const html = render(createElement(StepArtifacts,{method,step,workflow,selectedParameters:{},disabled:false,
      locked:false,busySlotId:null,busyCompleting:false,onBind(){},onClear(){},onBindHistory(){},onCompleteDependencies(){}}));
    assert(html.includes('建议路径 · 尚未采用') && html.includes('采用建议'));
    assert(html.includes('填写或复用样本规则'));
    assert(!html.includes('已选择：') && !html.includes('补齐上游'));
    assert(!html.includes('>preprocessed_dwi<'));
  });
  await test('user-selected rules remain pending verification and can be cleared', () => {
    const chosen = {...step,input_bindings:{dwi:{slot_id:'dwi',artifact_type:'preprocessed_dwi',source:'existing_path',
      binding_mode:'per_sample',path_template:'{sample}/dwi/image.mif',confirmed_by_user:true,deferred_resolution:true,messages:[]}}};
    const html = render(createElement(StepArtifacts,{method,step:chosen,workflow,selectedParameters:{},disabled:false,
      locked:false,busySlotId:null,busyCompleting:false,onBind(){},onClear(){},onBindHistory(){},onCompleteDependencies(){}}));
    assert(html.includes('已选择：') && html.includes('待 dry-run 核验') && html.includes('清除选择'));
    assert(!html.includes('采用建议'));
  });
  await test('an unlocked user draft immediately exposes input controls', () => {
    const html = render(createElement(WorkflowStepSection,{workflow,step,locked:false,disabled:false,busyKey:null,
      busyParameterId:null,busySlotId:null,onAction(){}}));
    assert(html.includes('采用建议') && html.includes('选择已有'));
    assert(!html.includes('hidden=""'));
  });
  await test('conversation status identifies the input-selection stage without a run action', () => {
    const html = render(createElement(AgentTaskStatus,{task:{task_id:'task',goal:'计算FOD',phase:'awaiting_inputs'}}));
    assert(html.includes('草案已生成，待选择输入') && !html.includes('<button'));
  });
  await test('preview, adoption and clearing call configuration endpoints only', async () => {
    const calls=[];
    globalThis.fetch=async(path,init={}) => {calls.push({path,...init}); return new Response('{}');};
    await api.previewStepInputPath('wf','s','dwi','root','study/{sample}/image.mif',undefined,'template');
    await api.bindStepInput('wf','s','dwi','root','study/{sample}/image.mif',3,'template');
    await api.clearStepInput('wf','s','dwi',4);
    assert.deepEqual(calls.map(c => c.method),['POST','PATCH','POST']);
    assert.equal(JSON.parse(calls[0].body).binding_mode,'template');
    assert.equal(JSON.parse(calls[1].body).expected_revision,3);
    assert.equal(JSON.parse(calls[2].body).expected_revision,4);
    assert(calls.every(c => !c.path.endsWith('/runs') && !c.path.endsWith('/dry-run')));
  });
} finally {
  await server.close();
  for (const [key,value] of Object.entries(descriptors)) {
    if(value)Object.defineProperty(globalThis,key,value); else delete globalThis[key];
  }
}
