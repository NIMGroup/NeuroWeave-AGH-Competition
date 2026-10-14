import { useRef, useState } from 'react';
import { Activity, CircleAlert, Info, Loader2, Square } from 'lucide-react';
import { AnchoredPopover } from '../../ui/AnchoredPopover';
import { IconButton } from '../../ui/IconButton';
import { useLanguageStore } from '../../stores/languageStore';
import { ChatTurnPanel } from './ChatTurnPanel';
import type { BackgroundChat } from './ChatTurnPanel';
import type { TrackedTurn } from './turnTracking';
import { requestStatus, turnIsFailed, turnIsPending } from './turnTracking';
import { TokenUsage } from './TokenUsage';
import type { TokenUsageSummary } from './TokenUsage';
import { AgentTaskStatus } from './AgentTaskStatus';
import type { AssistantTaskState } from './AgentTaskStatus';
import { TaskEditor } from './TaskEditor';

export function ChatActivityControl({ background, records = background.records, onOpen, sendPosition = false }: {
  background: BackgroundChat; records?: TrackedTurn[]; onOpen: (id: string) => void; sendPosition?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  if (!records.length) return null;
  const failed = records.some(turnIsFailed);
  const pending = records.findLast(turnIsPending);
  const stopping = pending && (pending.turn?.cancel_requested || background.actions[pending.id] === 'cancel');
  const stopLabel = stopping ? (zh ? '正在停止' : 'Stopping')
    : pending?.turn?.request_kind === 'result_verification' ? (zh ? '停止核查' : 'Stop verification')
    : (zh ? '停止请求' : 'Stop request');
  const label = sendPosition ? `${requestStatus(records.at(-1)!, zh)} · ${zh ? '查看进度' : 'View progress'}`
    : `${zh ? '后台会话' : 'Background conversations'} (${records.length})`;
  return <>
    <span className="relative inline-flex shrink-0">
    <IconButton ref={anchor} label={label} aria-haspopup="dialog" aria-expanded={open}
      icon={failed ? <CircleAlert /> : sendPosition ? <Loader2 className="animate-spin" /> : <Activity />}
      variant={sendPosition ? 'solid' : 'ghost'} size={sendPosition ? 'lg' : 'md'} onClick={() => setOpen(value => !value)} />
    {!sendPosition && <span aria-hidden="true" className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-pill bg-accent-solid px-0.5 text-micro text-on-accent">{records.length}</span>}
    </span>
    {sendPosition && pending?.turn && <IconButton label={stopLabel} icon={<Square />}
      disabled={Boolean(stopping)} onClick={() => void background.act(pending.id, 'cancel')} />}
    {sendPosition && <span role="status" className="sr-only">{label}</span>}
    <AnchoredPopover open={open} anchorRef={anchor} onClose={() => setOpen(false)} side="top" role="dialog"
      aria-label={label} className="w-96 rounded-card border border-line bg-raised p-3 shadow-panel text-ink">
      <p className="mb-2 text-meta text-muted">{zh ? '会话在后台继续处理，可以切换会话或使用其他功能。' : 'Processing continues in the background. You can switch conversations or use other features.'}</p>
      <ChatTurnPanel background={background} records={records} onOpen={id => { setOpen(false); onOpen(id); }} />
    </AnchoredPopover>
  </>;
}

export function ConversationInfo({ usage, task, conversationId, busy = false, onTaskUpdated }: {
  usage?: TokenUsageSummary; task?: AssistantTaskState | null; conversationId?: string; busy?: boolean;
  onTaskUpdated?: (task: AssistantTaskState, replan: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const label = zh ? '当前任务与会话信息' : 'Current task and conversation information';
  return <>
    <IconButton ref={anchor} label={label} icon={<Info />} aria-haspopup="dialog" aria-expanded={open}
      onClick={() => setOpen(value => !value)} />
    <AnchoredPopover open={open} anchorRef={anchor} onClose={() => setOpen(false)} side="top" role="dialog"
      aria-label={label} className="w-96 space-y-3 rounded-card border border-line bg-raised p-3 text-ink shadow-panel">
      <AgentTaskStatus task={task} />
      {task?.task_id && !task.task_deleted && task.deliverable !== 'answer' && conversationId && onTaskUpdated &&
        <TaskEditor key={`${task.task_id}:${task.task_revision ?? 1}`} task={task} conversationId={conversationId} zh={zh} busy={busy}
          onUpdated={(updated, replan) => { onTaskUpdated(updated, replan); if (replan) setOpen(false); }} />}
      <TokenUsage usage={usage} conversation />
    </AnchoredPopover>
  </>;
}
