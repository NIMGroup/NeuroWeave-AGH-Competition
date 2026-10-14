import { useEffect, useState } from 'react';
import { FolderOpen, Maximize2 } from 'lucide-react';
import { getTranslations } from '../../lib/i18n';
import { useLanguageStore } from '../../stores/languageStore';
import { listWorkspaces } from './workspaceApi';
import type { Workspace } from './workspaceTypes';
import { Button } from '../../ui/Button';

type WorkspaceContextProps = {
  workspaceId: string | null;
  onOpenFull: () => void;
};

/**
 * Compact contextual summary for the Workspace rail icon.
 *
 * Lists registered workspaces through the existing workspace API and marks
 * the active one; opening/browsing stays in the full `WorkspacePanel` surface
 * so there is exactly one workspace browser implementation.
 */
export function WorkspaceContextPanel({ workspaceId, onOpenFull }: WorkspaceContextProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const value = await listWorkspaces();
        if (!cancelled) {
          setWorkspaces(value);
          setError('');
        }
      } catch (failure: unknown) {
        if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-4 py-3">
      {loading && <p className="text-meta text-muted">{t.workspaceLoadingDir}</p>}
      {error && <p role="alert" className="text-meta text-danger">{error}</p>}
      {!loading && !error && (
        <ul className="min-w-0 space-y-1">
          {workspaces.length === 0 && <li className="text-meta text-muted">{t.workspaceSelect}</li>}
          {workspaces.map((workspace) => {
            const active = workspace.id === workspaceId;
            return (
              <li key={workspace.id} className="min-w-0">
                <button
                  type="button"
                  onClick={onOpenFull}
                  aria-current={active || undefined}
                  className={
                    active
                      ? 'flex w-full min-w-0 items-center gap-2 rounded-control bg-selected px-2.5 py-2 text-left transition-control focus-ring-inset'
                      : 'flex w-full min-w-0 items-center gap-2 rounded-control px-2.5 py-2 text-left text-ink transition-control hover:bg-hover focus-ring-inset'
                  }
                >
                  <FolderOpen aria-hidden="true" className="size-4 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body font-medium">{workspace.name}</span>
                    <span className="block truncate text-meta text-subtle">{workspace.root_path}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-auto border-t border-line pt-2.5">
        <Button size="sm" onClick={onOpenFull} className="w-full"
          title={`${t.openFullPanel}: ${t.workspaceTitle}`}>
          <Maximize2 aria-hidden="true" className="size-4 shrink-0 text-subtle" />
          <span className="truncate">{t.workspaceTitle}</span>
        </Button>
      </div>
    </div>
  );
}
