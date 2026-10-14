import { useEffect, useRef } from 'react';
import { useIsMutating, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from '../../api/http';
import type { OperationView } from '../../api/operations';

export type WorkflowCheck = OperationView & {
  kind?: string; expected_revision?: number; error?: { message?: string };
};
export const workflowCheckKey = (id: string) => ['workflow-check', id] as const;
export const checkIsActive = (check: WorkflowCheck | null | undefined) =>
  Boolean(check && ['queued', 'running', 'cancel_requested'].includes(check.status));

/** Server status survives card unmounts and reloads. Discovery cannot start work. */
export function useWorkflowCheckStatus(workflowId: string) {
  const client = useQueryClient();
  const handled = useRef<string | null>(null);
  const submitting = useIsMutating({ predicate: mutation => {
    const key = mutation.options.mutationKey;
    return key?.[1] === workflowId && (key[0] === 'workflow-check-submit' || key[0] === 'workflow-change');
  } });
  const query = useQuery({
    queryKey: workflowCheckKey(workflowId),
    queryFn: async ({ signal }) => (await request<{ operation: WorkflowCheck | null }>(
      `/api/workflows/${encodeURIComponent(workflowId)}/checks/latest`, { signal })).operation,
    staleTime: 0, retry: 1,
    refetchInterval: query => checkIsActive(query.state.data) || submitting > 0
      ? 1000 : query.state.status === 'error' ? 2000 : false,
    refetchIntervalInBackground: true
  });
  const operation = query.data;
  useEffect(() => {
    if (!operation || checkIsActive(operation)) return;
    const key = operation.operation_id + ':' + operation.status;
    if (handled.current === key) return;
    handled.current = key;
    void client.invalidateQueries({ queryKey: ['workflow', workflowId] });
    void client.invalidateQueries({ queryKey: ['execution-activities'] });
  }, [operation, client, workflowId]);
  return { operation, active: checkIsActive(operation) || submitting > 0,
    reconnecting: query.isError, refresh: query.refetch };
}
