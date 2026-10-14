import type { DryRunReport } from './workflowTypes';

export type QualityCheck = NonNullable<DryRunReport['quality_checks']>[number];
export type DryRunIssue = {
  stepId: string; runnerId: string; message: string; code?: string; slotId?: string;
  reasons: string[]; count: number; sampleCount: number; examples: string[];
};

export const qualityLabels: Record<string, string> = {
  image_header_readable: '图像头可读取；未验证影像内容',
  invalid_image_header: '图像头无法读取或格式无效',
  gradient_source_resolved: '已确定实际使用的梯度来源',
  invalid_gradient_source: '梯度来源缺失或与影像不匹配',
  response_function_readable: '响应函数结构已核查',
  invalid_response_function: '响应函数与所选输入不兼容',
  input_feature_requires_review: '输入条件需要审阅确认',
  input_feature_mismatch: '输入条件与方法要求不符',
  header_readable: '图像头可读取；未验证影像内容',
  reader_unavailable: '图像头读取环境不可用',
  format_not_checked: '此格式未接入通用图像头检查',
  reader_failed: '图像头检查未完成',
  invalid_header: '图像头损坏或几何信息无效',
  gradient_table_readable: '梯度表格式可读取',
  invalid_gradient_table: '梯度表无效',
  gradient_volume_mismatch: '梯度数量与 DWI 体积数不匹配',
  five_tt_channels: '5TT 必须有五个组织通道',
  label_dimensions: '脑区标签图维度不符合要求',
  spatial_alignment_requires_review: '需要核对输入影像的实际空间对齐；网格或头信息不能证明解剖对齐',
  voxel_values_and_anatomy_not_checked: '尚未验证组织概率、标签数值和解剖质量',
  upstream_check_deferred: '上游文件生成后才检查实际内容',
};

// Retain the unabridged report for export; default output depends on categories,
// never on the number of samples in a batch.
export function dryRunIssues(report: DryRunReport): DryRunIssue[] {
  const checks = new Map<string, QualityCheck>();
  for (const row of report.quality_checks ?? []) checks.set(JSON.stringify([row.step_id, row.sample_id ?? '', row.code, row.slot_id]), row);
  const groups = new Map<string, DryRunIssue & { samples: Set<string> }>();
  for (const command of report.commands) {
    if (command.valid) continue;
    for (const raw of command.messages) {
      const match = raw.match(/^样本 (.*?)：/);
      const sample = match?.[1];
      const message = match ? raw.slice(match[0].length).split(`/${sample}/`).join('/{sample}/') : raw;
      if (message.startsWith('输入 ') && message.includes('实际文件及内容将在执行该步骤前检查')) continue;
      const scientific = message.match(/^Scientific preflight: (\w+) \(([^)]*)\)$/);
      const row = scientific && checks.get(JSON.stringify([command.step_id, sample ?? '', scientific[1], scientific[2]]));
      const reason = row && typeof row.details.reason === 'string' ? row.details.reason : undefined;
      const key = JSON.stringify([command.step_id, command.runner_id, message]);
      let group = groups.get(key);
      if (!group) {
        group = { stepId: command.step_id, runnerId: command.runner_id, message,
          code: scientific?.[1], slotId: scientific?.[2], reasons: [],
          count: 0, sampleCount: 0, examples: [], samples: new Set() };
        groups.set(key, group);
      }
      group.count += 1;
      if (reason && group.reasons.length < 3 && !group.reasons.includes(reason)) group.reasons.push(reason);
      if (sample !== undefined) {
        group.samples.add(sample);
        if (group.examples.length < 3 && !group.examples.includes(sample)) group.examples.push(sample);
      }
    }
  }
  return [...groups.values()].map(({ samples, ...group }) => ({ ...group, sampleCount: samples.size }));
}

export function qualityGroups(report: DryRunReport) {
  const groups = new Map<string, { status: string; code: string; stepId: string; slotId: string; count: number }>();
  for (const row of report.quality_checks ?? []) {
    const key = JSON.stringify([row.status, row.code, row.step_id, row.slot_id]);
    const group = groups.get(key) ?? { status: row.status, code: row.code, stepId: row.step_id, slotId: row.slot_id, count: 0 };
    group.count += 1;
    groups.set(key, group);
  }
  const priority: Record<string, number> = { error: 0, review: 1, not_checked: 2, deferred: 3, passed: 4 };
  return [...groups.values()].sort((a, b) => (priority[a.status] ?? 5) - (priority[b.status] ?? 5));
}

export const gradientReasons: Record<string, string> = {
  'MRtrix gradients require 4 columns': '此输入要求四列 MRtrix 梯度表。请核对输入槽与文件格式。',
  'b-vectors require 3xN or Nx3': '梯度方向需要三行或三列，每个体积对应一个方向。',
  'b-values require 1xN or Nx1': 'b 值需要一行或一列，每个体积对应一个数值。',
  'Negative b-value': 'b 值不能为负数。',
  'Empty or non-finite gradient table': '梯度表为空，或包含 NaN、无穷大等无效数值。',
  'Ragged gradient table': '梯度表各行列数不一致。',
};
