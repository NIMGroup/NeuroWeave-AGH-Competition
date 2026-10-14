import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const code = ts.transpileModule(await readFile(new URL('../../integrations/agh/web-management-adapter.ts.txt', import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.None } }).outputText;
function fixture() {
  const calls = { archives: [], forks: [], renamed: [] };
  const state = { connected: true, sessionPending: false, sending: false, stopping: false, liveApproval: undefined,
    current: { id: 'selected' }, selectedWorkspace: { path: '/work' }, clientModules: { session: {} },
    sessionTitles: new Map(), list: async () => {}, renderControls() {}, updateWorkspaceOptions() {}, updateSidebar() {},
    updateTitle() {}, refreshWorkspaces: async () => {}, open: async () => {},
    beginNewDraft: async () => { state.current = undefined; }, forkSidebar: async (id, title) => calls.forks.push({ id, title }),
    client: { call: async () => ({ turns: [{ status: 'completed' }] }),
      workspace: { list: async () => ({ items: [{ path: '/work', name: 'original', available: true }] }) },
      session: { list: async () => ({ items: [{ sessionId: 'selected', title: 'old' }] }),
        rename: async (id, title) => calls.renamed.push({ id, title }),
        archive: async (id, archived) => calls.archives.push({ id, archived }) } } };
  runInNewContext(code, state);
  return { state, calls, manage: state.clientModules.session.manage };
}
await test('only owned workbench effect commands skip browser approval', () => {
  const {state}=fixture();
  for(const service of ['nw.panel.edit','nw.session.select','nw.action.prepare','nw.action.submit','nw.workspace.edit','nw.workspace.remove']) {
    assert.equal(state.authorizeWorkbenchCommand('web:@neuroweave/agh-roi',{effectService:service}),true);
    assert.equal(state.authorizeWorkbenchCommand('web:@another/plugin',{effectService:service}),false);
  }
  assert.equal(state.authorizeWorkbenchCommand('web:@neuroweave/agh-roi',{effectService:'other.execute'}),false);
  assert.equal(state.authorizeWorkbenchCommand('web:@neuroweave/agh-roi',{}),false);
});
await test('native rename replaces cached title and workspace aliases keep the real cwd', async () => {
  const { state, calls, manage } = fixture();
  await manage('rename', { session_id: 'selected', title: 'new name' });
  assert.equal(state.sessionTitles.get('selected'), 'new name');
  assert.deepEqual(JSON.parse(JSON.stringify(calls.renamed)), [{ id: 'selected', title: 'new name' }]);
  await manage('workspace-labels', { workspaces: [{ path: '/work', name: '研究' }] });
  const records = await manage('list');
  assert.equal(records.workspaces[0].path, '/work'); assert.equal(records.workspaces[0].name, '研究');
});
await test('workspace removal preserves its native record and associated session for restoration', async () => {
  const { state, manage, calls } = fixture();
  state.client.session.list = async () => ({ items: [{ sessionId: 'selected', cwd: '/work', title: 'kept' }] });
  await manage('workspace-labels', { workspaces: [{ path: '/work', name: '保留名称', removed: true }] });
  assert.equal(state.visibleWorkbenchWorkspaces([{ path: '/work', name: 'original', available: true }]).length, 0);
  const records = await manage('list');
  assert.equal(records.workspaces[0].removed, true); assert.equal(records.workspaces[0].path, '/work');
  assert.equal(records.sessions[0].sessionId, 'selected'); assert.equal(state.current.id, 'selected');
  assert.equal(calls.archives.length, 0);
  await manage('workspace-labels', { workspaces: [{ path: '/work', name: '保留名称', removed: false }] });
  assert.equal(state.visibleWorkbenchWorkspaces([{ path: '/work', name: 'original', available: true }])[0].name, '保留名称');
});
await test('delete refuses active turns and native approvals without canceling or detaching them', async () => {
  const { state, calls, manage } = fixture();
  state.client.call = async () => ({ turns: [{ status: 'running' }] });
  await assert.rejects(manage('archive', { session_id: 'selected', archived: true }), /仍在运行/);
  state.client.call = async () => ({ opState: {}, turns: [] });
  await assert.rejects(manage('archive', { session_id: 'selected', archived: true }), /等待审批/);
  assert.equal(calls.archives.length, 0); assert.equal(state.current.id, 'selected');
});
await test('delete clears the selected session; restore reuses its original identity', async () => {
  const { state, calls, manage } = fixture();
  await manage('archive', { session_id: 'selected', archived: true });
  assert.equal(state.current, undefined);
  await manage('archive', { session_id: 'selected', archived: false });
  assert.deepEqual(JSON.parse(JSON.stringify(calls.archives)), [{ id: 'selected', archived: true }, { id: 'selected', archived: false }]);
});
await test('a stalled native pagination fails rather than showing incomplete deletable records', async () => {
  const { state, manage } = fixture();
  state.client.session.list = async () => ({ items: [], next: 'same' });
  await assert.rejects(manage('list'), /分页未前进/);
});
await test('management on a blank draft uses an existing native record without selecting it', async () => {
  const { state, manage } = fixture();
  state.current = undefined;
  state.profileName = 'local-dev';
  const loaded = [];
  state.client.session.load = async id => { loaded.push(id); return {}; };
  state.client.clientModules = { list: async () => ({ rows: [{ rowId: 'web:@neuroweave/agh-roi', packageId: '@neuroweave/agh-roi',
    phase: 'ready', enabled: true, services: ['nw.workspace.query'] }] }) };
  let request;
  state.fetch = async (path, options) => { request = { path, ...JSON.parse(options.body) }; return { ok: true, json: async () => ({ output: { ok: true, result: { workspaces: [] } } }) }; };
  const result = await manage('service', { name: 'nw.workspace.query', input: {} });
  assert.equal(result.ok, true); assert.equal(request.sessionId, 'selected');
  await manage('service', { name: 'nw.workspace.query', input: {} });
  assert.deepEqual(loaded, ['selected']);
  assert.equal(state.current, undefined);
  await assert.rejects(manage('service', { name: 'nw.action.prepare', input: {} }), /仅支持/);
});
