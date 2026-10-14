import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile, mkdtemp, mkdir, rm, symlink } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { tmpdir } from 'node:os';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { createServer } from 'vite';
import { applyWebAdapter } from '../../scripts/hackathon/apply-web-adapter.mjs';

const root = resolve(import.meta.dirname, '..');
const code = ts.transpileModule(await readFile(resolve(root, '../integrations/agh/web-session-adapter.ts.txt'), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.None } }).outputText;
function fixture() {
  const calls = { created: [], models: [], permissions: [], prompts: 0 };
  const created = { id: 'real-empty', detach: async () => {},
    setModel: async args => { calls.models.push(args); return { effectiveFromSeq: 1 }; },
    setYolo: async enabled => { calls.permissions.push(enabled); return { effectiveFromSeq: 2 }; },
    prompt: () => { calls.prompts++; } };
  const state = { current: undefined, connected: true, sending: false, sessionPending: false, draftingNew: true,
    selectedWorkspace: { available: true, path: '/work/selected' }, selection: 1, permissionConnectionEpoch: 1,
    knownSessionModel: { route: 'agnes', id: 'flash', settings: { thinking: 'low' } },
    pendingSessionKey: 'draft-key', newSessionCreating: false, permissionMode: 'full',
    initialModelPending: undefined, initialPermissionPending: undefined, permissionRefreshPending: false,
    sessionYoloEnabled: undefined, modelSelectionSeq: 0, permissionSelectionSeq: 0,
    crypto: { randomUUID: () => 'random-key' }, renderControls() {}, render() {}, openNewSessionDialog() {},
    yoloEnabled: mode => mode === 'full', clientModules: { session: {} },
    client: { session: { new: async args => { calls.created.push(args); return created; } } },
    open: async (id, options) => { assert.equal(id, created.id); state.current = options.created;
      state.initialModelPending = options.initialModel; state.initialPermissionPending = state.permissionMode; } };
  runInNewContext(code, state);
  return { state, calls, created, ensure: state.clientModules.session.ensure };
}
await test('first workbench open creates exactly one real empty session and preserves draft model and permissions', async () => {
  const { calls, ensure, state } = fixture();
  // The real Cordis service proxy returns a bound method rather than the raw value.
  const proxied = new Proxy(state.clientModules.session, { get(target, key, receiver) {
    const value = Reflect.get(target, key, receiver); return typeof value === 'function' ? value.bind(receiver) : value;
  } });
  assert.equal(typeof proxied.ensure, 'function');
  assert.deepEqual(await Promise.all([ensure(), ensure()]), ['real-empty', 'real-empty']);
  assert.equal(await ensure(), 'real-empty');
  assert.equal(calls.created.length, 1);
  assert.equal(calls.created[0].cwd, '/work/selected'); assert.equal(calls.created[0].sessionKey, 'draft-key');
  assert.equal(calls.models[0].model, 'flash'); assert.deepEqual(calls.permissions, [true]);
  assert.equal(calls.prompts, 0); assert.equal(state.sessionPending, false);
});
await test('unavailable workspace or pending selection creates no session', async () => {
  const { state, calls, ensure } = fixture();
  state.selectedWorkspace.available = false; await assert.rejects(ensure(), /工作区/);
  state.selectedWorkspace.available = true; state.sessionPending = true;
  await assert.rejects(ensure(), /正在准备/); assert.equal(calls.created.length, 0);
});
await test('failed creation keeps the draft key and releases the busy state for an idempotent retry', async () => {
  const { state, ensure, calls, created } = fixture();
  state.client.session.new = async () => { throw new Error('offline'); };
  await assert.rejects(ensure(), /offline/);
  assert.equal(state.pendingSessionKey, 'draft-key'); assert.equal(state.sessionPending, false);
  state.client.session.new = async args => { calls.created.push(args); return created; };
  assert.equal(await ensure(), 'real-empty'); assert.equal(calls.created[0].sessionKey, 'draft-key');
});
await test('switching drafts during creation cannot attach the old session to the new draft', async () => {
  const { state, ensure, created } = fixture(); let finish;
  state.client.session.new = () => new Promise(resolve => { finish = resolve; });
  const result = ensure(); state.selection++; finish(created);
  await assert.rejects(result, /会话已切换/); assert.equal(state.current, undefined);
});
const server = await createServer({ root, configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { ensureWorkbenchSession } = await server.ssrLoadModule('/src/features/agh/host.ts');
  await test('business configuration is available only after the host selects the created session', async () => {
    let session;
    const host = { session: { getSnapshot: () => session, ensure: async () => { session = 'created'; return session; } } };
    assert.equal(await ensureWorkbenchSession(host), 'created');
    session = undefined; host.session.ensure = async () => 'unselected';
    await assert.rejects(ensureWorkbenchSession(host), /会话已切换/);
    delete host.session.ensure; await assert.rejects(ensureWorkbenchSession(host), /刷新/);
  });
} finally { await server.close(); }
await test('Web adapter refuses unexpected upstream source and a shared symlink runtime', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'nw-web-adapter-'));
  try {
    await mkdir(join(directory, 'packages/web/src'), { recursive: true });
    const { writeFile } = await import('node:fs/promises');
    await writeFile(join(directory, 'packages/web/src/app.ts'), 'unexpected source');
    await assert.rejects(applyWebAdapter(directory), /does not match pinned/);
    await symlink(directory, join(directory, 'shared'), process.platform === 'win32' ? 'junction' : 'dir');
    await assert.rejects(applyWebAdapter(join(directory, 'shared')), /own runtime/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
