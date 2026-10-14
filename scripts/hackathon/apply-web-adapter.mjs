import { createHash } from 'node:crypto';
import { readFile, writeFile, realpath } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Apply only to an isolated runtime copy. Never patch a shared AGH installation.
export async function applyWebAdapter(directory) {
  const root = await realpath(directory);
  if (root !== resolve(directory)) throw new Error('AGH adapter requires its own runtime directory, not a symlink');
  const path = resolve(root, 'packages/web/src/app.ts');
  const adapter = (await readFile(new URL('../../integrations/agh/web-session-adapter.ts.txt', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
  const management = (await readFile(new URL('../../integrations/agh/web-management-adapter.ts.txt', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
  const source = (await readFile(path, 'utf8')).replaceAll('\r\n', '\n');
  const substitutions = [
    ['authorizeCommand: ({ owner, command }) =>\n    window.confirm(', 'authorizeCommand: ({ owner, command }) =>\n    authorizeWorkbenchCommand(owner, command) || window.confirm('],
    ['sessions: sessionRows,', 'sessions: sessionRows.filter(row => !row.cwd || !neuroWeaveRemovedWorkspaces.has(row.cwd)),'],
    ['workspaces: workspaceRows,', 'workspaces: visibleWorkbenchWorkspaces(workspaceRows),'],
    ['selectedWorkspace?.name ??', '(selectedWorkspace ? namedWorkbenchWorkspace(selectedWorkspace).name : undefined) ??'],
    ["renderWorkspaceOptions(element('workspace-option-items', 'div'), workspaceRows,", "renderWorkspaceOptions(element('workspace-option-items', 'div'), visibleWorkbenchWorkspaces(workspaceRows),"],
    ['function renderControls(): void {', 'function renderControls(): void {\n  if (!current && selectedWorkspace && neuroWeaveRemovedWorkspaces.has(selectedWorkspace.path)) selectedWorkspace = undefined'],
  ];
  let original = source.includes(adapter) ? source.replace(adapter, '') : source;
  if (original.includes(management)) {
    original = original.replace(management, '');
    for (const [before, after] of substitutions) original = original.replace(after, before);
  }
  const digest = value => createHash('sha256').update(value).digest('hex');
  if (digest(original) !== '8d5730800d1d11f8fa21fe6a83226a7dd810c56ba33f3664d943fd82dfc3b4f8')
    throw new Error('AGH Web source does not match pinned dc267c3; refusing to patch');
  const anchor = 'const diagnostics = createDiagnosticsDialog({';
  if (original.split(anchor).length !== 2) throw new Error('AGH Web adapter anchor is ambiguous');
  let patched = original.replace(anchor, adapter + management + anchor);
  for (const [before, after] of substitutions) {
    if (patched.split(before).length !== 2) throw new Error('AGH management adapter anchor is ambiguous');
    patched = patched.replace(before, after);
  }
  await writeFile(path, patched);
  return { upstream: 'dc267c3c78429933e2b3d8c5d544e56bb980a574', web_source_sha256: digest(patched) };
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) throw new Error('Pass the isolated AGH source directory');
  console.log(JSON.stringify(await applyWebAdapter(resolve(process.argv[2]))));
}
