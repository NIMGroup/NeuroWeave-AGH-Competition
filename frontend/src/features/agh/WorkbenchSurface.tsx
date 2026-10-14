import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import stylesheet from '../../index.css?inline';

let sheet: CSSStyleSheet | undefined;

/** Native AGH controls and business panels must not share their CSS cascade. */
export function WorkbenchSurface({ children }: { children: ReactNode }) {
  const [root, setRoot] = useState<ShadowRoot | null>(null);
  const attach = useCallback((element: HTMLDivElement | null) => {
    if (!element) return;
    if (!sheet) {
      sheet = new CSSStyleSheet();
      // Constructed sheets work with AGH's strict style CSP. Tokens live on the
      // shadow host; the themed business wrapper overrides them for dark mode.
      sheet.replaceSync(stylesheet.replaceAll(':root', ':host') + '\n:host { display: inline-flex; } .nw-workbench { font-family: var(--app-font-sans); font-size: 14px; line-height: 1.5; color: var(--ink); }');
    }
    const shadow = element.shadowRoot ?? element.attachShadow({ mode: 'open' });
    shadow.adoptedStyleSheets = [sheet];
    setRoot(shadow);
  }, []);
  return <div data-neuroweave-host ref={attach}>{root && createPortal(children, root)}</div>;
}
