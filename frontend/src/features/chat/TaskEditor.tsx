import { useState } from 'react';
import { request } from '../../api/http';
import { Button } from '../../ui/Button';
import type { AssistantTaskState } from './AgentTaskStatus';
import { goalOutcome } from './taskLifecycle';

export function TaskEditor({ task, conversationId, zh, busy, onUpdated }: {
  task: AssistantTaskState; conversationId: string; zh: boolean; busy: boolean;
  onUpdated: (task: AssistantTaskState, replan: boolean) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [closingProgress, setClosingProgress] = useState('');
  const [draft, setDraft] = useState(() => ({
    user_requirements: task.user_requirements || task.decisions?.map(d => d.quote).join('\n') || task.goal,
    goal: task.goal, scope_notes: task.scope_notes || '', success_criteria: task.success_criteria?.join('\n') || ''
  }));
  const persist = async (clear = false) => {
    if (busy || saving) return;
    setSaving(true); setError('');
    try {
      const updated = await request<AssistantTaskState>(`/api/conversations/${encodeURIComponent(conversationId)}/task${clear ? '/clear' : ''}`, {
        method: clear ? 'POST' : 'PATCH', body: JSON.stringify({ ...draft, task_id: task.task_id,
          expected_revision: task.task_revision ?? 1, success_criteria: draft.success_criteria.split('\n').map(s => s.trim()).filter(Boolean) })
      });
      setEditing(false); onUpdated(updated, !clear);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };
  const closeGoal = async (outcome:'completed'|'ended') => {
    if (busy || saving || goalOutcome(task)) return;
    const ended = outcome === 'ended';
    if (!window.confirm(ended
      ? (zh ? '结束目标并归档？即使未完成也可以结束，已有结果会保留。已暂停的计算将先停止，正在运行的计算需先手动停止。'
        : 'End and archive this goal without completing it? Existing results will remain. Paused computation will be stopped; running computation must be stopped first.')
      : (zh ? '确认此目标已完成并结束？当前方案将归档，后续需求会开启新目标。本操作不会运行流程或修改文件。'
        : 'Confirm this goal is complete? Its final plan will be archived and later requests can start a new goal. This does not run the plan or change files.'))) return;
    setSaving(true); setError('');
    try {
      const deadline=Date.now()+45000;
      while (true) {
        const updated = await request<AssistantTaskState | {status:'stopping';message:string}>(
          '/api/conversations/' + encodeURIComponent(conversationId) + '/task/' + (ended ? 'end' : 'confirm-completion'),
          {method:'POST',body:JSON.stringify({task_id:task.task_id,expected_revision:task.task_revision ?? 1,
            expected_plan:task.active_plan ?? null,confirmed:true})});
        if ('task_id' in updated) { onUpdated(updated,false); break; }
        setClosingProgress(zh ? updated.message : 'Stopping paused computation before archiving…');
        if (Date.now()>=deadline) throw new Error(zh ? '停止尚未确认，请查看执行状态，停止完成后再次归档。' : 'Stop is not confirmed yet. Check execution status, then try archiving again.');
        await new Promise(resolve => setTimeout(resolve,1500));
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); setClosingProgress(''); }
  };
  if (goalOutcome(task)) return <div className="space-y-2 text-meta">
    <p>{goalOutcome(task) === 'ended'
      ? (zh ? '此目标已主动结束，尚未完成；方案和已有结果已归档。可以通过对话提出下一个目标。' : 'This goal was ended without completion. Its plan and existing results are archived. Describe your next goal in the conversation.')
      : (zh ? '你已确认此目标完成，最终方案已归档。可以通过对话提出下一个目标。' : 'You confirmed this goal is complete. Its final plan is archived. Describe your next goal in the conversation.')}</p>
    <Button size="sm" variant="ghost" disabled={busy || saving} onClick={() => {
      if (window.confirm(zh ? '删除归档任务记录？会话、运行记录和输出文件仍会保留。' : 'Delete this archived task entry? Messages, runs and output files will remain.')) void persist(true);
    }}>{zh ? '删除任务记录' : 'Delete task entry'}</Button>
    {error && <p role="alert" className="text-danger">{error}</p>}
  </div>;
  return <div className="space-y-2 text-meta">
    {task.active_plan && (task.active_plan.task_revision ?? 1) !== (task.task_revision ?? 1) &&
      <p className="text-warning">{zh ? '当前方案对应旧目标，请更新方案后再运行。' : 'The current plan belongs to the previous goal. Update it before running.'}</p>}
    {editing ? <>
      {(['user_requirements', 'goal', 'scope_notes', 'success_criteria'] as const).map((field, index) => <label key={field} className="block space-y-1">
        <span>{(zh ? ['用户要求', '任务目标', '处理范围', '完成条件（每行一项）'] : ['User requirements', 'Task goal', 'Scope', 'Success criteria (one per line)'])[index]}</span>
        <textarea value={draft[field]} rows={field === 'user_requirements' ? 4 : 3} maxLength={field === 'user_requirements' ? 8000 : 1200}
          className="w-full resize-y rounded-control border border-line bg-well p-2 text-ink" disabled={busy || saving}
          onChange={e => setDraft(previous => ({ ...previous, [field]: e.target.value }))} />
      </label>)}
      <div className="flex gap-2"><Button size="sm" disabled={busy || saving || !draft.goal.trim() || !draft.user_requirements.trim()} onClick={() => void persist()}>
        {saving ? (zh ? '保存中…' : 'Saving…') : (zh ? '保存并更新方案' : 'Save and update plan')}</Button>
        <Button variant="ghost" size="sm" disabled={saving} onClick={() => setEditing(false)}>{zh ? '取消' : 'Cancel'}</Button></div>
      <p className="text-muted">{zh ? '更新方案不会启动计算或修改文件。' : 'Updating the plan does not run computation or change files.'}</p>
    </> : <div className="flex flex-wrap gap-2">
      <Button size="sm" disabled={busy || saving} onClick={() => setEditing(true)}>{zh ? '编辑任务目标' : 'Edit task goal'}</Button>
      <Button size="sm" variant="ghost" disabled={busy || saving} onClick={() => onUpdated(task, true)}>{zh ? '更新方案' : 'Update plan'}</Button>
      <Button size="sm" variant="ghost" disabled={busy || saving} onClick={() => void closeGoal('completed')}>
        {zh ? '确认完成并结束此目标' : 'Confirm completion and close goal'}</Button>
      <Button size="sm" variant="ghost" disabled={busy || saving} onClick={() => void closeGoal('ended')}>
        {zh ? '结束目标并归档' : 'End and archive goal'}</Button>
    </div>}
    {busy && <p className="text-muted">{zh ? '请先停止当前请求，等待停止完成后再编辑。' : 'Stop the current request and wait for it to finish before editing.'}</p>}
    {closingProgress && <p role="status" className="text-muted">{closingProgress}</p>}
    {error && <p role="alert" className="break-words text-danger">{error}</p>}
  </div>;
}
