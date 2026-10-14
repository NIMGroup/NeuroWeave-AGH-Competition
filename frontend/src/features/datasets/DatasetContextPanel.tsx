import { useMemo, useState } from 'react';
import { errorText } from '../../api/http';
import { Link2, Maximize2, ScanSearch } from 'lucide-react';
import { getTranslations } from '../../lib/i18n';
import { useLanguageStore } from '../../stores/languageStore';
import { useDatasetStore } from '../../stores/datasetStore';
import { useRegisteredDatasets, useSelectedDataset } from './useDatasets';
import type { Dataset } from './datasetsApi';
import { Button } from '../../ui/Button';

type DatasetContextProps = {
  /** Opens the full Datasets surface (navbar/main-workspace behaviour). */
  onOpenFull: () => void;
  /** Choose a dataset without leaving the sidebar. */
  onChoose: (dataset: Dataset) => Promise<void>;
  disabled?: boolean;
  notice?: string | null;
  onBrowse?: () => void;
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

/**
 * Compact contextual browser for the Datasets rail icon.
 *
 * Read-only mirror of the shared dataset hooks: selecting a row updates the
 * same `datasetStore` the full `DatasetPicker` writes to, so there is exactly
 * one selection state and no duplicate fetching logic.
 */
export function DatasetContextPanel({ onOpenFull, onChoose, disabled, notice, onBrowse }: DatasetContextProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const selectedDatasetId = useDatasetStore((s) => s.selectedDatasetId);
  const [error, setError] = useState('');
  const datasetsQuery = useRegisteredDatasets();
  const selectedQuery = useSelectedDataset(selectedDatasetId);
  const datasets = useMemo(() => datasetsQuery.data?.datasets ?? [], [datasetsQuery.data]);
  const selected = selectedQuery.data ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-4 py-3">
      {notice && <p className="text-meta text-muted">{notice}</p>}
      {error && <p role="alert" className="text-meta text-danger">{error}</p>}
      {selected && onBrowse && <Button size="sm" onClick={onBrowse}>{t.datasetBrowseFiles}</Button>}
      {selected && (
        <section aria-label={t.datasetSelectedDatasets} className="rounded-card border border-line bg-raised px-3 py-2">
          <p className="truncate text-body font-medium text-ink">{selected.display_name}</p>
          <p className="mt-0.5 truncate text-meta text-muted">
            {t.datasetSamples.replace('{{count}}', String(selected.inspection.sample_count))}
            {' · '}
            {formatBytes(selected.inspection.total_bytes)}
          </p>
        </section>
      )}

      {datasetsQuery.isPending && <p className="text-meta text-muted">{t.datasetLoading}</p>}
      {datasetsQuery.isError && (
        <div>
          <p role="alert" className="text-meta text-danger">{t.datasetLoadError}</p>
          <Button size="sm" className="mt-2" onClick={() => void datasetsQuery.refetch()}>
            {t.retry}
          </Button>
        </div>
      )}

      {!datasetsQuery.isPending && !datasetsQuery.isError && (
        <ul className="min-w-0 space-y-1">
          {datasets.length === 0 && <li className="text-meta text-muted">{t.datasetNoRegistered}</li>}
          {datasets.map((dataset) => {
            const active = dataset.dataset_id === selectedDatasetId;
            return (
              <li key={dataset.dataset_id} className="min-w-0">
                <button
                  type="button"
                  aria-current={active || undefined}
                  disabled={disabled}
                  onClick={() => {
                    setError('');
                    void onChoose(dataset).catch(error => setError(errorText(error, t.datasetLoadError)));
                  }}
                  className={
                    active
                      ? 'flex w-full min-w-0 items-center gap-2 rounded-control bg-selected px-2.5 py-2 text-left transition-control focus-ring-inset'
                      : 'flex w-full min-w-0 items-center gap-2 rounded-control px-2.5 py-2 text-left text-ink transition-control hover:bg-hover focus-ring-inset'
                  }
                >
                  <Link2 aria-hidden="true" className="size-4 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body font-medium">{dataset.display_name}</span>
                    <span className="block truncate text-meta text-subtle">{dataset.relative_path}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="mt-auto flex shrink-0 items-center gap-2 border-t border-line pt-2.5">
        <Button size="sm" disabled={disabled} onClick={onOpenFull}
          title={`${t.openFullPanel}: ${t.datasetPickerTitle}`}
          className="min-w-0 flex-1">
          <ScanSearch aria-hidden="true" className="size-4 shrink-0" />
          <span className="truncate">{t.datasetPickerTitle}</span>
          <Maximize2 aria-hidden="true" className="ml-auto size-3.5 shrink-0 text-subtle" />
        </Button>
      </div>
    </div>
  );
}
