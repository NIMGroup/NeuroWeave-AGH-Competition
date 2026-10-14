import { useEffect, useState, useSyncExternalStore } from 'react';
import { Copy, FolderPlus, Pencil, RotateCcw, Settings2, Trash2 } from 'lucide-react';
import { Button } from '../../ui/Button';
import { Dialog } from '../../ui/Dialog';
import { WorkbenchSurface } from './WorkbenchSurface';
import { managementService } from './host';
import type { AGHHost } from './host';
import { cx } from '../../ui/cx';

type Session = { sessionId: string; title?: string; cwd?: string; archived?: boolean };
type Workspace = { path: string; name: string; available: boolean; removed?: boolean };
type Preference = { path: string; name: string; description: string; removed?: boolean };
type Records = { sessions: Session[]; workspaces: Workspace[]; currentId?: string };
const events = new EventTarget();
function showManager() { events.dispatchEvent(new Event('open')); }

export function RecordManagerButton({ host }: { host: AGHHost }) {
  const theme = useSyncExternalStore(host.theme.subscribe, host.theme.getSnapshot);
  return <WorkbenchSurface><div className={'nw-workbench w-full' + (theme === 'dark' ? ' dark' : '')}>
    <Button fullWidth variant="ghost" className="justify-start" onClick={showManager}><Settings2/>工作区与会话管理</Button>
  </div></WorkbenchSurface>;
}

export function RecordManagerController({ host }: { host: AGHHost }) {
  const theme = useSyncExternalStore(host.theme.subscribe, host.theme.getSnapshot);
  const [open, setOpen] = useState(false);
  useEffect(() => { const show = () => setOpen(true); events.addEventListener('open', show); return () => events.removeEventListener('open', show); }, []);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      let delay = 2000;
      try {
        const prefs = await managementService<{ workspaces: Preference[] }>(host, 'nw.workspace.query', {});
        if (live) await host.session.manage?.('workspace-labels', prefs);
        delay = 30000;
      } catch { /* The module may mount before the native connection is ready. */ }
      if (live) timer = setTimeout(() => { void refresh(); }, delay);
    };
    void refresh();
    return () => { live = false; clearTimeout(timer); };
  }, [host]);
  return <WorkbenchSurface><div className={'nw-workbench' + (theme === 'dark' ? ' dark' : '')}>
    {open && <Manager host={host} onClose={() => setOpen(false)}/>}
  </div></WorkbenchSurface>;
}

function Manager({ host, onClose }: { host: AGHHost; onClose: () => void }) {
  const [tab, setTab] = useState<'workspaces' | 'sessions' | 'deleted' | 'removedWorkspaces'>('sessions');
  const [records, setRecords] = useState<Records>();
  const [preferences, setPreferences] = useState<Preference[]>([]);
  const [query, setQuery] = useState('');
  const [workspaceFilter, setWorkspaceFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<{ kind: 'session' | 'workspace'; id: string; name: string; description: string }>();
  const [deleting, setDeleting] = useState<Session>();
  const [removing, setRemoving] = useState<Workspace>();
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState('');
  const [notice, setNotice] = useState('');
  const manage = (operation: string, input?: Record<string, unknown>) => {
    if (!host.session.manage) throw new Error('管理入口尚未加载，请刷新 AGH 页面');
    return host.session.manage(operation, input);
  };
  const refresh = async () => {
    const prefs = await managementService<{ workspaces: Preference[] }>(host, 'nw.workspace.query', {});
    await manage('workspace-labels', prefs);
    setPreferences(prefs.workspaces);
    setRecords(await manage('list') as Records);
  };
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => { void refresh().catch(failure => { if (live) setError(String(failure instanceof Error ? failure.message : failure)); }); }, 0);
    return () => { live = false; clearTimeout(timer); };
    // Mount a fresh manager each time; native selection is deliberately independent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const act = async (action: () => Promise<unknown>, message: string) => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await action(); await refresh(); setNotice(message); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  };
  const save = () => void act(async () => {
    if (!editing) return;
    if (editing.kind === 'session') await manage('rename', { session_id: editing.id, title: editing.name });
    else {
      const prefs = await managementService<{ workspaces: Preference[] }>(host, 'nw.workspace.edit',
        { path: editing.id, name: editing.name, description: editing.description }, true);
      await manage('workspace-labels', prefs);
    }
    setEditing(undefined);
  }, '名称与设置已保存');
  const removeWorkspace = async (workspace: Workspace, removed: boolean) => {
    const prefs = await managementService<{ workspaces: Preference[] }>(host, 'nw.workspace.remove', { path: workspace.path, removed }, true);
    await manage('workspace-labels', prefs);
  };
  const workspaceMode = tab === 'workspaces' || tab === 'removedWorkspaces';
  const filter = query.trim().toLocaleLowerCase();
  const sessions = records?.sessions.filter(row => Boolean(row.archived) === (tab === 'deleted')
    && (!workspaceFilter || row.cwd === workspaceFilter)
    && `${row.title ?? row.sessionId} ${row.cwd ?? ''}`.toLocaleLowerCase().includes(filter)) ?? [];
  const workspaces = records?.workspaces.filter(row => Boolean(row.removed) === (tab === 'removedWorkspaces')
    && `${row.name} ${row.path}`.toLocaleLowerCase().includes(filter)) ?? [];
  return <Dialog title="工作区与会话管理" closeLabel="关闭管理" size="xl" className="h-[78dvh] !max-w-[1080px]"
    contentClassName="min-h-0 flex-1 overflow-auto" onClose={() => { if (!busy) onClose(); }}>
    <div className="space-y-4 p-5" data-agh-record-manager>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav aria-label="管理分类" className="flex gap-1 rounded-control bg-panel p-1">
          {([['sessions', '会话'], ['workspaces', '工作区'], ['deleted', '已删除会话'], ['removedWorkspaces', '已移除工作区']] as const).map(([id, label]) =>
            <Button key={id} disabled={busy} aria-pressed={tab === id} variant={tab === id ? 'primary' : 'ghost'}
              onClick={() => { setTab(id); setEditing(undefined); setDeleting(undefined); setRemoving(undefined); setQuery(''); }}>{label}</Button>)}
        </nav>
        <Button disabled={busy} onClick={() => void act(async () => {}, '列表已刷新')}><RotateCcw/>刷新</Button>
      </div>
      <div className="flex flex-wrap gap-3">
        <input className="min-w-48 flex-1 rounded-control border border-line bg-canvas px-3 py-2" aria-label="搜索工作区或会话"
          placeholder="搜索名称或服务器路径" value={query} onChange={event => setQuery(event.target.value)}/>
        {!workspaceMode && <select className="max-w-sm rounded-control border border-line bg-canvas px-3 py-2" aria-label="按工作区筛选会话"
          value={workspaceFilter} onChange={event => setWorkspaceFilter(event.target.value)}><option value="">全部工作区</option>
          {[...new Set(records?.sessions.map(row => row.cwd).filter(Boolean))].map(cwd => <option key={cwd} value={cwd}>
            {records?.workspaces.find(row => row.path === cwd)?.name ?? cwd}</option>)}</select>}
        {tab === 'workspaces' && <Button disabled={busy} onClick={() => setAdding(true)}><FolderPlus/>添加工作区</Button>}
      </div>
      {error && <p role="alert" className="rounded-control border border-danger-line bg-danger-quiet p-3 text-sm text-danger">{error}</p>}
      {notice && <p role="status" className="text-sm text-accent">{notice}</p>}
      {editing && <form className="space-y-3 rounded-card border border-accent-line bg-accent-quiet p-4" onSubmit={event => { event.preventDefault(); save(); }}>
        <label className="block text-sm">{editing.kind === 'workspace' ? '工作区名称' : '会话名称'}<input required maxLength={80} autoFocus disabled={busy}
          className="mt-2 block w-full rounded-control border border-line bg-canvas px-3 py-2" value={editing.name}
          onChange={event => setEditing({ ...editing, name: event.target.value })}/></label>
        {editing.kind === 'workspace' && <><p className="break-all font-mono text-xs text-muted">服务器目录：{editing.id}</p>
          <label className="block text-sm">工作区说明<textarea maxLength={2000} disabled={busy} rows={3}
            className="mt-2 block w-full rounded-control border border-line bg-canvas px-3 py-2" value={editing.description}
            onChange={event => setEditing({ ...editing, description: event.target.value })}/></label>
          <p className="text-xs text-muted">改名只更改显示名称；已有会话继续使用原目录。选择其他目录时，请添加一个工作区并新建会话。</p></>}
        <div className="flex gap-2"><Button type="submit" variant="primary" disabled={busy || !editing.name.trim()}>保存名称与设置</Button>
          <Button disabled={busy} onClick={() => setEditing(undefined)}>取消编辑</Button></div>
      </form>}
      {adding && tab === 'workspaces' && <form className="space-y-3 rounded-card border border-line p-4" onSubmit={event => {
        event.preventDefault(); void act(async () => {
          const added = await manage('workspace-add', { path }) as Workspace;
          if (preferences.some(pref => pref.path === added.path && pref.removed)) await removeWorkspace(added, false);
          setAdding(false); setPath('');
        }, '工作区已添加，可用于新会话');
      }}><label className="block text-sm">服务器工作区目录<input autoFocus required disabled={busy}
        className="mt-2 block w-full rounded-control border border-line bg-canvas px-3 py-2" placeholder="/home/你的目录" value={path}
        onChange={event => setPath(event.target.value)}/></label><div className="flex gap-2"><Button type="submit" variant="primary" disabled={busy}>添加目录</Button>
        <Button disabled={busy} onClick={() => setAdding(false)}>取消添加</Button></div></form>}
      {deleting && <section aria-label="确认删除会话" className="space-y-3 rounded-card border border-danger-line bg-danger-quiet p-4">
        <p className="font-medium">删除“{deleting.title ?? deleting.sessionId}”？</p>
        <p className="text-sm text-muted">会话将从列表移入“已删除会话”，可以恢复。数据文件、分析结果和流程记录保留。</p>
        <div className="flex gap-2"><Button variant="danger" disabled={busy} onClick={() => void act(async () => {
          await managementService(host, 'nw.session.removalcheck', { session_id: deleting.sessionId });
          await manage('archive', { session_id: deleting.sessionId, archived: true }); setDeleting(undefined);
        }, '会话已删除，可在“已删除会话”恢复')}><Trash2/>确认删除</Button>
          <Button disabled={busy} onClick={() => setDeleting(undefined)}>取消删除</Button></div>
      </section>}
      {removing && <section aria-label="确认移除工作区" className="space-y-3 rounded-card border border-danger-line bg-danger-quiet p-4">
        <p className="font-medium">移除工作区“{removing.name}”？</p>
        <p className="break-all font-mono text-xs text-muted">{removing.path}</p>
        <p className="text-sm text-muted">工作区将移入“已移除工作区”，侧栏和新会话选择器将隐藏它。服务器目录、数据及关联会话保留，已有会话仍可在“会话”页打开。</p>
        <div className="flex gap-2"><Button variant="danger" disabled={busy} onClick={() => void act(async () => {
          await removeWorkspace(removing, true); setRemoving(undefined);
        }, '工作区已移除，可在“已移除工作区”恢复')}><Trash2/>确认移除</Button>
          <Button disabled={busy} onClick={() => setRemoving(undefined)}>取消移除</Button></div>
      </section>}
      {!records && !error && <p role="status" className="text-sm text-muted">正在读取 AGH 工作区与会话…</p>}
      <div className="space-y-2">
        {workspaceMode ? workspaces.map(row => <section key={row.path} data-workspace-path={row.path}
          className="flex flex-wrap items-start justify-between gap-3 rounded-card border border-line bg-panel p-4">
          <div className="min-w-0 flex-1"><h3 className="break-words font-medium">{row.name}</h3><p className="mt-1 break-all font-mono text-xs text-muted">{row.path}</p>
            {preferences.find(pref => pref.path === row.path)?.description && <p className="mt-2 whitespace-pre-wrap text-sm text-muted">{preferences.find(pref => pref.path === row.path)!.description}</p>}
            {!row.available && <p className="mt-2 text-xs text-danger">目录暂不可用，请检查服务器权限或挂载</p>}</div>
          <div className="flex flex-wrap gap-1">{row.removed ? <Button disabled={busy} onClick={() => void act(
            () => removeWorkspace(row, false), '工作区已恢复')}><RotateCcw/>恢复工作区</Button> : <>
            <Button disabled={busy} onClick={() => { setDeleting(undefined); setRemoving(undefined); setEditing({ kind: 'workspace', id: row.path, name: row.name,
              description: preferences.find(pref => pref.path === row.path)?.description ?? '' }); }}><Pencil/>编辑工作区</Button>
            <Button disabled={busy} onClick={() => { setDeleting(undefined); setEditing(undefined); setRemoving(row); }}><Trash2/>删除工作区</Button>
          </>}</div>
        </section>) : sessions.map(row => <section key={row.sessionId} data-managed-session={row.sessionId}
          className={cx('flex flex-wrap items-start justify-between gap-3 rounded-card border bg-panel p-4', row.sessionId === records?.currentId ? 'border-accent-line' : 'border-line')}>
          <div className="min-w-0 flex-1"><h3 className="break-words font-medium">{row.title ?? `会话 ${row.sessionId.slice(0, 8)}`}{row.sessionId === records?.currentId && <span className="ml-2 text-xs text-accent">当前会话</span>}</h3>
            <p className="mt-1 break-all font-mono text-xs text-muted">{row.cwd ?? '未关联工作区'}</p></div>
          <div className="flex flex-wrap gap-1">{tab === 'deleted' ? <Button disabled={busy} onClick={() => void act(
            () => manage('archive', { session_id: row.sessionId, archived: false }), '会话已恢复')}><RotateCcw/>恢复会话</Button> : <>
            <Button disabled={busy} onClick={() => void act(async () => { await manage('open', { session_id: row.sessionId }); onClose(); }, '')}>打开</Button>
            <Button disabled={busy} onClick={() => { setDeleting(undefined); setEditing({ kind: 'session', id: row.sessionId, name: row.title ?? '', description: '' }); }}><Pencil/>改名</Button>
            <Button disabled={busy} onClick={() => void act(() => manage('fork', { session_id: row.sessionId, title: row.title ?? row.sessionId }), '已复制会话')}><Copy/>复制</Button>
            <Button disabled={busy} onClick={() => { setEditing(undefined); setDeleting(row); }}><Trash2/>删除</Button></>}</div>
        </section>)}
      </div>
      {records && !(workspaceMode ? workspaces.length : sessions.length) && <p className="py-8 text-center text-sm text-muted">{tab === 'deleted' ? '没有已删除会话' : tab === 'removedWorkspaces' ? '没有已移除工作区' : '没有符合筛选条件的记录'}</p>}
    </div>
  </Dialog>;
}
