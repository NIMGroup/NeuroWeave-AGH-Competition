import { Moon, Sun } from 'lucide-react';
import { IconButton } from '../ui/IconButton';
import type { TranslationKeys } from '../types/i18n';

type ThemeSwitchProps = {
  t: TranslationKeys;
  darkMode: boolean;
  onToggle: () => void;
};

/**
 * Appearance toggle.
 *
 * Rendered as a single icon control instead of a wide sliding switch: the
 * switch occupied prime header space for a rarely used action and read as a
 * consumer setting rather than part of a professional tool.
 */
export function ThemeSwitch({ t, darkMode, onToggle }: ThemeSwitchProps) {
  return (
    <IconButton
      label={darkMode ? t.switchToLight : t.switchToDark}
      icon={darkMode ? <Moon /> : <Sun />}
      size="lg"
      variant="outline"
      onClick={onToggle}
    />
  );
}
