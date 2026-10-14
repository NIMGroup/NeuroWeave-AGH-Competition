import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { request, errorText } from '../../api/http';

export type ExecutionActivity = {
  id: string; kind: 'data' | 'workspace' | 'run' | 'check'; conversation_id: string | null;
  title: string; status: string; can_cancel: boolean; cancel_requested: boolean;
  completed?: number; total?: number; progress?: number;
};
export const executionKey = (item: ExecutionActivity) => item.kind + ':' + item.id;
export function cancelExecution(item: ExecutionActivity) {
  const id = encodeURIComponent(item.id);
  if (item.kind === 'data') return request('/api/data-operations/' + id + '/cancel?conversation_id='
    + encodeURIComponent(item.conversation_id ?? ''), { method: 'POST' });
  if (item.kind === 'workspace') return request('/api/fs/operations/' + id + '/cancel', {
    method: 'POST', body: JSON.stringify({ confirmed: true, conversation_id: item.conversation_id })
  });
  return request((item.kind === 'run' ? '/api/runs/' : '/api/operations/') + id + '/cancel', { method: 'POST' });
}

/** App-level observation survives navigation; discovering work cannot start it. */
export function useBackgroundExecutions() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ['execution-activities'],
    queryFn: () => request<{ activities: ExecutionActivity[] }>('/api/execution-activities',
      { signal: AbortSignal.timeout(15_000) }),
    refetchInterval: 2000, refetchIntervalInBackground: true, retry: false
  });
  const [actions, setActions] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  async function stop(item: ExecutionActivity) {
    const key = executionKey(item);
    if (actions[key] || item.cancel_requested) return;
    setActions(previous => ({ ...previous, [key]: true }));
    setErrors(previous => ({ ...previous, [key]: '' }));
    try {
      await cancelExecution(item);
      client.setQueryData<{ activities: ExecutionActivity[] }>(['execution-activities'], previous => previous && ({
        activities: previous.activities.map(value => executionKey(value) === key ? { ...value, cancel_requested: true } : value)
      }));
    } catch (error) {
      setErrors(previous => ({ ...previous, [key]: errorText(error) }));
    } finally {
      setActions(previous => ({ ...previous, [key]: false }));
      void client.invalidateQueries({ queryKey: ['execution-activities'] });
      if (item.conversation_id) void client.invalidateQueries({ queryKey: ['conversation', item.conversation_id] });
    }
  }
  return { records: query.data?.activities ?? [], actions, errors, stop, reconnecting: query.isError };
}
