import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import type { MethodOption, ParameterSpec, ParameterValue } from './workflowTypes';
import { decodeOption, optionKey, visibleParameters, parameterValues, parameterEnabled, parameterRequired } from './workflowConditions';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';

type StepParametersProps = {
  method: MethodOption;
  stepId: string;
  busyParameterId: string | null;
  disabled: boolean;
  reviewed: boolean;
  selected: Record<string, ParameterValue>;
  onChange: (parameterId: string, value: ParameterValue) => void;
};

export function StepParameters({
  method,
  stepId,
  busyParameterId,
  disabled,
  reviewed,
  selected,
  onChange
}: StepParametersProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const values = parameterValues(method, selected);
  const visible = visibleParameters(method, selected);
  const basic = visible.filter((spec) => spec.level !== 'advanced');
  const advanced = visible.filter((spec) => spec.level === 'advanced');
  const groups = Object.entries(
    advanced.reduce<Record<string, ParameterSpec[]>>((accumulator, spec) => {
      const group = spec.group || t.workflowParametersAdvanced;
      accumulator[group] = [...(accumulator[group] ?? []), spec];
      return accumulator;
    }, {})
  );

  const control = (spec: ParameterSpec) => (
    <ParameterControl
      key={spec.id}
      t={t}
      spec={{...spec, required: parameterRequired(spec, values)}}
      conditionHint={spec.condition_help || (Object.keys(spec.enabled_when ?? {}).length > 0 && !parameterEnabled(spec, values)
        ? (language === 'zh' ? '当前配置下不生效，不会传入命令。' : 'Inactive for this configuration; omitted from the command.') : '')}
      controlId={`parameter-${stepId}-${spec.id}`}
      value={Object.hasOwn(selected, spec.id) ? selected[spec.id] : spec.default}
      busy={busyParameterId === spec.id}
      disabled={disabled || !parameterEnabled(spec, values)}
      onChange={(value) => onChange(spec.id, value)}
    />
  );

  return (
    <div className="mt-3 rounded-card border border-line p-3 ">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold">
            {method.execution_mode === 'planning'
              ? t.workflowParametersTitlePlanning
              : t.workflowParametersTitleRunner}
          </p>
          <p className="mt-0.5 text-meta text-subtle">
            {t.workflowParametersSummary
              .replace('{{basic}}', String(basic.length))
              .replace('{{advanced}}', String(advanced.length))}
          </p>
        </div>
        {reviewed && (
          <span className="rounded border border-success-line bg-success-quiet px-1.5 py-0.5 text-micro text-success ">
            {t.workflowParametersReviewed}
          </span>
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">{basic.map(control)}</div>
      {advanced.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer rounded-control border border-accent-line bg-accent-quiet px-3 py-2 text-xs font-medium text-accent dark:border-accent-line dark:text-accent">
            {t.workflowParametersAdvancedCount.replace('{{count}}', String(advanced.length))}
          </summary>
          <div className="mt-3 space-y-4 rounded-control border border-line p-3 ">
            {groups.map(([group, specs]) => (
              <section key={group}>
                <p className="mb-2 text-micro font-semibold uppercase tracking-wide text-subtle">
                  {group}
                </p>
                <div className="grid gap-3 sm:grid-cols-2">{specs.map(control)}</div>
              </section>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

type ParameterControlProps = {
  conditionHint?: string;
  t: TranslationKeys;
  spec: ParameterSpec;
  controlId: string;
  value: ParameterValue;
  busy: boolean;
  disabled: boolean;
  onChange: (value: ParameterValue) => void;
};

function ParameterControl({
  conditionHint,
  t,
  spec,
  controlId,
  value,
  busy,
  disabled,
  onChange
}: ParameterControlProps) {
  const optional = !spec.required ? (
    <span className="ml-1 font-normal text-subtle">{t.workflowParameterOptional}</span>
  ) : null;
  if (spec.type === 'boolean') {
    return (
      <div className="flex min-h-20 items-start justify-between gap-3 rounded-control bg-well p-3 ">
        <div className="min-w-0 flex-1">
          <label htmlFor={controlId} className="text-xs font-medium">
            {spec.label}
            {optional}
          </label>
          <p className="mt-1 text-meta leading-4 text-subtle">{spec.description}</p>
          {conditionHint && <p className="mt-1 text-meta text-accent">{conditionHint}</p>}
        </div>
        {busy ? (
          <Loader2 className="mt-0.5 h-4 w-4 animate-spin text-accent" />
        ) : (
          <input
            id={controlId}
            type="checkbox"
            role="switch"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(event) => onChange(event.target.checked)}
            className="mt-0.5 h-4 w-4"
          />
        )}
      </div>
    );
  }

  const useSelect =
    spec.control === 'select' ||
    ((!spec.control || spec.control === 'auto') && spec.options.length > 0);
  const selectedOption = spec.options.find(
    (option) => optionKey(option.value) === optionKey(value)
  );

  return (
    <div className="min-h-20 min-w-0 rounded-control bg-well p-3 ">
      <div className="mb-2 flex items-center justify-between gap-2">
        <label htmlFor={controlId} className="text-xs font-medium">
          {spec.label}
          {optional}
          {spec.unit ? <span className="ml-1 font-normal text-subtle">({spec.unit})</span> : null}
        </label>
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />}
      </div>
      {useSelect ? (
        <select
          id={controlId}
          disabled={busy || disabled}
          value={value === null ? '' : optionKey(value)}
          onChange={(event) => onChange(event.target.value === '' ? null : decodeOption(spec, event.target.value))}
          className="w-full rounded-control border border-line bg-transparent px-2 py-1.5 text-xs "
        >
          {!spec.required && <option value="">{t.workflowParameterOptional}</option>}
          {spec.options.map((option) => (
            <option key={optionKey(option.value)} value={optionKey(option.value)}>
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <DirectParameterInput
          key={`${spec.id}:${String(value)}`}
          spec={spec}
          controlId={controlId}
          value={value}
          busy={busy}
          disabled={disabled}
          onChange={onChange}
        />
      )}
      <p className="mt-1.5 text-meta leading-4 text-subtle">{spec.description}</p>
      {!spec.required && spec.software_default && <p className="mt-1 text-micro text-subtle">{spec.software_default}</p>}
          {conditionHint && <p className="mt-1 text-meta text-accent">{conditionHint}</p>}
      {selectedOption?.description && (
        <p className="mt-1 text-micro leading-4 text-accent">{selectedOption.description}</p>
      )}
    </div>
  );
}

type DirectParameterInputProps = {
  spec: ParameterSpec;
  controlId: string;
  value: ParameterValue;
  busy: boolean;
  disabled: boolean;
  onChange: (value: ParameterValue) => void;
};

/** Free-form values are committed on blur/Enter so autosaves stay meaningful. */
function DirectParameterInput({
  spec,
  controlId,
  value,
  busy,
  disabled,
  onChange
}: DirectParameterInputProps) {
  const [draft, setDraft] = useState(value === null ? '' : String(value));

  const commit = () => {
    if (spec.type === 'string') {
      if (draft !== value) onChange(!draft && !spec.required ? null : draft);
      return;
    }
    const parsed = spec.type === 'integer' ? Number.parseInt(draft, 10) : Number(draft);
    if (!draft.trim() && !spec.required) {
      if (value !== null) onChange(null);
      return;
    }
    if (!Number.isFinite(parsed)) {
      setDraft(value === null ? '' : String(value));
      return;
    }
    if (parsed !== value) onChange(parsed);
  };

  return (
    <input
      id={controlId}
      type={spec.type === 'string' ? 'text' : 'number'}
      value={draft}
      disabled={busy || disabled}
      min={spec.minimum ?? undefined}
      max={spec.maximum ?? undefined}
      step={spec.step ?? undefined}
      placeholder={spec.placeholder}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
      }}
      className="w-full rounded-control border border-line bg-transparent px-2 py-1.5 text-xs "
    />
  );
}
