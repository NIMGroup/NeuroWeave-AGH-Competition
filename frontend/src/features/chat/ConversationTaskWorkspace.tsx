import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { request, errorText } from '../../api/http';
import { ManagedTaskCard } from '../workflows/ManagedTaskCard';
import { WorkflowCard } from '../workflows/WorkflowCard';
import { DataOperationCard } from '../data/DataOperationCard';
import { WorkspaceConfirmation } from '../workspace/WorkspaceConfirmation';
import { useLanguageStore } from '../../stores/languageStore';
import type { AssistantTaskState } from './AgentTaskStatus';
import type { ChatMessage } from './chatMessages';
import type { Workflow } from '../workflows/workflowTypes';
import type { TaskEntry } from '../workflows/taskIndex';
import type { WorkspaceEvent } from '../workspace/workspaceTypes';
import { conversationPlan } from './conversationPlan';
import { goalOutcome } from './taskLifecycle';

type Props = {
  conversationId?: string | null; task?: AssistantTaskState | null; messages: ChatMessage[];
  workflowId: string | null; workflow?: Workflow | null; live?: WorkspaceEvent[];
  sending: boolean; disabled?: boolean; onSelectTask?: (task: TaskEntry) => void;
  onRequestRevision?: (text: string) => void;
  onPresenceChange?: (visible: boolean) => void;
};

/** One viewport owns all plan rendering; messages retain revision explanations. */
export function ConversationTaskWorkspace(props: Props) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  return <section id="conversation-task-workspace" data-conversation-task-workspace
    aria-label={zh ? '当前方案工作区' : 'Current plan workspace'}
    className="min-w-0 scroll-mt-4 space-y-3 empty:hidden">
    {props.conversationId ? <LiveTaskWorkspace {...props} conversationId={props.conversationId} /> : <TaskView {...props} />}
  </section>;
}

function LiveTaskWorkspace(props: Props & { conversationId: string }) {
  const client = useQueryClient();
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const key = ['conversation-task', props.conversationId];
  const fallback = conversationPlan(props.messages, props.task, props.workflowId, props.workflow, props.live);
  const hasEvidence = Boolean(fallback.taskId || fallback.workflowId || fallback.operations.length);
  const query = useQuery({ queryKey: key,
    queryFn: ({signal}) => request<{task: AssistantTaskState | null; closed_tasks?: AssistantTaskState[]}>(`/api/conversations/${encodeURIComponent(props.conversationId)}/task`, {signal}),
    initialData: props.task ? {task: props.task} : undefined, initialDataUpdatedAt: 0,
    refetchInterval: props.sending ? 1500 : hasEvidence ? 5000 : false, retry: 1 });
  useEffect(() => { void client.invalidateQueries({queryKey:['conversation-task', props.conversationId]}, {cancelRefetch:false}); },
    [client, props.conversationId, props.sending, props.task?.task_id, props.task?.task_revision, props.task?.task_deleted, props.task?.phase]);
  const stored = query.data?.task;
  const suppliedNewer = (props.task?.updated_at ?? 0) > (stored?.updated_at ?? 0)
    || (stored?.task_id === props.task?.task_id && (stored?.task_revision ?? 1) < (props.task?.task_revision ?? 1));
  const task = (props.task?.task_deleted && !props.sending) || suppliedNewer ? props.task : query.data ? stored : props.task;
  const closed = Boolean(goalOutcome(task));
  const archives = [...new Map([
    ...(query.data?.closed_tasks ?? []), ...(closed && task ? [task] : [])
  ].filter(t => goalOutcome(t) && !t.task_deleted).map(t => [t.task_id,t])).values()];
  const currentPlan = conversationPlan(props.messages, task, props.workflowId, props.workflow, props.live);
  const visible = Boolean(archives.length || currentPlan.taskId || currentPlan.workflowId || currentPlan.operations.length);
  const {onPresenceChange} = props;
  useEffect(() => { onPresenceChange?.(visible); }, [onPresenceChange, visible]);
  // While resolving the task identity, never revive historical Run cards.
  return <>
    {archives.map(t =>
      <details key={t.task_id} className="rounded-card border border-line p-3 text-meta" data-archived-goal={t.task_id}>
        <summary className="cursor-pointer break-words">{goalOutcome(t)==='ended'
          ? (zh ? '已结束（未完成）：' : 'Ended without completion: ')
          : (zh ? '已确认完成的目标：' : 'Confirmed completed goal: ')}{t.goal}</summary>
        <div className="mt-3"><ManagedTaskCard taskId={t.task_id} initialTask={t} disabled /></div>
      </details>)}
    {closed ? null : query.isPending && !task && hasEvidence ? <p role="status" className="flex items-center gap-2 text-meta text-muted">
      <Loader2 className="size-4 animate-spin" />{zh ? '正在同步当前方案…' : 'Synchronizing the current plan…'}
    </p> : <TaskView {...props} task={task} pendingIdentity={props.sending && hasEvidence && !task?.active_plan?.id} />}
    {query.isError && <p role="alert" className="text-meta text-danger">{errorText(query.error, zh ? '当前任务同步失败' : 'Could not synchronize task')}
      <button className="ml-2 underline" onClick={() => void query.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
  </>;
}

function TaskView({task, messages, workflowId, workflow, live, disabled, sending, onSelectTask, onRequestRevision,
  pendingIdentity = false}: Props & {pendingIdentity?: boolean}) {
  const plan = conversationPlan(messages, task, workflowId, workflow, live);
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const locked = disabled || sending;
  if (plan.taskId) return <ManagedTaskCard key={plan.taskId} taskId={plan.taskId} initialTask={task} defaultExpanded
    disabled={locked} onSelectTask={onSelectTask} onRequestRevision={onRequestRevision} />;
  if (task?.task_deleted) return null;
  if (pendingIdentity && !plan.workflowId) return <p role="status" className="text-meta text-muted">{zh ? '正在更新当前方案，修订将在这里显示。' : 'Updating the current plan in this workspace.'}</p>;
  if (plan.workflowId) return <WorkflowCard workflowId={plan.workflowId} initialWorkflow={plan.workflow} defaultExpanded disabled={locked} />;
  return <>{plan.operations.map(event => event.data_operation
    ? <DataOperationCard key={'data:' + event.data_operation.id} operation={event.data_operation} disabled={locked} onRequestRevision={locked ? undefined : onRequestRevision} />
    : event.operation ? <div key={'workspace:' + event.operation.id} id={'workspace-operation-' + event.operation.id}>
      <WorkspaceConfirmation operation={event.operation} conversationId={event.operation.conversation_id} disabled={locked} persistent />
    </div> : null)}</>;
}
