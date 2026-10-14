import { useLanguageStore } from '../../stores/languageStore';

export type AssistantTaskState = {
  task_id: string;
  goal: string;
  deliverable: string;
  phase: string;
  next_step?: string;
  scope_notes?: string;
  success_criteria?: string[];
  strategy?: string;
  assumptions?: string[];
  open_questions?: string[];
  decisions?: { quote: string; reference: string }[];
  task_revision?: number;
  user_requirements?: string;
  active_plan?: { id: string; kind: string; revision?: string | number; task_revision?: number };
  task_deleted?: boolean;
  user_completion?: { confirmed_at: number; actor: string; task_revision: number;
    plan: AssistantTaskState['active_plan'] | null };
  user_closure?: { closed_at: number; actor: string; outcome:'ended'|'completed'; task_revision:number;
    plan: AssistantTaskState['active_plan'] | null; previous_phase?:string };
  updated_at?: number;
  scope?: { dataset_id?: string | null; workspace_id?: string | null };
  plan_check?: { status: string; methods?: string[] };
};

export function AgentTaskStatus({ task }: { task?: AssistantTaskState | null }) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  if (!task || task.phase === 'answered') return null;
  const labels: Record<string, string> = zh
    ? { needs_replan: '目标已更新，待更新方案', preparing: '准备方案', needs_input: '需要补充信息', awaiting_inputs: '草案已生成，待选择输入', awaiting_run: '等待点击运行', executing: '执行中', verifying: '核验结果', completed: '目标已完成', blocked: '尚未完成' }
    : { preparing: 'Preparing', needs_input: 'More information needed', awaiting_inputs: 'Draft prepared, select inputs', awaiting_run: 'Awaiting Run', executing: 'Running', verifying: 'Verifying results', completed: 'Goal completed', blocked: 'Incomplete' };
  return <div className="shrink-0 border-b border-line px-4 py-2 text-meta" role="status">
    <div className="flex min-w-0 items-center gap-3">
      <span className="shrink-0 font-medium">{task.user_closure?.outcome === 'ended'
        ? (zh ? '已主动结束，未完成' : 'Ended without completion') : task.user_completion || task.user_closure
        ? (zh ? '已由你确认完成' : 'Completion confirmed by you') : labels[task.phase] ?? task.phase}</span>
      <span className="text-muted">{zh ? '当前需求理解' : 'Current understanding'}</span>
    </div>
    <p className="mt-2 whitespace-pre-wrap break-words">{task.goal}</p>
    {task.task_revision && <p className="mt-1 text-muted">{zh ? '任务版本' : 'Task revision'} {task.task_revision}</p>}
    {task.user_requirements && <details className="mt-2"><summary>{zh ? '用户要求' : 'User requirements'}</summary>
      <p className="mt-1 whitespace-pre-wrap break-words">{task.user_requirements}</p></details>}
    {task.scope_notes && <p className="mt-2 break-words"><span className="font-medium">{zh ? '范围：' : 'Scope: '}</span>{task.scope_notes}</p>}
    {task.success_criteria?.length ? <div className="mt-2"><span className="font-medium">{zh ? '完成条件' : 'Success criteria'}</span>
      <ul className="mt-1 list-disc space-y-1 pl-4">{task.success_criteria.map((item, i) => <li key={i} className="break-words">{item}</li>)}</ul></div> : null}
    {task.strategy && <details className="mt-2 break-words"><summary>{zh ? '方案详情' : 'Plan details'}</summary><p>{task.strategy}</p></details>}
    {task.decisions?.length ? <div className="mt-2"><span className="font-medium">{zh ? '已引用的用户要求' : 'Quoted user requirements'}</span>
      <ul className="mt-1 list-disc space-y-1 pl-4">{task.decisions.map((item, i) => <li key={i} className="break-words">{item.quote}</li>)}</ul></div> : null}
    {task.assumptions?.length ? <div className="mt-2"><span className="font-medium">{zh ? '模型假设（待核实）' : 'Assumptions to verify'}</span>
      <ul className="mt-1 list-disc space-y-1 pl-4">{task.assumptions.map((item, i) => <li key={i} className="break-words">{item}</li>)}</ul></div> : null}
    {task.open_questions?.length ? <div className="mt-2"><span className="font-medium">{zh ? '待明确的问题' : 'Open questions'}</span>
      <ul className="mt-1 list-disc space-y-1 pl-4">{task.open_questions.map((item, i) => <li key={i} className="break-words">{item}</li>)}</ul></div> : null}
    {['blocked', 'needs_input', 'awaiting_inputs', 'awaiting_run'].includes(task.phase) && task.next_step &&
      <p className="mt-1 break-words text-muted">{task.next_step}</p>}
  </div>;
}
