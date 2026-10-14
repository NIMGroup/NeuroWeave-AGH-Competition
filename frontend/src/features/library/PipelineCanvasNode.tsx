import type { PointerEvent } from 'react';
import { ui } from '../../lib/libraryText';
import type { Method, Node } from './libraryApi';
import type { Point } from './pipelineGraph';
import type { canvasOutputSlots } from './pipelineGraph';
import type { displayedInputSlots } from '../workflows/workflowConditions';
import { NODE_WIDTH } from './canvasGeometry';

type Props = {
  node: Node;
  index: number;
  method?: Method;
  position: Point;
  inputs: ReturnType<typeof displayedInputSlots>;
  outputs: ReturnType<typeof canvasOutputSlots>;
  selected: boolean;
  disabled: boolean;
  readOnly?: boolean;
  sharedSlots: string[];
  focus?: { color: string; input?: string; output?: string };
  dimmed: boolean;
  onSelect: () => void;
  onMove: (point: Point) => void;
  onDragStart: (event: PointerEvent<HTMLButtonElement>) => void;
  onPortPointer: (
    event: PointerEvent<HTMLButtonElement>,
    slot: string,
    kind: 'input' | 'output'
  ) => void;
  onPortKeyboard: (slot: string, kind: 'input' | 'output') => void;
};

/** Node appearance and accessible ports; all graph mutations stay in the editor. */
export function PipelineCanvasNode(props: Props) {
  const { node, method, position, focus } = props;
  return (
    <article
      data-pipeline-node={node.id}
      onClick={props.onSelect}
      className={
        'absolute rounded-lg border bg-raised shadow-md ' +
        (props.selected
          ? 'border-accent ring-2 ring-accent/30'
          : 'border-line')
      }
      style={{
        left: position.x,
        top: position.y,
        width: NODE_WIDTH,
        opacity: props.dimmed ? 0.55 : 1,
        borderColor: focus?.color
      }}
    >
      <button
        className={'flex h-14 w-full items-center rounded-t-lg border-b border-line px-3 text-left text-xs font-semibold ' + (props.readOnly ? 'cursor-pointer' : 'cursor-grab active:cursor-grabbing')}
        aria-label={ui(props.readOnly ? '查看步骤：{0}' : '移动或选择 {0}', method?.label ?? node.method_id)}
        disabled={props.disabled}
        onClick={props.onSelect}
        onPointerDown={props.onDragStart}
        onKeyDown={(event) => {
          if (props.readOnly) return;
          if (!event.key.startsWith('Arrow')) return;
          event.preventDefault();
          props.onMove({
            x: Math.max(
              12,
              position.x +
                (event.key === 'ArrowRight'
                  ? 20
                  : event.key === 'ArrowLeft'
                    ? -20
                    : 0)
            ),
            y: Math.max(
              12,
              position.y +
                (event.key === 'ArrowDown'
                  ? 20
                  : event.key === 'ArrowUp'
                    ? -20
                    : 0)
            )
          });
        }}
      >
        <span className="mr-2 shrink-0 rounded bg-accent-quiet px-1 text-accent">
          {props.index + 1}
        </span>
        <span className="line-clamp-2 break-words">
          {method?.label ?? node.method_id}
        </span>
      </button>
      {!!Object.keys(node.when ?? {}).length && <p className="absolute -top-5 right-1 rounded bg-raised px-1 text-micro text-subtle">{ui('条件步骤')}</p>}
      <div className="grid grid-cols-2 pb-3 text-micro">
        <div>
          {props.inputs.map((slot) => (
            <div
              key={slot.id}
              className="relative flex h-8 items-center pl-4 pr-1"
              style={
                focus?.input === slot.id
                  ? { background: focus.color + '22', color: focus.color }
                  : undefined
              }
              title={`${slot.label} · ${slot.type}${slot.requiredNow ? ui(' · 必需') : ui(' · 可选')}${node.inputs[slot.id] ? ui(' · 已连接') : ''}`}
            >
              <button
                disabled={props.disabled || props.readOnly}
                data-node={node.id}
                data-pipeline-input={slot.id}
                aria-label={ui('输入 {0} / {1}', method?.label, slot.label)}
                className={
                  'absolute -left-2 top-2 h-4 w-4 rounded-full border-2 border-accent ' +
                  (node.inputs[slot.id] ? 'bg-accent' : 'bg-raised')
                }
                style={
                  focus?.input === slot.id
                    ? { borderColor: focus.color, background: focus.color }
                    : undefined
                }
                onPointerDown={(event) =>
                  props.onPortPointer(event, slot.id, 'input')
                }
                onClick={(event) => {
                  event.stopPropagation();
                  if (event.detail === 0)
                    props.onPortKeyboard(slot.id, 'input');
                }}
              />
              <span className="truncate">
                {props.sharedSlots.includes(slot.id) ? '↔ ' : ''}
                {slot.label}
                {slot.requiredNow
                  ? ' *'
                  : slot.conditionalOptional
                    ? ui('（当前非必需）')
                    : ''}
              </span>
            </div>
          ))}
        </div>
        <div>
          {props.outputs.map((slot) => (
            <div
              key={slot.id}
              className="relative flex h-8 items-center justify-end pl-1 pr-4"
              style={
                focus?.output === slot.id
                  ? { background: focus.color + '22', color: focus.color }
                  : undefined
              }
              title={`${slot.label} · ${slot.type}${slot.available ? '' : ui(' · 当前参数下不生成，请调整参数或删除连线')}`}
            >
              <span
                className={
                  slot.available ? 'truncate' : 'truncate text-warning'
                }
              >
                {slot.label}
                {!slot.available && ui('（当前不生成）')}
              </span>
              <button
                disabled={props.disabled || props.readOnly || !slot.available}
                data-node={node.id}
                data-pipeline-output={slot.id}
                aria-label={ui('输出 {0} / {1}', method?.label, slot.label)}
                className={
                  'absolute -right-2 top-2 h-4 w-4 rounded-full border-2 ' +
                  (slot.available
                    ? 'border-accent bg-accent'
                    : 'border-warning bg-warning-quiet')
                }
                style={
                  focus?.output === slot.id
                    ? { borderColor: focus.color, background: focus.color }
                    : undefined
                }
                onPointerDown={(event) =>
                  props.onPortPointer(event, slot.id, 'output')
                }
                onClick={(event) => {
                  event.stopPropagation();
                  if (event.detail === 0)
                    props.onPortKeyboard(slot.id, 'output');
                }}
              />
            </div>
          ))}
        </div>
      </div>
      {!method && (
        <p className="px-3 pb-3 text-xs text-subtle">
          {ui('正在读取端口定义…')}
        </p>
      )}
    </article>
  );
}
