import { ui } from '../../lib/libraryText';
import type {Draft,Pipeline} from './libraryApi';
type StorageAccess=Pick<Storage,'getItem'|'setItem'>;
const key=(p:Pipeline)=>`neuroweave:pipeline-layout:${p.id}:${p.version}`;
const access=(storage?:StorageAccess)=>storage??(typeof localStorage==='undefined'?undefined:localStorage);

/** Browser presentation state is deliberately separate from the execution recipe. */
export function restoreLayout(pipeline:Pipeline,storage?:StorageAccess):Pipeline {
  if(!pipeline.editor)return pipeline;
  try{
    const positions=JSON.parse(access(storage)?.getItem(key(pipeline))??'{}');
    return {...pipeline,editor:{...pipeline.editor,steps:pipeline.editor.steps.map(node=>{
      const p=positions?.[node.id];
      return p&&Number.isFinite(p.x)&&Number.isFinite(p.y)&&p.x>=0&&p.y>=0&&p.x<=50000&&p.y<=50000?{...node,position:{x:p.x,y:p.y}}:node;
    })}};
  }catch{return pipeline;}
}
export function rememberLayout(pipeline:Pipeline,draft:Draft,storage?:StorageAccess):Pipeline {
  try{
    const target=access(storage);
    if(!target)throw new Error('Browser storage unavailable');
    target.setItem(key(pipeline),JSON.stringify(Object.fromEntries(draft.steps.filter(n=>n.position).map(n=>[n.id,n.position]))));
    return restoreLayout(pipeline,target);
  }catch{return {...pipeline,layout_warning:ui("流程和节点位置已保存到服务器；浏览器布局缓存不可用。")};}
}
