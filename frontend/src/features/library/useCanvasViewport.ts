import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { clampZoom, fitBounds, zoomAnchor } from './canvasGeometry';
import type { Bounds } from './canvasGeometry';
import type { Point } from './pipelineGraph';

/** View changes are local UI state and never edit the saved pipeline graph. */
export function useCanvasViewport() {
  const viewport = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const scale = useRef(1);
  const scrollAfterRender = useRef<Point | null>(null);
  const apply = (next: number, scroll: Point) => {
    scrollAfterRender.current = scroll;
    scale.current = next;
    setZoom(next);
    if (next === zoom) {
      viewport.current?.scrollTo(scroll.x, scroll.y);
      scrollAfterRender.current = null;
    }
  };
  useLayoutEffect(() => {
    const scroll = scrollAfterRender.current;
    if (scroll) viewport.current?.scrollTo(scroll.x, scroll.y);
    scrollAfterRender.current = null;
  }, [zoom]);
  const changeZoom = (next: number, anchor?: Point) => {
    const el = viewport.current;
    if (!el) return;
    next = clampZoom(next);
    apply(
      next,
      zoomAnchor(
        { x: el.scrollLeft, y: el.scrollTop },
        anchor ?? { x: el.clientWidth / 2, y: el.clientHeight / 2 },
        scale.current,
        next
      )
    );
  };
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    // A non-passive native handler intercepts pinch/Ctrl-wheel only in the canvas.
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      const next = clampZoom(scale.current * Math.exp(-event.deltaY * 0.002));
      scrollAfterRender.current = zoomAnchor(
        { x: el.scrollLeft, y: el.scrollTop },
        { x: event.clientX - rect.left, y: event.clientY - rect.top },
        scale.current,
        next
      );
      scale.current = next;
      setZoom(next);
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, []);
  const fit = (bounds: Bounds) => {
    const el = viewport.current;
    if (!el) return;
    const view = fitBounds(bounds, {
      width: el.clientWidth,
      height: el.clientHeight
    });
    apply(view.zoom, view.scroll);
  };
  const point = (x: number, y: number): Point => {
    const el = viewport.current!;
    const rect = el.getBoundingClientRect();
    return {
      x: (x - rect.left + el.scrollLeft) / scale.current,
      y: (y - rect.top + el.scrollTop) / scale.current
    };
  };
  return {
    viewport,
    zoom,
    changeZoom,
    fit,
    point,
    reset: () => apply(1, { x: 0, y: 0 })
  };
}
