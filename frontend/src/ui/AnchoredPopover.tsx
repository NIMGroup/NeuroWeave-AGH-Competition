import { useLayoutEffect, useRef } from 'react';
import type { HTMLAttributes, RefObject } from 'react';
import { createPortal } from 'react-dom';
import { positionPopup } from './popupPosition';
import type { PopupAlign, PopupSide } from './popupPosition';

type AnchoredPopoverProps = HTMLAttributes<HTMLDivElement> & {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  side?: PopupSide;
  align?: PopupAlign;
  /** Informational labels must not take focus away from their trigger. */
  interactive?: boolean;
};

/** A viewport-positioned surface, outside overflow/transform ancestors. */
export function AnchoredPopover({
  open, anchorRef, onClose, side = 'bottom', align = 'end', interactive = true,
  style, children, ...props
}: AnchoredPopoverProps) {
  const popupRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef(onClose);
  const updateRef = useRef<(() => void) | null>(null);
  useLayoutEffect(() => { closeRef.current = onClose; });
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const popup = popupRef.current;
    if (!open || !anchor || !popup) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const visual = window.visualViewport;
      const viewport = { left: visual?.offsetLeft ?? 0, top: visual?.offsetTop ?? 0,
        width: visual?.width ?? window.innerWidth, height: visual?.height ?? window.innerHeight };
      const rect = anchor.getBoundingClientRect();
      let visible = { left: viewport.left, top: viewport.top,
        right: viewport.left + viewport.width, bottom: viewport.top + viewport.height };
      // A menu should not float over an unrelated row after its trigger scrolls out.
      for (let parent = anchor.parentElement; parent; parent = parent.parentElement) {
        const css = getComputedStyle(parent);
        const bounds = parent.getBoundingClientRect();
        if (/(auto|scroll|hidden|clip)/.test(css.overflowX))
          visible = { ...visible, left: Math.max(visible.left, bounds.left), right: Math.min(visible.right, bounds.right) };
        if (/(auto|scroll|hidden|clip)/.test(css.overflowY))
          visible = { ...visible, top: Math.max(visible.top, bounds.top), bottom: Math.min(visible.bottom, bounds.bottom) };
      }
      if (!anchor.isConnected || !rect.width || !rect.height || rect.bottom <= visible.top ||
        rect.top >= visible.bottom || rect.right <= visible.left || rect.left >= visible.right) {
        closeRef.current();
        return;
      }
      // Measure unconstrained content first, so resizing can grow a formerly capped menu.
      popup.style.maxWidth = Math.max(0, viewport.width - 16) + 'px';
      popup.style.maxHeight = Math.max(0, viewport.height - 16) + 'px';
      const size = popup.getBoundingClientRect();
      const position = positionPopup(rect, size, viewport, side, align);
      Object.assign(popup.style, { left: position.left + 'px', top: position.top + 'px',
        maxWidth: position.maxWidth + 'px', maxHeight: position.maxHeight + 'px', visibility: 'visible' });
      popup.dataset.side = position.side;
    };
    const schedule = (event?: Event) => {
      if (event?.target instanceof Node && popup.contains(event.target)) return;
      if (!frame) frame = requestAnimationFrame(update);
    };
    const onPointer = (event: PointerEvent) => {
      if (event.target instanceof Node && (popup.contains(event.target) || anchor.contains(event.target))) return;
      closeRef.current();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (interactive) anchor.focus({ preventScroll: true });
        closeRef.current();
      } else if (event.key === 'Tab' && popup.getAttribute('role') === 'menu') {
        // Continue the natural tab order at the trigger, not at the body's portal.
        anchor.focus({ preventScroll: true });
        closeRef.current();
      }
    };
    const onFocus = (event: FocusEvent) => {
      if (interactive && event.target instanceof Node && !popup.contains(event.target) && !anchor.contains(event.target))
        closeRef.current();
    };
    updateRef.current = update;
    update();
    if (interactive)
      popup.querySelector<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex="0"]')?.focus({ preventScroll: true });
    const observer = new ResizeObserver(() => schedule());
    observer.observe(anchor);
    observer.observe(popup);
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('scroll', schedule);
    document.addEventListener('pointerdown', onPointer, true);
    document.addEventListener('keydown', onKey);
    document.addEventListener('focusin', onFocus);
    return () => {
      updateRef.current = null;
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('scroll', schedule);
      document.removeEventListener('pointerdown', onPointer, true);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('focusin', onFocus);
      if (interactive && anchor.isConnected && popup.contains(document.activeElement))
        anchor.focus({ preventScroll: true });
    };
  }, [open, anchorRef, side, align, interactive]);
  // Content and list layout can change without a window resize.
  useLayoutEffect(() => { updateRef.current?.(); });

  if (!open || typeof document === 'undefined') return null;
  return createPortal(
    <div {...props} ref={popupRef} style={{ ...style, position: 'fixed', margin: 0,
      zIndex: 80, overflowY: 'auto', overscrollBehavior: 'contain' }}>
      {children}
    </div>, document.body
  );
}
