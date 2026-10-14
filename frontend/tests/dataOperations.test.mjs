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
const load = path => server.ssrLoadModule('/src/' + path);
const base = { id: 'batch/1', kind: 'data', title: '数据整理', actor: 'alice', conversation_id: 'conv&1',
  mode: 'copy', status: 'pending', source_root: '/input', target_root: '/output', file_count: 200, completed: 0,
  total_bytes: 200, manifest_hash: 'exact-manifest', action_counts: { copy: 200 }, conflicts: [],
  items: [{ action: 'copy', path: 'old.nii.gz', destination: 'T1.nii.gz', size: 1, has_artifact: false, index: 0 }],
  preview_truncated: true, can_confirm: true, can_undo: false };
try {
  const { useLanguageStore } = await load('stores/languageStore.ts');
  useLanguageStore.setState({ language: 'zh' });
  // React SSR uses the initial snapshot instead of the live store.
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
  const { DataOperationCard } = await load('features/data/DataOperationCard.tsx');
  const render = operation => renderToStaticMarkup(createElement(DataOperationCard, { operation }));
  await test('computed change scripts keep the two distinct Run stages visible', () => {
    const proposal = {...base, mode:'inplace', phase:'script', manifest_ready:false,
      file_count:0, items:[], action_counts:{}, can_confirm:false, can_run_script:true,
      script_plan_hash:'script-hash',script_result_kind:'changes'};
    const script = render(proposal);
    assert(script.includes('第一步：运行脚本计算完整操作预览') && script.includes('>运行脚本<'));
    assert(!script.includes('>运行本批操作<'));
    const preview = render({...proposal,phase:'files',manifest_ready:true,file_count:2,
      action_counts:{rename:2},can_run_script:false,can_confirm:true,
      quality:{blocking_issues:[],proposal_review:{decision:'ready',issues:[],plan_hash:'script-hash'}}});
    assert(preview.includes('原文件尚未修改') && preview.includes('>运行本批操作<'));
    assert(!preview.includes('>运行脚本<'));
  });
  await test('completed analysis offers a result continuation and explicitly marks truncated logs', () => {
    const task = {...base,mode:'analysis',status:'analyzed',phase:'analysis',can_confirm:false,
      file_count:0,items:[],script:'print(result)',execution_log:'partial result',execution_log_available:true,
      execution_log_truncated:true,execution_log_capture_limited:true};
    const html = renderToStaticMarkup(createElement(DataOperationCard,{operation:task,onRequestRevision(){}}));
    assert(html.includes('这次运行只完成分析') && html.includes('基于结果继续'));
    assert(html.includes('仅填写后续请求') && html.includes('下载已保存日志') && html.includes('无法恢复未保存部分'));
    assert(html.includes('/api/data-operations/batch%2F1/log?conversation_id=conv%261'));
    assert(!html.includes('>运行脚本<') && !html.includes('>运行本批操作<'));
  });
  await test('prepared scripts require Run for analysis and hold file grants until generation', () => {
    for (const mode of ['analysis', 'copy', 'inplace']) {
      const html = render({ ...base, mode, phase: 'script', manifest_ready: false, file_count: 0,
        items: [], action_counts: {}, can_confirm: false, can_run_script: true, script_plan_hash: 'script-hash', script: 'print(1)' });
      assert(html.includes('可运行 · 尚未执行') && html.includes('脚本尚未运行') && html.includes('运行脚本'));
      assert(!html.includes('运行本批操作') && !html.includes('授权这份清单修改原目录'));
      assert(!html.includes('下载完整操作清单') && !html.includes('查看操作预览'));
    }
  });
  await test('script without an exact hash cannot be launched', () => {
    const html = render({ ...base, phase: 'script', manifest_ready: false, can_confirm: false, can_run_script: true });
    assert(html.includes('尚未就绪 · 暂不能运行') && !html.includes('>运行脚本<'));
  });
  await test('incomplete coverage explains refinement and cannot offer approval', () => {
    const html = render({ ...base, quality: { complete: false, blocking_issues: ['unmatched_files'], regressed_files: 12, accounted_files: 240 },
      scan: { complete_scan: true, file_count: 240, matched_files: 200, unmatched_files: 30, excluded_files: 10, preserved_files: 20 } });
    assert(html.includes('需要模型修订') && html.includes('暂不能运行'));
    assert(html.includes('明确排除') && html.includes('按原名保留') && html.includes('相比上一版遗漏'));
    assert(!html.includes('运行本批操作'));
  });
  await test('pending batches show complete-manifest review and preserve distinct source/target', () => {
    const html = render(base);
    assert(html.includes('可运行 · 尚未执行') && html.includes('下载完整操作清单'));
    assert(html.includes('此处显示前 1 项') && html.includes('/input') && html.includes('/output'));
    assert(html.includes('运行本批操作'));
    assert(!html.includes('已完成'));
  });
  await test('in-place and deletion scope remains visible without extra approval checkboxes', () => {
    const html = render({ ...base, mode: 'inplace', action_counts: { copy: 199, delete: 1 } });
    assert(html.includes('将修改原目录') && html.includes('包含清单中的删除操作'));
    assert(!/button[^>]* disabled=""[^>]*>运行本批操作/.test(html));
    assert.equal((html.match(/type="checkbox"/g) || []).length, 0);
  });
  await test('analysis results have no commit action and untrusted logs render as text', () => {
    const html = render({ ...base, mode: 'analysis', status: 'analyzed', items: [], action_counts: {},
      file_count: 0, can_confirm: false, execution_log: '<script>bad()</script>\nrows=25' });
    assert(html.includes('分析完成，未修改文件') && html.includes('rows=25'));
    assert(html.includes('&lt;script&gt;') && !html.includes('<script>'));
    assert(!html.includes('运行本批操作') && !html.includes('修改原目录'));
  });
  await test('background scripts expose cancellation and quotas without claiming completion', () => {
    const html = render({ ...base, status: 'analyzing', file_count: 0, items: [], can_confirm: false,
      sandbox_environment: 'table', sandbox_resources: { timeout_seconds: 300, memory_mb: 2048, output_mb: 1024 } });
    assert(html.includes('脚本运行中') && html.includes('>停止</button>'));
    assert(html.includes('table') && html.includes('300 s') && html.includes('2048 MiB'));
    assert(!html.includes('运行本批操作') && !html.includes('分析完成'));
  });
  await test('copy progress includes in-flight bytes without counting unpublished files', () => {
    const html = render({ ...base, status: 'running', can_confirm: false, total_bytes: 2097152,
      completed: 0, processed_bytes: 1048576 });
    assert(html.includes('1.0 / 2.0 MiB') && html.includes('0/200'));
  });
  await test('selected output operations do not request dataset in-place authorization', () => {
    const html = render({ ...base, mode: 'inplace', source_scope: 'output', status: 'succeeded',
      execution_policy: 'selected_output', requires_inplace_confirmation: false,
      can_confirm: false, can_undo: true });
    assert(html.includes('按当前输出目录权限执行') && html.includes('恢复本批修改'));
    assert(!html.includes('授权这份清单修改原目录') && !html.includes('运行本批操作'));
  });
  await test('output overwrite scope is shown without an extra checkbox or dataset write grant', () => {
    const html = render({ ...base, mode: 'inplace', source_scope: 'output',
      requires_inplace_confirmation: false, requires_overwrite_confirmation: true });
    assert(html.includes('将覆盖清单中的已有输出文件') && !html.includes('将修改原目录'));
    assert(!/button[^>]* disabled=""[^>]*>运行本批操作/.test(html));
    assert.equal((html.match(/type="checkbox"/g) || []).length, 0);
  });
  await test('copy review explicitly explains exclusions and offers the full unmatched inventory', () => {
    const html = render({ ...base, scan: { complete_scan: true, file_count: 300, unmatched_files: 100 } });
    assert(html.includes('未匹配文件不会复制到输出目录'));
    assert(html.includes('查看未匹配文件') && html.includes('下载完整未匹配清单'));
    assert(!html.includes('旧批次未保存排除快照'));
  });
  await test('an interrupted batch with no writes exposes cancellation to release its reservation', () => {
    const html = render({ ...base, status: 'interrupted', can_confirm: false, can_undo: false, completed: 0 });
    assert(html.includes('撤销待运行方案'));
    assert(!html.includes('恢复本批修改') && !html.includes('运行本批操作'));
  });
  await test('cancelled batches disclose partial file completion and their recovery action', () => {
    const html = render({ ...base, status: 'cancelled', can_confirm: false, can_undo: true, completed: 8 });
    assert(html.includes('已停止') && html.includes('8/200'));
    assert(html.includes('role="progressbar"') && html.includes('aria-valuenow="8"'));
    assert(html.includes('恢复本批修改') && !html.includes('运行本批操作'));
  });
  await test('expired previews explain re-preparation and cannot offer execution', () => {
    const html = render({ ...base, preview_expired: true, can_confirm: false });
    assert(html.includes('此预览已过期') && !html.includes('运行本批操作'));
  });
  await test('large conflict sets remain bounded while their count and inspection controls stay visible', () => {
    const html = render({ ...base, can_confirm: false, conflicts: Array.from({ length: 20000 }, (_, i) => 'conflict-path-' + i) });
    assert(html.includes('20,000') && html.includes('查看冲突列表'));
    assert(html.includes('conflict-path-0') && html.includes('conflict-path-19'));
    assert(!html.includes('conflict-path-20<') && !html.includes('conflict-path-19999'));
    assert(html.includes('上一页') && html.includes('下一页') && html.includes('1/1000'));
    assert(!html.includes('运行本批操作'));
    assert(html.length < 16000);
  });
  await test('confirm and undo carry exact scope/hash and authenticated session credentials', async () => {
    const api = await load('features/data/dataApi.ts');
    const previous = globalThis.fetch;
    const calls = [];
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(base), { status: 200 });
    };
    try {
      await api.confirmDataOperation(base, true, false);
      await api.confirmDataOperation(base, true, true, true);
      await api.getDataOperation(base);
      await api.confirmDataOperation(base, false, false, false, true);
      await api.runDataScript({ ...base, script_plan_hash: 'script-plan' });
      assert.throws(() => api.runDataScript(base), /Missing script proposal hash/);
      assert.equal(calls[0].url, '/api/data-operations/batch%2F1/confirm');
      assert.equal(calls[1].url, '/api/data-operations/batch%2F1/undo');
      assert.equal(calls[2].url, '/api/data-operations/batch%2F1?conversation_id=conv%261');
      assert.deepEqual(JSON.parse(calls[0].init.body), { confirmed: true, conversation_id: 'conv&1',
        manifest_hash: 'exact-manifest', allow_inplace: true, allow_delete: false, allow_overwrite: false });
      assert(calls.every(call => call.init.credentials === 'include'));
      assert.equal(JSON.parse(calls[3].init.body).allow_overwrite, true);
      assert.equal(calls[4].url, '/api/data-operations/batch%2F1/run-script');
      assert.deepEqual(JSON.parse(calls[4].init.body), { confirmed: true, conversation_id: 'conv&1', script_plan_hash: 'script-plan' });
    } finally { globalThis.fetch = previous; }
  });
  await test('link exclusions are visible without claiming complete data coverage', () => {
    const html=render({...base,scan:{complete_scan:true,scope_complete:false,file_count:2,unmatched_files:0,
      skipped_symlinks:60,symlink_examples:[{path:'subject/link',reason:'not_followed'}],symlink_examples_truncated:true}});
    assert(html.includes('已扫描普通文件') && html.includes('已跳过 60 个符号链接'));
    assert(html.includes('subject/link') && html.includes('仅显示前 50 个链接'));
    assert(!html.includes('完整扫描：'));
  });
  await test('ordinary batches keep their existing run controls without link warnings', () => {
    const html=render({...base,scan:{complete_scan:true,file_count:200,unmatched_files:0},
      quality:{complete:true,blocking_issues:[],regressed_files:0,accounted_files:200,
        proposal_review:{decision:'ready',issues:[]}}});
    assert(!html.includes('查看跳过的链接'));
    assert(html.includes('运行'));
  });
} finally {
  await server.close();
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage);
  else delete globalThis.localStorage;
}
