import { CheckCircle2, CircleAlert, Loader2 } from 'lucide-react';
import { errorText } from '../../api/http';
import { canReconnect, TurnConnectionError } from './turnRecovery';
import { useLanguageStore } from '../../stores/languageStore';
import { Button } from '../../ui/Button';
import { TokenUsage } from './TokenUsage';
import { requestStatus, turnIsPending } from './turnTracking';
import type { TrackedTurn } from './turnTracking';
import type { useBackgroundChatTurns } from './useBackgroundChatTurns';

export type BackgroundChat = ReturnType<typeof useBackgroundChatTurns>;
/** Presentation only. Closing this popover never unmounts the background observer. */
export function ChatTurnPanel({ background, records, onOpen }: {
  background: BackgroundChat; records: TrackedTurn[]; onOpen: (id: string) => void;
}) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  return <div className="space-y-3 text-meta">
    {records.map(item => {
      const { id, turn } = item;
      const pending = turnIsPending(item);
      const stopping = pending && (turn?.cancel_requested || background.actions[id] === 'cancel');
      const failed = Boolean(turn && ['failed', 'interrupted', 'cancelled'].includes(turn.status));
      const disconnected = Boolean(item.error || item.reconnecting);
      const recoverable = !item.error || canReconnect(item.error) || item.error instanceof TurnConnectionError;
      return <div key={id} className="space-y-2 rounded-card border border-line p-3">
        <div className="flex min-w-0 items-center gap-2">
          {failed || item.error ? <CircleAlert className="size-4 shrink-0 text-danger" aria-hidden="true" />
            : pending ? <Loader2 className="size-4 shrink-0 animate-spin text-running" aria-hidden="true" />
            : <CheckCircle2 className="size-4 shrink-0 text-subtle" aria-hidden="true" />}
          <p role="status" className="font-medium">{stopping ? (zh ? '正在停止请求' : 'Stopping request') : requestStatus(item, zh)}</p>
        </div>
        {item.origin?.title && <p className="line-clamp-2 break-words text-muted">{item.origin.title}</p>}
        {background.errors[id] && <p role="alert" className="break-words text-danger">{background.errors[id]}</p>}
        {disconnected && <p className="break-words text-muted">{recoverable
          ? (zh ? '原请求仍在后台，正在恢复读取。切换页面不会停止请求。' : 'Recovering the original request. Navigation does not stop it.')
          : (zh ? '尚不能确认后台状态，请恢复查询或检查访问权限。' : 'Server status is unconfirmed; recover its status or check access.')}</p>}
        {Boolean(item.error) && <p className="break-words text-subtle">{errorText(item.error)}</p>}
        {turn?.error?.message && <p className="break-words text-danger">{turn.error.message}</p>}
        {turn?.message && <p className="break-words text-muted">{turn.message}</p>}
        {turn?.token_usage && <TokenUsage usage={turn.token_usage} />}
        <div className="flex flex-wrap gap-1.5">
          {disconnected && <Button size="sm" variant="ghost" onClick={() => void background.recover(id)}>{zh ? '恢复状态' : 'Recover status'}</Button>}
          {turn && <Button size="sm" variant="ghost" onClick={() => onOpen(turn.conversation_id)}>{zh ? '打开会话' : 'Open conversation'}</Button>}
          {failed && <Button size="sm" disabled={Boolean(background.actions[id])} onClick={() => void background.act(id, 'retry')}>{zh ? '重试同一请求' : 'Retry this request'}</Button>}
          {turn && pending ? <Button size="sm" disabled={Boolean(stopping)} onClick={() => void background.act(id, 'cancel')}>{stopping ? (zh ? '正在停止' : 'Stopping') : turn.request_kind === 'result_verification' ? (zh ? '停止核查' : 'Stop verification') : (zh ? '停止请求' : 'Stop request')}</Button>
            : (!pending || !recoverable) && <Button size="sm" variant="ghost" onClick={() => background.dismiss(id)}>{zh ? '关闭提示' : 'Dismiss'}</Button>}
        </div>
      </div>;
    })}
  </div>;
}
