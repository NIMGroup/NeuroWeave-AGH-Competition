import type { Method, Node } from './libraryApi';
const images=new Set(['imaging_input','t1w_image','bold_image','preprocessed_dwi','raw_dwi','brain_mask','five_tt_image','seed_mask','scalar_image','label_image','parcellation_image','registered_image','fod_image','z_stat_map','contrast_image']);
const files=new Set([...images,'transform_matrix','gradient_table','b_values','b_vectors','response_functions','tractogram','streamline_weights','connectome','connectome_matrix','roi_timeseries','generated_file']);
export const compatible=(source:string,target:string)=>source===target||(target==='imaging_input'&&images.has(source))||(['generated_file','analysis_input'].includes(target)&&files.has(source));
export function nodeMethod(node:Node,definitions:Record<string,Method>,steps:Node[],seen=new Set<string>()):Method|undefined {
  const method=definitions[node.method_id];if(!method||seen.has(node.id)||node.method_id!=='mrtrix.command.mrtransform')return method;
  seen.add(node.id);const link=node.inputs.input;const source=link&&steps.find(n=>n.id===link.step_id);
  const kind=source&&nodeMethod(source,definitions,steps,seen)?.output_slots.find(s=>s.id===link.output_slot_id)?.type;
  const values={...Object.fromEntries(method.parameters.map(p=>[p.id,p.default])),...node.parameters};
  if(!kind||!['parcellation_image','label_image','brain_mask','seed_mask'].includes(kind)||values.interp!=='nearest'||values.modulate)return method;
  return {...method,output_slots:method.output_slots.map(s=>s.id==='output'?{...s,type:kind}:s)};
}
