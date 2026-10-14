import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { installTransport } = await server.ssrLoadModule('/src/features/agh/host.ts');
  const { setRequestTransport, request } = await server.ssrLoadModule('/src/api/http.ts');
  const { runWorkflowLifecycleAction, completeDependencies } = await server.ssrLoadModule('/src/features/workflows/workflowsApi.ts');
  const { cancelOperation } = await server.ssrLoadModule('/src/api/operations.ts');
  function fixture() {
    const state = { session: 'original', workflow: { workflow_id: 'wf-test', revision: 4 }, prompts: [],
      panelWrites: [], cancels: 0, onPrompt: () => Promise.resolve(), dispose: undefined,
      task: {task_id:'task-one',goal:'完整且可编辑的当前目标'} };
    const host = {
      session: { getSnapshot: () => state.session, commands: {
        prompt: text => { state.prompts.push(text); return state.onPrompt(); },
        cancel: async () => { state.cancels++; state.onCancel?.(); }
      } },
      commands: { registerEffect() {}, execute: async () => ({ ok: true, result: { workflow: state.workflow } }) },
      agnes: { services: { call: async (name, args) => {
        if (name === 'nw.panel.edit') state.panelWrites.push(args);
        if (name === 'nw.panel.query' && args.input.path.startsWith('/api/tasks/')) {
          await state.onQuery?.();
          return {ok:true,result:{status:200,body:{task:state.task}}};
        }
        return { ok: true, result: { workflow: state.workflow } };
      } } },
      effect: factory => { state.dispose = factory(); }
    };
    installTransport(host);
    return state;
  }
  await test('task goal replan unwraps the scoped HTTP response before native AGH prompting', async () => {
    const state=fixture();
    try {
      const result=await request('/api/tasks/task-one/replan',{method:'POST'});
      assert.equal(result.task_id,'task-one');
      assert.equal(state.prompts.length,1);
      assert.match(state.prompts[0],/完整且可编辑的当前目标/);
      assert.match(state.prompts[0],/nw_method_routes/);
      assert.equal(state.panelWrites.length,0);
      state.task.user_closure={outcome:'ended'};
      await assert.rejects(request('/api/tasks/task-one/replan',{method:'POST'}),/已归档/);
      assert.equal(state.prompts.length,1);
    } finally {state.dispose();setRequestTransport(null);}
  });
  await test('a session switch during task lookup cannot send the old goal to a new conversation', async () => {
    const state=fixture();state.onQuery=async()=>{state.session='another';};
    try {
      await assert.rejects(request('/api/tasks/task-one/replan',{method:'POST'}),/会话已切换/);
      assert.equal(state.prompts.length,0);
    } finally {state.dispose();setRequestTransport(null);}
  });
  await test('3003 detached replan and dependency jobs use AGH and deliver their saved revision', async () => {
    const state = fixture();
    state.onPrompt = async () => { state.workflow = { ...state.workflow, revision: state.workflow.revision + 1 }; };
    try {
      const progress = [];
      const replanned = await runWorkflowLifecycleAction('wf-test', 'replan', 4, false, value => progress.push(value));
      assert.equal(replanned.revision, 5);
      assert(progress[0].operation_id.startsWith('agh_plan_'));
      assert.match(state.prompts[0], /nw_method_routes/);
      const completed = await completeDependencies('wf-test', 5);
      assert.equal(completed.revision, 6);
      assert.equal(state.panelWrites.length, 0);
    } finally { state.dispose(); setRequestTransport(null); }
  });
  await test('stopping an owned AGH planning job waits for native cancellation and preserves the old workflow', async () => {
    const state = fixture(); let finish;
    state.onPrompt = () => new Promise(resolve => { finish = resolve; });
    state.onCancel = () => finish();
    try {
      const operation = await request('/api/workflows/wf-test/replan-jobs', { method: 'POST', body: JSON.stringify({ expected_revision: 4 }) });
      await cancelOperation(operation.operation_id);
      const stopped = await request('/api/operations/' + operation.operation_id);
      assert.equal(stopped.status, 'cancelled');
      assert.equal(state.cancels, 1);
      assert.equal(state.workflow.revision, 4);
    } finally { state.dispose(); setRequestTransport(null); }
  });
  await test('switching conversations cannot cancel another active session through an old job', async () => {
    const state = fixture();
    try {
      const operation = await request('/api/workflows/wf-test/replan-jobs', { method: 'POST', body: JSON.stringify({ expected_revision: 4 }) });
      state.session = 'another';
      await assert.rejects(cancelOperation(operation.operation_id), /原 AGH 会话/);
      assert.equal(state.cancels, 0);
    } finally { state.dispose(); setRequestTransport(null); }
  });
} finally { await server.close(); }
