import { useEffect, useRef, useState } from 'react';
import { Database, ListChecks, FolderOpen, HardDrive, MessageSquare, Settings, Sparkles, Workflow } from 'lucide-react';
import type { ReactNode } from 'react';
import { SettingsDialog } from './SettingsDialog';
import { LanguageSelector } from './LanguageSelector';
import { ThemeSwitch } from './ThemeSwitch';
import { Tooltip } from '../ui/Tooltip';
import { cx } from '../ui/cx';
import type { TranslationKeys } from '../types/i18n';

export type RailDomain = 'conversations' | 'skills' | 'datasets' | 'workspace' | 'storage' | 'workflows' | 'pipelines';

type RailItem = { value: RailDomain; label: string; icon: ReactNode };

type AppRailProps = {
  t: TranslationKeys;
  domain: RailDomain;
  datasetCount: number;
  activity?: ReactNode;
  darkMode: boolean;
  onToggleTheme: () => void;
  onDomainChange: (domain: RailDomain) => void;
};

export function AppRail({ t, domain, datasetCount, activity, darkMode, onToggleTheme, onDomainChange }: AppRailProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') setSettingsOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [settingsOpen]);

  const primary: RailItem[] = [
    { value: 'workflows', label: t.workflowsAndRuns, icon: <ListChecks /> },
    { value: 'conversations', label: t.conversations, icon: <MessageSquare /> },
    { value: 'skills', label: t.skills, icon: <Sparkles /> },
    { value: 'pipelines', label: 'Pipeline', icon: <Workflow /> }
  ];
  const resources: RailItem[] = [
    { value: 'datasets', label: t.datasetSelectLocal, icon: <Database /> },
    { value: 'workspace', label: t.workspaceTitle, icon: <FolderOpen /> },
    { value: 'storage', label: t.storageOpen, icon: <HardDrive /> }
  ];
  const btn = (active: boolean) =>
    cx(
      'relative flex size-9 items-center justify-center rounded-control transition-control focus-ring [&_svg]:size-4',
      active ? 'bg-selected text-accent' : 'text-muted hover:bg-hover hover:text-ink'
    );

  return (
    <nav aria-label={t.sidebarSections} className="flex w-14 shrink-0 flex-col items-center gap-1 overflow-visible border-r border-line bg-panel py-3">
      <div aria-hidden="true" className="mb-3 flex size-8 items-center justify-center rounded-control bg-accent-solid text-on-accent [&_svg]:size-4">
        <Sparkles />
      </div>
      {primary.map((item) => {
        const active = item.value === domain;
        return (
          <Tooltip key={item.value} label={item.label}>
            <button type="button" aria-current={active ? 'page' : undefined} aria-label={item.label} onClick={() => onDomainChange(item.value)} className={btn(active)}>
              {active && <span aria-hidden="true" className="absolute -left-3 h-5 w-0.5 rounded-pill bg-accent-solid" />}
              {item.icon}
            </button>
          </Tooltip>
        );
      })}
      <div aria-hidden="true" className="my-2 h-px w-6 bg-line" />
      {resources.map((item) => {
        const active = item.value === domain;
        return (
          <Tooltip key={item.value} label={item.label}>
            <button
              type="button"
              aria-current={active ? 'page' : undefined}
              aria-label={item.label}
              onClick={() => onDomainChange(item.value)}
              className={btn(active)}
            >
              {active && <span aria-hidden="true" className="absolute -left-3 h-5 w-0.5 rounded-pill bg-accent-solid" />}
              {item.icon}
              {item.value === 'datasets' && datasetCount > 0 && (
                <span aria-hidden="true" className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-pill bg-accent-solid px-0.5 text-micro font-semibold text-on-accent">
                  {datasetCount}
                </span>
              )}
            </button>
          </Tooltip>
        );
      })}
      <div className="flex-1" />
      {activity}
      <ThemeSwitch t={t} darkMode={darkMode} onToggle={onToggleTheme} />
      <LanguageSelector compact />
      <Tooltip label={t.settingsNav}>
        <button
          ref={settingsButtonRef}
          type="button"
          aria-expanded={settingsOpen}
          aria-haspopup="dialog"
          aria-label={t.settingsNav}
          onClick={() => setSettingsOpen((o) => !o)}
          className={btn(settingsOpen)}
        >
          <Settings />
        </button>
      </Tooltip>
      {settingsOpen && <SettingsDialog t={t} onClose={() => setSettingsOpen(false)} returnFocusRef={settingsButtonRef} />}
      <span className="sr-only">{t.appName}</span>
    </nav>
  );
}
