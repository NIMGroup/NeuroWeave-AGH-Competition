import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { TaskEditor } = await server.ssrLoadModule('/src/features/chat/TaskEditor.tsx');
  const base = { task: { task_id: 'same-task', goal: '处理数据', deliverable: 'workflow', phase: 'completed', task_revision: 4 },
    conversationId: 'conversation', zh: true, onUpdated() {} };
  await test('a completed task can be edited and updated without an execution button', () => {
    const html = renderToStaticMarkup(createElement(TaskEditor, { ...base, busy: false }));
    assert(html.includes('编辑任务目标') && html.includes('更新方案') && html.includes('确认完成并结束此目标'));
    assert(!html.includes('重新运行') && !html.includes('点击运行'));
    assert(!html.includes('disabled=""'));
  });
  await test('editing cannot race a background request and the stop instruction remains visible', () => {
    const html = renderToStaticMarkup(createElement(TaskEditor, { ...base, busy: true }));
    assert.equal((html.match(/disabled=""/g) || []).length, 4);
    assert(html.includes('先停止当前请求'));
  });
  await test('human acceptance archives the goal instead of offering edit or rerun', () => {
    const html = renderToStaticMarkup(createElement(TaskEditor, { ...base, busy: false,
      task: { ...base.task, phase:'user_confirmed',user_completion:{confirmed_at:10,task_revision:4} } }));
    assert(html.includes('你已确认此目标完成') && html.includes('删除任务记录'));
    assert(!html.includes('编辑任务目标') && !html.includes('更新方案') && !html.includes('确认完成并结束此目标'));
  });
  await test('an edited goal identifies the old plan and keeps the update action available', () => {
    const html = renderToStaticMarkup(createElement(TaskEditor, { ...base, busy: false,
      task: { ...base.task, task_revision: 5, active_plan: { id: 'workflow', kind: 'workflow', task_revision: 4 } } }));
    assert(html.includes('当前方案对应旧目标') && html.includes('更新方案'));
    assert(!html.includes('disabled=""'));
    assert(html.includes('结束目标并归档'));
  });
  await test('an unfinished draft offers explicit End separately from completion confirmation', () => {
    const html=renderToStaticMarkup(createElement(TaskEditor,{...base,busy:false,
      task:{...base.task,phase:'awaiting_inputs'}}));
    assert(html.includes('确认完成并结束此目标') && html.includes('结束目标并归档'));
  });
  await test('an ended goal is read-only and never described as confirmed complete', () => {
    const html=renderToStaticMarkup(createElement(TaskEditor,{...base,busy:false,
      task:{...base.task,phase:'user_ended',user_closure:{outcome:'ended',closed_at:10,task_revision:4}}}));
    assert(html.includes('此目标已主动结束，尚未完成'));
    assert(!html.includes('你已确认此目标完成') && !html.includes('编辑任务目标') && !html.includes('更新方案'));
  });
} finally { await server.close(); }
