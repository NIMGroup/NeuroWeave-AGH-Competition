import { setRequestTransport } from '../../api/http';

export type AGHHost = {
  slots: { register: (name: string, component: unknown, options?: Record<string, unknown>) => () => void };
  agnes: { services: { call: (name: string, input: Record<string, unknown>) => Promise<unknown> } };
  commands: {
    registerEffect: (command: { id: string; title: string; service: string }) => () => void;
    execute: (id: string, input: Record<string, unknown>) => Promise<unknown>;
  };
  session: {
    getSnapshot: () => string | undefined;
    subscribe: (listener: () => void) => () => void;
    /** Competition Web adapter: create and select an empty official AGH session. */
    ensure?: () => Promise<string>;
    /** Isolated Web adapter using native rename/archive/fork and workspace APIs. */
    manage?: (operation: string, input?: Record<string, unknown>) => Promise<unknown>;
    projection?: { read: () => Promise<{ status: string; value?: { opState?: unknown } }>; subscribe: (listener: () => void) => () => void };
    commands: { prompt: (input: unknown) => Promise<unknown>; cancel: () => Promise<void> };
  };
  effect: (callback: () => () => void) => void;
  theme: { getSnapshot: () => 'light' | 'dark'; subscribe: (listener: () => void) => () => void };
};

type Bridge<T> = { ok: boolean; result: T; error?: string };
export async function managementService<T>(host: AGHHost, name: string, input: Record<string, unknown>, effect = false): Promise<T> {
  if (!host.session.manage) throw new Error('管理入口尚未加载，请刷新 AGH 页面');
  const value = await host.session.manage('service', { name, input, effect });
  const bridge = value as Bridge<T>;
  if (!bridge.ok) throw new Error(bridge.error ?? '管理服务暂不可用');
  return bridge.result;
}
export type Action = { action_id: string; action_digest: string; state: string;
  proposal?: unknown; result?: { status: number; body: unknown } };
const events = new EventTarget();
export async function ensureWorkbenchSession(host: AGHHost): Promise<string> {
  const existing = host.session.getSnapshot();
  if (existing) return existing;
  if (!host.session.ensure) throw new Error('新会话入口尚未加载，请刷新 AGH 页面后重试');
  const created = await host.session.ensure();
  if (host.session.getSnapshot() !== created) throw new Error('会话已切换，请重新打开当前会话的工作台');
  return created;
}
export function setWorkbenchOpen(open: boolean) {
  events.dispatchEvent(new CustomEvent('open', { detail: open }));
}
export function subscribeWorkbench(listener: (open: boolean) => void) {
  const callback = (event: Event) => listener((event as CustomEvent<boolean>).detail);
  events.addEventListener('open', callback);
  return () => events.removeEventListener('open', callback);
}
export function service<T>(host: AGHHost, name: string, input: Record<string, unknown>, effect = false): Promise<T> {
  const sessionKey = host.session.getSnapshot();
  if (!sessionKey) return Promise.reject(new Error('请先在 AGH 创建或选择会话'));
  const args = { session_key: sessionKey, input };
  const result = effect ? host.commands.execute(name.replaceAll('.', '-'), args) : host.agnes.services.call(name, args);
  return result.then(value => {
    const bridge = value as Bridge<T>;
    if (!bridge.ok) throw new Error(bridge.error ?? 'NeuroWeave service unavailable');
    return bridge.result;
  });
}

const executionPath = /^\/api\/(?:conversations\/[^/]+\/task\/end|workflows\/[^/]+\/runs|runs\/[^/]+\/(?:cancel|retry|recover-freesurfer)|data-operations\/[^/]+\/(?:confirm|run-script|cancel|undo)|fs\/(?:operations\/[^/]+\/(?:confirm|cancel)|trash\/[^/]+\/restore))$/;
export function installTransport(host: AGHHost) {
  type PlanningJob = { operation_id: string; status: string; message: string; session: string;
    workflowId: string; expectedRevision: number; settled: boolean; startedAt: number;
    result?: unknown; error?: { message: string }; cancelRequested?: boolean };
  const planningJobs = new Map<string, PlanningJob>();
  const executionRequests = new Map<string, Promise<{ status: number; body: unknown }>>();
  const readPlanningJob = async (job: PlanningJob) => {
    if (host.session.getSnapshot() !== job.session) throw new Error('请回到原 AGH 会话查看该规划任务');
    if (!['succeeded', 'failed', 'cancelled'].includes(job.status)) {
      const current = await service<{ workflow: { workflow_id: string; revision: number } | null }>(host, 'nw.session.context', {});
      if (current.workflow?.workflow_id === job.workflowId && current.workflow.revision > job.expectedRevision) {
        job.status = 'succeeded'; job.result = current.workflow; job.message = 'AGH 已保存新流程版本';
        setWorkbenchOpen(true);
      } else if (job.settled) {
        job.status = job.cancelRequested ? 'cancelled' : 'failed';
        job.message = job.cancelRequested ? 'AGH 规划已停止，原版本保留' : job.error?.message ?? 'AGH 本轮未修订流程，原版本保留';
      } else if (Date.now() - job.startedAt > 15 * 60 * 1000) {
        job.status = 'failed'; job.message = '规划尚未返回，请查看原 AGH 会话，不要重复提交';
      }
    }
    return { operation_id: job.operation_id, status: job.status, message: job.message, result: job.result, error: job.error };
  };
  for (const name of ['nw.panel.edit', 'nw.session.select', 'nw.action.prepare', 'nw.action.submit', 'nw.workspace.edit']) {
    const titles: Record<string, string> = { 'nw.panel.edit': '保存 NeuroWeave 草稿或配置',
      'nw.session.select': '更新本会话的数据、目录或流程选择', 'nw.action.prepare': '准备具体执行方案，尚不计算',
      'nw.action.submit': '提交工作台已授权的具体执行方案',
      'nw.workspace.edit': '保存工作区名称和说明' };
    host.commands.registerEffect({ id: name.replaceAll('.', '-'), title: titles[name], service: name });
  }
  setRequestTransport(async (path, init) => {
    const method = init.method ?? 'GET';
    const input = { path, method, body: init.body ? JSON.parse(String(init.body)) : null };
    const taskReplan = path.match(/^\/api\/tasks\/([^/]+)\/replan$/);
    if (method === 'POST' && taskReplan) {
      const session = host.session.getSnapshot();
      const response = await service<{ status: number; body: { task?: { task_id: string; goal: string; task_revision?: number; user_closure?: unknown; user_completion?: unknown } } }>(host, 'nw.panel.query', {
        path: '/api/tasks/' + taskReplan[1], method: 'GET', body: null });
      const current = response.body.task;
      if (response.status >= 400 || !current) throw new Error('无法读取当前任务，请刷新当前会话后重试');
      if (host.session.getSnapshot() !== session) throw new Error('会话已切换，请回到原会话更新方案');
      if (current.user_closure || current.user_completion) throw new Error('目标已归档，请提出下一个目标');
      setWorkbenchOpen(false);
      await host.session.commands.prompt('请按已保存的当前任务目标更新同一方案。先 nw_context 读取任务 ' + current.task_id +
        ' 的最新完整目标、范围和完成条件，再查询真实输出端口与 nw_method_routes，调用 nw_prepare_workflow 保存同一任务的新方案版本。' +
        '保留兼容输入、参数与输出选择；不扫描确认参数，不运行计算。当前目标：' + current.goal);
      if (host.session.getSnapshot() !== session) throw new Error('会话已切换，请回到原会话查看新方案');
      return { status: 202, body: { status: 'completed', task_id: current.task_id } };
    }
    const planningOperation = path.match(/^\/api\/operations\/(agh_plan_[a-f0-9]+)(\/cancel)?$/);
    if (planningOperation) {
      const job = planningJobs.get(planningOperation[1]);
      if (!job) throw new Error('该 AGH 规划记录已过期，请在原会话查看流程版本');
      const state = await readPlanningJob(job);
      if (method === 'POST' && planningOperation[2] && !['succeeded', 'failed', 'cancelled'].includes(job.status)) {
        job.cancelRequested = true;
        await host.session.commands.cancel();
        job.status = 'cancel_requested'; job.message = '正在停止本会话的 AGH 规划';
        return { status: 202, body: { ...state, status: job.status, message: job.message } };
      }
      if (method !== 'GET' && !(method === 'POST' && planningOperation[2])) throw new Error('不支持的 AGH 规划操作');
      return { status: 200, body: state };
    }
    const planningJob = path.match(/^\/api\/workflows\/([^/]+)\/(replan|complete-dependencies)-jobs$/);
    if (method === 'POST' && planningJob) {
      const session = host.session.getSnapshot();
      await service(host, 'nw.session.select', { workflow_id: planningJob[1] }, true);
      setWorkbenchOpen(false);
      const job: PlanningJob = { operation_id: 'agh_plan_' + crypto.randomUUID().replaceAll('-', ''),
        status: 'running', message: planningJob[2] === 'replan' ? 'AGH 正在重新规划' : 'AGH 正在补齐必要依赖',
        session: session!, workflowId: planningJob[1], expectedRevision: input.body.expected_revision,
        settled: false, startedAt: Date.now() };
      planningJobs.set(job.operation_id, job);
      void host.session.commands.prompt(`请用 AGH ${planningJob[2] === 'replan' ? '重新规划' : '补齐必要依赖'}当前 NeuroWeave 流程 ${job.workflowId}。先 nw_read_workflow 读取真实目标、输入、参数和用户限制；以 3003 规划机制查询 nw_method_routes，再调用 nw_prepare_workflow，只传 goal、route_id、workflow_id=${job.workflowId} 和 expected_revision=${job.expectedRevision}，修订同一流程草稿。保留兼容的已选输入、参数和输出，不自动选择其他文件，不运行计算。参数由用户在流程卡片选择，直接保存新版本后停止。`)
        .then(() => { job.settled = true; }, error => { job.settled = true; job.error = { message: error instanceof Error ? error.message : String(error) }; });
      return { status: 202, body: { operation_id: job.operation_id, status: job.status, message: job.message } };
    }
    const planning = path.match(/^\/api\/workflows\/([^/]+)\/(replan|complete-dependencies)$/);
    if (method === 'POST' && planning) {
      const session = host.session.getSnapshot();
      await service(host, 'nw.session.select', { workflow_id: planning[1] }, true);
      setWorkbenchOpen(false);
      const turn = host.session.commands.prompt(`请用 AGH ${planning[2] === 'replan' ? '重新规划' : '补齐必要依赖'}当前 NeuroWeave 流程 ${planning[1]}。先 nw_read_workflow 查看实际目标、输入、参数、输出与约束，再检索真实方法，调用 nw_prepare_workflow，只传 goal、route_id、workflow_id=${planning[1]} 和 expected_revision=${input.body.expected_revision}，修订同一流程草稿。保留用户已有的兼容选择，参数由用户在卡片选择，不自行运行，返回新版本后停止。`);
      let settled = false;
      void turn.finally(() => { settled = true; }).catch(() => {});
      const deadline = Date.now() + 15 * 60 * 1000;
      while (Date.now() < deadline) {
        if (host.session.getSnapshot() !== session) throw new Error('会话已切换，请在原会话查看规划结果');
        const current = await service<{ workflow: { workflow_id: string; revision: number } | null }>(host, 'nw.session.context', {});
        if (current.workflow?.workflow_id === planning[1] && current.workflow.revision > input.body.expected_revision) {
          setWorkbenchOpen(true); return { status: 200, body: current.workflow };
        }
        if (settled) throw new Error('AGH 本轮未修订流程，原版本保留');
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
      throw new Error('规划尚未返回，请查看原 AGH 会话');
    }
    if (method !== 'GET' && executionPath.test(path.split('?')[0])) {
      const session = host.session.getSnapshot();
      const key = session + ':' + method + ':' + path;
      const existing = executionRequests.get(key);
      if (existing) return existing;
      let resolved = false;
      let submitAttempted = false;
      const submission = (async () => {
        const action = await service<Action>(host, 'nw.action.prepare', input, true);
        if (host.session.getSnapshot() !== session) {
          resolved = true;
          throw new Error('会话已切换，尚未提交；请回到原会话运行');
        }
        // This registered AGH effect performs the already authorized frozen
        // action. A model response is never a submission receipt or run state.
        let current: Action | undefined;
        try {
          submitAttempted = true;
          current = await service<Action>(host, 'nw.action.submit', {
            action_id: action.action_id, action_digest: action.action_digest }, true);
        } catch { /* A lost receipt is reconciled by the same action, never re-submitted. */ }
        const deadline = Date.now() + 2 * 60 * 1000;
        while (Date.now() < deadline) {
          if (current?.state === 'completed' && current.result) {
            resolved = true;
            return current.result;
          }
          if (current?.state === 'unknown')
            throw new Error(`提交状态待核对（${action.action_id}），请查看原运行记录，勿重复提交`);
          if (host.session.getSnapshot() !== session)
            throw new Error('会话已切换；提交仍在后台处理，请回原会话查看运行，勿重复提交');
          try { current = await service<Action>(host, 'nw.action.status', { action_id: action.action_id }); }
          catch { /* Status queries can retry; execution requests cannot. */ }
          if (current?.state === 'completed' || current?.state === 'unknown') continue;
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
        throw new Error(`提交结果暂未返回（${action.action_id}），请查看工作台实时运行记录，勿重复提交`);
      })().finally(() => { if (resolved || !submitAttempted) executionRequests.delete(key); });
      executionRequests.set(key, submission);
      return submission;
    }
    return service(host, method === 'GET' ? 'nw.panel.query' : 'nw.panel.edit', input, method !== 'GET');
  });
  host.effect(() => () => setRequestTransport(null));
}
