import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { SetStateAction } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../api/http';
import { fetchExecutionDraft, saveExecutionDraft } from './workflowsApi';
import type { ExecutionOptions } from './workflowTypes';
import { competitionExecutionOptions, executionOptionsFingerprint } from './workflowTypes';

export function executionDraftQueryKey(workflowId: string) {
  return ['workflow', workflowId, 'execution-draft'] as const;
}

export function useExecutionDraft(
  workflowId: string,
  workflowRevision: number,
  defaults: ExecutionOptions,
  frozen = false
) {
  const queryClient = useQueryClient();
  const draftKey = useMemo(() => executionDraftQueryKey(workflowId), [workflowId]);
  const draftQuery = useQuery({
    queryKey: draftKey,
    enabled: !frozen,
    queryFn: ({ signal }) => fetchExecutionDraft(workflowId, signal),
    staleTime: 0,
    retry: 1
  });
  const server = draftQuery.data;
  const serverValue = useMemo(() => competitionExecutionOptions(server?.execution ?? defaults),
    [server?.execution, defaults]);
  const [edited, setEdited] = useState<ExecutionOptions | null>(null);
  const [loadToken, setLoadToken] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const revision = useRef(0);
  const saved = useRef('');
  const conflictRef = useRef(false);
  const queue = useRef<Promise<boolean>>(Promise.resolve(true));
  const flush = useRef<(() => void) | null>(null);
  const execution = edited ?? serverValue;

  // Adopt the fetched draft as the save baseline; only refs change here.
  useEffect(() => {
    if (!server) return;
    revision.current = server.draft_revision;
    saved.current = executionOptionsFingerprint(server.execution ?? defaults);
    conflictRef.current = false;
  }, [server, defaults, loadToken]);

  const persist = useCallback(
    (value: ExecutionOptions) => {
      if (frozen || workflowRevision < 1) return Promise.resolve(false);
      const serialized = executionOptionsFingerprint(value);
      queue.current = queue.current.then(async () => {
        if (conflictRef.current) return false;
        if (serialized === saved.current) return true;
        setSaving(true);
        try {
          const draft = await saveExecutionDraft(workflowId, {
            expectedRevision: workflowRevision,
            expectedDraftRevision: revision.current,
            execution: value
          });
          revision.current = draft.draft_revision;
          saved.current = serialized;
          queryClient.setQueryData(draftKey, draft);
          setSaveError(null);
          setConflict(false);
          return true;
        } catch (failure) {
          if (failure instanceof ApiError && failure.status === 409) {
            conflictRef.current = true;
            setConflict(true);
          }
          setSaveError(failure instanceof Error ? failure.message : '');
          return false;
        } finally {
          setSaving(false);
        }
      });
      return queue.current;
    },
    [draftKey, queryClient, workflowId, workflowRevision, frozen]
  );

  const ready = !frozen && workflowRevision > 0 && server !== undefined && !draftQuery.isFetching;

  useEffect(() => {
    if (!ready || executionOptionsFingerprint(execution) === saved.current) return;
    const timer = setTimeout(() => void persist(execution), 500);
    return () => clearTimeout(timer);
  }, [ready, execution, persist]);

  useLayoutEffect(() => {
    flush.current = ready ? () => void persist(execution) : null;
  }, [ready, execution, persist]);

  useEffect(() => () => flush.current?.(), []);

  const setExecution = useCallback(
    (next: SetStateAction<ExecutionOptions>) => {
      setEdited((previous) => competitionExecutionOptions(
        typeof next === 'function' ? next(previous ?? serverValue) : next));
    },
    [serverValue]
  );

  const loadError =
    draftQuery.error instanceof Error ? draftQuery.error.message : draftQuery.error ? '' : null;

  return {
    execution,
    setExecution,
    ready,
    error: loadError ?? saveError,
    conflict,
    saving,
    save: () => persist(execution),
    reload: () => {
      setEdited(null);
      setConflict(false);
      setSaveError(null);
      conflictRef.current = false;
      setLoadToken((previous) => previous + 1);
      void draftQuery.refetch();
    }
  };
}
