import { request } from '../../api/http';
import { executionDraft } from './pipelineGraph';
import { rememberLayout, restoreLayout } from './pipelineLayout';
import { mergeDraftRecovery } from './pipelineDraftRecovery';
import type { ParameterSpec, InputSlotSpec, OutputSlotSpec, ParameterValue, Workflow } from '../workflows/workflowTypes';
export type Entry = {id:string;label:string;description:string;kind:string;category:string[];category_ids?:string[];software?:string[];method_count?:number;pipeline_count?:number;version?:string;step_count?:number;status?:string;deletion_revision?:number;omitted_option_count?:number;parameter_count?:number};
export type Facets = {categories:Array<{path:string[];label:string;count:number}>;software:Array<{id:string;label:string;count:number}>};
export type Page = {facets?:Facets;items:Entry[];total:number;has_more:boolean;next_cursor:number|null};
export type Method = Entry & {parameters:ParameterSpec[];input_slots:InputSlotSpec[];output_slots:OutputSlotSpec[];
  execution?:{contract?:{total_options?:number;omitted_options?:Record<string,string>}};
  verification?:import('../skills/MethodVerification').Verification;
  option_coverage?: {flag:string;description:string}[]; runtime_status:{available:boolean;reason?:string};command_references?:Array<{command:string;description?:string}>};
export type SourceLink = {step_id:string;output_slot_id:string};
export type Link = SourceLink & {additional_sources?:SourceLink[]};
export type Node = {position?:{x:number;y:number};id:string;method_id:string;parameters:Record<string,ParameterValue>;inputs:Record<string,Link>;when?:Record<string,ParameterValue[]>};
export type InputPort = {step_id:string;input_slot_id:string};
export type Draft = {shared_inputs?:InputPort[][];controls?:ParameterSpec[];name:string;description:string;steps:Node[];expected_version?:number};
export type Pipeline = {draft_id?:string;draft_revision?:number;status?:string;deletion_revision?:number;layout_warning?:string;id:string;version:string;label:string;description:string;editor?:Draft;
  parameters:ParameterSpec[];input_slots:InputSlotSpec[];output_slots:OutputSlotSpec[];
  steps:Array<{id:string;method_ref:string;method_id?:string;title:string;fixed_parameters?:Record<string,ParameterValue>}>};
export const catalog = (kind:string,q:string,cursor=0,category='',software='',language='zh') => request<Page>(`/api/library?${new URLSearchParams({kind,q,cursor:String(cursor),limit:'100',category,software,language})}`);
export const method = (id:string,signal?:AbortSignal) => request<Method>(`/api/library/methods/${encodeURIComponent(id)}`,{signal});
export const pipeline = async (id:string,version?:string,signal?:AbortSignal) => restoreLayout(await request<Pipeline>(`/api/library/pipelines/${encodeURIComponent(id)}${version?'?version='+encodeURIComponent(version):''}`,{signal}));
export const pipelineSource = (id:string,version:string) => request<{editor:Draft}>(`/api/library/pipelines/${encodeURIComponent(id)}/source?version=${encodeURIComponent(version)}`);
export const validate = (draft:Draft) => request<Pipeline>('/api/library/pipelines/validate',{method:'POST',body:JSON.stringify(executionDraft(draft))});
export const save = async (draft:Draft,id?:string) => {
  const value=await request<Pipeline>(`/api/library/pipelines${id?'/'+encodeURIComponent(id):''}`,{method:id?'PUT':'POST',body:JSON.stringify(executionDraft(draft))});
  return rememberLayout(value,draft);
};
export const usePipeline = (id:string,dataset_id:string|null,workspace_id:string|null,version?:string,conversation_id?:string|null) => request<Workflow>(`/api/library/pipelines/${encodeURIComponent(id)}/workflows`,{method:'POST',body:JSON.stringify({dataset_id,workspace_id,conversation_id,version:version?Number(version):null})});

export const describe = (draft:Draft,language:string,signal?:AbortSignal) => request<{description:string;source:string;warning:string;fingerprint:string}>(`/api/library/pipelines/describe?language=${language}`,{method:'POST',body:JSON.stringify(executionDraft(draft)),signal});
export type SavedDraft={id:string;revision:number;updated_at:string;editor:Draft;pipeline_id:string|null;pipeline_version:number|null;server_revision?:number};
export type DeletedDraft=SavedDraft&{deleted_at:string};
export const drafts=async()=>{
  const server=await request<{items:SavedDraft[];deleted_ids:string[];deleted_items:DeletedDraft[]}>('/api/library/drafts');
  const recovered:SavedDraft[]=[];
  try{for(let i=0;i<localStorage.length;i++){
    const key=localStorage.key(i);if(!key?.startsWith('neuroweave:pipeline-draft:'))continue;
    try{const value=JSON.parse(localStorage.getItem(key)??'null') as SavedDraft|null;if(value)recovered.push(value);}catch{/* Ignore one damaged browser copy. */}
  }}catch{/* Server drafts remain visible. */}
  return {items:mergeDraftRecovery(server.items,recovered,server.deleted_ids??[]),deleted_items:server.deleted_items??[]};
};
export const deleteDraft=async(draft:SavedDraft)=>{
  await request(`/api/library/drafts/${encodeURIComponent(draft.id)}`,{method:'DELETE',body:JSON.stringify({
    expected_revision:draft.server_revision??draft.revision,editor:draft.editor,pipeline_id:draft.pipeline_id,pipeline_version:draft.pipeline_version})});
  try{localStorage.removeItem('neuroweave:pipeline-draft:'+draft.id);}catch{/* The server tombstone also hides recovery copies. */}
};
export const restoreDraft=(draft:DeletedDraft)=>request(`/api/library/drafts/${encodeURIComponent(draft.id)}/restore`,
  {method:'POST',body:JSON.stringify({expected_revision:draft.revision})});
export const lifecycle=(id:string,version:string,status:string)=>request(`/api/library/pipelines/${encodeURIComponent(id)}/versions/${version}?status=${status}`,{method:'PATCH'});
export const versions=(id:string)=>request<{items:Array<{version:string;status?:string}>}>(`/api/library/pipelines/${encodeURIComponent(id)}/versions`);
export const migration=(id:string,version?:string)=>request<{changes:Array<{step_id:string;method_id:string;from_version:string;to_version:string}>;errors:string[];editor?:Draft}>(`/api/library/pipelines/${encodeURIComponent(id)}/migration${version?'?version='+version:''}`);
export type DeletedPipeline={id:string;label:string;version:string;version_count:number;deletion_revision:number;deleted_at:string};
export const pipelineTrash=()=>request<{items:DeletedPipeline[]}>('/api/library/pipelines/trash');
export const deletePipeline=(pipeline:Pick<Pipeline,'id'|'version'|'deletion_revision'>)=>request(`/api/library/pipelines/${encodeURIComponent(pipeline.id)}`,
  {method:'DELETE',body:JSON.stringify({expected_version:Number(pipeline.version),expected_deletion_revision:pipeline.deletion_revision??0})});
export const restorePipeline=(pipeline:DeletedPipeline)=>request(`/api/library/pipelines/${encodeURIComponent(pipeline.id)}/restore`,
  {method:'POST',body:JSON.stringify({expected_deletion_revision:pipeline.deletion_revision})});
