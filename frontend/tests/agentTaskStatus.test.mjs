import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => 'zh', setItem() {} } });
const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { useLanguageStore } = await server.ssrLoadModule('/src/stores/languageStore.ts');
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
  const { AgentTaskStatus } = await server.ssrLoadModule('/src/features/chat/AgentTaskStatus.tsx');
  const { refreshNewestMessages } = await server.ssrLoadModule('/src/features/chat/chatMessages.ts');
  const render = task => renderToStaticMarkup(createElement(AgentTaskStatus, { task }));
  await test('task phases distinguish awaiting manual Run, result verification and completion', () => {
    const base = { task_id: 'goal', goal: '处理数据', deliverable: 'files' };
    assert(render({ ...base, phase: 'awaiting_run' }).includes('等待点击运行'));
    assert(render({ ...base, phase: 'verifying' }).includes('核验结果'));
    assert(render({ ...base, phase: 'completed' }).includes('目标已完成'));
    assert(!render({ ...base, phase: 'awaiting_run' }).includes('<button'));
    assert.equal(render({ ...base, phase: 'answered' }), '');
  });
  await test('long goals wrap inside conversation information and errors state the next step', () => {
    const base = { task_id: 'goal', goal: '长目标'.repeat(1000), deliverable: 'files' };
    const html = render({ ...base, phase: 'blocked', next_step: '继续核验已完成的结果' });
    assert(html.includes('shrink-0') && html.includes('whitespace-pre-wrap break-words'));
    assert(html.includes('继续核验已完成的结果') && html.includes('role="status"'));
  });
  await test('understanding separates user requirements, scope, success criteria and assumptions', () => {
    const html = render({ task_id: 'goal', goal: '汇总数据', deliverable: 'files', phase: 'awaiting_run',
      scope_notes: '选定目录内的 CSV', success_criteria: ['生成一张汇总表'], strategy: '按分组聚合',
      assumptions: ['输出为 CSV'], open_questions: ['分组字段'], decisions: [{ quote: '排除 backups', reference: 'message:1' }] });
    for (const text of ['当前需求理解', '选定目录内的 CSV', '完成条件', '模型假设（待核实）', '分组字段', '排除 backups']) assert(html.includes(text));
    assert(!html.includes('<button'));
  });
  await test('automatic result refresh preserves previously loaded history and updates the newest page', () => {
    const message = sequence => ({ id: 'stored-'+sequence, sequence, role: 'assistant', content: String(sequence), createdAt: 'now' });
    const older = Array.from({ length: 80 }, (_, index) => message(index+1));
    const latest = Array.from({ length: 40 }, (_, index) => message(index+43));
    latest[0].content = 'new state';
    const merged = refreshNewestMessages(older, latest);
    assert.equal(merged.length, 82);
    assert.equal(merged[0].sequence, 1);
    assert.equal(merged.at(-1).sequence, 82);
    assert.equal(merged.find(m => m.sequence === 43).content, 'new state');
    assert.equal(new Set(merged.map(m => m.id)).size, merged.length);
  });
} finally {
  await server.close();
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage);
  else delete globalThis.localStorage;
}
