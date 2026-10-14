import type { DataRoot } from './datasetsApi';

export function datasetAbsolutePath(rootPath: string, relativePath: string) {
  return rootPath.replace(/[\\/]+$/, '') + '/' + relativePath.replace(/^[\\/]+/, '');
}

/** Accept pasted absolute paths while keeping the API's root + relative contract. */
export function resolveDatasetLocation(roots: DataRoot[], input: string, currentRootId: string) {
  const path = input.trim().replace(/\\/g, '/').replace(/\/{2,}/g, '/').replace(/\/$/, '');
  if (path.split('/').includes('..')) return null;
  const absolute = input.trim().startsWith('/') || /^[A-Za-z]:\//.test(path);
  if (!absolute) {
    return roots.some(root => root.root_id === currentRootId)
      ? { rootId: currentRootId, relativePath: path.replace(/^\.\//, '') } : null;
  }
  const match = roots.map(root => ({ root, base: root.path.replace(/\\/g, '/').replace(/\/$/, '') }))
    .filter(({ root, base }) => {
      if (!root.readable) return false;
      const windows = /^[A-Za-z]:/.test(base);
      const candidate = windows ? path.toLowerCase() : path;
      const parent = windows ? base.toLowerCase() : base;
      return candidate === parent || candidate.startsWith(parent + '/');
    }).sort((a, b) => b.base.length - a.base.length)[0];
  return match ? { rootId: match.root.root_id, relativePath: path.slice(match.base.length).replace(/^\//, '') } : null;
}
