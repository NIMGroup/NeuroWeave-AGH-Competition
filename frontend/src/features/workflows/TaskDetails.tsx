import { Menu } from 'lucide-react';
import { ManagedTaskCard } from './ManagedTaskCard';
import { RunHistory } from '../runs/RunHistory';
import { ui } from '../../lib/libraryText';
import { IconButton } from '../../ui/IconButton';
import type { TaskEntry } from './taskIndex';
import { taskIdentity } from './taskIndex';
import { useQuery } from '@tanstack/react-query';
import { request } from '../../api/http';
import type { ManagedTaskView } from './ManagedTaskCard';

export function TaskDetails({task, sidebarOpen, onOpenSidebar, onOpenPipelines, onOpenConversation}: {
  task: TaskEntry | null; sidebarOpen: boolean; onOpenSidebar: () => void; onOpenPipelines: () => void;
  onOpenConversation?: (id: string) => void;
}) {
  const current = useQuery({queryKey:['managed-task',task ? taskIdentity(task) : ''],enabled:false,
    queryFn:()=>request<ManagedTaskView>(`/api/tasks/${encodeURIComponent(task ? taskIdentity(task) : '')}`)});
  const entry=current.data?.entry ?? task;
  return <>
    <header className="flex min-h-16 shrink-0 items-center gap-3 border-b border-line px-4">
      {!sidebarOpen && <IconButton label={ui('打开任务列表')} icon={<Menu/>} onClick={onOpenSidebar}/>}
      <div className="min-w-0 flex-1"><p className="text-micro text-subtle">{ui('任务管理')}</p>
        <h1 className="truncate text-title">{entry?.title ?? ui('选择任务')}</h1></div>
      <button className="rounded-control border border-line px-3 py-2 text-xs hover:bg-hover"
        onClick={onOpenPipelines}>{ui('从 Pipeline 创建任务')}</button>
    </header>
    <div className="min-h-0 flex-1 overflow-auto p-4 sm:p-6">
      {task ? <div className="mx-auto max-w-6xl space-y-4">
        <p className="text-meta text-subtle">{ui('任务来源')}：{task.source==='pipeline'?'Pipeline':ui('对话')}</p>
        <ManagedTaskCard defaultExpanded key={taskIdentity(task)} taskId={taskIdentity(task)} initialEntry={task} onOpenConversation={onOpenConversation}/>
        {entry?.workflow_id && <RunHistory key={'runs:'+entry.workflow_id} workflowId={entry.workflow_id}/>}
      </div> : <div className="mx-auto mt-12 max-w-xl space-y-3 text-sm text-muted">
        <p>{ui('对话规划和 Pipeline 创建的任务都在这里管理。')}</p>
        <p>{ui('选择任务后可检查输入、输出和参数，完成预检与确认，再执行并查看运行记录。')}</p>
      </div>}
    </div>
  </>;
}
