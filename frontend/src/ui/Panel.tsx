import type { ReactNode } from 'react';
import { cx } from './cx';

export type PanelTone = 'raised' | 'panel' | 'well';

const TONES: Record<PanelTone, string> = {
  raised: 'border-line bg-raised',
  panel: 'border-line bg-panel',
  well: 'border-line bg-well'
};

/**
 * A bounded surface, not a floating card.
 *
 * Surface hierarchy rule for the whole app:
 * canvas (app) < panel (persistent chrome) < raised (cards/dialogs)
 * `well` is the recessed variant for inputs, code and log output.
 * Only genuine content groupings get a `raised` panel - decorative card
 * wrapping is what makes an interface look busy.
 */
export function Panel({
  tone = 'raised',
  className,
  children
}: {
  tone?: PanelTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cx('min-w-0 rounded-card border', TONES[tone], className)}>
      {children}
    </section>
  );
}

export function PanelHeader({
  title,
  description,
  actions,
  id,
  className
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  id?: string;
  className?: string;
}) {
  return (
    <div
      className={cx(
        'flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3',
        className
      )}
    >
      <div className="min-w-0">
        <h2 id={id} className="text-title">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-meta text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function PanelBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx('min-w-0 px-4 py-3', className)}>{children}</div>;
}

export function PanelFooter({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cx(
        'flex flex-wrap items-center justify-end gap-2 border-t border-line px-4 py-3',
        className
      )}
    >
      {children}
    </div>
  );
}
