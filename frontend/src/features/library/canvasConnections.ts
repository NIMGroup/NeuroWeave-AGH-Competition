import type { InputPort, Method, Node, SourceLink } from './libraryApi';
import { inputPortKey, linkSources } from './pipelineGraph';
import type { Point } from './pipelineGraph';
import { pathData, routeConnection } from './canvasGeometry';
import type { Bounds } from './canvasGeometry';

export type CanvasConnection = {
  id: string;
  number: number;
  color: string;
  path: string;
  points: Point[];
  from: string;
  fromSlot: string;
  to: string;
  sourceLabel: string;
  targetLabel: string;
  input: string;
  source?: SourceLink;
  sharedPort?: InputPort;
};
const colors = [
  '#2563eb',
  '#0d9488',
  '#d97706',
  '#db2777',
  '#7c3aed',
  '#0891b2',
  '#65a30d'
];
export const dependencyKey = (to: string, input: string, source: SourceLink) =>
  `${source.step_id}/${source.output_slot_id}->${to}/${input}`;

export function canvasConnections(
  steps: Node[],
  definitions: Record<string, Method>,
  shared: InputPort[][],
  endpoint: (id: string, slot: string, output: boolean) => Point | null,
  boxes: Bounds[]
): CanvasConnection[] {
  const result: CanvasConnection[] = [];
  const label = (id: string, slot: string, output: boolean) => {
    const index = steps.findIndex((n) => n.id === id);
    const method = definitions[steps[index]?.method_id];
    const port = (output ? method?.output_slots : method?.input_slots)?.find(
      (p) => p.id === slot
    );
    return `${index + 1}. ${method?.label ?? id} / ${port?.label ?? slot}`;
  };
  const add = (
    from: string,
    output: string,
    to: string,
    input: string,
    isShared: boolean
  ) => {
    const a = endpoint(from, output, !isShared),
      b = endpoint(to, input, false);
    if (!a || !b) return;
    const points = routeConnection(a, b, boxes, result.length, isShared);
    const source = { step_id: from, output_slot_id: output };
    const sharedPort = { step_id: to, input_slot_id: input };
    result.push({
      id: isShared
        ? 'shared/' + inputPortKey(sharedPort)
        : dependencyKey(to, input, source),
      number: result.length + 1,
      color: isShared ? '#a855f7' : colors[result.length % colors.length],
      points,
      path: pathData(points),
      from,
      fromSlot: output,
      to,
      input,
      sourceLabel: label(from, output, !isShared),
      targetLabel: label(to, input, false),
      ...(isShared ? { sharedPort } : { source })
    });
  };
  for (const n of steps)
    for (const [input, binding] of Object.entries(n.inputs))
      for (const source of linkSources(binding))
        add(source.step_id, source.output_slot_id, n.id, input, false);
  for (const group of shared)
    for (const port of group.slice(1))
      add(
        group[0].step_id,
        group[0].input_slot_id,
        port.step_id,
        port.input_slot_id,
        true
      );
  return result;
}

export const connectionLabel = (edge: CanvasConnection) =>
  `${edge.sourceLabel} ${edge.sharedPort ? '↔' : '→'} ${edge.targetLabel}`;
