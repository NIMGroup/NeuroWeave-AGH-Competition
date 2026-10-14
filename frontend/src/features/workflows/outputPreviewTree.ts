export type OutputEntry = { path: string; label?: string; detail?: string; directory?: boolean };
export type OutputNode = { name: string; path: string; directory: boolean; labels: string[]; details: string[]; children: OutputNode[] };

/** Only planned paths are shown. Never imply a complete view of existing files. */
export function outputPreviewTree(entries: OutputEntry[]): OutputNode[] {
  const root: OutputNode[] = [];
  for (const entry of entries) {
    const parts = entry.path.split('/');
    if (!parts.length || parts.some(p => !p || p === '..' || p === '.' || p.includes('\\'))) continue;
    let children = root;
    parts.forEach((name, index) => {
      let node = children.find(n => n.name === name);
      if (!node) {
        node = { name, path: parts.slice(0, index + 1).join('/'),
          directory: index < parts.length - 1 || Boolean(entry.directory), labels: [], details: [], children: [] };
        children.push(node);
      }
      if (index === parts.length - 1) {
        node.directory ||= Boolean(entry.directory);
        if (entry.label && !node.labels.includes(entry.label)) node.labels.push(entry.label);
        if (entry.detail && !node.details.includes(entry.detail)) node.details.push(entry.detail);
      }
      children = node.children;
    });
  }
  return root;
}
