import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState
} from 'react';
import type { Draft, Pipeline } from './libraryApi';
import { request } from '../../api/http';
import { createNodeId } from './pipelineGraph';
import { ui, useLibraryText } from '../../lib/libraryText';
import { errorText } from '../../api/http';
import type { LibraryMessage } from '../../lib/libraryText';

/** Local recovery survives unload; versioned server drafts survive browser changes. */
export function usePipelineDraft(
  initial: Pipeline | undefined,
  pipelineId?: string,
  pipelineVersion?: number
) {
  const [id] = useState(() => initial?.draft_id ?? createNodeId());
  const key = `neuroweave:pipeline-draft:${id}`;
  const [recovery] = useState(() => {
    try {
      const value = JSON.parse(localStorage.getItem(key) ?? 'null');
      return value?.editor && value.revision >= (initial?.draft_revision ?? 0)
        ? value
        : null;
    } catch {
      return null;
    }
  });
  const [draft, rawSet] = useState<Draft>(
    () =>
      recovery?.editor ??
      initial?.editor ?? { name: ui('新 Pipeline'), description: '', steps: [] }
  );
  const current = useRef(draft),
    revision = useRef(recovery?.revision ?? initial?.draft_revision ?? 0);
  const versions = useRef<Draft[]>([]),
    future = useRef<Draft[]>([]),
    lastEdit = useRef(0);
  const [historyState, setHistoryState] = useState({
    canUndo: false,
    canRedo: false
  });
  const [status, setStatus] = useState<LibraryMessage | null>(null);
  const translate = useLibraryText();
  const saved = useRef(''),
    flight = useRef<Promise<void> | null>(null);
  const metadata = useRef({ pipelineId, pipelineVersion });
  // Event handlers and an in-flight save need the latest committed association.
  useLayoutEffect(() => {
    metadata.current = { pipelineId, pipelineVersion };
  }, [pipelineId, pipelineVersion]);
  const setDraft = useCallback(
    (value: Draft | ((previous: Draft) => Draft)) => {
      const next = typeof value === 'function' ? value(current.current) : value;
      if (JSON.stringify(next) === JSON.stringify(current.current)) return;
      const now = Date.now();
      if (now - lastEdit.current > 400) {
        versions.current = [...versions.current.slice(-79), current.current];
      }
      lastEdit.current = now;
      future.current = [];
      current.current = next;
      rawSet(next);
      setHistoryState({
        canUndo: versions.current.length > 0,
        canRedo: future.current.length > 0
      });
    },
    []
  );
  const undo = () => {
    const value = versions.current.pop();
    if (value) {
      future.current.push(current.current);
      current.current = value;
      rawSet(value);
      lastEdit.current = 0;
      setHistoryState({
        canUndo: versions.current.length > 0,
        canRedo: future.current.length > 0
      });
    }
  };
  const redo = () => {
    const value = future.current.pop();
    if (value) {
      versions.current.push(current.current);
      current.current = value;
      rawSet(value);
      lastEdit.current = 0;
      setHistoryState({
        canUndo: versions.current.length > 0,
        canRedo: future.current.length > 0
      });
    }
  };
  const flush = useCallback(
    async function saveLatest() {
      while (flight.current) await flight.current;
      const editor = current.current;
      const signature = JSON.stringify([editor, metadata.current]);
      if (signature === saved.current) return;
      const action = (async () => {
        setStatus({ source: '正在保存草稿…' });
        try {
          const result = await request<{ revision: number }>(
            `/api/library/drafts/${id}`,
            {
              method: 'PUT',
              body: JSON.stringify({
                expected_revision: revision.current,
                editor,
                pipeline_id: metadata.current.pipelineId ?? null,
                pipeline_version: metadata.current.pipelineVersion ?? null
              })
            }
          );
          revision.current = result.revision;
          saved.current = signature;
          try {
            localStorage.setItem(
              key,
              JSON.stringify({
                id,
                editor: current.current,
                revision: revision.current,
                updated_at: new Date().toISOString(),
                pipeline_id: metadata.current.pipelineId ?? null,
                pipeline_version: metadata.current.pipelineVersion ?? null
              })
            );
          } catch {
            /* Server revision is durable even when browser storage is full. */
          }
          setStatus({ source: '草稿已保存' });
        } catch (error) {
          setStatus({ source: '草稿同步失败，保留浏览器恢复副本：{0}', values: [errorText(error)] });
          throw error;
        }
      })();
      flight.current = action;
      try {
        await action;
      } finally {
        flight.current = null;
      }
      if (JSON.stringify([current.current, metadata.current]) !== signature)
        await saveLatest();
    },
    [id, key]
  );
  useEffect(() => {
    try {
      localStorage.setItem(
        key,
        JSON.stringify({
          id,
          editor: draft,
          revision: revision.current,
          updated_at: new Date().toISOString(),
          pipeline_id: pipelineId ?? null,
          pipeline_version: pipelineVersion ?? null
        })
      );
    } catch {
      setStatus({ source: '浏览器恢复副本不可用，请保存到服务器。' });
    }
    const timer = setTimeout(() => void flush().catch(() => {}), 1000);
    return () => clearTimeout(timer);
  }, [draft, pipelineId, pipelineVersion, flush, key, id]);
  useEffect(() => {
    const save = () => {
      try {
        localStorage.setItem(
          key,
          JSON.stringify({
            id,
            editor: current.current,
            revision: revision.current,
            updated_at: new Date().toISOString(),
            pipeline_id: metadata.current.pipelineId ?? null,
            pipeline_version: metadata.current.pipelineVersion ?? null
          })
        );
      } catch {
        /* state shown by autosave */
      }
    };
    window.addEventListener('pagehide', save);
    return () => {
      save();
      window.removeEventListener('pagehide', save);
    };
  }, [key, id]);
  const statusText = !status
    ? ''
    : 'text' in status ? status.text : translate(status.source, ...(status.values ?? []));
  return { draft, setDraft, undo, redo, ...historyState, flush, status: statusText };
}
