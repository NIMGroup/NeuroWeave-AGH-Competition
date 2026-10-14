import { useRef, useState } from 'react';
import type { CSSProperties, PointerEvent } from 'react';

const EDGES = [
  ['n', 'inset-x-2 top-0 h-2 cursor-ns-resize'],
  ['s', 'inset-x-2 bottom-0 h-2 cursor-ns-resize'],
  ['e', 'inset-y-2 right-0 w-2 cursor-ew-resize'],
  ['w', 'inset-y-2 left-0 w-2 cursor-ew-resize'],
  ['ne', 'right-0 top-0 size-3 cursor-nesw-resize'],
  ['nw', 'left-0 top-0 size-3 cursor-nwse-resize'],
  ['se', 'right-0 bottom-0 size-3 cursor-nwse-resize'],
  ['sw', 'left-0 bottom-0 size-3 cursor-nesw-resize']
] as const;

/** Centered modal sizing is independent of async content and directory depth. */
export function useResizablePanel(label: string) {
  const panelRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{x:number;y:number;width:number;height:number;edge:string} | null>(null);
  const [size, setSize] = useState({width:1024, height:720});
  const bounded = (width:number, height:number) => ({
    width: Math.min(Math.max(0, window.innerWidth - 32), Math.max(520, width)),
    height: Math.min(Math.max(0, window.innerHeight - 32), Math.max(440, height))
  });
  const finish = (event:PointerEvent<HTMLButtonElement>) => {
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const style:CSSProperties = {
    width:`min(${size.width}px, calc(100vw - 32px))`,
    height:`min(${size.height}px, calc(100dvh - 32px))`,
    containerType:'inline-size'
  };
  const handles = EDGES.map(([edge, position]) => <button key={edge} type="button"
    aria-label={`${label} (${edge.toUpperCase()})`} title={label}
    className={`absolute z-20 touch-none rounded-sm p-0 hover:bg-accent-quiet focus-visible:bg-accent-quiet focus-visible:outline-2 ${position}`}
    onPointerDown={event => {
      if (event.button !== 0 || !panelRef.current) return;
      event.preventDefault();event.stopPropagation();
      const rect = panelRef.current.getBoundingClientRect();
      drag.current = {x:event.clientX,y:event.clientY,width:rect.width,height:rect.height,edge};
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={event => {
      const start = drag.current;
      if (!start || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
      // The panel remains centered: moving one edge changes both half-extents.
      const dx = 2 * (event.clientX-start.x), dy = 2 * (event.clientY-start.y);
      setSize(bounded(start.width+(edge.includes('e')?dx:edge.includes('w')?-dx:0),
        start.height+(edge.includes('s')?dy:edge.includes('n')?-dy:0)));
    }}
    onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={()=>{drag.current=null;}}
    onKeyDown={event => {
      const horizontal=edge.includes('e') || edge.includes('w');
      const vertical=edge.includes('n') || edge.includes('s');
      const delta = event.key==='ArrowRight' && horizontal ? [32,0] : event.key==='ArrowLeft' && horizontal ? [-32,0]
        : event.key==='ArrowDown' && vertical ? [0,32] : event.key==='ArrowUp' && vertical ? [0,-32] : null;
      if (!delta) return;
      event.preventDefault();
      const rect=panelRef.current!.getBoundingClientRect();
      setSize(bounded(rect.width+delta[0],rect.height+delta[1]));
    }}/>
  );
  return {panelRef,style,handles};
}
