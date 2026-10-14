import { useEffect, useState } from 'react';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';
import { cancelFsOperation, confirmFsOperation, fetchFsOperation } from './workspaceApi';
import type { WorkspaceOperation } from './workspaceTypes';
import { isWorkspaceOperation } from './workspaceTypes';
import { ServerPath } from './ServerPath';

const ACTION_KEYS: Record<string, keyof TranslationKeys> = {
  mkdir: 'workspaceActionMkdir',
  copy: 'workspaceActionCopy',
  move: 'workspaceActionMove',
  rename: 'workspaceActionRename',
  delete: 'workspaceActionDelete',
  restore: 'workspaceActionRestore',
  'read-sensitive': 'workspaceActionReadSensitive'
};

const STATUS_KEYS: Record<string, keyof TranslationKeys> = {
  pending: 'workspaceStatusPending',
  running: 'workspaceStatusRunning',
  succeeded: 'workspaceStatusSucceeded',
  failed: 'workspaceStatusFailed'
};

export function WorkspaceConfirmation({
  operation,
  conversationId,
  onDone,
  onDismiss,
  disabled = false,
  persistent = false
}: {
  operation: WorkspaceOperation;
  conversationId: string | null;
  onDone?: (value: WorkspaceOperation) => void;
  onDismiss?: () => void;
  disabled?: boolean;
  persistent?: boolean;
}) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [current, setCurrent] = useState(operation);
  const [dismissedId, setDismissedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const validOperation = isWorkspaceOperation(operation);
  useEffect(() => {
    if (!validOperation) return;
    let alive = true, fetching = false;
    const refresh = async () => {
      if (fetching) return;
      fetching = true;
      try { const value = await fetchFsOperation(operation.id); if (alive) setCurrent(value); }
      catch (e: unknown) { if (alive) setError(e instanceof Error ? e.message : String(e)); }
      finally { fetching = false; }
    };
    void refresh();
    const timer = busy || current.status === 'running' ? setInterval(() => void refresh(), 1000) : null;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [operation.id, operation.status, validOperation, busy, current.status]);
  useEffect(() => {
    if (persistent || current.action !== 'mkdir' || current.status !== 'succeeded') return;
    // Hide only transient success UI; the server audit record is retained.
    const timer = setTimeout(() => { setDismissedId(current.id); onDismiss?.(); }, 2500);
    return () => clearTimeout(timer);
  }, [current.id, current.action, current.status, onDismiss, persistent]);
  const confirm = async () => {
    if (disabled) return;
    setBusy(true);
    setError('');
    try {
      const value = await confirmFsOperation(current.id, current.conversation_id ?? conversationId);
      setCurrent(value);
      onDone?.(value);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    setStopping(true); setError('');
    try {
      const value = await cancelFsOperation(current.id, current.conversation_id ?? conversationId);
      setCurrent(value); onDone?.(value);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setStopping(false); }
  };
  if (!isWorkspaceOperation(current) || dismissedId === current.id) return null;
  return (
    <div className="my-2 min-w-0 rounded-control border border-warning-line bg-warning-quiet p-3 text-xs ">
      <strong>
        {t[ACTION_KEYS[current.action]] ?? current.action} ·{' '}
        {current.status === 'cancelled' ? (language.startsWith('zh') ? '已停止' : 'Stopped')
          : current.cancel_requested ? (language.startsWith('zh') ? '正在停止' : 'Stopping')
          : t[STATUS_KEYS[current.status]] ?? current.status}
      </strong>
      {(current.status === 'running' || (busy && current.status === 'pending')) && <button
        type="button" className="ml-3 rounded-control border border-line px-2 py-1 disabled:opacity-50"
        disabled={stopping || current.cancel_requested} onClick={() => void stop()}>
        {stopping || current.cancel_requested ? (language.startsWith('zh') ? '正在停止' : 'Stopping')
          : (language.startsWith('zh') ? '停止' : 'Stop')}
      </button>}
      {current.action === 'mkdir' && current.status === 'succeeded' &&
        <button className="float-right underline" onClick={() => { setDismissedId(current.id); onDismiss?.(); }}>{t.workspaceDismiss}</button>}
      <p className="mt-2">{t.workspaceConfirmSource}</p>
      <ServerPath path={current.source} />
      {current.target && (
        <>
          <p>{t.workspaceConfirmTarget}</p>
          <ServerPath path={current.target} />
        </>
      )}
      {current.request.permanent && (
        <p className="font-semibold text-danger">{t.workspaceConfirmPermanent}</p>
      )}
      {current.request.overwrite && <p>{t.workspaceConfirmOverwrite}</p>}
      {current.action === 'read-sensitive' && <p>{t.workspaceConfirmSensitive}</p>}
      {current.status === 'pending' && (
        <button
          type="button"
          className="mt-2 rounded-control border border-line px-2 py-1 disabled:opacity-50 "
          disabled={busy || disabled}
          onClick={() => void confirm()}
        >
          {busy ? t.workspaceConfirmBusy : t.workspaceConfirmButton}
        </button>
      )}
      {current.status === 'running' && (
        <button
          type="button"
          className="mt-2 rounded-control border border-line px-2 py-1 "
          onClick={() =>
            void fetchFsOperation(current.id)
              .then((value) => { setCurrent(value); onDone?.(value); })
              .catch((failure: unknown) => setError(failure instanceof Error ? failure.message : String(failure)))
          }
        >
          {t.workspaceRefreshStatus}
        </button>
      )}
      {(current.error || error) && (
        <p className="mt-2 break-words text-danger">{current.error || error}</p>
      )}
      {['failed', 'cancelled'].includes(current.status) && current.staging_path && (
        <>
          <p>{t.workspaceStaging}</p>
          <ServerPath path={current.staging_path} />
        </>
      )}
      {current.action === 'read-sensitive' && typeof current.result?.text === 'string' && (
        <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all">
          {current.result.text}
        </pre>
      )}
    </div>
  );
}
