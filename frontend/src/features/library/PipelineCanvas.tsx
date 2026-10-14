import { Minus, Plus } from 'lucide-react';
import { nodeMethod } from './artifactSemantics';
import { ui } from '../../lib/libraryText';
import { useEffect, useMemo, useRef, useState } from 'react';
import { displayedInputSlots } from '../workflows/workflowConditions';
import type { InputPort, Method, Node, SourceLink } from './libraryApi';
import {
  nodePosition,
  canvasOutputSlots,
  displayBindings,
  inputPortKey
} from './pipelineGraph';
import type { Point } from './pipelineGraph';
import { useCanvasViewport } from './useCanvasViewport';
import {
  enclosingBounds,
  NODE_WIDTH,
  PORT_OFFSET,
  PORT_ROW,
  nodeHeight
} from './canvasGeometry';
import {
  canvasConnections,
  connectionLabel,
  dependencyKey
} from './canvasConnections';
import type { CanvasConnection } from './canvasConnections';
import {
  PipelineConnections,
  PipelineConnectionList
} from './PipelineConnections';
import { button } from './pipelineEditorStyles';
import { PipelineCanvasNode } from './PipelineCanvasNode';
import { canvasInputAtPoint } from './canvasHitTest';
type Props = {
  sharedInputs?: InputPort[][];
  onShareInputs?: (from: InputPort, to: InputPort) => void;
  onDetachSharedInput?: (port: InputPort) => void;
  showConditionalInputs?: boolean;
  onToggleConditionalInputs?: () => void;
  steps: Node[];
  definitions: Record<string, Method>;
  selected: string | null;
  disabled: boolean;
  readOnly?: boolean;
  onSelect: (id: string) => void;
  onMove: (id: string, position: Point) => void;
  onAdd: (id: string, position: Point) => void;
  onConnect: (from: string, output: string, to: string, input: string) => void;
  onDisconnect: (to: string, input: string, source?: SourceLink) => void;
  onArrange: () => void;
};

export function PipelineCanvas(props: Props) {
  const { viewport, zoom, changeZoom, fit, point, reset } = useCanvasViewport();
  const [wire, setWire] = useState<{
    kind: 'input' | 'output';
    from: string;
    output: string;
    point: Point;
  } | null>(null);
  const [edge, setEdge] = useState<{
    to: string;
    input: string;
    source: SourceLink;
  } | null>(null);
  const [sharedEdge, setSharedEdge] = useState<InputPort | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [showConnections, setShowConnections] = useState(false);
  const gesture = useRef<
    | { kind: 'node'; id: string; start: Point; position: Point }
    | { kind: 'pan'; start: Point; left: number; top: number }
    | null
  >(null);
  const shared = props.sharedInputs ?? [];
  // Geometry changes only with graph edits, never on hover or zoom.
  const geometry = useMemo(() => {
    const positions = Object.fromEntries(
      props.steps.map((n, i) => [n.id, nodePosition(n, i)])
    );
    const outputs = Object.fromEntries(
      props.steps.map((n) => [
        n.id,
        props.definitions[n.method_id]
          ? canvasOutputSlots(
              n,
              nodeMethod(n, props.definitions, props.steps)!,
              props.steps
            )
          : []
      ])
    );
    const inputs = Object.fromEntries(
      props.steps.map((n) => [
        n.id,
        props.definitions[n.method_id]
          ? displayedInputSlots(
              props.definitions[n.method_id],
              n.parameters,
              displayBindings(n, props.sharedInputs ?? []),
              props.showConditionalInputs
            )
          : []
      ])
    );
    const boxes = props.steps.map((n) => ({
      ...positions[n.id],
      width: NODE_WIDTH,
      height: nodeHeight(Math.max(inputs[n.id].length, outputs[n.id].length))
    }));
    const endpoint = (
      id: string,
      slot: string,
      output: boolean
    ): Point | null => {
      const index =
        (output ? outputs[id] : inputs[id])?.findIndex((s) => s.id === slot) ??
        -1;
      return index < 0
        ? null
        : {
            x: positions[id].x + (output ? NODE_WIDTH : 0),
            y: positions[id].y + PORT_OFFSET + index * PORT_ROW
          };
    };
    const connections = canvasConnections(
      props.steps,
      props.definitions,
      props.sharedInputs ?? [],
      endpoint,
      boxes
    );
    const bounds = enclosingBounds([
      ...boxes,
      ...connections.flatMap((e) =>
        e.points.map((p) => ({ ...p, width: 1, height: 1 }))
      )
    ]);
    return { positions, outputs, inputs, boxes, endpoint, connections, bounds };
  }, [
    props.steps,
    props.definitions,
    props.sharedInputs,
    props.showConditionalInputs
  ]);
  const { positions, outputs, inputs, endpoint, connections, bounds } =
    geometry;
  const fitted = useRef(false);
  useEffect(() => {
    if (!props.readOnly || !props.steps.length) return;
    const element = viewport.current;
    if (!element) return;
    // Cached graphs mount before the native dialog opens. Observe its real
    // layout instead of fitting a hidden, zero-sized viewport to minimum zoom.
    const fitVisible = () => {
      if (fitted.current || !element.clientWidth || !element.clientHeight) return;
      fitted.current = true;
      fit(bounds);
    };
    const observer = new ResizeObserver(fitVisible);
    observer.observe(element);
    fitVisible();
    return () => observer.disconnect();
  }, [props.readOnly, props.steps.length, bounds, fit, viewport]);
  const selectedEdge = edge
    ? dependencyKey(edge.to, edge.input, edge.source)
    : sharedEdge
      ? 'shared/' + inputPortKey(sharedEdge)
      : null;
  const focusEdge = connections.find((e) => e.id === (hovered ?? selectedEdge));
  const selectedConnection = connections.find((e) => e.id === selectedEdge);
  const selectConnection = (connection: CanvasConnection) => {
    setWire(null);
    setEdge(
      connection.source
        ? {
            to: connection.to,
            input: connection.input,
            source: connection.source
          }
        : null
    );
    setSharedEdge(connection.sharedPort ?? null);
  };
  const width = Math.max(1800, bounds.x + bounds.width + 300),
    height = Math.max(1100, bounds.y + bounds.height + 300);
  const fitSelected = () => {
    const index = props.steps.findIndex((n) => n.id === props.selected);
    if (index >= 0) fit(geometry.boxes[index]);
  };
  const finish = (x: number, y: number) => {
    if (!wire || props.disabled || props.readOnly) return;
    const input = canvasInputAtPoint(viewport.current, x, y);
    if (input) {
      const to = {
        step_id: input.dataset.node!,
        input_slot_id: input.dataset.pipelineInput!
      };
      if (wire.kind === 'input') {
        if (wire.from === to.step_id && wire.output === to.input_slot_id)
          return;
        props.onShareInputs?.(
          { step_id: wire.from, input_slot_id: wire.output },
          to
        );
      } else
        props.onConnect(wire.from, wire.output, to.step_id, to.input_slot_id);
      setWire(null);
    }
  };
  return (
    <section
      aria-label={ui('Pipeline 节点画布')}
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded border border-line"
    >
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-line bg-raised p-2 text-xs">
        <button
          className={button}
          aria-label={ui('缩小画布')}
          onClick={() => changeZoom(zoom / 1.2)}
        >
          <Minus aria-hidden="true" className="size-4" />
        </button>
        <input
          type="range"
          className="w-20 accent-accent"
          min={2}
          max={200}
          value={Math.round(zoom * 100)}
          aria-label={ui('缩放比例')}
          onChange={(e) => changeZoom(Number(e.target.value) / 100)}
        />
        <span className="w-10 text-center tabular-nums">
          {Math.round(zoom * 100)}%
        </span>
        <button
          className={button}
          aria-label={ui('放大画布')}
          onClick={() => changeZoom(zoom * 1.2)}
        >
          <Plus aria-hidden="true" className="size-4" />
        </button>
        <button className={button} onClick={() => fit(bounds)}>
          {ui('适应全部')}
        </button>
        <button
          className={button}
          disabled={!props.selected}
          onClick={fitSelected}
        >
          {ui('定位选中节点')}
        </button>
        <button className={button} onClick={reset}>
          {ui('重置视图')}
        </button>
        {!props.readOnly && <button
          className={button}
          disabled={props.disabled || !props.steps.length}
          onClick={props.onArrange}
        >
          {ui('自动排列')}
        </button>}
        <button
          className={button}
          aria-expanded={showConnections}
          onClick={() => setShowConnections((v) => !v)}
        >
          {ui('连接清单')} ({connections.length})
        </button>
        {props.onToggleConditionalInputs && (
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={props.showConditionalInputs ?? false}
              onChange={props.onToggleConditionalInputs}
            />
            {ui('显示当前非必需的条件输入')}
          </label>
        )}
      </div>
      {showConnections && (
        <PipelineConnectionList
          edges={connections}
          selected={selectedEdge}
          onSelect={selectConnection}
        />
      )}
      {selectedConnection && (
        <div
          className="flex max-h-24 shrink-0 flex-wrap items-center gap-2 overflow-auto border-b border-line bg-raised px-3 py-2 text-xs"
          role="status"
        >
          <span
            className="min-w-0 flex-1 break-words"
            style={{ color: selectedConnection.color }}
          >
            #{selectedConnection.number} {connectionLabel(selectedConnection)}
          </span>
          <button
            className={button}
            onClick={() =>
              fit(
                enclosingBounds(
                  geometry.boxes.filter((_, i) =>
                    [selectedConnection.from, selectedConnection.to].includes(
                      props.steps[i].id
                    )
                  )
                )
              )
            }
          >
            {ui('定位连线')}
          </button>
          {!props.readOnly && <button
            className={button}
            disabled={props.disabled}
            onClick={() => {
              if (selectedConnection.source)
                props.onDisconnect(
                  selectedConnection.to,
                  selectedConnection.input,
                  selectedConnection.source
                );
              else if (selectedConnection.sharedPort)
                props.onDetachSharedInput?.(selectedConnection.sharedPort);
              setEdge(null);
              setSharedEdge(null);
            }}
          >
            {selectedConnection.sharedPort
              ? ui('解除此输入共享')
              : ui('删除所选连线')}
          </button>}
          <button
            className={button}
            onClick={() => {
              setEdge(null);
              setSharedEdge(null);
            }}
          >
            {ui('取消选择')}
          </button>
        </div>
      )}
      <div
        ref={viewport}
        aria-label={ui('画布视口')}
        className="relative min-h-0 flex-1 overflow-auto overscroll-contain bg-canvas"
        tabIndex={0}
        style={{ touchAction: 'none' }}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && (wire || selectedEdge)) {
            e.preventDefault();
            e.stopPropagation();
            setWire(null);
            setEdge(null);
            setSharedEdge(null);
            return;
          }
          if (e.target !== e.currentTarget) return;
          if (['+', '=', '-', '0', 'f', 'F'].includes(e.key)) {
            e.preventDefault();
            if (e.key === '-') changeZoom(zoom / 1.2);
            else if (e.key === '0') reset();
            else if (e.key.toLowerCase() === 'f') fit(bounds);
            else changeZoom(zoom * 1.2);
          }
        }}
        onDragOver={(e) => {
          if (
            !props.readOnly &&
            e.dataTransfer.types.includes('application/x-neuroweave-method')
          ) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          const id = e.dataTransfer.getData('application/x-neuroweave-method');
          if (id && !props.disabled && !props.readOnly)
            props.onAdd(id, point(e.clientX, e.clientY));
        }}
        onPointerDown={(e) => {
          if (
            e.button !== 0 ||
            (e.target as HTMLElement).closest(
              'button,[role="button"],[data-pipeline-node]'
            )
          )
            return;
          if (wire) {
            setWire(null);
            return;
          }
          setEdge(null);
          setSharedEdge(null);
          const el = e.currentTarget;
          el.focus();
          gesture.current = {
            kind: 'pan',
            start: { x: e.clientX, y: e.clientY },
            left: el.scrollLeft,
            top: el.scrollTop
          };
          el.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const g = gesture.current;
          if (g?.kind === 'node' && !props.disabled && !props.readOnly) {
            const p = point(e.clientX, e.clientY);
            props.onMove(g.id, {
              x: Math.max(12, Math.min(30000, g.position.x + p.x - g.start.x)),
              y: Math.max(12, Math.min(30000, g.position.y + p.y - g.start.y))
            });
          } else if (g?.kind === 'pan') {
            e.currentTarget.scrollLeft = g.left + g.start.x - e.clientX;
            e.currentTarget.scrollTop = g.top + g.start.y - e.clientY;
          } else if (wire && !props.disabled && !props.readOnly)
            setWire({ ...wire, point: point(e.clientX, e.clientY) });
        }}
        onPointerUp={(e) => {
          gesture.current = null;
          finish(e.clientX, e.clientY);
          if (e.currentTarget.hasPointerCapture(e.pointerId))
            e.currentTarget.releasePointerCapture(e.pointerId);
        }}
        onPointerCancel={() => {
          gesture.current = null;
          setWire(null);
        }}
      >
        <div
          className="relative shrink-0 overflow-hidden"
          style={{ width: width * zoom, height: height * zoom }}
        >
          <div
            className="relative origin-top-left"
            style={{
              width,
              height,
              transform: `scale(${zoom})`,
              backgroundImage:
                'radial-gradient(#94a3b844 1px, transparent 1px)',
              backgroundSize: '20px 20px'
            }}
          >
            <svg
              width={width}
              height={height}
              className="pointer-events-none absolute inset-0"
              aria-label={ui('步骤依赖连线')}
            >
              <PipelineConnections
                edges={connections}
                selected={selectedEdge}
                hovered={hovered}
                onSelect={selectConnection}
                onHover={setHovered}
              />
              {wire &&
                (() => {
                  const a = endpoint(
                    wire.from,
                    wire.output,
                    wire.kind === 'output'
                  );
                  return a ? (
                    <path
                      d={`M ${a.x} ${a.y} L ${wire.point.x} ${wire.point.y}`}
                      stroke="#3b82f6"
                      strokeWidth="2"
                      strokeDasharray="5 4"
                      fill="none"
                    />
                  ) : null;
                })()}
            </svg>
            {!props.steps.length && (
              <p className="absolute left-12 top-12 max-w-sm rounded border border-dashed border-line bg-raised p-6 text-sm text-subtle">
                {ui(
                  '从左侧拖入 Method 放在画布上，再从输出端口连到下一步的输入端口。'
                )}
              </p>
            )}
            {props.steps.map((node, nodeIndex) => (
              <PipelineCanvasNode
                key={node.id}
                node={node}
                index={nodeIndex}
                method={props.definitions[node.method_id]}
                position={positions[node.id]}
                inputs={inputs[node.id]}
                outputs={outputs[node.id]}
                selected={props.selected === node.id}
                disabled={props.disabled}
                readOnly={props.readOnly}
                sharedSlots={shared
                  .flat()
                  .filter((p) => p.step_id === node.id)
                  .map((p) => p.input_slot_id)}
                focus={
                  focusEdge &&
                  (focusEdge.from === node.id || focusEdge.to === node.id)
                    ? {
                        color: focusEdge.color,
                        input:
                          focusEdge.to === node.id
                            ? focusEdge.input
                            : focusEdge.sharedPort
                              ? focusEdge.fromSlot
                              : undefined,
                        output:
                          focusEdge.from === node.id
                            ? focusEdge.source?.output_slot_id
                            : undefined
                      }
                    : undefined
                }
                dimmed={
                  !!focusEdge &&
                  focusEdge.from !== node.id &&
                  focusEdge.to !== node.id
                }
                onSelect={() => props.onSelect(node.id)}
                onMove={(p) => { if (!props.readOnly && !props.disabled) props.onMove(node.id, p); }}
                onDragStart={(event) => {
                  if (event.button !== 0) return;
                  event.stopPropagation();
                  props.onSelect(node.id);
                  setWire(null);
                  setEdge(null);
                  setSharedEdge(null);
                  if (props.readOnly || props.disabled) return;
                  gesture.current = {
                    kind: 'node',
                    id: node.id,
                    start: point(event.clientX, event.clientY),
                    position: positions[node.id]
                  };
                  viewport.current?.setPointerCapture(event.pointerId);
                }}
                onPortPointer={(event, slot, kind) => {
                  event.stopPropagation();
                  if (
                    props.readOnly || props.disabled ||
                    event.button !== 0 ||
                    (kind === 'input' && (wire || !props.onShareInputs))
                  )
                    return;
                  props.onSelect(node.id);
                  setEdge(null);
                  setSharedEdge(null);
                  setWire({
                    kind,
                    from: node.id,
                    output: slot,
                    point: endpoint(node.id, slot, kind === 'output')!
                  });
                  viewport.current?.setPointerCapture(event.pointerId);
                }}
                onPortKeyboard={(slot, kind) => {
                  if (props.readOnly || props.disabled) return;
                  if (kind === 'input' && wire) {
                    if (wire.kind === 'input')
                      props.onShareInputs?.(
                        { step_id: wire.from, input_slot_id: wire.output },
                        { step_id: node.id, input_slot_id: slot }
                      );
                    else props.onConnect(wire.from, wire.output, node.id, slot);
                    setWire(null);
                  } else if (kind === 'output' || props.onShareInputs)
                    setWire({
                      kind,
                      from: node.id,
                      output: slot,
                      point: endpoint(node.id, slot, kind === 'output')!
                    });
                }}
              />
            ))}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2 border-t border-line bg-raised px-2 py-1 text-micro text-subtle">
        {wire ? (
          <>
            <span>
              {wire.kind === 'input'
                ? ui('选择另一输入端口，共用文件；Esc 取消')
                : ui('选择目标输入端口；Esc 取消')}
            </span>
            <button className={button} onClick={() => setWire(null)}>
              {ui('取消连线')}
            </button>
          </>
        ) : (
          <span>
            {ui(
              'Ctrl/⌘ + 滚轮或触控板捏合缩放 · 拖动空白处平移 · F 适应全部 · 点击连线查看两端'
            )}
          </span>
        )}
      </div>
    </section>
  );
}
