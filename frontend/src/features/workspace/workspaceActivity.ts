import type { WorkspaceEvent } from './workspaceTypes';
import { isWorkspaceOperation } from './workspaceTypes';

export type WorkspaceActivity = WorkspaceEvent & { key: number };

function argumentsKey(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(argumentsKey).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.entries(value)
    .sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => JSON.stringify(k) + ':' + argumentsKey(v)).join(',') + '}';
  return JSON.stringify(value) ?? '';
}

/** Pair lifecycle events, retaining separate calls, retries and independent tasks. */
export function workspaceActivities(events: WorkspaceEvent[]): WorkspaceActivity[] {
  const activities: WorkspaceActivity[] = [];
  for (const [index, event] of events.entries()) {
    const pending = event.status !== 'running' ? activities.findLastIndex(item =>
      item.tool === event.tool && item.agent === event.agent && item.status === 'running' &&
      (!event.arguments || argumentsKey(item.arguments) === argumentsKey(event.arguments))) : -1;
    if (pending >= 0) activities[pending] = { ...activities[pending], ...event };
    else activities.push({ ...event, key: index });
  }
  return activities;
}

export function supersededOperationIds(events: WorkspaceEvent[]): Set<string> {
  const tasks = new Map(events.flatMap(event => event.data_operation ? [[event.data_operation.id, event.data_operation] as const] : []));
  const superseded = new Set<string>();
  for (const task of tasks.values()) {
    const next = task.quality?.proposal_review?.superseded_by;
    if (task.status !== 'pending' || !next || next === task.id || tasks.get(next)?.conversation_id !== task.conversation_id) continue;
    const seen = new Set([task.id]);
    let identity: string | undefined = next;
    while (identity && tasks.has(identity) && !seen.has(identity)) {
      seen.add(identity);
      identity = tasks.get(identity)?.quality?.proposal_review?.superseded_by;
    }
    if (!identity || !seen.has(identity)) superseded.add('data:' + task.id);
  }
  return superseded;
}

export function latestOperationEvents(events: WorkspaceEvent[]): WorkspaceActivity[] {
  const latest = new Map<string, WorkspaceActivity>();
  for (const event of workspaceActivities(events)) {
    if (event.data_operation) latest.set('data:' + event.data_operation.id, event);
    if (isWorkspaceOperation(event.operation)) latest.set('workspace:' + event.operation.id, event);
  }
  const superseded = supersededOperationIds(events);
  return [...latest].filter(([key]) => !superseded.has(key)).map(([, event]) => event);
}
