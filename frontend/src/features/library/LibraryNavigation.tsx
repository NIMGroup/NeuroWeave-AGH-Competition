import { ui } from '../../lib/libraryText';
import { useState } from 'react';
import { ChevronDown, ChevronRight, FolderTree } from 'lucide-react';
import { useLanguageStore } from '../../stores/languageStore';
import type { Facets } from './libraryApi';

/** Navigation counts come from the whole matching catalog, before pagination. */
export function LibraryNavigation({
  facets,
  category,
  software,
  onCategory,
  onSoftware
}: {
  facets?: Facets;
  category: string;
  software: string;
  onCategory: (value: string) => void;
  onSoftware: (value: string) => void;
}) {
  const zh = useLanguageStore((s) => s.language) === 'zh';
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(category.includes('/') ? [category.split('/')[0]] : [])
  );
  const [previousCategory, setPreviousCategory] = useState(category);
  // Reconcile a changed selection before rendering its children, without a second
  // post-paint effect. Manual expand/collapse remains independent of filtering.
  if (category !== previousCategory) {
    setPreviousCategory(category);
    if (category.includes('/'))
      setExpanded((previous) => new Set([...previous, category.split('/')[0]]));
  }
  const roots = facets?.categories.filter((c) => c.path.length === 1) ?? [];
  const toggle = (id: string) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <section className="min-w-0 space-y-2 rounded border border-line p-2">
      <div className="flex items-center gap-2 text-xs font-medium">
        <FolderTree size={14} />
        {zh ? ui('按处理目标查找') : 'Browse by goal'}
      </div>
      <label className="flex min-w-0 items-center gap-2 text-xs">
        <span className="shrink-0">{zh ? ui('软件') : 'Software'}</span>
        <select
          className="min-w-0 flex-1 rounded border border-line bg-raised p-1"
          value={software}
          onChange={(e) => onSoftware(e.target.value)}
        >
          <option value="">{zh ? ui('所有软件') : 'All software'}</option>
          {facets?.software.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label} ({s.count})
            </option>
          ))}
        </select>
      </label>
      {/* Categories scroll first, then hand off at either boundary to the
          enclosing sidebar/dialog. The outer viewport owns containment. */}
      <nav
        aria-label={zh ? ui('功能分类') : 'Function categories'}
        className="max-h-64 overflow-y-auto overscroll-auto text-xs"
      >
        <button
          className={
            'mb-1 w-full rounded p-1.5 text-left hover:bg-hover ' +
            (!category ? 'bg-selected font-semibold' : '')
          }
          aria-current={!category ? 'page' : undefined}
          onClick={() => onCategory('')}
        >
          {zh ? ui('全部功能') : 'All functions'}
        </button>
        {roots.map((root) => {
          const id = root.path[0];
          const children =
            facets?.categories.filter(
              (c) => c.path.length === 2 && c.path[0] === id
            ) ?? [];
          const open = expanded.has(id);
          return (
            <div key={id}>
              <div className="flex min-w-0 items-center gap-1">
                <button
                  className="shrink-0 rounded p-1 hover:bg-hover"
                  aria-label={
                    (zh ? ui('展开分类：') : 'Expand category: ') + root.label
                  }
                  aria-expanded={open}
                  onClick={() => toggle(id)}
                >
                  {open ? (
                    <ChevronDown size={14} />
                  ) : (
                    <ChevronRight size={14} />
                  )}
                </button>
                <button
                  className={
                    'flex min-w-0 flex-1 items-center justify-between gap-2 rounded p-1.5 text-left hover:bg-hover ' +
                    (category === id ? 'bg-selected font-semibold' : '')
                  }
                  aria-current={category === id ? 'page' : undefined}
                  onClick={() => onCategory(id)}
                >
                  <span className="min-w-0 break-words">{root.label}</span>
                  <span className="shrink-0 tabular-nums text-subtle">
                    {root.count}
                  </span>
                </button>
              </div>
              {open && (
                <div className="ml-5 border-l border-line pl-2">
                  {children.map((child) => {
                    const path = child.path.join('/');
                    return (
                      <button
                        key={path}
                        className={
                          'flex w-full min-w-0 items-center justify-between gap-2 rounded p-1.5 text-left hover:bg-hover ' +
                          (category === path ? 'bg-selected font-semibold' : '')
                        }
                        aria-current={category === path ? 'page' : undefined}
                        onClick={() => onCategory(path)}
                      >
                        <span className="min-w-0 break-words">
                          {child.label}
                        </span>
                        <span className="shrink-0 tabular-nums text-subtle">
                          {child.count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      {(category || software) && (
        <button
          className="text-xs text-accent underline"
          onClick={() => {
            onCategory('');
            onSoftware('');
          }}
        >
          {zh
            ? ui('清除分类和软件筛选')
            : 'Clear category and software filters'}
        </button>
      )}
    </section>
  );
}
