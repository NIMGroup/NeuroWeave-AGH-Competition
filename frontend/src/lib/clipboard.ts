/** Clipboard support for HTTPS and the existing HTTP server, preserving focus. */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const field = document.createElement('textarea');
  const focused = document.activeElement as HTMLElement | null;
  field.value = text;
  field.style.position = 'fixed';
  field.style.opacity = '0';
  (focused?.closest('dialog') ?? document.body).appendChild(field);
  try {
    field.select();
    if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
  } finally {
    field.remove();
    focused?.focus();
  }
}
