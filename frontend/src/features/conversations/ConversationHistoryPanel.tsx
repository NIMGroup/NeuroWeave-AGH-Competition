import { useEffect, useRef } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '../../ui/Button';
import { Tabs } from '../../ui/Tabs';
import { ConversationRow } from './ConversationRow';
import { ConversationSearch } from './ConversationSearch';
import type { TranslationKeys } from '../../types/i18n';
import type { ConversationListItem } from './conversationTypes';
import type { useConversationList } from './useConversations';
import type { useConversationUiStore } from '../../stores/conversationUiStore';

type Props = {
  t: TranslationKeys;
  ui: ReturnType<typeof useConversationUiStore.getState>;
  chatSection: 'history' | 'archived';
  setChatSection: (value: 'history' | 'archived') => void;
  conversationSearch: string;
  onSearch: (value: string) => void;
  debouncedSearch: string;
  disabled: boolean;
  pendingConversations?: string[];
  listQuery: ReturnType<typeof useConversationList>;
  listItems: ConversationListItem[];
  activeConversationId: string | null;
  handleNew: () => void;
  handleSelect: (id: string) => void;
  handleCommitRename: (id: string) => void;
  handleArchive: (id: string, archive: boolean) => void;
  handleConfirmDelete: (id: string) => void;
};
/** List rendering and page observation only; navigation and mutations stay in the controller. */
export function ConversationHistoryPanel({
  t,
  ui,
  chatSection,
  setChatSection,
  conversationSearch,
  onSearch,
  debouncedSearch,
  disabled,
  pendingConversations = [],
  listQuery,
  listItems,
  activeConversationId,
  handleNew,
  handleSelect,
  handleCommitRename,
  handleArchive,
  handleConfirmDelete
}: Props) {
  const listSentinelRef = useRef<HTMLDivElement | null>(null);
  const sectionTabs = [
    { value: 'history' as const, label: t.chatHistory },
    { value: 'archived' as const, label: t.archivedChats }
  ];
  useEffect(() => {
    const sentinel = listSentinelRef.current;
    if (!sentinel || typeof IntersectionObserver === 'undefined') return;
    if (!listQuery.hasNextPage || listQuery.isFetchingNextPage) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting))
          void listQuery.fetchNextPage();
      },
      { rootMargin: '0px 0px 80px 0px' }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listQuery.hasNextPage, listQuery.isFetchingNextPage, listItems.length]);
  return (
    <>
      <div className="shrink-0 space-y-2 px-3 pt-3">
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={disabled}
            onClick={handleNew}
          >
            <Plus aria-hidden="true" />
            {t.newChat}
          </Button>
        </div>
        <ConversationSearch value={conversationSearch} onChange={onSearch} />
        <Tabs
          items={sectionTabs}
          value={chatSection}
          onChange={setChatSection}
          label={t.chatHistory}
        />
      </div>
      <div
        key={chatSection}
        className="view-enter min-h-0 flex-1 overflow-y-auto px-3 pb-3"
      >
        {ui.actionError && (
          <p role="alert" className="px-2 pt-2 text-meta text-danger">
            {ui.actionError}
          </p>
        )}
        {listQuery.isPending ? (
          <p className="px-2 py-6 text-center text-meta text-muted">
            {t.loadingConversations}
          </p>
        ) : listQuery.isError ? (
          <div className="px-2 py-6 text-center">
            <p role="alert" className="text-meta text-danger">
              {t.conversationsLoadError}
            </p>
            <Button
              size="sm"
              className="mt-2"
              loading={listQuery.isFetching}
              onClick={() => void listQuery.refetch()}
            >
              {t.retry}
            </Button>
          </div>
        ) : listItems.length === 0 ? (
          <p className="px-2 py-6 text-center text-meta text-muted">
            {debouncedSearch.trim()
              ? t.noMatchingConversations
              : chatSection === 'archived'
                ? t.noArchivedConversations
                : t.noConversations}
          </p>
        ) : (
          <div className="space-y-1 pt-3">
            {listItems.map((item) => (
              <ConversationRow
                key={item.conversation_id}
                conversation={item}
                isActive={item.conversation_id === activeConversationId}
                isMenuOpen={ui.openMenuId === item.conversation_id}
                isRenaming={ui.renamingId === item.conversation_id}
                renameValue={ui.renameValue}
                isArchivedView={chatSection === 'archived'}
                disabled={disabled}
                processing={pendingConversations.includes(item.conversation_id)}
                pendingDelete={ui.pendingDeleteId === item.conversation_id}
                t={t}
                onSelect={() => handleSelect(item.conversation_id)}
                onToggleMenu={() =>
                  ui.setOpenMenuId(
                    ui.openMenuId === item.conversation_id
                      ? null
                      : item.conversation_id
                  )
                }
                onCloseMenu={() => ui.setOpenMenuId(null)}
                onStartRename={() =>
                  ui.startRename(item.conversation_id, item.title)
                }
                onRenameChange={ui.setRenameValue}
                onCommitRename={() => handleCommitRename(item.conversation_id)}
                onCancelRename={() => ui.cancelRename()}
                onArchive={() => handleArchive(item.conversation_id, true)}
                onUnarchive={() => handleArchive(item.conversation_id, false)}
                onDelete={() => ui.requestDelete(item.conversation_id)}
                onCancelDelete={() => ui.cancelDelete()}
                onConfirmDelete={() =>
                  handleConfirmDelete(item.conversation_id)
                }
              />
            ))}
            <div ref={listSentinelRef} />
            {listQuery.isFetchingNextPage && (
              <p className="px-2 py-3 text-center text-meta text-muted">
                {t.loadingConversations}
              </p>
            )}
          </div>
        )}
      </div>
    </>
  );
}
