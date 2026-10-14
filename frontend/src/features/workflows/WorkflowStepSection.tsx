import { useId, useState } from 'react';
import { Check, ChevronDown, ChevronUp, Code2, Loader2 } from 'lucide-react';
import { StepArtifacts } from './StepArtifacts';
import { StepParameters } from './StepParameters';
import type { MethodOption, Workflow, WorkflowStep } from './workflowTypes';
import type { WorkflowStepAction } from './useWorkflows';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';

type WorkflowStepSectionProps = {
  workflow: Workflow;
  step: WorkflowStep;
  locked: boolean;
  disabled: boolean;
  busyKey: string | null;
  busyParameterId: string | null;
  busySlotId: string | null;
  onAction: (action: WorkflowStepAction) => void;
};

const RESOLUTION_KEYS: Record<string, keyof TranslationKeys> = {
  missing_runtime: 'workflowResolutionMissingRuntime',
  missing_implementation: 'workflowResolutionMissingImplementation',
  needs_protocol: 'workflowResolutionNeedsProtocol',
  interactive: 'workflowResolutionInteractive',
  external_service: 'workflowResolutionExternalService',
  source_error: 'workflowResolutionSourceError',
  platform_managed: 'workflowResolutionPlatformManaged',
  unreviewed: 'workflowResolutionUnreviewed'
};

function methodExecutionLabel(method: MethodOption, t: TranslationKeys): string {
  if (method.execution_mode === 'runner') {
    return method.execution_recipe
      ? t.workflowRunnerRecipeLabel.replace(
          '{{count}}',
          String(method.execution_recipe.steps.length)
        )
      : t.workflowAtomicMethod;
  }
  const key = RESOLUTION_KEYS[method.execution_resolution?.state ?? ''];
  return key ? t[key] : t.workflowResolutionPlanningOnly;
}

function MethodGuidance({
  method,
  includeInputs,
  t
}: {
  method: MethodOption;
  includeInputs: boolean;
  t: TranslationKeys;
}) {
  const resolution = method.execution_resolution;
  const warnings = [
    ...(resolution && resolution.state !== 'mapped'
      ? [
          resolution.reason,
          ...(resolution.requirements?.length
            ? [
                t.workflowMethodRequirements.replace(
                  '{{items}}',
                  resolution.requirements.join('; ')
                )
              ]
            : [])
        ]
      : []),
    ...(includeInputs ? (method.input_warnings ?? []) : []),
    ...(method.scope_warnings ?? [])
  ];
  if (!warnings.length) return null;
  return (
    <span className="mt-1.5 block space-y-1 break-words text-meta leading-5 text-warning">
      {[...new Set(warnings.filter((warning): warning is string => typeof warning === 'string' && warning.length > 0))].map((warning) => (
        <span key={warning} className="block">
          {warning}
        </span>
      ))}
    </span>
  );
}

function MethodEvidence({ method, t }: { method: MethodOption; t: TranslationKeys }) {
  const commands = method.command_references ?? [];
  const sources = method.source_references ?? [];
  if (commands.length === 0 && sources.length === 0) return null;
  return (
    <details className="mt-2 rounded-control border border-line px-3 py-2 ">
      <summary className="cursor-pointer text-meta font-medium text-muted">
        {t.workflowMethodEvidence}
      </summary>
      <div className="mt-2 space-y-3">
        {commands.length > 0 && (
          <div>
            <p className="text-micro font-medium uppercase tracking-wide text-subtle">
              {t.workflowMethodReferenceCommands}
            </p>
            <div className="mt-1.5 space-y-2">
              {commands.map((reference, index) => (
                <div
                  key={`${reference.source_path}:${reference.line_start}:${index}`}
                  className="rounded-chip bg-well px-2.5 py-2"
                >
                  <code className="block overflow-x-auto whitespace-pre-wrap break-words text-micro leading-4 text-ink">
                    {reference.command}
                  </code>
                  <p className="mt-1 text-micro text-subtle">
                    {reference.source_path}
                    {reference.line_start ? `:${reference.line_start}` : ''} · {reference.kind}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
        {sources.length > 0 && (
          <div>
            <p className="text-micro font-medium uppercase tracking-wide text-subtle">
              {t.workflowMethodSources}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {sources.map((source, index) => (
                <span
                  key={`${source.path}:${source.line_start}:${index}`}
                  className="max-w-full truncate rounded bg-well px-1.5 py-0.5 text-micro text-muted "
                >
                  {source.path}
                  {source.line_start ? `:${source.line_start}` : ''}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </details>
  );
}

export function WorkflowStepSection({
  workflow,
  step,
  locked,
  disabled,
  busyKey,
  busyParameterId,
  busySlotId,
  onAction
}: WorkflowStepSectionProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [expanded, setExpanded] = useState(workflow.input_selection === 'user' && Boolean(step.missing_required_inputs?.length));
  const detailsId = useId();
  const method = step.method_options.find((option) => option.id === step.selected_method_id);
  const methodBusy = busyKey === `method:${step.id}`;
  const removeBusy = busyKey === `remove-step:${step.id}`;
  const showArtifacts = Boolean(
    method &&
    (locked || workflow.input_selection === 'user' || Boolean(step.manual_input_types?.length)) &&
    ((method.input_slots?.length ?? 0) > 0 || (method.output_slots?.length ?? 0) > 0)
  );

  return (
    <section
      className={`relative rounded-card border p-4 ${
        step.status === 'needs_method' ? 'border-running-line' : 'border-line'
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`grid h-7 w-7 shrink-0 place-items-center rounded-full border text-xs font-semibold ${
            method
              ? 'border-success-line bg-success-quiet text-success '
              : 'border-accent-line bg-accent-quiet text-accent dark:border-accent-line dark:text-accent'
          }`}
        >
          {method ? <Check className="h-3.5 w-3.5" /> : step.order}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 className="break-words text-xs font-semibold">{step.title}</h3>
              <p className="mt-0.5 break-words text-meta leading-5 text-subtle">
                {expanded ? step.description : method?.label ?? t.workflowStatusAwaitingMethodChoice}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {step.origin === 'dependency' && (
                <span className="rounded bg-cyan-50 px-1.5 py-0.5 text-micro text-cyan-700 dark:bg-cyan-950/40 dark:text-upstream">
                  {t.workflowAutoUpstream}
                </span>
              )}
              {method && (
                <>
                  <span
                    className={`rounded px-1.5 py-0.5 text-micro ${
                      method.execution_mode === 'planning'
                        ? 'bg-warning-quiet text-warning dark:text-warning'
                        : 'bg-success-quiet text-success '
                    }`}
                  >
                    {methodExecutionLabel(method, t)}
                  </span>
                  <span className="rounded border border-line px-1.5 py-0.5 font-mono text-micro text-subtle ">
                    {method.skill_id}
                  </span>
                </>
              )}
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={detailsId}
                aria-label={`${expanded ? t.workflowCollapseStep : t.workflowExpandStep}: ${step.title}`}
                onClick={() => setExpanded(previous => !previous)}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-control border border-line px-2 py-1 text-meta hover:bg-hover"
              >
                {expanded ? <ChevronUp className="size-3.5" aria-hidden="true" />
                  : <ChevronDown className="size-3.5" aria-hidden="true" />}
                {expanded ? t.workflowCollapseStep : t.workflowExpandStep}
              </button>
              {!locked && workflow.status !== 'confirmed' && (
                <button
                  type="button"
                  aria-label={t.workflowRemoveStep}
                  title={workflow.steps.length <= 1 ? t.workflowKeepOneStep : t.workflowRemoveStep}
                  disabled={disabled || removeBusy || workflow.steps.length <= 1}
                  onClick={() => onAction({ kind: 'remove-step', stepId: step.id })}
                  className="rounded-control border border-line px-2 py-1 text-meta disabled:opacity-50 "
                >
                  {removeBusy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    t.workflowRemoveStep
                  )}
                </button>
              )}
            </div>
          </div>
          {/* Keep inputs and parameter drafts mounted when either disclosure closes. */}
          <div id={detailsId} hidden={!expanded}>
          {!locked && (step.method_options.length > 1 || !method) && (
            <fieldset aria-label={step.title} disabled={disabled} className="mt-3 grid gap-2">
              {step.method_options.map((option) => {
                const controlId = `${step.id}-${option.id}`;
                return (
                  <label
                    key={option.id}
                    htmlFor={controlId}
                    className={`flex items-start gap-2 rounded-control border p-3 ${
                      step.selected_method_id === option.id
                        ? 'border-accent-line bg-accent-quiet/50 dark:border-accent-line '
                        : 'border-line'
                    }`}
                  >
                    <input
                      id={controlId}
                      type="radio"
                      name={`method-${step.id}`}
                      value={option.id}
                      disabled={!option.compatible || disabled}
                      checked={step.selected_method_id === option.id}
                      onChange={() =>
                        onAction({ kind: 'method', stepId: step.id, methodId: option.id })
                      }
                      className="mt-0.5"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-semibold">{option.label}</span>
                        {option.recommended && option.compatible && (
                          <span className="rounded bg-accent-quiet px-1.5 py-0.5 text-micro text-accent dark:bg-accent-quiet dark:text-accent">
                            {t.workflowMethodRecommended}
                          </span>
                        )}
                        {!option.compatible && (
                          <span className="rounded bg-warning-quiet px-1.5 py-0.5 text-micro text-warning ">
                            {t.workflowMethodNotExecutable}
                          </span>
                        )}
                        {option.execution_mode === 'planning' && (
                          <span className="rounded bg-warning-quiet px-1.5 py-0.5 text-micro text-warning dark:text-warning">
                            {methodExecutionLabel(option, t)}
                          </span>
                        )}
                      </span>
                      <span className="mt-1 block text-meta leading-5 text-muted">
                        {option.description}
                      </span>
                      <span className="mt-1 block text-micro text-subtle">
                        {option.structure_level === 'reviewed'
                          ? t.workflowStructureReviewed
                          : option.structure_level === 'evidence'
                            ? option.command_references?.length
                              ? t.workflowStructureEvidence.replace(
                                  '{{count}}',
                                  String(option.command_references.length)
                                )
                              : t.workflowStructureEvidencePlain
                            : t.workflowStructureCategory}
                      </span>
                      {option.recommended && option.recommendation_reason && option.compatible && (
                        <span className="mt-1 block text-meta leading-5 text-accent">
                          {option.recommendation_reason}
                        </span>
                      )}
                      {!option.compatible && option.blocked_reasons.length > 0 && (
                        <span className="mt-1 block text-meta leading-5 text-warning">
                          {option.blocked_reasons.join('; ')}
                        </span>
                      )}
                      <MethodGuidance method={option} includeInputs t={t} />
                    </span>
                  </label>
                );
              })}
            </fieldset>
          )}

          {!locked && step.method_options.length === 1 && method && (
            <div className="mt-3 rounded-control border border-line p-3 text-meta text-muted ">
              <span className="font-medium text-muted dark:text-ink">{method.label}</span>{' '}
              <span>{method.description}</span>
            </div>
          )}

          {methodBusy && (
            <p className="mt-2 flex items-center gap-2 text-meta text-accent">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t.workflowMethodUpdating}
            </p>
          )}

          {locked && method && (
            <div className="mt-3 rounded-control border border-success-line bg-success-quiet/60 p-3 ">
              <p className="text-micro font-medium uppercase tracking-wide text-success ">
                {t.workflowMethodLockedIn}
              </p>
              <p className="mt-1 text-xs font-medium">{method.label}</p>
              <p className="mt-1 text-meta leading-5 text-muted">{method.description}</p>
              {method.execution_recipe && (
                <p className="mt-2 flex flex-wrap items-center gap-1 text-micro text-success">
                  {method.execution_recipe.steps.map((recipeStep) => (
                    <span
                      key={recipeStep.id}
                      className="rounded bg-raised px-1.5 py-0.5 ring-1 ring-success-line dark:bg-transparent "
                    >
                      {recipeStep.title}
                    </span>
                  ))}
                </p>
              )}
            </div>
          )}

          {method && <MethodEvidence method={method} t={t} />}
          {method && (locked || Boolean(step.manual_input_types?.length)) && (
            <MethodGuidance method={method} includeInputs={!locked} t={t} />
          )}
          {showArtifacts && method && (
            <StepArtifacts
              method={method}
              step={step}
              workflow={workflow}
              selectedParameters={step.selected_parameters}
              busySlotId={busySlotId}
              busyCompleting={busyKey === 'complete-dependencies'}
              disabled={disabled || (!locked && workflow.input_selection !== 'user')}
              locked={locked}
              onBind={(slotId, rootId, relativePath, bindingMode) =>
                onAction({ kind: 'input', stepId: step.id, slotId, rootId, relativePath, bindingMode })
              }
              onClear={(slotId) => onAction({kind:'clear-input', stepId:step.id, slotId})}
              onBindHistory={(slotId, runId, groupId, allowPartial) =>
                onAction({
                  kind: 'history',
                  stepId: step.id,
                  slotId,
                  runId,
                  groupId,
                  allowPartial
                })
              }
              onCompleteDependencies={() => onAction({ kind: 'complete-dependencies' })}
            />
          )}

          {locked && method && method.parameters.length > 0 && (
            <StepParameters
              method={method}
              stepId={step.id}
              busyParameterId={busyParameterId}
              disabled={disabled}
              reviewed={Boolean(workflow.parameters_reviewed_at)}
              selected={step.selected_parameters}
              onChange={(parameterId, value) =>
                onAction({
                  kind: 'parameters',
                  stepId: step.id,
                  parameters: { [parameterId]: value }
                })
              }
            />
          )}

          {locked && method && step.command_preview && (
            <div className="mt-3 rounded-control border border-line bg-well px-3 py-2.5 text-ink">
              <div className="mb-1.5 flex items-center gap-2 text-micro font-medium uppercase tracking-wide text-subtle">
                <Code2 className="h-3.5 w-3.5" />
                {method.execution_mode === 'planning'
                  ? t.workflowCommandPlanning
                  : method.execution_recipe
                    ? t.workflowCommandRunnerRecipe
                    : t.workflowCommandRunner}
              </div>
              <code className="block overflow-x-auto whitespace-nowrap text-meta leading-5">
                {step.command_preview}
              </code>
            </div>
          )}
          </div>
        </div>
      </div>
    </section>
  );
}
