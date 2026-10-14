import type { ReactNode } from 'react';
import { cx } from './cx';

export type BadgeTone =
  'neutral' | 'accent' | 'running' | 'success' | 'warning' | 'danger' | 'artifact' | 'upstream';

export type BadgeSize = 'sm' | 'md';

const TONES: Record<BadgeTone, string> = {
  neutral: 'border-line bg-well text-muted',
  accent: 'border-accent-line bg-accent-quiet text-accent',
  running: 'border-running-line bg-running-quiet text-running',
  success: 'border-success-line bg-success-quiet text-success',
  warning: 'border-warning-line bg-warning-quiet text-warning',
  danger: 'border-danger-line bg-danger-quiet text-danger',
  artifact: 'border-artifact-line bg-artifact-quiet text-artifact',
  upstream: 'border-upstream-line bg-upstream-quiet text-upstream'
};

/**
 * How the severity wheel is scoped (mirror of the design tokens):
 * accent/running -> in-progress work success -> finished work
 * warning -> attention required danger -> failure / destructive
 * artifact -> produced outputs upstream-> auto-generated steps
 */
const SIZES: Record<BadgeSize, string> = {
  sm: 'h-5 gap-1 px-1.5 text-micro',
  md: 'h-6 gap-1.5 px-2 text-xs'
};

type BadgeProps = {
  tone?: BadgeTone;
  size?: BadgeSize;
  dot?: boolean;
  title?: string;
  className?: string;
  children: ReactNode;
};

/** Small status/label chip. One implementation for every status pill in the app. */
export function Badge({
  tone = 'neutral',
  size = 'sm',
  dot = false,
  title,
  className,
  children
}: BadgeProps) {
  return (
    <span
      title={title}
      className={cx(
        'inline-flex shrink-0 items-center whitespace-nowrap rounded-chip border font-medium',
        SIZES[size],
        TONES[tone],
        className
      )}
    >
      {dot && <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-current" />}
      {children}
    </span>
  );
}
