import type { SampleScope, ExecutionOptions } from '../workflows/workflowTypes';

export type RunStatus =
  'queued' | 'running' | 'cancel_requested' | 'cancelled' | 'succeeded' | 'failed';

export type RunArtifact = {
  artifact_id: string;
  step_id: string;
  artifact_type: string;
  label: string;
  absolute_path: string;
  relative_path: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  metadata: Record<string, unknown>;
};

export type RunStep = {
  step_id: string;
  title: string;
  runner_id: string;
  method_id: string;
  sample_id?: string | null;
  sample_label?: string;
  node_name?: string | null;
  status: 'queued' | 'running' | 'cancelled' | 'succeeded' | 'failed';
  progress: number;
  message: string;
  artifacts: RunArtifact[];
  error_code: string;
  error_message: string;
  started_at: string | null;
  finished_at: string | null;
  scheduler?: {
    job_id?: string | number;
    state?: string;
    node_list?: string;
    reason?: string;
    [key: string]: unknown;
  };
};

export type WorkflowRun = {
  workspace_id?: string | null;
  workspace_root_path?: string | null;
  output_relative_path?: string;
  sample_scope?: SampleScope | null;
  state_revision?: number;
  retry_of?: string | null;
  reused_step_ids?: string[];
  run_id: string;
  workflow_id: string;
  workflow_revision: number;
  execution?: ExecutionOptions;
  status: RunStatus;
  progress: number;
  current_step_id: string | null;
  output_directory: string;
  plan_snapshot: Record<string, unknown>;
  steps: RunStep[];
  artifacts: RunArtifact[];
  error_code: string;
  error_message: string;
  cancellation_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
};

export type RunEvent = {
  event_id: number;
  event_type: string;
  level: 'info' | 'warning' | 'error';
  message: string;
  progress: number | null;
  step_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type RunListPage = {
  runs: WorkflowRun[];
};

export type MonitorUpdate = {
  run: Omit<WorkflowRun, 'steps' | 'plan_snapshot'>;
  steps: RunStep[];
  reset: boolean;
};

export type EventPage = {
  events: RunEvent[];
  next_event_id?: number;
  has_more?: boolean;
  has_older?: boolean;
};

export function mergeRunSummary(previous: WorkflowRun | null, summary: WorkflowRun | null): WorkflowRun | null {
  if (!summary) return previous;
  if (!previous || previous.run_id !== summary.run_id) return summary;
  if ((previous.state_revision ?? 0) > (summary.state_revision ?? 0)) return previous;
  return { ...previous, ...summary, steps: previous.steps, artifacts: summary.artifacts.length ? summary.artifacts : previous.artifacts };
}

export function mergeRunUpdate(previous: WorkflowRun, update: MonitorUpdate): WorkflowRun {
  const steps = new Map((update.reset ? [] : previous.steps).map((step) => [step.step_id, step]));
  update.steps.forEach((step) => steps.set(step.step_id, step));
  const ordered = [...steps.values()];
  const artifacts = new Map(
    [...update.run.artifacts, ...ordered.flatMap((step) => step.artifacts)].map((artifact) => [
      artifact.artifact_id,
      artifact
    ])
  );
  return {
    ...previous,
    ...update.run,
    steps: ordered,
    artifacts: [...artifacts.values()],
    plan_snapshot: {}
  };
}
