import type { KeyboardEvent, ReactNode, RefObject } from 'react';
import { AnchoredPopover } from './AnchoredPopover';
import { cx } from './cx';

type MenuProps = {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  id?: string;
  onClose: () => void;
  /** Accessible name for the popup. */
  label: string;
  align?: 'start' | 'end';
  className?: string;
  children: ReactNode;
};

/**
 * Menus share collision-aware viewport positioning with confirmation bubbles.
 * The portal prevents a scrolling history list from clipping menu actions.
 */
export function Menu({ open, onClose, anchorRef, id, label, align = 'end', className, children }: MenuProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const items = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []
    );
    if (!items.length) return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const offset = event.key === 'ArrowDown' ? 1 : -1;
    items[(index + offset + items.length) % items.length]?.focus();
  };

  return (
    <AnchoredPopover
      id={id}
      open={open}
      anchorRef={anchorRef}
      onClose={onClose}
      align={align}
      role="menu"
      aria-label={label}
      onKeyDown={handleKeyDown}
      className={cx(
        'w-40 rounded-card border border-line bg-raised p-1 text-ink shadow-panel',
        className
      )}
    >
      {children}
    </AnchoredPopover>
  );
}

type MenuItemProps = {
  onSelect: () => void;
  icon?: ReactNode;
  tone?: 'default' | 'danger';
  disabled?: boolean;
  children: ReactNode;
};

export function MenuItem({
  onSelect,
  icon,
  tone = 'default',
  disabled = false,
  children
}: MenuItemProps) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onSelect}
      className={cx(
        'flex w-full items-center gap-2 rounded-chip px-2.5 py-2 text-left text-xs transition-control',
        'focus-ring-inset',
        'disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-4',
        tone === 'danger' ? 'text-danger hover:bg-danger-quiet' : 'text-ink hover:bg-hover'
      )}
    >
      {icon}
      {children}
    </button>
  );
}
