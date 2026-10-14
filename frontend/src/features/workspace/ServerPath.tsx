import { useState } from 'react';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import { copyText } from '../../lib/clipboard';

export function ServerPath({ path, label }: { path: string; label?: string }) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  return (
    <div className="flex min-w-0 items-center gap-1 text-xs">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-1" title={path}>
        {label ?? path}
      </code>
      <button
        className="shrink-0 rounded border border-line px-1.5 py-1 hover:bg-hover "
        title={path}
        onClick={async () => {
          setCopyError(false);
          try {
            await copyText(path);
            setCopied(true);
          } catch {
            setCopied(false);
            setCopyError(true);
          }
        }}
      >
        {copied ? t.workspaceCopied : copyError ? t.workspaceCopyFallback : t.workspaceCopyPath}
      </button>
    </div>
  );
}
