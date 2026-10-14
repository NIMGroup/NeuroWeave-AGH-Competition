import { Children, isValidElement, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, Copy } from 'lucide-react';
import { useLanguageStore } from '../../stores/languageStore';
import { copyText } from '../../lib/clipboard';
import { IconButton } from '../../ui/IconButton';
import { Tooltip } from '../../ui/Tooltip';

export function CopyMessageButton({ content, code = false }: { content: string; code?: boolean }) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  useEffect(() => {
    if (state !== 'copied') return;
    const timer = window.setTimeout(() => setState('idle'), 2000);
    return () => window.clearTimeout(timer);
  }, [state]);
  const label = state === 'copied' ? (zh ? '已复制' : 'Copied')
    : state === 'failed' ? (zh ? '复制失败，请手动选择' : 'Copy failed; select the text')
    : code ? (zh ? '复制代码' : 'Copy code') : (zh ? '复制回复' : 'Copy reply');
  const icon = state === 'copied' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />;
  const onCopy = () => void copyText(content).then(() => setState('copied')).catch(() => setState('failed'));
  if (!code) return <Tooltip label={label}>
    <IconButton label={label} title="" icon={icon} onClick={onCopy} />
    <span role="status" aria-live="polite" className="sr-only">{state === 'idle' ? '' : label}</span>
  </Tooltip>;
  return <button type="button" className="focus-ring inline-flex items-center gap-1.5 rounded-chip px-2 py-1 text-micro text-muted hover:bg-hover hover:text-ink [&_svg]:size-3.5"
    aria-label={label} onClick={onCopy}>
    {icon}
    <span aria-live="polite">{label}</span>
  </button>;
}

function codeText(children: ReactNode): string {
  return Children.toArray(children).map(child => typeof child === 'string' ? child
    : isValidElement<{ children?: ReactNode }>(child) ? codeText(child.props.children) : '').join('');
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const first = Children.toArray(children)[0];
  const language = isValidElement<{ className?: string }>(first)
    ? first.props.className?.replace('language-', '') : '';
  return <div className="chat-code">
    <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-1">
      <span className="text-micro text-subtle">{language || 'code'}</span>
      <CopyMessageButton content={codeText(children).replace(/\n$/, '')} code />
    </div>
    <pre tabIndex={0}>{children}</pre>
  </div>;
}

export function MessageContent({ content }: { content: string }) {
  return <div className="chat-prose">
    <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
      pre: CodeBlock,
      table: ({ children }) => <div className="chat-table" tabIndex={0}><table>{children}</table></div>,
      a: ({ href, children }) => href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
      // Keep model-provided images as text rather than fetching remote resources.
      img: ({ alt }) => <span className="text-muted">[{alt || 'image'}]</span>
    }}>{content}</Markdown>
  </div>;
}
