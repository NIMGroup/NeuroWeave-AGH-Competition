import type { Workspace } from './workspaceTypes';

function normalized(path: string) {return path.replace(/\\/g, '/').replace(/\/+$/, '') || '/';}
/** Match path components, so /data does not accidentally include /database. */
export function browseRootForPath(path: string, roots: string[]): string | null {
  const target = normalized(path);
  return [...roots].sort((a,b)=>a.length-b.length).find(root => {
    const value = normalized(root);
    return value==='/' || target===value || target.startsWith(value+'/');
  }) ?? null;
}
export function workspaceForPath(workspaces: Workspace[], path: string) {
  return workspaces.find(workspace => workspace.status==='active' && workspace.manageable
    && normalized(workspace.root_path)===normalized(path));
}
