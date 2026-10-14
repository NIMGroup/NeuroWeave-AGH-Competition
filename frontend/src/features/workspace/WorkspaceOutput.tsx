import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { adoptConflict } from '../workflows/useWorkflows';
import { errorText } from '../../api/http';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { Workflow } from '../workflows/workflowTypes';
import { saveWorkflowOutput } from './workspaceApi';
import type { Workspace, FsEntry } from './workspaceTypes';
import { ServerPath } from './ServerPath';
import { WorkspacePanel } from './WorkspacePanel';

export function WorkspaceOutput({
  workflow,
  onChange,
  disabled
}: {
  workflow: Workflow;
  onChange: (workflow: Workflow) => void;
  disabled?: boolean;
}) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [editing, setEditing] = useState(false);
  const [path, setPath] = useState(workflow.output_relative_path ?? 'outputs');
  const [picker, setPicker] = useState(false);
  const [selectingRoot, setSelectingRoot] = useState(false);
  const [error, setError] = useState('');
  const client = useQueryClient();
  const save = useMutation({
    mutationKey: ['workflow-change', workflow.workflow_id, 'output'],
    mutationFn: () => saveWorkflowOutput(workflow.workflow_id, path, workflow.revision),
    onSuccess: value => { onChange(value); setEditing(false); },
    onError: error => { adoptConflict(client, error); setError(errorText(error, t.workflowErrorGeneric)); },
  });
  const bind = useMutation({
    mutationKey: ['workflow-change', workflow.workflow_id, 'output-root'],
    mutationFn: (workspace: Workspace | null) => saveWorkflowOutput(
      workflow.workflow_id, '.', workflow.revision,
      { workspaceId: workspace?.id ?? null, conversationId: null }
    ),
    onSuccess: value => { onChange(value); setEditing(false); },
    onError: error => { adoptConflict(client, error); setError(errorText(error, t.workflowErrorGeneric)); },
  });
  const busy = save.isPending || bind.isPending;
  const hasOutput = !!workflow.workspace_id && !!workflow.workspace_root_path;
  const locked = disabled || busy || workflow.status === 'confirmed';
  return (
    <div className="mt-2 min-w-0 rounded border border-accent-line bg-accent-quiet p-2 text-xs dark:border-accent-line ">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span>{t.workspaceOutputTitle}</span>
        <button type="button" className="rounded-control border border-line px-2 py-1"
          disabled={locked} onClick={() => setSelectingRoot(true)}>
          {t.workspaceSelect}
        </button>
        {hasOutput && <button
          type="button"
          className="rounded-control border border-line px-2 py-1 "
          disabled={locked}
          onClick={() => {
            setPath(workflow.output_relative_path ?? 'outputs');
            setEditing((v) => !v);
          }}
        >
          {t.workspaceOutputAdjust}
        </button>}
      </div>
      {hasOutput ? <ServerPath
        path={`${workflow.workspace_root_path}/${workflow.output_relative_path ?? 'outputs'}`}
        label={`${workflow.output_relative_path ?? 'outputs'}/<run_id>`}
      /> : <p className="mt-2 text-warning">{t.workspaceOutputRequired}</p>}
      {workflow.status === 'confirmed' && <p className="mt-2 text-subtle">{t.workspaceLockedHint}</p>}
      {selectingRoot && <WorkspacePanel selectionMode
        workspaceId={workflow.workspace_id ?? null}
        conversationId={null}
        locked={locked}
        onSelect={async workspace => { await bind.mutateAsync(workspace); }}
        onClose={() => setSelectingRoot(false)}
      />}
      {editing && hasOutput && (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (disabled || workflow.status === 'confirmed' || busy) return;
            setError('');
            save.mutate();
          }}
        >
          <input
            aria-label={t.workspaceOutputAria}
            className="w-full min-w-0 rounded border border-line bg-raised p-2 "
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-control border border-line px-2.5 py-1.5 text-xs "
              onClick={() => setPicker(true)}
            >
              {t.workspaceOutputBrowse}
            </button>
            <button
              type="submit"
              disabled={busy || disabled}
              className="rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent hover:bg-accent-solid-hover disabled:opacity-50"
            >
              {t.workspaceOutputSave}
            </button>
          </div>
        </form>
      )}
      {error && (
        <p role="alert" className="break-words text-danger">
          {error}
        </p>
      )}
      {picker && (
        <WorkspacePanel
          workspaceId={workflow.workspace_id ?? null}
          conversationId={null}
          onSelect={async () => undefined}
          onClose={() => setPicker(false)}
          onPick={(entry: FsEntry) => {
            if (entry.type !== 'directory') {
              setError(t.workspacePickMustBeFolder);
              return;
            }
            setPath(entry.relative_path);
            setPicker(false);
          }}
        />
      )}
    </div>
  );
}
