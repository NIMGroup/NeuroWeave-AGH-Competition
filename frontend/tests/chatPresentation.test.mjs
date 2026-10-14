import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => 'zh', setItem() {} } });
const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
const load = path => server.ssrLoadModule('/src/' + path);
const batch = { id: 'preview-1', title: '文件整理预览', conversation_id: 'chat-1', mode: 'copy',
  status: 'pending', source_root: '/input', target_root: '/output', file_count: 20, completed: 0,
  total_bytes: 0, manifest_hash: 'exact-hash', action_counts: { copy: 19, delete: 1 },
  requires_overwrite_confirmation: true, items: [], conflicts: [], can_confirm: true, can_undo: false };
try {
  const { useLanguageStore } = await load('stores/languageStore.ts');
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
  const { workspaceActivities, latestOperationEvents } = await load('features/workspace/workspaceActivity.ts');
  const { WorkspaceTimeline } = await load('features/workspace/WorkspaceTimeline.tsx');
  const { MessageContent } = await load('features/chat/MessageContent.tsx');
  const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
  await test('legacy query summaries remain readable tool records and cannot render a file confirmation', async () => {
    const {WorkspaceConfirmation}=await load('features/workspace/WorkspaceConfirmation.tsx');
    const operation={id:'batch-123',kind:'preserved',status:'pending'};
    const event={tool:'list_data_operation_files',status:'succeeded',operation};
    assert.equal(latestOperationEvents([event]).length,0);
    const html=render(WorkspaceTimeline,{events:[event]});
    assert(html.includes('查询批次文件清单') && !html.includes('workspace-operation-batch-123'));
    assert.equal(render(WorkspaceConfirmation,{operation,conversationId:'chat-1'}),'');
    const valid={id:'real-fs',action:'rename',source:'/output/old',target:'/output/new',status:'pending',request:{},conversation_id:'chat-1'};
    assert.deepEqual(latestOperationEvents([{tool:'rename_output_path',status:'pending',operation:valid},event]).map(e=>e.operation.id),['real-fs']);
    assert(render(WorkspaceConfirmation,{operation:valid,conversationId:'chat-1'}).includes('/output/old'));
  });
  await test('tool lifecycles pair reordered arguments without hiding retries or separate paths', () => {
    const events = [
      { tool: 'read_output_text', status: 'running', arguments: { path: 'a', limit: 10 } },
      { tool: 'read_output_text', status: 'succeeded', arguments: { limit: 10, path: 'a' }, count: 1 },
      { tool: 'read_output_text', status: 'running', arguments: { path: 'b' } },
      { tool: 'read_output_text', status: 'failed', error: 'permission denied' },
      { tool: 'read_output_text', status: 'running', arguments: { path: 'b' } },
      { tool: 'read_output_text', status: 'succeeded', arguments: { path: 'b' } }
    ];
    const calls = workspaceActivities(events);
    assert.equal(calls.length, 3);
    assert.deepEqual(calls.map(item => item.status), ['succeeded', 'failed', 'succeeded']);
    assert.equal(calls[1].arguments.path, 'b');
    assert.equal(calls[1].error, 'permission denied');
  });
  await test('one latest card per operation preserves independent operations and visible approvals', () => {
    const events = [
      { tool: 'propose_data_changes', status: 'pending', data_operation: batch },
      { tool: 'get_data_operation', status: 'succeeded', data_operation: { ...batch, completed: 2 } },
      { tool: 'propose_data_changes', status: 'pending', data_operation: { ...batch, id: 'independent' } }
    ];
    assert.equal(latestOperationEvents(events).length, 2);
    const html = render(WorkspaceTimeline, { events });
    assert.equal((html.match(/id="data-operation-preview-1"/g) || []).length, 1);
    assert(html.includes('id="data-operation-independent"'));
    const endOfTrace = html.indexOf('</details>');
    assert(html.indexOf('同时授权清单中的删除操作') > endOfTrace);
    assert(html.indexOf('确认覆盖清单中的已有输出文件') > endOfTrace);
    assert(!html.includes('chat-activity rounded-card border border-line bg-panel text-meta" open'));
  });
  await test('tool submission is distinct from batch completion and failed calls are expanded', () => {
    const submitted = render(WorkspaceTimeline, { events: [
      { tool: 'run_data_python', status: 'submitted', data_operation: { ...batch, status: 'analyzing', can_confirm: false } }
    ] });
    assert(submitted.includes('已提交后台') && submitted.includes('脚本运行中') && !submitted.includes('已完成'));
    const failed = render(WorkspaceTimeline, { events: [{ tool: 'read_output_text', status: 'failed', error: '<script>bad()</script>' }] });
    assert.match(failed, /<details[^>]* open=""/);
    assert(failed.includes('&lt;script&gt;') && !failed.includes('<script>'));
  });
  await test('assistant Markdown renders lists, tables and code without active HTML or unsafe links', () => {
    const html = render(MessageContent, { content: [
      '## 结果', '**完成核对**', '- 保留原文件', '',
      '| 序列 | 文件数 |', '| --- | --- |', '| T1 | 20 |', '',
      '```python', 'print("<script>")', '```', '',
      '<script>alert(1)</script>', '[bad](javascript:alert%281%29)', '![remote](https://example.com/tracker.png)',
      '[reference](https://example.com/info)'
    ].join('\n') });
    assert(html.includes('<h2>') && html.includes('<strong>') && html.includes('<ul>') && html.includes('<table>'));
    assert(html.includes('复制代码') && html.includes('language-python'));
    assert(!html.includes('<script>') && !html.includes('href="javascript:') && !html.includes('<img'));
    assert(html.includes('rel="noopener noreferrer"'));
  });
  await test('repeated operation snapshots have one card without extra view-current-plan links', async () => {
    const { MessageArea } = await load('features/chat/MessageArea.tsx');
    const { getTranslations } = await load('lib/i18n.ts');
    const html = render(MessageArea, { t: getTranslations('zh'), messages: ['old', 'new'].map(id => ({
      id, role: 'assistant', content: '已生成预览', workspaceEvents: [{ tool: 'get_data_operation', status: 'succeeded', data_operation: batch }]
    })), scrollRef: { current: null }, sending: false, selectedCount: 0, onLoadOlder() {} });
    assert.equal((html.match(/id="data-operation-preview-1"/g) || []).length, 1);
    assert.equal((html.match(/id="conversation-task-workspace"/g) || []).length, 1);
    assert(!html.includes('href="#conversation-task-workspace"') && !html.includes('查看当前方案'));
    assert(!html.includes(getTranslations('zh').chatNoWorkflowNeeded));
  });
  await test('background request details retain stop/retry and distinguish disconnection from failure', async () => {
    const { ChatTurnPanel } = await load('features/chat/ChatTurnPanel.tsx');
    const { belongsToConversation } = await load('features/chat/turnTracking.ts');
    const background = { actions:{}, errors:{}, act(){}, recover(){}, dismiss(){} };
    const record = (status, error) => ({id:'request-1',reconnecting:false,error,
      turn:{turn_id:'request-1',conversation_id:'chat-1',status,message:'合成请求详情',error:null}});
    const renderTurn = (status, error) => render(ChatTurnPanel, {background, records:[record(status,error)],onOpen(){}});
    const delivered = renderTurn('succeeded');
    assert(delivered.includes('请求已回复') && !delivered.includes('已完成'));
    assert(renderTurn('running').includes('停止请求'));
    assert(renderTurn('failed').includes('重试同一请求'));
    const recovering = renderTurn('running',new TypeError('network offline'));
    assert(recovering.includes('原请求仍在后台') && recovering.includes('恢复状态'));
    assert(recovering.includes('停止请求') && !recovering.includes('重试同一请求') && !recovering.includes('回复失败'));
    const { TurnRecordMissingError } = await load('features/chat/turnRecovery.ts');
    const missing = render(ChatTurnPanel,{background,records:[{id:'missing',reconnecting:false,
      error:new TurnRecordMissingError('原请求 ID 已保留')}],onOpen(){}});
    assert(missing.includes('暂未查到请求记录') && missing.includes('恢复状态') && missing.includes('关闭提示'));
    assert(!missing.includes('Turn not found') && !missing.includes('回复失败') && !missing.includes('重试同一请求') && !missing.includes('animate-spin'));
    const unrelated = record('failed');
    assert(!belongsToConversation(unrelated,'current-chat','draft'));
    assert(!render(ChatTurnPanel,{background,records:[unrelated].filter(item=>belongsToConversation(item,'current-chat','draft')),onOpen(){}}).includes('合成请求详情'));
  });
  await test('request failure stays beside its original message before subsequent successful replies', async () => {
    const { MessageArea } = await load('features/chat/MessageArea.tsx');
    const { getTranslations } = await load('lib/i18n.ts');
    const html = render(MessageArea, {t:getTranslations('zh'), messages:[
      {id:'old',role:'user',content:'原请求',requestError:'原请求连接中断'},
      {id:'new',role:'assistant',content:'后续回复'}], scrollRef:{current:null},sending:false,
      selectedCount:0,onLoadOlder(){}});
    assert(html.indexOf('原请求连接中断') > html.indexOf('原请求</p>'));
    assert(html.indexOf('原请求连接中断') < html.indexOf('后续回复'));
  });
  await test('proposal checks show progress and concrete mismatches instead of irrelevant coverage instructions', async () => {
    const { DataOperationCard } = await load('features/data/DataOperationCard.tsx');
    const pending = render(DataOperationCard, {operation:{...batch,can_confirm:false,
      quality:{blocking_issues:['目标核验中'],proposal_review:{decision:'pending',issues:[]}}}});
    assert(pending.includes('正在核对方案') && !pending.includes('方案需要完善'));
    assert(!pending.includes('运行本批操作'));
    const mismatch = render(DataOperationCard, {operation:{...batch,can_confirm:false,
      quality:{blocking_issues:['核验失败'],proposal_review:{decision:'revise',issues:['实际规则仍保留了日期']}}}});
    assert(mismatch.includes('实际规则仍保留了日期') && !mismatch.includes('请继续完善未覆盖文件或冲突'));
  });
  await test('readiness and next-step ownership are consistent with Run and exact reviewed content', async () => {
    const { DataOperationCard } = await load('features/data/DataOperationCard.tsx');
    const script = {...batch,phase:'script',manifest_ready:false,script_plan_hash:'script-hash',can_run_script:true,can_confirm:false};
    const present = review => render(DataOperationCard,{operation:{...script,quality:{blocking_issues:[],proposal_review:review}},onRequestRevision(){}});
    const ready = present({decision:'ready',plan_hash:'script-hash',issues:[],step_role:'preparation'});
    assert(ready.includes('可运行 · 尚未执行') && ready.includes('运行脚本') && ready.includes('这是前置步骤'));
    const wrongHash = present({decision:'ready',plan_hash:'previous-content',issues:[]});
    assert(wrongHash.includes('核验未完成') && !wrongHash.includes('>运行脚本<'));
    const generated = render(DataOperationCard,{operation:{...script,phase:'files',manifest_ready:true,can_run_script:false,can_confirm:true,
      quality:{blocking_issues:[],proposal_review:{decision:'ready',issues:[],plan_hash:'script-hash'}}}});
    assert(generated.includes('可运行 · 尚未执行') && generated.includes('>运行本批操作<') && !generated.includes('>运行脚本<'));
    const changedScript = render(DataOperationCard,{operation:{...script,phase:'files',manifest_ready:true,can_run_script:false,can_confirm:true,script_plan_hash:'changed-script',
      quality:{blocking_issues:[],proposal_review:{decision:'ready',issues:[],plan_hash:'script-hash'}}}});
    assert(changedScript.includes('核验未完成') && !changedScript.includes('>运行本批操作<'));
    const revise = present({decision:'revise',issues:['详细实现依据'],fixes:[{area:'表格缺失值',change:'使用已确认的填充规则'}]});
    assert(revise.includes('需要模型修订') && revise.includes('无需手动修改代码') && revise.includes('使用已确认的填充规则'));
    assert(!revise.includes('>运行脚本<') && revise.includes('填写修订请求'));
    assert.match(revise, /<details[^>]*><summary>查看完整检查详情/);
    const clarify = present({decision:'clarify',issues:['缺少用户选择'],questions:['报告标题是什么？']});
    assert(clarify.includes('需要你补充信息') && clarify.includes('报告标题是什么？') && !clarify.includes('>运行脚本<'));
    const unavailable = present({decision:'unavailable',issues:['核验超时']});
    assert(unavailable.includes('不能据此判定代码有错') && unavailable.includes('填写重新核验请求') && !unavailable.includes('需要模型修订'));
    const conflict = render(DataOperationCard,{operation:{...batch,conflicts:['目标已存在']}});
    assert(conflict.includes('暂不能运行') && !conflict.includes('>运行本批操作<'));
  });
  await test('superseded proposals collapse across messages while independent plans and broken references stay available', async () => {
    const old = {...batch,id:'old',quality:{blocking_issues:['旧版'],proposal_review:{decision:'revise',issues:['已被新版替代'],superseded_by:'new'}}};
    const events = [old,{...batch,id:'new'},{...batch,id:'independent'}].map(data_operation=>({tool:'propose_data_changes',status:'pending',data_operation}));
    assert.deepEqual(latestOperationEvents(events).map(e=>e.data_operation.id),['new','independent']);
    assert.equal(latestOperationEvents(events.slice(0,1)).length,1);
    const cycle = [old,{...old,id:'new',quality:{blocking_issues:[],proposal_review:{decision:'revise',issues:[],superseded_by:'old'}}}].map(data_operation=>({tool:'propose_data_changes',status:'pending',data_operation}));
    assert.equal(latestOperationEvents(cycle).length,2);
    const { MessageArea } = await load('features/chat/MessageArea.tsx');
    const { getTranslations } = await load('lib/i18n.ts');
    const html = render(MessageArea,{t:getTranslations('zh'),messages:[
      {id:'before',role:'assistant',content:'旧回复',workspaceEvents:events.slice(0,1)},
      {id:'after',role:'assistant',content:'新回复',workspaceEvents:events.slice(1)}],scrollRef:{current:null},sending:false,selectedCount:0,onLoadOlder(){}});
    assert(!html.includes('id="data-operation-old"') && html.includes('id="data-operation-new"') && html.includes('id="data-operation-independent"'));
  });
  await test('only legacy generated audit replies fold; ordinary explanations remain visible', async () => {
    const { MessageArea } = await load('features/chat/MessageArea.tsx');
    const { legacyProposalReply } = await load('features/chat/replyPresentation.ts');
    const { getTranslations } = await load('lib/i18n.ts');
    const content = '方案尚未满足目标，当前不能运行：旧审核的技术长段。尚未修改文件。';
    const event = {tool:'propose_data_changes',status:'pending',data_operation:{...batch,can_confirm:false,quality:{blocking_issues:['检查失败'],proposal_review:{decision:'revise',issues:['实现问题']}}}};
    const message = {id:'audit',role:'assistant',content,workspaceEvents:[event]};
    assert(legacyProposalReply(message));
    assert(!legacyProposalReply({...message,content:'正常的说明或代码：'+content}));
    assert(!legacyProposalReply({...message,workspaceEvents:[]}));
    const html = render(MessageArea,{t:getTranslations('zh'),messages:[message],scrollRef:{current:null},sending:false,selectedCount:0,onLoadOlder(){}});
    assert(html.includes('方案的当前状态和下一步见方案面板'));
    assert.match(html, /<details[^>]*><summary>查看原始检查说明<\/summary>/);
    assert(html.includes('旧审核的技术长段') && html.includes('需要模型修订'));
  });
  await test('recovered tool errors do not expand the trace above a current proposal', () => {
    const html = render(WorkspaceTimeline,{events:[
      {tool:'run_data_python',status:'failed',error:'之前一次参数错误'},
      {tool:'run_data_python',status:'pending',data_operation:batch}]});
    assert(html.includes('次调用失败') && html.includes('之前一次参数错误'));
    assert(!html.includes('bg-panel text-meta" open'));
    assert(html.includes('可运行 · 尚未执行'));
  });
} finally {
  await server.close();
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage);
  else delete globalThis.localStorage;
}
