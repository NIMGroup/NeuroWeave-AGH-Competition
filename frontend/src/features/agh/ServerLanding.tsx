import { useQuery } from '@tanstack/react-query';
import { request } from '../../api/http';

type Overview = { mode: string; agh_url: string; revision: string; approval: string;
  execution: { slurm: { available: boolean; reason: string; worker_root: string; nodes: { name: string; state: string }[] } } };

export function ServerLanding() {
  const overview = useQuery({ queryKey: ['agh-server-overview'], queryFn: () => request<Overview>('/api/agh/overview'), refetchInterval: 10000 });
  const state = overview.data;
  return <main className="min-h-screen bg-canvas p-6 text-ink sm:p-12"><div className="mx-auto max-w-4xl space-y-6">
    <header className="space-y-3 border-b border-line pb-6">
      <p className="text-meta text-muted">NeuroWeave · 独立比赛版本</p>
      <h1 className="text-3xl font-semibold">AGH 服务器分析工作台</h1>
      <p className="text-sm text-muted">在 AGH 会话中描述需求，打开 NeuroWeave 工作台，选择服务器数据、编辑流程和资源，预检通过后点击运行。</p>
      <a className="inline-block rounded-control bg-accent-solid px-5 py-3 text-on-accent" href={state?.agh_url ?? 'http://127.0.0.1:4187'}>打开 AGH 工作台 ↗</a>
      <p className="text-xs text-muted">本机通过 SSH 转发访问服务器上的 AGH 页面。会话和模型由 AGH 管理，工作台操作不再弹出额外审批。</p>
    </header>
    <ol className="grid gap-3 text-sm sm:grid-cols-3">
      <li className="rounded-card border border-line p-4">选择数据、输出目录和专业指导，查询方法与 Pipeline。</li>
      <li className="rounded-card border border-line p-4">设置参数、输入映射和节点资源，预检通过后点击运行。</li>
      <li className="rounded-card border border-line p-4">查看真实任务与日志，由 AGH 调用验证工具并解释结果。</li>
    </ol>
    {overview.error && <p role="alert" className="text-danger">{overview.error.message}</p>}
    {state && <section className="space-y-3 rounded-card border border-line p-5 text-sm">
      <h2 className="font-semibold">服务器状态</h2>
      <p>Slurm：{state.execution.slurm.available ? '可用' : state.execution.slurm.reason}</p>
      <p>节点：{state.execution.slurm.nodes.map(node => `${node.name} (${node.state})`).join(' · ')}</p>
      <p className="text-xs text-muted">版本：{state.revision}</p>
    </section>}
  </div></main>;
}
