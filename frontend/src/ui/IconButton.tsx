import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react';
import { cx } from './cx';

export type IconButtonSize = 'sm' | 'md' | 'lg';
export type IconButtonVariant = 'ghost' | 'outline' | 'solid' | 'danger-ghost';

const BASE = [
  'inline-flex shrink-0 items-center justify-center rounded-control border transition-control',
  'focus-ring',
  'disabled:cursor-not-allowed disabled:opacity-50'
].join(' ');

const SIZES: Record<IconButtonSize, string> = {
  sm: 'size-6 [&_svg]:size-3.5',
  md: 'size-8 [&_svg]:size-4',
  lg: 'size-9 [&_svg]:size-[18px]'
};

const VARIANTS: Record<IconButtonVariant, string> = {
  ghost: 'border-transparent text-muted hover:bg-hover hover:text-ink',
  outline: 'border-line bg-raised text-muted hover:bg-hover hover:text-ink',
  solid: 'border-transparent bg-accent-solid text-on-accent hover:bg-accent-solid-hover',
  'danger-ghost': 'border-transparent text-danger hover:bg-danger-quiet'
};

type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label' | 'children'> & {
  /** Required accessible name - icon-only controls must never be unlabelled. */
  label: string;
  ref?: Ref<HTMLButtonElement>;
  icon: ReactNode;
  size?: IconButtonSize;
  variant?: IconButtonVariant;
  /** Set for toggle buttons so the pressed state is announced. */
  pressed?: boolean;
};

export function IconButton({
  label,
  icon,
  size = 'md',
  variant = 'ghost',
  pressed,
  type = 'button',
  className,
  ...rest
}: IconButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-label={label}
      aria-pressed={pressed}
      title={rest.title ?? label}
      className={cx(BASE, SIZES[size], VARIANTS[variant], className)}
    >
      {icon}
    </button>
  );
}
