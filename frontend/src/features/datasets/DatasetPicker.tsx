import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, ChevronRight, File, Folder, X } from 'lucide-react';
import { useResizablePanel } from '../../ui/useResizablePanel';
import type { Dataset } from './datasetsApi';
import {
  useDatasetRoots,
  useDirectoryListing,
  useInspectDataset,
  useRegisterDataset,
  useRegisteredDatasets,
  useSelectedDataset
} from './useDatasets';
import { cancelOperation } from '../../api/operations';
import type { OperationView } from '../../api/operations';
import { useDatasetStore } from '../../stores/datasetStore';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import { datasetAbsolutePath, resolveDatasetLocation } from './datasetLocation';

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function displayNameForPath(relativePath: string, fallback: string) {
  const parts = relativePath.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? fallback;
}

type DatasetPickerProps = {
  onClose: () => void;
  selectedDatasetId?: string | null;
  onSelect?: (dataset: Dataset) => void | Promise<void>;
};

export function DatasetPicker({ onClose, onSelect, selectedDatasetId: selection }: DatasetPickerProps) {
  const { selectDataset } = useDatasetStore();
  const dialogRef=useRef<HTMLDialogElement>(null);
  useEffect(()=>{if(dialogRef.current&&!dialogRef.current.open)dialogRef.current.showModal();},[]);
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const {panelRef, style: panelStyle, handles: resizeHandles} = useResizablePanel(language === 'zh' ? '拖动边框调整窗口大小' : 'Drag the border to resize the window');
  const conversationDatasetId = useDatasetStore((s) => s.selectedDatasetId);
  const selectedDatasetId = selection === undefined ? conversationDatasetId : selection;
  const selectedDatasetQuery = useSelectedDataset(selectedDatasetId);
  // Follow the selected dataset until the user navigates. Do not fetch the root
  // while a restored conversation's dataset metadata is still loading.
  const [location, setLocation] = useState<{ rootId: string; relativePath: string } | null>(null);
  const [draft, setPathDraft] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [operation, setOperation] = useState<OperationView | null>(null);
  const [busy, setBusy] = useState<'register' | 'inspect' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootsQuery = useDatasetRoots();
  const datasetsQuery = useRegisteredDatasets();
  const roots = useMemo(() => rootsQuery.data?.roots ?? [], [rootsQuery.data]);
  const selectedDataset = selectedDatasetQuery.data
    ?? datasetsQuery.data?.datasets.find((dataset) => dataset.dataset_id === selectedDatasetId);
  const effectiveRootId = location?.rootId
    ?? (selectedDatasetId ? selectedDataset?.root_id ?? '' : roots[0]?.root_id ?? '');
  const relativePath = location?.relativePath ?? selectedDataset?.relative_path ?? '';
  const effectiveRoot = roots.find(root => root.root_id === effectiveRootId);
  const pathDraft = draft ?? (effectiveRoot ? datasetAbsolutePath(effectiveRoot.path, relativePath) : relativePath);
  const waitingForSelection = location === null && !!selectedDatasetId && !selectedDataset;
  const listingQuery = useDirectoryListing(effectiveRootId, relativePath);
  const registerMutation = useRegisterDataset();
  const inspectMutation = useInspectDataset();
  const datasets = datasetsQuery.data?.datasets ?? [];
  const listing = listingQuery.data ?? null;
  const loadingRoots = rootsQuery.isPending;
  const listingBusy = listingQuery.isPending || listingQuery.isFetching;
  const q = filter.trim().toLowerCase();
  const dirs = useMemo(
    () => (listing?.directories ?? []).filter((d) => d.name.toLowerCase().includes(q)),
    [listing, q]
  );
  const files = useMemo(
    () => (listing?.files ?? []).filter((f) => f.name.toLowerCase().includes(q)),
    [listing, q]
  );
  const current: Dataset | null =
    datasets.find(
      (d) => d.root_id === effectiveRootId && d.relative_path === (listing?.relative_path ?? '')
    ) ?? null;
  const [choosing, setChoosing] = useState(false);
  const choose = async (dataset: Dataset) => {
    if (choosing) return;
    setChoosing(true);
    setError(null);
    try {
      if (onSelect) await onSelect(dataset);
      else selectDataset(dataset);
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : t.datasetLoadError);
    } finally { setChoosing(false); }
  };
  const doRegister = () => {
    if (!listing || busy || !effectiveRootId) return;
    const name = displayNameForPath(listing.relative_path, listing.root.label);
    setBusy('register');
    setError(null);
    registerMutation.mutate(
      {
        rootId: effectiveRootId,
        relativePath: listing.relative_path,
        displayName: name,
        onProgress: setOperation
      },
      {
        onSuccess: (dataset) => {
          setOperation(null);
          void choose(dataset);
        },
        onError: (failure) => {
          setOperation(null);
          setError(failure instanceof Error ? failure.message : t.datasetRegisterError);
        },
        onSettled: () => setBusy(null)
      }
    );
  };
  const doInspect = (dataset: Dataset) => {
    if (busy) return;
    setBusy('inspect');
    setError(null);
    inspectMutation.mutate(
      { datasetId: dataset.dataset_id, onProgress: setOperation },
      {
        onSuccess: (fresh) => {
          setOperation(null);
          void choose(fresh);
        },
        onError: (failure) => {
          setOperation(null);
          setError(failure instanceof Error ? failure.message : t.datasetInspectError);
        },
        onSettled: () => setBusy(null)
      }
    );
  };
  const doCancel = () => {
    if (!operation) return;
    cancelOperation(operation.operation_id).catch(() => setError(t.datasetCancelFailed));
  };
  const refreshAll = () => {
    setError(null);
    void rootsQuery.refetch();
    void datasetsQuery.refetch();
    if (effectiveRootId) void listingQuery.refetch();
    if (selectedDatasetId) void selectedDatasetQuery.refetch();
  };
  const goDraft = () => {
    setError(null);
    if (busy || choosing) return;
    const next = resolveDatasetLocation(roots, pathDraft, effectiveRootId);
    if (!next) { setError(t.datasetPathOutsideRoots); return; }
    setLocation(next);
    setPathDraft(null);
    setFilter('');
  };
  const openDir = (next: string) => {
    if (busy || !effectiveRootId) return;
    setError(null);
    setLocation({ rootId: effectiveRootId, relativePath: next });
    setPathDraft(null);
    setFilter('');
  };
  const registeredList = (
    <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 pb-4">
      <h3 className="px-1 pt-1 text-xs font-semibold">{t.datasetRegisteredTitle}</h3>
      <p className="px-1 text-meta text-subtle">{t.datasetRegisteredSubtitle}</p>
      {datasetsQuery.isPending ? (
        <p className="px-1 py-4 text-xs text-subtle">{t.datasetLoading}</p>
      ) : datasetsQuery.isError ? (
        <div className="py-2 text-center">
          <p className="text-xs text-danger">{t.datasetLoadError}</p>
          <button
            type="button"
            onClick={() => void datasetsQuery.refetch()}
            className="mt-2 rounded-control border px-2.5 py-1.5 text-xs"
          >
            {t.retry}
          </button>
        </div>
      ) : datasets.length === 0 ? (
        <p className="rounded-card border border-dashed px-3 py-6 text-center text-xs text-subtle">
          {t.datasetNoRegistered}
        </p>
      ) : (
        datasets.map((dataset) => {
          const active = dataset.dataset_id === (current?.dataset_id ?? selectedDatasetId);
          return (
            <button
              key={dataset.dataset_id}
              type="button"
              disabled={busy !== null || choosing}
              onClick={() => choose(dataset)}
              className="w-full rounded-card border p-3 text-left"
            >
              <span className="block truncate text-sm font-medium">
                {dataset.display_name}
                {active ? ' ✓' : ''}
              </span>
              <span className="mt-1 block truncate font-mono text-xs text-subtle">
                {dataset.relative_path || dataset.absolute_path}
              </span>
              <span className="mt-2 flex flex-wrap gap-1">
                {dataset.inspection.is_bids && (
                  <span className="rounded border px-1.5 py-0.5 text-micro">BIDS</span>
                )}
                {dataset.inspection.has_dwi && (
                  <span className="rounded border px-1.5 py-0.5 text-micro">DWI</span>
                )}
                {dataset.inspection.has_t1w && (
                  <span className="rounded border px-1.5 py-0.5 text-micro">T1w</span>
                )}
                {dataset.inspection.has_fod && (
                  <span className="rounded border px-1.5 py-0.5 text-micro">FOD</span>
                )}
                {dataset.inspection.has_five_tt && (
                  <span className="rounded border px-1.5 py-0.5 text-micro">5TT</span>
                )}
                <span className="rounded border px-1.5 py-0.5 text-micro">
                  {t.datasetSamples.replace('{{count}}', String(dataset.inspection.sample_count))}
                </span>
                <span className="rounded border px-1.5 py-0.5 text-micro">
                  {t.datasetFilesSize
                    .replace('{{count}}', String(dataset.inspection.file_count))
                    .replace('{{size}}', formatBytes(dataset.inspection.total_bytes))}
                </span>
              </span>
              {dataset.inspection.warnings.length > 0 && (
                <span className="mt-2 block text-xs text-warning">
                  {dataset.inspection.warnings[0]}
                </span>
              )}
            </button>
          );
        })
      )}
    </div>
  );
  const browser = (
    <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-2 px-5 pb-2 pt-4">
        <label className="text-xs text-muted" htmlFor="dataset-root">
          {t.datasetRootsLabel}
        </label>
        <select
          id="dataset-root"
          value={effectiveRootId}
          disabled={loadingRoots || busy !== null}
          onChange={(e) => {
            setLocation({ rootId: e.target.value, relativePath: '' });
            setPathDraft(null);
            setFilter('');
            setError(null);
          }}
          className="min-w-0 flex-1 rounded-control border border-line bg-raised px-2 py-1.5 text-xs "
        >
          {roots.map((r) => (
            <option key={r.root_id} value={r.root_id} disabled={!r.readable}>
              {r.label} · {r.path}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={refreshAll}
          aria-label={t.datasetRefreshDirectory}
          className="rounded-control border border-line p-1.5 text-muted "
        >
          {t.datasetRefreshDirectory}
        </button>
      </div>
      <div className="shrink-0 space-y-2 px-5 pb-2">
        <div className="relative">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t.datasetFilterPlaceholder}
            aria-label={t.datasetSearch}
            className="h-9 w-full rounded-control border border-line bg-raised pl-3 pr-3 text-xs outline-none "
          />
        </div>
        <div className="flex items-center gap-2">
          <input
            value={pathDraft}
            onChange={(e) => setPathDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') goDraft();
            }}
            aria-label={t.datasetPath}
            placeholder={t.datasetPathPlaceholder}
            className="min-w-0 flex-1 rounded-control border border-line bg-raised px-2 py-1.5 font-mono text-xs "
          />
          <button
            type="button"
            onClick={goDraft}
            disabled={busy !== null || choosing}
            className="shrink-0 rounded-chip border border-line px-2 py-1.5 text-xs "
          >
            {t.datasetGo}
          </button>
        </div>
      </div>
      <nav
        aria-label={t.datasetBreadcrumb}
        className="flex h-8 shrink-0 items-center gap-1 overflow-x-auto whitespace-nowrap px-5 py-1 text-xs text-subtle"
      >
        {(listing?.breadcrumbs ?? []).map((crumb, index) => (
          <span key={crumb.relative_path} className="flex shrink-0 items-center gap-1">
            {index > 0 && <span>/</span>}
            <button
              type="button"
              disabled={busy !== null || choosing}
              onClick={() => openDir(crumb.relative_path)}
              className="rounded px-1 py-0.5 hover:bg-hover"
            >
              {crumb.label}
            </button>
          </span>
        ))}
      </nav>
      {rootsQuery.isError || datasetsQuery.isError || (waitingForSelection && selectedDatasetQuery.isError) ? (
        <div className="px-5 py-6 text-center">
          <p className="text-xs text-danger">{t.datasetLoadError}</p>
          <button
            type="button"
            onClick={refreshAll}
            className="mt-2 rounded-control border px-2.5 py-1.5 text-xs"
          >
            {t.retry}
          </button>
        </div>
      ) : loadingRoots || waitingForSelection ? (
        <p className="px-5 py-6 text-xs text-subtle">{t.datasetLoading}</p>
      ) : listingQuery.isError ? (
        <div className="px-5 py-6 text-center">
          <p className="text-xs text-danger">{t.datasetLoadError}</p>
          <button
            type="button"
            onClick={() => void listingQuery.refetch()}
            className="mt-2 rounded-control border px-2.5 py-1.5 text-xs"
          >
            {t.retry}
          </button>
        </div>
      ) : listingBusy && !listing ? (
        <p className="px-5 py-6 text-xs text-subtle">{t.datasetLoading}</p>
      ) : listing ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-2">
          {listing.parent_relative_path !== null && listing.parent_relative_path !== undefined && (
            <button
              type="button"
              disabled={busy !== null || choosing}
              onClick={() => openDir(listing.parent_relative_path ?? '')}
              className="mb-1 flex items-center gap-2 rounded-control px-2 py-2 text-xs text-muted hover:bg-hover"
            >
              <ArrowUp className="size-4" aria-hidden="true"/>
              {t.datasetBackToParent}
            </button>
          )}
          {dirs.map((dir) => (
            <button
              key={dir.relative_path}
              type="button"
              disabled={busy !== null || !dir.readable}
              onClick={() => openDir(dir.relative_path)}
              className="group flex w-full items-center gap-3 rounded-control px-3 py-2.5 text-left text-sm hover:bg-hover disabled:opacity-40"
            >
              <Folder className="size-4 shrink-0 text-accent" aria-hidden="true"/>
              <span className="min-w-0 flex-1 truncate">{dir.name}</span>
              <ChevronRight className="size-3.5 shrink-0 text-subtle" aria-hidden="true"/>
            </button>
          ))}
          {files.map((file) => (
            <div
              key={file.relative_path}
              className="flex items-center gap-2 px-2 py-1.5 font-mono text-xs text-muted"
            >
              <File className="size-4 shrink-0 text-subtle" aria-hidden="true"/>
              <span className="min-w-0 flex-1 truncate">{file.name}</span>
              <span className="text-micro">{formatBytes(file.size_bytes)}</span>
            </div>
          ))}
          {dirs.length === 0 && files.length === 0 && (
            <p className="px-2 py-6 text-center text-xs text-subtle">
              {filter.trim() ? t.datasetNoMatch : t.datasetFolderEmpty}
            </p>
          )}
          <p className="mt-2 text-meta text-subtle">
            {t.datasetCounts.replace('{{directories}}', String(listing.directory_count))} ·{' '}
            {t.datasetFileCount.replace('{{count}}', String(listing.file_count))}
            {(listing.directories_truncated || listing.files_truncated) &&
              ` · ${t.datasetTruncated}`}
          </p>
        </div>
      ) : (
        <p className="px-5 py-6 text-xs text-subtle">{t.datasetFolderEmpty}</p>
      )}
    </section>
  );
  return (
    <dialog ref={dialogRef} className="dialog-root" aria-label={t.datasetPickerTitle}
      onClick={e=>{if(e.target===e.currentTarget)onClose();}}
      onCancel={e=>{e.preventDefault();e.stopPropagation();onClose();}}>
      <div
        ref={panelRef}
        style={panelStyle}
        onClick={(e) => e.stopPropagation()}
        className="relative flex shrink-0 flex-col overflow-hidden rounded-card border border-line bg-raised shadow-xl"
      >
        {resizeHandles}
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4 ">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">{t.datasetPickerTitle}</h2>
            <p className="mt-1 text-xs text-subtle">{t.datasetPickerSubtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t.datasetClosePicker}
            className="rounded-control p-2 text-muted hover:bg-hover"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="dataset-picker-layout grid min-h-0 flex-1 gap-0 overflow-hidden">
          {browser}
          <aside className="dataset-picker-registered flex min-h-0 min-w-0 flex-col overflow-hidden border-line">
            {registeredList}
          </aside>
        </div>
        <div className="max-h-[25%] shrink-0 space-y-2 overflow-y-auto border-t border-line px-5 py-3">
          {operation && (
            <div className="flex items-center gap-2 text-xs text-muted">
              <span className="min-w-0 flex-1 truncate">{operation.message}</span>
              <button
                type="button"
                onClick={doCancel}
                className="rounded-chip border px-2 py-1 text-meta"
              >
                {t.datasetCancelCheck}
              </button>
            </div>
          )}
          {busy === 'register' || busy === 'inspect' ? (
            <p className="text-xs text-muted">
              {busy === 'register' ? t.datasetRegistering : t.datasetInspecting}
            </p>
          ) : current ? (
            <button
              type="button"
              onClick={() => doInspect(current)}
              disabled={busy !== null || choosing || listingBusy}
              className="bg-accent-solid text-on-accent hover:bg-accent-solid-hover w-full rounded-control px-4 py-2 text-sm font-medium"
            >
              {t.datasetRescanSelect}
            </button>
          ) : (
            <button
              type="button"
              onClick={doRegister}
              disabled={busy !== null || choosing || !listing || listingBusy}
              className="bg-accent-solid text-on-accent hover:bg-accent-solid-hover w-full rounded-control px-4 py-2 text-sm font-medium disabled:opacity-40"
            >
              {t.datasetRegisterSelect}
            </button>
          )}
          {error && (
            <p role="alert" className="text-xs text-danger">
              {error}
            </p>
          )}
        </div>
      </div>
    </dialog>
  );
}
