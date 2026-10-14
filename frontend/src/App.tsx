import {ConversationHistoryPanel} from './features/conversations/ConversationHistoryPanel';
import {useMessageViewport} from './features/chat/useMessageViewport';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query';
import { patchConversation } from './features/conversations/conversationsApi';
import type { ConversationDetailState } from './features/conversations/useConversations';
import { currentConversationRead, mergeConversationMessages } from './features/conversations/conversationContext';
import { adoptConflict, useWorkflowDataset, useWorkflowQuery, workflowQueryKey } from './features/workflows/useWorkflows';
import type { WorkflowAction } from './features/workflows/useWorkflows';
import { saveWorkflowOutput } from './features/workspace/workspaceApi';
import type { Dataset } from './features/datasets/datasetsApi';
import type { Workspace } from './features/workspace/workspaceTypes';
import NeuroSplash from './components/SplashScreen/NeuroSplash';
import { AppRail } from './components/AppRail';
import type { RailDomain } from './components/AppRail';
import { ContextPanelHeader } from './components/ContextPanelHeader';
import { useThemeStore } from './stores/themeStore';
import { setMouseWaveEnabled } from './lib/mouseWave';
import { ChatActivityControl, ConversationInfo } from './features/chat/ChatActivityControl';
import { useBackgroundChatTurns } from './features/chat/useBackgroundChatTurns';
import { useBackgroundExecutions } from './features/chat/useBackgroundExecutions';
import { ExecutionActivityControl } from './features/chat/ExecutionActivityControl';
import { IconButton } from './ui/IconButton';
import { Send } from 'lucide-react';
import { belongsToConversation, responseConversation, turnIsPending } from './features/chat/turnTracking';
import type { TurnOrigin } from './features/chat/turnTracking';
import type { Turn } from './features/chat/chatTurns';
import { newTurnId, registerTurnOrigin } from './features/chat/chatTurns';
import { WorkflowHistory } from './features/workflows/WorkflowHistory';
import { TaskDetails } from './features/workflows/TaskDetails';
import { PipelineTaskDialog } from './features/library/PipelineTaskDialog';
import { taskFromWorkflow, taskIdentity } from './features/workflows/taskIndex';
import type { TaskEntry } from './features/workflows/taskIndex';
import { ChatHeader } from './features/chat/ChatHeader';
import { MessageArea } from './features/chat/MessageArea';
import { MessageComposer } from './features/chat/MessageComposer';
import type { ChatMessage } from './features/chat/chatMessages';
import { useSendChat } from './features/chat/useChat';
import { DatasetPicker } from './features/datasets/DatasetPicker';
import { StoragePanel } from './features/storage/StoragePanel';
import { WorkspacePanel } from './features/workspace/WorkspacePanel';
import { getTranslations } from './lib/i18n';
import { useLanguageStore } from './stores/languageStore';
import type { ConversationListItem } from './features/conversations/conversationTypes';
import {
  useArchiveConversation,
  useConversationDetail,
  useConversationList,
  useDeleteConversation,
  useLoadOlderMessages,
  useRenameConversation
} from './features/conversations/useConversations';
import { LibraryPanel } from './features/library/LibraryPanel';
import { SkillDetails } from './features/skills/SkillDetails';
import type { Skill } from './features/skills/skillsApi';
import { DatasetFilesDrawer } from './features/datasets/DatasetFilesDrawer';
import { useSkillStore } from './stores/skillStore';
import { useSkills } from './features/skills/useSkills';
import { useDatasetStore } from './stores/datasetStore';
import { useSelectedDataset } from './features/datasets/useDatasets';
import { useConversationUiStore } from './stores/conversationUiStore';
import { useWorkspaceToolEvents } from './features/workspace/useWorkspaceToolEvents';
import { DatasetContextPanel } from './features/datasets/DatasetContextPanel';
import { StorageContextPanel } from './features/storage/StorageContextPanel';
import { WorkspaceContextPanel } from './features/workspace/WorkspaceContextPanel';
import { ApiError } from './api/http';
import { Button } from './ui/Button';

function App() {
  const ui = useConversationUiStore();
  const { activeConversationId } = ui;
  const interactionMode = 'task' as const;
  const { selectedSkillIds, setSelectedSkills } = useSkillStore();
  const [viewedSkill, setViewedSkill] = useState<Skill | null>(null);
  // Keep unsent skill choices scoped to their conversation when browsing history.
  const skillDrafts = useRef<Record<string, string[]>>({});
  const skillQuery = useSkills();
  const skills = skillQuery.data?.skills ?? [];
  const skillsStats = skillQuery.data?.stats;
  const selectedDatasetId = useDatasetStore((s) => s.selectedDatasetId);
  const { data: chosenDataset } = useSelectedDataset(selectedDatasetId);
  const [datasetFilesOpen, setDatasetFilesOpen] = useState(false);
  const [restoredConversation, setRestoredConversation] = useState<string | null>(null);
  const [datasetPickerOpen, setDatasetPickerOpen] = useState(false);
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const selectedSkills = selectedSkillIds.map(name => skills.find(skill => skill.name === name) ?? { name, display_name: name });
  const selectedSkillNames = selectedSkills.map((s) => s.display_name || s.name).join(' + ');
  const scopeId = activeConversationId ?? ui.draftId;
  const [draftInputs, setDraftInputs] = useState<Record<string, string>>({});
  const input = draftInputs[scopeId] ?? '';
  const setInput = (value: string) => setDraftInputs(previous => ({ ...previous, [scopeId]: value }));
  const [conversationSearch, setConversationSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [chatSection, setChatSection] = useState<'history' | 'archived'>('history');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarView, setSidebarView] = useState<RailDomain>('conversations');
  const [selectedTask, setSelectedTask] = useState<TaskEntry|null>(null);
  const [taskPickerOpen,setTaskPickerOpen]=useState(false);
  const theme = useThemeStore((s) => s.theme);
  const wave = useThemeStore((s) => s.wave);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);
  const darkMode = theme === 'dark';
  const [sendErrors, setSendErrors] = useState<Record<string, string>>({});
  const clearSendError = (id: string) => setSendErrors(previous => { const next = { ...previous }; delete next[id]; return next; });
  const [olderError, setOlderError] = useState<string | null>(null);
  const [localMessagesByConversation, setLocalMessagesByConversation] = useState<
    Record<string, ChatMessage[]>
  >({});
  const localMessages = localMessagesByConversation[scopeId] ?? [];
  const scope = chatSection === 'archived' ? 'archived' : 'active';
  const listQuery = useConversationList(scope, debouncedSearch, sidebarOpen && sidebarView === 'conversations');
  const listItems: ConversationListItem[] = (listQuery.data?.pages ?? []).flatMap(
    (p) => p.conversations
  );
  const detailQuery = useConversationDetail(activeConversationId);
  const renameMutation = useRenameConversation();
  const archiveMutation = useArchiveConversation();
  const deleteMutation = useDeleteConversation();
  const sendMutation = useSendChat();
  const [draftWorkspaceId, setDraftWorkspaceId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const workspaceMutation = useMutation({
    mutationKey: ['workflow-change', 'output-root'],
    mutationFn: async ({ id, workspaceId }: { id: string | null; workspaceId: string | null }) => {
      if (activeWorkflow) {
        const updated = await saveWorkflowOutput(activeWorkflow.workflow_id, '.', activeWorkflow.revision,
          { workspaceId, conversationId: id });
        queryClient.setQueryData(workflowQueryKey(updated.workflow_id), updated);
      } else if (id) await patchConversation(id, { workspace_id: workspaceId });
    },
    onError: error => { adoptConflict(queryClient, error); },
    onSuccess: (_data, { id, workspaceId }) => {
      setDraftWorkspaceId(workspaceId);
      if (id) queryClient.setQueryData<ConversationDetailState>(['conversation', id], (previous) =>
        previous ? { ...previous, workspaceId } : previous
      );
      void queryClient.invalidateQueries({ queryKey: ['conversations'] });
    }
  });
  const bindTurn = (turn: Turn, origin?: TurnOrigin) => {
    if (!origin || origin.scope === turn.conversation_id) return;
    setLocalMessagesByConversation(previous => {
      const source = previous[origin.scope] ?? [];
      if (!source.length) return previous;
      const next = { ...previous };
      next[turn.conversation_id] = [...(next[turn.conversation_id] ?? []), ...source];
      delete next[origin.scope]; return next;
    });
    setDraftInputs(previous => {
      if (!(origin.scope in previous)) return previous;
      const next = { ...previous, [turn.conversation_id]: previous[origin.scope] };
      delete next[origin.scope]; return next;
    });
    if (skillDrafts.current[origin.scope]) {
      skillDrafts.current[turn.conversation_id] = skillDrafts.current[origin.scope];
      delete skillDrafts.current[origin.scope];
    }
    const current = useConversationUiStore.getState();
    if (!current.activeConversationId && current.draftId === origin.scope) {
      // Only the originating draft may adopt this server ID. Never navigate on a late reply.
      ui.selectConversation(turn.conversation_id);
    }
  };
  const background = useBackgroundChatTurns(bindTurn, turn => clearSendError(turn.turn_id));
  const executions = useBackgroundExecutions();
  const currentExecutions = executions.records.filter(item => !item.conversation_id || item.conversation_id === activeConversationId);
  const currentTurns = background.records.filter(item => belongsToConversation(item, activeConversationId, ui.draftId));
  const activeSending = currentTurns.some(turnIsPending);
  const activeTurn = [...currentTurns].reverse().find(turnIsPending)?.id ?? null;
  const conversationPending = (id: string) => background.records.some(item =>
    (item.turn?.conversation_id ?? item.origin?.conversationId) === id && turnIsPending(item));
  const { events: liveWorkspaceEvents } = useWorkspaceToolEvents(activeTurn);
  const [storageOpen, setStorageOpen] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [workspaceSelection, setWorkspaceSelection] = useState(true);
  const mutating =
    renameMutation.isPending || archiveMutation.isPending || deleteMutation.isPending;
  const detail = detailQuery.data ?? null;
  const serverMessages = detail?.messages ?? [];
  const mergedMessages = mergeConversationMessages(serverMessages, localMessages, detail?.readGeneration ?? 0).map(message =>
    message.role === 'user' && message.clientTurnId && sendErrors[message.clientTurnId] && !serverMessages.some(item =>
      item.role === 'assistant' && item.clientTurnId === message.clientTurnId)
      ? { ...message, requestError: sendErrors[message.clientTurnId] } : message);
  const latestWorkflowMessage = [...mergedMessages].reverse().find(message => message.workflow || message.workflowId);
  const embeddedWorkflow = latestWorkflowMessage?.workflow ?? null;
  const activeWorkflowId = detail?.assistantTask?.task_deleted ? null
    : detail?.assistantTask?.active_plan ? (detail.assistantTask.active_plan.kind === 'workflow' ? detail.assistantTask.active_plan.id : null)
    : embeddedWorkflow?.workflow_id ?? latestWorkflowMessage?.workflowId
    ?? (typeof detail?.context.workflow_id === 'string' ? detail.context.workflow_id : null);
  const workflowQuery = useWorkflowQuery(activeWorkflowId, embeddedWorkflow);
  const activeWorkflow = workflowQuery.data ?? null;
  // The persisted workflow owns its inputs, even after opening history.
  const selectedDataset = activeWorkflow ? activeWorkflow.dataset : chosenDataset;
  const workspaceId = activeWorkflow ? (activeWorkflow.workspace_id ?? null)
    : activeConversationId ? (detail?.workspaceId ?? null) : draftWorkspaceId;
  const datasetPatch = useWorkflowDataset(activeWorkflowId);
  const workflowMutations = useIsMutating({ predicate: mutation => {
    const key = mutation.options.mutationKey;
    const input = mutation.state.variables as { action?: WorkflowAction } | undefined;
    return key?.[0] === 'workflow-change' && key[1] === activeWorkflowId
      && input?.action?.kind !== 'replan' && input?.action?.kind !== 'complete-dependencies';
  } });
  const contextLoading = Boolean(activeConversationId && (restoredConversation !== activeConversationId || detailQuery.isPending || detailQuery.isError))
    || Boolean(activeWorkflowId && (workflowQuery.isPending || workflowQuery.isError));
  const navigationBusy = workspaceMutation.isPending || datasetPatch.isPending || mutating;
  const contextBusy = activeSending || workspaceMutation.isPending || datasetPatch.isPending
    || workflowMutations > 0 || contextLoading || mutating;
  const datasetLocked = Boolean(activeWorkflow?.locked_at || activeWorkflow?.status === 'confirmed');
  const datasetDisabled = datasetLocked || contextBusy;
  // Output can still be chosen during parameter review; submitted plans remain immutable.
  const outputDisabled = contextBusy || activeWorkflow?.status === 'confirmed';
  const datasetNotice = datasetLocked ? t.workflowDatasetLocked
    : activeWorkflow ? t.datasetChangeNotice : null;
  const setDatasetId = useDatasetStore(s => s.setDatasetId);

  // Apply a fetched snapshot once when opening a conversation. Background
  // refresh must not overwrite choices made after it was opened.
  useLayoutEffect(() => {
    if (!activeConversationId || !detail || restoredConversation === activeConversationId) return;
    const context = detail.context;
    setSelectedSkills(skillDrafts.current[activeConversationId] ?? (Array.isArray(context.selected_skills)
      ? context.selected_skills.filter((id): id is string => typeof id === 'string') : []));
    const scope = detail.assistantTask?.task_deleted ? undefined : detail.assistantTask?.scope;
    setDatasetId(scope ? (scope.dataset_id ?? null) : typeof context.dataset_id === 'string' ? context.dataset_id : null);
    // Deliberately synchronize controls with the asynchronous server snapshot.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRestoredConversation(activeConversationId);
  }, [activeConversationId, detail, restoredConversation, setDatasetId, setSelectedSkills]);
  useEffect(() => {
    if (activeWorkflow) {
      setDatasetId(activeWorkflow.dataset_id);
      if (activeWorkflow.dataset)
        queryClient.setQueryData(['datasets', 'detail', activeWorkflow.dataset_id], activeWorkflow.dataset);
    }
  }, [activeWorkflow, queryClient, setDatasetId]);

  const handleWorkspaceSelect = async (workspace: Workspace | null) => {
    if (outputDisabled) throw new Error(t.workspaceLockedHint);
    await workspaceMutation.mutateAsync({ id: activeConversationId, workspaceId: workspace?.id ?? null });
  };
  const handleDatasetSelect = async (dataset: Dataset) => {
    if (datasetDisabled) throw new Error(t.workflowDatasetLocked);
    if (activeWorkflow) {
      const updated = await datasetPatch.mutateAsync({
        workflowId: activeWorkflow.workflow_id, datasetId: dataset.dataset_id,
        expectedRevision: activeWorkflow.revision,
      });
      setDatasetId(updated.dataset_id);
    } else setDatasetId(dataset.dataset_id);
    queryClient.setQueryData(['datasets', 'detail', dataset.dataset_id], dataset);
  };
  const openDatasetPicker = () => { if (!datasetDisabled) setDatasetPickerOpen(true); };
  const changeSkills = (ids: string[]) => {
    if (contextBusy || olderMutation.isPending) return;
    const next = [...new Set(ids)];
    if (activeConversationId) skillDrafts.current[activeConversationId] = next;
    setSelectedSkills(next);
  };
  const addSkill = (id: string) => changeSkills([...selectedSkillIds, id]);
  const removeSkill = (id: string) => changeSkills(selectedSkillIds.filter(value => value !== id));
  const clearSkills = () => changeSkills([]);
  const viewSkill = (id: string) => setViewedSkill(skills.find(skill => skill.name === id) ?? {
    name:id, display_name:id, description:'', layer:'', skill_type:'', runtime:'', status:''
  });
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(conversationSearch), 250);
    return () => window.clearTimeout(timer);
  }, [conversationSearch]);
  useEffect(() => {
    setMouseWaveEnabled(wave);
    return () => setMouseWaveEnabled(false);
  }, [wave]);
  const handleSelect = (id: string) => {
    if (navigationBusy || olderMutation.isPending) return;
    setWorkspaceOpen(false);
    setDatasetPickerOpen(false);
    setDatasetFilesOpen(false);
    setRestoredConversation(null);
    setOlderError(null);
    ui.setActionError(null);
    ui.selectConversation(id);
    ui.setOpenMenuId(null);
  };
  const handleNew = () => {
    if (
      navigationBusy ||
      olderMutation.isPending
    )
      return;
    setDraftWorkspaceId(workspaceId);
    setRestoredConversation(null);
    setDatasetPickerOpen(false);
    setDatasetFilesOpen(false);
    setWorkspaceOpen(false);
    setSelectedSkills([]);
    ui.setActionError(null);
    setOlderError(null);
    ui.newConversation();
  };
  const handleCommitRename = (id: string) => {
    const title = ui.renameValue.replace(/\s+/g, ' ').trim();
    if (!title) {
      ui.cancelRename();
      return;
    }
    ui.setActionError(null);
    renameMutation.mutate(
      { id, title },
      {
        onSuccess: () => ui.cancelRename(),
        onError: (e) => ui.setActionError(e instanceof Error ? e.message : t.errorMessage)
      }
    );
  };
  const handleArchive = (id: string, archived: boolean) => {
    if (conversationPending(id) || mutating) return;
    ui.setActionError(null);
    archiveMutation.mutate(
      { id, archived },
      {
        onSuccess: () => {
          ui.setOpenMenuId(null);
          if (archived && id === activeConversationId) ui.selectConversation(null);
        },
        onError: (e) => ui.setActionError(e instanceof Error ? e.message : t.errorMessage)
      }
    );
  };
  const handleConfirmDelete = (id: string) => {
    if (conversationPending(id) || mutating) return;
    ui.setActionError(null);
    deleteMutation.mutate(id, {
      onSuccess: () => {
        setLocalMessagesByConversation((previous) => {
          const next = { ...previous };
          delete next[id];
          return next;
        });
        ui.cancelDelete();
        ui.setOpenMenuId(null);
        if (useConversationUiStore.getState().activeConversationId === id) {
          setOlderError(null);
          setWorkspaceOpen(false);
          ui.newConversation();
        }
      },
      onError: (e) => ui.setActionError(e instanceof Error ? e.message : t.errorMessage)
    });
  };
  const mainRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const animation = mainRef.current?.animate(
      [
        { opacity: 0.85, translate: '0 5px' },
        { opacity: 1, translate: '0 0' }
      ],
      { duration: 160, easing: 'ease-out' }
    );
    return () => animation?.cancel();
  }, [activeConversationId, sidebarOpen]);
  const {setFollowTail,scrollRef,scrollScope,restorePrepend}=useMessageViewport(activeConversationId,contextLoading,mergedMessages.at(-1)?.id);
  const olderMutation = useLoadOlderMessages(activeConversationId, {
    onSettledScroll: restorePrepend
  });
  const handleLoadOlder = () => {
    if (!activeConversationId || !detail?.hasMoreOlder || detail.beforeSequence == null) return;
    if (olderMutation.isPending || contextLoading) return;
    const viewport = scrollRef.current;
    const anchor = viewport ? { top: viewport.scrollTop, height: viewport.scrollHeight } : null;
    setOlderError(null);
    olderMutation.mutate(
      { beforeSequence: detail.beforeSequence, anchor },
      {
        onError: (e: Error) => setOlderError(e instanceof Error ? e.message : t.olderLoadError)
      }
    );
  };

  const handleSend = (override?: unknown) => {
    if (contextBusy || olderMutation.isPending) return;
    const text = typeof override === 'string' ? override.trim() : input.trim();
    if (!text || activeSending) return;
    const turnId = newTurnId();
    const origin: TurnOrigin = { scope: scopeId, conversationId: activeConversationId, title: text.slice(0, 100) };
    const userMessage: ChatMessage = {
      clientTurnId: turnId, id: `local-user-${turnId}`, role: 'user', content: text,
      createdAt: new Date().toISOString(), skills: [...selectedSkillIds], interactionMode
    };
    setFollowTail(true);
    setLocalMessagesByConversation(previous => ({ ...previous, [scopeId]: [...(previous[scopeId] ?? []), userMessage] }));
    skillDrafts.current[scopeId] = [...selectedSkillIds];
    if (typeof override !== 'string') setInput('');
    registerTurnOrigin(turnId, origin);
    // Each promise owns its captured scope. Per-call mutate callbacks are replaced
    // by React Query when a second conversation sends while the first is pending.
    void sendMutation.mutateAsync({
      message: text, conversation_id: activeConversationId, workspace_id: workspaceId,
      task_action: typeof override === 'string' ? 'update_plan' : 'message',
      client_turn_id: turnId, interaction_mode: interactionMode, selected_skills: [...selectedSkillIds],
      dataset_id: selectedDataset?.dataset_id ?? null, language: language === 'zh' ? 'zh-CN' : 'en-US'
    }).then(response => {
      const target = responseConversation(origin, response.conversation_id);
      if (response.conversation_id) bindTurn({ turn_id: turnId, conversation_id: target,
        status: 'succeeded', message: '', error: null, result: response }, origin);
      if (response.workflow) queryClient.setQueryData(workflowQueryKey(response.workflow.workflow_id), response.workflow);
      queryClient.setQueryData<ConversationDetailState>(['conversation', target], previous => previous ? {
        ...previous, workspaceId: response.workspace_id === undefined ? previous.workspaceId : response.workspace_id ?? null,
        assistantTask: response.assistant_task
      } : previous);
      if (!response.content) { setSendErrors(previous => ({ ...previous, [turnId]: t.chatSendError })); return; }
      const persistedAfterRead = currentConversationRead();
      const persistedUser = { ...userMessage, persistedAfterRead };
      const assistant: ChatMessage = {
        tokenUsage: response.token_usage, clientTurnId: turnId, id: `local-assistant-${turnId}`,
        persistedAfterRead, role: 'assistant', content: response.content, createdAt: new Date().toISOString(),
        planningOffer: response.planning_offer ?? undefined, interactionMode: response.interaction_mode ?? interactionMode,
        workflowRecommended: response.workflow_recommended, clarificationRequired: response.clarification_required,
        workflowId: response.workflow?.workflow_id, workflow: response.workflow ?? null,
        capabilityRouting: response.capability_routing, capabilityRecommendation: response.capability_recommendation,
        workspaceEvents: Array.isArray(response.workspace_tool_events) ? response.workspace_tool_events : undefined
      };
      setLocalMessagesByConversation(previous => {
        const messages = previous[target] ?? [];
        const updated = messages.map(message => message.id === userMessage.id ? persistedUser : message);
        if (!messages.some(message => message.id === userMessage.id)) updated.push(persistedUser);
        if (!messages.some(message => message.id === assistant.id)) updated.push(assistant);
        return { ...previous, [target]: updated };
      });
      clearSendError(turnId);
      void queryClient.invalidateQueries({ queryKey: ['conversation', target] });
    }).catch(error => {
      // A server-created verification may begin between discovery polls.
      // A rejected send must not consume the draft or overwrite newer typing.
      if (error instanceof ApiError && error.status === 409)
        setDraftInputs(previous => previous[origin.scope] ? previous : { ...previous, [origin.scope]: text });
      setSendErrors(previous => ({ ...previous, [turnId]:
        error instanceof ApiError || error instanceof Error ? error.message || t.chatSendError : t.chatSendError
      }));
    });
  };
  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };
  const todayCount = listItems.length;
  const activeTitle = detail?.title ?? t.newConversation;
  const contextPanelMeta: Record<RailDomain, { title: string; subtitle?: string }> = {
    conversations: {
      title: t.chatHistory,
      subtitle: t.chatsToday.replace('{{count}}', String(todayCount))
    },
    workflows: {title:t.workflowsAndRuns},
    pipelines: {title:'Pipeline'},
    skills: { title: t.skills, subtitle: String(skills.length) },
    datasets: { title: t.datasetPickerTitle, subtitle: t.datasetPickerSubtitle },
    workspace: { title: t.workspaceTitle, subtitle: t.workspaceHint },
    storage: { title: t.storageTitle, subtitle: t.storageDescription }
  };
  // A rail click both selects the domain and reveals its context panel beside
  // the rail, so the icon -> panel relationship is always demonstrated. The
  // navbar's existing open control and resource buttons share the same state.
  const handleRailDomain = (domain: RailDomain) => {
    setSidebarView(domain);
    setSidebarOpen(true);
  };
  return (
    <>
      <NeuroSplash />
      <div className="flex h-dvh overflow-hidden bg-canvas text-ink">
        <AppRail
          t={t}
          domain={sidebarView}
          datasetCount={selectedDataset ? 1 : 0}
          activity={<>
            <ChatActivityControl background={background} onOpen={id => { handleSelect(id); setSidebarView('conversations'); }} />
            <ExecutionActivityControl background={executions} onOpen={id => { handleSelect(id); setSidebarView('conversations'); }} />
          </>}
          darkMode={darkMode}
          onToggleTheme={toggleTheme}
          onDomainChange={handleRailDomain}
        />
        {sidebarOpen && <button aria-label={t.closeSidebar} className="fixed inset-y-0 left-14 right-0 z-20 bg-black/20 lg:hidden" onClick={() => setSidebarOpen(false)} />}
        {sidebarOpen && (
          <aside className="max-lg:fixed max-lg:inset-y-0 max-lg:left-14 max-lg:z-30 flex w-72 max-w-[80vw] shrink-0 flex-col overflow-hidden border-r border-line bg-panel">
            <ContextPanelHeader
              title={contextPanelMeta[sidebarView].title}
              subtitle={contextPanelMeta[sidebarView].subtitle}
              t={t}
              onClose={() => setSidebarOpen(false)}
            />
            {sidebarView === 'conversations' ? (
              <ConversationHistoryPanel t={t} ui={ui} chatSection={chatSection} setChatSection={setChatSection}
                conversationSearch={conversationSearch} onSearch={setConversationSearch} debouncedSearch={debouncedSearch}
                listQuery={listQuery} listItems={listItems} activeConversationId={activeConversationId} disabled={navigationBusy||olderMutation.isPending} pendingConversations={background.records.filter(turnIsPending).map(item => item.turn?.conversation_id ?? item.origin?.conversationId).filter((id): id is string => Boolean(id))}
                handleNew={handleNew} handleSelect={handleSelect} handleCommitRename={handleCommitRename}
                handleArchive={handleArchive} handleConfirmDelete={handleConfirmDelete}/>
            ) : sidebarView === 'skills' ? (
              <div className="view-enter min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3">
                <LibraryPanel selectedSkillIds={selectedSkillIds} disabled={contextBusy || olderMutation.isPending}
                  onAdd={addSkill} onRemove={removeSkill} onDetails={setViewedSkill} onClear={clearSkills}/>
                {skillsStats && (
                  <div className="mt-3 border-t border-line pt-2">
                    <p className="px-1 text-meta text-subtle">
                      {t.skillCatalogStats.replace('{{total}}', String(skillsStats.total))}
                      {skillsStats.capability_catalog
                        ? ` · ${t.skillCatalogMethods.replace('{{count}}', String(skillsStats.capability_catalog.methods))}`
                        : ''}
                    </p>
                  </div>
                )}
              </div>
            ) : sidebarView === 'pipelines' ? (
              <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3">
                <LibraryPanel key="pipelines" domain="pipelines" selectedSkillIds={[]} disabled={false}
                  onAdd={()=>{}} onRemove={()=>{}} onDetails={setViewedSkill} onClear={()=>{}}
                  onTaskCreated={workflow=>{setSelectedTask(taskFromWorkflow(workflow));setSidebarView('workflows');if(window.innerWidth<1024)setSidebarOpen(false);}}/>
              </div>
            ) : sidebarView === 'workflows' ? (<WorkflowHistory selectedId={selectedTask ? taskIdentity(selectedTask) : null}
              onSelect={task=>{setSelectedTask(task);if(window.innerWidth<1024)setSidebarOpen(false);}}/>) : sidebarView === 'datasets' ? (
              <DatasetContextPanel onOpenFull={openDatasetPicker} onChoose={handleDatasetSelect}
                disabled={datasetDisabled} notice={datasetNotice}
                onBrowse={selectedDataset ? () => setDatasetFilesOpen(true) : undefined} />
            ) : sidebarView === 'workspace' ? (
              <WorkspaceContextPanel workspaceId={workspaceId} onOpenFull={() => {setWorkspaceSelection(false);setWorkspaceOpen(true);}} />
            ) : (
              <StorageContextPanel onOpenFull={() => setStorageOpen(true)} />
            )}
          </aside>
        )}
        <main ref={mainRef} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {sidebarView==='workflows' ? <TaskDetails task={selectedTask} sidebarOpen={sidebarOpen}
            onOpenSidebar={()=>setSidebarOpen(true)} onOpenPipelines={()=>setTaskPickerOpen(true)}
            onOpenConversation={id=>{handleSelect(id);setSidebarView('conversations');}}/> : <>
          <ChatHeader
            workspaceId={workspaceId}
            onOpenWorkspace={() => {setWorkspaceSelection(true);setWorkspaceOpen(true);}}
            t={t}
            title={activeTitle}
            domainLabel={sidebarView==='pipelines'?'Pipeline':sidebarView === 'skills' ? t.skills : sidebarView === 'datasets' ? t.datasetPickerTitle : sidebarView === 'workspace' ? t.workspaceTitle : sidebarView === 'storage' ? t.storageTitle : t.conversations}
            sidebarOpen={sidebarOpen}
            datasetLabel={selectedDataset ? selectedDataset.display_name : null}
            datasetCount={selectedDataset ? 1 : 0}
            datasetDisabled={datasetDisabled}
            datasetNotice={datasetNotice}
            onBrowseDataset={selectedDataset ? () => setDatasetFilesOpen(true) : undefined}
            skillLabel={selectedSkills.length > 0 ? selectedSkillNames : null}
            skillCount={selectedSkills.length}
            onOpenSidebar={() => setSidebarOpen(true)}
            onOpenDatasets={openDatasetPicker}
          />
          <MessageArea onPlanningOffer={setInput}
            conversationId={activeConversationId}
            managedTask={detail?.assistantTask}
            onSelectManagedTask={task=>{setSelectedTask(task);setSidebarView('workflows');}}
            scopeKey={scrollScope}
            activeWorkflowId={activeWorkflowId}
            activeWorkflow={activeWorkflow}
            t={t}
            messages={mergedMessages}
            activeTask={null}
            sending={activeSending}
            controlsDisabled={contextBusy || Boolean(detail?.assistantTask?.active_plan &&
              (detail.assistantTask.active_plan.task_revision ?? 1) !== (detail.assistantTask.task_revision ?? 1))}
            loadingOlder={olderMutation.isPending}
            hasOlder={Boolean(detail?.hasMoreOlder)}
            olderError={olderError}
            sendError={detailQuery.isError ? t.conversationsLoadError : null}
            selectedLabel={selectedSkills.length > 0 ? selectedSkillNames : null}
            selectedCount={selectedSkills.length}
            liveWorkspaceEvents={liveWorkspaceEvents}
            onLoadOlder={handleLoadOlder}
            onNearBottomChange={near => { setFollowTail(near); }}
            scrollRef={scrollRef}
          >
          <MessageComposer
            t={t}
            value={input}
            sending={contextBusy || olderMutation.isPending}
            processing={activeSending}
            sendControl={activeSending || currentExecutions.length ? <>
              <ExecutionActivityControl key={'execution:' + scopeId} background={executions} records={currentExecutions} sendPosition onOpen={handleSelect} />
              {activeSending ? <ChatActivityControl key={scopeId} background={background} records={currentTurns}
                sendPosition onOpen={handleSelect} /> : <IconButton label={t.sendMessage} icon={<Send />} variant="solid" size="lg"
                disabled={!input.trim() || contextBusy || olderMutation.isPending} onClick={handleSend} />}
            </> : undefined}
            infoControls={<>
              {activeConversationId && <ConversationInfo key={activeConversationId} usage={detail?.tokenUsage} task={detail?.assistantTask}
                conversationId={activeConversationId} busy={activeSending || contextLoading}
                onTaskUpdated={(task, replan) => {
                  queryClient.setQueryData<ConversationDetailState>(['conversation', activeConversationId], previous => previous ? { ...previous, assistantTask: task } : previous);
                  if (replan) handleSend(language === 'zh' ? '请根据当前任务目标更新同一份完整方案，保留仍有效的输入选择和参数，不执行任务。' : 'Update the same complete plan according to the current task goal. Preserve compatible input selections and parameters. Do not execute.');
                  else void queryClient.invalidateQueries({queryKey:['conversation',activeConversationId]});
                }} />}
              {contextLoading && (detailQuery.isError || workflowQuery.isError) && <Button size="sm"
                onClick={() => { void detailQuery.refetch(); if (activeWorkflowId) void workflowQuery.refetch(); }}>{t.retry}</Button>}
            </>}
            selectedSkills={selectedSkills.map(skill => ({id:skill.name,label:skill.display_name || skill.name}))}
            onRemoveSkill={removeSkill}
            onViewSkill={viewSkill}
            onChange={setInput}
            onKeyDown={handleKeyDown}
            onSend={handleSend}
            onClearSkills={clearSkills}
          />
          </MessageArea>
          </>}
        </main>
      </div>
      {datasetPickerOpen && <DatasetPicker onSelect={handleDatasetSelect} onClose={() => setDatasetPickerOpen(false)} />}
      {taskPickerOpen&&<PipelineTaskDialog onClose={()=>setTaskPickerOpen(false)} onCreated={workflow=>{
        setTaskPickerOpen(false);setSelectedTask(taskFromWorkflow(workflow));setSidebarView('workflows');
        if(window.innerWidth<1024)setSidebarOpen(false);
      }}/>}
      {storageOpen && <StoragePanel onClose={() => setStorageOpen(false)} />}
      {viewedSkill && <SkillDetails key={viewedSkill.name} skill={viewedSkill}
        selected={selectedSkillIds.includes(viewedSkill.name)} disabled={contextBusy || olderMutation.isPending}
        onAdd={() => addSkill(viewedSkill.name)} onRemove={() => removeSkill(viewedSkill.name)} onClose={() => setViewedSkill(null)}/>}
      {datasetFilesOpen && selectedDataset && (
        <DatasetFilesDrawer key={selectedDataset.dataset_id} dataset={selectedDataset}
          onClose={() => setDatasetFilesOpen(false)} />
      )}
      {workspaceOpen && (
        <WorkspacePanel selectionMode={workspaceSelection}
          key={activeConversationId ?? 'new'}
          workspaceId={workspaceId}
          conversationId={activeConversationId}
          datasetId={selectedDatasetId}
          locked={outputDisabled}
          onSelect={handleWorkspaceSelect}
          onClose={() => setWorkspaceOpen(false)}
        />
      )}
    </>
  );
}

export default App;
