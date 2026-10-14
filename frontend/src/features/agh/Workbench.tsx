import { useEffect, useState, useSyncExternalStore } from 'react';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { DatasetPicker } from '../datasets/DatasetPicker';
import { DatasetFilesDrawer } from '../datasets/DatasetFilesDrawer';
import type { Dataset } from '../datasets/datasetsApi';
import { WorkspacePanel } from '../workspace/WorkspacePanel';
import { LibraryPanel } from '../library/LibraryPanel';
import { SkillDetails } from '../skills/SkillDetails';
import type { Skill } from '../skills/skillsApi';
import { WorkflowHistory } from '../workflows/WorkflowHistory';
import { WorkflowCard } from '../workflows/WorkflowCard';
import { RunHistory } from '../runs/RunHistory';
import { DataOperationCard } from '../data/DataOperationCard';
import type { DataOperation } from '../data/dataTypes';
import { StoragePanel } from '../storage/StoragePanel';
import { Dialog } from '../../ui/Dialog';
import { ensureWorkbenchSession, service, setWorkbenchOpen, subscribeWorkbench } from './host';
import type { AGHHost } from './host';
import type { Workflow } from '../workflows/workflowTypes';
import type { Workspace as OutputDirectory } from '../workspace/workspaceTypes';
import { useLanguageStore } from '../../stores/languageStore';
import { useDatasetStore } from '../../stores/datasetStore';
import { Activity, ArrowRight, Database, Files, FolderOutput, GitBranch, Layers3, LayoutDashboard, Library, Settings2 } from 'lucide-react';
import { Button } from '../../ui/Button';
import { cx } from '../../ui/cx';
import { WorkbenchSurface } from './WorkbenchSurface';
import { ConversationTaskWorkspace } from '../chat/ConversationTaskWorkspace';
import { ManagedTaskCard } from '../workflows/ManagedTaskCard';
import type { AssistantTaskState } from '../chat/AgentTaskStatus';

const tabs = [
  { id: 'tasks', label: '流程与运行', icon: Activity, description: '配置分析流程、检查参数，并查看服务器运行结果。' },
  { id: 'library', label: 'Skills · 方法', icon: Library, description: '按处理目标查找能力，查看方法参数和使用指导。' },
  { id: 'pipelines', label: 'Pipeline', icon: GitBranch, description: '浏览和编辑分析模板，创建可复用的流程。' },
  { id: 'data', label: '数据操作', icon: Files, description: '查看数据整理方案、执行范围和操作结果。' },
] as const;

export type BusinessContext = { conversation_id: string; session_id: string; task?: AssistantTaskState | null;
  selected: { dataset_id?: string; workspace_id?: string; workflow_id?: string; selected_skills?: string[] };
  dataset: Dataset | null; workspace: OutputDirectory | null; workflow: Workflow | null;
  workflows: Workflow[]; data_operations: DataOperation[] };

function Workspace({ host }: { host: AGHHost }) {
  const client = useQueryClient();
  const context = useQuery({ queryKey: ['agh-context'], queryFn: () => service<BusinessContext>(host, 'nw.session.context', {}), refetchInterval: 3000 });
  const [tab, setTab] = useState<'tasks' | 'library' | 'pipelines' | 'data'>('tasks');
  const [picker, setPicker] = useState<'dataset' | 'output' | 'files' | 'storage' | null>(null);
  const [skill, setSkill] = useState<Skill | null>(null);
  const [error, setError] = useState('');
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const sending = useNativeBusy(host);
  const current = context.data;
  const workflow = current?.workflow;
  const selectedSkills = current?.selected.selected_skills ?? [];
  const activeTab = tabs.find(item => item.id === tab)!;
  useEffect(() => { useDatasetStore.getState().setDatasetId(current?.selected.dataset_id ?? null); }, [current?.selected.dataset_id]);
  const change = async (selection: Record<string, unknown>) => {
    setError('');
    try {
      const state = await service<BusinessContext>(host, 'nw.session.select', selection, true);
      client.setQueryData(['agh-context'], state);
      if (selection.dataset_id !== undefined) useDatasetStore.getState().setDatasetId(state.selected.dataset_id ?? null);
      return true;
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); return false; }
  };
  return <div className="flex h-full min-h-0 flex-col bg-canvas text-ink md:flex-row">
    <aside className="flex shrink-0 flex-col gap-5 border-b border-line bg-panel p-4 md:w-64 md:border-r md:border-b-0">
      <nav aria-label="分析工作台导航" className="grid grid-cols-2 gap-1 md:grid-cols-1">
        {tabs.map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-pressed={tab === id}
          className={cx('flex items-center gap-3 rounded-control px-3 py-2.5 text-left text-sm transition-control',
            tab === id ? 'bg-selected font-medium text-accent' : 'text-muted hover:bg-hover hover:text-ink')}
          onClick={() => setTab(id)}><Icon className="size-4 shrink-0" aria-hidden="true"/>{label}</button>)}
      </nav>
      <section aria-label="数据与输出" className="grid grid-cols-2 gap-3 md:grid-cols-1">
        <div className="rounded-card border border-line bg-raised p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium"><Database className="size-4 text-accent" aria-hidden="true"/>输入数据</div>
          <p className="mb-3 break-all font-mono text-xs text-muted" title={current?.dataset?.absolute_path}>
            {current?.dataset?.absolute_path ?? '选择服务器上的数据集'}</p>
          <Button fullWidth onClick={() => setPicker('dataset')}>选择数据集<ArrowRight/></Button>
        </div>
        <div className="rounded-card border border-line bg-raised p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium"><FolderOutput className="size-4 text-accent" aria-hidden="true"/>分析输出</div>
          <p className="mb-3 break-all font-mono text-xs text-muted" title={current?.workspace?.root_path}>
            {current?.workspace?.root_path ?? '设置结果保存位置'}</p>
          <Button fullWidth onClick={() => setPicker('output')}>输出目录<ArrowRight/></Button>
        </div>
      </section>
      <div className="flex flex-wrap gap-1 md:mt-auto md:flex-col">
        <Button variant="ghost" className="justify-start" disabled={!current?.dataset} onClick={() => setPicker('files')}><Files/>浏览数据文件</Button>
        <Button variant="ghost" className="justify-start" onClick={() => setPicker('storage')}><Settings2/>存储与环境</Button>
      </div>
    </aside>
    <main className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-line px-5 py-4">
        <div className="min-w-0"><h2 className="text-title">{activeTab.label}</h2><p className="mt-1 text-xs text-muted">{activeTab.description}</p></div>
      </header>
      {(error || context.error) && <p role="alert" className="mx-5 mt-3 rounded-control border border-danger-line bg-danger-quiet p-3 text-xs text-danger">{error || context.error?.message}</p>}
      {context.isPending && <p role="status" className="px-5 py-3 text-xs text-muted">正在读取会话…</p>}
      {tab === 'tasks' && <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <aside className="flex max-h-48 shrink-0 flex-col border-b border-line lg:max-h-none lg:w-56 lg:border-r lg:border-b-0"><WorkflowHistory selectedId={selectedTask ?? current?.task?.task_id ?? workflow?.managed_task_id ?? workflow?.workflow_id ?? null}
        onSelect={task => { setSelectedTask(task.task_id ?? null); if (task.workflow_id) void change({ workflow_id: task.workflow_id }); }}/></aside>
      <section className="min-h-0 min-w-0 flex-1 overflow-auto p-5">
        {selectedTask && selectedTask !== current?.task?.task_id ? <ManagedTaskCard taskId={selectedTask} defaultExpanded disabled={sending}
          onRequestRevision={text => { setWorkbenchOpen(false); void host.session.commands.prompt(text); }}/>
          : current?.task ? <ConversationTaskWorkspace conversationId={current.conversation_id} task={current.task}
            messages={[]} workflowId={workflow?.workflow_id ?? null} workflow={workflow} sending={sending}
            onRequestRevision={text => { setWorkbenchOpen(false); void host.session.commands.prompt(text); }}/>
          : workflow ? <><WorkflowCard key={workflow.workflow_id} workflowId={workflow.workflow_id} defaultExpanded disabled={sending}/>
          <RunHistory key={'runs-' + workflow.workflow_id} workflowId={workflow.workflow_id}/></> :
          <div className="mx-auto flex max-w-lg flex-col items-center px-6 py-16 text-center">
            <div className="mb-5 rounded-panel border border-line bg-panel p-4"><Layers3 className="size-7 text-accent" aria-hidden="true"/></div>
            <h3 className="text-title">开始一次分析</h3>
            <p className="mt-2 text-sm leading-6 text-muted">选择数据集，在对话中描述分析需求，或从 Pipeline 创建任务。生成流程后，在这里调整输入、参数和运行资源。</p>
            <div className="mt-6 flex gap-3"><Button onClick={() => setPicker('dataset')}>添加输入数据</Button>
              <Button variant="primary" onClick={() => setTab('pipelines')}>浏览 Pipeline<ArrowRight/></Button></div>
          </div>}
      </section>
    </div>}
    {(tab === 'library' || tab === 'pipelines') && <section className="min-h-0 flex-1 overflow-auto p-5"><LibraryPanel key={tab}
      domain={tab === 'pipelines' ? 'pipelines' : 'skills'} selectedSkillIds={selectedSkills} disabled={false}
      onAdd={id => void change({ selected_skills: [...new Set([...selectedSkills, id])] })}
      onRemove={id => void change({ selected_skills: selectedSkills.filter(s => s !== id) })}
      onClear={() => void change({ selected_skills: [] })} onDetails={setSkill}
      onTaskCreated={wf => { void change({ workflow_id: wf.workflow_id }); setTab('tasks'); }}/></section>}
    {tab === 'data' && <section className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
      {!current?.data_operations.length && <p className="text-sm text-muted">在 AGH 提出查询、整理、改名或脚本处理需求。待运行方案、确认范围和反馈会显示在这里。</p>}
      {current?.data_operations.map(operation => <DataOperationCard key={operation.id} operation={operation}
        onRequestRevision={text => { setWorkbenchOpen(false); void host.session.commands.prompt(text); }}/>)}</section>}
    </main>
    {picker === 'dataset' && <DatasetPicker selectedDatasetId={current?.selected.dataset_id ?? null}
      onSelect={async dataset => { if (!await change({ dataset_id: dataset.dataset_id })) throw new Error('未能选择数据集，请重试。'); }} onClose={() => setPicker(null)}/>}
    {picker === 'output' && <WorkspacePanel workspaceId={current?.selected.workspace_id ?? null} conversationId={current?.conversation_id ?? null}
      datasetId={current?.selected.dataset_id ?? null} onSelect={async value => { await change({ workspace_id: value?.id ?? null }); }} onClose={() => setPicker(null)}/>}
    {picker === 'files' && current?.dataset && <DatasetFilesDrawer dataset={current.dataset} onClose={() => setPicker(null)}/>}
    {picker === 'storage' && <StoragePanel onClose={() => setPicker(null)}/>}
    {skill && <SkillDetails skill={skill} selected={selectedSkills.includes(skill.name)} disabled={false}
      onAdd={() => void change({ selected_skills: [...new Set([...selectedSkills, skill.name])] })}
      onRemove={() => void change({ selected_skills: selectedSkills.filter(s => s !== skill.name) })} onClose={() => setSkill(null)}/>}
  </div>;
}

function SessionPanels({ host, open, onClose }: { host: AGHHost; open: boolean; onClose: () => void }) {
  const [client] = useState(() => new QueryClient());
  return <QueryClientProvider client={client}>
    {open && <Dialog title={<span className="flex items-center gap-2"><LayoutDashboard className="size-4 text-accent" aria-hidden="true"/>NeuroWeave 分析工作台</span>}
      closeLabel="返回 AGH 对话" size="xl" className="h-[88dvh] !max-w-[1480px]" contentClassName="min-h-0 flex-1 overflow-auto md:overflow-hidden" onClose={onClose}>
      <Workspace host={host}/>
    </Dialog>}
  </QueryClientProvider>;
}

function useNativeBusy(host: AGHHost) {
  const [sending, setSending] = useState(false);
  useEffect(() => {
    const projection = host.session.projection;
    if (!projection) return;
    let live = true;
    const read = () => { void projection.read().then(value => { if (live && value.status === 'available') setSending(value.value?.opState != null); }).catch(() => {}); };
    const off = projection.subscribe(read); read();
    return () => { live = false; off(); };
  }, [host]);
  return sending;
}

export function BusinessToolbar({ host }: { host: AGHHost }) {
  const session = useSyncExternalStore(host.session.subscribe, host.session.getSnapshot);
  const theme = useSyncExternalStore(host.theme.subscribe, host.theme.getSnapshot);
  return <WorkbenchSurface><div className={'nw-workbench' + (theme === 'dark' ? ' dark' : '')}>
    <Button disabled={!session} onClick={() => setWorkbenchOpen(true)} className="whitespace-nowrap border-accent-line bg-accent-quiet text-accent"><LayoutDashboard/>NeuroWeave 工作台</Button>
  </div></WorkbenchSurface>;
}

export function NewSessionWorkbench({ host }: { host: AGHHost }) {
  const session = useSyncExternalStore(host.session.subscribe, host.session.getSnapshot);
  const theme = useSyncExternalStore(host.theme.subscribe, host.theme.getSnapshot);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  if (session) return null;
  const open = async () => {
    if (opening) return;
    setOpening(true); setError('');
    try { await ensureWorkbenchSession(host); setWorkbenchOpen(true); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setOpening(false); }
  };
  return <WorkbenchSurface><div className={'nw-workbench mb-5 flex flex-col items-center gap-2' + (theme === 'dark' ? ' dark' : '')}>
    <Button disabled={opening} onClick={() => void open()} className="border-accent-line bg-accent-quiet text-accent"><LayoutDashboard/>{opening ? '正在建立会话…' : 'NeuroWeave 工作台'}</Button>
    <p className="text-xs text-muted">发送消息前，选择本会话的数据集和输出目录</p>
    {error && <p role="alert" className="max-w-sm text-xs text-danger">{error}</p>}
  </div></WorkbenchSurface>;
}

/** A single root-scoped dialog survives the transition from draft to real session. */
export function WorkbenchController({ host }: { host: AGHHost }) {
  const session = useSyncExternalStore(host.session.subscribe, host.session.getSnapshot);
  const theme = useSyncExternalStore(host.theme.subscribe, host.theme.getSnapshot);
  const [openSession, setOpenSession] = useState<string>();
  useEffect(() => subscribeWorkbench(open => setOpenSession(open ? host.session.getSnapshot() : undefined)), [host]);
  useEffect(() => { useLanguageStore.getState().setLanguage('zh'); }, []);
  return <WorkbenchSurface><div className={'nw-workbench' + (theme === 'dark' ? ' dark' : '')}>
    {session && <SessionPanels key={session} host={host} open={openSession === session} onClose={() => setWorkbenchOpen(false)}/>}
  </div></WorkbenchSurface>;
}
