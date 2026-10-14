import { stateText } from '../../lib/stateText';
import { ui } from '../../lib/libraryText';
import { errorText } from '../../api/http';
import { DefinitionDetails } from './DefinitionDetails';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Dialog } from '../../ui/Dialog';
import { Tabs } from '../../ui/Tabs';
import { useSkills } from '../skills/useSkills';
import type { Skill } from '../skills/skillsApi';
import { PipelineLaunchDialog } from './PipelineLaunchDialog';
import { PipelineDraftList } from './PipelineDraftList';
import { PipelineDeleteAction } from './PipelineDeleteAction';
import { PipelineTrashList } from './PipelineTrashList';
import type { Workflow } from '../workflows/workflowTypes';
import * as api from './libraryApi';
import type { Entry, Pipeline } from './libraryApi';
import { methodQuery, refreshPipelineViews } from './libraryQueries';
import { PipelineDetails } from './PipelineDetails';
import { PipelineEditor } from './PipelineEditor';
import { LibraryNavigation } from './LibraryNavigation';
import { button } from './pipelineEditorStyles';
import { useLanguageStore } from '../../stores/languageStore';

type Props={selectedSkillIds:string[];disabled:boolean;onAdd:(id:string)=>void;onRemove:(id:string)=>void;
  onDetails:(s:Skill)=>void;onClear:()=>void;domain?:'skills'|'pipelines';taskPicker?:boolean;onTaskCreated?:(workflow:Workflow)=>void};
type DetailSelection={id:string;kind:'method'|'pipeline';version?:string};
export function LibraryPanel(props:Props) {
  const language=useLanguageStore(s=>s.language);
  const [category,setCategory]=useState(''); const [software,setSoftware]=useState('');
  const [search,setSearch]=useState('');
  const [kind,setKind]=useState(props.domain==='pipelines'?'pipeline':'skill');const [q,setQ]=useState('');const [cursor,setCursor]=useState(0);
  const [editing,setEditing]=useState<Pipeline|'new'|null>(null);
  const [detail,setDetail]=useState<DetailSelection|null>(null);const [error,setError]=useState('');
  const [launch,setLaunch]=useState<Pipeline|null>(null);const [busy,setBusy]=useState(false);
  const client=useQueryClient();const skills=useSkills();
  useEffect(()=>{const timer=setTimeout(()=>setSearch(q),200);return()=>clearTimeout(timer);},[q]);
  const query=useQuery({queryKey:['library',kind,search,cursor,category,software,language],queryFn:()=>api.catalog(kind,search,cursor,category,software,language)});
  // Keep only the selection in component state. Query keys own loading, locale
  // changes and cancellation, so opening a definition does not fetch it twice.
  const methodDetail=useQuery({...methodQuery(detail?.id??'',language),enabled:detail?.kind==='method'});
  const pipelineDetail=useQuery({
    queryKey:['pipeline-detail',detail?.id,detail?.version,language],
    queryFn:({signal})=>api.pipeline(detail!.id,detail?.version,signal),
    enabled:detail?.kind==='pipeline'
  });
  const detailQuery=detail?.kind==='method'?methodDetail:pipelineDetail;
  const definition=detail?.kind==='method'?methodDetail.data:pipelineDetail.data;
  const inspect=(e:Entry)=>{setError('');try{
    if(e.kind==='skill'){const s=skills.data?.skills.find(s=>s.name===e.id);if(s)props.onDetails(s);else throw new Error(ui("Skill 元数据尚未加载"));}
    else if(e.kind==='method'||e.kind==='pipeline')setDetail({id:e.id,kind:e.kind,version:e.version});
  }catch(e){setError(errorText(e,ui("读取详情失败")));}};
  const prepare=async(e:Entry)=>{if(busy)return;setBusy(true);setError('');try{setLaunch(await api.pipeline(e.id,e.version));}catch(err){setError(errorText(err,ui('读取详情失败')));}finally{setBusy(false);}};
  const navigation=<LibraryNavigation facets={query.data?.facets} category={category} software={software}
    onCategory={value=>{setCategory(value);setCursor(0);}} onSoftware={value=>{setSoftware(value);setCursor(0);}}/>;
  return <div className="min-w-0 space-y-3">
    {props.domain!=='pipelines'&&<Tabs
      items={[
        { value: 'skill', label: 'Skill' },
        { value: 'method', label: 'Method' }
      ]}
      value={kind}
      onChange={next => { setKind(next); setCursor(0); setCategory(''); setSoftware(''); }}
      label={ui("能力资源目录")}
    />}
    <input className="w-full min-w-0 rounded border border-line bg-raised p-2 text-xs" aria-label={ui("搜索资源")} placeholder={ui("搜索名称、功能或软件")} value={q} onChange={e=>{setQ(e.target.value);setCursor(0);}}/>
    <p className="text-xs text-subtle">{kind==='skill'?ui("Skill：按目标统筹方法与流程"):kind==='method'?ui("Method：可执行的最小命令或库调用"):ui("Pipeline：已定义输入输出的可复用方法组合")}</p>
    {props.taskPicker?<details className="rounded-control border border-line p-2 text-xs"><summary className="cursor-pointer">{ui('按处理目标或软件筛选')}</summary>{navigation}</details>:navigation}
    {kind==='pipeline'&&!props.taskPicker&&<PipelineDraftList onEdit={setEditing}/>}
    {kind==='pipeline'&&!props.taskPicker&&<PipelineTrashList/>}
    {kind==='pipeline'&&!props.taskPicker&&<button className={button} onClick={()=>setEditing('new')}>{ui("＋ 拖拽创建 Pipeline")}</button>}
    {kind==='skill'&&!!props.selectedSkillIds.length&&<button className={button} disabled={props.disabled} onClick={props.onClear}>{ui("清空已选 Skill")}</button>}
    {(error||query.isError)&&<p role="alert" className="break-words text-xs text-danger">{error||String(query.error)}</p>}
    {query.isPending?<p className="text-xs">{ui("加载目录…")}</p>:<div className={props.taskPicker?'grid gap-3 sm:grid-cols-2':'space-y-2'}>{(query.data?.items??[]).map(e=><div key={e.id} className="min-w-0 rounded border border-line p-3 text-xs">
      <p className="block w-full break-words text-left font-medium">{e.label}</p>
      <p className="mt-1 line-clamp-3 break-words text-subtle" title={e.description}>{e.description}</p>
      <p className="mt-1 break-words text-micro text-subtle">{e.category.join(" / ")}{e.software?.length ? " · "+e.software.join(", ") : ""}</p>
      {e.kind==='method'&&<p className="mt-1 text-subtle">{ui('已开放 {0} 个参数，{1} 个原生命令选项尚未开放',e.parameter_count??0,e.omitted_option_count??0)}</p>}
      <div className="mt-2 flex flex-wrap gap-1">
        <button className={button} aria-label={ui('查看详情：{0}',e.label)} onClick={()=>inspect(e)}>{ui('查看详情')}</button>
        {e.kind==='pipeline'&&<button className={button} disabled={busy||e.status==='archived'} onClick={()=>void prepare(e)}>{ui("创建任务")}</button>}
        {e.kind==='pipeline'&&!props.taskPicker&&e.version&&<PipelineDeleteAction pipeline={{...e,version:e.version}} onDeleted={()=>{if(detail?.id===e.id)setDetail(null);if(launch?.id===e.id)setLaunch(null);}}/>}
        {e.kind==='skill'&&<button className={button} disabled={props.disabled} onClick={()=>props.selectedSkillIds.includes(e.id)?props.onRemove(e.id):props.onAdd(e.id)}>{props.selectedSkillIds.includes(e.id)?ui("移出会话"):ui("加入会话")}</button>}
        {e.kind==='pipeline'&&<span className="text-subtle">{e.step_count} {ui("步 · v")}{e.version} · {stateText(e.status??'published')}</span>}</div>
    </div>)}</div>}
    {!query.isPending&&!query.data?.items.length&&<p role="status" className="py-3 text-xs text-subtle">{ui("没有匹配条目，请调整搜索或清除筛选。")}</p>}
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs"><button className={button} disabled={!cursor} onClick={()=>setCursor(Math.max(0,cursor-100))}>{ui("上一页")}</button>
      <span>{query.data?.total??0} {ui("项")}</span><button className={button} disabled={!query.data?.has_more} onClick={()=>setCursor(query.data?.next_cursor??0)}>{ui("下一页")}</button></div>
    {editing&&<PipelineEditor initial={editing==='new'?undefined:editing} onClose={()=>{setEditing(null);void client.invalidateQueries({queryKey:['pipeline-drafts']});}} onSaved={p=>void refreshPipelineViews(client,p.id)}/>}
    {detail&&!definition&&<Dialog title={ui('查看详情')} closeLabel={ui('关闭')} onClose={()=>setDetail(null)}>
      {detailQuery.isPending&&<p role="status">{ui('加载目录…')}</p>}
      {detailQuery.isError&&<p role="alert" className="text-danger">{errorText(detailQuery.error,ui('读取详情失败'))}
        <button className={button} onClick={()=>void detailQuery.refetch()}>{ui('重试加载')}</button></p>}
    </Dialog>}
    {detail&&definition&&'steps' in definition&&<PipelineDetails key={definition.id+':'+definition.version} pipeline={definition} taskPicker={props.taskPicker}
      onClose={()=>setDetail(null)} onSelectVersion={version=>setDetail({...detail,version})} onEdit={p=>{setEditing(p);setDetail(null);}}
      onLaunch={()=>{setLaunch(definition);setDetail(null);}}/>}
    {detail&&definition&&!('steps' in definition)&&<Dialog title={definition.label} closeLabel={ui("关闭")} size="xl" onClose={()=>setDetail(null)}>
      <DefinitionDetails definition={definition}/>
      {detailQuery.isError&&<p role="alert" className="text-danger">{errorText(detailQuery.error,ui('读取详情失败'))}</p>}
    </Dialog>}
    {launch&&<PipelineLaunchDialog key={launch.id+':'+launch.version} pipeline={launch} onClose={()=>setLaunch(null)}
      onCreated={workflow=>{setLaunch(null);props.onTaskCreated?.(workflow);}}/>}
  </div>;
}
