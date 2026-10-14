import { ui } from '../../lib/libraryText';
import { useLanguageStore } from '../../stores/languageStore';
import { ApiError, errorText } from '../../api/http';
import { Component, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { Dialog } from '../../ui/Dialog';
import * as api from './libraryApi';
import type { Draft, InputPort, Method, Node, Pipeline } from './libraryApi';
import { usePipelineDraft } from './usePipelineDraft';
import { PipelineCanvas } from './PipelineCanvas';
import { PipelineValidationPanel } from './PipelineValidationPanel';
import {
  shareInputs,
  sharedGroup,
  retainSharedNodes,
  detachSharedInput,
  arrangeNodes,
  conditionalConnectionIssues,
  connectNodes,
  createNodeId,
  disconnectInput,
  orderedSteps,
  removeNode
} from './pipelineGraph';
import type { Point } from './pipelineGraph';

import { button, field } from './pipelineEditorStyles';
import { PipelineMethodPalette } from './PipelineMethodPalette';
import { PipelineNodeInspector } from './PipelineNodeInspector';
import { usePipelineDescription } from './usePipelineDescription';
import { PipelineControls } from './PipelineControls';
import { methodQuery } from './libraryQueries';

type Props = {
  initial?: Pipeline;
  onClose: () => void;
  onSaved: (p: Pipeline) => void;
};

/** An editor failure must not unmount the user's conversation or running-task monitor. */
class EditorBoundary extends Component<
  { children: ReactNode; onClose: () => void },
  { error: string }
> {
  state = { error: '' };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message || ui('未知错误') };
  }
  render() {
    return this.state.error ? (
      <Dialog
        title={ui('Pipeline 编辑器遇到错误')}
        closeLabel={ui('关闭')}
        onClose={this.props.onClose}
      >
        <p>
          {ui('请关闭编辑器后重试。已保存的 Pipeline 和运行中的任务不受影响。')}
        </p>
        <p className="mt-2 break-words text-xs text-danger">
          {this.state.error}
        </p>
      </Dialog>
    ) : (
      this.props.children
    );
  }
}
export function PipelineEditor(props: Props) {
  return (
    <EditorBoundary onClose={props.onClose}>
      <Editor {...props} />
    </EditorBoundary>
  );
}

function Editor({ initial, onClose, onSaved }: Props) {
  const language = useLanguageStore((s) => s.language);
  const [identity, setIdentity] = useState(
    initial?.editor ? initial.id || undefined : undefined
  );
  const [version, setVersion] = useState(
    initial?.version ? Number(initial.version) : undefined
  );
  const authoring = usePipelineDraft(initial, identity, version);
  const { draft, setDraft } = authoring;
  const [showConditionalInputs, setShowConditionalInputs] = useState(false);
  const [selected, setSelected] = useState<string | null>(
    draft.steps[0]?.id ?? null
  );
  const [showMethods, setShowMethods] = useState(
    () => typeof window === 'undefined' || window.innerWidth >= 1100
  );
  const [showInspector, setShowInspector] = useState(
    () => typeof window === 'undefined' || window.innerWidth >= 1100
  );
  useEffect(() => {
    // Crossing into a narrow viewport frees the canvas; users can reopen a dock.
    const narrow = window.matchMedia('(max-width: 1099px)');
    const resize = (event: MediaQueryListEvent) => {
      if (event.matches) {
        setShowMethods(false);
        setShowInspector(false);
      }
    };
    narrow.addEventListener('change', resize);
    return () => narrow.removeEventListener('change', resize);
  }, []);
  const [q, setQ] = useState('');
  const [cursor, setCursor] = useState(0);
  const client = useQueryClient();
  const pending = useRef(false);
  const methods = useQuery({
    queryKey: ['library-editor-methods', q, cursor, language],
    queryFn: () => api.catalog('method', q, cursor, '', '', language)
  });
  const ids = [...new Set(draft.steps.map((s) => s.method_id))];
  const contracts = useQueries({
    queries: ids.map((id) => methodQuery(id, language))
  });
  const definitions: Record<string, Method> = Object.fromEntries(
    contracts.flatMap((r) => (r.data ? [[r.data.id, r.data]] : []))
  );
  const contractError = contracts.find((r) => r.isError)?.error;
  const loading = contracts.some((r) => r.isPending);
  const [error, setError] = useState('');
  const [mutating, setBusy] = useState(false);
  const [preview, setPreview] = useState<Pipeline | null>(null);

  const [saved, setSaved] = useState(false);
  const connectionIssues = conditionalConnectionIssues(
    draft.steps,
    definitions
  );
  const {
    generating,
    descriptionStatus,
    generateDescription,
    cancelGeneration
  } = usePipelineDescription({
    draft,
    setDraft,
    language,
    blocked: loading || !!contractError || !!connectionIssues.length || mutating
  });
  const busy = mutating || generating;

  const change = (next: Draft) => {
    if (pending.current) return;
    setDraft(next);
    setPreview(null);
    setSaved(false);
    setError('');
  };
  const update = (id: string, next: Partial<Node>) =>
    change({
      ...draft,
      steps: draft.steps.map((s) => (s.id === id ? { ...s, ...next } : s))
    });
  const add = async (id: string, position?: Point) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      await client.fetchQuery(methodQuery(id, language));
      // Generate outside React's updater: failures stay inside this try/catch,
      // and StrictMode cannot generate a second identity when replaying an updater.
      const node: Node = {
        id: createNodeId(),
        method_id: id,
        parameters: {},
        inputs: {},
        position: position ?? {
          x: 80 + (draft.steps.length % 3) * 480,
          y: 80 + Math.floor(draft.steps.length / 3) * 440
        }
      };
      setDraft((p) => ({ ...p, steps: [...p.steps, node] }));
      setSelected(node.id);
      setPreview(null);
      setSaved(false);
    } catch (e) {
      setError(errorText(e, ui('读取方法详情失败')));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  const connect = (from: string, output: string, to: string, input: string) => {
    try {
      if (
        sharedGroup(draft.shared_inputs ?? [], {
          step_id: to,
          input_slot_id: input
        })
      )
        throw new Error(
          ui('该输入正在共享外部文件，请先解除共享，再连接上游输出')
        );
      change({
        ...draft,
        steps: connectNodes(draft.steps, definitions, from, output, to, input)
      });
    } catch (e) {
      setError(errorText(e));
    }
  };
  const share = (from: InputPort, to: InputPort) => {
    try {
      change(shareInputs(draft, definitions, from, to));
    } catch (e) {
      setError(errorText(e));
    }
  };
  const detach = (port: InputPort) =>
    change({
      ...draft,
      shared_inputs: detachSharedInput(draft.shared_inputs ?? [], port)
    });
  const submit = async (persist: boolean) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      // Validation and publishing preserve the description already reviewed by the user.
      const ordered = { ...draft, steps: orderedSteps(draft.steps) };
      const p = persist
        ? await api.save({ ...ordered, expected_version: version }, identity)
        : await api.validate(ordered);
      setPreview(p);
      if (persist) {
        setIdentity(p.id);
        setVersion(Number(p.version));
        setSaved(true);
        onSaved(p);
        if (p.layout_warning) setError(p.layout_warning);
      }
    } catch (e) {
      setError(errorText(e, ui('保存或校验流程失败')));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  const selectedNode = draft.steps.find((n) => n.id === selected);
  const close = () => {
    cancelGeneration();
    void authoring
      .flush()
      .then(onClose)
      .catch((error) => {
        // Another window may delete this draft while its editor remains open.
        if(error instanceof ApiError&&error.code==='draft_deleted')onClose();
        else setError(errorText(error, ui('保存草稿失败')));
      });
  };
  const canSave =
    !busy &&
    !loading &&
    !contractError &&
    !connectionIssues.length &&
    !!draft.steps.length &&
    !!draft.name.trim();
  return (
    <Dialog
      title={ui('Pipeline 节点编排')}
      size="xl"
      closeLabel={ui('关闭')}
      onClose={close}
      placement="fullscreen"
      className="!max-w-none h-full !rounded-none"
      contentClassName="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-2"
      footer={
        <>
          <span className="mr-auto text-xs text-subtle">
            {busy ? ui('正在读取方法或保存… · ') : ''}
            {draft.steps.length} {ui('个步骤 · 保存不会启动任务')}
            {saved ? ui(' · 已保存 v{0}', version) : ui(' · 未保存')}
          </span>
          <button
            className={button}
            disabled={busy || !authoring.canUndo}
            onClick={authoring.undo}
          >
            {ui('撤销')}
          </button>
          <button
            className={button}
            disabled={busy || !authoring.canRedo}
            onClick={authoring.redo}
          >
            {ui('重做')}
          </button>
          <button
            className={button}
            disabled={busy}
            onClick={() =>
              void authoring.flush().catch((e) => setError(errorText(e, ui('保存草稿失败'))))
            }
          >
            {ui('保存草稿')}
          </button>
          <button
            className={button}
            disabled={!canSave}
            onClick={() => void submit(false)}
          >
            {ui('校验并预览接口')}
          </button>
          <button
            className={button}
            disabled={!canSave}
            onClick={() => void submit(true)}
          >
            {ui('发布可用版本')}
            {version ? ` v${version + 1}` : ''}
          </button>
        </>
      }
    >
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
        <span
          className="min-w-0 flex-1 truncate font-medium"
          title={draft.name}
        >
          {draft.name}
        </span>
        <span className="text-subtle">{authoring.status}</span>
      </div>
      <PipelineControls draft={draft} disabled={busy} onChange={change}/>
      <details className="max-h-[28dvh] shrink-0 overflow-auto rounded border border-line bg-raised px-3 py-2 text-xs">
        <summary className="cursor-pointer font-medium">
          {ui('名称与说明')}
        </summary>
        <div className="pt-2">
          <div className="flex shrink-0 flex-wrap gap-3">
            <label className="min-w-0 flex-1 text-xs">
              {ui('名称')}
              <input
                disabled={busy}
                className={field}
                value={draft.name}
                onChange={(e) => change({ ...draft, name: e.target.value })}
              />
            </label>
            <label className="min-w-0 flex-[2] text-xs">
              {ui('说明')}
              <textarea
                disabled={busy}
                className={field}
                rows={3}
                value={draft.description}
                onChange={(e) =>
                  change({ ...draft, description: e.target.value })
                }
              />
            </label>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-subtle">
            <button
              type="button"
              className={button}
              disabled={!canSave}
              onClick={() =>
                void generateDescription().then((updated) => {
                  if (updated) {
                    setPreview(null);
                    setSaved(false);
                  }
                })
              }
            >
              {generating ? ui('正在生成描述…') : ui('生成说明')}
            </button>
            <span>
              {ui('完成编排后点击生成；将替换当前说明，可继续手动编辑。')}
            </span>
            <span role="status">{descriptionStatus}</span>
          </div>
        </div>
      </details>
      {(error || contractError) && (
        <div
          role="alert"
          className="max-h-24 shrink-0 overflow-auto rounded border border-line p-2 text-sm text-danger"
        >
          {error || ui('无法加载方法定义：{0}', String(contractError))}
          {contractError && (
            <button
              className={button}
              onClick={() => {
                for (const c of contracts) if (c.isError) void c.refetch();
              }}
            >
              {ui('重试加载')}
            </button>
          )}
        </div>
      )}
      {!!connectionIssues.length && (
        <div
          role="alert"
          className="max-h-28 shrink-0 overflow-auto rounded border border-line p-2 text-xs text-warning"
        >
          {ui('连接已保留，请先调整上游参数：')}
          {connectionIssues.map((issue) => (
            <p key={issue}>{issue}</p>
          ))}
        </div>
      )}
      <div
        className="grid min-h-0 flex-1 gap-2 overflow-hidden"
        style={{
          gridTemplateColumns: `${showMethods ? 'minmax(170px, 220px)' : '32px'} minmax(0, 1fr) ${showInspector ? 'minmax(210px, 280px)' : '32px'}`,
          gridTemplateRows: 'minmax(0, 1fr)'
        }}
      >
        {showMethods ? <div className="flex min-h-0 flex-col gap-1">
          <div className="flex shrink-0 items-center justify-between text-xs"><strong>{ui('方法目录')}</strong>
            <button className={button} aria-label={ui('收起方法目录')} title={ui('收起方法目录')} onClick={()=>setShowMethods(false)}>‹</button></div>
          <div className="min-h-0 flex-1"><PipelineMethodPalette
            page={methods.data}
            error={methods.error}
            loading={methods.isPending}
            q={q}
            cursor={cursor}
            busy={busy}
            nodeCount={draft.steps.length}
            onSearch={(value) => {
              setQ(value);
              setCursor(0);
            }}
            onPage={setCursor}
            onAdd={(id) => void add(id)}
          /></div>
        </div> : <button className="self-stretch rounded border border-line text-accent hover:bg-hover"
          aria-label={ui('展开方法目录')} title={ui('展开方法目录')}
          onClick={()=>{setShowMethods(true);if(window.innerWidth<1100)setShowInspector(false);}}>›</button>}
        <PipelineCanvas
          sharedInputs={draft.shared_inputs}
          onShareInputs={share}
          onDetachSharedInput={detach}
          showConditionalInputs={showConditionalInputs}
          onToggleConditionalInputs={() => setShowConditionalInputs((v) => !v)}
          steps={draft.steps}
          definitions={definitions}
          selected={selected}
          disabled={busy}
          onSelect={setSelected}
          onMove={(id, position) => update(id, { position })}
          onAdd={(id, position) => {
            if (draft.steps.length >= 50)
              setError(ui('每个 Pipeline 最多 50 个步骤'));
            else void add(id, position);
          }}
          onConnect={connect}
          onDisconnect={(id, input, source) =>
            change({
              ...draft,
              steps: disconnectInput(draft.steps, id, input, source)
            })
          }
          onArrange={() => {
            try {
              change({
                ...draft,
                steps: arrangeNodes(draft.steps, definitions)
              });
            } catch (e) {
              setError(errorText(e, ui('自动排列失败')));
            }
          }}
        />
        {showInspector ? <div className="flex min-h-0 flex-col gap-1">
          <div className="flex shrink-0 items-center justify-between text-xs"><strong>{ui('步骤参数')}</strong>
            <button className={button} aria-label={ui('收起步骤参数')} title={ui('收起步骤参数')} onClick={()=>setShowInspector(false)}>›</button></div>
          <div className="min-h-0 flex-1"><PipelineNodeInspector
            draft={draft}
            definitions={definitions}
            selectedNode={selectedNode}
            showConditionalInputs={showConditionalInputs}
            busy={busy}
            change={change}
            update={update}
            connect={connect}
            detach={detach}
            onRemove={(id) => {
              const steps = removeNode(draft.steps, id);
              change({
                ...draft,
                steps,
                shared_inputs: retainSharedNodes(
                  draft.shared_inputs ?? [],
                  steps
                )
              });
              setSelected(null);
            }}
          /></div>
        </div> : <button className="self-stretch rounded border border-line text-accent hover:bg-hover"
          aria-label={ui('展开步骤参数')} title={ui('展开步骤参数')}
          onClick={()=>{setShowInspector(true);if(window.innerWidth<1100)setShowMethods(false);}}>‹</button>}
      </div>
      {preview && (
        <PipelineValidationPanel
          preview={preview}
          showConditionalInputs={showConditionalInputs}
        />
      )}
    </Dialog>
  );
}
