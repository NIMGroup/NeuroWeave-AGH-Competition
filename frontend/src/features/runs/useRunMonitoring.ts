import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { SetStateAction } from 'react';
import { fetchRunEvents, fetchRunMonitor, fetchWorkflowRuns } from './runsApi';
import { mergeRunSummary, mergeRunUpdate } from './runsTypes';
import type { RunEvent, WorkflowRun } from './runsTypes';

const ACTIVE_RUN_STATUSES = ['queued', 'running', 'cancel_requested'];

type EventTail = { events: RunEvent[]; cursor: number; hasMore: boolean };

/** Query owns server state; monitor and event requests retry independently. */
export function useWorkflowRunMonitoring(workflowId: string) {
  const client = useQueryClient();
  const runKey = ['workflow-run', workflowId] as const;
  const runQuery = useQuery({
    queryKey: runKey,
    queryFn: async ({ signal }) => {
      const newest = (await fetchWorkflowRuns(workflowId, signal)).runs[0] ?? null;
      return mergeRunSummary(client.getQueryData<WorkflowRun | null>(runKey) ?? null, newest);
    },
    staleTime: 0,
    // Discover a backend submission even if its UI receipt was lost or the
    // workbench was reopened. Active runs have their own one-second monitor.
    refetchInterval: query => query.state.data && ACTIVE_RUN_STATUSES.includes(query.state.data.status) ? false : 2000
  });
  const run = runQuery.data ?? null;
  const runId = run?.run_id;
  const active = !!run && ACTIVE_RUN_STATUSES.includes(run.status);
  const monitorKey = ['run-monitor', runId] as const;
  const eventsKey = ['run-events', runId] as const;
  const monitor = useQuery({
    queryKey: monitorKey,
    enabled: !!runId,
    queryFn: async ({ signal }) => {
      const revision = client.getQueryData<number>(monitorKey) ?? -1;
      const update = await fetchRunMonitor(runId!, revision, signal);
      if (!signal.aborted)
        client.setQueryData<WorkflowRun | null>(runKey, (current) =>
          current && current.run_id === runId ? mergeRunUpdate(current, update) : current
        );
      return update.run.state_revision ?? 0;
    },
    refetchInterval: active ? 1000 : false
  });
  const events = useQuery({
    queryKey: eventsKey,
    enabled: !!runId,
    queryFn: async ({ signal }): Promise<EventTail> => {
      const previous = client.getQueryData<EventTail>(eventsKey);
      const page = await fetchRunEvents(
        runId!,
        {
          afterEventId: previous?.cursor ?? 0,
          tail: !previous
        },
        signal
      );
      const merged = new Map((previous?.events ?? []).map((event) => [event.event_id, event]));
      page.events.forEach((event) => merged.set(event.event_id, event));
      return {
        events: [...merged.values()].sort((a, b) => a.event_id - b.event_id).slice(-1000),
        cursor: page.next_event_id ?? previous?.cursor ?? 0,
        hasMore: page.has_more ?? false
      };
    },
    refetchInterval: (query) => (query.state.data?.hasMore ? 100 : active ? 1000 : false)
  });
  const setRun = (value: SetStateAction<WorkflowRun | null>) => {
    void client.cancelQueries({ queryKey: runKey });
    client.setQueryData<WorkflowRun | null>(runKey, (current) =>
      typeof value === 'function' ? value(current ?? null) : value
    );
  };
  const setRunEvents = (value: SetStateAction<RunEvent[]>) => {
    void client.cancelQueries({ queryKey: eventsKey });
    // When clearing events on run start, preserve the EventTail structure
    client.setQueryData<EventTail | undefined>(eventsKey, (current) => {
      const events = typeof value === 'function' ? value(current?.events ?? []) : value;
      return {
        events,
        cursor: current?.cursor ?? 0,
        hasMore: current?.hasMore ?? false
      };
    });
  };
  return {
    run,
    setRun,
    setRunEvents,
    runEvents: events.data?.events ?? [],
    loading: runQuery.isPending,
    error: runQuery.error ?? monitor.error ?? events.error,
    refresh: () => {
      if (!runId) void runQuery.refetch();
      else {
        void monitor.refetch();
        void events.refetch();
      }
    }
  };
}
