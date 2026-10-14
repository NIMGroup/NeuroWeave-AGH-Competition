import { ServerPath } from '../workspace/ServerPath';
import { useLanguageStore } from '../../stores/languageStore';
import { OutputTree } from './OutputTree';
import type { OutputEntry } from './outputPreviewTree';
import type { OutputPreview } from './outputPreviewTypes';

export function OutputPreviewContent({ preview }: { preview: OutputPreview }) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const entries: OutputEntry[] = preview.groups.flatMap(group => group.steps.flatMap(step => [
    { path: step.relative_directory, directory: true, label: step.title },
    ...step.outputs.map(output => ({ path: step.relative_directory + '/' + (output.relative_path ?? '{' + output.slot_id + '}'),
      directory: output.format === 'directory', label: output.label,
      detail: [output.artifact_type, output.certainty === 'declared' ? '' : zh ? '名称执行时确定' : 'Name resolved during execution'].filter(Boolean).join(' · ') }))
  ]));
  return <div className="space-y-3">
    {preview.root_template && <ServerPath path={preview.root_template} />}
    {preview.settings_need_change && <p className="rounded-control border border-warning-line bg-warning-quiet p-2 text-meta text-warning">
      {zh ? '按样本输出预览 · 当前运行设置待调整。这些目录按方案必需的样本模式展示，尚未修改运行设置。'
        : 'Per-sample output preview · Run settings need adjustment. This projection uses the sample mode required by the plan; run settings have not changed.'}
    </p>}
    <p className="font-medium text-meta text-ink">{preview.naming_rule === 'sample_id'
      ? (zh ? '命名规则：运行目录 / 真实样本 ID / 步骤 / 文件' : 'Naming: run directory / real sample ID / step / file')
      : preview.naming_rule === 'ordinal_step'
        ? (zh ? '命名规则：序号与步骤名；文件夹名称不是样本名。' : 'Naming: ordinal and step; folder names do not use sample names.')
        : (zh ? '命名规则：单次运行 / 步骤；不会创建样本文件夹。' : 'Naming: single run / step; no sample folders are created.')}</p>
    <p className="text-meta text-muted">{zh ? `${preview.sample_mode === 'per_sample' ? preview.sample_count + ' 个样本' : '单次运行'} · ${preview.unit_count} 个步骤 · ${preview.declared_output_count} 项预计输出`
      : `${preview.sample_count} sample groups · ${preview.unit_count} steps · ${preview.declared_output_count} declared outputs`}
      {preview.excluded_sample_count > 0 && (zh ? ` · 已排除 ${preview.excluded_sample_count} 个样本` : ` · ${preview.excluded_sample_count} samples excluded`)}</p>
    {preview.issues.length > 0 && <ul className="space-y-1 text-meta text-warning">{preview.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
    {preview.groups.some(group => group.sample_id !== null) && <div className="max-h-56 overflow-auto rounded-control border border-line">
      <table className="w-full text-left text-xs"><caption className="p-2 text-left font-medium text-muted">{zh ? '输入样本与输出文件夹对应关系' : 'Input samples and output folders'}</caption>
        <thead className="border-b border-line bg-panel text-subtle"><tr>
          <th className="p-2">{zh ? '样本 ID' : 'Sample ID'}</th><th className="p-2">{zh ? '输入样本目录' : 'Input sample directory'}</th><th className="p-2">{zh ? '输出文件夹' : 'Output folder'}</th>
        </tr></thead>
        <tbody>{preview.groups.filter(group => group.sample_id !== null).map(group => <tr key={group.sample_id} className="border-b border-line last:border-0">
          <td className="break-all p-2 align-top font-mono">{group.sample_id}
            {group.sample_label && group.sample_label !== group.sample_id && <p className="mt-1 text-micro font-sans text-muted">{group.sample_label}</p>}
          </td>
          <td className="break-all p-2 align-top font-mono">{group.source_relative_root}</td>
          <td className="break-all p-2 align-top"><span className="font-mono">{group.output_sample_directory ? group.output_sample_directory + '/' : group.steps.map(s => s.relative_directory + '/').join(' · ')}</span>
            {group.folder_matches_input_name === true && <p className="mt-1 text-micro text-success">{zh ? '与输入样本目录名一致' : 'Matches the input folder name'}</p>}
            {group.folder_matches_input_name === false && <p className="mt-1 text-micro text-warning">{group.sample_id_sanitized
              ? (zh ? '样本 ID 含路径不支持的字符，使用安全目录名；原 ID 保留。' : 'The sample ID required a safe folder name; the original ID is preserved.')
              : (zh ? '按样本 ID 命名；与原目录名称不同。' : 'Uses the sample ID, which differs from the input folder name.')}</p>}
          </td>
        </tr>)}</tbody>
      </table>
    </div>}
    {entries.length > 0 && <OutputTree entries={entries} />}
    {preview.entries_truncated && <p className="text-micro text-warning">{zh ? '本页预计输出超过展示上限，只展示前 200 项。' : 'This page has more outputs; showing the first 200 entries.'}</p>}
  </div>;
}
