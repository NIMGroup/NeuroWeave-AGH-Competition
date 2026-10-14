import type {
  ExecutionBackends,
  ExecutionOptions,
  NodeAllocation,
  SlurmNode,
  Workflow
} from './workflowTypes';
import { assignedSamples, executionErrors, RESOURCE_FIELDS } from './executionValidation';
import type { ResourceKey } from './executionValidation';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';

const INPUT_CLASS =
  'w-full rounded-control border border-line bg-transparent px-2 py-1.5 text-xs disabled:opacity-50 ';

function ResourceFields({
  id,
  value,
  node,
  disabled,
  t,
  onChange
}: {
  id: string;
  value: NodeAllocation | ExecutionOptions;
  node?: SlurmNode;
  disabled: boolean;
  t: TranslationKeys;
  onChange: (key: ResourceKey, number: number) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {RESOURCE_FIELDS.map(([key, labelKey, min, max]) => {
        const capacity =
          key === 'cpus'
            ? node?.cpus
            : key === 'memory_gb' && node?.memory_mb
              ? Math.floor(node.memory_mb / 1024)
              : key === 'gpus'
                ? node?.gpus
                : undefined;
        return (
          <div key={key} className="min-w-0 space-y-1">
            <label htmlFor={id + key} className="block break-words text-xs font-medium">
              {t[labelKey]}
            </label>
            <input
              id={id + key}
              type="number"
              step={1}
              min={min}
              max={Math.min(max, capacity ?? max)}
              value={Number.isFinite(value[key]) ? value[key] : ''}
              disabled={disabled || capacity === 0}
              onChange={(event) =>
                onChange(key, event.target.value === '' ? Number.NaN : Number(event.target.value))
              }
              className={INPUT_CLASS}
            />
          </div>
        );
      })}
    </div>
  );
}
type ExecutionSettingsProps = {
  value: ExecutionOptions;
  backends: ExecutionBackends | null;
  workflow: Workflow;
  disabled: boolean;
  onChange: (value: ExecutionOptions) => void;
};

/** Competition Slurm partition, sample node allocation and resource requests. */
export function ExecutionSettings({
  value,
  backends,
  workflow,
  disabled,
  onChange
}: ExecutionSettingsProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const nodes = value.nodes ?? [];
  const available =
    backends?.slurm.nodes?.filter((node) => node.partition === value.partition) ?? [];
  const count =
    workflow.sample_scope?.eligible_sample_ids.length ??
    workflow.dataset?.inspection.sample_count ??
    0;
  const errors = executionErrors(value, backends, workflow, t);
  const allocation = (node: SlurmNode): NodeAllocation => ({
    node: node.name,
    cpus: Math.min(value.cpus, node.cpus ?? 128),
    memory_gb: Math.min(value.memory_gb, Math.floor((node.memory_mb ?? 512 * 1024) / 1024)),
    gpus: Math.min(value.gpus, node.gpus),
    time_minutes: value.time_minutes,
    parallel_samples: 1
  });

  return (
    <div className="min-w-0 space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="min-w-0 space-y-1">
          <label
            htmlFor={`partition-${workflow.workflow_id}`}
            className="block text-xs font-medium"
          >
            {t.workflowPartition}
          </label>
          <select
            id={`partition-${workflow.workflow_id}`}
            disabled={disabled}
            value={value.partition}
            onChange={(event) =>
              onChange({ ...value, partition: event.target.value, nodes: [], node: null })
            }
            className={INPUT_CLASS}
          >
            {(backends?.slurm.partitions ?? []).map((partition) => (
              <option key={partition} value={partition}>
                {partition}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-0 space-y-1">
          <p className="text-xs font-medium">{t.workflowSampleMode}</p>
          <p className="px-2 py-1.5 text-xs">
            {t.workflowSampleModePerSample.replace('{{count}}', String(count))}
          </p>
        </div>
      </div>

      {count === 1 && !workflow.sample_scope && (
        <p className="text-meta leading-5 text-subtle">{t.workflowSingleSampleNotice}</p>
      )}

      <fieldset className="min-w-0 space-y-2 rounded-control border border-line p-3 ">
          <legend className="px-1 text-xs font-medium">{t.workflowRunNodesTitle}</legend>
          <div className="flex flex-wrap gap-2">
            {available.map((node) => (
              <label
                key={node.name}
                className="flex min-w-0 items-center gap-2 rounded-control border border-line px-2 py-1.5 text-xs "
              >
                <input
                  type="checkbox"
                  checked={nodes.some((item) => item.node === node.name)}
                  disabled={disabled || !node.schedulable}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      nodes: event.target.checked
                        ? [...nodes, allocation(node)]
                        : nodes.filter((item) => item.node !== node.name)
                    })
                  }
                />
                <span className="truncate">
                  {node.name} · {node.schedulable ? node.state : t.workflowNodeUnavailable}
                </span>
              </label>
            ))}
          </div>
          <p className="text-meta leading-5 text-subtle">{t.workflowRunNodesHint}</p>
      </fieldset>

      {nodes.map((item, index) => {
        const node = available.find((row) => row.name === item.node);
        const assigned = assignedSamples(count, nodes, index);
        const parallel = Math.min(item.parallel_samples ?? 1, assigned);
        return (
          <section
            key={item.node}
            aria-label={item.node}
            className="min-w-0 space-y-3 rounded-control border border-line p-3 "
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-xs font-semibold">
                {item.node}
                  <span className="font-normal text-subtle">
                    {' · '}
                    {t.workflowRunAssignedSamples.replace('{{count}}', String(assigned))}
                  </span>
              </h4>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    onChange({ ...value, nodes: nodes.filter((row) => row.node !== item.node) })
                  }
                  className="rounded-control border border-line px-2 py-1 text-meta disabled:opacity-50 "
                >
                  {t.workflowRunNodeRemove}
                </button>
            </div>
            <p className="break-words text-meta leading-5 text-subtle">
              {t.workflowRunNodeCapacity
                .replace('{{cpus}}', String(node?.cpus ?? '?'))
                .replace(
                  '{{memory}}',
                  String(node?.memory_mb ? Math.floor(node.memory_mb / 1024) : '?')
                )
                .replace('{{gpus}}', String(node?.gpus ?? 0))}
              {node?.allocated_cpus !== undefined
                ? ` · ${t.workflowRunNodeAllocated.replace('{{cpus}}', String(node.allocated_cpus))}`
                : ''}
            </p>
              <div className="min-w-0 space-y-1">
                <label
                  htmlFor={`${workflow.workflow_id}${item.node}-parallel`}
                  className="block text-xs font-medium"
                >
                  {t.workflowRunParallelSamples}
                </label>
                <input
                  id={`${workflow.workflow_id}${item.node}-parallel`}
                  type="number"
                  min={1}
                  max={128}
                  step={1}
                  disabled={disabled}
                  value={
                    Number.isFinite(item.parallel_samples ?? 1) ? (item.parallel_samples ?? 1) : ''
                  }
                  onChange={(event) =>
                    onChange({
                      ...value,
                      nodes: nodes.map((row) =>
                        row.node === item.node
                          ? {
                              ...row,
                              parallel_samples:
                                event.target.value === '' ? Number.NaN : Number(event.target.value)
                            }
                          : row
                      )
                    })
                  }
                  className={INPUT_CLASS}
                />
                <p className="text-meta leading-5 text-subtle">
                  {t.workflowRunParallelHint
                    .replace('{{assigned}}', String(assigned))
                    .replace('{{parallel}}', Number.isFinite(parallel) ? String(parallel) : '—')}
                </p>
              </div>
            <ResourceFields
              id={workflow.workflow_id + item.node}
              value={item}
              node={node}
              disabled={disabled}
              t={t}
              onChange={(key, number) =>
                onChange({
                  ...value,
                  nodes: nodes.map((row) =>
                    row.node === item.node ? { ...row, [key]: number } : row
                  )
                })
              }
            />
            {Number.isInteger(parallel) && parallel > 0 && (
              <p className="break-words text-meta leading-5 text-subtle">
                {t.workflowRunParallelTotal
                  .replace('{{cpus}}', String(item.cpus * parallel))
                  .replace('{{memory}}', String(item.memory_gb * parallel))
                  .replace('{{gpus}}', String(item.gpus * parallel))}
              </p>
            )}
          </section>
        );
      })}
      <p className="text-meta leading-5 text-subtle">{t.workflowResourcesHint}</p>
      {errors.length > 0 && (
        <ul role="alert" className="list-inside list-disc space-y-1 text-meta text-danger">
          {errors.map((error) => (
            <li key={error} className="break-words">
              {error}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
