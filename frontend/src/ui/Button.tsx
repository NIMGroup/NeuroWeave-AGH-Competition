import type { ButtonHTMLAttributes } from 'react';
import { Loader2 } from 'lucide-react';
import { cx } from './cx';

export type ButtonVariant = 'primary' | 'outline' | 'ghost' | 'danger' | 'danger-ghost';
export type ButtonSize = 'sm' | 'md' | 'lg';

const BASE = [
  'inline-flex shrink-0 select-none items-center justify-center gap-2',
  'rounded-control border font-medium transition-control',
  'focus-ring',
  'disabled:cursor-not-allowed disabled:opacity-50',
  '[&_svg]:size-4'
].join(' ');

/**
 * Filled variants are reserved for the single primary action of a view.
 * Everything else is outline or ghost - priority is expressed by weight and
 * position, never by introducing another hue.
 */
const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'border-transparent bg-accent-solid text-on-accent hover:bg-accent-solid-hover',
  outline: 'border-line bg-raised text-ink hover:bg-hover',
  ghost: 'border-transparent text-muted hover:bg-hover hover:text-ink',
  danger: 'border-transparent bg-danger-solid text-on-danger hover:bg-danger-solid-hover',
  'danger-ghost': 'border-transparent text-danger hover:bg-danger-quiet'
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 gap-1.5 px-2.5 text-micro',
  md: 'h-8 px-3 text-xs',
  lg: 'h-9 px-4 text-xs'
};

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Keeps the label in place and shows a spinner; also disables the button. */
  loading?: boolean;
  fullWidth?: boolean;
};

export function Button({
  variant = 'outline',
  size = 'md',
  loading = false,
  fullWidth = false,
  type = 'button',
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(BASE, VARIANTS[variant], SIZES[size], fullWidth && 'w-full', className)}
    >
      {loading && <Loader2 className="animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}
