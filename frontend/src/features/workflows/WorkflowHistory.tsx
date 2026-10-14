import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, Database, FolderOpen, MessageSquare, Network, Search } from 'lucide-react';
import { request, errorText } from '../../api/http';
import { stateText } from '../../lib/stateText';
import { ui } from '../../lib/libraryText';
import { taskIndexUrl, taskIdentity } from './taskIndex';
import type { TaskEntry } from './taskIndex';
import { TaskStatusBadge } from './TaskStatusBadge';

export function WorkflowHistory({selectedId,onSelect}:{selectedId:string|null;onSelect:(task:TaskEntry)=>void}) {
  const [offset,setOffset]=useState(0),[q,setQ]=useState(''),[source,setSource]=useState(''),[status,setStatus]=useState('');
  const list=useQuery({queryKey:['task-index',offset,q,source,status],
    queryFn:()=>request<{items:TaskEntry[];has_more:boolean}>(taskIndexUrl(offset,q,source,status)),refetchInterval:5000});
  const field='w-full min-w-0 rounded-control border border-line bg-raised p-2';
  const hasFilters=!!(q||source||status);
  const reset=()=>{setQ('');setSource('');setStatus('');setOffset(0);};
  const date=(value:string)=>new Date(value).toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  return <div className="min-h-0 flex-1 overflow-auto p-3 text-xs">
    <div className="sticky top-0 z-10 space-y-2 bg-panel pb-3">
      <label className="relative block"><Search className="absolute left-2.5 top-2.5 size-4 text-subtle" aria-hidden="true"/>
        <input aria-label={ui('搜索任务')} placeholder={ui('搜索任务名称')} value={q} onChange={e=>{setQ(e.target.value);setOffset(0);}} className={`${field} pl-8`}/></label>
      <div className="grid grid-cols-2 gap-2">
        <select aria-label={ui('任务来源')} className={field} value={source} onChange={e=>{setSource(e.target.value);setOffset(0);}}>
          <option value="">{ui('全部来源')}</option><option value="conversation">{ui('对话')}</option><option value="pipeline">Pipeline</option>
        </select>
        <select aria-label={ui('任务状态')} className={field} value={status} onChange={e=>{setStatus(e.target.value);setOffset(0);}}>
          <option value="">{ui('全部状态')}</option><option value="draft">{ui('待执行')}</option><option value="active">{ui('执行中')}</option>
          <option value="succeeded">{stateText('succeeded')}</option><option value="failed">{stateText('failed')}</option><option value="cancelled">{stateText('cancelled')}</option>
        </select>
      </div>
      <div className="flex items-center justify-between text-micro text-subtle"><span>{ui('本页 {0} 个任务',list.data?.items.length??0)}</span>
        {hasFilters&&<button className="text-accent hover:underline" onClick={reset}>{ui('清除筛选')}</button>}</div>
    </div>
    {list.isError&&<div role="alert" className="mb-3 rounded-control bg-danger-quiet p-3 text-danger">{errorText(list.error,ui('读取任务列表失败'))}<button className="ml-2 underline" onClick={()=>void list.refetch()}>{ui('重试')}</button></div>}
    {list.isPending&&<p role="status" className="py-8 text-center text-subtle">{ui('正在读取任务…')}</p>}
    <ul className="space-y-2" aria-label={ui('任务列表')}>
      {list.data?.items.map(task=><li key={taskIdentity(task)}><button aria-current={selectedId===taskIdentity(task)?'true':undefined}
        className={`group block w-full rounded-card border p-3 text-left transition hover:bg-hover ${selectedId===taskIdentity(task)?'border-accent-line bg-selected ring-1 ring-accent-line':'border-line bg-raised'}`}
        onClick={()=>onSelect(task)}>
        <span className="mb-2 flex items-center justify-between gap-2"><span className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-micro text-subtle">{task.source==='pipeline'?<Network className="size-3" aria-hidden="true"/>:<MessageSquare className="size-3" aria-hidden="true"/>}{task.source==='pipeline'?'Pipeline':ui('对话')}</span><TaskStatusBadge task={task}/></span>
        <span className="flex items-start gap-2"><strong className="line-clamp-2 min-w-0 flex-1 break-words text-xs leading-5" title={task.title}>{task.title}</strong><ArrowRight className="mt-1 size-3 shrink-0 text-subtle group-hover:text-accent" aria-hidden="true"/></span>
        {task.dataset_label&&<span className="mt-2 flex items-center gap-1.5 text-micro text-muted"><Database className="size-3 shrink-0" aria-hidden="true"/><span className="truncate">{task.dataset_label}</span></span>}
        {task.workspace_path&&<span className="mt-1 flex items-center gap-1.5 text-micro text-subtle" title={task.workspace_path}><FolderOpen className="size-3 shrink-0" aria-hidden="true"/><span className="truncate">{task.workspace_path}</span></span>}
        <span className="mt-2 flex items-center justify-between gap-2 border-t border-line/60 pt-2 text-micro text-subtle"><span>{ui('{0} 次运行',task.run_count)}</span><time dateTime={task.updated_at} title={new Date(task.updated_at).toLocaleString()}>{date(task.updated_at)}</time></span>
      </button></li>)}
    </ul>
    {!list.isPending&&!list.isError&&!list.data?.items.length&&<div className="rounded-control border border-dashed border-line px-3 py-8 text-center text-subtle"><p>{hasFilters?ui('没有符合筛选条件的任务'):ui('暂无任务')}</p>{hasFilters&&<button className="mt-3 text-accent hover:underline" onClick={reset}>{ui('清除筛选')}</button>}</div>}
    <div className="mt-3 flex items-center justify-between gap-2 text-micro text-subtle">
      <button className="rounded-control border border-line px-2 py-1.5 disabled:opacity-40" disabled={!offset||list.isFetching} onClick={()=>setOffset(v=>Math.max(0,v-30))}>{ui('上一页')}</button><span>{ui('第 {0} 页',offset/30+1)}</span>
      <button className="rounded-control border border-line px-2 py-1.5 disabled:opacity-40" disabled={!list.data?.has_more||list.isFetching} onClick={()=>setOffset(v=>v+30)}>{ui('下一页')}</button>
    </div>
  </div>;
}
