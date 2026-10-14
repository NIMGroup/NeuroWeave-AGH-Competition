import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLanguageStore } from '../../stores/languageStore';
import { Badge } from '../../ui/Badge';
import { Button } from '../../ui/Button';
import { Dialog } from '../../ui/Dialog';
import { getTranslations } from '../../lib/i18n';
import { cancelOperation } from '../../api/operations';
import { fetchStorageStatus, inspectStorage } from './storageApi';
import type { StorageInventory } from './storageApi';

const formatSize = (bytes: number) =>
  bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
    : `${(bytes / 1024 ** 2).toFixed(1)} MB`;

export function StoragePanel({ onClose }: { onClose: () => void }) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const client = useQueryClient();
  const statusQuery = useQuery({ queryKey: ['storage'], queryFn: fetchStorageStatus });
  const status = statusQuery.data;
  const inventoryQuery = useQuery<StorageInventory>({
    queryKey: ['storage-inventory'],
    enabled: false
  });
  const inventory = inventoryQuery.data;
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [operationId, setOperationId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const frontendRevision =
    (import.meta.env.VITE_NEUROWEAVE_REVISION as string | undefined) ?? t.storageDevelopment;

  const inspection = useMutation({
    mutationFn: () =>
      inspectStorage({
        onProgress: (operation) => {
          setProgress(operation.message);
          setOperationId(operation.operation_id);
          if (operation.status === 'cancel_requested') setCancelling(true);
        }
      }),
    onMutate: () => {
      setError('');
      setCancelling(false);
    },
    onSuccess: (value) => client.setQueryData(['storage-inventory'], value),
    onError: (failure) => setError(failure.message || t.storageInspectFailed),
    onSettled: () => {
      setProgress('');
      setOperationId(null);
      setCancelling(false);
    }
  });
  const busy = inspection.isPending;
  const refresh = () => {
    setError('');
    void statusQuery.refetch();
  };
  const inspect = () => inspection.mutate();
  const cancelInspect = () => {
    if (!operationId) return;
    setCancelling(true);
    void cancelOperation(operationId).catch(() => {
      setCancelling(false);
      setError(t.storageCancelFailed);
    });
  };

  return (
    <Dialog
      onClose={onClose}
      title={t.storageTitle}
      description={t.storageDescription}
      closeLabel={t.close}
      size="lg"
      footer={
        <>
          <Button onClick={() => void refresh()}>{t.storageRefresh}</Button>
          <Button variant="primary" loading={busy} onClick={() => void inspect()}>
            {busy ? t.storageInspecting : t.storageInspect}
          </Button>
          {operationId && (
            <Button variant="danger-ghost" disabled={cancelling} onClick={cancelInspect}>
              {t.storageCancelInspect}
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-5">
        {statusQuery.isPending && <p className="text-meta text-muted">{t.loadingStorage}</p>}
        {statusQuery.error && (
          <p role="alert" className="text-meta text-danger">
            {statusQuery.error.message}
          </p>
        )}
        {status && (
          <>
            <div className="space-y-1.5 text-meta">
              <p className="text-ink">
                {t.storageDatabaseLine
                  .replace('{{size}}', formatSize(status.database_bytes))
                  .replace('{{count}}', String(status.backup_count))}
              </p>
              <p className="text-ink">
                {status.latest_backup?.created_at
                  ? (status.latest_backup.restore_verified
                      ? t.storageLatestBackup
                      : t.storageLatestBackupUnverified
                    ).replace(
                      '{{time}}',
                      new Date(status.latest_backup.created_at).toLocaleString()
                    )
                  : t.storageNoBackup}
              </p>
              <p className="text-muted">{status.retention}</p>
            </div>

            {status.latest_backup?.error && (
              <p role="alert" className="text-meta text-danger">
                {status.latest_backup.error}
              </p>
            )}

            <section className="space-y-2">
              <h3 className="text-title">{t.storageVolumes}</h3>
              {!status.volumes.length && (
                <p className="text-meta text-muted">{t.storageNoVolumes}</p>
              )}
              {status.volumes.map((volume) => (
                <div
                  key={volume.path}
                  className="min-w-0 rounded-card border border-line px-3 py-2"
                >
                  <p className="break-all text-code text-ink">{volume.path}</p>
                  <p
                    className={`mt-1 text-meta ${volume.low_space ? 'text-danger' : 'text-muted'}`}
                  >
                    {t.storageVolumeFree
                      .replace('{{free}}', formatSize(volume.free_bytes))
                      .replace('{{total}}', formatSize(volume.total_bytes))}
                    {volume.low_space && t.storageLowSpace}
                  </p>
                </div>
              ))}
            </section>

            <div className="space-y-0.5 text-code text-subtle">
              <p>{t.storageFrontendRevision.replace('{{revision}}', frontendRevision)}</p>
              <p>
                {t.storageBackendRevision.replace(
                  '{{revision}}',
                  status.backend_revision ?? t.storageUnknown
                )}
              </p>
            </div>
          </>
        )}
        {progress && (
          <p role="status" className="text-meta text-muted">
            {progress}
          </p>
        )}
        {error && (
          <p role="alert" className="text-meta text-danger">
            {error}
          </p>
        )}
        {inventory && (
          <section className="space-y-2">
            <h3 className="text-title">{t.storageInventory}</h3>
            <p className="text-meta text-muted">
              {t.storageInventorySummary
                .replace('{{runs}}', String(inventory.run_count))
                .replace('{{size}}', formatSize(inventory.totals.bytes))
                .replace('{{files}}', String(inventory.totals.files))}
            </p>
            {(inventory.totals.unreadable > 0 || inventory.totals.symlinks_skipped > 0) && (
              <p className="text-meta text-warning">
                {t.storageInventorySkipped
                  .replace('{{unreadable}}', String(inventory.totals.unreadable))
                  .replace('{{symlinks}}', String(inventory.totals.symlinks_skipped))}
              </p>
            )}
            <div className="max-h-64 overflow-y-auto rounded-card border border-line">
              {!inventory.largest_runs.length && (
                <p className="px-3 py-3 text-meta text-muted">{t.runsEmpty}</p>
              )}
              {inventory.largest_runs.map((run) => (
                <div
                  key={run.run_id}
                  className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-line px-3 py-2 text-meta last:border-b-0"
                >
                  <span className="min-w-0 break-all text-code text-ink">{run.run_id}</span>
                  <span className="flex shrink-0 items-center gap-2">
                    <Badge tone="neutral">{run.status}</Badge>
                    <span className="text-muted">{formatSize(run.bytes)}</span>
                    <span aria-hidden="true" className="text-line-strong">
                      ·
                    </span>
                    <span className="text-muted">
                      {t.storageFiles.replace('{{count}}', String(run.files))}
                    </span>
                  </span>
                </div>
              ))}
            </div>
            <p className="text-meta text-subtle">{t.storageInventoryTop}</p>
          </section>
        )}
      </div>
    </Dialog>
  );
}
