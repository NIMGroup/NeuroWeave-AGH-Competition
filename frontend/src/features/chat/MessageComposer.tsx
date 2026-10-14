import { Send, X } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { useLanguageStore } from '../../stores/languageStore';
import { Badge } from '../../ui/Badge';
import { Button } from '../../ui/Button';
import { IconButton } from '../../ui/IconButton';
import type { TranslationKeys } from '../../types/i18n';

type MessageComposerProps = {
  t: TranslationKeys;
  value: string;
  sending: boolean;
  processing?: boolean;
  sendControl?: ReactNode;
  infoControls?: ReactNode;
  selectedSkills: { id: string; label: string }[];
  onRemoveSkill: (id: string) => void;
  onViewSkill: (id: string) => void;
  onChange: (value: string) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onClearSkills: () => void;
};

export function MessageComposer({
  t,
  value,
  sending,
  processing = false,
  sendControl,
  infoControls,
  selectedSkills,
  onRemoveSkill,
  onViewSkill,
  onChange,
  onKeyDown,
  onSend,
  onClearSkills
}: MessageComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  useLayoutEffect(() => {
    const field = textareaRef.current;
    if (field) {
      field.style.height = 'auto';
      field.style.height = Math.min(160, field.scrollHeight) + 'px';
    }
  }, [value]);

  return (
    <div className="shrink-0 border-t border-line bg-canvas px-4 pb-3 pt-3 sm:px-8">
      <div className="mx-auto w-full max-w-4xl">
        {infoControls && <div className="mb-2 flex items-center justify-end gap-1">{infoControls}</div>}
        {selectedSkills.length > 0 && (
          <div className="mb-2 flex flex-wrap items-start gap-2 text-meta text-muted">
            <span className="shrink-0">
              {selectedSkills.length > 1
                ? t.usingSkills.replace('{{count}}', String(selectedSkills.length))
                : t.usingSkill}
            </span>

            <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
              {selectedSkills.map(skill => <Badge key={skill.id} tone="neutral" className="max-w-full" title={skill.label}>
                <button type="button" className="min-w-0 truncate hover:text-accent" onClick={() => onViewSkill(skill.id)}
                  aria-label={`${t.skillViewDetails}: ${skill.label}`}>{skill.label}</button>
                <button type="button" className="shrink-0 rounded p-0.5 hover:bg-hover disabled:opacity-50" disabled={sending}
                  aria-label={`${t.skillRemove}: ${skill.label}`} onClick={() => onRemoveSkill(skill.id)}><X className="size-3" aria-hidden="true"/></button>
              </Badge>)}
            </div>

            <Button variant="ghost" size="sm" disabled={sending} onClick={onClearSkills}>
              {t.clearSelectedSkills}
            </Button>
          </div>
        )}

        <div className="focus-ring flex items-end gap-2 rounded-card border border-line bg-well p-2 transition-control">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={event => { if (!event.nativeEvent.isComposing && event.keyCode !== 229) onKeyDown(event); }}
            placeholder={t.messagePlaceholder}
            aria-label={t.messagePlaceholder}
            rows={1}
            disabled={sending && !processing}
            className="max-h-40 min-h-10 min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-body text-ink outline-none placeholder:text-subtle disabled:cursor-not-allowed"
          />

          {sendControl ?? <IconButton
            label={t.sendMessage}
            icon={<Send />}
            variant="solid"
            size="lg"
            onClick={onSend}
            disabled={!value.trim() || sending}
          />}
        </div>

        <div className="mt-2 flex flex-wrap justify-between gap-1 text-micro text-subtle">
          <span>{zh ? '只读查询可直接进行；任务需点击运行，覆盖和删除需确认' : 'Read-only queries run directly; click Run for tasks and approve overwrites or deletions'}</span>
          <span>{zh ? 'Enter 发送 · Shift + Enter 换行' : 'Enter to send · Shift + Enter for a new line'}</span>
        </div>
      </div>
    </div>
  );
}
