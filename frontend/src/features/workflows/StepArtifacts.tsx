import { useState } from 'react';
import { GitBranch, Loader2 } from 'lucide-react';
import { ArtifactPathPicker } from '../datasets/ArtifactPathPicker';
import { HistoricalInputPicker } from '../datasets/HistoricalInputPicker';
import { InputRuleEditor } from './InputRuleEditor';
import type {
  InputBinding,
  InputSelectionMode,
  MethodOption,
  ParameterValue,
  Workflow,
  WorkflowStep
} from './workflowTypes';
import { activeOutputSlots, displayedInputSlots } from './workflowConditions';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';

type StepArtifactsProps = {
  method: MethodOption;
  step: WorkflowStep;
  workflow: Workflow;
  selectedParameters: Record<string, ParameterValue>;
  busySlotId: string | null;
  busyCompleting: boolean;
  disabled: boolean;
  locked: boolean;
  onBind: (slotId: string, rootId: string, relativePath: string, mode?: InputSelectionMode) => void;
  onClear: (slotId: string) => void;
  onBindHistory: (slotId: string, runId: string, groupId: string, allowPartial: boolean) => void;
  onCompleteDependencies: () => void;
};

function bindingSummary(binding: InputBinding, t: TranslationKeys) {
  if (binding.confirmed_by_user && binding.deferred_resolution) return t.workflowInputSelectedPending.replace('{{input}}', binding.path_template || binding.label);
  if (binding.deferred_resolution) return t.workflowInputPendingCheck.replace('{{input}}', binding.path_template || binding.label);
  if (binding.source === 'upstream_output') {
    return t.workflowInputFromUpstream
      .replace('{{step}}', String(binding.upstream_step_id))
      .replace('{{slot}}', String(binding.output_slot_id));
  }
  if (binding.history) return binding.label;
  if (binding.binding_mode === 'per_sample') {
    return t.workflowInputPerSample
      .replace('{{count}}', String(binding.coverage?.matched ?? 0))
      .replace('{{template}}', binding.path_template || binding.relative_path || '');
  }
  return binding.relative_path || binding.label;
}

/** Input slots (bindings + pickers), declared outputs and upstream completion. */
export function StepArtifacts({
  method,
  step,
  workflow,
  selectedParameters,
  busySlotId,
  busyCompleting,
  disabled,
  locked,
  onBind,
  onClear,
  onBindHistory,
  onCompleteDependencies
}: StepArtifactsProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [showConditionalInputs, setShowConditionalInputs] = useState(false);
  const inputSlots = displayedInputSlots(method, selectedParameters, step.input_bindings ?? {}, showConditionalInputs);
  const conditionalCount = displayedInputSlots(method, selectedParameters, {}, true).filter(slot => slot.conditionalOptional).length;
  const outputSlots = activeOutputSlots(method, selectedParameters);
  const bindings = step.input_bindings ?? {};
  const pendingCheck = Object.values(bindings).some(binding => binding.deferred_resolution);
  const missing = new Set((step.missing_required_inputs ?? []).filter(id => inputSlots.some(slot => slot.id === id && slot.requiredNow)));
  const canCompleteUpstream = workflow.input_selection !== 'user' && inputSlots.some(
    (slot) =>
      missing.has(slot.id) &&
      !step.manual_input_types?.includes(slot.type) &&
      ['raw_dwi', 'bids_dwi', 'preprocessed_dwi'].includes(slot.type)
  );

  return (
    <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <section className="rounded-card border border-line p-3 ">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-xs font-semibold">{t.workflowInputsTitle}</p>
            <p className="mt-0.5 text-micro text-subtle">{workflow.input_selection === 'user' ? t.workflowInputsUserHint : t.workflowInputsHint}</p>
          </div>
          {missing.size > 0 ? (
            <span className="rounded border border-warning-line bg-warning-quiet px-1.5 py-0.5 text-micro text-warning dark:text-warning">
              {t.workflowInputsMissing.replace('{{count}}', String(missing.size))}
            </span>
          ) : (
            <span className="rounded border border-success-line bg-success-quiet px-1.5 py-0.5 text-micro text-success ">
              {pendingCheck ? t.workflowInputsPendingCheck : t.workflowInputsReady}
            </span>
          )}
        </div>
        {conditionalCount > 0 && <label className="mb-3 flex items-center gap-2 text-meta text-subtle">
          <input type="checkbox" checked={showConditionalInputs} onChange={e => setShowConditionalInputs(e.target.checked)}/>
          {t.workflowShowConditionalInputs}
        </label>}
        <div className="space-y-2">
          {inputSlots.map((slot) => {
            const required = slot.requiredNow;
            const binding = bindings[slot.id];
            const suggestion = step.input_suggestions?.[slot.id];
            return (
              <div
                key={slot.id}
                className={`rounded-control border p-3 ${
                  missing.has(slot.id)
                    ? 'border-warning-line bg-warning-quiet '
                    : binding && !binding.deferred_resolution
                      ? 'border-success-line bg-success-quiet/40 '
                      : 'border-line'
                }`}
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <p className="text-xs font-medium">{slot.label}</p>
                  <span className="rounded border border-line px-1.5 py-0.5 font-mono text-micro text-subtle ">
                    {slot.formats.join(', ') || slot.binding}
                  </span>
                  <span className="rounded bg-well px-1.5 py-0.5 text-micro text-muted ">
                    {required ? t.workflowInputRequiredLabel : t.workflowInputOptionalLabel}
                  </span>
                </div>
                <p className="mt-1 text-meta leading-4 text-subtle">{slot.description}</p>
                {slot.condition_help && <p className="mt-1 text-meta leading-4 text-subtle">{slot.condition_help}</p>}
                {slot.conditionalOptional && <p className="mt-1 text-meta text-subtle">{t.workflowConditionalInputHint}</p>}
                {binding && (
                  <p className={`mt-1.5 break-all text-micro leading-4 ${binding.deferred_resolution ? 'text-muted' : 'text-success'}`}>
                    {bindingSummary(binding, t)}
                  </p>
                )}
                {!binding && required && (
                  <p className="mt-1.5 text-micro text-warning">{t.workflowInputUnresolved}</p>
                )}
                {!binding && suggestion && <div className="mt-2 rounded-control border border-line p-2">
                  <p className="text-micro text-subtle">{t.workflowInputSuggestionTitle}</p>
                  <p className="mt-1 break-all font-mono text-micro">{suggestion.path}</p>
                  <button type="button" disabled={disabled || !suggestion.root_id || !workflow.dataset}
                    onClick={() => suggestion.root_id && onBind(slot.id, suggestion.root_id, suggestion.relative_path, suggestion.binding_mode)}
                    className="mt-2 rounded-control border border-line px-2 py-1 text-xs disabled:opacity-50">{t.workflowInputAdoptSuggestion}</button>
                </div>}
                {binding?.source === 'existing_path' && <button type="button" disabled={disabled}
                  onClick={() => onClear(slot.id)} className="mt-2 rounded-control border border-line px-2 py-1 text-xs disabled:opacity-50">{t.workflowInputClear}</button>}
                {!binding && step.manual_input_types?.includes(slot.type) && (
                  <p className="mt-1.5 text-meta leading-4 text-warning dark:text-warning">
                    {t.workflowInputProducerRemoved}{' '}
                    {slot.source_options.includes('existing_path')
                      ? locked
                        ? t.workflowInputProducerRemovedLocked
                        : t.workflowInputProducerRemovedUnlocked
                      : t.workflowInputProducerRemovedUnsupported}
                  </p>
                )}
                {slot.source_options.includes('existing_path') && (
                  <div className="mt-2 flex min-w-0 flex-wrap gap-2">
                    {busySlotId === slot.id ? (
                      <Loader2 className="h-4 w-4 animate-spin text-accent" />
                    ) : (
                      <>
                        <ArtifactPathPicker
                          workflowId={workflow.workflow_id}
                          stepId={step.id}
                          slot={slot}
                          dataset={workflow.dataset}
                          disabled={disabled}
                          onSelected={(rootId, relativePath, mode) =>
                            onBind(slot.id, rootId, relativePath, mode)
                          }
                        />
                        {workflow.dataset && (
                          <HistoricalInputPicker
                            key={workflow.revision}
                            workflowId={workflow.workflow_id}
                            revision={workflow.revision}
                            stepId={step.id}
                            slotId={slot.id}
                            slotLabel={slot.label}
                            disabled={disabled}
                            onSelected={(runId, groupId, allowPartial) =>
                              onBindHistory(slot.id, runId, groupId, allowPartial)
                            }
                          />
                        )}
                      </>
                    )}
                    {workflow.input_selection === 'user' && workflow.dataset && <InputRuleEditor
                      key={workflow.revision} workflowId={workflow.workflow_id} stepId={step.id} slotId={slot.id}
                      dataset={workflow.dataset} binding={binding} disabled={disabled || busySlotId === slot.id}
                      onSelected={(rootId, path, mode) => onBind(slot.id, rootId, path, mode)} />}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {canCompleteUpstream && workflow.dependency_policy !== 'complete' && (
          <div className="mt-3 rounded-control border border-accent-line bg-accent-quiet p-3 dark:border-accent-line ">
            <p className="text-xs font-medium text-accent">{t.workflowCompleteUpstreamTitle}</p>
            <p className="mt-1 text-meta text-accent">{t.workflowCompleteUpstreamHint}</p>
            <button
              type="button"
              disabled={disabled || busyCompleting}
              onClick={onCompleteDependencies}
              className="mt-2 inline-flex items-center gap-1.5 rounded-control border border-accent-line px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 dark:border-accent-line"
            >
              {busyCompleting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <GitBranch className="h-3.5 w-3.5" />
              )}
              {t.workflowCompleteUpstream}
            </button>
          </div>
        )}
      </section>

      <section className="rounded-card border border-line p-3 ">
        <p className="text-xs font-semibold">{t.workflowOutputsTitle}</p>
        <p className="mt-0.5 text-micro text-subtle">{t.workflowOutputsHint}</p>
        <div className="mt-3 space-y-2">
          {outputSlots.map((slot) => (
            <div
              key={slot.id}
              className="rounded-control border border-artifact-line bg-artifact-quiet p-3 "
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <p className="text-xs font-medium">{slot.label}</p>
                {slot.primary && (
                  <span className="rounded bg-artifact-quiet px-1.5 py-0.5 text-micro text-artifact dark:text-artifact">
                    {t.workflowOutputPrimary}
                  </span>
                )}
              </div>
              <p className="mt-1 text-meta leading-4 text-subtle">{slot.description}</p>
              {slot.path_template && (
                <p className="mt-1.5 break-all font-mono text-micro leading-4 text-artifact dark:text-artifact">
                  {slot.path_template}
                </p>
              )}
            </div>
          ))}
          {outputSlots.length === 0 && (
            <p className="rounded-control border border-dashed border-line-strong px-3 py-6 text-center text-xs text-subtle ">
              {t.workflowOutputEmpty}
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
