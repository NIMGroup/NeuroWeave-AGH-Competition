import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { request, errorText } from '../../api/http';
import { Button } from '../../ui/Button';
import { useLanguageStore } from '../../stores/languageStore';
import { WorkflowCard } from './WorkflowCard';
import { DataOperationCard } from '../data/DataOperationCard';
import { WorkspaceConfirmation } from '../workspace/WorkspaceConfirmation';
import type { DataOperationProgress } from '../data/dataTypes';
import type { WorkspaceOperation } from '../workspace/workspaceTypes';
import type { Workflow } from './workflowTypes';
import type { AssistantTaskState } from '../chat/AgentTaskStatus';
import { TaskEditor } from '../chat/TaskEditor';
import { TaskStatusBadge } from './TaskStatusBadge';
import type { TaskEntry } from './taskIndex';
import { goalOutcome } from '../chat/taskLifecycle';

export type ManagedTaskView = {
  entry: TaskEntry; task: AssistantTaskState; deleted: boolean;
  current_plan: { kind: string; id: string; revision?: string | number; task_revision?: number } | null;
  plan?: Workflow | DataOperationProgress | WorkspaceOperation | null;
  versions: { version: number; kind: string; plan_id: string; revision: string; title?: string; status?: string }[];
};

export function ManagedTaskCard({ taskId, initialEntry, initialTask, disabled = false, defaultExpanded = false, onSelectTask, onOpenConversation, onRequestRevision }: {
  taskId: string; initialEntry?: TaskEntry; initialTask?: AssistantTaskState | null; disabled?: boolean; defaultExpanded?: boolean;
  onSelectTask?: (entry: TaskEntry) => void; onOpenConversation?: (id: string) => void;
  onRequestRevision?: (text: string) => void;
}) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const client = useQueryClient();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const query = useQuery({ queryKey: ['managed-task', taskId],
    queryFn: () => request<ManagedTaskView>(`/api/tasks/${encodeURIComponent(taskId)}`),
    refetchInterval: q => goalOutcome(q.state.data?.task) ? false : 3000 });
  const value = query.data;
  const incomingNewer = initialTask?.task_id === taskId && ((initialTask?.task_revision ?? 1) > (value?.task.task_revision ?? 1)
    || (initialTask?.updated_at ?? 0) > (value?.task.updated_at ?? 0));
  const task = incomingNewer ? initialTask : value?.task ?? initialTask;
  const outcome = goalOutcome(task);
  const closed = Boolean(outcome);
  const entry = value?.entry ?? initialEntry;
  const active = value ? value.current_plan : initialTask?.active_plan ?? (entry?.workflow_id ? { kind: 'workflow', id: entry.workflow_id } : null);
  const currentVersion = value?.versions.find(v => v.kind === active?.kind && v.plan_id === active?.id
    && (v.kind !== 'workflow' || v.revision === String(active?.revision)));
  const stale = Boolean(task && active?.id && (active.task_revision ?? task.active_plan?.task_revision ?? 1) !== (task.task_revision ?? 1));
  useEffect(() => { void client.invalidateQueries({queryKey:['managed-task',taskId]}, {cancelRefetch:false}); },
    [client, taskId, initialTask?.task_revision, initialTask?.active_plan?.id, initialTask?.active_plan?.revision]);
  useEffect(() => {
    if (!closed && active?.kind === 'workflow' && active.revision != null) {
      const cached = client.getQueryData<Workflow>(['workflow', active.id]);
      if (cached && cached.revision < Number(active.revision)) void client.invalidateQueries({queryKey:['workflow',active.id]});
    }
  }, [active?.id, active?.kind, active?.revision, client, closed]);
  const openConversation = async () => {
    setBusy(true); setError('');
    try {
      const result = await request<{ conversation_id: string }>(`/api/tasks/${encodeURIComponent(taskId)}/open-conversation`, { method: 'POST' });
      void client.invalidateQueries({ queryKey: ['conversation', result.conversation_id] });
      onOpenConversation?.(result.conversation_id);
      await client.invalidateQueries({ queryKey: ['managed-task', taskId] });
    } catch (e) { setError(errorText(e, zh ? '无法打开任务会话' : 'Could not open task conversation')); }
    finally { setBusy(false); }
  };
  const replan = async () => {
    setBusy(true); setError('');
    try {
      await request(`/api/tasks/${encodeURIComponent(taskId)}/replan`, { method: 'POST' });
      await client.invalidateQueries({ queryKey: ['managed-task', taskId] });
    } catch (e) { setError(errorText(e, zh ? '无法更新方案' : 'Could not update plan')); }
    finally { setBusy(false); }
  };
  return <article data-task-id={taskId} id={`managed-task-${taskId}`} className="w-full min-w-0 space-y-3 rounded-card border border-line bg-panel p-3">
    <div className="flex min-w-0 items-start gap-3">
      <div className="min-w-0 flex-1"><p className="text-micro text-muted">{outcome==='ended'
        ? (zh ? '已结束（未完成）的目标' : 'Goal ended without completion')
        : closed ? (zh ? '已确认完成的目标' : 'Confirmed completed goal') : (zh ? '当前任务' : 'Current task')}</p>
        <p className="break-words font-medium">{task?.goal ?? entry?.title ?? (zh ? '正在读取任务…' : 'Loading task…')}</p></div>
      {entry && <TaskStatusBadge task={stale ? {...entry,status:'needs_replan'} : entry} />}
    </div>
    {value?.deleted ? <p className="text-meta text-muted">{zh ? '任务已删除，历史记录与输出保留。' : 'Task deleted. History and outputs are retained.'}</p> : <>
      {stale && <p role="status" className="text-meta text-warning">{zh ? '当前方案对应旧目标，请更新方案后再运行。' : 'This plan belongs to the previous goal. Update it before Run.'}</p>}
      {query.isPending && !active && <p className="flex items-center gap-2 text-meta text-muted"><Loader2 className="size-4 animate-spin" />{zh ? '正在准备任务…' : 'Preparing task…'}</p>}
      {active?.kind === 'workflow' && (!closed || value?.plan) && <WorkflowCard key={active.id} workflowId={active.id}
        initialWorkflow={value?.plan as Workflow | null} disabled={disabled || stale || closed} frozen={closed} defaultExpanded={defaultExpanded} />}
      {closed && active?.kind === 'workflow' && value && !value.plan && <p className="text-meta text-muted">
        {zh ? '归档时流程记录已不存在；任务目标和历史说明仍保留。' : 'The workflow record was unavailable at archive time. The goal and historical explanations are retained.'}</p>}
      {active?.kind === 'data' && value?.plan && <DataOperationCard key={active.id} operation={value.plan as DataOperationProgress}
        disabled={disabled || stale || closed} onRequestRevision={disabled || closed ? undefined : onRequestRevision} />}
      {active?.kind === 'workspace' && value?.plan && <WorkspaceConfirmation key={active.id} operation={value.plan as WorkspaceOperation}
        conversationId={entry?.conversation_id ?? null} disabled={disabled || stale || closed} persistent />}
      {task && entry?.conversation_id && <details open={!closed} className="text-meta"><summary>{closed ? (zh ? '目标归档记录' : 'Goal archive record') : (zh ? '任务目标与完成确认' : 'Task goal and completion')}</summary>
        <div className="mt-2"><TaskEditor key={`${task.task_id}:${task.task_revision}`} task={task} conversationId={entry.conversation_id} zh={zh} busy={busy || disabled}
          onUpdated={(updated, prepare) => {
            client.setQueryData<ManagedTaskView>(['managed-task', taskId], old => old ? { ...old, task: updated, deleted: Boolean(updated.task_deleted) } : old);
            void client.invalidateQueries({queryKey:['managed-task',taskId]});
            void client.invalidateQueries({ queryKey: ['conversation', entry.conversation_id] });
            client.setQueryData<{task:AssistantTaskState|null; closed_tasks?:AssistantTaskState[]}>(['conversation-task', entry.conversation_id], old => ({
              task:updated, closed_tasks:old?.closed_tasks ?? [] }));
            void client.invalidateQueries({ queryKey: ['conversation-task', entry.conversation_id] });
            void client.invalidateQueries({ queryKey: ['task-index'] });
            if (prepare) void replan();
          }} /></div></details>}
    </>}
    {!!value?.versions.length && <details className="text-meta text-muted"><summary>{zh ? '修订记录' : 'Revision history'} ({value.versions.length})</summary>
      <p className="mt-2 text-micro">{zh ? '方案在同一张卡中更新；历史记录仅供查阅。' : 'Revisions update this card. History is read-only.'}</p>
      <ol className="mt-2 space-y-1">{value.versions.map(v => {
        const current = currentVersion?.version === v.version;
        const label = v.title || (zh ? {workflow:'流程方案',data:'数据方案',workspace:'文件操作'} : {workflow:'Workflow',data:'Data proposal',workspace:'File operation'})[v.kind as 'workflow'|'data'|'workspace'];
        return <li key={v.version} aria-current={current ? 'true' : undefined}>V{v.version} · {label}{v.kind === 'workflow' ? ` · R${v.revision}` : ''}
          {current && <span className="ml-2 font-medium text-accent">{zh ? '当前版本' : 'Current version'}</span>}</li>;
      })}</ol></details>}
    <div className="flex flex-wrap gap-2">
      {onSelectTask && entry && <Button variant="ghost" size="sm" onClick={() => onSelectTask(entry)}>{zh ? '在任务管理中查看' : 'View in task management'}</Button>}
      {onOpenConversation && !value?.deleted && <Button variant="ghost" size="sm" disabled={busy || disabled} onClick={() => void openConversation()}>{closed ? (zh ? '查看目标会话' : 'View goal conversation') : (zh ? '在会话中继续此任务' : 'Continue this task in conversation')}</Button>}
    </div>
    {(query.isError || error) && <p role="alert" className="break-words text-meta text-danger">{error || errorText(query.error, zh ? '读取任务失败' : 'Could not read task')}</p>}
  </article>;
}
