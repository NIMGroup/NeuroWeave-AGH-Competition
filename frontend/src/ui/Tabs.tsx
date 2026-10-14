import { cx } from './cx';

export type TabItem<Value extends string> = {
  value: Value;
  label: string;
  /** Optional counter, e.g. selected item or unfiltered record count. */
  badge?: number;
};

export type TabsVariant = 'segmented' | 'chip';

type TabsProps<Value extends string> = {
  items: readonly TabItem<Value>[];
  value: Value;
  onChange: (value: Value) => void;
  /** Accessible group name - required, the control is never unlabelled. */
  label: string;
  variant?: TabsVariant;
  /** Disables every item, e.g. while a blocking request is in flight. */
  disabled?: boolean;
  className?: string;
};

const CONTAINER: Record<TabsVariant, string> = {
  segmented: 'flex gap-1 rounded-control border border-line bg-well p-1',
  chip: 'flex flex-wrap items-center gap-1'
};

const ITEM_BASE =
  'flex border font-medium transition-control focus-ring disabled:cursor-not-allowed disabled:opacity-50';

const ITEM_BY_VARIANT: Record<TabsVariant, { shape: string; active: string; idle: string }> = {
  segmented: {
    shape: 'h-7 flex-1 items-center justify-center gap-1.5 rounded-chip px-2.5 text-micro',
    active: 'border-line bg-raised text-ink',
    idle: 'border-transparent text-muted hover:bg-hover hover:text-ink'
  },
  chip: {
    shape: 'h-6 items-center gap-1.5 rounded-pill px-2.5 text-micro',
    active: 'border-transparent bg-solid text-on-solid',
    idle: 'border-transparent text-muted hover:bg-hover hover:text-ink'
  }
};

/**
 * Single segmented-control implementation. Replaces the several ad-hoc
 * "pill strip" styles that had accumulated per feature.
 *
 * Uses the toggle-group pattern (`role="group"` + `aria-pressed`) rather than
 * `role="tablist"`, because most callers switch a filter/view whose panel is
 * owned by the caller, and tabs without tabpanels would be invalid ARIA.
 */
export function Tabs<Value extends string>({
  items,
  value,
  onChange,
  label,
  variant = 'segmented',
  disabled = false,
  className
}: TabsProps<Value>) {
  const styles = ITEM_BY_VARIANT[variant];
  return (
    <div role="group" aria-label={label} className={cx(CONTAINER[variant], className)}>
      {items.map((item) => {
        const selected = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            aria-pressed={selected}
            disabled={disabled}
            onClick={() => onChange(item.value)}
            className={cx(ITEM_BASE, styles.shape, selected ? styles.active : styles.idle)}
          >
            <span className="truncate">{item.label}</span>
            {item.badge !== undefined && item.badge > 0 && (
              <span className="shrink-0 rounded-pill border border-line bg-well px-1.5 text-micro text-muted tabular-nums">
                {item.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
