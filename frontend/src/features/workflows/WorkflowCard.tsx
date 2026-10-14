import { useEffect, useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDashed,
  FlaskConical,
  Loader2,
  LockKeyhole,
  Play,
  RefreshCw,
  RotateCcw,
  TriangleAlert,
  UnlockKeyhole
} from 'lucide-react';
import { DryRunResult } from './DryRunResult';
import { WorkflowOutputPreview } from './WorkflowOutputPreview';
import { ApiError } from '../../api/http';
import { cancelOperation } from '../../api/operations';
import { DatasetPicker } from '../datasets/DatasetPicker';
import type { Dataset } from '../datasets/datasetsApi';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';
import { RunPanel } from '../runs/RunPanel';
import { cancelRun, resumeRunMonitor, retryRun, startRun } from '../runs/runsApi';
import { useWorkflowRunMonitoring } from '../runs/useRunMonitoring';
import { runStatusActive } from '../runs/runMonitor';
import { WorkspaceOutput } from '../workspace/WorkspaceOutput';
import { request as apiRequest } from '../../api/http';
import { executionErrors } from './executionValidation';
import { ExecutionSettings } from './ExecutionSettings';
import { SampleScopeNotice } from './SampleScopeNotice';
import { useWorkflowCheckStatus, checkIsActive } from './useWorkflowCheckStatus';
import { WorkflowStepSection } from './WorkflowStepSection';
import { useExecutionDraft } from './useExecutionDraft';
import { confirmWorkflowForRun, prepareWorkflowCheck } from './workflowsApi';
import {
  useExecutionBackends,
  useWorkflowAction,
  useWorkflowDataset,
  useWorkflowDryRun,
  useWorkflowQuery,
  workflowActionKey,
  workflowQueryKey
} from './useWorkflows';
import { adoptWorkflow } from './useWorkflows';
import type { WorkflowAction } from './useWorkflows';
import { DEFAULT_EXECUTION_OPTIONS } from './workflowTypes';
import type { ExecutionOptions, Workflow } from './workflowTypes';

const STATUS_KEYS: Record<Workflow['status'], keyof TranslationKeys> = {
  awaiting_method_choice: 'workflowStatusAwaitingMethodChoice',
  awaiting_input_binding: 'workflowStatusAwaitingInputBinding',
  chain_ready: 'workflowStatusChainReady',
  awaiting_parameter_review: 'workflowStatusAwaitingParameterReview',
  awaiting_dry_run: 'workflowStatusAwaitingDryRun',
  ready: 'workflowStatusReady',
  confirmed: 'workflowStatusConfirmed'
};

const MODE_KEYS: Record<Workflow['execution_mode'], keyof TranslationKeys> = {
  planning: 'workflowExecutionModePlanning',
  runner: 'workflowExecutionModeRunner',
  mixed: 'workflowExecutionModeMixed'
};

const BADGE_CLASS = 'rounded px-1.5 py-0.5 text-micro bg-well text-muted dark:text-subtle';
const BUTTON_CLASS =
  'inline-flex items-center gap-1.5 rounded-control border border-line px-2.5 py-1.5 text-xs font-medium transition hover:bg-hover disabled:opacity-50 ';

/**
 * Hairline section heading used to structure the card into
 * Inputs -> (steps) -> Execution -> Results.
 */
function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="mt-4 flex items-center gap-2 first:mt-0">
      <h4 className="text-meta font-semibold text-ink">{children}</h4>
      <span aria-hidden="true" className="h-px min-w-4 flex-1 bg-line" />
    </div>
  );
}

type WorkflowCardProps = {
  workflowId: string;
  initialWorkflow?: Workflow | null;
  deferred?: boolean;
  defaultExpanded?: boolean;
  disabled?: boolean;
  frozen?: boolean;
};

export function WorkflowCard(props: WorkflowCardProps) {
  // A new workflow gets its own disclosure state, even in the retained-card slot.
  return <WorkflowCardDisclosure key={props.workflowId} {...props} />;
}

function WorkflowCardDisclosure({
  workflowId,
  initialWorkflow,
  deferred = false,
  defaultExpanded = false,
  disabled = false,
  frozen = false
}: WorkflowCardProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [hasOpened, setHasOpened] = useState(!deferred);
  // Keep historical cards lazy until first opened. Afterwards hide the body
  // without unmounting it: draft state and in-flight mutation callbacks survive.
  if (!hasOpened) {
    return (
      <button type="button" aria-expanded={false} onClick={() => {
        setHasOpened(true);
        setExpanded(true);
      }} className={`mt-3 ${BUTTON_CLASS}`}>
        <ChevronDown className="size-3.5" aria-hidden="true" />
        {t.workflowExpandDetails}
      </button>
    );
  }
  return (
    <WorkflowCardBody
      workflowId={workflowId}
      initialWorkflow={initialWorkflow}
      disabled={disabled}
      frozen={frozen}
      t={t}
      expanded={expanded}
      onToggle={() => setExpanded(previous => !previous)}
    />
  );
}

type WorkflowCardBodyProps = {
  workflowId: string;
  initialWorkflow?: Workflow | null;
  disabled: boolean;
  frozen?: boolean;
  t: TranslationKeys;
  expanded: boolean;
  onToggle: () => void;
};

function WorkflowToggle({ expanded, onToggle, controls, t }: {
  expanded: boolean; onToggle: () => void; controls: string; t: TranslationKeys;
}) {
  return <button type="button" onClick={onToggle} aria-expanded={expanded}
    aria-controls={controls} className={`${BUTTON_CLASS} shrink-0`}>
    {expanded ? <ChevronUp className="size-3.5" aria-hidden="true" />
      : <ChevronDown className="size-3.5" aria-hidden="true" />}
    {expanded ? t.workflowCollapseDetails : t.workflowExpandDetails}
  </button>;
}

function WorkflowCardBody({
  workflowId,
  initialWorkflow,
  disabled,
  frozen = false,
  t,
  expanded,
  onToggle
}: WorkflowCardBodyProps) {
  const language = useLanguageStore(s => s.language);
  const detailsId = useId();
  const query = useWorkflowQuery(workflowId, initialWorkflow, frozen);
  const workflow = query.data ?? null;
  const action = useWorkflowAction(workflowId);
  const dryRun = useWorkflowDryRun(workflowId);
  const backgroundCheck = useWorkflowCheckStatus(workflowId);
  const datasetPatch = useWorkflowDataset(workflowId);
  const draft = useExecutionDraft(workflowId, workflow?.revision ?? 0, DEFAULT_EXECUTION_OPTIONS, frozen);
  const { setExecution } = draft;
  const queryClient = useQueryClient();
  const backendsQuery = useExecutionBackends(workflow?.status === 'confirmed' || Boolean(workflow?.can_confirm));
  const monitoring = useWorkflowRunMonitoring(workflowId);
  const startRunMutation = useMutation({
    mutationKey: ['workflow-change', workflowId],
    // Keep the start mutation pending during draft persistence as well as the
    // submission, so a second click cannot enqueue another start request.
    mutationFn: async (execution: ExecutionOptions) => {
      if (!(await draft.save())) throw new Error(t.workflowRunDraftNotSaved);
      if (!workflow) throw new Error(t.workflowErrorGeneric);
      const confirmed = await confirmWorkflowForRun(workflow);
      adoptWorkflow(queryClient, confirmed);
      return startRun(workflowId, confirmed.revision, execution);
    }
  });
  const retryMutation = useMutation({
    mutationKey: ['workflow-change', workflowId],
    mutationFn: (input: { runId: string; stepIds: string[] }) =>
      retryRun(input.runId, input.stepIds)
  });
  const cancelRunMutation = useMutation({
    mutationKey: ['workflow-change', workflowId], mutationFn: (runId: string) => cancelRun(runId) });
  const resumeMutation = useMutation({
    mutationKey: ['workflow-change', workflowId],
    mutationFn: (runId: string) => resumeRunMonitor(runId)
  });
  const operation = backgroundCheck.operation;
  const [pipelineSaved, setPipelineSaved] = useState<number | null>(null);
  const [pipelineSaving, setPipelineSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [checkPreparing, setCheckPreparing] = useState(false);

  useEffect(() => {
    const payload = backendsQuery.data;
    if (!payload || !draft.ready) return;
    setExecution((previous) => ({
      ...previous,
      partition: payload.slurm.partitions.includes(previous.partition)
        ? previous.partition
        : (payload.slurm.partitions[0] ?? (previous.partition || DEFAULT_EXECUTION_OPTIONS.partition)),
      gpus: payload.slurm.gpu_available ? previous.gpus : 0
    }));
  }, [backendsQuery.data, setExecution, draft.ready]);

  const pendingAction = action.isPending ? action.variables?.action : undefined;
  const busyAction = pendingAction ? workflowActionKey(pendingAction) : null;
  const busyParameterId =
    pendingAction?.kind === 'parameters'
      ? (Object.keys(pendingAction.parameters)[0] ?? null)
      : null;
  const busySlotId =
    pendingAction?.kind === 'input' || pendingAction?.kind === 'history' || pendingAction?.kind === 'clear-input'
      ? pendingAction.slotId
      : null;
  const locked = Boolean(workflow?.locked_at);
  const configuredCount = workflow?.steps.filter((step) => step.selected_method_id).length ?? 0;
  const controlsDisabled =
    disabled || query.isFetching || action.isPending || datasetPatch.isPending || backgroundCheck.active || checkPreparing || workflow?.status === 'confirmed';
  const backends = backendsQuery.data ?? null;
  const slurmReady = Boolean(
    backends &&
    backends.slurm.enabled !== false &&
    backends.slurm.available &&
    backends.slurm.partitions.length
  );
  const dryRunReport = workflow?.dry_run_report ?? null;

  const fail = (failure: unknown, fallback: keyof TranslationKeys) => {
    if (failure instanceof DOMException && failure.name === 'AbortError') return;
    setError(failure instanceof Error ? failure.message || t[fallback] : t[fallback]);
  };

  const conflictKey = (failure: unknown): keyof TranslationKeys =>
    failure instanceof ApiError && failure.status === 409
      ? 'workflowConflict'
      : 'workflowErrorGeneric';

  const runAction = (next: WorkflowAction) => {
    if (!workflow) return;
    setError(null);
    action.mutate(
      { action: next, expectedRevision: workflow.revision },
      { onError: (failure) => fail(failure, conflictKey(failure)) }
    );
  };

  const startDryRun = async () => {
    if (!workflow || backgroundCheck.active || checkPreparing) return;
    setError(null);
    setCheckPreparing(true);
    try {
      const checked = await prepareWorkflowCheck(workflow, next => adoptWorkflow(queryClient, next));
      await dryRun.mutateAsync({ expectedRevision: checked.revision });
    } catch (failure) { fail(failure, conflictKey(failure)); }
    finally { setCheckPreparing(false); }
  };

  const cancelDryRun = () => {
    if (!operation) return;
    void cancelOperation(operation.operation_id).then(() => backgroundCheck.refresh()).catch(() =>
      fail(new Error(t.datasetCancelFailed), 'datasetCancelFailed')
    );
  };

  const validationErrors = workflow ? executionErrors(draft.execution, backends, workflow, t) : [];
  const executionBlocked = validationErrors.length > 0 || !slurmReady || !draft.execution.partition;
  const runActive = Boolean(monitoring.run && runStatusActive(monitoring.run));
  const runnable = (workflow?.status === 'confirmed' || workflow?.can_confirm) &&
    Boolean(dryRunReport?.valid && dryRunReport.execution_enabled);
  const startRunDisabled =
    disabled || !workflow?.workspace_id || !workflow?.workspace_root_path ||
    !runnable || runActive || !draft.ready || draft.saving ||
    query.isFetching || monitoring.loading || backgroundCheck.active ||
    startRunMutation.isPending || retryMutation.isPending || cancelRunMutation.isPending ||
    resumeMutation.isPending || executionBlocked;

  const handleStartRun = () => {
    if (!workflow || startRunDisabled) return;
    setError(null);
    startRunMutation.mutate(
        draft.execution,
        {
          onSuccess: (run) => {
            monitoring.setRun(run);
            monitoring.setRunEvents([]);
          },
          onError: (failure) => fail(failure, 'workflowErrorGeneric')
        }
    );
  };

  const changeDataset = async (dataset: Dataset) => {
    if (!workflow || disabled || workflow.locked_at || workflow.status === 'confirmed')
      throw new Error(t.workflowDatasetLocked);
    setError(null);
    await datasetPatch.mutateAsync({ workflowId, datasetId: dataset.dataset_id, expectedRevision: workflow.revision });
  };

  if (query.isPending) {
    return (
      <section data-workflow-id={workflowId} className="mt-3 min-w-0 rounded-card border border-line bg-raised p-4">
        <WorkflowToggle expanded={expanded} onToggle={onToggle} controls={detailsId} t={t} />
        <div id={detailsId} hidden={!expanded}>
          <p className="mt-2 flex items-center gap-2 text-xs text-subtle">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />{t.workflowLoading}
          </p>
        </div>
      </section>
    );
  }

  if (!workflow) {
    return (
      <div data-workflow-id={workflowId} className="mt-3 min-w-0 rounded-card border border-line p-4 ">
        <WorkflowToggle expanded={expanded} onToggle={onToggle} controls={detailsId} t={t} />
        <div id={detailsId} hidden={!expanded}>
        <p role="alert" className="text-xs text-danger">
          {t.workflowLoadError}
        </p>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className={`mt-2 ${BUTTON_CLASS}`}
        >
          {t.retry}
        </button>
        </div>
      </div>
    );
  }
  return (
    <section data-workflow-id={workflowId} data-workflow-revision={workflow.revision} className="mt-3 min-w-0 rounded-card border border-line bg-raised p-4 shadow-sm ">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span
              className={`rounded px-1.5 py-0.5 text-micro ${
                workflow.status === 'confirmed'
                  ? 'bg-success-quiet text-success '
                  : 'bg-accent-quiet text-accent dark:text-accent'
              }`}
            >
              {t[STATUS_KEYS[workflow.status]]}
            </span>
            <span className={BADGE_CLASS}>
              {workflow.plan_kind === 'atomic' ? t.workflowPlanAtomic : t.workflowPlanComposite}
            </span>
            <span className={BADGE_CLASS}>{t[MODE_KEYS[workflow.execution_mode]]}</span>
          </div>
          <h3 className="mt-1.5 break-words text-sm font-semibold">{workflow.title}</h3>
          <p className="mt-0.5 break-words text-meta leading-5 text-subtle">
            {expanded && <>{workflow.summary}{' · '}</>}
            {t.workflowStepsConfigured
              .replace('{{configured}}', String(configuredCount))
              .replace('{{total}}', String(workflow.steps.length))}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="font-mono text-micro text-subtle">
            {t.workflowRevision.replace('{{revision}}', String(workflow.revision))}
          </span>
          <WorkflowToggle expanded={expanded} onToggle={onToggle} controls={detailsId} t={t} />
        </div>
      </div>

      <div id={detailsId} hidden={!expanded}>
      <SectionLabel>{t.workflowInputsTitle}</SectionLabel>
      {workflow.input_selection === 'user' && workflow.status !== 'confirmed' && <p className="mt-2 text-meta leading-5 text-muted">{t.workflowDraftNotice}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-meta text-muted">
        <span className="font-medium text-muted dark:text-line-strong">
          {workflow.dataset ? workflow.dataset.display_name : t.datasetEmptyTitle}
        </span>
        {workflow.dataset?.inspection.is_bids && <span className={BADGE_CLASS}>BIDS</span>}
        {workflow.dataset?.inspection.has_dwi && <span className={BADGE_CLASS}>DWI</span>}
        {workflow.dataset?.inspection.has_t1w && <span className={BADGE_CLASS}>T1w</span>}
        {workflow.dataset?.inspection.has_fod && <span className={BADGE_CLASS}>FOD</span>}
        {workflow.dataset?.inspection.has_five_tt && <span className={BADGE_CLASS}>5TT</span>}
        {workflow.dataset && (
          <span className={BADGE_CLASS}>
            {t.datasetSamples.replace(
              '{{count}}',
              String(workflow.dataset.inspection.sample_count)
            )}
          </span>
        )}
        <button
          type="button"
          disabled={controlsDisabled || locked || datasetPatch.isPending}
          onClick={() => setPickerOpen(true)}
          className="rounded-control border border-line px-2 py-0.5 text-meta disabled:opacity-50 "
        >
          {datasetPatch.isPending ? t.workflowLoading : t.workflowChangeDataset}
        </button>
        {locked && <span className="text-meta text-subtle">{t.workflowDatasetLocked}</span>}
      </div>

      {workflow.warnings.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-control border border-warning-line bg-warning-quiet p-3 text-meta leading-5 text-warning ">
          {workflow.warnings.map((warning) => (
            <li key={warning} className="flex gap-1.5 break-words">
              <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />
              {warning}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3">
        <SampleScopeNotice scope={workflow.sample_scope} />
      </div>

      <div className="mt-3 space-y-3">
        {workflow.steps.map((step) => (
          <WorkflowStepSection
            key={step.id}
            workflow={workflow}
            step={step}
            locked={locked}
            disabled={controlsDisabled}
            busyKey={busyAction}
            busyParameterId={busyParameterId}
            busySlotId={busySlotId}
            onAction={runAction}
          />
        ))}
      </div>
      {dryRunReport && (
        <DryRunResult key={dryRunReport.checked_at} report={dryRunReport} workflowId={workflowId}
          disabled={controlsDisabled || workflow.status === 'confirmed'} />
      )}

      {dryRun.isPending && (
        <p className="mt-3 flex items-center gap-2 text-xs text-accent">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t.workflowDryRunRunning}
        </p>
      )}

      {operation && checkIsActive(operation) && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-control border border-line p-2 text-meta ">
          <span role="status" className="min-w-0 break-words text-muted">{operation.message}</span>
          <button
            type="button"
            disabled={operation.status === 'cancel_requested'}
            onClick={cancelDryRun}
            className="rounded-control border border-line px-2 py-1 disabled:opacity-50 "
          >
            {t.workflowCancelOperation}
          </button>
        </div>
      )}

      {backgroundCheck.reconnecting && <p role="status" className="mt-2 text-xs text-warning">
        {language === 'zh' ? '正在恢复检查进度连接，后台检查仍可能在继续。' : 'Reconnecting; the background check may still be running.'}
      </p>}
      {operation?.status === 'failed' && operation.expected_revision === workflow.revision && operation.error?.message &&
        <p role="alert" className="mt-2 break-words text-xs text-danger">{operation.error.message}</p>}

      {error && (
        <p role="alert" className="mt-3 break-words text-xs text-danger">
          {error}
        </p>
      )}
      {runnable && !runActive && (
        <div className="mt-3 space-y-3 rounded-card border border-line p-3 ">
          <SectionLabel>{t.workflowExecutionTitle}</SectionLabel>
          <label className="block text-xs font-medium">
            {language === 'zh' ? '输出目录结构' : 'Output folders'}
            <select aria-label={language === 'zh' ? '输出目录结构' : 'Output folders'}
              value={draft.execution.output_layout ?? workflow.output_layout ?? 'legacy'}
              disabled={disabled || !draft.ready || draft.saving || startRunMutation.isPending}
              onChange={event => setExecution(previous => ({ ...previous,
                output_layout: event.target.value as 'legacy' | 'sample_steps' }))}
              className="ml-2 max-w-full rounded-control border border-line bg-transparent px-2 py-1.5 text-xs">
              <option value="sample_steps">{language === 'zh' ? '真实样本 ID / 步骤 / 文件' : 'Sample ID / step / files'}</option>
              <option value="legacy">{language === 'zh' ? '旧版序号与步骤目录' : 'Legacy numbered step folders'}</option>
            </select>
          </label>
          <p className="text-micro text-subtle">
            {draft.error ? (
              <>
                <span className="text-danger">{draft.error}</span>{' '}
                <button type="button" className="underline" onClick={draft.reload}>
                  {t.retry}
                </button>
              </>
            ) : draft.ready && !draft.saving ? (
              t.workflowExecutionHint
            ) : (
              t.workflowLoading
            )}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium">{t.datasetServer}：Slurm</span>
            <button
              type="button"
              onClick={() => void backendsQuery.refetch()}
              disabled={backendsQuery.isFetching}
              className={BUTTON_CLASS}
            >
              <RefreshCw className="h-3 w-3" />
              {t.retry}
            </button>
            {!slurmReady && (
              <span className="break-words text-meta text-subtle">
                {backends?.slurm.reason || t.workflowErrorPickPartition}
              </span>
            )}
          </div>
          <ExecutionSettings
            value={draft.execution}
            backends={backends}
            workflow={workflow}
            disabled={disabled || !draft.ready || draft.saving || startRunMutation.isPending}
            onChange={setExecution}
          />
          {draft.conflict && (
            <p role="alert" className="text-meta text-danger">
              {t.workflowConflict}
            </p>
          )}
        </div>
      )}
      <button className={BUTTON_CLASS} disabled={disabled || pipelineSaving || pipelineSaved === workflow.revision || workflow.steps.some(s=>!s.selected_method_id)} onClick={async()=>{
        setPipelineSaving(true);
        try {
          await apiRequest(`/api/library/from-workflow/${encodeURIComponent(workflow.workflow_id)}`,{
            method:'POST',body:JSON.stringify({expected_revision:workflow.revision,name:workflow.title})});
          void queryClient.invalidateQueries({queryKey:['library']});
          setPipelineSaved(workflow.revision);
        } catch(failure) { fail(failure,'workflowErrorGeneric'); } finally { setPipelineSaving(false); }
      }}>{pipelineSaved === workflow.revision?(language === 'zh' ? '已保存到 Pipeline 列表' : 'Saved to Pipeline library'):(language === 'zh' ? '保存为可复用 Pipeline' : 'Save as reusable Pipeline')}</button>
      <SectionLabel>{t.workflowOutputsTitle}</SectionLabel>
      <WorkspaceOutput
        workflow={workflow}
        onChange={(next) => queryClient.setQueryData(workflowQueryKey(next.workflow_id), next)}
        disabled={disabled}
      />
      {!runActive && draft.ready && <WorkflowOutputPreview workflow={workflow} execution={draft.execution} />}
      {monitoring.run && (
        <RunPanel
          events={monitoring.runEvents}
          run={monitoring.run}
          onRetry={(stepIds) =>
            monitoring.run &&
            retryMutation.mutate(
              { runId: monitoring.run.run_id, stepIds },
              { onSuccess: (run) => monitoring.setRun(run), onError: (failure) => fail(failure, 'workflowErrorGeneric') }
            )
          }
          onCancel={() =>
            monitoring.run &&
            cancelRunMutation.mutate(monitoring.run.run_id, {
              onSuccess: (run) => monitoring.setRun(run),
              onError: (failure) => fail(failure, 'workflowErrorGeneric')
            })
          }
          onResume={() => {
            if (monitoring.run) resumeMutation.mutate(monitoring.run.run_id, {
              onSuccess: (run) => monitoring.setRun(run),
              onError: (failure) => fail(failure, 'workflowErrorGeneric')
            });
          }}
          retryPending={retryMutation.isPending}
          controlsPending={disabled || resumeMutation.isPending || retryMutation.isPending || cancelRunMutation.isPending}
          cancelPending={cancelRunMutation.isPending}
        />
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <p className="flex min-w-0 items-center gap-1.5 text-meta leading-5 text-subtle">
          {workflow.status === 'confirmed' ? (
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success " />
          ) : locked ? (
            <LockKeyhole className="h-3.5 w-3.5 shrink-0 text-accent" />
          ) : (
            <CircleDashed className="h-3.5 w-3.5 shrink-0" />
          )}
          {workflow.status === 'confirmed'
            ? dryRunReport?.execution_enabled
              ? t.workflowLiveHint
              : t.workflowConfirmedPlanningOnly
            : dryRunReport?.valid
              ? t.workflowReadyHint
              : locked
                ? workflow.status === 'awaiting_input_binding'
                  ? t.workflowInputBindingHint
                  : t.workflowLockedHint
                : t.workflowUnlockedHint}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          {runnable && !runActive && (
            <button
              type="button"
              disabled={startRunDisabled}
              onClick={handleStartRun}
              className="inline-flex items-center gap-1.5 rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent hover:bg-accent-solid-hover disabled:opacity-50"
            >
              {startRunMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Play className="h-3.5 w-3.5" />
              )}
              {monitoring.run ? t.workflowRunAgain : t.workflowStartRun}
            </button>
          )}
          {workflow.status === 'confirmed' && !runnable && (
            <span className="text-meta text-subtle">{t.workflowStartRunPlanning}</span>
          )}
          {workflow.status !== 'confirmed' && !locked && (
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={() => runAction({ kind: 'replan' })}
              className={BUTTON_CLASS}
            >
              {busyAction === 'replan' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCcw className="h-3.5 w-3.5" />
              )}
              {t.workflowReplan}
            </button>
          )}
          {workflow.can_unlock && (
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={() => runAction({ kind: 'unlock' })}
              className={BUTTON_CLASS}
            >
              {busyAction === 'unlock' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <UnlockKeyhole className="h-3.5 w-3.5" />
              )}
              {t.workflowUnlock}
            </button>
          )}
          {workflow.can_lock && (
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={() => runAction({ kind: 'lock' })}
              className="inline-flex items-center gap-1.5 rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent hover:bg-accent-solid-hover disabled:opacity-50"
            >
              {busyAction === 'lock' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <LockKeyhole className="h-3.5 w-3.5" />
              )}
              {t.workflowLock}
            </button>
          )}
          {(workflow.can_dry_run || workflow.can_review_parameters || workflow.can_lock) && !dryRunReport?.valid && (
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={startDryRun}
              className="inline-flex items-center gap-1.5 rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent hover:bg-accent-solid-hover disabled:opacity-50"
            >
              {dryRun.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <FlaskConical className="h-3.5 w-3.5" />
              )}
              {t.workflowDryRun}
            </button>
          )}
          {workflow.can_dry_run && dryRunReport?.valid && (
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={startDryRun}
              className={BUTTON_CLASS}
            >
              {dryRun.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {t.workflowDryRunRerun}
            </button>
          )}
        </div>
      </div>

      </div>
      {expanded && pickerOpen && (
        <DatasetPicker selectedDatasetId={workflow.dataset_id} onClose={() => setPickerOpen(false)} onSelect={changeDataset} />
      )}
    </section>
  );
}
