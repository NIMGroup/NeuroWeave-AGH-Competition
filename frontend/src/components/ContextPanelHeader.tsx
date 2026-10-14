import { X } from 'lucide-react';
import { IconButton } from '../ui/IconButton';
import type { TranslationKeys } from '../types/i18n';

type ContextPanelHeaderProps = {
  title: string;
  subtitle?: string;
  t: TranslationKeys;
  onClose: () => void;
};

/**
 * Header of the domain context panel. Identifies the active domain so the
 * panel reads as part of the shell (Rail -> Context -> Surface), not as a
 * generic sidebar.
 */
export function ContextPanelHeader({ title, subtitle, t, onClose }: ContextPanelHeaderProps) {
  return (
    <div className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-line px-4">
      <div className="min-w-0">
        <h2 className="truncate text-title">{title}</h2>
        {subtitle && <p className="mt-0.5 truncate text-meta text-muted">{subtitle}</p>}
      </div>

      <IconButton label={t.closeSidebar} icon={<X />} onClick={onClose} />
    </div>
  );
}
