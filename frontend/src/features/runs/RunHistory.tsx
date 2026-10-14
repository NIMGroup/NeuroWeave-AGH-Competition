import { stateText } from '../../lib/stateText';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { request, errorText } from '../../api/http';
import { ui } from '../../lib/libraryText';
import { RunPanel } from './RunPanel';
import { fetchRunEvents,fetchRunMonitor } from './runsApi';
import type { WorkflowRun,RunListPage } from './runsTypes';
function SavedRun({id}:{id:string}){
 const run=useQuery({queryKey:['history-run',id],queryFn:async()=>{const r=await fetchRunMonitor(id,-1);return {...r.run,steps:r.steps,plan_snapshot:{}} as WorkflowRun;},refetchInterval:q=>['queued','running','cancel_requested'].includes(q.state.data?.status??'')?3000:false});
 const events=useQuery({queryKey:['history-run-events',id],queryFn:()=>fetchRunEvents(id,{afterEventId:0,tail:true})});
 return <>{run.data&&<RunPanel run={run.data} events={events.data?.events??[]}/>} {(run.error||events.error)&&<p role="alert">{errorText(run.error??events.error,ui('读取运行记录失败'))}</p>}</>;
}
export function RunHistory({workflowId}:{workflowId:string}){
 const [offset,setOffset]=useState(0),[id,setId]=useState<string|null>(null);
 const query=useQuery({queryKey:['run-history',workflowId,offset],queryFn:()=>request<RunListPage>(`/api/runs?workflow_id=${encodeURIComponent(workflowId)}&summary=true&limit=21&offset=${offset}`)});
 return <details className="mt-3 rounded border border-line p-2 text-xs"><summary>{ui('查看此流程的历史运行')}</summary>
 <div className="my-2 flex flex-wrap gap-2">{query.data?.runs.slice(0,20).map(r=><button className="rounded border border-line p-2 text-left" key={r.run_id} onClick={()=>setId(r.run_id)}>{new Date(r.created_at).toLocaleString()} · {stateText(r.status)} · {r.run_id}</button>)}</div>
 {query.error&&<p role="alert">{errorText(query.error,ui('读取运行历史失败'))}</p>}<div className="flex justify-between"><button disabled={!offset} onClick={()=>setOffset(v=>v-20)}>{ui('上一页')}</button><button disabled={(query.data?.runs.length??0)<=20} onClick={()=>setOffset(v=>v+20)}>{ui('下一页')}</button></div>
 {id&&<SavedRun key={id} id={id}/>}
 </details>;
}
