import { CheckCircle2, CircleDashed, Clock3, Loader2, TriangleAlert, XCircle } from 'lucide-react';
import { stateText } from '../../lib/stateText';
import { ui } from '../../lib/libraryText';
import type { TaskEntry } from './taskIndex';

export function TaskStatusBadge({task}:{task:TaskEntry}) {
  const status=(task.task_id ? task.status : task.latest_run_status??task.status) || 'awaiting_run';
  const active=['queued','running','validating','analyzing','cancel_requested','rolling_back'].includes(status);
  const tone=active?'bg-running-quiet text-running':status==='succeeded'?'bg-success-quiet text-success':status==='failed'?'bg-danger-quiet text-danger':status==='confirmed'||status==='ready'?'bg-accent-quiet text-accent':'bg-well text-muted';
  const Icon=active?status==='queued'?Clock3:Loader2:status==='succeeded'?CheckCircle2:status==='failed'?TriangleAlert:status==='cancelled'?XCircle:CircleDashed;
  return <span title={task.task_id?ui('任务状态'):task.latest_run_status?ui('最新运行状态'):ui('流程配置状态')} className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-micro font-medium ${tone}`}>
    <Icon aria-hidden="true" className={`size-3 ${status==='running'?'animate-spin':''}`}/>{({user_ended:ui('已结束（未完成）'),user_confirmed:ui('已由用户确认完成'),preparing:ui('准备方案'),awaiting_inputs:ui('待选择输入'),awaiting_run:ui('等待点击运行'),needs_replan:ui('目标已更新，待更新方案'),verifying:ui('核验结果'),blocked:ui('尚未完成'),needs_input:ui('需要补充信息')} as Record<string,string>)[status] ?? stateText(status)}
  </span>;
}
