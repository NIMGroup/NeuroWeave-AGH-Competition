import { CheckCircle2, ChevronDown, CircleAlert, Loader2, Wrench } from 'lucide-react';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';
import type { WorkspaceEvent } from './workspaceTypes';
import { WorkspaceConfirmation } from './WorkspaceConfirmation';
import { DataOperationCard } from '../data/DataOperationCard';
import { latestOperationEvents, workspaceActivities } from './workspaceActivity';

const TOOL_KEYS: Record<string, keyof TranslationKeys> = {
  list_workspace_directory: 'workspaceToolList',
  inspect_workspace_path: 'workspaceToolInspect',
  search_workspace_files: 'workspaceToolSearch',
  read_workspace_text: 'workspaceToolRead',
  create_workspace_directory: 'workspaceToolMkdir',
  copy_workspace_path: 'workspaceToolCopy',
  move_workspace_path: 'workspaceToolMove',
  rename_workspace_path: 'workspaceToolRename',
  delete_workspace_path: 'workspaceToolDelete'
};
for (const [tool, label] of Object.entries(TOOL_KEYS)) {
  TOOL_KEYS[tool.replace('_workspace_', '_output_')] = label;
  if (/^(list|inspect|search|read)_/.test(tool)) TOOL_KEYS[tool.replace('_workspace_', '_dataset_')] = label;
}

export function WorkspaceTimeline({
  events, conversationId = null, live = false, hiddenOperationIds = [], retiredOperationIds = [], onRequestRevision, hidePlans = false
}: {
  events: WorkspaceEvent[];
  conversationId?: string | null;
  live?: boolean;
  hiddenOperationIds?: string[];
  retiredOperationIds?: string[];
  onRequestRevision?: (text: string) => void;
  hidePlans?: boolean;
}) {
  const language = useLanguageStore(s => s.language);
  const zh = language.startsWith('zh');
  const t = getTranslations(language);
  const labels: Record<string, string> = zh
    ? { propose_data_changes: '文件修改', propose_data_organization: '完整扫描并整理', run_data_python: '数据分析与整理', inventory_data_files: '扫描文件清单', list_data_operations: '查询数据任务', get_data_operation: '查询执行结果', list_data_operation_files: '查询批次文件清单', inspect_data_coverage: '核验完整覆盖', understand_task: '理解任务', answer_without_action: '回答问题', search_conversation_history: '检索对话历史', read_conversation_memory: '读取历史记录', prepare_current_step: '核对操作方案' }
    : { propose_data_changes: 'File changes', propose_data_organization: 'Scan and organize', run_data_python: 'Data analysis and changes', inventory_data_files: 'Scan inventory', list_data_operations: 'List data tasks', get_data_operation: 'Check result', list_data_operation_files: 'List batch files', inspect_data_coverage: 'Check full coverage', understand_task: 'Understand task', answer_without_action: 'Answer', search_conversation_history: 'Search conversation history', read_conversation_memory: 'Read past records', prepare_current_step: 'Check proposed action' };
  const statuses: Record<string, string> = zh
    ? { running: '进行中', succeeded: '已返回', pending: '已生成预览', submitted: '已提交后台', failed: '调用失败', cancelled: '已停止' }
    : { running: 'Running', succeeded: 'Returned', pending: 'Preview prepared', submitted: 'Submitted', failed: 'Call failed', cancelled: 'Stopped' };
  Object.assign(labels, zh
    ? { update_task_state: '记录目标与选择', prepare_workflow: '生成科学流程', read_data_result: '读取实际产物', get_workflow_result: '核验流程结果', propose_filename_transform: '准备名称变换', review_plan: '核对待运行方案' }
    : { update_task_state: 'Record goal and decisions', prepare_workflow: 'Prepare scientific workflow', read_data_result: 'Read actual artifact', get_workflow_result: 'Verify workflow result', propose_filename_transform: 'Prepare name transform', review_plan: 'Review prepared plan' });
  Object.assign(labels, zh
    ? { delegate_agent: '分派事实调查', read_shared_facts: '读取共享事实', compress_agent_context: '整理过程记忆' }
    : { delegate_agent: 'Delegate investigation', read_shared_facts: 'Read shared facts', compress_agent_context: 'Summarize process' });
  const agents: Record<string, string> = zh
    ? { coordinator: '主控', data: '数据', methods: '方法', runtime: '环境与结果' }
    : { coordinator: 'Coordinator', data: 'Data', methods: 'Methods', runtime: 'Environment and results' };
  const activities = workspaceActivities(events);
  const operations = latestOperationEvents(events).filter(event => !retiredOperationIds.includes('data:' + event.data_operation?.id));
  const failures = activities.filter(event => event.status === 'failed');
  const running = activities.findLast(event => event.status === 'running');
  const label = (event: WorkspaceEvent) => (event.agent ? agents[event.agent] + ' · ' : '')
    + (labels[event.tool] ?? t[TOOL_KEYS[event.tool]] ?? event.tool);
  if (!activities.length) return null;
  return <div className="w-full min-w-0 space-y-3">
    <details className="chat-activity rounded-card border border-line bg-panel text-meta" open={failures.length > 0 && (live || operations.length === 0)}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-muted">
        {live ? <Loader2 className="size-3.5 shrink-0 animate-spin text-running" aria-hidden="true" />
          : failures.length ? <CircleAlert className="size-3.5 shrink-0 text-danger" aria-hidden="true" />
          : <Wrench className="size-3.5 shrink-0" aria-hidden="true" />}
        <span className="min-w-0 flex-1">
          {live ? (running ? label(running) : zh ? '正在整理结果' : 'Preparing response') : zh ? '工具记录' : 'Tool activity'}
          <span className="ml-2 text-subtle">{activities.length} {zh ? '步' : 'steps'}</span>
          {failures.length > 0 && <span className="ml-2 text-danger">{failures.length} {zh ? '次调用失败' : 'failed calls'}</span>}
        </span>
        <ChevronDown className="activity-chevron size-3.5 shrink-0" aria-hidden="true" />
      </summary>
      <ol className="max-h-64 space-y-3 overflow-y-auto border-t border-line px-3 py-3">
        {activities.map(event => <li key={event.key} className="flex min-w-0 gap-2.5">
          {event.status === 'running' ? <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-running" aria-hidden="true" />
            : event.status === 'failed' ? <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-danger" aria-hidden="true" />
            : <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-subtle" aria-hidden="true" />}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-ink">{event.tool.includes('_dataset_') ? t.datasetFilesTitle + ' · ' : event.tool.includes('_output_') ? t.workspaceTitle + ' · ' : ''}{label(event)}</span>
              <span className={event.status === 'failed' ? 'text-danger' : 'text-subtle'}>{statuses[event.status] ?? event.status}</span>
              {event.count != null && <span className="text-subtle">{t.workspaceFoundCount.replace('{{count}}', String(event.count))}</span>}
            </div>
            {typeof event.arguments?.path === 'string' && <code className="mt-1 block break-all text-micro text-subtle">{event.arguments.path}</code>}
            {event.error && <p className="mt-1 break-words text-danger">{event.error}</p>}
          </div>
        </li>)}
      </ol>
    </details>
    {!hidePlans && operations.map(event => event.data_operation
      ? hiddenOperationIds.includes('data:' + event.data_operation.id)
        ? <a key={'data:' + event.data_operation.id} href={'#data-operation-' + event.data_operation.id} className="block text-meta text-muted underline">{zh ? '查看此任务的最新状态' : 'View the latest task status'}</a>
        : <DataOperationCard key={'data:' + event.data_operation.id} operation={event.data_operation} onRequestRevision={onRequestRevision} />
      : event.operation
        ? hiddenOperationIds.includes('workspace:' + event.operation.id)
          ? <a key={'workspace:' + event.operation.id} href={'#workspace-operation-' + event.operation.id} className="block text-meta text-muted underline">{zh ? '查看此操作的最新状态' : 'View the latest operation status'}</a>
          : <div id={'workspace-operation-' + event.operation.id} key={'workspace:' + event.operation.id}>
            <WorkspaceConfirmation operation={event.operation} conversationId={conversationId} />
          </div>
        : null)}
  </div>;
}
