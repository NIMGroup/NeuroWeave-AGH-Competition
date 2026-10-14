import { ui } from '../../lib/libraryText';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ArrowDown, MessageSquare } from 'lucide-react';
import type { ChatMessage } from './chatMessages';
import type { AgentTask } from '../../types/agent';
import type { TranslationKeys } from '../../types/i18n';
import { Button } from '../../ui/Button';
import { cx } from '../../ui/cx';
import { TaskExecution } from '../tasks/TaskExecution';
import { CapabilityRecommendationCard } from '../workflows/CapabilityRecommendationCard';
import { WorkspaceTimeline } from '../workspace/WorkspaceTimeline';
import type { WorkspaceEvent } from '../workspace/workspaceTypes';
import { CopyMessageButton, MessageContent } from './MessageContent';
import { useLanguageStore } from '../../stores/languageStore';
import { legacyProposalReply } from './replyPresentation';
import { TokenUsage } from './TokenUsage';
import { ConversationTaskWorkspace } from './ConversationTaskWorkspace';
import { ConversationPlanLayout } from './ConversationPlanLayout';
import { conversationPlan } from './conversationPlan';
import type { AssistantTaskState } from './AgentTaskStatus';
import type { TaskEntry } from '../workflows/taskIndex';

type MessageAreaProps = {
  children?: ReactNode;
  scopeKey?: string;
  t: TranslationKeys;
  messages: ChatMessage[];
  activeWorkflowId: string | null;
  activeWorkflow: ChatMessage['workflow'];
  activeTask: AgentTask | null;
  sending: boolean;
  controlsDisabled?: boolean;
  loadingOlder: boolean;
  hasOlder: boolean;
  olderError: string | null;
  sendError: string | null;
  selectedLabel: string | null;
  selectedCount: number;
  liveWorkspaceEvents?: WorkspaceEvent[];
  onPlanningOffer?: (text:string)=>void;
  managedTask?: AssistantTaskState | null;
  conversationId?: string | null;
  onSelectManagedTask?: (task:TaskEntry)=>void;
  onLoadOlder: () => void;
  onNearBottomChange?: (near: boolean) => void;
  scrollRef: React.RefObject<HTMLDivElement | null>;
};

export function MessageArea({
  children,
  scopeKey,
  t,
  messages,
  activeWorkflowId,
  activeWorkflow,
  activeTask,
  sending,
  controlsDisabled,
  loadingOlder,
  hasOlder,
  olderError,
  sendError,
  selectedLabel,
  selectedCount,
  liveWorkspaceEvents,
  onPlanningOffer,
  managedTask,
  conversationId,
  onSelectManagedTask,
  onLoadOlder,
  onNearBottomChange,
  scrollRef,
}: MessageAreaProps) {
  const previousScrollTop = useRef(0);
  const followTail = useRef(true);
  const viewportScope = scopeKey ?? conversationId ?? 'new-conversation';
  const [viewport, setViewport] = useState({scope: viewportScope, near: true});
  const nearBottom = viewport.scope === viewportScope ? viewport.near : true;
  const [storedPlan, setStoredPlan] = useState({scope: viewportScope, visible: false});
  const storedPlanVisible = storedPlan.scope === viewportScope && storedPlan.visible;
  const planPresence = useCallback((visible: boolean) => setStoredPlan({scope: viewportScope, visible}), [viewportScope]);
  // Reset the reader's scroll policy, while preserving the composer's DOM and focus.
  useLayoutEffect(() => {
    followTail.current = true;previousScrollTop.current = 0;
  }, [viewportScope]);
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const plan = conversationPlan(messages, managedTask, activeWorkflowId, activeWorkflow, liveWorkspaceEvents);
  const hasPlan = Boolean(plan.taskId || plan.workflowId || plan.operations.length);
  // Follow live tool updates only while the reader is already at the end.
  useLayoutEffect(() => {
    if (followTail.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [liveWorkspaceEvents?.length, sending, scrollRef, viewportScope]);
  return (
    <ConversationPlanLayout hasPlan={hasPlan || storedPlanVisible} plan={
      <ConversationTaskWorkspace conversationId={conversationId} messages={messages} task={managedTask}
        workflowId={activeWorkflowId} workflow={activeWorkflow} live={liveWorkspaceEvents} sending={sending}
        disabled={controlsDisabled} onSelectTask={onSelectManagedTask} onRequestRevision={onPlanningOffer}
        onPresenceChange={planPresence} />
    }>
    <div className="relative flex min-h-0 flex-1 flex-col">
    <div
      ref={scrollRef}
      data-message-scroll
      onScroll={(event) => {
        const el = event.currentTarget;
        const scrollingUp = el.scrollTop < previousScrollTop.current;
        previousScrollTop.current = el.scrollTop;
        const near = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        followTail.current = near;
        setViewport({scope: viewportScope, near});
        onNearBottomChange?.(near);
        if (scrollingUp && el.scrollTop < 60 && hasOlder && !loadingOlder) {
          onLoadOlder();
        }
      }}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
    >
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-8 sm:py-8">
        {sendError && <p role="alert" className="text-meta text-danger">{sendError}</p>}
        {activeTask && <TaskExecution task={activeTask} />}

        {messages.length === 0 && !hasPlan ? (
          <div className="flex min-h-[50vh] flex-col items-center justify-center px-4 text-center">
            <div className="mx-auto mb-5 flex size-12 items-center justify-center rounded-panel bg-well text-muted [&_svg]:size-6">
              <MessageSquare />
            </div>

            <h2 className="max-w-md text-display">{t.startConversation}</h2>

            <p className="mt-2 max-w-md text-body text-muted">{t.askAgent}</p>

            {selectedCount > 0 && selectedLabel ? (
              <p className="mt-4 max-w-md truncate text-meta text-ink">
                {selectedCount > 1
                  ? t.usingSkills.replace('{{count}}', String(selectedCount))
                  : `${t.usingSkill} ${selectedLabel}`}
              </p>
            ) : (
              <p className="mt-4 max-w-md text-meta text-subtle">{t.noSkillSelected}</p>
            )}
          </div>
        ) : (
          <div className="space-y-8 sm:space-y-10">
            {hasOlder && (
              <div className="flex justify-center">
                <Button size="sm" loading={loadingOlder} disabled={controlsDisabled || sending} onClick={onLoadOlder}>
                  {loadingOlder ? t.loadingOlder : t.loadOlder}
                </Button>
              </div>
            )}
            {olderError && (
              <p role="alert" className="text-center text-meta text-danger">
                {olderError}
              </p>
            )}
            {messages.map((message) => {
              const folded = legacyProposalReply(message);
              const reply = folded
                ? (zh ? '方案的当前状态和下一步见方案面板；方案修订由模型完成。' : 'See the plan panel for current readiness and next steps. The assistant handles proposal revisions.')
                : message.content;
              return (
                <article
                  key={message.id}
                  aria-label={message.role === 'user' ? (zh ? '你的消息' : 'Your message') : 'NeuroWeave'}
                  className={`flex flex-col ${
                    message.role === 'user' ? 'items-end' : 'items-start'
                  }`}
                >
                  <div className={cx('mb-2 flex items-center gap-2 text-micro text-subtle', message.role === 'user' && 'pr-1')}>
                    <span className="font-medium">{message.role === 'user' ? (zh ? '你' : 'You') : 'NeuroWeave'}</span>
                    {message.role === 'assistant' && message.clarificationRequired &&
                      <span className="text-warning">{zh ? '需要补充信息' : 'More information needed'}</span>}
                  </div>
                  <div
                    className={cx(
                      'min-w-0 max-w-full break-words',
                      message.role === 'user'
                        ? 'rounded-panel bg-well px-4 py-3 text-ink sm:max-w-[85%]'
                        : 'w-full text-ink'
                    )}
                  >
                    {message.role === 'user'
                      ? <p className="cursor-text select-text whitespace-pre-wrap text-body leading-7">{message.content}</p>
                      : <><MessageContent content={reply} />{folded && <details className="chat-disclosure mt-3 text-meta text-muted"><summary>{zh ? '查看原始检查说明' : 'Original review explanation'}</summary><MessageContent content={message.content} /></details>}</>}
                  </div>
                  {message.requestError && <p role="alert" className="mt-2 max-w-full break-words text-meta text-danger">{message.requestError}</p>}
                  {message.role === 'assistant' && message.content && <div className="mt-2 mb-3 flex min-w-0 flex-wrap items-start gap-3">
                    <CopyMessageButton content={reply} /><TokenUsage usage={message.tokenUsage} />
                  </div>}
                  {message.role === 'user' && message.tokenUsage && message.clientTurnId &&
                    !messages.some(item => item.role === 'assistant' && item.clientTurnId === message.clientTurnId) &&
                    <div className="mt-2"><TokenUsage usage={message.tokenUsage} /></div>}
                  {/* Retrieval diagnostics stay available without occupying the reply. */}
                  {message.planningOffer&&<button type="button" disabled={controlsDisabled||sending} className="my-2 rounded border border-line px-3 py-2 text-xs hover:bg-hover" onClick={()=>onPlanningOffer?.(message.planningOffer!)}>{ui('按此建议准备规划请求')}</button>}
                      {message.capabilityRouting && (
                    <details className="mt-2 text-micro text-subtle">
                      <summary className="cursor-pointer">{zh ? '能力检索详情' : 'Capability lookup details'}</summary>
                      {message.capabilityRouting.strategy === 'hierarchical' ? (
                        <span>
                          {zh ? '检索层数' : 'Levels'} {message.capabilityRouting.levels.length} · {zh ? '候选' : 'Candidates'}{' '}
                          {message.capabilityRouting.candidates_seen} · {zh ? '上下文比例' : 'Context ratio'}{' '}
                          {Math.round(message.capabilityRouting.payload_ratio * 100)}%
                        </span>
                      ) : message.capabilityRouting.strategy === 'semantic' ? (
                        <span>
                          {zh ? '语义意图匹配 · 置信度' : 'Semantic match · Confidence'}{' '}
                          {Math.round(message.capabilityRouting.confidence * 100)}%
                        </span>
                      ) : (
                        <span>{zh ? '使用本地能力检索' : 'Local capability lookup'}</span>
                      )}
                    </details>
                  )}

                  {/* Capability recommendation card */}
                  {message.capabilityRecommendation && (
                    <CapabilityRecommendationCard
                      recommendation={message.capabilityRecommendation}
                    />
                  )}

                  {/* Workspace tool events timeline */}
                  {Boolean(message.workspaceEvents?.length) && (
                    <div className="mt-3 w-full min-w-0"><WorkspaceTimeline events={message.workspaceEvents ?? []}
                      hidePlans /></div>
                  )}
                </article>
              );
            })}

            {sending && liveWorkspaceEvents && liveWorkspaceEvents.length > 0 && (
              <WorkspaceTimeline events={liveWorkspaceEvents} live hidePlans />
            )}
              </div>
        )}
      </div>
    </div>
    {!nearBottom && <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
      <Button className="pointer-events-auto rounded-pill shadow-raised" size="sm" onClick={() => {
        followTail.current = true;
        onNearBottomChange?.(true);
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'instant' });
      }}><ArrowDown aria-hidden="true" />{zh ? '回到最新消息' : 'Jump to latest'}</Button>
    </div>}
    </div>
    {children}
    </ConversationPlanLayout>
  );
}
