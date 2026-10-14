import { Search, X } from 'lucide-react';
import { getTranslations } from '../../lib/i18n';
import { useLanguageStore } from '../../stores/languageStore';

type ConversationSearchProps = {
  value: string;
  onChange: (value: string) => void;
};

export function ConversationSearch({ value, onChange }: ConversationSearchProps) {
  const language = useLanguageStore((state) => state.language);
  const t = getTranslations(language);

  return (
    <div className="relative">
      <Search
        className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-subtle"
        aria-hidden="true"
      />

      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={t.searchConversations}
        aria-label={t.searchConversations}
        className="h-9 w-full rounded-control border border-line bg-raised pl-8 pr-8 text-xs text-ink transition-control placeholder:text-subtle"
      />

      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label={t.clearSearch}
          className="absolute right-1.5 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-chip text-subtle transition-control hover:bg-hover hover:text-ink"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
