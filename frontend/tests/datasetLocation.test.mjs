import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { resolveDatasetLocation, datasetAbsolutePath } = await server.ssrLoadModule('/src/features/datasets/datasetLocation.ts');
  const shared = { root_id: 'data', path: '/home/data', readable: true };
  const system = { root_id: 'server', path: '/', readable: true };
  await test('pasting a full server path selects the matching root without duplicating its prefix', () => {
    assert.deepEqual(resolveDatasetLocation([shared, system], '/home/data/xy/dataset', 'server'),
      { rootId: 'data', relativePath: 'xy/dataset' });
    assert.deepEqual(resolveDatasetLocation([shared, system], '/home/hipeson/dataset/', 'data'),
      { rootId: 'server', relativePath: 'home/hipeson/dataset' });
    assert.deepEqual(resolveDatasetLocation([system], '/', 'server'), { rootId: 'server', relativePath: '' });
    assert.equal(datasetAbsolutePath('/', 'home/data'), '/home/data');
    assert.equal(datasetAbsolutePath('/home/data', ''), '/home/data/');
  });
  await test('relative paths keep their root and absolute paths respect directory boundaries', () => {
    assert.deepEqual(resolveDatasetLocation([shared], 'xy/dataset', 'data'), { rootId: 'data', relativePath: 'xy/dataset' });
    assert.deepEqual(resolveDatasetLocation([shared], '/home/data', 'data'), { rootId: 'data', relativePath: '' });
    assert.equal(resolveDatasetLocation([shared], '/home/data-other', 'data'), null);
    assert.equal(resolveDatasetLocation([shared, system], '/home/data/../secret', 'data'), null);
    assert.equal(resolveDatasetLocation([{ ...shared, readable: false }], '/home/data/test', 'data'), null);
  });
  await test('Windows paths preserve drive roots and compare paths without case sensitivity', () => {
    const windows = [{ root_id: 'windows', path: 'D:\\datasets', readable: true }];
    assert.deepEqual(resolveDatasetLocation(windows, 'd:\\Datasets\\sub-01', 'windows'),
      { rootId: 'windows', relativePath: 'sub-01' });
    assert.equal(resolveDatasetLocation(windows, 'D:\\datasets-other', 'windows'), null);
  });
} finally { await server.close(); }
