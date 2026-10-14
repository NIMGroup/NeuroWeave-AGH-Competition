import { Globe } from 'lucide-react';
import { useRef, useState } from 'react';
import { AnchoredPopover } from '../ui/AnchoredPopover';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { getTranslations } from '../lib/i18n';
import { useLanguageStore } from '../stores/languageStore';

export function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const { language, setLanguage } = useLanguageStore();
  const t = getTranslations(language);
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);

  if (compact) return <>
    <IconButton ref={anchor} label={t.language} title={`${t.language} · ${language === 'zh' ? '中文' : 'English'}`}
      icon={<Globe />} size="lg" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)} />
    <AnchoredPopover open={open} anchorRef={anchor} onClose={() => setOpen(false)} side="right" role="dialog"
      aria-label={t.language} className="w-36 space-y-1 rounded-card border border-line bg-raised p-2 text-ink shadow-panel">
      {(['zh', 'en'] as const).map(value => <Button key={value} variant={language === value ? 'primary' : 'ghost'}
        fullWidth aria-pressed={language === value} onClick={() => { setLanguage(value); setOpen(false); }}>
        {value === 'zh' ? '中文' : 'English'}
      </Button>)}
    </AnchoredPopover>
  </>;

  return (
    <div className="focus-ring flex h-9 shrink-0 items-center gap-1.5 rounded-control border border-line bg-raised pl-2.5 transition-control">
      <Globe className="size-3.5 shrink-0 text-subtle" aria-hidden="true" />

      <select
        value={language}
        onChange={(event) => setLanguage(event.target.value as 'en' | 'zh')}
        aria-label={t.language}
        className="h-full cursor-pointer rounded-control bg-transparent pr-2 text-xs font-medium text-ink outline-none"
      >
        <option value="en">English</option>
        <option value="zh">中文</option>
      </select>
    </div>
  );
}
