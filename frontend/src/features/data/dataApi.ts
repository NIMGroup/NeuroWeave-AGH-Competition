import { request } from '../../api/http';
import type { DataOperation, DataOperationFiles } from './dataTypes';

export type DataSession = { required: boolean; authenticated: boolean; actor: string | null; inplace_available: boolean };
export const getSession = () => request<DataSession>('/api/auth/session');
export const login = (username: string, password: string) => request('/api/auth/login', {
  method: 'POST', body: JSON.stringify({ username, password })
});
export const logout = () => request('/api/auth/logout', { method: 'POST' });
type OperationKey = Pick<DataOperation, 'id' | 'conversation_id'>;
const path = (task: OperationKey) => '/api/data-operations/' + encodeURIComponent(task.id);
const query = (task: OperationKey) => '?conversation_id=' + encodeURIComponent(task.conversation_id);
export const getDataOperation = (task: OperationKey) => request<DataOperation>(path(task) + query(task));
export const getDataProgress = (task: OperationKey) => request<Omit<DataOperation, 'items'>>(path(task) + '/progress' + query(task));
export const cancelDataOperation = (task: DataOperation) => request<DataOperation>(path(task) + '/cancel' + query(task), { method: 'POST' });
export const runDataScript = (task: DataOperation) => {
  if (!task.script_plan_hash) throw new Error('Missing script proposal hash');
  return request<DataOperation>(path(task) + '/run-script', { method: 'POST', body: JSON.stringify({
    confirmed: true, conversation_id: task.conversation_id, script_plan_hash: task.script_plan_hash
  }) });
};
export const getDataManifest = (task: DataOperation) => request<{ items: unknown[] }>(path(task) + '/manifest' + query(task));
export const getDataOperationFiles = (task: DataOperation, offset = 0, download = false) =>
  request<DataOperationFiles>(path(task) + '/files' + query(task) + '&kind=unmatched&offset=' + offset + '&limit=50&download=' + download);
export const confirmDataOperation = (task: DataOperation, inplace: boolean, deletion: boolean, undo = false, overwrite = false) =>
  request<DataOperation>(path(task) + (undo ? '/undo' : '/confirm'), { method: 'POST', body: JSON.stringify({
    confirmed: true, conversation_id: task.conversation_id, manifest_hash: task.manifest_hash,
    allow_inplace: inplace, allow_delete: deletion, allow_overwrite: overwrite
  }) });
