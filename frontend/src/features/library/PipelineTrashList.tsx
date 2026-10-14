import { useState } from 'react';
import { useQuery,useQueryClient } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { errorText } from '../../api/http';
import { ui } from '../../lib/libraryText';
import * as api from './libraryApi';
import { button } from './pipelineEditorStyles';
import { refreshPipelineViews } from './libraryQueries';

export function PipelineTrashList() {
  const query=useQuery({queryKey:['pipeline-trash'],queryFn:api.pipelineTrash,refetchOnWindowFocus:true,refetchInterval:5000});
  const client=useQueryClient(),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const restore=async(pipeline:api.DeletedPipeline)=>{
    if(busy)return;setBusy(true);setError('');
    try{
      await api.restorePipeline(pipeline);
      await refreshPipelineViews(client,pipeline.id);
    }catch(e){setError(errorText(e,ui('恢复 Pipeline 失败')));await query.refetch();}
    finally{setBusy(false);}
  };
  return <section className="text-xs">
    <details className="rounded-control border border-line p-2">
      <summary className="cursor-pointer">{ui('已删除 Pipeline')} · {query.data?.items.length??0}</summary>
      <p className="mt-2 text-subtle">{ui('恢复全部版本，并保留删除前各版本的发布或归档状态。')}</p>
      {query.isPending&&<p role="status">{ui('加载目录…')}</p>}
      {query.data?.items.map(p=><div key={p.id} className="mt-2 flex items-start gap-2 rounded border border-line p-2">
        <span className="min-w-0 flex-1 break-words"><strong>{p.label}</strong><span className="mt-1 block text-micro text-subtle">{ui('共 {0} 个版本',p.version_count)} · {new Date(p.deleted_at).toLocaleString()}</span></span>
        <button className={`${button} shrink-0`} disabled={busy} aria-label={ui('恢复 Pipeline：{0}',p.label)} onClick={()=>void restore(p)}><RotateCcw className="size-3" aria-hidden="true"/>{ui('恢复')}</button>
      </div>)}
      {!query.isPending&&!query.data?.items.length&&<p className="py-3 text-subtle">{ui('暂无已删除 Pipeline')}</p>}
      {(error||query.isError)&&<p role="alert" className="mt-2 text-danger">{error||errorText(query.error,ui('读取已删除 Pipeline 失败'))}</p>}
    </details>
  </section>;
}
