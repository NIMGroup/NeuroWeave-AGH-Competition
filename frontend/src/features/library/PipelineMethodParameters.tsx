import { ui } from '../../lib/libraryText';
import type { ParameterSpec, ParameterValue } from '../workflows/workflowTypes';
import { conditionMatches, parameterEnabled, parameterRequired } from '../workflows/workflowConditions';
import type { Method, Node } from './libraryApi';
import { conditionDescription } from './pipelineGraph';
import { field } from './pipelineEditorStyles';

/** One contract projection for draft editing and immutable Pipeline inspection. */
export function PipelineMethodParameters({ method, node, readOnly = false, disabled = false, fixedParameters = [], onChange }: {
  method: Method; node: Node; readOnly?: boolean; disabled?: boolean;
  fixedParameters?: string[]; onChange?: (id: string, value: ParameterValue) => void;
}) {
  const values = Object.fromEntries(method.parameters.map(p => [p.id,
    Object.hasOwn(node.parameters, p.id) ? node.parameters[p.id] : p.default]));
  const text = (p: ParameterSpec, value: ParameterValue) => {
    if (value === null || value === undefined) return ui('未指定');
    return (p.options ?? []).find(o => o.value === value)?.label ??
      (typeof value === 'boolean' ? ui(value ? '是' : '否') : String(value));
  };
  return <section aria-label={ui('步骤参数')}>
    <h4 className="mt-3 text-xs font-semibold">{ui('步骤参数')}</h4>
    {method.parameters.filter(p => readOnly || conditionMatches(p.visible_when ?? {}, values)).map(p => {
      const enabled = parameterEnabled(p, values);
      const controlId = `pipeline-parameter-${node.id}-${p.id}`;
      const options = p.options?.length ? p.options : p.type === 'boolean'
        ? [{ value: true, label: ui('是'), description: '' }, { value: false, label: ui('否'), description: '' }] : [];
      return <div key={p.id} data-parameter-id={p.id} className="mt-3 rounded border border-line p-2 text-xs">
        <label htmlFor={controlId} className="block font-medium">
          {p.label}{parameterRequired(p, values) ? ' *' : ''}
          <code className="ml-1 font-normal text-subtle">{p.argument || p.id}</code>
        </label>
        {options.length ? <select id={controlId} className={field}
          disabled={readOnly || disabled || !enabled} value={JSON.stringify(values[p.id] ?? null)}
          onChange={e => onChange?.(p.id, JSON.parse(e.target.value))}>
          <option value="null">{ui('未指定')}</option>
          {options.map(o => <option key={JSON.stringify(o.value)} value={JSON.stringify(o.value)}>{o.label}</option>)}
        </select> : <input id={controlId} className={field}
          disabled={readOnly || disabled || !enabled}
          type={p.type === 'string' ? 'text' : 'number'}
          min={p.minimum ?? undefined} max={p.maximum ?? undefined}
          step={p.step ?? (p.type === 'integer' ? 1 : 'any')} placeholder={p.placeholder}
          value={String(values[p.id] ?? '')}
          onChange={e => onChange?.(p.id, e.target.value === '' ? null : p.type === 'string' ? e.target.value : Number(e.target.value))}/>}
        <p className="mt-1 break-words text-subtle">{ui('方法默认值：{0}', text(p, p.default))}</p>
        {readOnly && <p className="mt-1 break-words text-accent">
          {ui('Pipeline 预设值：{0}', text(p, values[p.id]))}
          {fixedParameters.includes(p.id) ? ' · ' + ui('固定参数') : ''}
        </p>}
        <p className="mt-1 whitespace-pre-wrap break-words text-subtle">{p.description} {p.usage} {p.condition_help}</p>
        {p.software_default && <p className="mt-1 text-subtle">{p.software_default}</p>}
        {!!options.length && <ul aria-label={ui('可选值')} className="mt-2 space-y-1 text-subtle">
          {options.map(o => <li key={JSON.stringify(o.value)}>
            {o.label} <code>({String(o.value)})</code>{o.description ? '：' + o.description : ''}
          </li>)}
        </ul>}
        {(p.minimum != null || p.maximum != null) && <p className="mt-1 text-subtle">
          {ui('取值范围：{0} 至 {1}', p.minimum ?? '−∞', p.maximum ?? '+∞')}{p.unit ? ' ' + p.unit : ''}
        </p>}
        {!enabled && <p className="mt-1 text-warning">{ui('当前参数下不生效。')}</p>}
        {([[ui('显示条件'), p.visible_when], [ui('启用条件'), p.enabled_when], [ui('必填条件'), p.required_when]] as const)
          .filter(([, value]) => value && Object.keys(value).length).map(([label, value]) =>
            <p key={label} className="mt-1 text-subtle">{label}：{conditionDescription(method, value!)}</p>)}
      </div>;
    })}
  </section>;
}
