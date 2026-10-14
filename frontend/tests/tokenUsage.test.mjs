import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
Object.defineProperty(globalThis, 'localStorage', { configurable: true,
  value: { getItem: () => 'zh', setItem() {} } });
const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { useLanguageStore } = await server.ssrLoadModule('/src/stores/languageStore.ts');
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
  const { TokenUsage } = await server.ssrLoadModule('/src/features/chat/TokenUsage.tsx');
  const { toChatMessages } = await server.ssrLoadModule('/src/features/chat/chatMessages.ts');
  const usage = { calls: 3, reported_calls: 3, unavailable_calls: 0, complete: true,
    input_tokens: 1000, output_tokens: 200, total_tokens: 1200, reasoning_tokens: 40, cached_input_tokens: 700 };
  const render = props => renderToStaticMarkup(createElement(TokenUsage, props));
  await test('conversation totals explain subsets and historical limits without recounting them', () => {
    const html = render({ usage, conversation: true });
    assert(html.includes('会话 Token') && html.includes('1,200'));
    assert(html.includes('包含在输入中') && html.includes('包含在输出中'));
    assert(html.includes('历史用量无法补记'));
    assert(!html.includes('1,940') && !html.includes('部分统计'));
    assert(!html.includes(' open='));
  });
  await test('unknown, measured zero and partial usage remain visibly distinct', () => {
    assert(render({}).includes('Token 未记录'));
    const zero = render({ usage: { ...usage, total_tokens: 0, input_tokens: 0, output_tokens: 0 } });
    assert(zero.includes('Token 0') && !zero.includes('Token 未记录'));
    const partial = render({ usage: { ...usage, reported_calls: 2, unavailable_calls: 1, complete: false } });
    assert(partial.includes('部分统计') && partial.includes('次用量未完整返回'));
  });
  await test('stored replies receive server turn usage while legacy history stays unknown', () => {
    const records = [
      { message_id: 1, role: 'user', content: 'request', metadata: { client_turn_id: 'turn-a' }, created_at: 'now' },
      { message_id: 2, role: 'assistant', content: 'reply', metadata: { client_turn_id: 'turn-a' }, created_at: 'now' },
      { message_id: 3, role: 'assistant', content: 'legacy', metadata: {}, created_at: 'now' }
    ];
    const messages = toChatMessages(records, { 'turn-a': usage });
    assert.deepEqual(messages[1].tokenUsage, usage);
    assert.equal(messages[2].tokenUsage, undefined);
    assert.equal(toChatMessages(records)[0].tokenUsage, undefined);
    assert.deepEqual(toChatMessages(records, { 'turn-a': usage }), messages);
  });
} finally {
  await server.close();
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage);
  else delete globalThis.localStorage;
}
