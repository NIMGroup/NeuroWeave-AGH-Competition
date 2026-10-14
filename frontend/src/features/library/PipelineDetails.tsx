import { useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { Dialog } from '../../ui/Dialog';
import { ui } from '../../lib/libraryText';
import { errorText } from '../../api/http';
import { useLanguageStore } from '../../stores/languageStore';
import * as api from './libraryApi';
import { arrangeNodes, linkSources } from './pipelineGraph';
import { copyPipeline, stageCondition } from './pipelineCopy';
import { PipelineCanvas } from './PipelineCanvas';
import { PipelineVersions } from './PipelineVersions';
import { DefinitionDetails } from './DefinitionDetails';
import { PipelineMethodParameters } from './PipelineMethodParameters';
import { PipelineDeleteAction } from './PipelineDeleteAction';
import { methodQuery } from './libraryQueries';
import { button } from './pipelineEditorStyles';

/** Viewing an immutable version has no draft hook or graph mutation callbacks. */
export function PipelineDetails({pipeline, taskPicker=false, onClose, onSelectVersion, onEdit, onLaunch}: {
  pipeline:api.Pipeline; taskPicker?:boolean; onClose:()=>void;
  onSelectVersion:(version:string)=>void; onEdit:(p:api.Pipeline)=>void;
  onLaunch:()=>void;
}) {
  const language=useLanguageStore(s=>s.language);
  const [selected,setSelected]=useState<string|null>(null);
  const [showInspector,setShowInspector]=useState(()=>typeof window==='undefined'||window.innerWidth>=1100);
  const source=useQuery({queryKey:['pipeline-source',pipeline.id,pipeline.version],queryFn:()=>api.pipelineSource(pipeline.id,pipeline.version)});
  const ids=[...new Set(pipeline.steps.map(s=>s.method_ref??s.method_id!))];
  const contracts=useQueries({queries:ids.map(id=>methodQuery(id,language))});
  const definitions=Object.fromEntries(contracts.flatMap(r=>r.data?[[r.data.id,r.data]]:[]));
  const loading=source.isPending||contracts.some(r=>r.isPending);
  const error=source.error??contracts.find(r=>r.error)?.error;
  const original=source.data?.editor;
  const editor=original&&!loading&&!error?{...original,steps:original.steps.every(s=>s.position)?original.steps:arrangeNodes(original.steps,definitions)}:null;
  const node=editor?.steps.find(s=>s.id===(selected??editor.steps[0]?.id));
  const method=node&&definitions[node.method_id];
  const noop=()=>{};
  return <Dialog title={ui('Pipeline 详情：{0}',pipeline.label)} closeLabel={ui('关闭')} onClose={onClose}
    placement="fullscreen" className="!max-w-none h-full !rounded-none"
    contentClassName="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-2"
    footer={<><span className="mr-auto text-xs text-subtle">{ui('查看不会修改原 Pipeline，也不会启动任务。')}</span>
      {!taskPicker&&<PipelineDeleteAction pipeline={pipeline} onDeleted={onClose}/>}
      <button className={button} disabled={!editor||!!error} onClick={()=>{if(editor)onEdit(copyPipeline(pipeline,editor));}}>{ui('基于此 Pipeline 创建')}</button>
      <button className={button} disabled={pipeline.status==='archived'} onClick={onLaunch}>{ui('创建任务')}</button></>}>
    <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
      <span className="rounded bg-accent-quiet px-2 py-1 text-accent">{ui(pipeline.id.startsWith('user-')?'Pipeline 版本快照 · 只读':'内置 Pipeline · 只读')}</span>
      <span className="min-w-0 flex-1">{ui('可查看节点、参数和连线；创建独立副本后编辑。')}</span>
      <button className={button} onClick={()=>setShowInspector(v=>!v)}>{ui(showInspector?'收起详情':'展开详情')}</button>
    </div>
    {loading&&<p role="status" className="p-3 text-sm">{ui('正在读取 Pipeline 画布…')}</p>}
    {error&&<p role="alert" className="p-3 text-sm text-danger">{errorText(error,ui('读取详情失败'))}</p>}
    {editor&&<div className="grid min-h-0 flex-1 gap-2" style={{gridTemplateColumns:showInspector?'minmax(0,1fr) minmax(220px,320px)':'minmax(0,1fr)'}}>
      <PipelineCanvas steps={editor.steps} sharedInputs={editor.shared_inputs} definitions={definitions}
        selected={node?.id??null} readOnly disabled={false} showConditionalInputs
        onSelect={id=>{setSelected(id);setShowInspector(true);}} onMove={noop} onAdd={noop}
        onConnect={noop} onDisconnect={noop} onArrange={noop}/>
      {showInspector&&<aside aria-label={ui('Pipeline 画布详情')} className="min-h-0 overflow-auto rounded border border-line p-3 text-xs">
        <details className="mb-3"><summary className="cursor-pointer font-semibold">{ui('Pipeline 接口与版本')}</summary>
          <PipelineVersions pipeline={pipeline} readOnly={taskPicker} onSelectVersion={onSelectVersion} onEdit={onEdit}/>
          <DefinitionDetails definition={pipeline}/>
        </details>
        {!!editor.controls?.length&&<div className="mb-3"><h3 className="font-semibold">{ui('Pipeline 控制参数')}</h3>
          {editor.controls.map(p=><p key={p.id}>{p.label}：{p.options.find(o=>o.value===p.default)?.label??String(p.default)}</p>)}
        </div>}
        {node&&method&&<><h3 className="mb-2 text-sm font-semibold">{method.label}</h3>
          {!!Object.keys(node.when??{}).length&&<p className="mb-2 text-warning">{ui('执行条件：{0}',stageCondition(node,editor))}</p>}
          <h4 className="mb-1 font-semibold">{ui('输入连接')}</h4>
          {method.input_slots.map(slot=><p key={slot.id} className="mb-1 break-words">{slot.label}：{node.inputs[slot.id]?linkSources(node.inputs[slot.id]).map(l=>`${l.step_id} / ${l.output_slot_id}`).join(' → '):ui('外部输入')}
            {editor.shared_inputs?.some(g=>g.some(p=>p.step_id===node.id&&p.input_slot_id===slot.id))?' · '+ui('共享输入'):''}</p>)}
          <PipelineMethodParameters method={method} node={node} readOnly
            fixedParameters={Object.keys(pipeline.steps.find(s=>s.id===node.id)?.fixed_parameters??{})}/>
          <details className="mt-3"><summary className="cursor-pointer font-semibold">{ui('方法接口与说明')}</summary>
            <DefinitionDetails definition={method} showParameters={false}/>
          </details>
        </>}
      </aside>}
    </div>}
  </Dialog>;
}
