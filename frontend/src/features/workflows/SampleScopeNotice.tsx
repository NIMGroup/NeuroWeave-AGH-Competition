import type { SampleScope } from './workflowTypes';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';

export function SampleScopeNotice({ scope }: { scope?: SampleScope | null }) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  if (!scope) return null;
  const matched = scope.eligible_sample_ids.length;
  return (
    <section className="rounded-card border border-warning-line bg-warning-quiet p-3 text-xs text-warning ">
      <p className="break-words">
        {t.workflowScopeSummary
          .replace('{{total}}', String(scope.total))
          .replace('{{matched}}', String(matched))
          .replace('{{skipped}}', String(scope.skipped_samples.length))}{' '}
        {matched > 0 ? t.workflowScopeRunnable : t.workflowScopeAdjust}
      </p>
      {scope.skipped_samples.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer">
            {t.workflowScopeSkipped.replace('{{count}}', String(scope.skipped_samples.length))}
          </summary>
          <ul className="mt-2 max-h-52 space-y-2 overflow-y-auto pr-2">
            {scope.skipped_samples.map((sample) => (
              <li key={sample.sample_id} className="break-words">
                <span className="font-medium">
                  {sample.label}（{sample.sample_id}）
                </span>
                <p>{sample.reasons.join('; ')}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
