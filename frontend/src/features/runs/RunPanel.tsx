import { ui } from '../../lib/libraryText';
import { useMemo, useState } from 'react';
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  FileText,
  Loader2,
  RotateCcw,
  Square
} from 'lucide-react';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';
import { ServerPath } from '../workspace/ServerPath';
import { SampleScopeNotice } from '../workflows/SampleScopeNotice';
import { filterRunGroups, groupRunSteps, monitorPage, RUN_FILTERS, runStatusActive } from './runMonitor';
import type { MonitorFilter, MonitorGroup } from './runMonitor';
import type { RunArtifact, RunEvent, RunStep, WorkflowRun } from './runsTypes';
import { RunLogs } from './RunLogs';

const STATUS_STYLE: Record<MonitorFilter, string> = {
  all: '',
  queued: 'bg-well text-muted',
  running: 'bg-running-quiet text-running',
  succeeded: 'bg-success-quiet text-success',
  failed: 'bg-danger-quiet text-danger',
  cancelled: 'bg-warning-quiet text-warning'
};

const RUN_STATUS_KEYS: Record<WorkflowRun['status'], keyof TranslationKeys> = {
  queued: 'runStatusQueued',
  running: 'runStatusRunning',
  cancel_requested: 'runStatusCancelRequested',
  cancelled: 'runStatusCancelled',
  succeeded: 'runStatusSucceeded',
  failed: 'runStatusFailed'
};

const STEP_STATUS_KEYS: Record<MonitorFilter, keyof TranslationKeys> = {
  all: 'runFilterAll',
  queued: 'runStepQueued',
  running: 'runStepRunning',
  failed: 'runStepFailed',
  succeeded: 'runStepSucceeded',
  cancelled: 'runStepCancelled'
};

const SCHEDULER_STATUS_KEYS: Record<string, keyof TranslationKeys> = {
  PENDING: 'schedPending',
  CONFIGURING: 'schedConfiguring',
  RUNNING: 'schedRunning',
  COMPLETING: 'schedCompleting',
  COMPLETED: 'schedCompleted',
  CANCELLED: 'schedCancelled',
  FAILED: 'schedFailed',
  TIMEOUT: 'schedTimeout',
  OUT_OF_MEMORY: 'schedOutOfMemory',
  NODE_FAIL: 'schedNodeFail',
  PREEMPTED: 'schedPreempted',
  SUSPENDED: 'schedSuspended'
};

function Pagination({
  page,
  pages,
  total,
  onChange,
  t
}: {
  page: number;
  pages: number;
  total: number;
  onChange: (page: number) => void;
  t: TranslationKeys;
}) {
  return (
    <nav
      aria-label={t.runPaginationLabel}
      className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-subtle"
    >
      <span>
        {t.runPaginationSummary
          .replace('{{total}}', String(total))
          .replace('{{page}}', String(page + 1))
          .replace('{{pages}}', String(pages))}
      </span>
      <div className="flex gap-1">
        <button
          type="button"
          aria-label={t.runPaginationPrevious}
          disabled={page === 0}
          onClick={() => onChange(page - 1)}
          className="inline-flex items-center gap-1 rounded-control border border-line px-2 py-1 disabled:opacity-50 "
        >
          <ChevronLeft className="size-3" />
          {t.runPaginationPrevious}
        </button>
        <button
          type="button"
          aria-label={t.runPaginationNext}
          disabled={page + 1 >= pages}
          onClick={() => onChange(page + 1)}
          className="inline-flex items-center gap-1 rounded-control border border-line px-2 py-1 disabled:opacity-50 "
        >
          {t.runPaginationNext}
          <ChevronRight className="size-3" />
        </button>
      </div>
    </nav>
  );
}

function StepDetails({
  step,
  onRetry,
  t
}: {
  step: RunStep;
  onRetry?: (id: string) => void;
  t: TranslationKeys;
}) {
  return (
    <div className="min-w-0 border-t border-line px-3 py-2 text-xs ">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-ink">{step.title}</span>
        <span className="text-subtle">
          {t[STEP_STATUS_KEYS[step.status]]} · {Math.round(step.progress)}%
        </span>
      </div>
      {step.message && <p className="mt-1 text-subtle">{step.message}</p>}
      {step.scheduler?.job_id && (
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-accent">
          <span className="font-mono">
            {t.runJobId} {step.scheduler.job_id}
          </span>
          {step.scheduler.state && (
            <span>
              {t[SCHEDULER_STATUS_KEYS[String(step.scheduler.state).toUpperCase()]] ??
                step.scheduler.state}
            </span>
          )}
          {(step.scheduler.node_list || step.node_name) && (
            <span>
              {t.runNodeLabel}
              {step.scheduler.node_list || step.node_name}
            </span>
          )}
          {step.scheduler.reason && (
            <span>
              {t.runReasonLabel}
              {step.scheduler.reason}
            </span>
          )}
        </div>
      )}
      {step.error_message && <p className="mt-1 text-danger">{step.error_message}</p>}
      {onRetry && ['failed', 'cancelled'].includes(step.status) && (
        <button
          type="button"
          onClick={() => onRetry(step.step_id)}
          className="mt-2 rounded-control border border-line px-2 py-1 "
        >
          {t.runRetryStep}
        </button>
      )}
    </div>
  );
}

function OutputFiles({ artifacts, t }: { artifacts: RunArtifact[]; t: TranslationKeys }) {
  const [query, setQuery] = useState('');
  const [requestedPage, setPage] = useState(0);
  const term = query.trim().toLocaleLowerCase();
  const matches = artifacts.filter(
    (item) => !term || `${item.label} ${item.absolute_path}`.toLocaleLowerCase().includes(term)
  );
  const { items, page, pages } = monitorPage(matches, requestedPage);
  if (page !== requestedPage) setPage(page);
  return (
    <details className="group/output mt-3 min-w-0 rounded-control border border-success-line bg-success-quiet ">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-control px-3 py-2.5 text-sm font-medium text-success [&::-webkit-details-marker]:hidden">
        <FileText className="size-4 shrink-0" />
        <span className="min-w-0 flex-1">
          {t.runOutputFiles.replace('{{count}}', String(artifacts.length))}
        </span>
      </summary>
      <div className="min-w-0 border-t border-success-line p-3 ">
        <input
          aria-label={t.runOutputFilterLabel}
          placeholder={t.runOutputFilterPlaceholder}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setPage(0);
          }}
          className="w-full min-w-0 rounded-chip border border-line bg-raised px-2 py-1.5 text-sm "
        />
        <div className="mt-2 max-h-64 overflow-y-auto">
          {items.map((artifact) => (
            <div
              className="min-w-0 border-b border-success-line py-2 last:border-0 "
              key={artifact.artifact_id}
            >
              <p className="text-sm font-medium text-ink">{artifact.label}</p>
              <ServerPath path={artifact.absolute_path} />
              {artifact.sha256 && (
                <p className="break-all font-mono text-xs text-subtle">SHA-256 {artifact.sha256}</p>
              )}
            </div>
          ))}
          {!items.length && <p className="py-3 text-sm text-subtle">{t.runOutputNoMatch}</p>}
        </div>
        <Pagination page={page} pages={pages} total={matches.length} onChange={setPage} t={t} />
      </div>
    </details>
  );
}

type RunPanelProps = {
  events: RunEvent[];
  run: WorkflowRun;
  onRetry?: (stepIds: string[]) => void;
  onCancel?: () => void;
  onResume?: () => void;
  retryPending?: boolean;
  controlsPending?: boolean;
  cancelPending?: boolean;
};

export function RunPanel(props: RunPanelProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  return <RunPanelContent key={props.run.run_id} {...props} t={t} />;
}

function RunControls({
  run,
  onCancel,
  onResume,
  onRetry,
  cancelPending,
  retryPending,
  controlsPending,
  t
}: {
  run: WorkflowRun;
  onCancel?: () => void;
  onResume?: () => void;
  onRetry?: (stepIds: string[]) => void;
  cancelPending?: boolean;
  retryPending?: boolean;
  controlsPending?: boolean;
  t: TranslationKeys;
}) {
  const active = runStatusActive(run);
  const busy = cancelPending || retryPending || controlsPending;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {onCancel && active && (
        <button
          type="button"
          disabled={cancelPending || run.status === 'cancel_requested'}
          onClick={onCancel}
          className="inline-flex items-center gap-1.5 rounded-control border border-red-300 px-2.5 py-1.5 text-xs font-medium text-danger disabled:opacity-50 dark:border-red-800"
        >
          {cancelPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Square className="size-3.5" />
          )}
          {run.status === 'cancel_requested' ? t.runCancelling : t.runCancel}
        </button>
      )}
      {onResume && active && run.execution?.backend === 'slurm' && (
        <button
          type="button"
          disabled={busy}
          onClick={onResume}
          className="inline-flex items-center gap-1.5 rounded-control border border-line px-2.5 py-1.5 text-xs font-medium "
        >
          {t.runResumeMonitor}
        </button>
      )}
      {onRetry && ['failed', 'cancelled'].includes(run.status) && (
        <button
          type="button"
          disabled={busy}
          // Empty selection asks the existing Runner to retry unfinished steps
          // and validate reusable outputs across all affected samples.
          onClick={() => onRetry([])}
          className="inline-flex items-center gap-1.5 rounded-control border border-line px-2.5 py-1.5 text-xs font-medium disabled:opacity-50"
        >
          {retryPending ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />}
          {t.runRetryIncomplete}
        </button>
      )}
    </div>
  );
}

function RunPanelContent({
  events,
  run,
  onRetry,
  onCancel,
  onResume,
  cancelPending,
  retryPending,
  controlsPending,
  t
}: RunPanelProps & { t: TranslationKeys }) {
  const active = ['queued', 'running', 'cancel_requested'].includes(run.status);
  const groups = useMemo(() => groupRunSteps(run.steps), [run.steps]);
  const [filter, setFilter] = useState<MonitorFilter>(() =>
    groups.some((group) => group.status === 'failed')
      ? 'failed'
      : groups.some((group) => group.status === 'running')
        ? 'running'
        : 'all'
  );
  const [query, setQuery] = useState('');
  const [requestedPage, setPage] = useState(0);
  const counts = {
    all: groups.length,
    running: 0,
    failed: 0,
    queued: 0,
    succeeded: 0,
    cancelled: 0
  };
  groups.forEach((group) => {
    counts[group.status] += 1;
  });
  const filtered = filterRunGroups(groups, filter, query);
  const { items, page, pages } = monitorPage(filtered, requestedPage);
  // Persist a clamped page so live results growing again cannot jump backwards
  // to an obsolete requested page. Keep filters and sample order unchanged.
  if (page !== requestedPage) setPage(page);
  const samples = groups.some((group) => group.sampleId);
  const stepRetry = (stepId: string) => onRetry?.([stepId]);

  return (
    <section
      aria-label={t.runPanelAriaLabel}
      className="mt-3 min-w-0 rounded-card border border-running-line bg-running-quiet p-3 sm:p-4 "
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {active ? (
            <Loader2 className="size-4 animate-spin text-accent" />
          ) : run.status === 'succeeded' ? (
            <CheckCircle2 className="size-4 text-success" />
          ) : (
            <CircleAlert className="size-4 text-warning" />
          )}
          <h3 className="text-sm font-semibold text-ink">{t[RUN_STATUS_KEYS[run.status]]}{run.status==='succeeded'&&<span className="ml-2 text-xs text-subtle">{ui('程序执行成功；结果仍需科研质量审阅')}</span>}</h3>
          <span className="rounded border border-line bg-raised px-1.5 py-0.5 font-mono text-xs ">
            {run.run_id.slice(0, 12)}
          </span>
          {run.execution?.backend === 'slurm' && (
            <span className="rounded border border-line bg-raised px-1.5 py-0.5 text-xs ">
              Slurm · {run.execution.partition}
            </span>
          )}
        </div>
        <span className="text-xs tabular-nums text-subtle">
          {t.runCompletedCount
            .replace('{{done}}', String(counts.succeeded))
            .replace('{{total}}', String(groups.length))
            .replace('{{unit}}', samples ? t.runSamplesUnit : t.runStepsUnit)}
        </span>
      </div>
      {run.execution?.sample_mode === 'per_sample' && (
        <p className="mt-2 text-xs text-subtle">
          {t.runParallelCap}{' '}
          {run.execution.nodes
            ?.map((node) => `${node.node}: ${node.parallel_samples ?? 1}`)
            .join(' · ')}
        </p>
      )}
      {run.retry_of && (
        <p className="mt-2 text-xs text-subtle">
          {t.runRetryOf
            .replace('{{run}}', run.retry_of)
            .replace('{{count}}', String(run.reused_step_ids?.length ?? 0))}
        </p>
      )}
      <SampleScopeNotice scope={run.sample_scope} />
      <div className="mt-3 h-2 min-w-0 overflow-hidden rounded-full bg-well ">
        <div
          className="h-full rounded-full bg-accent-solid transition-all"
          style={{ width: `${Math.max(0, Math.min(100, run.progress))}%` }}
        />
      </div>
      <RunControls
        run={run}
        onCancel={onCancel}
        onResume={onResume}
        onRetry={onRetry}
        cancelPending={cancelPending}
        retryPending={retryPending}
        controlsPending={controlsPending}
        t={t}
      />
      <fieldset
        aria-label={samples ? t.runFilterSamplesAria : t.runFilterStepsAria}
        className="mt-3 flex min-w-0 flex-wrap gap-1.5"
      >
        {RUN_FILTERS.map((status) => (
          <button
            key={status}
            type="button"
            aria-pressed={filter === status}
            onClick={() => {
              setFilter(status);
              setPage(0);
            }}
            className={`rounded-control border px-2 py-1 text-xs ${
              filter === status ? 'border-transparent bg-well text-on-accent ' : 'border-line'
            } ${status === 'failed' && counts.failed > 0 && filter !== status ? 'border-red-300 text-danger dark:border-red-800' : ''}`}
          >
            {status === 'all' ? t.runFilterAll : t[STEP_STATUS_KEYS[status]]} {counts[status]}
          </button>
        ))}
      </fieldset>
      <input
        className="mt-2 w-full min-w-0 rounded-chip border border-line bg-raised px-2 py-1.5 text-sm "
        aria-label={t.runSearchAriaLabel}
        placeholder={samples ? t.runSearchSamplesPlaceholder : t.runSearchStepsPlaceholder}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setPage(0);
        }}
      />
      <RunGroupList
        groups={items}
        samples={samples}
        run={run}
        onRetry={onRetry && !retryPending && !controlsPending ? stepRetry : undefined}
        t={t}
      />
      <Pagination page={page} pages={pages} total={filtered.length} onChange={setPage} t={t} />
      <RunLogs runId={run.run_id} events={events} />
      {run.artifacts.length > 0 && <OutputFiles artifacts={run.artifacts} t={t} />}
      {run.error_message && (
        <p
          role="alert"
          className="mt-3 max-h-32 overflow-y-auto rounded-control border border-red-200 bg-red-50 p-3 text-xs text-danger dark:border-red-900 dark:bg-red-950/40 dark:text-danger"
        >
          {run.error_message}
        </p>
      )}
      <details className="mt-2 text-xs text-subtle">
        <summary className="cursor-pointer py-1">{t.runOutputDirectory}</summary>
        <ServerPath path={run.output_directory}
          label={run.workspace_root_path && run.output_directory.startsWith(run.workspace_root_path.replace(/\/$/, '') + '/')
            ? run.output_directory.slice(run.workspace_root_path.replace(/\/$/, '').length + 1)
            : run.output_directory} />
      </details>
    </section>
  );
}

function RunGroupList({
  groups,
  samples,
  run,
  onRetry,
  t
}: {
  groups: MonitorGroup[];
  samples: boolean;
  run: WorkflowRun;
  onRetry?: (id: string) => void;
  t: TranslationKeys;
}) {
  return (
    <section
      aria-label={samples ? t.runSampleListAria : t.runStepListAria}
      className="mt-2 max-h-96 min-w-0 space-y-1.5 overflow-y-auto"
    >
      {groups.map((group) => {
        const current =
          group.steps.find((step) => step.status === 'failed') ||
          group.steps.find((step) => step.status === 'running') ||
          group.steps.find((step) => step.status === 'queued') ||
          group.steps[group.steps.length - 1];
        const completed = group.steps.filter((step) => step.status === 'succeeded').length;
        return (
          <details
            key={group.id}
            className="group/sample min-w-0 rounded-control border border-line bg-raised "
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 rounded-control px-3 py-2 [&::-webkit-details-marker]:hidden">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-ink" title={group.label}>
                  {group.label}
                </span>
                <span
                  className="block truncate text-xs text-subtle"
                  title={`${group.nodes.join('、')} · ${current.title}`}
                >
                  {group.nodes.join('、') || t.runNoNode} · {completed}/{group.steps.length}{' '}
                  {t.runStepsUnit}
                  {group.status !== 'succeeded' && ` · ${current.title}`}
                </span>
              </span>
              <span className="flex shrink-0 flex-col items-end gap-0.5">
                <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[group.status]}`}>
                  {t[STEP_STATUS_KEYS[group.status]]}
                </span>
                <span className="text-xs tabular-nums text-subtle">
                  {Math.round(group.progress)}%
                </span>
              </span>
            </summary>
            <div className="min-w-0">
              <p className="border-t border-line px-3 py-2 text-xs text-subtle ">
                {group.label}
                {group.sampleId &&
                  group.sampleId !== group.label &&
                  ` · ${t.runSampleIdLabel}${group.sampleId}`}
                {group.nodes.length > 0 && ` · ${t.runNodeLabel}${group.nodes.join('、')}`}
              </p>
              {group.steps.map((step) => (
                <StepDetails
                  step={step}
                  key={step.step_id}
                  onRetry={['failed', 'cancelled'].includes(run.status) ? onRetry : undefined}
                  t={t}
                />
              ))}
            </div>
          </details>
        );
      })}
      {!groups.length && (
        <p className="rounded-control bg-raised p-4 text-sm text-subtle ">{t.runFilterEmpty}</p>
      )}
    </section>
  );
}
