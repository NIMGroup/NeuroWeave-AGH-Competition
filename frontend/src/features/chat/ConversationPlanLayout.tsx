import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { PanelRightClose, Workflow, X } from 'lucide-react';
import { IconButton } from '../../ui/IconButton';
import { useLanguageStore } from '../../stores/languageStore';

/** One mounted plan moves between a dock and an overlay without losing edits. */
export function ConversationPlanLayout({ children, plan, hasPlan }: {
  children: ReactNode; plan: ReactNode; hasPlan: boolean;
}) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeControl = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const [docked, setDocked] = useState(false);
  const [preference, setPreference] = useState<boolean>();
  const open = hasPlan && (preference ?? docked);
  useEffect(() => {
    if (!root.current) return;
    const observer = new ResizeObserver(([entry]) => setDocked(entry.contentRect.width >= 960));
    observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  const close = useCallback(() => {
    setPreference(false);
    requestAnimationFrame(() => trigger.current?.focus());
  }, []);
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented &&
          !document.querySelector('dialog[open], [role="dialog"], [role="menu"]')) close();
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [open, close]);
  const title = zh ? '当前方案' : 'Current plan';
  return <div ref={root} data-conversation-layout data-plan-mode={docked ? 'docked' : 'overlay'}
    className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden">
    <div data-message-column className="relative flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
    {open && !docked && <button type="button" aria-label={zh ? '收起方案面板' : 'Close plan panel'}
      tabIndex={-1} className="absolute inset-0 z-10 bg-ink/15" onClick={close} />}
    <aside id={panelId} data-conversation-plan-panel hidden={!open} inert={!open}
      aria-label={title} className={`conversation-plan-panel min-h-0 min-w-0 flex-col border-l border-line bg-panel
        ${docked ? 'shrink-0' : 'absolute inset-y-0 right-0 z-20 shadow-panel'}`}>
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 className="flex min-w-0 items-center gap-2 text-meta font-medium"><Workflow className="size-4 shrink-0 text-muted" />{title}</h2>
        <IconButton ref={closeControl} label={zh ? '收起方案面板' : 'Close plan panel'} icon={docked ? <PanelRightClose /> : <X />} onClick={close} />
      </div>
      <div data-plan-scroll className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3">{plan}</div>
    </aside>
    {hasPlan && !open && <div className="flex w-11 shrink-0 items-start justify-center border-l border-line bg-panel py-3">
      <IconButton ref={trigger} label={zh ? '展开方案面板' : 'Open plan panel'} icon={<Workflow />}
        aria-controls={panelId} aria-expanded={open} onClick={() => {
          setPreference(true);requestAnimationFrame(() => closeControl.current?.focus());
        }} />
    </div>}
  </div>;
}
