import { ui } from '../../lib/libraryText';
import { connectionLabel } from './canvasConnections';
import type { CanvasConnection } from './canvasConnections';

export function PipelineConnections({
  edges,
  selected,
  hovered,
  onSelect,
  onHover
}: {
  edges: CanvasConnection[];
  selected: string | null;
  hovered: string | null;
  onSelect: (edge: CanvasConnection) => void;
  onHover: (id: string | null) => void;
}) {
  const focus = hovered ?? selected;
  // The focused wire is painted last, so crossings cannot hide it.
  const ordered = [
    ...edges.filter((edge) => edge.id !== focus),
    ...edges.filter((edge) => edge.id === focus)
  ];
  return (
    <>
      <defs>
        <marker
          id="pipeline-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
        </marker>
      </defs>
      {ordered.map((edge) => (
        <g
          key={edge.id}
          data-pipeline-edge={edge.id}
          aria-label={edge.sharedPort ? ui('共享输入连线') : undefined}
          opacity={focus && focus !== edge.id ? 0.16 : 1}
        >
          <title>{connectionLabel(edge)}</title>
          <path
            d={edge.path}
            stroke="var(--canvas)"
            strokeWidth="7"
            fill="none"
            strokeLinejoin="round"
          />
          <path
            d={edge.path}
            stroke={edge.color}
            strokeWidth={focus === edge.id ? 4 : 2.5}
            fill="none"
            strokeLinejoin="round"
            strokeDasharray={edge.sharedPort ? '6 4' : undefined}
            markerEnd={edge.sharedPort ? undefined : 'url(#pipeline-arrow)'}
          />
          <path
            d={edge.path}
            stroke="transparent"
            strokeWidth="14"
            fill="none"
            style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
            role="button"
            tabIndex={0}
            aria-label={connectionLabel(edge)}
            aria-pressed={selected === edge.id}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => onSelect(edge)}
            onPointerEnter={() => onHover(edge.id)}
            onPointerLeave={() => onHover(null)}
            onFocus={() => onHover(edge.id)}
            onBlur={() => onHover(null)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelect(edge);
              }
            }}
          />
        </g>
      ))}
    </>
  );
}

export function PipelineConnectionList({
  edges,
  selected,
  onSelect
}: {
  edges: CanvasConnection[];
  selected: string | null;
  onSelect: (edge: CanvasConnection) => void;
}) {
  return (
    <div
      aria-label={ui('连接清单')}
      className="max-h-32 shrink-0 overflow-auto border-b border-line bg-raised p-2 text-xs"
    >
      {!edges.length && <p className="text-subtle">{ui('尚未建立连接')}</p>}
      {edges.map((edge) => (
        <button
          key={edge.id}
          type="button"
          aria-pressed={selected === edge.id}
          className={
            'flex w-full items-start gap-2 rounded px-2 py-1 text-left hover:bg-accent-quiet ' +
            (selected === edge.id ? 'bg-accent-quiet' : '')
          }
          onClick={() => onSelect(edge)}
        >
          <span
            className="shrink-0 font-semibold"
            style={{ color: edge.color }}
          >
            #{edge.number}
          </span>
          <span className="break-words">{connectionLabel(edge)}</span>
        </button>
      ))}
    </div>
  );
}
