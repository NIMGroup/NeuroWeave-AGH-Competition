import { compatible, nodeMethod } from './artifactSemantics';
import { ui } from '../../lib/libraryText';
import type { Draft, InputPort, Method, Node, Link, SourceLink } from './libraryApi';
import { activeOutputSlots } from '../workflows/workflowConditions';
import type { ParameterValue } from '../workflows/workflowTypes';
export const linkSources=(link:Link):SourceLink[]=>[{step_id:link.step_id,output_slot_id:link.output_slot_id},...(link.additional_sources??[])];
export type Point = {x:number;y:number};
let sequence=0;

/** Layout IDs are not credentials. getRandomValues also works on HTTP LAN pages. */
export function createNodeId(source: Pick<Crypto,'getRandomValues'> | null = typeof crypto==='undefined'?null:crypto): string {
  const words=new Uint32Array(2);
  if(source) source.getRandomValues(words);
  else {words[0]=Math.floor(Math.random()*0x100000000);words[1]=Math.floor(Math.random()*0x100000000);}
  return `s_${Date.now().toString(36)}_${(++sequence).toString(36)}_${Array.from(words,v=>v.toString(16).padStart(8,'0')).join('')}`;
}
export const nodePosition=(node:Node,index:number):Point=>node.position??{x:40+(index%3)*350,y:40+Math.floor(index/3)*420};

/** Spatial position never determines execution order: dependency edges do. */
export function orderedSteps(steps:Node[]):Node[] {
  const byId=new Map(steps.map(s=>[s.id,s]));
  if(byId.size!==steps.length) throw new Error(ui("步骤 ID 重复"));
  const pending=new Set(steps.map(s=>s.id));const ordered:Node[]=[];
  while(pending.size){
    const ready=steps.filter(s=>pending.has(s.id)&&Object.values(s.inputs).flatMap(linkSources).every(link=>{
      if(!byId.has(link.step_id))throw new Error(ui("连接的上游步骤不存在，请重新连接"));
      return !pending.has(link.step_id);
    }));
    if(!ready.length)throw new Error(ui("连接会形成循环；Pipeline 必须有明确的先后依赖"));
    for(const node of ready){pending.delete(node.id);ordered.push(node);}
  }
  return ordered;
}
export function connectNodes(steps:Node[],definitions:Record<string,Method>,from:string,output:string,to:string,input:string):Node[]{
  if(from===to)throw new Error(ui("不能把步骤连接到自身"));
  const source=steps.find(s=>s.id===from),target=steps.find(s=>s.id===to);
  const out=source&&nodeMethod(source,definitions,steps)?.output_slots.find(s=>s.id===output);
  const slot=target&&definitions[target.method_id]?.input_slots.find(s=>s.id===input);
  if(!out||!slot)throw new Error(ui("端口定义尚未加载，请稍后重试"));
  if(!compatible(out.type,slot.type))throw new Error(ui("端口类型不匹配：{0} → {1}",out.type,slot.type));
  const existing=target?.inputs[input];
  const links=slot.cardinality==='many'&&existing?linkSources(existing):[];
  if(!links.some(l=>l.step_id===from&&l.output_slot_id===output))links.push({step_id:from,output_slot_id:output});
  if(links.length>64)throw new Error(ui('每个多文件端口最多连接 64 个输出'));
  const next=steps.map(s=>s.id===to?{...s,inputs:{...s.inputs,[input]:{...links[0],...(links.length>1?{additional_sources:links.slice(1)}:{})}}}:s);
  orderedSteps(next);return next;
}
export function disconnectInput(steps:Node[],id:string,input:string,source?:SourceLink):Node[]{
  return steps.map(s=>{if(s.id!==id)return s;const inputs={...s.inputs};
    const remaining=source&&inputs[input]?linkSources(inputs[input]).filter(v=>v.step_id!==source.step_id||v.output_slot_id!==source.output_slot_id):[];
    if(remaining.length)inputs[input]={...remaining[0],additional_sources:remaining.slice(1)};else delete inputs[input];return {...s,inputs};});
}
export function removeNode(steps:Node[],id:string):Node[]{
  return steps.filter(s=>s.id!==id).map(s=>({...s,inputs:Object.fromEntries(Object.entries(s.inputs).flatMap(([key,link])=>{const left=linkSources(link).filter(l=>l.step_id!==id);return left.length?[[key,{...left[0],additional_sources:left.slice(1)}]]:[];}))}));
}
export const executionDraft=(draft:Draft):Draft=>({...draft,steps:orderedSteps(draft.steps)});
export function arrangeNodes(steps:Node[],definitions:Record<string,Method>):Node[]{
  const depth=new Map<string,number>(),heights=new Map<number,number>(),positions=new Map<string,Point>();
  for(const node of orderedSteps(steps)){
    const column=Math.max(0,...Object.values(node.inputs).flatMap(linkSources).map(link=>(depth.get(link.step_id)??0)+1));
    depth.set(node.id,column);const y=heights.get(column)??80;
    const m=definitions[node.method_id];const height=88+Math.max(m?.input_slots.length??0,m?.output_slots.length??0)*32;
    positions.set(node.id,{x:80+column*500,y});heights.set(column,y+height+100);
  }
  return steps.map(s=>({...s,position:positions.get(s.id)}));
}


export function conditionDescription(method:Method,condition:Record<string,ParameterValue[]>):string {
  return Object.entries(condition).map(([key,allowed])=>{
    const parameter=method.parameters.find(p=>p.id===key);
    return `${parameter?.label??key} = ${allowed.map(value=>parameter?.options?.find(o=>o.value===value)?.label??String(value)).join(' / ')}`;
  }).join('；');
}
/** Keep the user's wiring while parameters change; report unavailable outputs. */
export function conditionalConnectionIssues(steps:Node[],definitions:Record<string,Method>):string[]{
  const issues:string[]=[];
  for(const target of steps)for(const [input,binding] of Object.entries(target.inputs))for(const link of linkSources(binding)){
    const source=steps.find(n=>n.id===link.step_id);const method=source&&definitions[source.method_id];
    const output=source&&nodeMethod(source,definitions,steps)?.output_slots.find(o=>o.id===link.output_slot_id);
    if(!source||!method||!output)continue;
    const destination=definitions[target.method_id]?.input_slots.find(s=>s.id===input);
    if(destination&&!compatible(output.type,destination.type)){
      issues.push(`${source.id} → ${target.id} / ${input}: ${ui('端口类型不匹配：{0} → {1}',output.type,destination.type)}`);
    }
    if(!activeOutputSlots(method,source.parameters).some(slot=>slot.id===output.id)){
      const consumer=definitions[target.method_id];const slot=consumer?.input_slots.find(s=>s.id===input);
      issues.push(ui("{0}（{1}）→ {2} / {3}：{4}当前不会生成，需要{5}。",method.label,source.id.slice(-4),consumer?.label??target.method_id,slot?.label??input,output.label,conditionDescription(method,output.generated_when??{})));
    }
  }
  return issues;
}

/** Retain wired inactive ports so users can inspect and remove their connections. */
export function canvasOutputSlots(node:Node,method:Method,steps:Node[]){
  const active=new Set(activeOutputSlots(method,node.parameters).map(s=>s.id));
  const linked=new Set(steps.flatMap(s=>Object.values(s.inputs).flatMap(linkSources).filter(l=>l.step_id===node.id).map(l=>l.output_slot_id)));
  return method.output_slots.filter(s=>active.has(s.id)||linked.has(s.id)).map(s=>({...s,available:active.has(s.id)}));
}


export const inputPortKey=(port:InputPort)=>`${port.step_id}/${port.input_slot_id}`;
export const sharedGroup=(groups:InputPort[][],port:InputPort)=>groups.find(g=>g.some(p=>inputPortKey(p)===inputPortKey(port)));
export const detachSharedInput=(groups:InputPort[][],port:InputPort)=>groups.map(g=>g.filter(p=>inputPortKey(p)!==inputPortKey(port))).filter(g=>g.length>1);
export const retainSharedNodes=(groups:InputPort[][],steps:Node[])=>groups.map(g=>g.filter(p=>steps.some(n=>n.id===p.step_id))).filter(g=>g.length>1);
/** Shared references keep conditional input ports visible without pretending to be upstream outputs. */
export function displayBindings(node:Node,groups:InputPort[][]):Record<string,unknown>{
  return {...node.inputs,...Object.fromEntries(groups.flat().filter(p=>p.step_id===node.id).map(p=>[p.input_slot_id,true]))};
}
export function shareInputs(draft:Draft,definitions:Record<string,Method>,from:InputPort,to:InputPort):Draft{
  if(inputPortKey(from)===inputPortKey(to))throw new Error(ui("请选择两个不同的输入端口"));
  const groups=draft.shared_inputs??[];
  const combined=[...(sharedGroup(groups,from)??[from]),...(sharedGroup(groups,to)??[to])];
  const members=[...new Map(combined.map(p=>[inputPortKey(p),p])).values()];
  const slots=members.map(p=>{
    const node=draft.steps.find(n=>n.id===p.step_id);
    const slot=node&&definitions[node.method_id]?.input_slots.find(s=>s.id===p.input_slot_id);
    if(!node||!slot)throw new Error(ui("输入端口定义尚未加载或不存在"));
    if(node.inputs[p.input_slot_id])throw new Error(ui("已连接上游输出的输入不能同时共享外部文件，请先解除上游连接"));
    return slot;
  });
  if(slots.some(s=>s.type!==slots[0].type||s.cardinality!==slots[0].cardinality||s.binding!==slots[0].binding))throw new Error(ui("共享输入的类型、文件数量和文件/目录形式必须一致"));
  const formats=slots.filter(s=>s.formats.length).map(s=>s.formats);
  if(formats.length&&!formats[0].some(f=>formats.every(choices=>choices.includes(f))))throw new Error(ui("共享输入没有共同支持的文件格式"));
  if(!slots[0].source_options.some(f=>slots.every(s=>s.source_options.includes(f))))throw new Error(ui("共享输入没有共同支持的文件来源"));
  return {...draft,shared_inputs:[...groups.filter(g=>!g.some(p=>members.some(m=>inputPortKey(m)===inputPortKey(p)))),members]};
}
