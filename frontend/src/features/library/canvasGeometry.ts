import type { Point } from './pipelineGraph';

export type Bounds = Point & { width: number; height: number };
export const NODE_WIDTH = 280;
export const PORT_ROW = 32;
export const PORT_OFFSET = 72;
export const nodeHeight = (ports: number) => 70 + ports * PORT_ROW;
export const clampZoom = (zoom: number) => Math.max(0.02, Math.min(2, zoom));

export function enclosingBounds(rects: Bounds[]): Bounds {
  if (!rects.length) return { x: 0, y: 0, width: 480, height: 320 };
  const x = Math.min(...rects.map((r) => r.x));
  const y = Math.min(...rects.map((r) => r.y));
  return {
    x,
    y,
    width: Math.max(...rects.map((r) => r.x + r.width)) - x,
    height: Math.max(...rects.map((r) => r.y + r.height)) - y
  };
}

/** Keep the world coordinate under the pointer stationary as scale changes. */
export function zoomAnchor(
  scroll: Point,
  anchor: Point,
  from: number,
  to: number
): Point {
  return {
    x: Math.max(0, ((scroll.x + anchor.x) * to) / from - anchor.x),
    y: Math.max(0, ((scroll.y + anchor.y) * to) / from - anchor.y)
  };
}

export function fitBounds(
  bounds: Bounds,
  viewport: { width: number; height: number }
) {
  const zoom = clampZoom(
    Math.min(
      1,
      Math.max(1, viewport.width - 100) / Math.max(1, bounds.width),
      Math.max(1, viewport.height - 100) / Math.max(1, bounds.height)
    )
  );
  return {
    zoom,
    scroll: {
      x: Math.max(0, (bounds.x + bounds.width / 2) * zoom - viewport.width / 2),
      y: Math.max(
        0,
        (bounds.y + bounds.height / 2) * zoom - viewport.height / 2
      )
    }
  };
}

export function segmentCrossesBox(a: Point, b: Point, box: Bounds): boolean {
  if (a.y === b.y)
    return (
      a.y > box.y &&
      a.y < box.y + box.height &&
      Math.max(a.x, b.x) > box.x &&
      Math.min(a.x, b.x) < box.x + box.width
    );
  return (
    a.x > box.x &&
    a.x < box.x + box.width &&
    Math.max(a.y, b.y) > box.y &&
    Math.min(a.y, b.y) < box.y + box.height
  );
}

/** Bounded orthogonal candidates favor clear corridors over crossing node bodies.
 * Different port pairs receive different lanes; crowded/manual overlapping graphs
 * remain inspectable through edge highlighting and the explicit connection list.
 */
export function routeConnection(
  a: Point,
  b: Point,
  boxes: Bounds[],
  lane: number,
  shared = false
): Point[] {
  const offset = 22 + (lane % 7) * 11;
  const exit = { x: shared ? Math.max(8, a.x - offset) : a.x + offset, y: a.y };
  const entry = { x: Math.max(8, b.x - offset), y: b.y };
  const candidates: Point[][] = [];
  if (!shared && b.x > a.x + 60) {
    const x = a.x + (b.x - a.x) * (0.25 + (lane % 7) / 12);
    candidates.push([a, { x, y: a.y }, { x, y: b.y }, b]);
  }
  if (shared) {
    const x = Math.max(8, Math.min(a.x, b.x) - offset);
    candidates.push([a, { x, y: a.y }, { x, y: b.y }, b]);
  }
  for (const y of new Set(
    boxes.flatMap((box) => [
      Math.max(8, box.y - offset),
      box.y + box.height + offset
    ])
  )) {
    candidates.push([a, exit, { x: exit.x, y }, { x: entry.x, y }, entry, b]);
  }
  if (!candidates.length) candidates.push([a, exit, { x: exit.x, y: b.y }, b]);
  const score = (points: Point[]) =>
    points
      .slice(1)
      .reduce(
        (total, p, i) =>
          total +
          Math.abs(p.x - points[i].x) +
          Math.abs(p.y - points[i].y) +
          boxes.filter((box) => segmentCrossesBox(points[i], p, box)).length *
            100000,
        points.length * 12
      );
  return candidates
    .reduce((best, path) => (score(path) < score(best) ? path : best))
    .filter(
      (p, i, points) => !i || p.x !== points[i - 1].x || p.y !== points[i - 1].y
    );
}

export const pathData = (points: Point[]) =>
  points.map((p, i) => `${i ? 'L' : 'M'} ${p.x} ${p.y}`).join(' ');
