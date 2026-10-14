import { useMemo, useState } from 'react';
import { CheckCircle2, FolderOpen, Loader2, TriangleAlert, X } from 'lucide-react';
import { useDatasetRoots, useDirectoryListing } from './useDatasets';
import type { Dataset } from './datasetsApi';
import { useWorkflowPathPreview } from '../workflows/useWorkflows';
import type { InputSelectionMode, InputSlotSpec } from '../workflows/workflowTypes';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';

const PREVIEW_LIMIT = 50;

type ArtifactPathPickerProps = {
  workflowId: string;
  stepId: string;
  slot: InputSlotSpec;
  dataset: Dataset | null;
  disabled: boolean;
  onSelected: (rootId: string, relativePath: string, mode?: InputSelectionMode) => void;
};

export function ArtifactPathPicker({
  workflowId,
  stepId,
  slot,
  dataset,
  disabled,
  onSelected
}: ArtifactPathPickerProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [open, setOpen] = useState(false);
  const [rootId, setRootId] = useState('');
  const [relativePath, setRelativePath] = useState('');
  const [pathDraft, setPathDraft] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [bindingMode, setBindingMode] = useState<InputSelectionMode>('auto');
  const rootsQuery = useDatasetRoots();
  const roots = useMemo(() => rootsQuery.data?.roots ?? [], [rootsQuery.data]);
  const listingQuery = useDirectoryListing(open ? rootId : '', relativePath);
  const listing = listingQuery.data ?? null;
  const previewMutation = useWorkflowPathPreview(workflowId, stepId, slot.id);
  const preview = previewMutation.data ?? null;
  const pathValue = pathDraft ?? listing?.relative_path ?? '';

  const openPicker = () => {
    const initial = dataset?.root_id || roots[0]?.root_id || '';
    setRootId(initial);
    setRelativePath(dataset?.root_id === initial ? (dataset.relative_path ?? '') : '');
    setPathDraft(null);
    setFilter('');
    previewMutation.reset();
    setOpen(true);
  };

  const openDirectory = (next: string) => {
    setFilter('');
    setPathDraft(null);
    previewMutation.reset();
    setRelativePath(next.replace(/\\/g, '/').replace(/^\/+/, ''));
  };

  const canChooseFolder = slot.binding === 'folder' || slot.binding === 'file_bundle';
  const canChooseFile = slot.binding === 'file' || slot.binding === 'file_bundle';
  const acceptsFile = (name: string) =>
    slot.formats.length === 0 ||
    slot.formats.some((format) => name.toLowerCase().endsWith(format.toLowerCase()));
  const query = filter.trim().toLowerCase();
  const directories = (listing?.directories ?? []).filter((item) =>
    item.name.toLowerCase().includes(query)
  );
  const files = (listing?.files ?? []).filter(
    (item) => acceptsFile(item.name) && item.name.toLowerCase().includes(query)
  );
  const busy = listingQuery.isFetching || previewMutation.isPending;
  const error =
    rootsQuery.error || listingQuery.error || previewMutation.error
      ? ((rootsQuery.error ?? listingQuery.error ?? previewMutation.error) as Error).message ||
        t.workflowPickerPreviewError
      : null;

  const choose = (target: string) => {
    if (!dataset) {
      onSelected(rootId, target);
      setOpen(false);
      return;
    }
    setFilter('');
    previewMutation.mutate({ rootId, relativePath: target, bindingMode });
  };

  return (
    <div className="min-w-0">
      <button
        type="button"
        disabled={disabled}
        onClick={openPicker}
        className="inline-flex max-w-full items-center gap-1.5 rounded-control border border-line px-2.5 py-1.5 text-xs font-medium transition hover:bg-hover disabled:opacity-50 "
      >
        <FolderOpen className="h-3.5 w-3.5 shrink-0" />
        {t.workflowChooseExistingPath}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-card border border-line bg-raised shadow-xl ">
            <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3 ">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold">
                  {t.workflowPickerTitle.replace('{{slot}}', slot.label)}
                </h3>
                <p className="mt-0.5 text-xs leading-5 text-muted">
                  {slot.description}
                  {slot.formats.length > 0
                    ? ` ${t.workflowPickerFormats.replace('{{formats}}', slot.formats.join(', '))}`
                    : ''}
                  {dataset
                    ? ` ${t.workflowPickerFromDataset.replace('{{dataset}}', dataset.display_name)}`
                    : ''}
                </p>
              </div>
              <button
                type="button"
                aria-label={t.workflowClose}
                onClick={() => setOpen(false)}
                className="rounded-control p-1.5 text-subtle hover:bg-hover"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3">
              <div className="flex items-center gap-2">
                <select
                  value={rootId}
                  onChange={(event) => {
                    setRootId(event.target.value);
                    openDirectory('');
                  }}
                  className="min-w-0 flex-1 rounded-control border border-line bg-transparent px-2 py-1.5 text-xs "
                >
                  {roots.map((root) => (
                    <option key={root.root_id} value={root.root_id} disabled={!root.readable}>
                      {root.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  disabled={busy || !rootId}
                  onClick={() => openDirectory(relativePath)}
                  className="rounded-control border border-line px-2.5 py-1.5 text-xs disabled:opacity-50 "
                >
                  {t.datasetRefreshDirectory}
                </button>
              </div>
              <nav className="flex flex-wrap items-center gap-1 text-meta text-muted">
                {(listing?.breadcrumbs ?? []).map((crumb) => (
                  <button
                    key={crumb.relative_path}
                    type="button"
                    disabled={busy}
                    onClick={() => openDirectory(crumb.relative_path)}
                    className="rounded px-1 py-0.5 hover:bg-hover"
                  >
                    {crumb.label}
                  </button>
                ))}
              </nav>
              <form
                className="flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (rootId) openDirectory(pathValue);
                }}
              >
                <input
                  aria-label={t.datasetPath}
                  value={pathValue}
                  onChange={(event) => setPathDraft(event.target.value)}
                  placeholder={t.datasetPath}
                  className="min-w-0 flex-1 rounded-control border border-line bg-transparent px-2 py-1.5 font-mono text-xs "
                />
                <button
                  type="submit"
                  disabled={busy || !rootId}
                  className="rounded-control border border-line px-2.5 py-1.5 text-xs disabled:opacity-50 "
                >
                  {t.datasetGo}
                </button>
              </form>
              <input
                aria-label={t.datasetFilterPlaceholder}
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder={t.datasetFilterPlaceholder}
                className="w-full rounded-control border border-line bg-transparent px-2 py-1.5 text-xs "
              />
              {dataset && <select aria-label={t.workflowInputRuleTitle} value={bindingMode}
                onChange={e => {setBindingMode(e.target.value as InputSelectionMode); previewMutation.reset();}}
                className="w-full rounded-control border border-line bg-transparent px-2 py-1.5 text-xs">
                <option value="auto">{t.workflowInputRuleBatch}</option>
                <option value="single">{t.workflowInputRuleShared}</option>
              </select>}
              <div className="max-h-64 min-h-32 overflow-y-auto rounded-control border border-line">
                {listing?.parent_relative_path !== null &&
                  listing?.parent_relative_path !== undefined && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => openDirectory(listing.parent_relative_path ?? '')}
                      className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs text-muted hover:bg-hover"
                    >
                      {t.datasetBackToParent}
                    </button>
                  )}
                {canChooseFolder && listing && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => choose(listing.relative_path)}
                    className="flex w-full items-center gap-2 border-b border-line px-2.5 py-1.5 text-left text-xs font-medium text-accent hover:bg-hover dark:text-accent "
                  >
                    <FolderOpen className="h-3.5 w-3.5 shrink-0" />
                    {t.workflowPickerChooseFolder}
                  </button>
                )}
                {directories.map((directory) => (
                  <button
                    key={directory.relative_path}
                    type="button"
                    disabled={busy || !directory.readable}
                    onClick={() => openDirectory(directory.relative_path)}
                    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-hover disabled:opacity-45 "
                  >
                    <span className="truncate">{directory.name}</span>
                  </button>
                ))}
                {canChooseFile &&
                  files.map((file) => (
                    <button
                      key={file.relative_path}
                      type="button"
                      disabled={busy || !file.readable}
                      onClick={() => choose(file.relative_path)}
                      className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left font-mono text-xs hover:bg-hover disabled:opacity-45 "
                    >
                      <span className="truncate">{file.name}</span>
                    </button>
                  ))}
                {busy && (
                  <p className="flex items-center justify-center gap-2 py-10 text-xs text-subtle">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {t.datasetLoading}
                  </p>
                )}
                {!busy && directories.length === 0 && (!canChooseFile || files.length === 0) && (
                  <p className="py-10 text-center text-xs text-subtle">
                    {query ? t.datasetNoMatch : t.workflowPickerEmpty}
                  </p>
                )}
              </div>
              {listing && (
                <p className="text-meta text-subtle">
                  {t.datasetCounts.replace('{{directories}}', String(listing.directory_count))}
                  {' · '}
                  {t.datasetFileCount.replace('{{count}}', String(listing.file_count))}
                  {slot.formats.length > 0 ? ` · ${t.workflowPickerFormatsOnly}` : ''}
                  {listing.directories_truncated || listing.files_truncated
                    ? ` · ${t.datasetTruncated}`
                    : ''}
                </p>
              )}
              {preview && (
                <div className="rounded-card border border-accent-line bg-running-quiet p-3 dark:border-accent-line ">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="text-xs font-semibold text-accent">
                      {preview.binding_mode === 'per_sample'
                        ? t.workflowPickerPreviewPerSample
                        : t.workflowPickerPreviewSingle}
                    </p>
                    <span className="rounded border border-accent-line bg-raised px-1.5 py-0.5 text-micro dark:border-accent-line dark:bg-transparent">
                      {preview.validation === 'pending_dry_run' ? t.workflowInputRulePendingCount.replace('{{count}}', String(preview.coverage.total)) : t.workflowPickerMatched
                        .replace('{{matched}}', String(preview.coverage.matched))
                        .replace('{{total}}', String(preview.coverage.total))}
                    </span>
                  </div>
                  <p className="mt-1 overflow-x-auto whitespace-nowrap font-mono text-micro text-accent">
                    {preview.path_template || preview.selected_relative_path}
                  </p>
                  <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-control border border-accent-line bg-raised p-2 dark:border-accent-line dark:bg-transparent">
                    {preview.matches.slice(0, PREVIEW_LIMIT).map((match) => (
                      <div key={match.sample_id} className="flex items-start gap-2 text-micro">
                        {match.status === 'pending' ? <span className="mt-0.5 h-3 w-3 shrink-0 rounded-full border border-line-strong" /> : match.status === 'matched' ? (
                          <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-success" />
                        ) : (
                          <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
                        )}
                        <span className="w-32 shrink-0 truncate font-medium">{match.label}</span>
                        <span className="min-w-0 break-all font-mono text-muted">
                          {match.relative_path || match.message}
                        </span>
                      </div>
                    ))}
                    {preview.matches.length > PREVIEW_LIMIT && (
                      <p className="pt-1 text-center text-micro text-subtle">
                        {t.workflowPickerRemaining.replace(
                          '{{count}}',
                          String(preview.matches.length - PREVIEW_LIMIT)
                        )}
                      </p>
                    )}
                  </div>
                  {preview.messages.map((message) => (
                    <p key={message} className="mt-1 text-micro text-muted">
                      {message}
                    </p>
                  ))}
                  {!preview.can_apply && (
                    <p className="mt-2 text-micro text-warning">{t.workflowPickerCannotApply}</p>
                  )}
                  <div className="mt-2 flex flex-wrap justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => previewMutation.reset()}
                      className="rounded-control px-2.5 py-1.5 text-xs text-muted"
                    >
                      {t.workflowPickerReselect}
                    </button>
                    <button
                      type="button"
                      disabled={!preview.can_apply}
                      onClick={() => {
                        onSelected(rootId, preview.selected_relative_path, bindingMode);
                        setOpen(false);
                        previewMutation.reset();
                      }}
                      className="rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent hover:bg-accent-solid-hover disabled:opacity-50"
                    >
                      {preview.validation === 'pending_dry_run' ? t.workflowInputRuleApply : preview.binding_mode === 'per_sample'
                        ? t.workflowPickerApply.replace(
                            '{{count}}',
                            String(preview.coverage.matched)
                          )
                        : t.workflowPickerConfirm}
                    </button>
                  </div>
                </div>
              )}
              {error && (
                <p role="alert" className="text-xs text-danger">
                  {error}
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
