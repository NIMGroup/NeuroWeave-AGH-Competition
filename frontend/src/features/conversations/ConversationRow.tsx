import { useId, useRef } from 'react';
import { AnchoredPopover } from '../../ui/AnchoredPopover';
import { Archive, Loader2, MoreHorizontal, Pencil, Trash2, Undo2 } from 'lucide-react';
import { Badge } from '../../ui/Badge';
import { Button } from '../../ui/Button';
import { IconButton } from '../../ui/IconButton';
import { Menu, MenuItem } from '../../ui/Menu';
import { cx } from '../../ui/cx';
import type { ConversationListItem } from './conversationTypes';
import type { TranslationKeys } from '../../types/i18n';

type ConversationRowProps = {
  conversation: ConversationListItem;
  isActive: boolean;
  isMenuOpen: boolean;
  isRenaming: boolean;
  renameValue: string;
  isArchivedView: boolean;
  disabled: boolean;
  processing?: boolean;
  pendingDelete: boolean;
  t: TranslationKeys;
  onSelect: () => void;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onStartRename: () => void;
  onRenameChange: (value: string) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onArchive: () => void;
  onUnarchive: () => void;
  onDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
};

export function ConversationRow({
  conversation,
  isActive,
  isMenuOpen,
  isRenaming,
  renameValue,
  isArchivedView,
  disabled,
  processing = false,
  pendingDelete,
  t,
  onSelect,
  onToggleMenu,
  onCloseMenu,
  onStartRename,
  onRenameChange,
  onCommitRename,
  onCancelRename,
  onArchive,
  onUnarchive,
  onDelete,
  onCancelDelete,
  onConfirmDelete
}: ConversationRowProps) {
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const menuId = useId();
  const confirmationId = useId();
  const deleteDescriptionId = useId();
  const updatedAt = new Date(conversation.updated_at);
  const timestamp = Number.isNaN(updatedAt.getTime())
    ? ''
    : updatedAt.toDateString() === new Date().toDateString()
      ? `${String(updatedAt.getHours()).padStart(2, '0')}:${String(updatedAt.getMinutes()).padStart(2, '0')}`
      : `${updatedAt.getMonth() + 1}/${updatedAt.getDate()}`;
  const preview = conversation.last_message_preview || '';

  return (
    <div
      className={cx(
        'group relative rounded-card transition-control',
        isActive ? 'bg-selected' : 'hover:bg-hover'
      )}
    >
      {/* Selection marker: reads faster than a background tint alone. */}
      {isActive && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute bottom-2 left-0 top-2 w-0.5 rounded-pill bg-accent"
        />
      )}
      <button
        type="button"
        onClick={onSelect}
        disabled={disabled}
        aria-current={isActive || undefined}
        aria-label={`${isActive ? t.deselect : t.select} ${conversation.title}`}
        className="w-full rounded-card px-3 py-2.5 pr-10 text-left disabled:cursor-wait"
      >
        {isRenaming ? (
          <input
            autoFocus
            value={renameValue}
            placeholder={t.renamePlaceholder}
            onChange={(event) => onRenameChange(event.target.value)}
            onBlur={onCommitRename}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                onCommitRename();
              }

              if (event.key === 'Escape') {
                onCancelRename();
              }
            }}
            onClick={(event) => event.stopPropagation()}
            aria-label={t.renameConversation}
            className="w-full rounded-chip border border-line-strong bg-raised px-2 py-1 text-body font-medium text-ink"
          />
        ) : (
          <>
            <p className="truncate text-body font-medium">{conversation.title}</p>

            <p className="mt-1 line-clamp-2 min-h-8 text-meta leading-4 text-muted">{preview}</p>

            <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {timestamp && (
                <time className="text-meta text-subtle" dateTime={conversation.updated_at}>
                  {timestamp}
                </time>
              )}
              <Badge tone="neutral">
                {t.messagesCount.replace('{{count}}', String(conversation.message_count))}
              </Badge>
              {processing && <span role="status" title={t.processingRequest} aria-label={t.processingRequest}>
                <Loader2 className="size-3.5 animate-spin text-running" aria-hidden="true" />
              </span>}
            </span>
          </>
        )}
      </button>

      <IconButton
        ref={menuButtonRef}
        disabled={disabled || processing}
        label={t.conversationMenu}
        icon={<MoreHorizontal />}
        size="sm"
        aria-haspopup={pendingDelete ? 'dialog' : 'menu'}
        aria-controls={pendingDelete ? confirmationId : isMenuOpen ? menuId : undefined}
        aria-expanded={isMenuOpen || pendingDelete}
        onClick={(event) => {
          event.stopPropagation();
          if (pendingDelete) { onCancelDelete(); return; }
          onToggleMenu();
        }}
        className={cx(
          'absolute right-1.5 top-2 text-muted hover:text-ink',
          isMenuOpen ? 'opacity-100' : 'opacity-0 focus-visible:opacity-100 group-hover:opacity-100'
        )}
      />

      {isArchivedView && !isRenaming && (
        <button
          type="button"
          onClick={onUnarchive}
          disabled={disabled}
          className="mb-2 ml-3 rounded-chip text-meta text-muted transition-control hover:text-ink hover:underline disabled:opacity-50"
        >
          {t.archivedRestoreHint}
        </button>
      )}

      <Menu
        id={menuId}
        anchorRef={menuButtonRef}
        open={isMenuOpen}
        onClose={onCloseMenu}
        label={t.conversationMenu}
        className="w-36"
      >
        <MenuItem disabled={disabled} onSelect={onStartRename} icon={<Pencil />}>
          {t.rename}
        </MenuItem>
        {isArchivedView ? (
          <MenuItem disabled={disabled} onSelect={onUnarchive} icon={<Undo2 />}>
            {t.unarchive}
          </MenuItem>
        ) : (
          <MenuItem disabled={disabled} onSelect={onArchive} icon={<Archive />}>
            {t.archive}
          </MenuItem>
        )}
        <MenuItem onSelect={onDelete} icon={<Trash2 />} tone="danger" disabled={disabled}>
          {t.delete}
        </MenuItem>
      </Menu>

      <AnchoredPopover
        id={confirmationId}
        open={pendingDelete}
        anchorRef={menuButtonRef}
        onClose={onCancelDelete}
        role="alertdialog"
        aria-label={t.deleteConfirmTitle}
        aria-describedby={deleteDescriptionId}
        className="w-60 rounded-card border border-line bg-raised p-3 text-ink shadow-panel"
      >
        <p className="text-body font-semibold">{t.deleteConfirmTitle}</p>
        <p id={deleteDescriptionId} className="mt-1 text-meta text-muted">{t.deleteConfirmBody}</p>
        <div className="mt-2 flex flex-wrap justify-end gap-1.5">
          <Button variant="ghost" size="sm" onClick={onCancelDelete}>{t.cancel}</Button>
          <Button variant="danger" size="sm" onClick={onConfirmDelete} disabled={disabled}>{t.confirmDelete}</Button>
        </div>
      </AnchoredPopover>
    </div>
  );
}
