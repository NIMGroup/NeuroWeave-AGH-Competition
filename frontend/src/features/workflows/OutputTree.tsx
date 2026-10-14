import { File, Folder } from 'lucide-react';
import { outputPreviewTree } from './outputPreviewTree';
import type { OutputEntry, OutputNode } from './outputPreviewTree';

function Branch({ nodes, depth = 0 }: { nodes: OutputNode[]; depth?: number }) {
  return <ul className={depth ? 'ml-3.5 border-l border-line pl-3' : 'space-y-1'}>
    {nodes.map(node => <li key={node.path} className="min-w-0 py-0.5">
      {node.directory ? <details open={depth < 1} className="chat-disclosure">
        <summary className="break-all font-mono text-xs"><Folder className="mr-1 inline size-3.5 text-accent" aria-hidden="true" />{node.name}/
          {node.labels.length > 0 && <span className="ml-2 font-sans text-subtle">{node.labels.join(' · ')}</span>}
        </summary>
        {node.details.length > 0 && <p className="ml-4 break-words text-micro text-subtle">{node.details.join(' · ')}</p>}
        {node.children.length > 0 && <Branch nodes={node.children} depth={depth + 1} />}
      </details> : <div className="flex min-w-0 items-start gap-1.5">
        <File className="mt-0.5 size-3 shrink-0 text-subtle" aria-hidden="true" />
        <div className="min-w-0"><span className="break-all font-mono text-xs">{node.name}</span>
          {node.labels.length > 0 && <span className="ml-2 break-words text-micro text-muted">{node.labels.join(' · ')}</span>}
          {node.details.length > 0 && <p className="break-words text-micro text-subtle">{node.details.join(' · ')}</p>}
        </div>
      </div>}
    </li>)}
  </ul>;
}

export function OutputTree({ entries }: { entries: OutputEntry[] }) {
  return <div className="max-h-80 min-w-0 overflow-auto rounded-control bg-well p-3"><Branch nodes={outputPreviewTree(entries)} /></div>;
}
