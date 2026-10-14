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
  const load = path => server.ssrLoadModule('/src/' + path);
  const { outputPreviewTree } = await load('features/workflows/outputPreviewTree.ts');
  const { OutputTree } = await load('features/workflows/OutputTree.tsx');
  const { OutputPreviewContent } = await load('features/workflows/OutputPreviewContent.tsx');
  const { useLanguageStore } = await load('stores/languageStore.ts');
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.setState({ language: 'zh' });
  useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
  await test('sample and step hierarchy keeps identity and merges directory declarations', () => {
    const tree = outputPreviewTree([{ path: '002_S_0413/fod', directory: true, label: 'FOD' },
      { path: '002_S_0413/fod/wm.mif' }, { path: '002_S_0413/tractography/tracks.tck' },
      { path: '../outside' }, { path: '/absolute' }]);
    assert.equal(tree.length, 1);
    assert.equal(tree[0].name, '002_S_0413');
    assert.equal(tree[0].children.length, 2);
    assert.equal(tree[0].children[0].children[0].name, 'wm.mif');
    const html = renderToStaticMarkup(createElement(OutputTree, { entries: [{ path: '002_S_0413/fod/wm.mif' }] }));
    assert(html.includes('002_S_0413/') && html.includes('fod/') && html.includes('wm.mif'));
  });
  const preview = { root_template: '/selected/output/{new_run_id}', sample_mode: 'per_sample',
    requested_sample_mode: 'single', settings_need_change: true, naming_rule: 'sample_id',
    sample_count: 499, unit_count: 998, declared_output_count: 1497, excluded_sample_count: 0,
    issues: [], groups: [{ sample_id: '002_S_0413', sample_label: '002_S_0413', source_relative_root: '002_S_0413',
      output_sample_directory: '002_S_0413', folder_matches_input_name: true, sample_id_sanitized: false,
      steps: [{ title: 'FOD', relative_directory: '002_S_0413/fod', outputs: [{ slot_id: 'wm', relative_path: 'wm_fod.mif', label: 'FOD', certainty: 'declared', artifact_type: 'fod', format: 'mif' }] }] }] };
  await test('unconfigured batch shows real sample folders and an explicit pending-settings notice', () => {
    const html = renderToStaticMarkup(createElement(OutputPreviewContent, { preview }));
    assert(html.includes('真实样本 ID') && html.includes('当前运行设置待调整'));
    assert(html.includes('输入样本与输出文件夹对应关系') && html.includes('与输入样本目录名一致'));
    assert(html.includes('002_S_0413/') && html.includes('wm_fod.mif') && !html.includes('steps/'));
  });
  await test('different sample labels, input folders and safe names stay visibly distinct', () => {
    const group = { ...preview.groups[0], sample_id: 'sub/002', sample_label: 'Second visit',
      source_relative_root: 'participant_002/visit1', output_sample_directory: 'sub_002--hash',
      folder_matches_input_name: false, sample_id_sanitized: true };
    const html = renderToStaticMarkup(createElement(OutputPreviewContent, { preview: { ...preview, groups: [group] } }));
    assert(html.includes('sub/002') && html.includes('participant_002/visit1') && html.includes('sub_002--hash/'));
    assert(html.includes('Second visit') && html.includes('使用安全目录名') && !html.includes('与输入样本目录名一致'));
  });
  await test('legacy naming cannot be mistaken for preserved sample folder names', () => {
    const group = { ...preview.groups[0], output_sample_directory: null, folder_matches_input_name: null,
      steps: [{ ...preview.groups[0].steps[0], relative_directory: 'sample_0001__fod' }] };
    const html = renderToStaticMarkup(createElement(OutputPreviewContent, { preview: { ...preview, naming_rule: 'ordinal_step', groups: [group] } }));
    assert(html.includes('文件夹名称不是样本名') && html.includes('sample_0001__fod/'));
    assert(!html.includes('与输入样本目录名一致'));
  });
} finally {
  await server.close();
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage);
  else delete globalThis.localStorage;
}
