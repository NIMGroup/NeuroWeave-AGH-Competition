import { useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AnchoredPopover } from './AnchoredPopover';
import { cx } from './cx';

type TooltipProps = {
  /** Visible accessible name is provided by the wrapped control; tooltip mirrors it. */
  label: string;
  children: ReactNode;
  className?: string;
};

/** Rail labels share viewport collision handling without taking focus. */
export function Tooltip({ label, children, className }: TooltipProps) {
  const anchorRef = useRef<HTMLSpanElement | null>(null);
  const [open, setOpen] = useState(false);
  return (
    <span ref={anchorRef} className={cx('relative inline-flex', className)}
      onPointerEnter={() => setOpen(true)}
      onPointerLeave={() => setOpen(false)}
      onPointerDown={() => setOpen(false)}
      onFocusCapture={() => setOpen(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}>
      {children}
      <AnchoredPopover open={open} anchorRef={anchorRef} onClose={() => setOpen(false)}
        side="right" align="center" interactive={false} aria-hidden="true"
        className="pointer-events-none w-max max-w-64 break-words rounded-control border border-line bg-raised px-2 py-1 text-xs font-medium text-ink shadow-panel">
        {label}
      </AnchoredPopover>
    </span>
  );
}
