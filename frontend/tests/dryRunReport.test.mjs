import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { dryRunIssues, qualityGroups } = await server.ssrLoadModule('/src/features/workflows/dryRunSummary.ts');
  const { DryRunResult } = await server.ssrLoadModule('/src/features/workflows/DryRunResult.tsx');
  const { useLanguageStore } = await server.ssrLoadModule('/src/stores/languageStore.ts');
  const initial = useLanguageStore.getInitialState();
  useLanguageStore.setState({ language: 'zh' });
  useLanguageStore.getInitialState = () => ({ ...initial, language: 'zh' });
  const checks = [];
  const messages = [];
  for (let i = 0; i < 499; i++) for (const slot of ['bvecs', 'bvals']) {
    const sample = `S${String(i).padStart(3, '0')}`;
    checks.push({ step_id: 'fod', slot_id: slot, sample_id: sample, status: 'error',
      code: 'invalid_gradient_table', details: { reason: 'MRtrix gradients require 4 columns' } });
    messages.push(`样本 ${sample}：Scientific preflight: invalid_gradient_table (${slot})`);
  }
  const report = { valid: false, execution_enabled: true, dataset_path_validated: true, checked_at: 'fixture',
    quality_checks: checks, quality_summary: { error: 998 }, quality_review_required: true,
    commands: [{ step_id: 'fod', runner_id: 'test', execution_mode: 'runner', valid: false,
      command_preview: 'sensitive long preview', messages }], warnings: ['fixture warning'] };

  await test('batch failures retain all evidence and summarize by step, cause and input port', () => {
    const before = JSON.stringify(report);
    const groups = dryRunIssues(report);
    assert.equal(groups.length, 2);
    for (const group of groups) {
      assert.equal(group.count, 499); assert.equal(group.sampleCount, 499);
      assert.deepEqual(group.examples, ['S000', 'S001', 'S002']);
      assert.deepEqual(group.reasons, ['MRtrix gradients require 4 columns']);
    }
    assert.equal(qualityGroups(report).length, 2);
    assert.equal(JSON.stringify(report), before);
    const html = renderToStaticMarkup(createElement(DryRunResult, { report, workflowId: 'wf', checked: false, disabled: false, onCheck() {} }));
    assert.equal((html.match(/<li /g) ?? []).length, 2);
    assert(!html.includes('S498') && !html.includes('sensitive long preview') && !html.includes('Scientific preflight'));
    assert(html.includes('998') && html.includes('499') && html.includes('下载检查报告') && html.includes('请先解决'));
    assert(html.includes('type="checkbox"') && !html.includes('200 项'));
    assert(html.length < 8000, html.length);
  });
  await test('historical truncation is stated explicitly, without duplicating grouped errors', () => {
    const old = { ...report, quality_checks: checks.slice(0, 200) };
    assert.equal(dryRunIssues(old).length, 2);
    assert(dryRunIssues(old).every(group => group.sampleCount === 499));
    const html = renderToStaticMarkup(createElement(DryRunResult, { report: old, workflowId: 'wf', checked: false, disabled: true, onCheck() {} }));
    assert(html.includes('200 / 998') && html.includes('重新检查可获得完整报告') && html.includes('disabled=""'));
  });
  await test('different steps and failure types remain distinct; missing paths group by sample-relative path', () => {
    const copy = { ...report, quality_checks: [], commands: [
      { ...report.commands[0], messages: ['样本 S1：输入 mask 不存在：/input/S1/mask.nii', '样本 S2：输入 mask 不存在：/input/S2/mask.nii', '环境依赖不可用'] },
      { ...report.commands[0], step_id: 'other', messages: ['环境依赖不可用'] },
    ] };
    const groups = dryRunIssues(copy);
    assert.equal(groups.length, 3);
    assert.equal(groups[0].sampleCount, 2);
    assert.equal(groups[0].message, '输入 mask 不存在：/input/{sample}/mask.nii');
    assert.equal(groups[1].stepId, 'fod'); assert.equal(groups[2].stepId, 'other');
  });
  await test('passed preflight explicitly preserves the manual execution boundary', () => {
    const passed = { ...report, valid: true, quality_checks: [], quality_summary: {}, quality_review_required: false,
      commands: report.commands.map(row => ({ ...row, valid: true, messages: [] })) };
    const html = renderToStaticMarkup(createElement(DryRunResult, { report: passed, workflowId: 'wf', checked: false, disabled: false, onCheck() {} }));
    assert(html.includes('Dry-run 已通过') && html.includes('需手动点击运行'));
    assert(!html.includes('请先解决') && !html.includes('type="checkbox"'));
  });
} finally { await server.close(); }
