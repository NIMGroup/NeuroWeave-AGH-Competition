import { useRef, useState } from 'react';
import { Terminal } from 'lucide-react';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import { fetchRunEventsBefore } from './runsApi';
import type { RunEvent } from './runsTypes';

type HistoryPage = { events: RunEvent[]; has_older?: boolean };

export function RunLogs({ runId, events }: { runId: string; events: RunEvent[] }) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [history, setHistory] = useState<HistoryPage | null>(null);
  const [cursors, setCursors] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(0);
  const displayed = history?.events ?? events.slice(-20);
  const load = async (next: number[]) => {
    const generation = ++request.current;
    if (!next.length) {
      setHistory(null);
      setCursors([]);
      setBusy(false);
      setError('');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const page = await fetchRunEventsBefore(runId, next[next.length - 1]);
      if (generation !== request.current) return;
      setHistory(page);
      setCursors(next);
    } catch (failure) {
      if (generation === request.current) {
        setError(failure instanceof Error ? failure.message : t.runLogsError);
      }
    } finally {
      if (generation === request.current) setBusy(false);
    }
  };
  return (
    <details className="group/log mt-3 min-w-0 rounded-control border border-line bg-well text-ink">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-control px-3 py-2.5 text-sm [&::-webkit-details-marker]:hidden">
        <Terminal className="size-4 shrink-0" />
        <span className="flex-1">{history ? t.runLogsHistorical : t.runLogsLive}</span>
      </summary>
      <div className="max-h-64 space-y-1 overflow-y-auto border-t border-line p-3 font-mono text-xs">
        {displayed.map((event) => (
          <p className={event.level === 'error' ? 'text-red-300' : ''} key={event.event_id}>
            [{event.event_id}] {event.message}
          </p>
        ))}
        {!displayed.length && <p>{t.runLogsEmpty}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-line p-2 text-xs">
        <button
          type="button"
          className="rounded-control border border-line-strong px-2 py-1 disabled:opacity-50"
          disabled={busy || !displayed.length || history?.has_older === false}
          onClick={() => void load([...cursors, displayed[0].event_id])}
        >
          {t.runLogsOlder}
        </button>
        <button
          type="button"
          className="rounded-control border border-line-strong px-2 py-1 disabled:opacity-50"
          disabled={busy || !history}
          onClick={() => void load(cursors.slice(0, -1))}
        >
          {t.runLogsNewer}
        </button>
        {history && (
          <button
            type="button"
            className="rounded-control border border-line-strong px-2 py-1"
            onClick={() => void load([])}
          >
            {t.runLogsBackToLive}
          </button>
        )}
        {busy && <span>{t.loadingOlder}</span>}
        {error && (
          <span role="alert" className="text-red-300">
            {error}
          </span>
        )}
      </div>
    </details>
  );
}
