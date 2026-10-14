import { request } from '../../api/http';
import type { ExecutionOptions } from '../workflows/workflowTypes';
import type { EventPage, MonitorUpdate, RunListPage, WorkflowRun } from './runsTypes';

export function fetchWorkflowRuns(workflowId: string, signal?: AbortSignal): Promise<RunListPage> {
  return request<RunListPage>(
    `/api/runs?workflow_id=${encodeURIComponent(workflowId)}&limit=1&summary=true`,
    { signal }
  );
}

export function fetchRunMonitor(
  runId: string,
  afterRevision: number,
  signal?: AbortSignal
): Promise<MonitorUpdate> {
  return request<MonitorUpdate>(
    `/api/runs/${encodeURIComponent(runId)}/monitor?after_revision=${afterRevision}`,
    { signal }
  );
}

export function fetchRunEvents(
  runId: string,
  input: { afterEventId: number; tail: boolean },
  signal?: AbortSignal
): Promise<EventPage> {
  return request<EventPage>(
    `/api/runs/${encodeURIComponent(runId)}/events?after_event_id=${input.afterEventId}&tail=${input.tail}&limit=200`,
    { signal }
  );
}

export function fetchRunEventsBefore(
  runId: string,
  beforeEventId: number,
  signal?: AbortSignal
): Promise<EventPage> {
  return request<EventPage>(
    `/api/runs/${encodeURIComponent(runId)}/events?before_event_id=${beforeEventId}&limit=20`,
    { signal }
  );
}

export function startRun(
  workflowId: string,
  expectedRevision: number,
  execution: ExecutionOptions
): Promise<WorkflowRun> {
  return request<WorkflowRun>(`/api/workflows/${encodeURIComponent(workflowId)}/runs`, {
    method: 'POST',
    body: JSON.stringify({
      expected_revision: expectedRevision,
      execution: execution.backend === 'local'
        ? { backend: 'local', output_layout: execution.output_layout } : execution
    })
  });
}

export function retryRun(runId: string, stepIds: string[]): Promise<WorkflowRun> {
  return request<WorkflowRun>(`/api/runs/${encodeURIComponent(runId)}/retry`, {
    method: 'POST',
    body: JSON.stringify({ step_ids: stepIds })
  });
}

function runAction(runId: string, action: 'cancel' | 'resume-monitoring'): Promise<WorkflowRun> {
  return request<WorkflowRun>(`/api/runs/${encodeURIComponent(runId)}/${action}`, {
    method: 'POST'
  });
}

export function cancelRun(runId: string): Promise<WorkflowRun> {
  return runAction(runId, 'cancel');
}

export function resumeRunMonitor(runId: string): Promise<WorkflowRun> {
  return runAction(runId, 'resume-monitoring');
}
