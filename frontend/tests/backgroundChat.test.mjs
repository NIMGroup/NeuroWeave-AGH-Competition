import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const descriptors = Object.fromEntries(['localStorage','window','fetch'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
const storage = new Map();
Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)}});
globalThis.window = new EventTarget();
const server = await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const load = path=>server.ssrLoadModule('/src/'+path);
  const {belongsToConversation,responseConversation,turnIsPending,requestStatus} = await load('features/chat/turnTracking.ts');
  const {adoptActiveTurn,readActiveTurns,registerTurnOrigin,turnOrigin,submitTurn,pendingTurns,readTurn,forgetTurn} = await load('features/chat/chatTurns.ts');
  const {useConversationUiStore} = await load('stores/conversationUiStore.ts');
  await test('unknown turns lock only their originating conversation or draft, never the whole UI',()=>{
    const a={id:'A',origin:{scope:'conv-A',conversationId:'conv-A',title:'A'},reconnecting:false};
    const b={id:'B',origin:{scope:'draft-B',conversationId:null,title:'B'},reconnecting:false};
    assert(turnIsPending(a) && belongsToConversation(a,'conv-A','draft-C'));
    assert(!belongsToConversation(a,'conv-C','draft-C'));
    assert(belongsToConversation(b,null,'draft-B'));
    assert(!belongsToConversation(b,null,'draft-C'));
    assert(!belongsToConversation({id:'legacy',reconnecting:false},null,'draft-C'));
    assert(belongsToConversation({...b,turn:{conversation_id:'conv-B',status:'running'}},'conv-B','draft-C'));
    assert(!belongsToConversation({...b,turn:{conversation_id:'conv-B',status:'running'}},null,'draft-B'));
  });
  await test('new drafts receive distinct identities and late replies resolve to their captured origin',()=>{
    useConversationUiStore.getState().newConversation();
    const first = useConversationUiStore.getState().draftId;
    useConversationUiStore.getState().newConversation();
    assert.notEqual(first,useConversationUiStore.getState().draftId);
    const origin = {scope:first,conversationId:null,title:'first'};
    assert.equal(responseConversation(origin,'created-first'),'created-first');
    assert.equal(responseConversation({scope:'conv-A',conversationId:'conv-A',title:'A'}),'conv-A');
    assert.equal(useConversationUiStore.getState().activeConversationId,null);
  });
  await test('parallel submissions retain scope through registration and complete out of order with one POST each',async()=>{
    const calls=[], releases={};
    const ready=id=>({turn_id:id,conversation_id:'conv-'+id,status:'succeeded',message:'done',error:null,result:{content:'reply-'+id,conversation_id:'conv-'+id}});
    globalThis.fetch = async (path,init)=>{
      calls.push([path,init.method??'GET']);
      if(init.method==='POST') {
        const id=JSON.parse(init.body).client_turn_id;
        return await new Promise(resolve=>{releases[id]=()=>resolve(new Response(JSON.stringify(ready(id))));});
      }
      return new Response(JSON.stringify(ready(path.split('/').at(-1))));
    };
    registerTurnOrigin('A',{scope:'draft-A',conversationId:null,title:'A'});
    registerTurnOrigin('B',{scope:'draft-B',conversationId:null,title:'B'});
    const a=submitTurn({client_turn_id:'A',message:'A'}),b=submitTurn({client_turn_id:'B',message:'B'});
    assert.deepEqual(pendingTurns(),['A','B']);
    const observeA=readTurn('A');
    assert.equal(calls.filter(([,method])=>method==='GET').length,0);
    assert.equal(turnOrigin('A').scope,'draft-A');
    releases.B(); assert.equal((await b).content,'reply-B');
    assert.deepEqual(pendingTurns(),['A']);
    assert.equal(turnOrigin('A').scope,'draft-A');
    releases.A(); assert.equal((await a).content,'reply-A');
    assert.equal((await observeA).conversation_id,'conv-A');
    assert.deepEqual(pendingTurns(),[]);
    assert.equal(calls.filter(([,method])=>method==='POST').length,2);
    assert.equal(turnOrigin('A'),undefined);
    await forgetTurn('B');
  });
  await test('processing leaves the textarea available for a draft and displays the progress control at Send',async()=>{
    const {MessageComposer}=await load('features/chat/MessageComposer.tsx');
    const {translations}=await load('lib/i18n.ts');
    const {useLanguageStore}=await load('stores/languageStore.ts');
    useLanguageStore.setState({language:'zh'});
    const html=renderToStaticMarkup(createElement(MessageComposer,{t:translations.zh,value:'next draft',sending:true,processing:true,
      selectedSkills:[],sendControl:createElement('button',{'aria-label':'查看进度'},'progress'),
      onChange(){},onKeyDown(){},onSend(){},onClearSkills(){},onRemoveSkill(){},onViewSkill(){}}));
    assert.match(html,/<textarea[^>]*>next draft<\/textarea>/);
    assert(!/<textarea[^>]* disabled=""/.test(html));
    assert(html.includes('查看进度') && !html.includes(`aria-label="${translations.zh.sendMessage}"`));
    assert(!html.includes('仅问答') && !html.includes('交互模式'));
    assert.match(html,/任务需点击运行，覆盖和删除需确认|click Run for tasks and approve overwrites or deletions/);
  });
  await test('stop acknowledgement stays visible and disabled until backend cleanup completes',async()=>{
    const {ChatTurnPanel}=await load('features/chat/ChatTurnPanel.tsx');
    const {useLanguageStore}=await load('stores/languageStore.ts');
    useLanguageStore.setState({language:'zh'});
    const item={id:'stopping',turn:{turn_id:'stopping',conversation_id:'conv',status:'running',cancel_requested:true,message:'正在停止请求并清理当前步骤',result:null,error:null},reconnecting:false};
    assert.equal(requestStatus(item,true),'正在停止请求');
    assert.equal(requestStatus(item,false),'Stopping request');
    const background={actions:{},errors:{},recover(){},act(){},dismiss(){}};
    const html=renderToStaticMarkup(createElement(ChatTurnPanel,{background,records:[item],onOpen(){}}));
    assert.match(html,/<button[^>]*disabled=""[^>]*>(?:正在停止|Stopping)<\/button>/);
    assert(!html.includes('重试同一请求') && !html.includes('关闭提示'));
    const done={...item,turn:{...item.turn,status:'cancelled',cancel_requested:false,message:'请求已停止'}};
    assert.equal(requestStatus(done,true),'请求已取消');
    const final=renderToStaticMarkup(createElement(ChatTurnPanel,{background,records:[done],onOpen(){}}));
    assert(final.includes('Retry this request') && final.includes('Dismiss') && !final.includes('Stopping'));
    const retry={...item,turn:{...item.turn,cancel_requested:false}};
    const retrying=renderToStaticMarkup(createElement(ChatTurnPanel,{background:{...background,actions:{stopping:'retry'}},records:[retry],onOpen(){}}));
    assert(!retrying.match(/<button[^>]*>Stop request<\/button>/)[0].includes(' disabled='));
  });
  await test('server verification is discovered without posting a user message and exposes a direct stop control',async()=>{
    const {ChatActivityControl}=await load('features/chat/ChatActivityControl.tsx');
    const calls=[];
    const turn={turn_id:'result-synthetic',conversation_id:'conv-A',request_kind:'result_verification',
      status:'running',message:'核验执行结果',result:null,error:null};
    globalThis.fetch=async(path,init)=>{calls.push([path,init.method??'GET']);return new Response(JSON.stringify({turns:[turn]}));};
    const active=await readActiveTurns();
    active.turns.forEach(adoptActiveTurn);active.turns.forEach(adoptActiveTurn);
    assert.deepEqual(calls,[['/api/chat/turns','GET']]);
    assert.deepEqual(pendingTurns(),[turn.turn_id]);
    const item={id:turn.turn_id,turn,origin:turnOrigin(turn.turn_id),reconnecting:false};
    assert(belongsToConversation(item,'conv-A','other'));
    assert(!belongsToConversation(item,'conv-B','other'));
    assert.equal(requestStatus(item,true),'正在核查执行结果');
    const background={records:[item],actions:{},errors:{},recover(){},act(){},dismiss(){}};
    const html=renderToStaticMarkup(createElement(ChatActivityControl,{background,sendPosition:true,onOpen(){}}));
    assert(html.includes('Stop verification') && html.includes('Verifying execution result'));
    adoptActiveTurn({...turn,status:'succeeded'});assert.deepEqual(pendingTurns(),[turn.turn_id]);
    await forgetTurn(turn.turn_id);
  });
  await test('restricted browser storage still tracks the accepted request and scopes its send lock',async()=>{
    const setItem=localStorage.setItem;let release;
    localStorage.setItem=()=>{throw new DOMException('Storage blocked','SecurityError');};
    globalThis.fetch=async(path,init)=>{
      const value={turn_id:'restricted',conversation_id:'accepted',status:'succeeded',message:'done',error:null,result:{content:'done',conversation_id:'accepted'}};
      if(init.method==='POST')await new Promise(resolve=>{release=resolve;});
      return new Response(JSON.stringify(value));
    };
    try {
      registerTurnOrigin('restricted',{scope:'draft-restricted',conversationId:null,title:'restricted'});
      const submitted=submitTurn({client_turn_id:'restricted',message:'restricted'});
      assert(pendingTurns().includes('restricted'));
      assert(belongsToConversation({id:'restricted',origin:turnOrigin('restricted')},null,'draft-restricted'));
      release();await submitted;assert(!pendingTurns().includes('restricted'));
    } finally {localStorage.setItem=setItem;}
  });
} finally {
  await server.close();
  for(const [key,descriptor] of Object.entries(descriptors)) {
    if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
  }
}
