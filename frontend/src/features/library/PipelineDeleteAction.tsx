import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { Dialog } from '../../ui/Dialog';
import { errorText } from '../../api/http';
import { ui } from '../../lib/libraryText';
import * as api from './libraryApi';
import { button } from './pipelineEditorStyles';
import { refreshPipelineViews } from './libraryQueries';

export function PipelineDeleteAction({pipeline,onDeleted}:{
  pipeline:Pick<api.Pipeline,'id'|'label'|'version'|'deletion_revision'>;onDeleted:()=>void;
}) {
  const [confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const client=useQueryClient();
  const remove=async()=>{
    if(busy)return;setBusy(true);setError('');
    try {
      await api.deletePipeline(pipeline);setConfirm(false);onDeleted();
      await refreshPipelineViews(client,pipeline.id);
    }catch(e){
      setError(errorText(e,ui('删除 Pipeline 失败')));
      await client.invalidateQueries({queryKey:['library']});
    }finally{setBusy(false);}
  };
  if(!pipeline.id.startsWith('user-'))return null;
  return <>
    <button className={`${button} text-danger`} aria-label={ui('删除 Pipeline：{0}',pipeline.label)} onClick={()=>{setError('');setConfirm(true);}}>
      <Trash2 className="size-3" aria-hidden="true"/>{ui('删除 Pipeline')}
    </button>
    {confirm&&<Dialog title={ui('删除已发布的 Pipeline')} description={pipeline.label} closeLabel={ui('关闭')}
      onClose={()=>{if(!busy)setConfirm(false);}}
      footer={<><button className={button} disabled={busy} onClick={()=>setConfirm(false)}>{ui('取消')}</button>
        <button className="rounded-control bg-danger-solid px-3 py-2 text-xs text-on-danger disabled:opacity-50" disabled={busy} onClick={()=>void remove()}>{busy?ui('正在删除…'):ui('确认删除')}</button></>}>
      <p className="text-sm">{ui('将删除此 Pipeline 的全部发布版本，并从目录和新任务选择中移除。可在“已删除 Pipeline”中恢复。已有任务、运行结果和草稿保留。')}</p>
      {error&&<p role="alert" className="mt-3 text-xs text-danger">{error}</p>}
    </Dialog>}
  </>;
}
