/** Pointer capture changes event targets; hit-test in the canvas's own DOM tree. */
export function canvasInputAtPoint(viewport: HTMLElement | null, x: number, y: number): HTMLElement | null {
  if (!viewport) return null;
  const root = viewport.getRootNode() as Document | ShadowRoot;
  const input = root.elementFromPoint(x, y)?.closest<HTMLElement>('[data-pipeline-input]') ?? null;
  // Never connect a port belonging to a different editor or overlay.
  return input && viewport.contains(input) && !(input as HTMLButtonElement).disabled ? input : null;
}
