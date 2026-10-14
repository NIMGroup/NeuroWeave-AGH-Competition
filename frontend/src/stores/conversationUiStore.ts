import { create } from 'zustand';
import { newTurnId } from '../features/chat/chatTurns';

type ConversationUiStore = {
  activeConversationId: string | null;
  draftId: string;
  newConversation: () => void;
  openMenuId: string | null;
  renamingId: string | null;
  renameValue: string;
  pendingDeleteId: string | null;
  actionError: string | null;
  selectConversation: (id: string | null) => void;
  setOpenMenuId: (id: string | null) => void;
  startRename: (id: string, title: string) => void;
  setRenameValue: (value: string) => void;
  cancelRename: () => void;
  requestDelete: (id: string) => void;
  cancelDelete: () => void;
  setActionError: (message: string | null) => void;
};

export const useConversationUiStore = create<ConversationUiStore>((set) => ({
  activeConversationId: null,
  draftId: 'draft-' + newTurnId(),
  newConversation: () => set({ activeConversationId: null, draftId: 'draft-' + newTurnId() }),
  openMenuId: null,
  renamingId: null,
  renameValue: '',
  pendingDeleteId: null,
  actionError: null,
  selectConversation: (id) => set({ activeConversationId: id }),
  setOpenMenuId: (id) => set({ openMenuId: id }),
  startRename: (id, title) => set({ renamingId: id, renameValue: title, openMenuId: null }),
  setRenameValue: (value) => set({ renameValue: value }),
  cancelRename: () => set({ renamingId: null, renameValue: '' }),
  requestDelete: (id) => set({ pendingDeleteId: id, openMenuId: null }),
  cancelDelete: () => set({ pendingDeleteId: null }),
  setActionError: (message) => set({ actionError: message })
}));
