import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { IconButton } from './IconButton';
import { cx } from './cx';

export type DialogSize = 'sm' | 'md' | 'lg' | 'xl';

const SIZES: Record<DialogSize, string> = {
  sm: 'max-w-md',
  md: 'max-w-xl',
  lg: 'max-w-2xl',
  xl: 'max-w-5xl'
};

type DialogProps = {
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  /** Accessible name for the close affordance. */
  closeLabel: string;
  size?: DialogSize;
  footer?: ReactNode;
  /** Rendered without the standard title/description header, e.g. a picker shell. */
  header?: ReactNode;
  className?: string;
  children: ReactNode;
  placement?: 'center' | 'right' | 'fullscreen';
  resizeLabel?: string;
  contentClassName?: string;
};

/**
 * The single modal implementation.
 *
 * Built on the native `<dialog>` element so we inherit a real focus trap,
 * `Escape` handling and correct focus restoration without shipping a library.
 * The visual reset lives in `.dialog-root` / `.dialog-panel` (index.css).
 */
export function Dialog({
  onClose,
  title,
  description,
  closeLabel,
  size = 'md',
  footer,
  header,
  className,
  children,
  placement = 'center',
  resizeLabel,
  contentClassName
}: DialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const titleId = useId();
  const [width, setWidth] = useState(600);

  useEffect(() => {
    const element = dialogRef.current;
    if (!element || element.open) return;
    element.showModal();
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="dialog-root"
      data-placement={placement}
      // Keep React state in sync when the browser closes the dialog itself.
      onCancel={(event) => {
        event.preventDefault();
        // Escape closes the topmost detail card, not its enclosing editor.
        event.stopPropagation();
        onClose();
      }}
      // The dialog element only receives clicks on the backdrop/gutter, since
      // the visible surface is a child.
      onClick={(event) => {
        if (event.target === dialogRef.current) onClose();
      }}
    >
      <div
        className={cx(
          'dialog-panel flex w-full flex-col overflow-hidden rounded-panel border border-line bg-raised shadow-panel',
          placement === 'right' ? 'relative h-full !rounded-none' : SIZES[size],
          className
        )}
        style={placement === 'right' ? {width, maxWidth:'100vw'} : undefined}
      >
        {placement === 'right' && <button type="button" aria-label={resizeLabel} title={resizeLabel}
          className="absolute inset-y-0 left-0 z-10 w-1 cursor-col-resize touch-none hover:bg-accent-quiet focus:bg-accent-quiet"
          onKeyDown={event => {
            if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
            event.preventDefault();
            setWidth(value => Math.min(window.innerWidth, Math.max(320,value+(event.key==='ArrowLeft'?32:-32))));
          }}
          onPointerDown={event=>event.currentTarget.setPointerCapture(event.pointerId)}
          onPointerUp={event=>event.currentTarget.releasePointerCapture(event.pointerId)}
          onPointerMove={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId)) setWidth(Math.min(window.innerWidth,Math.max(320,window.innerWidth-event.clientX)));}}/>}
        {header ?? (
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0">
              <h2 id={titleId} className="break-words text-title">
                {title}
              </h2>
              {description && <p className="mt-0.5 break-words text-meta text-muted">{description}</p>}
            </div>
            <IconButton label={closeLabel} icon={<X />} onClick={onClose} />
          </div>
        )}
        <div className={contentClassName ?? "min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 text-body"}>
          {children}
        </div>
        {footer && (
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-line px-4 py-3">
            {footer}
          </div>
        )}
      </div>
    </dialog>
  );
}
