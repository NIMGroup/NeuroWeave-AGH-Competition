import { ui } from '../../lib/libraryText';
import { MethodVerification } from '../skills/MethodVerification';
import type { Method, Pipeline } from './libraryApi';

/** Shared read-only projection of the registered contract; never edits the graph. */
export function DefinitionDetails({
  definition, showParameters = true
}: {
  definition: Method | Pipeline; showParameters?: boolean;
}) {
  return (
    <div className="min-w-0 space-y-2 break-words">
      <code className="block break-all text-xs text-subtle">
        {definition.id}
      </code>
      <p className="whitespace-pre-wrap text-sm">{definition.description}</p>
      {'runtime_status' in definition && (
        <p className="my-2 text-xs">
          {ui('运行环境：')}
          {definition.runtime_status.available
            ? ui('可用')
            : ui('尚未满足')} · {definition.runtime_status.reason}
        </p>
      )}
      {'verification' in definition && (
        <MethodVerification value={definition.verification} />
      )}
      {'option_coverage' in definition &&
        !!definition.option_coverage?.length && (
          <details className="my-3 rounded border border-line p-2 text-xs">
            <summary>{ui('尚未开放的命令选项（请检查覆盖范围）')}</summary>
            {definition.option_coverage.map(({ flag, description }) => (
              <p key={flag}>
                {flag}：{description}
              </p>
            ))}
          </details>
        )}
      <h3 className="mt-3 font-medium">{ui('输入')}</h3>
      {definition.input_slots.map((s) => (
        <p key={s.id} className="break-words text-xs">
          {s.label} · {s.type} · {s.required ? ui('必需') : ui('可选/条件必需')}{' '}
          — {s.description}
        </p>
      ))}
      <h3 className="mt-3 font-medium">{ui('输出')}</h3>
      {definition.output_slots.map((s) => (
        <p key={s.id} className="break-words text-xs">
          {s.label} · {s.type} — {s.description}
        </p>
      ))}
      {showParameters && <h3 className="mt-3 font-medium">{ui('参数与用法')}</h3>}
      {showParameters && definition.parameters.map((p) => (
        <details
          key={p.id}
          className="my-2 rounded border border-line p-2 text-xs"
        >
          <summary>
            {p.label} · {p.id} · {p.type}
          </summary>
          <p className="whitespace-pre-wrap">{p.description}</p>
          <p>{p.usage}</p>
          <p>
            {ui('默认：')}
            {String(p.default ?? ui('未指定'))}；
            {p.required ? ui('必需') : ui('可选')} {p.condition_help}
          </p>
          {!!p.options.length && (
            <p>{p.options.map((o) => `${o.label}=${o.value}`).join('；')}</p>
          )}
          {[
            [ui('显示条件'), p.visible_when],
            [ui('启用条件'), p.enabled_when],
            [ui('必填条件'), p.required_when]
          ].map(([label, value]) =>
            value && Object.keys(value).length > 0 ? (
              <p key={String(label)}>
                {String(label)}：{JSON.stringify(value)}
              </p>
            ) : null
          )}
        </details>
      ))}
    </div>
  );
}
