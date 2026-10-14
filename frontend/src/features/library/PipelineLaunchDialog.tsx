import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Dialog } from '../../ui/Dialog';
import { ui } from '../../lib/libraryText';
import { errorText } from '../../api/http';
import { DatasetPicker } from '../datasets/DatasetPicker';
import type { Dataset } from '../datasets/datasetsApi';
import { WorkspacePanel } from '../workspace/WorkspacePanel';
import type { Workspace } from '../workspace/workspaceTypes';
import type { Workflow } from '../workflows/workflowTypes';
import { usePipeline as instantiatePipeline } from './libraryApi';
import type { Pipeline } from './libraryApi';
import { button } from './pipelineEditorStyles';

export function PipelineLaunchDialog({pipeline, onClose, onCreated}: {
  pipeline: Pipeline; onClose: () => void; onCreated: (workflow: Workflow) => void;
}) {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [picker, setPicker] = useState<'dataset'|'workspace'|null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const client = useQueryClient();
  const create = async () => {
    if (pending.current || !workspace || pipeline.status==='archived') return;
    pending.current=true;setBusy(true);setError('');
    try {
      // A catalog launch has its own context; it never binds the active chat.
      const workflow = await instantiatePipeline(pipeline.id,dataset?.dataset_id??null,workspace.id,pipeline.version);
      client.setQueryData(['workflow',workflow.workflow_id], workflow);
      void client.invalidateQueries({queryKey:['task-index']});
      onCreated(workflow);
    } catch (e) {setError(errorText(e,ui('创建任务失败')));}
    finally {pending.current=false;setBusy(false);}
  };
  return <>
    <Dialog title={ui('创建 Pipeline 任务')} description={`${pipeline.label} · v${pipeline.version}`}
      closeLabel={ui('关闭')} onClose={()=>{if(!pending.current)onClose();}} size="lg"
      footer={<><button className={button} disabled={busy} onClick={onClose}>{ui('返回选择 Pipeline')}</button><button className={button} disabled={busy||!workspace||pipeline.status==='archived'}
        onClick={()=>void create()}>{busy?ui('正在创建任务…'):ui('创建任务并配置运行')}</button></>}>
      <div className="space-y-4 text-sm">
        <p>{ui('为本次任务选择输入和输出。创建后仍需检查参数、预检并确认执行。')}</p>
        <section className="space-y-2 rounded border border-line p-3">
          <h3 className="font-medium">{ui('输入数据集')}</h3>
          <p className="break-all text-xs text-subtle">{dataset?.absolute_path ?? ui('未选择数据集，可在任务中手动绑定输入文件。')}</p>
          <div className="flex gap-2"><button className={button} disabled={busy} onClick={()=>setPicker('dataset')}>{ui('选择数据集')}</button>
            {dataset && <button className={button} disabled={busy} onClick={()=>setDataset(null)}>{ui('清除')}</button>}</div>
        </section>
        <section className="space-y-2 rounded border border-line p-3">
          <h3 className="font-medium">{ui('输出目录')}</h3>
          <p className="break-all text-xs text-subtle">{workspace?.root_path ?? ui('请选择或登记输出目录。')}</p>
          <button className={button} disabled={busy} onClick={()=>setPicker('workspace')}>{ui('选择或登记输出目录')}</button>
        </section>
        <p className="text-xs text-subtle">{ui('本任务使用所选 Pipeline 版本，不改变当前对话的数据集或输出目录。')}</p>
        {error && <p role="alert" className="text-danger">{error}</p>}
      </div>
    </Dialog>
    {picker==='dataset' && <DatasetPicker selectedDatasetId={dataset?.dataset_id??null}
      onSelect={value=>{setDataset(value);setPicker(null);}} onClose={()=>setPicker(null)}/>}
    {picker==='workspace' && <WorkspacePanel selectionMode workspaceId={workspace?.id??null} conversationId={null}
      onSelect={async value=>{setWorkspace(value);}} onClose={()=>setPicker(null)}/>}
  </>;
}
