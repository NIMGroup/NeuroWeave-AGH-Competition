import { useState } from 'react';
import { History, Loader2, X } from 'lucide-react';
import { useWorkflowHistoryPreview, useWorkflowInputHistory } from '../workflows/useWorkflows';
import type { HistoryCandidate } from '../workflows/workflowsApi';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';

const PREVIEW_PAGE_SIZE = 20;
const PAGE_STEP = 10;

type HistoricalInputPickerProps = {
  workflowId: string;
  revision: number;
  stepId: string;
  slotId: string;
  slotLabel: string;
  disabled: boolean;
  onSelected: (runId: string, groupId: string, allowPartial: boolean) => void;
};

export function HistoricalInputPicker({
  workflowId,
  revision,
  stepId,
  slotId,
  slotLabel,
  disabled,
  onSelected
}: HistoricalInputPickerProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState(0);
  const historyQuery = useWorkflowInputHistory(workflowId, stepId, slotId, offset);
  const previewMutation = useWorkflowHistoryPreview(workflowId, stepId, slotId);
  const preview = previewMutation.data ?? null;
  const candidates = historyQuery.data?.candidates ?? [];
  const rows =
    preview?.matches.slice(page * PREVIEW_PAGE_SIZE, (page + 1) * PREVIEW_PAGE_SIZE) ?? [];
  const pageCount = Math.max(1, Math.ceil((preview?.matches.length ?? 0) / PREVIEW_PAGE_SIZE));
  const busy = historyQuery.isFetching || previewMutation.isPending;
  const error = (historyQuery.error ?? previewMutation.error) as Error | null;
  const partial = Boolean(preview && preview.coverage.matched < preview.coverage.total);

  const load = (position: number) => {
    setPage(0);
    setOffset(Math.max(0, position));
    previewMutation.reset();
  };

  const choose = (candidate: HistoryCandidate) => {
    setPage(0);
    previewMutation.mutate({
      runId: candidate.run_id,
      groupId: candidate.group_id,
      expectedRevision: revision
    });
  };

  return (
    <div className="min-w-0">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setOffset(0);
          setPage(0);
          previewMutation.reset();
          setOpen(true);
        }}
        className="inline-flex max-w-full items-center gap-1.5 rounded-control border border-line px-2.5 py-1.5 text-xs font-medium transition hover:bg-hover disabled:opacity-50 "
      >
        <History className="h-3.5 w-3.5 shrink-0" />
        {t.workflowHistoryPick}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-card border border-line bg-raised shadow-xl ">
            <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3 ">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold">
                  {t.workflowHistoryTitle.replace('{{slot}}', slotLabel)}
                </h3>
                <p className="mt-0.5 text-xs text-muted">{t.workflowHistoryDescription}</p>
              </div>
              <button
                type="button"
                aria-label={t.workflowClose}
                onClick={() => {
                  setOpen(false);
                  previewMutation.reset();
                }}
                className="rounded-control p-1.5 text-subtle hover:bg-hover"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3 text-sm">
              <p className="text-xs text-muted">{t.workflowHistoryHint}</p>
              <div
                aria-label={t.workflowHistoryCandidates}
                className="max-h-52 space-y-2 overflow-y-auto rounded-control border border-line p-2 "
              >
                {candidates.map((candidate) => {
                  const active =
                    preview?.run_id === candidate.run_id &&
                    preview?.group_id === candidate.group_id;
                  return (
                    <button
                      key={`${candidate.run_id}:${candidate.group_id}`}
                      type="button"
                      disabled={busy}
                      onClick={() => choose(candidate)}
                      aria-pressed={active}
                      className={`w-full rounded-control border p-2.5 text-left text-xs disabled:opacity-60 ${
                        active ? 'border-accent-line bg-accent-quiet ' : 'border-line'
                      }`}
                    >
                      <span className="block break-words font-medium">
                        {candidate.run_title} · {candidate.step_title}
                      </span>
                      <span className="mt-1 block break-words text-muted">
                        {candidate.label} ·{' '}
                        {t.workflowHistorySamples.replace(
                          '{{count}}',
                          String(candidate.sample_count)
                        )}{' '}
                        · {new Date(candidate.created_at).toLocaleString()}
                      </span>
                      <span className="mt-1 block break-all font-mono text-micro text-subtle">
                        {candidate.run_id}
                      </span>
                    </button>
                  );
                })}
                {!busy && candidates.length === 0 && (
                  <p className="p-3 text-xs text-subtle">{t.workflowHistoryEmpty}</p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={busy || offset === 0}
                  onClick={() => load(offset - PAGE_STEP)}
                  className="rounded-control border border-line px-2.5 py-1.5 text-xs disabled:opacity-50 "
                >
                  {t.workflowHistoryNewer}
                </button>
                <button
                  type="button"
                  disabled={busy || !historyQuery.data?.has_more}
                  onClick={() => load(historyQuery.data?.next_offset ?? offset)}
                  className="rounded-control border border-line px-2.5 py-1.5 text-xs disabled:opacity-50 "
                >
                  {t.workflowHistoryOlder}
                </button>
                {busy && (
                  <span className="flex items-center gap-1.5 text-xs text-subtle">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    {t.workflowHistoryChecking}
                  </span>
                )}
              </div>
              {error && (
                <p role="alert" className="break-words text-xs text-danger">
                  {error.message || t.workflowHistorySelectError}
                </p>
              )}
              {preview && (
                <div className="min-w-0 space-y-2 rounded-control border border-line p-3 ">
                  <p className="text-xs font-medium">
                    {t.workflowHistoryPreviewSummary
                      .replace('{{matched}}', String(preview.coverage.matched))
                      .replace('{{total}}', String(preview.coverage.total))
                      .replace('{{missing}}', String(preview.coverage.missing))
                      .replace('{{invalid}}', String(preview.coverage.invalid))}
                  </p>
                  <div
                    aria-label={t.workflowHistoryRows}
                    className="max-h-60 space-y-2 overflow-y-auto"
                  >
                    {rows.map((match) => (
                      <div
                        key={match.sample_id}
                        className="min-w-0 rounded border border-line p-2 text-xs "
                      >
                        <p className={match.status === 'matched' ? 'text-success' : 'text-danger'}>
                          {match.label} · {match.message}
                        </p>
                        <p className="mt-1 break-all font-mono text-subtle">
                          {match.absolute_path || t.workflowHistoryNoPath}
                        </p>
                      </div>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={page === 0}
                      onClick={() => setPage(page - 1)}
                      className="rounded-control px-2 py-1 text-xs text-muted disabled:opacity-50"
                    >
                      {t.workflowHistoryPagePrevious}
                    </button>
                    <span className="text-xs text-muted">
                      {page + 1} / {pageCount}
                    </span>
                    <button
                      type="button"
                      disabled={page + 1 >= pageCount}
                      onClick={() => setPage(page + 1)}
                      className="rounded-control px-2 py-1 text-xs text-muted disabled:opacity-50"
                    >
                      {t.workflowHistoryPageNext}
                    </button>
                  </div>
                  {preview.messages.map((message) => (
                    <p key={message} className="text-xs text-subtle">
                      {message}
                    </p>
                  ))}
                  {preview.can_apply && partial && (
                    <p className="text-xs text-warning">
                      {t.workflowHistoryPartialNotice.replace(
                        '{{count}}',
                        String(preview.coverage.matched)
                      )}
                    </p>
                  )}
                  {!preview.can_apply && (
                    <p role="alert" className="text-xs text-warning">
                      {t.workflowHistoryNoMatch}
                    </p>
                  )}
                </div>
              )}
            </div>
            <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-line px-4 py-3 ">
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  previewMutation.reset();
                }}
                className="rounded-control border border-line px-2.5 py-1.5 text-xs "
              >
                {t.workflowClose}
              </button>
              <button
                type="button"
                disabled={busy || disabled || !preview?.can_apply}
                onClick={() => {
                  if (!preview?.can_apply) return;
                  onSelected(preview.run_id, preview.group_id, partial);
                  setOpen(false);
                  previewMutation.reset();
                }}
                className="rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent hover:bg-accent-solid-hover disabled:opacity-50"
              >
                {(partial ? t.workflowHistoryApplyPartial : t.workflowHistoryApply).replace(
                  '{{count}}',
                  String(preview?.coverage.matched ?? 0)
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
