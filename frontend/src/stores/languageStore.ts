import { create } from 'zustand';
import type { Language } from '../types/i18n';

type LanguageStore = {
  language: Language;
  setLanguage: (language: Language) => void;
};

const STORAGE_KEY = 'neuroweave-language';
/** Key used before the rebrand; read once so existing prefs survive. */
const LEGACY_STORAGE_KEY = 'neuroclaw-language';

function readStoredLanguage(): Language | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_STORAGE_KEY);
    return value === 'en' || value === 'zh' ? value : null;
  } catch {
    // Storage unavailable: fall back to the default below.
    return null;
  }
}

/**
 * Document language drives hyphenation, spellcheck and - importantly - the
 * Simplified Chinese font fallback selected by the browser.
 */
function applyLanguage(language: Language) {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
}

const initialLanguage: Language = readStoredLanguage() ?? 'en';
applyLanguage(initialLanguage);

export const useLanguageStore = create<LanguageStore>((set) => ({
  language: initialLanguage,

  setLanguage: (language) => {
    applyLanguage(language);
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // Preference will not persist; the session still works.
    }
    set({ language });
  }
}));
