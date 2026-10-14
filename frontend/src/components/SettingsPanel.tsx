import { BackendStatusDot } from './BackendStatusDot';
import { useLanguageStore } from '../stores/languageStore';
import { useThemeStore } from '../stores/themeStore';
import type { AccentId } from '../stores/themeStore';
import { Tabs } from '../ui/Tabs';
import type { TabItem } from '../ui/Tabs';
import { cx } from '../ui/cx';
import type { TranslationKeys } from '../types/i18n';

/** Kept in sync with package.json; shown in the About section. */
const APP_VERSION = '0.0.0';

const ACCENTS: {
  id: AccentId;
  labelKey: 'settingsAccentDefault' | 'settingsAccentTeal' | 'settingsAccentViolet';
  swatch: string;
}[] = [
  { id: 'blue', labelKey: 'settingsAccentDefault', swatch: '#2563eb' },
  { id: 'teal', labelKey: 'settingsAccentTeal', swatch: '#0d9488' },
  { id: 'violet', labelKey: 'settingsAccentViolet', swatch: '#7c3aed' }
];

function SectionLabel({ children }: { children: string }) {
  return <p className="text-meta font-medium text-muted">{children}</p>;
}

/**
 * Content of the Settings dialog. Exposes only preferences that are genuinely
 * implemented: appearance (theme store), language (i18n store), accent colour
 * and font size (token overrides on `<html>`), Classy Mode pointer atmosphere,
 * and real application/backend information. Everything persists through the
 * existing preference store - there is no second settings system.
 */
export function SettingsPanel({ t }: { t: TranslationKeys }) {
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  const accent = useThemeStore((s) => s.accent);
  const setAccent = useThemeStore((s) => s.setAccent);
  const fontSize = useThemeStore((s) => s.fontSize);
  const setFontSize = useThemeStore((s) => s.setFontSize);
  const wave = useThemeStore((s) => s.wave);
  const setWave = useThemeStore((s) => s.setWave);
  const language = useLanguageStore((s) => s.language);
  const setLanguage = useLanguageStore((s) => s.setLanguage);

  const themeItems: TabItem<'light' | 'dark'>[] = [
    { value: 'light', label: t.settingsThemeLight },
    { value: 'dark', label: t.settingsThemeDark }
  ];
  const languageItems: TabItem<'en' | 'zh'>[] = [
    { value: 'en', label: 'English' },
    { value: 'zh', label: '中文' }
  ];
  const fontSizeItems: TabItem<'small' | 'default' | 'large'>[] = [
    { value: 'small', label: t.settingsFontSmall },
    { value: 'default', label: t.settingsFontDefault },
    { value: 'large', label: t.settingsFontLarge }
  ];
  const waveItems: TabItem<'off' | 'on'>[] = [
    { value: 'off', label: t.settingsOff },
    { value: 'on', label: t.settingsOn }
  ];

  return (
    <div className="space-y-4">
      <section aria-label={t.settingsAppearance} className="space-y-1.5">
        <SectionLabel>{t.settingsAppearance}</SectionLabel>
        <Tabs items={themeItems} value={theme} onChange={setTheme} label={t.settingsAppearance} />
      </section>

      <section aria-label={t.settingsLanguage} className="space-y-1.5">
        <SectionLabel>{t.settingsLanguage}</SectionLabel>
        <Tabs items={languageItems} value={language} onChange={setLanguage} label={t.settingsLanguage} />
      </section>

      <section aria-label={t.settingsAccentColor} className="space-y-1.5">
        <SectionLabel>{t.settingsAccentColor}</SectionLabel>
        <div className="flex items-center gap-1.5">
          {ACCENTS.map((option) => {
            const selected = option.id === accent;
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={selected}
                title={t[option.labelKey]}
                aria-label={t[option.labelKey]}
                onClick={() => setAccent(option.id)}
                className={cx(
                  'flex size-7 items-center justify-center rounded-pill border transition-control focus-ring',
                  selected ? 'border-ink/40' : 'border-transparent hover:border-line-strong'
                )}
              >
                <span
                  aria-hidden="true"
                  className={cx(
                    'size-4 rounded-full border',
                    selected ? 'border-on-accent' : 'border-transparent'
                  )}
                  style={{ backgroundColor: option.swatch }}
                />
              </button>
            );
          })}
        </div>
      </section>

      <section aria-label={t.settingsFontSize} className="space-y-1.5">
        <SectionLabel>{t.settingsFontSize}</SectionLabel>
        <Tabs
          items={fontSizeItems}
          value={fontSize}
          onChange={setFontSize}
          label={t.settingsFontSize}
        />
      </section>

      <section aria-label={t.settingsMouseWave} className="space-y-1.5">
        <SectionLabel>{t.settingsMouseWave}</SectionLabel>
        <Tabs items={waveItems} value={wave ? 'on' : 'off'} onChange={(v) => setWave(v === 'on')} label={t.settingsMouseWave} />
      </section>

      <section aria-label={t.settingsAbout} className="space-y-1 border-t border-line pt-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-meta font-medium text-ink">{t.settingsAbout}</p>
          <BackendStatusDot t={t} />
        </div>
        <p className="text-meta text-muted">{t.appName}</p>
        <p className="text-meta text-subtle">
          {t.settingsVersion.replace('{{version}}', APP_VERSION)}
        </p>
      </section>
    </div>
  );
}
