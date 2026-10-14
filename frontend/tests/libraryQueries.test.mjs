import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { QueryClient } from '@tanstack/react-query';

const server = await createServer({
  root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom'
});
try {
  const { methodQuery, refreshPipelineViews } = await server.ssrLoadModule('/src/features/library/libraryQueries.ts');

  await test('simultaneous method consumers share a request and release its abort signal on cancellation', async () => {
    const client = new QueryClient();
    const original = globalThis.fetch;
    let calls = 0, signal;
    globalThis.fetch = async (_url, options) => {
      calls++; signal = options.signal;
      return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
    };
    try {
      const options = methodQuery('mrtrix.command.mrconvert', 'zh');
      const requests = [client.fetchQuery(options), client.fetchQuery(options)];
      const settled = Promise.allSettled(requests);
      assert.equal(calls, 1);
      await client.cancelQueries({ queryKey: options.queryKey });
      assert.equal(signal.aborted, true);
      assert.ok((await settled).every(result => result.status === 'rejected'));
    } finally { globalThis.fetch = original; client.clear(); }
  });

  await test('publication changes invalidate every version view without invalidating another Pipeline or method contract', async () => {
    const client = new QueryClient();
    const affected = [
      ['pipeline-detail', 'user-a', '1', 'zh'], ['pipeline-detail', 'user-a', '2', 'en'],
      ['pipeline-detail', 'user-a', undefined, 'zh'],
      ['library', 'pipeline', 'catalog'], ['pipeline-trash'], ['pipeline-versions', 'user-a'],
      ['pipeline-source', 'user-a', '1'], ['pipeline-source', 'user-a', '2'],
      ['pipeline-migration', 'user-a', '1']
    ];
    const untouched = [
      ['pipeline-detail', 'user-b', '1', 'zh'],
      ['pipeline-source', 'user-b', '1'], ['pipeline-versions', 'user-b'],
      methodQuery('mrtrix.command.mrconvert', 'zh').queryKey
    ];
    for (const key of [...affected, ...untouched]) client.setQueryData(key, { fixture: true });
    try {
      await refreshPipelineViews(client, 'user-a');
      for (const key of affected) assert.equal(client.getQueryState(key).isInvalidated, true, JSON.stringify(key));
      for (const key of untouched) assert.equal(client.getQueryState(key).isInvalidated, false, JSON.stringify(key));
    } finally { client.clear(); }
  });
} finally { await server.close(); }
