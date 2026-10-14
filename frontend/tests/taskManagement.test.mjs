import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';

const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,
  server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
const load=path=>server.ssrLoadModule('/src/'+path);
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
const render=(component,props)=>renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(component,props)));
try {
  const {useLanguageStore}=await load('stores/languageStore.ts');
  useLanguageStore.setState({language:'zh'});
  await test('chat header keeps resource actions visible and reserves their space beside long titles',async()=>{
    const {ChatHeader}=await load('features/chat/ChatHeader.tsx');
    const {translations}=await load('lib/i18n.ts');
    const html=render(ChatHeader,{t:translations.zh,title:'Chat',domainLabel:'Chat',sidebarOpen:true,
      datasetLabel:'Input',datasetCount:1,skillLabel:null,skillCount:0,workspaceId:null,
      onOpenSidebar(){},onOpenWorkspace(){},onOpenDatasets(){},onBrowseDataset(){}});
    assert(html.includes('grid-cols-[minmax(0,1fr)_auto]') && html.includes('h-14'));
    assert(html.includes(`aria-label="${translations.zh.conversationMenu}"`));
    assert(html.includes('Input') && html.includes(`aria-label="${translations.zh.datasetSelectLocal}"`));
    assert(html.includes(`aria-label="${translations.zh.workspaceSelect}"`) && html.includes(translations.zh.datasetBrowseFiles));
    assert(!html.includes('aria-label="Language"') && !html.includes('aria-label="语言"') && !html.includes(translations.zh.switchToDark));
  });
  await test('Pipeline is a rail domain and no longer a Skill library tab',async()=>{
    const {AppRail}=await load('components/AppRail.tsx');
    const {LibraryPanel}=await load('features/library/LibraryPanel.tsx');
    const {translations}=await load('lib/i18n.ts');
    const rail=render(AppRail,{t:translations.zh,domain:'pipelines',datasetCount:0,darkMode:false,onToggleTheme(){},onDomainChange(){}});
    assert.match(rail,/aria-current="page" aria-label="Pipeline"/);
    assert(rail.includes('任务管理'));
    const theme=rail.indexOf(`aria-label="${translations.zh.switchToDark}"`);
    const language=rail.search(/aria-label="(?:Language|语言)"/);
    const settings=rail.indexOf(`aria-label="${translations.zh.settingsNav}"`);
    assert(theme>=0 && language>theme && settings>language);
    const props={selectedSkillIds:[],disabled:false,onAdd(){},onRemove(){},onDetails(){},onClear(){}};
    const skills=render(LibraryPanel,props);
    assert(!/role="tab"[^>]*>Pipeline</.test(skills));
    const pipelines=render(LibraryPanel,{...props,domain:'pipelines'});
    assert(pipelines.includes('拖拽创建 Pipeline'));
    assert(!pipelines.includes('role="tab"'));
  });
  await test('task index URL carries server filters and safely encodes search text',async()=>{
    const {taskIndexUrl}=await load('features/workflows/taskIndex.ts');
    const url=new URL(taskIndexUrl(30,'A & B','pipeline','active'),'http://test');
    assert.equal(url.searchParams.get('q'),'A & B');
    assert.equal(url.searchParams.get('source'),'pipeline');
    assert.equal(url.searchParams.get('status'),'active');
    assert.equal(url.searchParams.get('offset'),'30');
  });
  await test('task picker exposes details and excludes draft authoring',async()=>{
    const {LibraryPanel}=await load('features/library/LibraryPanel.tsx');
    const language=useLanguageStore.getInitialState().language;
    const props={selectedSkillIds:[],disabled:false,onAdd(){},onRemove(){},onDetails(){},onClear(){}};
    for(const kind of ['skill','pipeline']){
      const entry={id:kind,label:kind+' details fixture',kind,description:'Description',category:[],status:'published'};
      client.setQueryData(['library',kind,'',0,'','',language],{items:[entry],total:1,has_more:false});
      const html=render(LibraryPanel,{...props,...(kind==='pipeline'?{domain:'pipelines',taskPicker:true}:{})});
      assert(html.includes(`aria-label="查看详情：${entry.label}"`));
      if(kind==='pipeline')assert(!html.includes('拖拽创建 Pipeline'));
    }
    const {PipelineTaskDialog}=await load('features/library/PipelineTaskDialog.tsx');
    const chooser=render(PipelineTaskDialog,{onClose(){},onCreated(){}});
    assert(chooser.includes('选择 Pipeline'));
    assert(chooser.includes('查看详情'));
    assert(!chooser.includes('未完成的 Pipeline 草稿'));
  });
  await test('deleted drafts stay hidden from old browser recovery and restored revisions win',async()=>{
    const {mergeDraftRecovery}=await load('features/library/pipelineDraftRecovery.ts');
    const draft={id:'owned',revision:1,updated_at:'2026-10-01',editor:{name:'Old',steps:[]},pipeline_id:null,pipeline_version:null};
    assert.deepEqual(mergeDraftRecovery([],[draft],['owned']),[]);
    const merged=mergeDraftRecovery([{...draft,revision:2,editor:{name:'Restored',steps:[]}}],[draft],[]);
    assert.equal(merged[0].editor.name,'Restored');assert.equal(merged[0].server_revision,2);
    const local=mergeDraftRecovery([],[draft],[]);assert.equal(local[0].server_revision,0);
  });
  await test('output selection supports ancestor navigation and reuses exact registered directories',async()=>{
    const {browseRootForPath,workspaceForPath}=await load('features/workspace/outputSelection.ts');
    assert.equal(browseRootForPath('/home/data/output',['/home/data','/']),'/');
    assert.equal(browseRootForPath('/database/out',['/data']),null);
    assert.equal(browseRootForPath('/data2/out',['/data','/data2']),'/data2');
    const workspaces=[{id:'active',root_path:'/data/output/',status:'active',manageable:true},
      {id:'readonly',root_path:'/data/readonly',status:'active',manageable:false}];
    assert.equal(workspaceForPath(workspaces,'/data/output').id,'active');
    assert.equal(workspaceForPath(workspaces,'/data/output/child'),undefined);
    assert.equal(workspaceForPath(workspaces,'/data/readonly'),undefined);
    const {WorkspacePanel}=await load('features/workspace/WorkspacePanel.tsx');
    const html=render(WorkspacePanel,{selectionMode:true,workspaceId:null,conversationId:null,onSelect:async()=>{},onClose(){}});
    assert(html.includes('data-placement="center"'));
    assert(html.includes('calc(100vw - 32px)'));
  });
  await test('Pipeline launch requires an output context before creating a task',async()=>{
    const {PipelineLaunchDialog}=await load('features/library/PipelineLaunchDialog.tsx');
    const html=render(PipelineLaunchDialog,{pipeline:{id:'one',version:'2',label:'Recipe',parameters:[],input_slots:[],output_slots:[],steps:[]},onClose(){},onCreated(){}});
    assert.match(html,/<button[^>]*disabled=""[^>]*>创建任务并配置运行<\/button>/);
    assert(html.includes('Recipe · v2'));
    assert(html.includes('选择或登记输出目录'));
  });
  await test('task management opens the selected task configuration immediately',async()=>{
    const {TaskDetails}=await load('features/workflows/TaskDetails.tsx');
    const wf={workflow_id:'detail',revision:1,title:'Fixture',status:'confirmed',execution_mode:'runner',
      steps:[],dataset:null,workspace_id:'ws',workspace_root_path:'/synthetic',requested_capabilities:[],
      warnings:[],locked_at:'now',dry_run_report:{valid:false,execution_enabled:true,commands:[],warnings:[]}};
    client.setQueryData(['workflow','detail'],wf);
    client.setQueryData(['workflow-run','detail'],null);
    const html=render(TaskDetails,{task:{workflow_id:'detail',title:'Fixture',source:'pipeline'},
      sidebarOpen:true,onOpenSidebar(){},onOpenPipelines(){}});
    assert.match(html,/aria-expanded="true"/);
    const {translations}=await load('lib/i18n.ts');
    const t=translations[useLanguageStore.getInitialState().language];
    assert(html.includes(t.workflowCollapseDetails));
    assert(html.includes(t.workspaceSelect));
  });
  await test('editor collapse buttons are attached to the docks rather than the toolbar',async()=>{
    const {PipelineEditor}=await load('features/library/PipelineEditor.tsx');
    const html=render(PipelineEditor,{onClose(){},onSaved(){}});
    assert(html.includes('aria-label="收起方法目录"'));
    assert(html.includes('aria-label="收起步骤参数"'));
    assert(!html.includes('aria-pressed="true"'));
  });
} finally {client.clear();await server.close();}
