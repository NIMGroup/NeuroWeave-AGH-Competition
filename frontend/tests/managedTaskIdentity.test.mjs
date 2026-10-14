import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
const client = new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}});
const render = (component, props) => renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(component,props)));
try {
  const { useLanguageStore } = await server.ssrLoadModule('/src/stores/languageStore.ts');
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
  const { getTranslations } = await server.ssrLoadModule('/src/lib/i18n.ts');
  const { TaskDetails } = await server.ssrLoadModule('/src/features/workflows/TaskDetails.tsx');
  const { MessageArea } = await server.ssrLoadModule('/src/features/chat/MessageArea.tsx');
  const { ManagedTaskCard } = await server.ssrLoadModule('/src/features/workflows/ManagedTaskCard.tsx');
  const { taskIdentity, taskIndexUrl } = await server.ssrLoadModule('/src/features/workflows/taskIndex.ts');
  const { conversationPlan } = await server.ssrLoadModule('/src/features/chat/conversationPlan.ts');
  const { adoptWorkflowSnapshot } = await server.ssrLoadModule('/src/features/workflows/useWorkflows.ts');
  const operation = id => ({id,kind:'data',title:'文件整理方案',actor:'local',conversation_id:'conversation',mode:'copy',
    status:'pending',source_root:'/input',target_root:'/output',manifest_hash:id,manifest_ready:true,file_count:1,total_bytes:1,
    completed:0,can_confirm:true,can_undo:false,preview_expired:false,preview_truncated:false,items:[],conflicts:[],action_counts:{copy:1},
    quality:{complete:true,blocking_issues:[],regressed_files:0,accounted_files:1,proposal_review:{decision:'ready',issues:[],plan_hash:id}}});
  const task={task_id:'same-task',task_revision:2,goal:'整理文件',deliverable:'files',phase:'awaiting_run',
    active_plan:{kind:'data',id:'second',task_revision:2}};
  const entry={task_id:task.task_id,title:task.goal,status:task.phase,source:'conversation',workflow_id:null,plan_kind:'data',plan_id:'second',
    conversation_id:'conversation',run_count:0,latest_run_status:null,updated_at:'2026-10-13T00:00:00Z'};
  const view={entry,task,current_plan:task.active_plan,plan:operation('second'),deleted:false,
    versions:[{version:2,kind:'data',plan_id:'second',revision:'hash2'},{version:1,kind:'data',plan_id:'first',revision:'hash1'}]};
  client.setQueryData(['managed-task',task.task_id],view);
  await test('management and conversation show the same single task and current plan',()=>{
    const management=render(TaskDetails,{task:entry,sidebarOpen:true,onOpenSidebar(){},onOpenPipelines(){}});
    const conversation=render(MessageArea,{t:getTranslations('zh'),managedTask:task,activeWorkflowId:null,activeTask:null,
      messages:['first','second'].map(id=>({id,role:'assistant',content:'已更新方案',workspaceEvents:[{tool:'propose_data_changes',status:'succeeded',data_operation:operation(id)}]})),
      sending:false,loadingOlder:false,hasOlder:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    for(const html of [management,conversation]){
      assert.equal((html.match(/data-task-id="same-task"/g)||[]).length,1);
      assert.equal((html.match(/id="data-operation-second"/g)||[]).length,1);
      assert(!html.includes('id="data-operation-first"'));
    }
    assert.equal(taskIdentity(entry),'same-task');
    assert(taskIndexUrl(0,'','','').startsWith('/api/task-index?'));
  });
  await test('a new script or file plan version changes only the child plan, not its task identity',()=>{
    client.setQueryData(['managed-task',task.task_id],{...view,current_plan:{kind:'data',id:'third'},plan:operation('third')});
    const html=render(ManagedTaskCard,{taskId:task.task_id});
    assert(html.includes('data-task-id="same-task"') && html.includes('id="data-operation-third"'));
    assert(!html.includes('id="data-operation-second"'));
  });
  await test('lightweight task progress waits for proposal details without losing its identity',()=>{
    const {items, ...progress} = operation('third');
    client.setQueryData(['managed-task',task.task_id],{...view,current_plan:{kind:'data',id:'third'},plan:progress});
    const html=render(ManagedTaskCard,{taskId:task.task_id});
    assert(html.includes('data-task-id="same-task"') && /正在读取当前方案详情|Loading the current proposal details/.test(html));
    assert(!/查看操作预览|Review proposed changes/.test(html));
    assert.match(html, /button[^>]* disabled=""[^>]*>(运行本批操作|Run this batch)/);
  });
  await test('deleting a maintained task does not reactivate its historical plan cards',()=>{
    const html=render(MessageArea,{t:getTranslations('zh'),managedTask:{task_deleted:true,phase:'answered'},activeWorkflowId:null,activeTask:null,
      messages:[{id:'old',role:'assistant',content:'历史说明',workspaceEvents:[{tool:'propose_data_changes',status:'succeeded',data_operation:operation('first')}]}],
      sending:false,loadingOlder:false,hasOlder:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert(html.includes('历史说明'));
    assert(!html.includes('id="data-operation-first"') && !html.includes('data-task-id='));
  });
  const workflow = (id, revision) => ({workflow_id:id,revision,title:'流程当前版本'+revision,status:'awaiting_method_choice',
    execution_mode:'planning',plan_kind:'atomic',steps:[],dataset:null,workspace_id:null,requested_capabilities:[],warnings:[],summary:'流程说明'});
  const closedTask={...task,task_id:'closed-goal',phase:'user_confirmed',
    active_plan:{id:'closed-flow',kind:'workflow',revision:2,task_revision:2},
    user_completion:{confirmed_at:10,task_revision:2}};
  const closedView={...view,task:closedTask,entry:{...entry,task_id:'closed-goal',status:'user_confirmed'},
    current_plan:closedTask.active_plan,plan:workflow('closed-flow',2)};
  await test('a human-confirmed goal renders its frozen graph even when live cache has a later version',()=>{
    client.setQueryData(['managed-task','closed-goal'],closedView);
    client.setQueryData(['workflow','closed-flow'],workflow('closed-flow',99));
    const html=render(ManagedTaskCard,{taskId:'closed-goal',defaultExpanded:true});
    assert.equal((html.match(/data-task-id="closed-goal"/g)||[]).length,1);
    assert(html.includes('data-workflow-revision="2"') && !html.includes('流程当前版本99'));
    assert(/你已确认此目标完成|You confirmed this goal is complete/.test(html));
    assert(!/编辑任务目标|Edit task goal/.test(html));
  });
  await test('completed goals have one folded record and do not replace the one current card',()=>{
    client.setQueryData(['managed-task',task.task_id],view);
    client.setQueryData(['managed-task','closed-goal'],closedView);
    client.setQueryData(['conversation-task','goals'],{task,closed_tasks:[closedTask,task]});
    const html=render(MessageArea,{t:getTranslations('zh'),conversationId:'goals',managedTask:task,
      messages:[{id:'old-1',role:'assistant',content:'旧目标说明',workflow:workflow('closed-flow',1)},
        {id:'old-2',role:'assistant',content:'旧目标修订',workflow:workflow('closed-flow',2)},
        {id:'new',role:'assistant',content:'当前目标说明',workflow:workflow('other',99)}],
      sending:false,activeWorkflowId:null,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert.equal((html.match(/data-task-id="same-task"/g)||[]).length,1);
    assert.equal((html.match(/data-task-id="closed-goal"/g)||[]).length,1);
    assert.equal((html.match(/data-archived-goal="closed-goal"/g)||[]).length,1);
    assert(!html.includes('data-workflow-id="other"') && !html.includes('流程当前版本99'));
  });
  await test('ending the current unfinished goal immediately folds exactly one archived card',()=>{
    const ended={...task,task_id:'ended-goal',phase:'user_ended',
      active_plan:{id:'ended-flow',kind:'workflow',revision:2,task_revision:2},
      user_closure:{outcome:'ended',closed_at:10,task_revision:2}};
    client.setQueryData(['managed-task',ended.task_id],{...view,task:ended,
      entry:{...entry,task_id:ended.task_id,status:'user_ended'},
      current_plan:ended.active_plan,plan:workflow('ended-flow',2)});
    client.setQueryData(['conversation-task','ending'],{task:ended,closed_tasks:[ended]});
    const html=render(MessageArea,{t:getTranslations('zh'),conversationId:'ending',managedTask:ended,
      messages:[{id:'reply',role:'assistant',content:'保留修改说明',workflow:workflow('ended-flow',2)}],
      sending:false,activeWorkflowId:null,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert.equal((html.match(/data-task-id="ended-goal"/g)||[]).length,1);
    assert.equal((html.match(/data-archived-goal="ended-goal"/g)||[]).length,1);
    assert(/已结束（未完成）|Ended without completion/.test(html));
    assert(!html.includes('href="#conversation-task-workspace"'));
    assert(!/details[^>]*data-archived-goal="ended-goal"[^>]*open/.test(html));
    assert(!/你已确认此目标完成|You confirmed this goal is complete/.test(html));
  });
  await test('the retained workflow card adopts newer snapshots without accepting older or unrelated graphs',()=>{
    client.setQueryData(['workflow','flow'],workflow('flow',1));
    adoptWorkflowSnapshot(client,'flow',workflow('flow',2));
    assert.equal(client.getQueryData(['workflow','flow']).revision,2);
    adoptWorkflowSnapshot(client,'flow',workflow('flow',1));
    assert.equal(client.getQueryData(['workflow','flow']).revision,2);
    adoptWorkflowSnapshot(client,'flow',workflow('other',99));
    assert.equal(client.getQueryData(['workflow','flow']).revision,2);
    assert.equal(client.getQueryData(['workflow','other']),undefined);
  });
  await test('workflow revisions and repeated history snapshots share one viewport and one graph',()=>{
    const history = [workflow('flow',1),workflow('flow',2),workflow('flow',1)].map((wf,i)=>({id:'reply-'+i,role:'assistant',content:'修订说明',workflow:wf}));
    assert.equal(conversationPlan(history,null,'flow',workflow('flow',3)).workflow.revision,3);
    client.setQueryData(['workflow','flow'],workflow('flow',3));
    const html=render(MessageArea,{t:getTranslations('zh'),messages:history,activeWorkflowId:'flow',activeWorkflow:workflow('flow',3),
      sending:false,loadingOlder:false,hasOlder:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert.equal((html.match(/id="conversation-task-workspace"/g)||[]).length,1);
    assert.equal((html.match(/data-workflow-id="flow"/g)||[]).length,1);
    assert.equal((html.match(/href="#conversation-task-workspace"/g)||[]).length,0);
    assert(html.includes('data-workflow-revision="3"') && !html.includes('流程当前版本1'));
  });
  await test('another workflow cannot seed the active graph while its current snapshot is loading',()=>{
    const selected=conversationPlan([{id:'old',role:'assistant',workflow:workflow('old-flow',99)}],null,'new-flow',workflow('old-flow',99));
    assert.equal(selected.workflowId,'new-flow');
    assert.equal(selected.workflow,null);
    client.removeQueries({queryKey:['workflow','new-flow']});
    const html=render(MessageArea,{t:getTranslations('zh'),messages:[{id:'old',role:'assistant',content:'旧流程说明',workflow:workflow('old-flow',99)}],
      activeWorkflowId:'new-flow',activeWorkflow:workflow('old-flow',99),sending:false,hasOlder:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert.equal((html.match(/data-workflow-id="new-flow"/g)||[]).length,1);
    assert(!html.includes('data-workflow-id="old-flow"') && !html.includes('流程当前版本99'));
    assert.equal(client.getQueryData(['workflow','new-flow']),undefined);
  });
  await test('live and historical proposals cannot compete with the authoritative current task card',()=>{
    client.setQueryData(['conversation-task','conversation'],{task});
    client.setQueryData(['managed-task',task.task_id],view);
    const html=render(MessageArea,{t:getTranslations('zh'),conversationId:'conversation',managedTask:null,
      messages:[{id:'history',role:'assistant',content:'旧说明',workspaceEvents:[{tool:'propose_data_changes',status:'pending',data_operation:operation('first')}]}],
      liveWorkspaceEvents:[{tool:'propose_data_changes',status:'pending',data_operation:operation('third')}],
      activeWorkflowId:null,sending:true,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert.equal((html.match(/data-task-id="same-task"/g)||[]).length,1);
    assert.equal((html.match(/id="data-operation-second"/g)||[]).length,1);
    assert(!html.includes('id="data-operation-first"') && !html.includes('id="data-operation-third"'));
  });
  await test('an empty restored conversation still has its single task card',()=>{
    const html=render(MessageArea,{t:getTranslations('zh'),managedTask:task,messages:[],activeWorkflowId:null,
      sending:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert.equal((html.match(/data-task-id="same-task"/g)||[]).length,1);
    assert.equal((html.match(/id="conversation-task-workspace"/g)||[]).length,1);
    assert(!html.includes(getTranslations('zh').startConversation));
  });
  await test('a newer goal disables the older proposal in the same card until synchronization finishes',()=>{
    client.setQueryData(['managed-task',task.task_id],view);
    const newer={...task,goal:'新的整理要求',task_revision:3};
    const html=render(ManagedTaskCard,{taskId:task.task_id,initialTask:newer});
    assert.equal((html.match(/data-task-id="same-task"/g)||[]).length,1);
    assert(html.includes('新的整理要求'));
    assert.match(html, /button[^>]* disabled=""[^>]*>(运行本批操作|Run this batch)/);
  });
  await test('a known workflow remains in its single viewport while a revision request is pending',()=>{
    client.setQueryData(['conversation-task','legacy-conversation'],{task:null});
    const html=render(MessageArea,{t:getTranslations('zh'),conversationId:'legacy-conversation',messages:[{id:'reply',role:'assistant',content:'修改说明',workflow:workflow('flow',2)}],
      activeWorkflowId:'flow',activeWorkflow:workflow('flow',3),sending:true,hasOlder:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert.equal((html.match(/data-workflow-id="flow"/g)||[]).length,1);
    assert.equal((html.match(/id="conversation-task-workspace"/g)||[]).length,1);
  });
  await test('a new answer-only task cannot revive an archived workflow from history or an older pointer cache',()=>{
    client.setQueryData(['conversation-task','new-answer'],{task:{...task,updated_at:100}});
    const current={task_id:'answer-task',task_revision:1,goal:'解释参数',phase:'answered',deliverable:'answer',updated_at:200};
    const history=[{id:'older',role:'assistant',content:'历史说明',workflow:workflow('flow',3)}];
    assert.equal(conversationPlan(history,current,'flow').workflowId,null);
    const html=render(MessageArea,{t:getTranslations('zh'),conversationId:'new-answer',managedTask:current,messages:history,
      activeWorkflowId:null,sending:false,hasOlder:false,selectedCount:0,onLoadOlder(){},scrollRef:{current:null}});
    assert(!html.includes('data-workflow-id=') && !html.includes('data-task-id='));
  });
} finally { await server.close(); client.clear(); }
