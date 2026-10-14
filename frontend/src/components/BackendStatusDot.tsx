import { useBackendStatus } from '../features/system/useBackendStatus';
import type { TranslationKeys } from '../types/i18n';

type BackendStatusDotProps = {
  t: TranslationKeys;
};

/**
 * Genuine product purpose: show whether the shell can reach the backend.
 * Queries `GET /api/health?include_runtime=false` through the shared client.
 */
export function BackendStatusDot({ t }: BackendStatusDotProps) {
  const status = useBackendStatus();
  const online = status.data?.status === 'ok';
  const label = status.isPending ? t.backendNotice : online ? t.backendOnline : t.backendOffline;
  const model = online && status.data?.mode === 'model' ? status.data.model : null;
  const tone = status.isPending ? 'bg-line-strong' : online ? 'bg-success' : 'bg-danger';

  return (
    <span
      className="flex min-w-0 items-center gap-1.5"
      title={model ? `${label} · ${model}` : label}
    >
      {/* role="img" + aria-label is what actually exposes a bare indicator to AT. */}
      <span role="img" aria-label={label} className={`size-2 shrink-0 rounded-full ${tone}`} />
      {model && (
        <span className="max-w-32 truncate text-meta text-muted" title={model}>
          {model}
        </span>
      )}
    </span>
  );
}
