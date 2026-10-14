import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const descriptors=Object.fromEntries(['localStorage','fetch'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=> 'zh',setItem(){}}});
const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,
  server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const load=path=>server.ssrLoadModule('/src/'+path);
  const {useLanguageStore}=await load('stores/languageStore.ts');
  useLanguageStore.setState({language:'zh'});
  const initial=useLanguageStore.getInitialState();
  useLanguageStore.getInitialState=()=>({...initial,language:'zh'});
  const {DataOperationCard}=await load('features/data/DataOperationCard.tsx');
  const {WorkspaceConfirmation}=await load('features/workspace/WorkspaceConfirmation.tsx');
  const {ExecutionActivityControl}=await load('features/chat/ExecutionActivityControl.tsx');
  const {cancelExecution}=await load('features/chat/useBackgroundExecutions.ts');
  const base={id:'task',kind:'data',title:'Fixture',actor:'local',conversation_id:'conv',
    mode:'copy',source_root:'/input',target_root:'/output',status:'running',manifest_hash:'hash',
    file_count:2,total_bytes:2,completed:1,action_counts:{copy:2},conflicts:[],items:[],
    preview_truncated:false,can_confirm:false,can_undo:false};
  await test('execution, validation, script and restoration offer Stop at the card header',()=>{
    for(const status of ['validating','queued','analyzing','running','rolling_back']) {
      const html=renderToStaticMarkup(createElement(DataOperationCard,{operation:{...base,status}}));
      assert.match(html.split('</header>')[0],/>停止<\/button>/);
      assert(!html.includes('撤销待运行方案'));
    }
    const html=renderToStaticMarkup(createElement(DataOperationCard,{operation:{...base,cancel_requested:true}}));
    assert.match(html.split('</header>')[0],/disabled=""[^>]*>.*正在停止<\/button>/);
    for(const status of ['succeeded','cancelled','rolled_back']) {
      const finished=renderToStaticMarkup(createElement(DataOperationCard,{operation:{...base,status}}));
      assert(!finished.includes('>停止</button>'));
    }
  });
  await test('workspace operations expose Stop without requiring refresh and retain stopped staging visibility',()=>{
    const operation={id:'fs',action:'copy',source:'/out/a',target:'/out/b',conversation_id:'conv',status:'running',request:{}};
    const html=renderToStaticMarkup(createElement(WorkspaceConfirmation,{operation,conversationId:'conv'}));
    assert(html.includes('>停止</button>'));
    const done=renderToStaticMarkup(createElement(WorkspaceConfirmation,{operation:{...operation,status:'cancelled',staging_path:'/out/staging'},conversationId:'conv'}));
    assert(done.includes('已停止')&&done.includes('/out/staging')&&!done.includes('>停止</button>'));
  });
  await test('input Stop remains available independently of other work, and server stop acknowledgement disables it',()=>{
    const item={id:'task',kind:'data',title:'Task',conversation_id:'conv',status:'running',can_cancel:true,cancel_requested:false};
    const background={records:[item],actions:{},errors:{},stop(){},reconnecting:false};
    const html=renderToStaticMarkup(createElement(ExecutionActivityControl,{background,sendPosition:true,onOpen(){}}));
    assert(html.includes('停止执行')&&html.includes('执行进度 (1)'));
    const pending=renderToStaticMarkup(createElement(ExecutionActivityControl,{background:{...background,actions:{'data:task':true}},sendPosition:true,onOpen(){}}));
    assert.match(pending,/<button(?=[^>]*aria-label="正在停止执行")(?=[^>]*disabled="")[^>]*>/);
  });
  await test('each execution kind cancels only its bound job with encoded identifiers and never starts work',async()=>{
    const calls=[];
    globalThis.fetch=async(path,init)=>{calls.push({path,method:init.method,body:init.body&&JSON.parse(init.body)});return new Response('{}');};
    for(const kind of ['data','workspace','run','check'])await cancelExecution({id:'a/b',kind,conversation_id:'c&d'});
    assert.deepEqual(calls.map(c=>c.path),[
      '/api/data-operations/a%2Fb/cancel?conversation_id=c%26d',
      '/api/fs/operations/a%2Fb/cancel','/api/runs/a%2Fb/cancel','/api/operations/a%2Fb/cancel']);
    assert(calls.every(c=>c.method==='POST'));
    assert.deepEqual(calls[1].body,{confirmed:true,conversation_id:'c&d'});
  });
} finally {
  await server.close();
  for(const[k,d]of Object.entries(descriptors))if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];
}
