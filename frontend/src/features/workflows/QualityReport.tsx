import { useMemo, useState } from 'react';
import { stateText } from '../../lib/stateText';
import type { DryRunReport } from './workflowTypes';
import { ui } from '../../lib/libraryText';
import { gradientReasons, qualityGroups, qualityLabels } from './dryRunSummary';

export function QualityReport({ report, checked, disabled, onCheck }: {
  report: DryRunReport; checked: boolean; disabled: boolean; onCheck?: (value: boolean) => void;
}) {
  const groups = useMemo(() => qualityGroups(report), [report]);
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const rows = report.quality_checks ?? [];
  const total = Object.values(report.quality_summary ?? {}).reduce((a, b) => a + b, 0);
  return <div className="my-2 rounded border border-line p-2 text-xs">
    <p>{ui('执行检查通过不代表科研质量合格。')}</p>
    {total > rows.length && <p className="mt-1">{ui('此历史报告仅保存 {0} / {1} 项明细；重新检查可获得完整报告。', rows.length, total)}</p>}
    {!!rows.length && <details className="mt-1">
      <summary className="cursor-pointer">{ui('图像与跨文件检查')} · {Object.entries(report.quality_summary ?? {}).map(([key, count]) => `${stateText(key)}: ${count}`).join(' · ')}</summary>
      <div className="mt-2 max-h-56 overflow-auto">{groups.map((group, i) => <p key={i} className="my-1 break-words">{group.stepId}{group.slotId ? ` / ${group.slotId}` : ''} · {stateText(group.status)} · {ui(qualityLabels[group.code] ?? group.code)} · {ui('{0} 项', group.count)}</p>)}</div>
      <details className="mt-2" onToggle={event => setOpen(event.currentTarget.open)}>
        <summary className="cursor-pointer">{ui('逐项明细')}</summary>
        {open && <>
          <div className="max-h-56 overflow-auto">{rows.slice(page * 20, (page + 1) * 20).map((row, i) => <div key={i} className="my-2 break-words">
            <p>{row.sample_id ?? row.step_id} / {row.slot_id} · {stateText(row.status)} · {ui(qualityLabels[row.code] ?? row.code)}</p>
            {typeof row.details.reason === 'string' && <p className="text-muted">{ui(gradientReasons[row.details.reason] ?? row.details.reason)}</p>}
            <pre className="whitespace-pre-wrap break-words text-micro text-muted">{JSON.stringify(row.details)}</pre>
          </div>)}</div>
          {rows.length > 20 && <div className="flex items-center gap-2">
            <button type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)} className="disabled:opacity-50">{ui('上一页')}</button>
            <span>{page + 1} / {Math.ceil(rows.length / 20)}</span>
            <button type="button" disabled={(page + 1) * 20 >= rows.length} onClick={() => setPage(value => value + 1)} className="disabled:opacity-50">{ui('下一页')}</button>
          </div>}
        </>}
      </details>
    </details>}
    {report.quality_review_required && onCheck && <label className="mt-2 flex items-start gap-2"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onCheck(event.target.checked)} /><span>{ui('我已审阅检查限制，并确认将核对空间对齐和结果质量。')}</span></label>}
  </div>;
}
