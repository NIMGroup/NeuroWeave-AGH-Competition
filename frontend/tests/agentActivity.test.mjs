import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { workspaceActivities } = await server.ssrLoadModule('/src/features/workspace/workspaceActivity.ts');
  await test('independent agents cannot complete each other\'s identically named tool call', () => {
    const events = [
      { tool: 'read_shared_facts', agent: 'data', status: 'running' },
      { tool: 'read_shared_facts', agent: 'methods', status: 'running' },
      { tool: 'read_shared_facts', agent: 'data', status: 'succeeded' },
    ];
    const rows = workspaceActivities(events);
    assert.equal(rows.length, 2);
    assert.equal(rows.find(r => r.agent === 'data').status, 'succeeded');
    assert.equal(rows.find(r => r.agent === 'methods').status, 'running');
  });
  await test('tool activity identifies responsibility without adding header components', async () => {
    const { WorkspaceTimeline } = await server.ssrLoadModule('/src/features/workspace/WorkspaceTimeline.tsx');
    const { useLanguageStore } = await server.ssrLoadModule('/src/stores/languageStore.ts');
    const initial = useLanguageStore.getInitialState();
    useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
    const html = renderToStaticMarkup(createElement(WorkspaceTimeline, { events: [
      { tool: 'delegate_agent', agent: 'methods', status: 'succeeded' },
      { tool: 'compress_agent_context', agent: 'data', status: 'cancelled' },
    ] }));
    assert(/方法 · 分派事实调查|Methods · Delegate investigation/.test(html));
    assert(/数据 · 整理过程记忆|Data · Summarize process/.test(html) && /已停止|Stopped/.test(html));
    assert(!html.includes('<header'));
  });
} finally { await server.close(); }
