import type { RunStep, WorkflowRun } from './runsTypes';

export type MonitorStatus = RunStep['status'];
export type MonitorFilter = MonitorStatus | 'all';
export type MonitorGroup = {
  id: string;
  label: string;
  sampleId: string;
  nodes: string[];
  steps: RunStep[];
  status: MonitorStatus;
  progress: number;
};

export const MONITOR_PAGE_SIZE = 8;

export function groupRunSteps(steps: RunStep[]): MonitorGroup[] {
  const groups = new Map<string, MonitorGroup>();
  for (const step of steps) {
    const id = step.sample_id ? `sample:${step.sample_id}` : `step:${step.step_id}`;
    let group = groups.get(id);
    if (!group) {
      group = {
        id,
        label: step.sample_label || step.sample_id || step.title,
        sampleId: step.sample_id || '',
        nodes: [],
        steps: [],
        status: 'queued',
        progress: 0
      };
      groups.set(id, group);
    }
    group.steps.push(step);
    const node = step.node_name || step.scheduler?.node_list;
    if (node && !group.nodes.includes(node)) group.nodes.push(node);
  }
  return Array.from(groups.values(), (group) => {
    // A failed step must remain visible even when its downstream steps never started.
    group.status = group.steps.some((step) => step.status === 'failed')
      ? 'failed'
      : group.steps.some((step) => step.status === 'running')
        ? 'running'
        : group.steps.every((step) => step.status === 'succeeded')
          ? 'succeeded'
          : group.steps.some((step) => step.status === 'cancelled')
            ? 'cancelled'
            : 'queued';
    group.progress =
      group.steps.reduce(
        (sum, step) =>
          sum +
          (step.status === 'succeeded' ? 100 : Math.max(0, Math.min(100, step.progress || 0))),
        0
      ) / group.steps.length;
    return group;
  });
}

export function filterRunGroups(
  groups: MonitorGroup[],
  status: MonitorFilter,
  query: string
): MonitorGroup[] {
  const term = query.trim().toLocaleLowerCase();
  return groups.filter(
    (group) =>
      (status === 'all' || group.status === status) &&
      (!term ||
        [
          group.label,
          group.sampleId,
          ...group.nodes,
          ...group.steps.flatMap((step) => [step.title, String(step.scheduler?.job_id ?? '')])
        ].some((value) => value.toLocaleLowerCase().includes(term)))
  );
}

export function monitorPage<T>(items: T[], requestedPage: number) {
  const pages = Math.max(1, Math.ceil(items.length / MONITOR_PAGE_SIZE));
  const page = Math.max(0, Math.min(requestedPage, pages - 1));
  return {
    page,
    pages,
    items: items.slice(page * MONITOR_PAGE_SIZE, (page + 1) * MONITOR_PAGE_SIZE)
  };
}

export const RUN_FILTERS: MonitorFilter[] = [
  'all',
  'running',
  'failed',
  'queued',
  'succeeded',
  'cancelled'
];

export function runFilterCounts(groups: MonitorGroup[]): Record<MonitorFilter, number> {
  const counts: Record<MonitorFilter, number> = {
    all: groups.length,
    queued: 0,
    running: 0,
    succeeded: 0,
    failed: 0,
    cancelled: 0
  };
  groups.forEach((group) => {
    counts[group.status] += 1;
  });
  return counts;
}

export function runStatusActive(run: WorkflowRun): boolean {
  return ['queued', 'running', 'cancel_requested'].includes(run.status);
}
