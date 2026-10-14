import type { Workflow } from './workflowTypes';

export type TaskEntry = {
  task_id?: string;
  workflow_id: string | null;
  plan_id?: string | null;
  plan_kind?: 'workflow' | 'data' | 'workspace' | null;
  task_revision?: number;
  conversation_id?: string | null;
  title: string;
  status: string;
  updated_at: string;
  run_count: number;
  latest_run_status: string | null;
  source: 'pipeline' | 'conversation';
  dataset_label: string | null;
  workspace_path: string | null;
};
export function taskFromWorkflow(workflow: Workflow): TaskEntry {
  return {
    task_id: workflow.managed_task_id ?? workflow.workflow_id, plan_id: workflow.workflow_id, plan_kind: 'workflow',
    workflow_id: workflow.workflow_id, title: workflow.title, status: workflow.status,
    updated_at: workflow.updated_at, run_count: 0, latest_run_status: null,
    source: workflow.planner_source === 'pipeline-library' ? 'pipeline' : 'conversation',
    dataset_label: workflow.dataset?.display_name ?? null,
    workspace_path: workflow.workspace_root_path ?? null
  };
}
export function taskIndexUrl(offset: number, q: string, source: string, status: string) {
  return `/api/task-index?${new URLSearchParams({offset: String(offset), q, source, status})}`;
}

export function taskIdentity(task: TaskEntry) { return task.task_id ?? task.workflow_id ?? task.plan_id ?? ''; }
