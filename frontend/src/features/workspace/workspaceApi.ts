import { request } from '../../api/http';
import type { Workflow } from '../workflows/workflowTypes';
import type {
  FsEntry,
  FsListing,
  FsRoots,
  TextPreview,
  TrashItem,
  Workspace,
  WorkspaceEvent,
  WorkspaceOperation
} from './workspaceTypes';

export function listWorkspaces(signal?: AbortSignal): Promise<Workspace[]> {
  return request<Workspace[]>('/api/output-directories', { signal });
}

export function deleteWorkspace(id: string): Promise<void> {
  return request<void>(`/api/output-directories/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function createWorkspace(name: string, rootPath: string): Promise<Workspace> {
  return request<Workspace>('/api/output-directories', {
    method: 'POST',
    body: JSON.stringify({ name, root_path: rootPath })
  });
}

export function editWorkspace(id: string, name: string): Promise<Workspace> {
  return request<Workspace>(`/api/output-directories/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name })
  });
}

export function setConversationWorkspace(
  conversationId: string,
  workspaceId: string | null
): Promise<void> {
  return request<void>(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ workspace_id: workspaceId })
  });
}

export function fetchFsRoots(signal?: AbortSignal): Promise<FsRoots> {
  return request<FsRoots>('/api/fs/roots', { signal });
}

export type FsListInput = {
  path: string;
  rootPath?: string;
  workspaceId?: string | null;
  datasetId?: string | null;
  sort: string;
  filter: string;
  showHidden: boolean;
  cursor?: string;
  signal?: AbortSignal;
};

export function listFs(input: FsListInput): Promise<FsListing> {
  const query = new URLSearchParams({
    path: input.path,
    sort: input.sort,
    filter: input.filter,
    show_hidden: String(input.showHidden),
    limit: '80'
  });
  if (!input.datasetId) {
    if (input.workspaceId) query.set('workspace_id', input.workspaceId);
    else if (input.rootPath) query.set('root_path', input.rootPath);
  }
  if (input.cursor) query.set('cursor', input.cursor);
  return request<FsListing>(`${input.datasetId ? '/api/datasets/' + encodeURIComponent(input.datasetId) + '/files' : '/api/fs'}/list?${query.toString()}`, { signal: input.signal });
}

export function inspectFs(
  path: string,
  workspaceId?: string | null,
  rootPath?: string,
  datasetId?: string | null,
  signal?: AbortSignal
): Promise<FsEntry & { exists: boolean }> {
  const query = new URLSearchParams({ path });
  if (workspaceId) query.set('workspace_id', workspaceId);
  else if (rootPath && !datasetId) query.set('root_path', rootPath);
  return request<FsEntry & { exists: boolean }>(`${datasetId ? '/api/datasets/' + encodeURIComponent(datasetId) + '/files' : '/api/fs'}/inspect?${query.toString()}`, { signal });
}

export function readFsText(workspaceId: string | null, path: string, offset = 0, datasetId?: string | null, signal?: AbortSignal): Promise<TextPreview> {
  const query = new URLSearchParams({ ...(workspaceId ? { workspace_id: workspaceId } : {}), path, offset: String(offset) });
  return request<TextPreview>(`${datasetId ? '/api/datasets/' + encodeURIComponent(datasetId) + '/files' : '/api/fs'}/read-text?${query.toString()}`, { signal });
}

export type FsAction = 'mkdir' | 'copy' | 'move' | 'rename' | 'delete';

export function runFsAction(
  action: FsAction,
  input: {
    workspaceId: string | null;
    conversationId: string | null;
    rootPath?: string;
    path: string;
    destination?: string | null;
    overwrite?: boolean;
    permanent?: boolean;
  }
): Promise<WorkspaceOperation> {
  return request<WorkspaceOperation>(`/api/fs/${action}`, {
    method: 'POST',
    body: JSON.stringify({
      workspace_id: input.workspaceId,
      ...(!input.workspaceId && action === 'mkdir' ? { root_path: input.rootPath } : {}),
      conversation_id: input.conversationId,
      path: input.path,
      destination: ['copy', 'move', 'rename'].includes(action) ? (input.destination ?? null) : null,
      overwrite: input.overwrite,
      permanent: input.permanent
    })
  });
}

export function requestSensitiveRead(
  workspaceId: string,
  conversationId: string | null,
  path: string
): Promise<WorkspaceOperation> {
  return request<WorkspaceOperation>('/api/fs/read-sensitive', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, conversation_id: conversationId, path })
  });
}

export function fetchTrash(workspaceId: string): Promise<TrashItem[]> {
  return request<TrashItem[]>(`/api/output-directories/${encodeURIComponent(workspaceId)}/trash`);
}

export function restoreTrashItem(
  id: string,
  conversationId: string | null,
  destination: string | null
): Promise<WorkspaceOperation> {
  return request<WorkspaceOperation>(`/api/fs/trash/${encodeURIComponent(id)}/restore`, {
    method: 'POST',
    body: JSON.stringify({ conversation_id: conversationId, destination })
  });
}

export function fetchFsOperation(id: string): Promise<WorkspaceOperation> {
  return request<WorkspaceOperation>(`/api/fs/operations/${encodeURIComponent(id)}`);
}

export function cancelFsOperation(id: string, conversationId: string | null): Promise<WorkspaceOperation> {
  return request<WorkspaceOperation>(`/api/fs/operations/${encodeURIComponent(id)}/cancel`, {
    method: 'POST', body: JSON.stringify({ confirmed: true, conversation_id: conversationId })
  });
}

export function confirmFsOperation(
  id: string,
  conversationId: string | null
): Promise<WorkspaceOperation> {
  return request<WorkspaceOperation>(`/api/fs/operations/${encodeURIComponent(id)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ confirmed: true, conversation_id: conversationId })
  });
}

export function fetchWorkspaceToolEvents(
  turnId: string,
  signal?: AbortSignal
): Promise<WorkspaceEvent[]> {
  return request<WorkspaceEvent[]>(
    `/api/workspace-tool-events?turn_id=${encodeURIComponent(turnId)}`,
    { signal }
  );
}

export function saveWorkflowOutput(
  workflowId: string,
  relativePath: string,
  expectedRevision: number,
  binding?: { workspaceId: string | null; conversationId: string | null }
): Promise<Workflow> {
  return request<Workflow>(`/api/workflows/${encodeURIComponent(workflowId)}/output`, {
    method: 'PATCH',
    body: JSON.stringify({ expected_revision: expectedRevision, relative_path: relativePath,
      ...(binding ? { workspace_id: binding.workspaceId, conversation_id: binding.conversationId } : {}) })
  });
}
