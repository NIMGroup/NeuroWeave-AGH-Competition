import { Database, FolderOpen, Menu, MoreHorizontal } from 'lucide-react';
import { useRef, useState } from 'react';
import { AnchoredPopover } from '../../ui/AnchoredPopover';
import { BackendStatusDot } from '../../components/BackendStatusDot';
import { Badge } from '../../ui/Badge';
import { IconButton } from '../../ui/IconButton';
import type { TranslationKeys } from '../../types/i18n';

type ChatHeaderProps = {
  t: TranslationKeys;
  title: string;
  /** Domain label shown in the conversation information menu. */
  domainLabel: string;
  sidebarOpen: boolean;
  datasetLabel: string | null;
  datasetCount: number;
  datasetDisabled?: boolean;
  datasetNotice?: string | null;
  onBrowseDataset?: () => void;
  skillLabel: string | null;
  skillCount: number;
  onOpenSidebar: () => void;
  workspaceId: string | null;
  onOpenWorkspace: () => void;
  onOpenDatasets: () => void;
};

export function ChatHeader({
  t,
  title,
  domainLabel,
  sidebarOpen,
  datasetLabel,
  datasetCount,
  datasetDisabled,
  datasetNotice,
  onBrowseDataset,
  skillLabel,
  skillCount,
  onOpenSidebar,
  workspaceId,
  onOpenWorkspace,
  onOpenDatasets
}: ChatHeaderProps) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const optionsLabel = t.conversationMenu;
  return (
    <header className="@container/chat-header shrink-0 border-b border-line bg-canvas px-4 sm:px-5">
      <div className="grid h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
        {/* Reserve space for the controls; title length must not choose the layout. */}
        <div className="flex min-w-0 items-center gap-2">
          {!sidebarOpen && (
            <IconButton label={t.openSidebar} icon={<Menu />} size="lg" onClick={onOpenSidebar} />
          )}

          <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
            <h1 className="truncate text-title" title={title}>{title}</h1>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <IconButton disabled={datasetDisabled} onClick={onOpenDatasets} label={t.datasetSelectLocal}
            title={datasetNotice ?? datasetLabel ?? t.datasetSelectLocal} variant="outline" size="lg"
            className="relative @[36rem]/chat-header:w-44 @[36rem]/chat-header:justify-start @[36rem]/chat-header:gap-2 @[36rem]/chat-header:pl-3 @[36rem]/chat-header:pr-6"
            icon={<><Database className="shrink-0" />
              <span className="hidden min-w-0 truncate text-xs font-medium @[36rem]/chat-header:inline">{datasetLabel ?? t.datasetSelectLocal}</span>
              {datasetCount > 0 && <span className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-pill bg-accent-solid px-0.5 text-micro text-on-accent">{datasetCount}</span>}
            </>} />
          <IconButton onClick={onOpenWorkspace} label={workspaceId ? t.workspaceTitle : t.workspaceSelect}
            variant="outline" size="lg"
            className="@[48rem]/chat-header:w-auto @[48rem]/chat-header:gap-2 @[48rem]/chat-header:px-3"
            icon={<><FolderOpen className="shrink-0" />
              <span className="hidden text-xs font-medium @[48rem]/chat-header:inline">{workspaceId ? t.workspaceTitle : t.workspaceSelect}</span>
            </>} />
          {onBrowseDataset && <IconButton label={t.datasetBrowseFiles} icon={<FolderOpen />}
            variant="outline" size="lg" onClick={onBrowseDataset} />}
          <IconButton ref={anchor} label={optionsLabel} icon={<MoreHorizontal />} size="lg"
          aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)} />
        </div>
        <AnchoredPopover open={open} anchorRef={anchor} onClose={() => setOpen(false)} role="dialog"
          aria-label={optionsLabel} className="w-80 space-y-3 rounded-card border border-line bg-raised p-3 text-ink shadow-panel">
          <div className="flex items-center gap-2 text-meta text-muted"><BackendStatusDot t={t} />{domainLabel}</div>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {skillCount>0 && <Badge tone="neutral" size="md" title={skillLabel??undefined} className="max-w-48">
              <span className="truncate">{skillLabel}</span></Badge>}
          </div>
        </AnchoredPopover>
      </div>
    </header>
  );
}
