import type {
  ExecutionBackends,
  ExecutionOptions,
  NodeAllocation,
  Workflow
} from './workflowTypes';
import type { TranslationKeys } from '../../types/i18n';

export const RESOURCE_FIELDS = [
  ['cpus', 'workflowResourcesCpus', 1, 128],
  ['memory_gb', 'workflowResourcesMemory', 1, 512],
  ['gpus', 'workflowResourcesGpus', 0, 1],
  ['time_minutes', 'workflowResourcesTime', 1, 1440]
] as const;

export type ResourceKey = (typeof RESOURCE_FIELDS)[number][0];

export function assignedSamples(count: number, nodes: NodeAllocation[], index: number) {
  return Math.floor(count / nodes.length) + (index < count % nodes.length ? 1 : 0);
}

/**
 * Resource validation ported from the old frontend: eligible sample scope,
 * mandatory per-sample scheduling for partial scope, node capacity and the
 * method's thread/GPU requirements.
 */
export function executionErrors(
  value: ExecutionOptions,
  backends: ExecutionBackends | null,
  workflow: Workflow,
  t: TranslationKeys
): string[] {
  const errors: string[] = [];
  if (workflow.sample_scope) {
    if (!workflow.sample_scope.eligible_sample_ids.length) {
      errors.push(t.workflowErrorNoCommonSamples);
    }
    if (value.backend !== 'slurm' || value.sample_mode !== 'per_sample') {
      errors.push(t.workflowErrorPerSampleNeedsSlurm);
    }
  }
  if (value.backend !== 'slurm') return errors;
  const nodes = value.nodes ?? [];
  const batch = value.sample_mode === 'per_sample';
  const count =
    workflow.sample_scope?.eligible_sample_ids.length ??
    workflow.dataset?.inspection.sample_count ??
    0;
  if (!backends?.slurm.available || !backends.slurm.partitions.includes(value.partition)) {
    errors.push(t.workflowErrorPickPartition);
  }
  if (batch && (!nodes.length || !count)) errors.push(t.workflowErrorPerSampleNeedsNodes);
  if (nodes.length > (batch ? count : 1)) errors.push(t.workflowErrorTooManyNodes);
  const concurrent = nodes.map((allocation, index) =>
    batch ? Math.min(allocation.parallel_samples ?? 1, assignedSamples(count, nodes, index)) : 1
  );
  if (concurrent.reduce((sum, number) => sum + number, 0) > 128) {
    errors.push(t.workflowErrorParallelLimit);
  }
  if (
    !batch &&
    workflow.steps.some((step) =>
      Object.values(step.input_bindings ?? {}).some(
        (binding) => binding.binding_mode === 'per_sample' && (binding.coverage?.total ?? 0) > 1
      )
    )
  ) {
    errors.push(t.workflowErrorPerSampleRequired);
  }
  for (const [index, allocation] of (nodes.length ? nodes : [value]).entries()) {
    const name = 'node' in allocation ? allocation.node : null;
    const target = name ?? t.workflowResourcesLabel;
    const node = name
      ? backends?.slurm.nodes?.find(
          (item) => item.name === name && item.partition === value.partition
        )
      : undefined;
    const parallel = 'parallel_samples' in allocation ? (allocation.parallel_samples ?? 1) : 1;
    if (!Number.isInteger(parallel) || parallel < 1 || parallel > 128) {
      errors.push(t.workflowErrorParallelRange.replace('{{target}}', target));
    }
    if (!batch && parallel !== 1) errors.push(t.workflowErrorParallelOnlyPerSample);
    if (name && (!node || !node.schedulable)) {
      errors.push(t.workflowErrorNodeUnschedulable.replace('{{node}}', name));
    }
    for (const [key, labelKey, min, max] of RESOURCE_FIELDS) {
      const capacity =
        key === 'cpus'
          ? node?.cpus
          : key === 'memory_gb' && node?.memory_mb
            ? Math.floor(node.memory_mb / 1024)
            : key === 'gpus'
              ? node?.gpus
              : undefined;
      const limit = Math.min(max, capacity ?? max);
      if (!Number.isInteger(allocation[key]) || allocation[key] < min || allocation[key] > limit) {
        errors.push(
          t.workflowErrorResourceRange
            .replace('{{target}}', target)
            .replace('{{label}}', t[labelKey])
            .replace('{{min}}', String(min))
            .replace('{{max}}', String(limit))
        );
      }
      if (
        key !== 'time_minutes' &&
        capacity !== undefined &&
        allocation[key] * (concurrent[index] ?? 1) > capacity
      ) {
        errors.push(
          t.workflowErrorNodeCapacity
            .replace('{{target}}', name ?? t.workflowResourcesLabel)
            .replace('{{parallel}}', String(concurrent[index] ?? 1))
            .replace('{{label}}', t[labelKey])
        );
      }
    }
    if (allocation.gpus && !backends?.slurm.gpu_available) errors.push(t.workflowErrorNoGpu);
    for (const step of workflow.steps) {
      for (const key of ['threads', 'omp_threads', 'n_jobs', 'nthreads']) {
        const threads = step.selected_parameters[key];
        if (typeof threads === 'number' && (threads < (key === 'nthreads' ? 0 : 1) || threads > allocation.cpus)) {
          errors.push(
            t.workflowErrorThreadsExceedCpus
              .replace('{{target}}', target)
              .replace('{{threads}}', String(threads))
          );
        }
      }
      if (
        (step.selected_parameters.backend === 'cuda' ||
          step.selected_parameters.device === 'cuda') &&
        !allocation.gpus
      ) {
        errors.push(t.workflowErrorCudaNeedsGpu.replace('{{target}}', target));
      }
    }
  }
  return [...new Set(errors)];
}
