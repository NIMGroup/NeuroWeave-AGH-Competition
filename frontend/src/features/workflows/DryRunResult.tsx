import { useMemo, useState } from 'react';
import { CheckCircle2, Download, TriangleAlert } from 'lucide-react';
import { ui } from '../../lib/libraryText';
import { useLanguageStore } from '../../stores/languageStore';
import type { DryRunReport } from './workflowTypes';
import { dryRunIssues, gradientReasons, qualityLabels, type DryRunIssue } from './dryRunSummary';
import { QualityReport } from './QualityReport';

function Issue({ issue }: { issue: DryRunIssue }) {
  return <li className="min-w-0 rounded-control border border-warning-line p-2 text-xs">
    <p className="break-words font-medium">{issue.stepId}{issue.slotId ? ` / ${issue.slotId}` : ''} · {issue.code ? ui(qualityLabels[issue.code] ?? issue.code) : issue.message}</p>
    <p className="mt-1 text-muted">{issue.sampleCount ? ui('影响 {0} 个样本', issue.sampleCount) : ui('共 {0} 项', issue.count)}</p>
    {issue.reasons.map(reason => <p key={reason} className="mt-1 break-words">{ui(gradientReasons[reason] ?? reason)}</p>)}
    {!!issue.examples.length && <p className="mt-1 break-words text-muted">{ui('样例：{0}', issue.examples.join('、'))}</p>}
  </li>;
}

export function DryRunResult({ report, workflowId, checked = false, disabled = false, onCheck }: {
  report: DryRunReport; workflowId: string; checked?: boolean; disabled?: boolean; onCheck?: (value: boolean) => void;
}) {
  useLanguageStore(state => state.language);
  const issues = useMemo(() => dryRunIssues(report), [report]);
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [commandsOpen, setCommandsOpen] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const [messagePage, setMessagePage] = useState(0);
  const command = report.commands[Math.min(commandIndex, report.commands.length - 1)];
  const messages = command?.messages.slice(messagePage * 20, (messagePage + 1) * 20) ?? [];
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${workflowId}-dry-run.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <section className={`mt-3 min-w-0 rounded-card border p-3 ${report.valid ? 'border-success-line bg-success-quiet/60' : 'border-warning-line bg-warning-quiet'}`} aria-label={ui('Dry-run 结果')}>
    <p className="flex items-center gap-1.5 text-xs font-medium">
      {report.valid ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <TriangleAlert className="h-3.5 w-3.5 text-warning" />}
      {ui(report.valid ? 'Dry-run 已通过' : 'Dry-run 未通过')} · {ui('{0} 个步骤', report.commands.length)}
    </p>
    <p className="mt-1 text-xs">{report.valid && !onCheck ? ui('尚未执行处理。点击运行后提交任务。') : ui(report.valid ? '尚未执行处理。最终确认后，需手动点击运行。' : '尚未执行处理。请先解决以下阻碍，再重新检查。')}</p>
    {!!issues.length && <ul className="mt-2 space-y-2">{issues.slice(0, 4).map((issue, i) => <Issue key={i} issue={issue} />)}</ul>}
    {issues.length > 4 && <details className="mt-2 text-xs" onToggle={event => setIssuesOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer">{ui('其余 {0} 类问题', issues.length - 4)}</summary>
      {issuesOpen && <ul className="mt-2 max-h-56 space-y-2 overflow-auto">{issues.slice(4).map((issue, i) => <Issue key={i} issue={issue} />)}</ul>}
    </details>}
    {!report.valid && !issues.length && <p className="mt-2 text-xs text-warning">{ui('输入路径或流程约束未满足，请查看检查说明。')}</p>}
    <QualityReport report={report} checked={checked} disabled={disabled} onCheck={onCheck} />
    <details className="mt-2 text-xs"><summary className="cursor-pointer">{ui('检查说明')}</summary>
      <div className="mt-1 max-h-40 space-y-1 overflow-auto">{report.warnings.map((warning, i) => <p key={i} className="break-words">{warning}</p>)}</div>
    </details>
    <details className="mt-2 text-xs" onToggle={event => setCommandsOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer">{ui('命令与原始消息')}</summary>
      {commandsOpen && command && <div className="mt-2 min-w-0 space-y-2">
        <label>{ui('步骤')} <select aria-label={ui('选择检查步骤')} value={commandIndex} onChange={event => { setCommandIndex(Number(event.target.value)); setMessagePage(0); }} className="max-w-full rounded border border-line bg-raised p-1">
          {report.commands.map((row, i) => <option key={i} value={i}>{row.step_id} · {row.runner_id}</option>)}
        </select></label>
        <code className="block overflow-x-auto whitespace-pre-wrap break-words rounded bg-well p-2 font-mono text-micro">{command.command_preview}</code>
        <div className="max-h-56 overflow-auto">{messages.map((message, i) => <p key={i} className="my-1 break-words text-micro">{message}</p>)}</div>
        {command.messages.length > 20 && <div className="flex items-center gap-2">
          <button type="button" disabled={messagePage === 0} onClick={() => setMessagePage(page => page - 1)} className="disabled:opacity-50">{ui('上一页')}</button>
          <span>{messagePage + 1} / {Math.ceil(command.messages.length / 20)}</span>
          <button type="button" disabled={(messagePage + 1) * 20 >= command.messages.length} onClick={() => setMessagePage(page => page + 1)} className="disabled:opacity-50">{ui('下一页')}</button>
        </div>}
      </div>}
    </details>
    <button type="button" onClick={download} className="mt-2 inline-flex items-center gap-1 text-xs text-accent"><Download className="h-3.5 w-3.5" />{ui('下载检查报告')}</button>
  </section>;
}
