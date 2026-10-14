export type PopupSide = 'top' | 'bottom' | 'left' | 'right';
export type PopupAlign = 'start' | 'center' | 'end';
type Rect = { left: number; top: number; right: number; bottom: number };
type Viewport = { left: number; top: number; width: number; height: number };

/** Flip on the main axis and shift on the cross axis, within the visible viewport. */
export function positionPopup(
  anchor: Rect,
  size: { width: number; height: number },
  viewport: Viewport,
  preferred: PopupSide = 'bottom',
  align: PopupAlign = 'end',
  gap = 6,
  margin = 8
) {
  const left = viewport.left + margin;
  const top = viewport.top + margin;
  const right = Math.max(left, viewport.left + viewport.width - margin);
  const bottom = Math.max(top, viewport.top + viewport.height - margin);
  const available = {
    top: Math.max(0, anchor.top - gap - top),
    bottom: Math.max(0, bottom - anchor.bottom - gap),
    left: Math.max(0, anchor.left - gap - left),
    right: Math.max(0, right - anchor.right - gap)
  };
  const opposite: Record<PopupSide, PopupSide> = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' };
  const vertical = preferred === 'top' || preferred === 'bottom';
  const required = vertical ? size.height : size.width;
  const other = opposite[preferred];
  const side = available[preferred] >= required || available[preferred] >= available[other] ? preferred : other;
  const maxWidth = Math.min(right - left, vertical ? right - left : available[side]);
  const maxHeight = Math.min(bottom - top, vertical ? available[side] : bottom - top);
  const width = Math.min(size.width, maxWidth);
  const height = Math.min(size.height, maxHeight);
  const cross = (start: number, end: number, length: number) =>
    align === 'start' ? start : align === 'end' ? end - length : (start + end - length) / 2;
  const x = vertical ? cross(anchor.left, anchor.right, width)
    : side === 'right' ? anchor.right + gap : anchor.left - gap - width;
  const y = !vertical ? cross(anchor.top, anchor.bottom, height)
    : side === 'bottom' ? anchor.bottom + gap : anchor.top - gap - height;
  return {
    side, maxWidth, maxHeight,
    left: Math.max(left, Math.min(x, right - width)),
    top: Math.max(top, Math.min(y, bottom - height))
  };
}
