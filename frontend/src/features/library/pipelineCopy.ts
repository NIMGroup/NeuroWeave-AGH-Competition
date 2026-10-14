import type { Draft, Node, Pipeline } from './libraryApi';
import { ui } from '../../lib/libraryText';

/** No identity, version association or recovery key may leak into a new copy. */
export function copyPipeline(pipeline: Pipeline, source: Draft): Pipeline {
  const editor = structuredClone(source);
  editor.name = ui('{0}（副本）', source.name).slice(0, 160);
  delete editor.expected_version;
  return {id:'', version:'', label:editor.name, description:editor.description,
    editor, parameters:pipeline.parameters, input_slots:pipeline.input_slots,
    output_slots:pipeline.output_slots, steps:pipeline.steps};
}

export function stageCondition(node:Node,draft:Draft):string {
  return Object.entries(node.when??{}).map(([key,values])=>{
    const spec=draft.controls?.find(p=>p.id===key);
    return `${spec?.label??key} = ${values.map(v=>spec?.options.find(o=>o.value===v)?.label??String(v)).join(' / ')}`;
  }).join('；');
}
