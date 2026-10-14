import { useRef, useState } from 'react';
import { Activity, Loader2, Square } from 'lucide-react';
import { AnchoredPopover } from '../../ui/AnchoredPopover';
import { IconButton } from '../../ui/IconButton';
import { Button } from '../../ui/Button';
import { useLanguageStore } from '../../stores/languageStore';
import { executionKey } from './useBackgroundExecutions';
import type { ExecutionActivity, useBackgroundExecutions } from './useBackgroundExecutions';

export function ExecutionActivityControl({ background, records = background.records, sendPosition = false, onOpen }: {
  background: ReturnType<typeof useBackgroundExecutions>; records?: ExecutionActivity[];
  sendPosition?: boolean; onOpen: (id: string) => void;
}) {
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  if (!records.length) return null;
  const label = (sendPosition ? (zh ? '执行进度' : 'Execution progress') : (zh ? '后台执行' : 'Background executions')) + ' (' + records.length + ')';
  const stopping = (item: ExecutionActivity) => item.cancel_requested || background.actions[executionKey(item)];
  const stopLabel = (item: ExecutionActivity) => item.kind === 'check'
    ? stopping(item) ? (zh ? '正在停止检查' : 'Stopping check') : (zh ? '停止检查' : 'Stop check')
    : stopping(item) ? (zh ? '正在停止执行' : 'Stopping execution') : (zh ? '停止执行' : 'Stop execution');
  const labels: Record<string, string> = zh
    ? { validating: '检查执行条件', queued: '排队中', analyzing: '脚本执行中', running: '执行中', rolling_back: '恢复中', cancel_requested: '正在停止' }
    : { validating: 'Checking execution conditions', queued: 'Queued', analyzing: 'Running script', running: 'Running', rolling_back: 'Restoring', cancel_requested: 'Stopping' };
  return <>
    <IconButton ref={anchor} label={label} icon={sendPosition ? <Loader2 className="animate-spin" /> : <Activity />}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)} />
    {sendPosition && records.length === 1 && records[0].can_cancel && <IconButton
      label={stopLabel(records[0])} icon={<Square />} disabled={Boolean(stopping(records[0]))}
      onClick={() => void background.stop(records[0])} />}
    <AnchoredPopover open={open} anchorRef={anchor} onClose={() => setOpen(false)} side="top" role="dialog"
      aria-label={label} className="w-96 space-y-3 rounded-card border border-line bg-raised p-3 text-meta text-ink shadow-panel">
      <p className="text-muted">{zh ? '停止会等待当前安全步骤退出；已完成的改动保留，可通过任务记录检查或恢复。' : 'Stop waits for the current safe step to exit. Completed changes remain available for inspection or recovery.'}</p>
      {background.reconnecting && <p role="status">{zh ? '正在恢复进度连接，后台任务可能仍在运行。' : 'Reconnecting; background work may still be running.'}</p>}
      {records.map(item => <div key={executionKey(item)} className="space-y-2 rounded-control border border-line p-3">
        <p className="break-words font-medium">{item.title}</p>
        <p role="status">{stopping(item) ? stopLabel(item) : item.kind === 'check' && item.status === 'running'
          ? (zh ? '后台检查中' : 'Checking in background') : labels[item.status] ?? item.status}
          {Boolean(item.total) && <> · {item.completed ?? 0}/{item.total}</>}
          {item.progress != null && <> · {Math.round(item.progress)}%</>}</p>
        {background.errors[executionKey(item)] && <p role="alert" className="break-words text-danger">{background.errors[executionKey(item)]}</p>}
        <div className="flex flex-wrap gap-2">
          {item.conversation_id && <Button size="sm" variant="ghost" onClick={() => { setOpen(false); onOpen(item.conversation_id!); }}>{zh ? '打开会话' : 'Open conversation'}</Button>}
          {item.can_cancel && <Button size="sm" disabled={Boolean(stopping(item))} onClick={() => void background.stop(item)}>{stopLabel(item)}</Button>}
        </div>
      </div>)}
    </AnchoredPopover>
  </>;
}
