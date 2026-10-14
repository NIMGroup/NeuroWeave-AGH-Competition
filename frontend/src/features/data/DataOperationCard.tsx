import { useEffect, useRef, useState } from 'react';
import { useLanguageStore } from '../../stores/languageStore';
import { ServerPath } from '../workspace/ServerPath';
import { cancelDataOperation, confirmDataOperation, getDataManifest, getDataOperation, getDataOperationFiles, getDataProgress, runDataScript } from './dataApi';
import type { DataOperation, DataOperationFiles, DataOperationProgress } from './dataTypes';
import { apiBase, downloadFile } from '../../api/http';
import { CheckCircle2, CircleAlert, FileStack, Loader2, ShieldCheck, Square } from 'lucide-react';
import { Button } from '../../ui/Button';
import { proposalContinuationRequest, proposalFixes, proposalRevisionRequest, proposalState } from './proposalPresentation';
import type { ProposalState } from './proposalPresentation';

export function DataOperationCard({ operation, onRequestRevision, disabled = false }: { operation: DataOperationProgress; onRequestRevision?: (text: string) => void; disabled?: boolean }) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const [task, setTask] = useState<DataOperation>(() => ({ ...operation, items: operation.items ?? [] }));
  const [previewLoaded, setPreviewLoaded] = useState(Array.isArray(operation.items));
  const [reload, setReload] = useState(0);
  const previousTask = useRef(task);
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const [unmatched, setUnmatched] = useState<DataOperationFiles | null>(null);
  const [conflictPage, setConflictPage] = useState(0);
  const working = ['validating', 'queued', 'analyzing', 'cancel_requested', 'running', 'rolling_back'].includes(task.status);
  const stopRequested = stopping || task.cancel_requested || task.status === 'cancel_requested';
  const scriptProposal = task.phase === 'script' && task.manifest_ready === false;
  const state = proposalState(task);
  const reviewing = state === 'reviewing';
  const review = task.quality?.proposal_review;
  useEffect(() => {
    let alive = true, fetching = false;
    const identity = { id: operation.id, conversation_id: operation.conversation_id };
    const refresh = async (full = false) => {
      if (fetching) return;
      fetching = true;
      try {
        let value: DataOperation;
        let complete = full;
        if (full) value = await getDataOperation(identity);
        else {
          const previous = previousTask.current;
          const progress = await getDataProgress(identity);
          const changed = previous.status !== progress.status || previous.phase !== progress.phase || previous.file_count !== progress.file_count;
          complete = changed;
          value = changed ? await getDataOperation(identity) : { ...previous, ...progress };
        }
        if (alive && !(value.updated_at != null && previousTask.current.updated_at != null
          && value.updated_at < previousTask.current.updated_at)) { previousTask.current = value; setTask(value); if (complete) setPreviewLoaded(true); }
      } catch (e: unknown) { if (alive) setError(e instanceof Error ? e.message : String(e)); }
      finally { fetching = false; }
    };
    void refresh(true);
    const timer = working ? setInterval(() => void refresh(), 2000) : null;
    return () => { alive = false; if (timer) clearInterval(timer); };
  }, [operation.id, operation.conversation_id, operation.status, operation.manifest_hash, operation.script_plan_hash, operation.quality?.proposal_review?.decision, operation.quality?.proposal_review?.superseded_by, working, reload]);
  const labels: Record<string, string> = zh
    ? { validating: '检查执行条件', queued: '等待脚本运行', analyzing: '脚本运行中', cancel_requested: '正在停止', analyzed: '分析完成，未修改文件', pending: '等待点击运行', running: '执行中', rolling_back: '恢复中', succeeded: '已完成', failed: '失败，可检查恢复记录', cancelled: '已停止', interrupted: '已中断', rolled_back: '已恢复' }
    : { validating: 'Checking execution conditions', queued: 'Script queued', analyzing: 'Script running', cancel_requested: 'Stopping', analyzed: 'Analysis completed; files unchanged', pending: 'Awaiting Run', running: 'Running', rolling_back: 'Restoring', succeeded: 'Completed', failed: 'Failed', cancelled: 'Stopped', interrupted: 'Interrupted', rolled_back: 'Restored' };
  const actions: Record<string, string> = zh ? { copy: '复制', rename: '改名', write_text: '写入新版本', delete: '删除并备份' }
    : { copy: 'Copy', rename: 'Rename', write_text: 'Write new version', delete: 'Delete with backup' };
  async function act(action: 'confirm' | 'undo' | 'cancel' | 'script') {
    if ((disabled || !previewLoaded) && action !== 'cancel') return;
    if (action === 'cancel') setStopping(true);
    else { setBusy(true); setStarting(true); }
    setError('');
    try {
      const value = action === 'cancel' ? await cancelDataOperation(task) : action === 'script' ? await runDataScript(task) : await confirmDataOperation(task, needsInplace, hasDelete, action === 'undo', hasOverwrite);
      if (!(value.updated_at != null && previousTask.current.updated_at != null
        && value.updated_at < previousTask.current.updated_at)) { previousTask.current = value; setTask(value); }
    }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { if (action === 'cancel') setStopping(false); else { setBusy(false); setStarting(false); } }
  }
  async function loadUnmatched(offset = 0) {
    setBusy(true); setError('');
    try { setUnmatched(await getDataOperationFiles(task, offset)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function download(exclusions = false) {
    try {
      const manifest = exclusions ? await getDataOperationFiles(task, 0, true) : await getDataManifest(task);
      const url = URL.createObjectURL(new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' }));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'data-operation-' + task.id + (exclusions ? '-unmatched' : '') + '.json'; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  const hasDelete = Boolean(task.action_counts.delete);
  const needsInplace = !scriptProposal && (task.requires_inplace_confirmation ?? task.mode === 'inplace');
  const hasOverwrite = Boolean(task.requires_overwrite_confirmation);
  const allowed = previewLoaded;
  const incomplete = task.status === 'pending' && state !== 'ready';
  const failed = ['failed', 'interrupted'].includes(task.status);
  const done = ['succeeded', 'analyzed', 'rolled_back'].includes(task.status);
  const attention = incomplete || task.preview_expired;
  const tone = failed ? 'border-danger-line text-danger bg-danger-quiet' : working || reviewing ? 'border-running-line text-running bg-running-quiet'
    : attention ? 'border-warning-line text-warning bg-warning-quiet' : done || state === 'ready' ? 'border-success-line text-success bg-success-quiet' : 'border-line text-muted bg-panel';
  const progress = task.file_count > 0 ? Math.min(100, Math.max(0, task.completed / task.file_count * 100)) : 0;
  const conflictPages = Math.ceil(task.conflicts.length / 20);
  const currentConflictPage = Math.min(conflictPage, Math.max(0, conflictPages - 1));
  const fixes = proposalFixes(task);
  const stateLabels: Record<ProposalState, string> = zh
    ? { active: '', historical: '旧版方案已被替代', expired: '预览已过期 · 暂不能运行', reviewing: '正在核对方案 · 暂不能运行', revise: '需要模型修订 · 暂不能运行', clarify: '需要你补充信息 · 暂不能运行', unavailable: '核验未完成 · 暂不能运行', ready: '可运行 · 尚未执行', blocked: '尚未就绪 · 暂不能运行' }
    : { active: '', historical: 'Replaced by a newer proposal', expired: 'Preview expired · Run unavailable', reviewing: 'Checking the proposal · Run unavailable', revise: 'Assistant revision needed · Run unavailable', clarify: 'Your input needed · Run unavailable', unavailable: 'Verification incomplete · Run unavailable', ready: 'Ready to run · Not executed', blocked: 'Not ready · Run unavailable' };
  if (state === 'historical') return <details className="chat-disclosure rounded-card border border-line p-3 text-meta text-muted">
    <summary>{stateLabels.historical}：{task.title}</summary>
    <a className="underline" href={'#data-operation-' + review?.superseded_by}>{zh ? '查看当前版本' : 'View current version'}</a>
  </details>;
  return <section id={'data-operation-' + task.id} aria-label={task.title}
    className="min-w-0 scroll-mt-4 overflow-hidden rounded-card border border-line bg-raised text-meta text-ink">
    <header className={'flex flex-wrap items-start gap-3 border-b px-4 py-3 ' + tone}>
      {working || reviewing ? <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin" aria-hidden="true" />
        : failed || incomplete ? <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        : done ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        : <FileStack className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
      <div className="min-w-0 flex-1"><h3 className="break-words font-semibold text-ink">{task.title}</h3>
        <p className="mt-0.5 font-medium">{state === 'active' ? labels[task.status] ?? task.status : stateLabels[state]}</p>
      </div>
      {!scriptProposal && task.mode !== 'analysis' && <span className="shrink-0 rounded-chip border border-current/20 px-2 py-0.5 tabular-nums">{task.file_count.toLocaleString()} {zh ? '个文件' : 'files'}</span>}
      {(working || starting) && <Button size="sm" disabled={Boolean(stopRequested)} onClick={() => void act('cancel')}>
        <Square className="size-3" aria-hidden="true" />{stopRequested ? (zh ? '正在停止' : 'Stopping') : (zh ? '停止' : 'Stop')}
      </Button>}
    </header>
    <div className="space-y-3 px-4 py-3">
    {!previewLoaded && <p role="status" className="text-muted">{zh ? '正在读取当前方案详情，读取完成后可检查并运行。' : 'Loading the current proposal details for review before Run.'}</p>}
    {state === 'ready' && <p className="text-muted">{scriptProposal
      ? task.script_result_kind === 'changes'
        ? (zh ? '第一步：运行脚本计算完整操作预览，原文件保持不变。完成后，此卡片会显示真实改动；检查后再次点击运行才会修改文件。' : 'First, run the script to compute the complete change preview. Original files remain unchanged. This card then shows the actual changes for a separate Run approval.')
        : (zh ? '脚本尚未运行。点击运行后才会在隔离环境中执行；如生成文件，请检查预览并再次点击运行以写入目标目录。' : 'The script has not run. Click Run to execute it in isolation; review any generated files and click Run again to write them to the target directory.')
      : (zh ? '方案尚未执行。请检查操作预览，点击运行后才会修改文件。' : 'This plan has not run. Review the proposed changes and click Run to modify files.')}</p>}
    {task.status === 'pending' && task.phase === 'files' && task.script_result_kind === 'changes' && <p className="text-muted">{zh ? '操作清单已计算完成，原文件尚未修改。下方运行按钮执行的是这份真实改动清单。' : 'The complete change list is ready. Original files remain unchanged. Run below applies this exact list.'}</p>}
    {task.status === 'analyzed' && task.script && <div className="space-y-2">
      <p className="text-muted">{zh ? '这次运行只完成分析，没有执行文件修改。若原目标还需要修改文件，可基于结果继续准备操作方案。' : 'This run completed the analysis without changing files. Continue from its results if the original goal requires file changes.'}</p>
      {onRequestRevision && <Button size="sm" onClick={() => onRequestRevision(proposalContinuationRequest(task, zh))}>{zh ? '基于结果继续' : 'Continue from results'}</Button>}
      {onRequestRevision && <p className="text-micro text-subtle">{zh ? '仅填写后续请求；不会重复运行脚本或执行文件修改。' : 'Drafts a follow-up request without rerunning the script or applying changes.'}</p>}
    </div>}
    {state === 'ready' && review?.step_role === 'preparation' && <p className="text-muted">{zh ? '这是前置步骤；运行后取得结果，再准备原目标的后续方案。' : 'This is a preparation step. Its result supports the next proposal for your original goal.'}</p>}
    {reviewing && <p role="status" className="text-muted">{zh ? '正在比较用户要求与实际方案，核对后才可点击运行。' : 'Comparing the proposal with your requirements before enabling Run.'}</p>}
    {state === 'revise' && <div className="space-y-2">
      <p>{zh ? '下面的改动由模型完成，你无需手动修改代码。' : 'The assistant makes these changes; you do not need to edit code.'}</p>
      {review?.summary && <p className="text-muted">{review.summary}</p>}
      {fixes.length > 0 && <ul className="list-disc space-y-1 pl-5">{fixes.slice(0, 3).map((fix, i) => <li key={i} className="break-words">{fix.area && <strong>{fix.area}： </strong>}{fix.change}</li>)}</ul>}
      {fixes.length > 3 && <p className="text-subtle">{zh ? `共 ${fixes.length} 项，完整说明见下方检查详情。` : `${fixes.length} items in total. See the full review below.`}</p>}
      <p className="text-muted">{zh ? '下一步：让模型继续修订，生成通过检查的版本后再点击运行。' : 'Next: ask the assistant to revise it, then run the version that passes review.'}</p>
    </div>}
    {state === 'clarify' && <div className="space-y-2"><p>{zh ? '请在对话中回答以下问题，模型收到后会补全方案：' : 'Answer these questions in chat so the assistant can complete the proposal:'}</p>
      <ul className="list-disc space-y-1 pl-5">{review?.questions?.map((question, i) => <li key={i}>{question}</li>)}</ul></div>}
    {state === 'unavailable' && <p className="text-muted">{zh ? '目前无法确认方案是否可运行；需要重新核验，不能据此判定代码有错。' : 'Readiness has not been verified. Retry verification; this does not establish a code defect.'}</p>}
    {state === 'blocked' && <p className="text-muted">{zh ? '当前没有可用的运行入口。请让模型检查方案或环境状态后补全。' : 'Run is not available. Ask the assistant to check and complete the proposal or environment setup.'}</p>}
    {review && review.issues.length > 0 && !reviewing && <details className="chat-disclosure"><summary>{zh ? '查看完整检查详情' : 'Full review details'} ({review.issues.length})</summary>
      <ol className="list-decimal space-y-2 pl-5">{review.issues.map((issue, i) => <li key={i} className="break-words">{issue}</li>)}</ol>
      {(review.fixes?.length ?? 0) > 3 && <div className="mt-3"><p className="font-medium">{zh ? '其余修订项' : 'Remaining revisions'}</p><ul className="mt-1 list-disc space-y-1 pl-5">{review.fixes?.slice(3).map((fix, i) => <li key={i}><strong>{fix.area}： </strong>{fix.change}</li>)}</ul></div>}
      {review.next_step && <p className="mt-3 whitespace-pre-wrap break-words text-muted">{review.next_step}</p>}
    </details>}
    {['revise', 'unavailable', 'blocked', 'expired'].includes(state) && onRequestRevision && <div className="space-y-1.5">
      <Button size="sm" disabled={busy} onClick={() => onRequestRevision(proposalRevisionRequest(task, zh))}>{state === 'unavailable' ? (zh ? '填写重新核验请求' : 'Draft a verification request') : (zh ? '填写修订请求' : 'Draft a revision request')}</Button>
      <p className="text-micro text-subtle">{zh ? '仅填入输入框；你可编辑后发送，不会执行任务。' : 'Fills the composer only. Edit and send when ready; it does not execute the task.'}</p>
    </div>}
    {task.preview_expired && <p role="alert">{zh ? '此预览已过期，不能执行。请根据当前文件重新生成预览。' : 'This preview has expired. Generate a fresh preview before execution.'}</p>}
    {(working || failed || done || task.completed > 0 || task.can_undo) && task.file_count > 0 && <div className="space-y-1.5">
      <div className="flex justify-between gap-3 text-muted"><span>{zh ? '执行进度' : 'Execution progress'}</span><span className="tabular-nums">{task.completed}/{task.file_count}</span></div>
      <div role="progressbar" aria-label={zh ? '文件执行进度' : 'File execution progress'} aria-valuemin={0} aria-valuemax={task.file_count} aria-valuenow={Math.min(task.completed, task.file_count)}
        className="h-1.5 overflow-hidden rounded-pill bg-well"><div className={'h-full rounded-pill ' + (failed ? 'bg-danger-solid' : done ? 'bg-success' : 'bg-accent-solid')} style={{ width: progress + '%' }} /></div>
    </div>}
    {task.total_bytes > 0 && <p className="text-muted">{task.status === 'pending'
      ? (zh ? '预计数据量：' : 'Planned size: ') + (task.total_bytes / 1048576).toFixed(1) + ' MiB'
      : ((task.processed_bytes ?? task.completed_bytes ?? 0) / 1048576).toFixed(1) + ' / ' + (task.total_bytes / 1048576).toFixed(1) + ' MiB'}</p>}
    {task.execution_policy === 'selected_output' && <p className="flex items-start gap-2 text-muted"><ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{zh ? '按当前输出目录权限执行；本批不会覆盖或删除已有文件。' : 'Runs under selected output permissions without overwriting or deleting existing files.'}</p>}
    <div className="grid min-w-0 gap-3 rounded-control bg-well p-3">
      <div><p className="text-micro text-subtle">{zh ? '来源' : 'Source'}</p><ServerPath path={task.source_root} /></div>
      {task.mode !== 'analysis' && <div><p className="text-micro text-subtle">{needsInplace ? (zh ? '修改原目录' : 'Modify original directory') : (zh ? '输出目录' : 'Output directory')}</p><ServerPath path={task.target_root} /></div>}
    </div>
    {task.scan && <div className="space-y-2 border-l-2 border-line-strong pl-3">
      <p>{zh ? '已扫描普通文件' : 'Scanned regular files'}：{task.scan.file_count} · {zh ? '未匹配' : 'Unmatched'}：{task.scan.unmatched_files}</p>
      {(task.scan.skipped_symlinks ?? 0) > 0 && <div role="status" className="space-y-1 text-muted">
        <p>{zh ? `已跳过 ${task.scan.skipped_symlinks} 个符号链接，本批只处理按真实路径扫描到的普通文件，不通过链接读取或修改目标。` : `Skipped ${task.scan.skipped_symlinks} symbolic links. This batch handles regular files at real paths without reading or modifying targets through links.`}</p>
        {(task.scan.symlink_examples?.length ?? 0) > 0 && <details className="chat-disclosure"><summary>{zh ? '查看跳过的链接' : 'Review skipped links'}</summary>
          <ul className="max-h-48 overflow-auto">{task.scan.symlink_examples?.map(row => <li key={row.path} className="break-all">{row.path}</li>)}</ul>
          {task.scan.symlink_examples_truncated && <p>{zh ? '仅显示前 50 个链接；跳过总数见上方，未展开链接目录内部。' : 'Showing the first 50 links. The total is shown above; linked directories were not traversed.'}</p>}
        </details>}
      </div>}
      {task.scan.matched_files != null && <p>{zh ? '已纳入' : 'Included'}：{task.scan.matched_files} · {zh ? '明确排除' : 'Explicitly excluded'}：{task.scan.excluded_files ?? 0}
        {(task.scan.preserved_files ?? 0) > 0 && <> · {zh ? '按原名保留' : 'Preserved under original names'}：{task.scan.preserved_files}</>}</p>}
      {(task.quality?.regressed_files ?? 0) > 0 && <p role="alert">{zh ? '相比上一版遗漏' : 'Coverage lost since previous plan'}：{task.quality?.regressed_files}</p>}
      <p>{task.mode === 'copy' ? (zh ? '未匹配文件不会复制到输出目录；原文件保持不变。' : 'Unmatched files are not copied to the output directory; originals are preserved.')
        : (zh ? '未匹配文件保持原名和内容。' : 'Unmatched files retain their original names and content.')}</p>
      {task.scan.unmatched_files > 0 && <div className="flex gap-3">
        <button disabled={busy} className="underline" onClick={() => void loadUnmatched()}>{zh ? '查看未匹配文件' : 'Review unmatched files'}</button>
        <button className="underline" onClick={() => void download(true)}>{zh ? '下载完整未匹配清单' : 'Download full unmatched list'}</button>
      </div>}
      {unmatched && <div className="rounded-control border border-line p-2">
        <p>{zh ? '未匹配总数' : 'Total unmatched'}：{unmatched.total}</p>
        {!unmatched.snapshot && <p>{zh ? '旧批次未保存排除快照；这里是重新扫描的当前结果。' : 'This older batch has no exclusion snapshot; these are current rescan results.'}</p>}
        <p>{Object.entries(unmatched.extension_counts).map(([type, count]) => type + ': ' + count).join(' · ')}</p>
        <ul className="max-h-48 overflow-auto">{unmatched.items.map(item => <li key={item.path} className="break-all">{item.path}</li>)}</ul>
        {unmatched.next_offset !== null && <button disabled={busy} className="underline" onClick={() => void loadUnmatched(unmatched.next_offset ?? 0)}>{zh ? '下一页' : 'Next page'}</button>}
      </div>}
    </div>}
    {previewLoaded && !scriptProposal && task.mode !== 'analysis' && <details className="chat-disclosure"><summary>{zh ? '查看操作预览' : 'Review proposed changes'} ({task.items.length}/{task.file_count})</summary><div className="max-h-72 overflow-auto rounded-control border border-line">
      <table className="w-full min-w-[28rem] text-left"><thead className="sticky top-0 bg-panel"><tr><th className="p-2">{zh ? '操作' : 'Action'}</th><th className="p-2">{zh ? '原路径' : 'Source'}</th><th className="p-2">{zh ? '新路径' : 'Target'}</th></tr></thead>
        <tbody>{task.items.map((item, index) => <tr key={index} className="border-t border-line">
          <td className="p-1 align-top">{actions[item.action] ?? item.action}</td>
          <td className="break-all p-1 align-top">{item.path}{item.diff && <details><summary>{zh ? '查看改动' : 'Review changes'}</summary><pre className="max-w-lg whitespace-pre-wrap">{item.diff}</pre></details>}
            {item.has_artifact && <a className="underline" href={apiBase() + '/api/data-operations/' + encodeURIComponent(task.id) + '/artifacts/' + item.index + '?conversation_id=' + encodeURIComponent(task.conversation_id)}
              onClick={event => { event.preventDefault(); void downloadFile('/api/data-operations/' + encodeURIComponent(task.id) + '/artifacts/' + item.index + '?conversation_id=' + encodeURIComponent(task.conversation_id)).catch(error => setError(error.message)); }}>{zh ? '下载新版本检查' : 'Download new version for review'}</a>}
          </td>
          <td className="break-all p-1 align-top">{item.destination ?? item.path}</td>
        </tr>)}</tbody>
      </table>
    </div></details>}
    {!scriptProposal && task.mode !== 'analysis' && <button className="focus-ring rounded-chip text-accent underline underline-offset-2" onClick={() => void download()}>{zh ? '下载完整操作清单' : 'Download full change list'}</button>}
    {previewLoaded && task.preview_truncated && <p className="text-muted">{zh ? `此处显示前 ${task.items.length} 项；确认前请下载完整清单。` : `Showing the first ${task.items.length} items. Download the full list before approval.`}</p>}
    {task.verification_report && <details className="chat-disclosure"><summary>{zh ? '方案核验结果' : 'Plan validation'}</summary>
      <p>{zh ? '已通过函数案例' : 'Function cases passed'}：{task.verification_report.function_cases_status === 'passed' ? task.verification_report.function_case_count : 0}</p>
      <ul className="list-disc pl-4">{task.verification_report.checks.map(check => <li key={check.criterion}>{({unique_destinations: zh ? '目标路径唯一' : 'Unique destinations', selected_file_coverage: zh ? '所选文件完整覆盖' : 'Selected file coverage', stem_group_preservation: zh ? '同名关联保持一致' : 'Stem groups preserved'} as Record<string, string>)[check.criterion] ?? check.criterion}：{check.passed ? '✓' : '×'}</li>)}</ul>
      <p className="text-muted">{zh ? task.verification_report.semantic_notice : 'Verified declared file invariants; semantic choices still require review against your request.'}</p>
    </details>}
    {task.sandbox_environment && <details className="chat-disclosure"><summary>{zh ? '脚本环境与资源' : 'Script environment and resources'}</summary><p>{task.sandbox_environment}
      {task.sandbox_resources && <> · {task.sandbox_resources.timeout_seconds} s · {task.sandbox_resources.memory_mb} MiB · {zh ? '输出上限' : 'Output limit'} {task.sandbox_resources.output_mb} MiB</>}</p></details>}
    {task.script && <details className="chat-disclosure"><summary>{zh ? '查看生成脚本' : 'Review generated script'}</summary><pre className="max-h-64 overflow-auto rounded-control bg-well p-3">{task.script}</pre></details>}
    {task.execution_log && <details className="chat-disclosure" open={task.mode === 'analysis'}><summary>{zh ? '分析结果 / 执行日志' : 'Analysis / execution log'}</summary>
      {task.execution_log_capture_limited && <p className="my-2 text-muted">{zh ? '日志达到保存上限，可能不完整；下载仅包含已保存内容，无法恢复未保存部分。完整文件操作应使用结构化操作清单。' : 'The log reached its capture limit and may be incomplete. Download contains only saved content; omitted output cannot be recovered. Use a structured change list for complete file operations.'}</p>}
      {task.execution_log_truncated && !task.execution_log_capture_limited && <p className="my-2 text-muted">{zh ? '此处仅显示部分已保存日志；可下载查看，不能将此处内容视为完整操作清单。' : 'Only part of the saved log is shown. Download it to review; this display is not a complete change list.'}</p>}
      {task.execution_log_available && <a className="my-2 block text-accent underline" href={apiBase() + '/api/data-operations/' + encodeURIComponent(task.id) + '/log?conversation_id=' + encodeURIComponent(task.conversation_id)}
        onClick={event => { event.preventDefault(); void downloadFile('/api/data-operations/' + encodeURIComponent(task.id) + '/log?conversation_id=' + encodeURIComponent(task.conversation_id)).catch(error => setError(error.message)); }}>{zh ? '下载已保存日志' : 'Download saved log'}</a>}
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-control bg-well p-3">{task.execution_log}</pre></details>}
    {task.conflicts.length > 0 && <div className="space-y-2 rounded-control border border-danger-line bg-danger-quiet p-3 text-danger">
      <div role="alert"><p className="font-medium">{zh ? '冲突' : 'Conflicts'}：{task.conflicts.length.toLocaleString()}</p>
        <ul className="mt-1 space-y-1">{task.conflicts.slice(0, 3).map((conflict, i) => <li key={i} className="break-words">{conflict}</li>)}</ul>
      </div>
      {task.conflicts.length > 3 && <details className="chat-disclosure">
        <summary>{zh ? '查看冲突列表' : 'Review conflicts'} ({task.conflicts.length.toLocaleString()})</summary>
        <ul className="max-h-56 space-y-2 overflow-y-auto text-meta">
          {task.conflicts.slice(currentConflictPage * 20, currentConflictPage * 20 + 20).map((conflict, i) =>
            <li key={currentConflictPage * 20 + i} className="break-words">{conflict}</li>)}
        </ul>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={currentConflictPage === 0} onClick={() => setConflictPage(currentConflictPage - 1)}>{zh ? '上一页' : 'Previous page'}</Button>
          <span className="tabular-nums">{currentConflictPage + 1}/{conflictPages}</span>
          <Button size="sm" disabled={currentConflictPage + 1 >= conflictPages} onClick={() => setConflictPage(currentConflictPage + 1)}>{zh ? '下一页' : 'Next page'}</Button>
        </div>
      </details>}
    </div>}
    {((state === 'ready' && task.can_confirm) || task.can_undo) && (needsInplace || hasDelete || hasOverwrite) && <div className="space-y-2 rounded-control border border-warning-line bg-warning-quiet p-3">
      <p className="font-medium text-warning">{zh ? '本批操作范围' : 'Changes in this batch'}</p>
      {needsInplace && <p>{zh ? '将修改原目录；内容修改保留原版本。' : 'Modifies the original directory and retains previous content versions.'}</p>}
      {hasDelete && <p>{zh ? '包含清单中的删除操作。' : 'Includes the listed deletions.'}</p>}
      {hasOverwrite && <p>{zh ? '将覆盖清单中的已有输出文件；保留原版本以便恢复。' : 'Overwrites the listed outputs, retaining previous versions for recovery.'}</p>}
    </div>}
    <div className="flex flex-wrap gap-3">
      {state === 'ready' && scriptProposal && task.can_run_script && <Button disabled={busy || disabled || !previewLoaded} variant="primary"
        onClick={() => void act('script')}>{zh ? '运行脚本' : 'Run script'}</Button>}
      {state === 'ready' && !scriptProposal && task.can_confirm && <Button disabled={busy || disabled || !allowed} variant="primary"
        onClick={() => void act('confirm')}>{zh ? '运行本批操作' : 'Run this batch'}</Button>}
      {task.can_undo && <Button disabled={busy || disabled || !allowed}
        onClick={() => void act('undo')}>{zh ? '恢复本批修改' : 'Undo this batch'}</Button>}
      {!starting && (task.status === 'pending' || (task.status === 'interrupted' && !task.can_undo)) && <Button disabled={stopping} variant="ghost"
        onClick={() => void act('cancel')}>{zh ? '撤销待运行方案' : 'Withdraw pending proposal'}</Button>}
    </div>
    {(error || task.error) && <p role="alert" className="break-words text-danger">{error || task.error}</p>}
    {error && !previewLoaded && <Button size="sm" variant="ghost" onClick={() => setReload(value => value + 1)}>{zh ? '重新读取方案' : 'Reload proposal'}</Button>}
    </div>
  </section>;
}
