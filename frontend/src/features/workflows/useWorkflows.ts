import { useEffect } from 'react';
import { useLanguageStore } from '../../stores/languageStore';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { ApiError } from '../../api/http';
import type { OperationView } from '../../api/operations';
import {
  bindStepHistoryInput,
  bindStepInput,
  clearStepInput,
  completeDependencies,
  fetchExecutionBackends,
  fetchInputHistory,
  fetchWorkflow,
  patchStepMethod,
  patchStepParameters,
  patchWorkflowDataset,
  previewInputHistory,
  previewStepInputPath,
  removeStep,
  runDryRun,
  runWorkflowLifecycleAction
} from './workflowsApi';
import type { WorkflowLifecycleAction } from './workflowsApi';
import type { InputSelectionMode, ParameterValue, Workflow } from './workflowTypes';
import { workflowCheckKey } from './useWorkflowCheckStatus';

export function workflowQueryKey(workflowId: string) {
  return ['workflow', workflowId] as const;
}

export function adoptWorkflow(queryClient: QueryClient, workflow: Workflow) {
  queryClient.setQueryData<Workflow>(workflowQueryKey(workflow.workflow_id), previous =>
    previous && previous.revision > workflow.revision ? previous : workflow);
}

export function adoptWorkflowSnapshot(queryClient: QueryClient, workflowId: string | null, snapshot?: Workflow | null) {
  if (!snapshot || snapshot.workflow_id !== workflowId) return;
  const current = queryClient.getQueryData<Workflow>(workflowQueryKey(snapshot.workflow_id));
  if (!current || current.revision < snapshot.revision) adoptWorkflow(queryClient, snapshot);
}

export function adoptConflict(queryClient: QueryClient, error: unknown): boolean {
  if (error instanceof ApiError && error.current) {
    const current = error.current as Workflow;
    if (current?.workflow_id) {
      adoptWorkflow(queryClient, current);
      return true;
    }
  }
  return false;
}

export function useWorkflowQuery(workflowId: string | null, initial?: Workflow | null, frozen = false) {
  const language = useLanguageStore(s => s.language);
  const localeClient = useQueryClient();
  useEffect(() => { if (!frozen) adoptWorkflowSnapshot(localeClient, workflowId, initial); }, [localeClient, workflowId, initial, frozen]);
  useEffect(() => {
    if (workflowId && !frozen) void localeClient.invalidateQueries({queryKey: workflowQueryKey(workflowId)});
  }, [language, workflowId, localeClient, frozen]);
  return useQuery({
    queryKey: frozen ? [...workflowQueryKey(workflowId ?? ''),'closed',initial?.revision] : workflowQueryKey(workflowId ?? ''),
    enabled: !frozen && workflowId !== null && workflowId !== '',
    queryFn: ({ signal }) => {
      if (!workflowId) throw new Error('No workflow selected');
      return fetchWorkflow(workflowId, signal);
    },
    initialData: initial?.workflow_id === workflowId ? initial : undefined,
    structuralSharing: (previous, incoming) => {
      const old = previous as Workflow | undefined;
      const fresh = incoming as Workflow;
      return old && old.workflow_id === fresh.workflow_id && old.revision > fresh.revision ? old : fresh;
    },
    staleTime: 10_000,
    retry: 1
  });
}

export type WorkflowStepAction =
  | { kind: 'method'; stepId: string; methodId: string }
  | { kind: 'parameters'; stepId: string; parameters: Record<string, ParameterValue> }
  | { kind: 'input'; stepId: string; slotId: string; rootId: string; relativePath: string; bindingMode?: InputSelectionMode }
  | { kind: 'clear-input'; stepId: string; slotId: string }
  | {
      kind: 'history';
      stepId: string;
      slotId: string;
      runId: string;
      groupId: string;
      allowPartial: boolean;
    }
  | { kind: 'remove-step'; stepId: string }
  | { kind: 'complete-dependencies' };

export type WorkflowAction = WorkflowStepAction | { kind: WorkflowLifecycleAction; acknowledgeQuality?:boolean };

export function workflowActionKey(action: WorkflowAction): string {
  if ('slotId' in action) return `${action.kind}:${action.stepId}:${action.slotId}`;
  if ('stepId' in action) return `${action.kind}:${action.stepId}`;
  return action.kind;
}

function applyAction(
  workflowId: string,
  action: WorkflowAction,
  expectedRevision: number,
  onProgress?: (operation: OperationView) => void
): Promise<Workflow> {
  switch (action.kind) {
    case 'method':
      return patchStepMethod(workflowId, action.stepId, action.methodId, expectedRevision);
    case 'parameters':
      return patchStepParameters(workflowId, action.stepId, action.parameters, expectedRevision);
    case 'input':
      return bindStepInput(
        workflowId,
        action.stepId,
        action.slotId,
        action.rootId,
        action.relativePath,
        expectedRevision,
        action.bindingMode
      );
    case 'clear-input':
      return clearStepInput(workflowId, action.stepId, action.slotId, expectedRevision);
    case 'history':
      return bindStepHistoryInput(
        workflowId,
        action.stepId,
        action.slotId,
        { runId: action.runId, groupId: action.groupId, allowPartial: action.allowPartial },
        expectedRevision
      );
    case 'remove-step':
      return removeStep(workflowId, action.stepId, expectedRevision);
    case 'complete-dependencies':
      return completeDependencies(workflowId, expectedRevision, onProgress);
    default:
      return runWorkflowLifecycleAction(workflowId, action.kind, expectedRevision, action.acknowledgeQuality, onProgress);
  }
}

export function useWorkflowAction(workflowId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ['workflow-change', workflowId],
    mutationFn: (input: { action: WorkflowAction; expectedRevision: number }) =>
      applyAction(workflowId, input.action, input.expectedRevision,
        operation => queryClient.setQueryData(workflowCheckKey(workflowId), operation)),
    onSuccess: (workflow) => {
      adoptWorkflow(queryClient, workflow);
    },
    onError: (error) => {
      adoptConflict(queryClient, error);
    }
  });
}

export function useWorkflowDryRun(workflowId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ['workflow-check-submit', workflowId],
    mutationFn: (input: {
      expectedRevision: number;
      onProgress?: (operation: OperationView) => void;
      signal?: AbortSignal;
    }) =>
      runDryRun(workflowId, input.expectedRevision, {
        onProgress: operation => {
          queryClient.setQueryData(workflowCheckKey(workflowId), operation);
          input.onProgress?.(operation);
        },
        signal: input.signal
      }),
    onSuccess: (workflow) => {
      adoptWorkflow(queryClient, workflow);
    },
    onError: error => { adoptConflict(queryClient, error); }
  });
}

export function useWorkflowDataset(workflowId: string | null = null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ['workflow-change', workflowId],
    mutationFn: (input: { workflowId: string; datasetId: string; expectedRevision: number }) =>
      patchWorkflowDataset(input.workflowId, input.datasetId, input.expectedRevision),
    onSuccess: (workflow) => {
      adoptWorkflow(queryClient, workflow);
    },
    onError: (error) => {
      adoptConflict(queryClient, error);
    }
  });
}

export function useWorkflowInputHistory(
  workflowId: string,
  stepId: string,
  slotId: string,
  offset: number
) {
  return useQuery({
    queryKey: ['workflow', workflowId, 'history', stepId, slotId, offset],
    queryFn: ({ signal }) => fetchInputHistory(workflowId, stepId, slotId, offset, signal),
    staleTime: 10_000,
    retry: 1
  });
}

export function useWorkflowHistoryPreview(workflowId: string, stepId: string, slotId: string) {
  return useMutation({
    mutationKey: ['workflow-read', workflowId],
    mutationFn: (input: { runId: string; groupId: string; expectedRevision: number }) =>
      previewInputHistory(workflowId, stepId, slotId, input)
  });
}

export function useWorkflowPathPreview(workflowId: string, stepId: string, slotId: string) {
  return useMutation({
    mutationKey: ['workflow-read', workflowId],
    mutationFn: (input: { rootId: string; relativePath: string; bindingMode?: InputSelectionMode }) =>
      previewStepInputPath(workflowId, stepId, slotId, input.rootId, input.relativePath, undefined, input.bindingMode)
  });
}

export function useExecutionBackends(enabled: boolean) {
  return useQuery({
    queryKey: ['execution', 'backends'],
    enabled,
    queryFn: ({ signal }) => fetchExecutionBackends(signal),
    staleTime: 15_000,
    retry: 1
  });
}
