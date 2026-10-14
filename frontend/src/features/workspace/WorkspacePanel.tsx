import { useCallback, useEffect, useRef, useState } from 'react';
import { useResizablePanel } from '../../ui/useResizablePanel';
import { browseRootForPath, workspaceForPath } from './outputSelection';
import { errorText } from '../../api/http';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';
import type { TranslationKeys } from '../../types/i18n';
import {
  createWorkspace,
  deleteWorkspace,
  editWorkspace,
  fetchFsRoots,
  fetchTrash,
  inspectFs,
  listFs,
  listWorkspaces,
  readFsText,
  requestSensitiveRead,
  restoreTrashItem,
  runFsAction
} from './workspaceApi';
import type {
  FsEntry,
  FsListing,
  TrashItem,
  Workspace,
  WorkspaceOperation
} from './workspaceTypes';
import { ServerPath } from './ServerPath';
import { WorkspaceConfirmation } from './WorkspaceConfirmation';

const FIELD = 'min-w-0 rounded-chip border border-line bg-raised px-2 py-1.5 text-sm ';
const BTN =
  'rounded-control border border-line px-2.5 py-1.5 text-xs font-medium transition hover:bg-hover disabled:opacity-50 ';
const ACTION_LABEL_KEYS: Record<string, keyof TranslationKeys> = {
  rename: 'workspaceRename',
  copy: 'workspaceCopy',
  move: 'workspaceMove',
  delete: 'workspaceDelete'
};

type WorkspacePanelProps = {
  workspaceId: string | null;
  conversationId: string | null;
  datasetId?: string | null;
  datasetOnly?: boolean;
  selectionMode?: boolean;
  onSelect: (workspace: Workspace | null) => Promise<void>;
  onClose: () => void;
  locked?: boolean;
  onPick?: (entry: FsEntry) => void;
  initialPath?: string;
};

export function WorkspacePanel({
  workspaceId,
  conversationId,
  datasetId = null,
  datasetOnly = false,
  selectionMode = false,
  onSelect,
  onClose,
  locked = false,
  onPick,
  initialPath
}: WorkspacePanelProps) {
  const language = useLanguageStore((s) => s.language);
  const t = getTranslations(language);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [roots, setRoots] = useState<string[]>([]);
  const [root, setRoot] = useState(datasetOnly ? 'dataset' : '');
  const [registerMode, setRegisterMode] = useState(selectionMode || !workspaceId);
  const [browsedWorkspaceId, setBrowsedWorkspaceId] = useState(workspaceId);
  const [directory, setDirectory] = useState(initialPath ?? '.');
  const [jump, setJump] = useState('');
  const [filter, setFilter] = useState('');
  const [sort, setSort] = useState('name');
  const [hidden, setHidden] = useState(false);
  const [listing, setListing] = useState<FsListing | null>(null);
  const [loadedLocation, setLoadedLocation] = useState('');
  const [selected, setSelected] = useState<FsEntry | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [preview, setPreview] = useState<{
    text: string;
    truncated: boolean;
    next_offset: number;
  } | null>(null);
  const [editor, setEditor] = useState<string | null>(null);
  const [destination, setDestination] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [permanent, setPermanent] = useState(false);
  const [pending, setPending] = useState<WorkspaceOperation | null>(null);
  const [trash, setTrash] = useState<TrashItem[] | null>(null);
  const [restorePaths, setRestorePaths] = useState<Record<string, string>>({});
  const [refresh, setRefresh] = useState(0);
  const requestVersion = useRef(0);
  const loading = useRef(false);
  const previewVersion = useRef(0);
  const mutationPending = useRef(false);
  const [width, setWidth] = useState(540);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const {panelRef, style: panelStyle, handles: resizeHandles} = useResizablePanel(t.workspacePickerResize);
  useEffect(()=>{if(dialogRef.current&&!dialogRef.current.open)dialogRef.current.showModal();}, []);
  const dismissPending = useCallback(() => setPending(null), []);
  const boundId = registerMode || datasetOnly || selectionMode ? null : workspaceId;
  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);

  const canManage = !datasetOnly && !selectionMode && !!selectedWorkspace?.manageable;
  const locationKey=JSON.stringify([boundId,directory,root,filter,hidden,sort,datasetOnly,datasetId]);
  const listingFresh=loadedLocation===locationKey;
  const matchingWorkspace = listing ? workspaceForPath(workspaces, listing.path) : undefined;
  const loadWorkspaces = useCallback(async () => setWorkspaces(await listWorkspaces()), []);
  useEffect(() => {
    if (datasetOnly) return;
    if (selectionMode) {
      let active=true;
      void Promise.all([listWorkspaces(), fetchFsRoots()]).then(([items, value])=>{
        if(!active)return;
        setWorkspaces(items);setRoots(value.roots);
        const current=items.find(item=>item.id===workspaceId);
        setRoot(browseRootForPath(current?.root_path??value.default_root,value.roots)??value.default_root);
        setDirectory(initialPath ?? current?.root_path ?? value.default_root);
      }).catch((e:unknown)=>{if(active)setError(errorText(e,t.errorMessage));});
      return ()=>{active=false;};
    }
    void listWorkspaces()
      .then(setWorkspaces)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    void fetchFsRoots()
      .then((value) => {
        setRoots(value.roots);
        setRoot(value.default_root);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [loadWorkspaces, datasetOnly, selectionMode, workspaceId, initialPath, t.errorMessage]);

  const load = useCallback(
    async (cursor?: string) => {
      if (!root || (cursor && loading.current)) return;
      const version = cursor ? requestVersion.current : ++requestVersion.current;
      loading.current = true;
      setBusy(true);
      setError('');
      try {
        const result = await listFs({
          path: directory,
          rootPath: boundId ? undefined : root,
          workspaceId: boundId,
          datasetId: datasetOnly ? datasetId : undefined,
          sort,
          filter,
          showHidden: hidden,
          cursor
        });
        if (version !== requestVersion.current) return;
        setListing((previous) =>
          cursor && previous ? { ...result, items: [...previous.items, ...result.items] } : result
        );
        setLoadedLocation(locationKey);
        setJump(result.path);
      } catch (e) {
        if (version === requestVersion.current)
          setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (version === requestVersion.current) {
          setBusy(false);
          loading.current = false;
        }
      }
    },
    [boundId, directory, root, filter, hidden, sort, datasetOnly, datasetId, locationKey]
  );
  useEffect(() => {
    const timer = setTimeout(() => {
      previewVersion.current += 1;
      setListing(null);
      setSelected(null);
      setPreview(null);
      void load();
    }, 180);
    return () => {
      clearTimeout(timer);
      requestVersion.current += 1;
      loading.current = false;
    };
  }, [load, refresh]);

  const perform = async () => {
    if (!editor || datasetOnly || busy || selecting || !listing || (selectionMode && !listingFresh)) return;
    if (['register', 'workspace-edit'].includes(editor) && locked) return;
    if (editor !== 'register' && editor !== 'workspace-edit' && (registerMode ? locked || editor !== 'mkdir' : !canManage)) return;
    mutationPending.current=true;
    setBusy(true);
    setError('');
    try {
      if (editor === 'register') {
        if (!destination.trim()) return;
        const value = await createWorkspace(destination.trim(), listing.path);
        await loadWorkspaces();
        await onSelect(value);
        if(selectionMode){onClose();return;}
        setRegisterMode(false); setDirectory('.'); setEditor(null);
        return;
      }
      if (editor === 'workspace-edit' && boundId) {
        if (locked || !destination.trim()) return;
        await editWorkspace(boundId, destination.trim());
        await loadWorkspaces();
        setEditor(null);
        return;
      }
      const path = editor === 'mkdir' ? `${listing.path}/${destination.trim()}` : selected?.path;
      const value = await runFsAction(editor as 'mkdir' | 'copy' | 'move' | 'rename' | 'delete', {
        workspaceId: boundId,
        rootPath: !boundId && editor === 'mkdir' ? listing.path : undefined,
        conversationId,
        path: path ?? '',
        destination: ['copy', 'move', 'rename'].includes(editor) ? destination : null,
        overwrite: ['copy', 'move', 'rename'].includes(editor) && overwrite,
        permanent: editor === 'delete' && permanent
      });
      setPending(value);
      setEditor(null);
      setRefresh((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      mutationPending.current=false;setBusy(false);
    }
  };
  const read = async (append = false) => {
    if (!selected || (!boundId && !datasetOnly)) return;
    const version = ++previewVersion.current;
    try {
      // Keep only the current bounded page, even when a huge text file is opened.
      const value = await readFsText(
        boundId,
        selected.path,
        append ? (preview?.next_offset ?? 0) : 0,
        datasetOnly ? datasetId : undefined
      );
      if (version === previewVersion.current) setPreview(value);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const relativeParts =
    listing?.relative_path === '.'
      ? []
      : (listing?.relative_path.split(/[\\/]/).filter(Boolean) ?? []);

  return (
    <dialog ref={dialogRef} aria-label={datasetOnly ? t.datasetFilesTitle : selectionMode ? t.workspaceSelect : t.workspaceTitle}
      className="dialog-root" data-placement={selectionMode?'center':'right'}
      onCancel={e=>{e.preventDefault();e.stopPropagation();if(!selecting&&!mutationPending.current)onClose();}}
      onClick={e=>{if(e.target===dialogRef.current&&!selecting&&!mutationPending.current)onClose();}}>
      <div ref={panelRef} style={selectionMode ? panelStyle : {width,maxWidth:'calc(100vw - 12px)'}}
        className="dialog-panel relative flex h-dvh min-h-0 min-w-0 flex-col overflow-hidden border border-line bg-raised shadow-2xl rounded-panel">
      {selectionMode && resizeHandles}
      {!selectionMode && <button type="button" aria-label={t.workspaceResize} className="absolute inset-y-0 left-0 z-10 w-2 cursor-col-resize touch-none hover:bg-accent-quiet"
        onKeyDown={e => {
          if (e.key === 'ArrowLeft') setWidth(w => Math.min(window.innerWidth - 12, w + 32));
          if (e.key === 'ArrowRight') setWidth(w => Math.max(320, w - 32));
        }}
        onPointerDown={e => e.currentTarget.setPointerCapture(e.pointerId)}
        onPointerMove={e => {
          if (e.currentTarget.hasPointerCapture(e.pointerId))
            setWidth(Math.max(320, Math.min(window.innerWidth - 12, window.innerWidth - e.clientX)));
        }} />}
      <div className="flex shrink-0 items-center justify-between border-b border-line p-3 pl-5 ">
        <strong className="text-sm">{datasetOnly ? t.datasetFilesTitle : selectionMode ? t.workspaceSelect : t.workspaceTitle}</strong>
        <button
          type="button"
          onClick={()=>{if(!selecting&&!mutationPending.current)onClose();}} disabled={selecting||(busy&&!!editor)}
          className="rounded-control border border-line px-2.5 py-1.5 text-xs "
        >
          {t.workspaceClose}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 pl-5" onScroll={e => {
        const element = e.currentTarget;
        if (!error && element.scrollHeight - element.scrollTop - element.clientHeight < 140 && listing?.next_cursor)
          void load(listing.next_cursor);
      }}>
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {!datasetOnly && <select
              aria-label={t.workspaceSelect}
              className={`${FIELD} flex-1`}
              disabled={locked || selecting || !!onPick}
              value={selectionMode ? (browsedWorkspaceId??'') : registerMode ? '' : (workspaceId ?? '')}
              onChange={(e) => {
                const value = workspaces.find((item) => item.id === e.target.value) ?? null;
                if(selectionMode){setBrowsedWorkspaceId(value?.id??null);setRoot(value ? browseRootForPath(value.root_path,roots)??root : root);setDirectory(value?.root_path??'.');setEditor(null);return;}
                setSelecting(true);
                setError('');
                void onSelect(value)
                  .then(() => {
                    setRegisterMode(!value);
                    setDirectory('.');
                    setTrash(null);
                    setEditor(null);
                  })
                  .catch((err: unknown) =>
                    setError(err instanceof Error ? err.message : String(err))
                  )
                  .finally(() => setSelecting(false));
              }}
            >
              <option value="">{t.workspaceSelect}</option>
              {workspaces
                .filter((item) => item.status === 'active')
                .map((item) => (
                  <option key={item.id} value={item.id} disabled={!item.manageable}>
                    {item.name} · {item.root_path}{item.manageable ? '' : ' (' + t.workspaceReadOnly + ')'}
                  </option>
                ))}
            </select>}
            {!datasetOnly && !selectionMode && !onPick && (
              <button
                type="button"
                className={BTN}
                disabled={locked}
                onClick={() => {
                  setRegisterMode(true);
                  setDirectory('.');
                }}
              >
                {t.workspaceBrowseServer}
              </button>
            )}
            <button type="button" className={BTN} onClick={() => setRefresh((v) => v + 1)}>
              {t.workspaceRefresh}
            </button>
          </div>
          <p className="text-xs text-subtle">
            {datasetOnly ? t.datasetFilesHint : t.workspaceHint}
            {locked ? t.workspaceLockedHint : ''}
          </p>
          {!datasetOnly && selectedWorkspace && !registerMode &&
            <p className="text-meta text-muted">{selectedWorkspace.permission_reason}</p>}
          {registerMode && !datasetOnly && (
            <select
              aria-label={t.workspaceRootsAria}
              value={root}
              className={`${FIELD} w-full`}
              onChange={(e) => {
                setRoot(e.target.value);
                setDirectory('.');
              }}
            >
              {!roots.includes(root) && <option value={root}>{root}</option>}
              {roots.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          )}
          <form
            className="flex min-w-0 gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if(selectionMode && jump.startsWith('/'))setRoot(browseRootForPath(jump,roots)??root);
              setEditor(null);setDirectory(jump);
            }}
          >
            <input
              aria-label={t.workspaceJumpAria}
              className={`${FIELD} flex-1`}
              value={jump}
              title={jump}
              onChange={(e) => setJump(e.target.value)}
            />
            <button type="submit" className={BTN}>
              {t.workspaceJump}
            </button>
            <button
              type="button"
              className={BTN}
              onClick={() => {
                void inspectFs(jump, boundId, root, datasetOnly ? datasetId : undefined)
                  .then((value) => {
                    if (!value.exists) {
                      setError(t.workspacePathNotExist);
                      return;
                    }
                    previewVersion.current += 1;
                    setSelected(value); setPreview(null); setError('');
                  })
                  .catch((err: unknown) =>
                    setError(err instanceof Error ? err.message : String(err))
                  );
              }}
            >
              {t.workspaceDetails}
            </button>
          </form>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {listing && <div className="min-w-0 flex-1">
              <ServerPath path={listing.path} />
              <nav aria-label={t.workspaceBreadcrumb} className="flex gap-1 overflow-x-auto whitespace-nowrap text-accent">
                <button onClick={() => setDirectory('.')}>{selectedWorkspace && !registerMode && !datasetOnly ? selectedWorkspace.name : listing.root_path}</button>
                {relativeParts.map((part, index) => <button key={index}
                  onClick={() => setDirectory(relativeParts.slice(0, index + 1).join('/'))}> / {part}</button>)}
              </nav>
            </div>}
            <button
              type="button"
              className={BTN}
              disabled={!listing || !relativeParts.length}
              onClick={() => setDirectory(relativeParts.slice(0, -1).join('/') || '.')}
            >
              {t.workspaceParent}
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              aria-label={t.workspaceFilterAria}
              placeholder={t.workspaceFilterPlaceholder}
              className={`${FIELD} min-w-0 flex-1`}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <select
              aria-label={t.workspaceSortLabel}
              className={FIELD}
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="name">{t.workspaceSortName}</option>
              <option value="modified">{t.workspaceSortModified}</option>
              <option value="size">{t.workspaceSortSize}</option>
            </select>
            <label className="flex items-center gap-1 text-xs">
              <input
                type="checkbox"
                checked={hidden}
                onChange={(e) => setHidden(e.target.checked)}
              />
              {t.workspaceShowHidden}
            </label>
          </div>
          {error && (
            <p
              role="alert"
              className="break-words rounded border border-red-300 p-2 text-xs text-danger dark:border-red-800"
            >
              {error}
            </p>
          )}
          {error.includes('敏感') && boundId && canManage && (
            <button
              type="button"
              className={BTN}
              onClick={() => {
                void requestSensitiveRead(boundId, conversationId, jump)
                  .then(setPending)
                  .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
              }}
            >
              {t.workspaceSensitiveButton}
            </button>
          )}
          {!datasetOnly && !selectionMode && !onPick && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className={BTN}
                disabled={!listing || busy || (registerMode ? locked : !canManage)}
                onClick={() => {
                  setEditor('mkdir');
                  setDestination('');
                  setOverwrite(false);
                  setPermanent(false);
                }}
              >
                {t.workspaceActionMkdir}
              </button>
              <button
                type="button"
                className={BTN}
                hidden={!boundId}
                onClick={() => {
                  if (!boundId) return;
                  void fetchTrash(boundId)
                    .then(setTrash)
                    .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
                }}
              >
                {t.workspaceTrash}
              </button>
              {registerMode && <button className={BTN} disabled={!listing || locked || busy}
                onClick={() => { setEditor('register'); setDestination(listing?.path.split(/[\\/]/).filter(Boolean).at(-1) || t.workspaceTitle); }}>
                {t.workspaceRegisterCurrent}</button>}
              {selectedWorkspace && !registerMode && !onPick && (
                <button
                  type="button"
                  className={BTN}
                  disabled={locked || selecting}
                  onClick={() => setEditor('workspace-remove')}
                >
                  {t.workspaceConfirmRemove}
                </button>
              )}
            </div>
          )}
          {workspaceId && !registerMode && !datasetOnly && !selectionMode && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className={BTN}
                disabled={locked}
                onClick={() => {
                  setEditor('workspace-edit');
                  setDestination(selectedWorkspace?.name ?? '');
                }}
              >
                {t.workspaceRename}
              </button>
            </div>
          )}
          {editor === 'workspace-remove' && selectedWorkspace && !onPick && (
            <div
              role="alertdialog"
              aria-label={t.workspaceConfirmRemove}
              className="space-y-2 rounded border border-line p-2 text-xs "
            >
              <p>{t.workspaceConfirmRemove}</p>
              <p>{selectedWorkspace.name}</p>
              <button
                type="button"
                className={BTN}
                disabled={locked || selecting}
                onClick={() => {
                  setSelecting(true);
                  setError('');
                  void onSelect(null)
                    .then(() => deleteWorkspace(selectedWorkspace.id))
                    .then(async () => {
                      await loadWorkspaces();
                      setRegisterMode(true);
                      setDirectory('.');
                      setTrash(null);
                      setEditor(null);
                    })
                    .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
                    .finally(() => setSelecting(false));
                }}
              >
                {t.workspaceConfirmRemove}
              </button>
              <button
                type="button"
                className={BTN}
                disabled={selecting}
                onClick={() => setEditor(null)}
              >
                {t.cancel}
              </button>
            </div>
          )}
          {editor && editor !== 'workspace-remove' && (
            <form
              className="space-y-2 rounded border border-line p-2 "
              onSubmit={(e) => {
                e.preventDefault();
                void perform();
              }}
            >
              <strong>{editor === 'register' ? t.workspaceRegisterName : editor === 'workspace-edit' ? t.workspaceRename : t[ACTION_LABEL_KEYS[editor]]}</strong>
              {selected && !['mkdir', 'register', 'workspace-edit'].includes(editor) && <ServerPath path={selected.path} />}
              {editor !== 'delete' && <input required
                aria-label={t.workspaceDestinationName}
                className={`${FIELD} w-full`}
                value={destination}
                onChange={(e) => setDestination(e.target.value)}
              />}
              {['copy', 'move', 'rename'].includes(editor) && (
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    checked={overwrite}
                    onChange={(e) => setOverwrite(e.target.checked)}
                  />
                  {t.workspaceOverwrite}
                </label>
              )}
              {editor === 'delete' && (
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    checked={permanent}
                    onChange={(e) => setPermanent(e.target.checked)}
                  />
                  {t.workspacePermanent}
                </label>
              )}
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={busy || selecting || (selectionMode&&!listingFresh) || (['register', 'workspace-edit'].includes(editor) && locked)}
                  className="rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent hover:bg-accent-solid-hover disabled:opacity-50"
                >
                  {selectionMode&&editor==='register' ? t.workspaceRegisterSelect : t.workspaceExecute}
                </button>
                <button type="button" className={BTN} onClick={() => setEditor(null)}>
                  {t.cancel}
                </button>
                {editor === 'workspace-edit' && (
                  <button
                    type="button"
                    className={BTN}
                    onClick={() => setEditor('workspace-remove')}
                  >
                    {t.workspaceConfirmRemove}
                  </button>
                )}
              </div>
            </form>
          )}
          {trash && (
            <div className="space-y-2 rounded border border-line p-2 text-xs ">
              <div className="flex justify-between">
                <strong>{t.workspaceTrash}</strong>
                <button type="button" onClick={() => setTrash(null)}>
                  {t.workspaceClose}
                </button>
              </div>
              {!trash.length && <p>{t.workspaceTrashEmpty}</p>}
              {trash.map((item) => (
                <div key={item.id}>
                  <ServerPath path={item.original_path} />
                  <p>{item.deleted_at}</p>
                  <input
                    className={`${FIELD} my-1 w-full`}
                    aria-label={t.workspaceRestoreTarget}
                    placeholder={item.original_path}
                    value={restorePaths[item.id] ?? ''}
                    onChange={(e) =>
                      setRestorePaths((value) => ({ ...value, [item.id]: e.target.value }))
                    }
                  />
                  <button
                    type="button"
                    className={BTN}
                    onClick={() => {
                      void restoreTrashItem(item.id, conversationId, restorePaths[item.id] || null)
                        .then(setPending)
                        .catch((e: unknown) =>
                          setError(e instanceof Error ? e.message : String(e))
                        );
                    }}
                  >
                    {t.workspaceRestore}
                  </button>
                </div>
              ))}
            </div>
          )}
          {listing?.items.map((item) => (
            <button
              key={item.path}
              className={`mb-1 flex w-full min-w-0 items-start gap-2 rounded-control border p-2 text-left ${
                selected?.path === item.path
                  ? 'border-accent-line bg-accent-quiet dark:border-accent-line '
                  : 'border-transparent hover:bg-hover'
              }`}
              onClick={() => {
                previewVersion.current += 1;
                setSelected(item);
                setPreview(null);
              }}
              onDoubleClick={() => {
                if (item.type === 'directory') setDirectory(item.path);
              }}
              title={item.path}
            >
              <span className="shrink-0">{item.type === 'directory' ? '📁' : '📄'}</span>
              <span className="min-w-0 flex-1">
                <span className="block break-all text-sm">{item.name}</span>
                <span className="block text-meta text-subtle">
                  {item.type === 'directory'
                    ? t.workspaceEntryDirectory
                    : `${item.size.toLocaleString()} B`}{' '}
                  · {new Date(item.modified_at).toLocaleString()} · {item.permissions}
                </span>
              </span>
              {item.type === 'directory' && (
                <span className="shrink-0 text-xs">{t.workspaceDoubleOpen}</span>
              )}
            </button>
          ))}
          {!busy && listing && !listing.items.length && (
            <p className="p-3 text-xs text-subtle">{t.workspaceEmptyDir}</p>
          )}
          {busy && <output className="block p-3 text-xs">{t.workspaceLoadingDir}</output>}
          {listing?.has_more && (
            <button
              type="button"
              className={BTN}
              disabled={busy}
              onClick={() => void load(listing.next_cursor!)}
            >
              {t.workspaceLoadMore}
            </button>
          )}
        </div>
      </div>
      {selected && (
        <div className="max-h-[40vh] shrink-0 space-y-2 overflow-auto border-t border-line p-3 pl-5 text-xs ">
          <ServerPath path={selected.path} label={selected.relative_path} />
          <p>
            {selected.type} · {selected.size.toLocaleString()} B · {selected.permissions} ·{' '}
            {selected.writable ? t.workspaceWritable : t.workspaceReadOnly}
          </p>
          <div className="flex flex-wrap gap-1">
            {selected.type === 'directory' && (
              <button type="button" className={BTN} onClick={() => setDirectory(selected.path)}>
                {t.workspaceOpenDir}
              </button>
            )}
            {onPick && (
              <button
                type="button"
                className="rounded-control bg-accent-solid px-2.5 py-1.5 text-xs font-medium text-on-accent"
                onClick={() => onPick(selected)}
              >
                {t.workspacePickPath}
              </button>
            )}
            {selected.type === 'file' && (boundId || datasetOnly) && (
              <button type="button" className={BTN} onClick={() => void read()}>
                {t.workspaceTextPreview}
              </button>
            )}
            {!registerMode && canManage &&
              !onPick &&
              (['rename', 'copy', 'move', 'delete'] as const).map((action) => (
                <button
                  key={action}
                  type="button"
                  className={BTN}
                  onClick={() => {
                    setEditor(action);
                    setDestination(action === 'rename' ? selected.name : '');
                    setOverwrite(false);
                    setPermanent(false);
                  }}
                >
                  {t[ACTION_LABEL_KEYS[action]]}
                </button>
              ))}
          </div>
          {preview && (
            <>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-well p-2 ">
                {preview.text}
              </pre>
              {preview.truncated && (
                <button type="button" className={BTN} onClick={() => void read(true)}>
                  {t.workspaceReadNext}
                </button>
              )}
            </>
          )}
        </div>
      )}
      {pending && (
        <WorkspaceConfirmation
          key={pending.id}
          operation={pending}
          conversationId={conversationId}
          onDismiss={dismissPending}
          onDone={value => {
            if (value.status !== 'succeeded') return;
            if (registerMode && value.action === 'mkdir' && value.request.root_path === listing?.path) {
              setDirectory(value.source); setFilter('');
            }
            setRefresh(v => v + 1);
            if (trash && boundId) void fetchTrash(boundId).then(setTrash).catch(error => setError(errorText(error, t.errorMessage)));
          }}
        />
      )}
      {selectionMode && <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-line p-3 text-xs">
        <div className="min-w-0 flex-1">{listing && <ServerPath path={listing.path}/>}</div>
        <button type="button" className={BTN} disabled={!listing||!listingFresh||busy||locked||selecting||!!editor}
          onClick={()=>{setEditor('mkdir');setDestination('');setOverwrite(false);setPermanent(false);}}>{t.workspaceActionMkdir}</button>
        <button type="button" className={BTN} disabled={!listing||!listingFresh||busy||locked||selecting||!!error||!!editor}
          onClick={()=>{
            if(!listing)return;
            if(!matchingWorkspace){setEditor('register');setDestination(listing.path.split('/').filter(Boolean).at(-1)||t.workspaceTitle);return;}
            setSelecting(true);setError('');
            void onSelect(matchingWorkspace).then(onClose).catch(e=>setError(errorText(e,t.errorMessage))).finally(()=>setSelecting(false));
          }}>{matchingWorkspace ? t.workspacePickPath : t.workspaceRegisterCurrent}</button>
      </div>}
      </div>
    </dialog>
  );
}
