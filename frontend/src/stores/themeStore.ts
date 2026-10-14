import { create } from 'zustand';

export type ThemeMode = 'light' | 'dark';
export type AccentId = 'blue' | 'teal' | 'violet';
export type FontSizeId = 'small' | 'default' | 'large';

/**
 * Persisted appearance preference.
 *
 * The initial value is applied to `<html>` before paint by the inline script in
 * index.html, so first render never flashes the wrong theme; this module keeps
 * React and the document class in sync afterwards.
 */
const STORAGE_KEY = 'neuroweave-theme';
const DARK_CHROME = '#0a0a0b';
const LIGHT_CHROME = '#ffffff';

type StoredAppearance = {
  theme: ThemeMode;
  accent: AccentId;
  fontSize: FontSizeId;
  wave: boolean;
};

/**
 * The preference is stored as one JSON object under a single key. Older builds
 * stored a bare `"light"`/`"dark"` string - both shapes are read so existing
 * users keep their theme; new fields default when absent.
 */
function readStoredAppearance(): StoredAppearance | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    if (raw === 'light' || raw === 'dark') {
      return { theme: raw, accent: 'blue', fontSize: 'default', wave: false };
    }
    const parsed = JSON.parse(raw) as Partial<StoredAppearance>;
    if (parsed.theme !== 'light' && parsed.theme !== 'dark') return null;
    const accent: AccentId =
      parsed.accent === 'teal' || parsed.accent === 'violet' ? parsed.accent : 'blue';
    const fontSize: FontSizeId =
      parsed.fontSize === 'small' || parsed.fontSize === 'large' ? parsed.fontSize : 'default';
    return { theme: parsed.theme, accent, fontSize, wave: parsed.wave === true };
  } catch {
    // Private-mode browsers or corrupt values: fall back to system defaults.
    return null;
  }
}

function prefersDark(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : false;
}

export function applyTheme(theme: ThemeMode) {
  if (typeof document === 'undefined') return;
  document.documentElement.classList.toggle('dark', theme === 'dark');
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', theme === 'dark' ? DARK_CHROME : LIGHT_CHROME);
}

/** Accent colours are token overrides keyed off a `data-accent` attribute. */
export function applyAccent(accent: AccentId) {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.accent = accent;
}

/** Font size scales every rem-based utility through the root font size. */
export function applyFontSize(fontSize: FontSizeId) {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.fontSize = fontSize;
}

/**
 * Classy Mode presentation layer. A single `data-classy` flag retunes the
 * existing tokens (see index.css) - no second theme system, no store split.
 */
export function applyClassy(enabled: boolean) {
  if (typeof document === 'undefined') return;
  if (enabled) document.documentElement.dataset.classy = 'true';
  else delete document.documentElement.dataset.classy;
}

const storedAppearance = readStoredAppearance();
const initialTheme: ThemeMode = storedAppearance?.theme ?? (prefersDark() ? 'dark' : 'light');
const initialAccent: AccentId = storedAppearance?.accent ?? 'blue';
const initialFontSize: FontSizeId = storedAppearance?.fontSize ?? 'default';
const initialWave = storedAppearance?.wave ?? false;
applyTheme(initialTheme);
applyAccent(initialAccent);
applyFontSize(initialFontSize);
applyClassy(initialWave);

type ThemeStore = {
  theme: ThemeMode;
  accent: AccentId;
  fontSize: FontSizeId;
  wave: boolean;
  setTheme: (theme: ThemeMode) => void;
  setAccent: (accent: AccentId) => void;
  setFontSize: (fontSize: FontSizeId) => void;
  setWave: (enabled: boolean) => void;
  toggleTheme: () => void;
};

function persist(appearance: Omit<StoredAppearance, never>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance));
  } catch {
    // Preference simply will not persist; the session still works.
  }
}

export const useThemeStore = create<ThemeStore>((set, get) => ({
  theme: initialTheme,
  accent: initialAccent,
  fontSize: initialFontSize,
  wave: initialWave,

  setTheme: (theme) => {
    applyTheme(theme);
    const { accent, fontSize, wave } = get();
    persist({ theme, accent, fontSize, wave });
    set({ theme });
  },

  setAccent: (accent) => {
    applyAccent(accent);
    const { theme, fontSize, wave } = get();
    persist({ theme, accent, fontSize, wave });
    set({ accent });
  },

  setFontSize: (fontSize) => {
    applyFontSize(fontSize);
    const { theme, accent, wave } = get();
    persist({ theme, accent, fontSize, wave });
    set({ fontSize });
  },

  setWave: (wave) => {
    // The overlay itself lives in lib/mouseWave; the App wires this store
    // field to it once on mount, so the store stays UI-library free.
    applyClassy(wave);
    const { theme, accent, fontSize } = get();
    persist({ theme, accent, fontSize, wave });
    set({ wave });
  },

  toggleTheme: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark')
}));
