import { useState } from 'react';
import { useQuery,useQueryClient } from '@tanstack/react-query';
import { RotateCcw, Trash2 } from 'lucide-react';
import { Dialog } from '../../ui/Dialog';
import { errorText } from '../../api/http';
import { ui } from '../../lib/libraryText';
import * as api from './libraryApi';
import { button } from './pipelineEditorStyles';

export function PipelineDraftList({onEdit}:{onEdit:(pipeline:api.Pipeline)=>void}) {
  const query=useQuery({queryKey:['pipeline-drafts'],queryFn:api.drafts,refetchOnWindowFocus:true,refetchInterval:5000});
  const client=useQueryClient();const [deleting,setDeleting]=useState<api.SavedDraft|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const remove=async()=>{if(!deleting||busy)return;setBusy(true);setError('');try{
    await api.deleteDraft(deleting);setDeleting(null);await client.invalidateQueries({queryKey:['pipeline-drafts']});
  }catch(e){setError(errorText(e,ui('删除草稿失败')));setDeleting(null);await query.refetch();}finally{setBusy(false);}};
  const restore=async(draft:api.DeletedDraft)=>{if(busy)return;setBusy(true);setError('');try{
    await api.restoreDraft(draft);await client.invalidateQueries({queryKey:['pipeline-drafts']});
  }catch(e){setError(errorText(e,ui('恢复草稿失败')));}finally{setBusy(false);}};
  return <section className="space-y-2 text-xs">
    <details className="rounded-control border border-line p-2"><summary className="cursor-pointer">{ui('未完成的 Pipeline 草稿')} · {query.data?.items.length??0}</summary>
      {query.isPending&&<p role="status">{ui('加载目录…')}</p>}
      {query.data?.items.map(draft=><div key={draft.id} className="mt-2 flex items-start gap-2 rounded border border-line bg-raised p-2">
        <button className="min-w-0 flex-1 text-left hover:text-accent" onClick={()=>onEdit({id:draft.pipeline_id??'',version:draft.pipeline_version?String(draft.pipeline_version):'',label:draft.editor.name,description:draft.editor.description,editor:draft.editor,draft_id:draft.id,draft_revision:draft.revision,parameters:[],input_slots:[],output_slots:[],steps:[]})}>
          <strong className="line-clamp-2 break-words">{draft.editor.name||ui('未命名草稿')}</strong><span className="mt-1 block text-micro text-subtle">{new Date(draft.updated_at).toLocaleString()}</span></button>
        <button className="rounded p-1 text-subtle hover:bg-danger-quiet hover:text-danger disabled:opacity-40" disabled={busy} aria-label={ui('删除草稿：{0}',draft.editor.name||ui('未命名草稿'))} title={ui('删除草稿')} onClick={()=>{setError('');setDeleting(draft);}}><Trash2 className="size-4" aria-hidden="true"/></button>
      </div>)}
      {!query.isPending&&!query.data?.items.length&&<p className="py-3 text-subtle">{ui('暂无未完成草稿')}</p>}
    </details>
    {!!query.data?.deleted_items.length&&<details className="rounded-control border border-line p-2"><summary className="cursor-pointer text-subtle">{ui('已删除草稿')} · {query.data.deleted_items.length}</summary>
      {query.data.deleted_items.map(draft=><div key={draft.id} className="mt-2 flex items-center gap-2"><span className="min-w-0 flex-1 truncate">{draft.editor.name||ui('未命名草稿')}</span><button disabled={busy} className={`${button} shrink-0`} onClick={()=>void restore(draft)} aria-label={ui('恢复草稿：{0}',draft.editor.name||ui('未命名草稿'))}><RotateCcw className="size-3" aria-hidden="true"/>{ui('恢复')}</button></div>)}
    </details>}
    {(error||query.isError)&&<p role="alert" className="text-danger">{error||errorText(query.error,ui('读取草稿失败'))}</p>}
    {deleting&&<Dialog title={ui('删除 Pipeline 草稿')} description={deleting.editor.name||ui('未命名草稿')} closeLabel={ui('关闭')} onClose={()=>{if(!busy)setDeleting(null);}}
      footer={<><button className={button} disabled={busy} onClick={()=>setDeleting(null)}>{ui('取消')}</button><button className="rounded-control bg-danger-solid px-3 py-2 text-xs text-on-danger disabled:opacity-50" disabled={busy} onClick={()=>void remove()}>{busy?ui('正在删除…'):ui('删除草稿')}</button></>}>
      <p className="text-sm">{ui('删除后可在“已删除草稿”中恢复。已发布版本、任务和运行结果不受影响。')}</p>
      {error&&<p role="alert" className="mt-3 text-xs text-danger">{error}</p>}
    </Dialog>}
  </section>;
}
