import { useCallback } from 'react';
import { useLanguageStore } from '../stores/languageStore';
import type { Language } from '../types/i18n';
import english from './library-en.json';

const DICTIONARY = english as Record<string, string>;

/**
 * A message held across renders. `source` is a dictionary key that stays
 * translatable; `text` is server-provided content that must never be rewritten.
 */
export type LibraryMessage = { source: string; values?: unknown[] } | { text: string };

function translate(language: Language, source: string, values: unknown[]): string {
  const text = language === 'en' ? DICTIONARY[source] ?? source : source;
  return text.replace(/\{(\d+)\}/g, (_, index: string) => String(values[Number(index)] ?? ''));
}

/** Static UI translations only. Never translate IDs, values or conversation text. */
export function ui(source: string, ...values: unknown[]): string {
  return translate(useLanguageStore.getState().language, source, values);
}

/**
 * Translator bound to the selected language. Components that keep a message in
 * state use this so the text follows a language switch instead of freezing the
 * language that happened to be active when the message was produced.
 */
export function useLibraryText(): (source: string, ...values: unknown[]) => string {
  const language = useLanguageStore((state) => state.language);
  return useCallback(
    (source: string, ...values: unknown[]) => translate(language, source, values),
    [language]
  );
}
