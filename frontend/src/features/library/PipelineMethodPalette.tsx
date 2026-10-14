import { useState } from 'react';
import { errorText } from '../../api/http';
import { MethodDetailsCard } from './MethodDetailsCard';
import { ui } from '../../lib/libraryText';
import type { Page, Entry } from './libraryApi';
import { button, field } from './pipelineEditorStyles';

type Props = {
  page?: Page;
  error: unknown;
  /** First load of the catalog, before any page has arrived. */
  loading?: boolean;
  q: string;
  cursor: number;
  busy: boolean;
  nodeCount: number;
  onSearch: (value: string) => void;
  onPage: (cursor: number) => void;
  onAdd: (id: string) => void;
};
/** Browse only the current page. Dragging communicates a registered method ID. */
export function PipelineMethodPalette({
  page,
  error,
  loading = false,
  q,
  cursor,
  busy,
  nodeCount,
  onSearch,
  onPage,
  onAdd
}: Props) {
  const [viewedMethod, setViewedMethod] = useState<Entry | null>(null);
  const groups = new Map<string, Entry[]>();
  for (const m of page?.items ?? []) {
    const category = m.category.join(' / ');
    groups.set(category, [...(groups.get(category) ?? []), m]);
  }
  return (
    <aside
      aria-label={ui('方法目录')}
      className="relative isolate min-h-0 h-full overflow-auto overscroll-contain rounded border border-line bg-raised p-2 [scrollbar-gutter:stable]"
    >
      <input
        aria-label={ui('查找 Method')}
        placeholder={ui('搜索方法或软件')}
        className={field}
        value={q}
        onChange={(e) => {
          onSearch(e.target.value);
        }}
      />
      <p className="my-2 text-xs text-subtle">
        {ui('点击方法查看详情；拖到画布或点击添加节点')}
      </p>
      {!!error && <p role="alert">{errorText(error, ui('读取方法目录失败'))}</p>}
      {loading && <p role="status" className="text-xs text-subtle">{ui('正在读取方法目录…')}</p>}
      {[...groups].map(([group, entries]) => (
        <details key={group} open className="my-2">
          {/* The details element bounds sticky positioning so the next category pushes this heading away. */}
          <summary className="sticky top-0 z-10 cursor-pointer break-words rounded border-b border-line bg-raised px-1 py-2 text-xs font-medium shadow-sm focus-visible:outline-2 focus-visible:outline-accent">
            {group}
          </summary>
          {entries.map((m) => (
            <div
              key={m.id}
              draggable={!busy}
              onDragStart={(e) => {
                e.dataTransfer.setData('application/x-neuroweave-method', m.id);
                e.dataTransfer.effectAllowed = 'copy';
              }}
              className="my-2 cursor-grab rounded border border-line bg-raised p-2 text-xs"
            >
              <button
                type="button"
                aria-label={`${ui('查看详情')}: ${m.label}`}
                aria-haspopup="dialog"
                className="block w-full rounded text-left hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
                onClick={() => setViewedMethod(m)}
              >
                <strong className="block break-words">{m.label}</strong>
                <span
                  className="my-1 line-clamp-2 break-words text-subtle"
                  title={m.description}
                >
                  {m.description}
                </span>
                <span className="mb-2 block text-subtle">{ui('查看详情')}</span>
              </button>
              <button
                className={button}
                disabled={busy || nodeCount >= 50}
                onClick={() => onAdd(m.id)}
              >
                {ui('添加节点')}
              </button>
            </div>
          ))}
        </details>
      ))}
      <div className="flex justify-between gap-1">
        <button
          className={button}
          disabled={!cursor}
          onClick={() => onPage(Math.max(0, cursor - 100))}
        >
          {ui('上一页')}
        </button>
        <button
          className={button}
          disabled={!page?.has_more}
          onClick={() => onPage(page?.next_cursor ?? 0)}
        >
          {ui('下一页')}
        </button>
      </div>
      {viewedMethod && (
        <MethodDetailsCard
          entry={viewedMethod}
          canAdd={!busy && nodeCount < 50}
          onAdd={onAdd}
          onClose={() => setViewedMethod(null)}
        />
      )}
    </aside>
  );
}
