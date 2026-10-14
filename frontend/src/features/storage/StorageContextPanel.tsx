import { useQuery } from '@tanstack/react-query';
import { HardDrive, Maximize2 } from 'lucide-react';
import { getTranslations } from '../../lib/i18n';
import { useLanguageStore } from '../../stores/languageStore';
import { fetchStorageStatus } from './storageApi';
import { Button } from '../../ui/Button';

type StorageContextProps = {
  onOpenFull: () => void;
};

function formatSize(bytes: number) {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

/**
 * Compact contextual summary for the Storage rail icon.
 *
 * Reads the same `storage` query as the full `StoragePanel` (shared query
 * key, so no duplicate fetch) and links out to the full surface for the
 * inventory actions.
 */
export function StorageContextPanel({ onOpenFull }: StorageContextProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const statusQuery = useQuery({ queryKey: ['storage'], queryFn: fetchStorageStatus });
  const status = statusQuery.data;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-4 py-3">
      {statusQuery.isPending && <p className="text-meta text-muted">{t.loadingStorage}</p>}
      {statusQuery.isError && (
        <div>
          <p role="alert" className="text-meta text-danger">{t.storageLoadError}</p>
          <Button size="sm" className="mt-2" onClick={() => void statusQuery.refetch()}>
            {t.retry}
          </Button>
        </div>
      )}
      {status && (
        <ul className="min-w-0 space-y-1">
          {status.volumes.length === 0 && <li className="text-meta text-muted">{t.storageNoVolumes}</li>}
          {status.volumes.map((volume) => (
            <li
              key={volume.path}
              className="flex min-w-0 items-center gap-2 rounded-control px-2.5 py-2"
            >
              <HardDrive aria-hidden="true" className="size-4 shrink-0 text-muted" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body font-medium text-ink">{volume.path}</span>
                <span className={`block truncate text-meta ${volume.low_space ? 'text-danger' : 'text-muted'}`}>
                  {t.storageVolumeFree
                    .replace('{{free}}', formatSize(volume.free_bytes))
                    .replace('{{total}}', formatSize(volume.total_bytes))}
                  {volume.low_space && t.storageLowSpace}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-auto border-t border-line pt-2.5">
        <Button size="sm" onClick={onOpenFull} className="w-full"
          title={`${t.openFullPanel}: ${t.storageTitle}`}>
          <Maximize2 aria-hidden="true" className="size-4 shrink-0 text-subtle" />
          <span className="truncate">{t.storageTitle}</span>
        </Button>
      </div>
    </div>
  );
}
