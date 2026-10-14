import type { ChatMessage } from './chatMessages';
import type { Workflow } from '../workflows/workflowTypes';
import type { AssistantTaskState } from './AgentTaskStatus';
import type { WorkspaceEvent } from '../workspace/workspaceTypes';
import { latestOperationEvents } from '../workspace/workspaceActivity';

export function conversationPlan(messages: ChatMessage[], task?: AssistantTaskState | null,
  workflowId?: string | null, workflow?: Workflow | null, live: WorkspaceEvent[] = []) {
  if (task?.task_deleted) return { taskId: null, workflowId: null, workflow: null, operations: [] };
  if (task?.task_id) {
    return { taskId: task.deliverable !== 'answer' || task.active_plan?.id ? task.task_id : null,
      workflowId: null, workflow: null, operations: [] };
  }
  const history = messages.filter(m => m.role === 'assistant');
  const identity = workflowId ?? history.findLast(m => m.workflow || m.workflowId)?.workflow?.workflow_id
    ?? history.findLast(m => m.workflow || m.workflowId)?.workflowId ?? null;
  // A late historical snapshot or another workflow cannot seed the current graph.
  const candidates = [...history.flatMap(m => m.workflow ? [m.workflow] : []), ...(workflow ? [workflow] : [])]
    .filter(w => w.workflow_id === identity);
  const latest = candidates.reduce<Workflow | null>((best, w) => !best || w.revision > best.revision ? w : best, null);
  return { taskId: null, workflowId: identity, workflow: latest,
    operations: identity ? [] : latestOperationEvents([...history.flatMap(m => m.workspaceEvents ?? []), ...live]) };
}
