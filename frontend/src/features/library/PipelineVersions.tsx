import { stateText } from '../../lib/stateText';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import * as api from './libraryApi';
import { ui } from '../../lib/libraryText';
import { errorText } from '../../api/http';
import { refreshPipelineViews } from './libraryQueries';
export function PipelineVersions({pipeline,onSelectVersion,onEdit,readOnly=false}:{pipeline:api.Pipeline;onSelectVersion:(version:string)=>void;onEdit:(value:api.Pipeline)=>void;readOnly?:boolean}){
 const client=useQueryClient();
 const [error,setError]=useState(''),[reviewed,setReviewed]=useState(false),[busy,setBusy]=useState(false);
 const versions=useQuery({queryKey:['pipeline-versions',pipeline.id],queryFn:()=>api.versions(pipeline.id)});
 const migration=useQuery({queryKey:['pipeline-migration',pipeline.id,pipeline.version],queryFn:()=>api.migration(pipeline.id,pipeline.version),enabled:!!pipeline.editor});
 const act=async()=>{setBusy(true);setError('');try{await api.lifecycle(pipeline.id,pipeline.version,pipeline.status==='archived'?'published':'archived');await refreshPipelineViews(client,pipeline.id);}catch(e){setError(errorText(e,ui('更新版本状态失败')));}finally{setBusy(false);}};
 return <div className="my-3 space-y-2 rounded border border-line p-2 text-xs">
  <p>{ui('当前版本')} v{pipeline.version} · {stateText(pipeline.status??'published')}</p>
  <p>{ui('新流程只检索最新发布且方法版本一致的 Pipeline；已有运行继续使用其快照。')}</p>
  <div className="flex flex-wrap gap-2">{versions.data?.items.map(v=><button key={v.version} className="rounded border border-line px-2 py-1" onClick={()=>onSelectVersion(v.version)}>v{v.version} · {stateText(v.status??'published')}</button>)}
  {!readOnly&&pipeline.id.startsWith('user-')&&<button className="rounded border border-line px-2 py-1" disabled={busy} onClick={()=>void act()}>{ui(pipeline.status==='archived'?'恢复发布':'归档此版本')}</button>}</div>
  {!!migration.data?.changes.length&&<div><p className="text-warning">{ui('引用的方法已升级，请比较接口后重新发布。')}</p>{migration.data.changes.map(c=><p key={c.step_id}>{c.step_id} / {c.method_id} · {c.from_version} → {c.to_version}</p>)}<label className="flex gap-2"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>{ui('我将按最新方法重新检查参数、输入和输出')}</label></div>}
  {migration.data?.errors.map(e=><p key={e} className="break-words text-warning">{e}</p>)}
  {!readOnly&&pipeline.editor&&<button className="rounded border border-line px-2 py-1" disabled={!!migration.data?.changes.length&&!reviewed} onClick={()=>onEdit(pipeline)}>{ui(migration.data?.changes.length?'迁移到最新方法并编辑':'编辑新版本')}</button>}
  {(error||versions.error||migration.error)&&<p role="alert" className="break-words text-danger">{error||errorText(versions.error??migration.error,ui('读取版本信息失败'))}</p>}
 </div>;
}
