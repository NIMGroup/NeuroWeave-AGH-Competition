import type { Draft } from './libraryApi';
import { ui } from '../../lib/libraryText';
import { field } from './pipelineEditorStyles';

/** Imported conditional stages retain their original public switch in copies. */
export function PipelineControls({draft,disabled,onChange}:{draft:Draft;disabled:boolean;onChange:(d:Draft)=>void}) {
  if(!draft.controls?.length)return null;
  return <details className="max-h-[25dvh] shrink-0 overflow-auto rounded border border-line px-3 py-2 text-xs">
    <summary className="cursor-pointer font-medium">{ui('Pipeline 控制参数')}</summary>
    <div className="grid gap-3 py-2 sm:grid-cols-2">{draft.controls.map(p=><label key={p.id}>{p.label}
      <select className={field} disabled={disabled} value={JSON.stringify(p.default)} onChange={e=>onChange({...draft,controls:draft.controls!.map(c=>c.id===p.id?{...c,default:JSON.parse(e.target.value)}:c)})}>
        {p.options.map(o=><option key={JSON.stringify(o.value)} value={JSON.stringify(o.value)}>{o.label}</option>)}
      </select><span className="text-subtle">{p.description}</span>
    </label>)}</div>
  </details>;
}
