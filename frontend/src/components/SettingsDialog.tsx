import type { ReactNode } from 'react';
import { useEffect } from 'react';
import { Dialog } from '../ui/Dialog';
import { SettingsPanel } from './SettingsPanel';
import type { TranslationKeys } from '../types/i18n';

type SettingsDialogProps = {
  t: TranslationKeys;
  onClose: () => void;
  /** Element to receive focus once the dialog unmounts - the rail gear. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
};

/**
 * Application settings as a compact centered window.
 *
 * Reuses the single `Dialog` primitive (native `<dialog>`), so Escape,
 * backdrop click and the focus trap come from the platform. Content stays the
 * existing `SettingsPanel` - no second settings system.
 */
export function SettingsDialog({ t, onClose, returnFocusRef }: SettingsDialogProps): ReactNode {
  useEffect(
    () => () => {
      // Native `showModal` restores focus to the opener when `close()` runs,
      // but React unmounts without closing, so return focus explicitly.
      const target = returnFocusRef?.current;
      if (!target) return;
      const active = document.activeElement;
      if (!active || active === document.body || target.contains(active)) {
        target.focus({ preventScroll: true });
      }
    },
    [returnFocusRef]
  );

  return (
    <Dialog
      onClose={onClose}
      title={t.settingsNav}
      description={t.settingsGeneral}
      closeLabel={t.close}
      size="md"
    >
      <SettingsPanel t={t} />
    </Dialog>
  );
}
