import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ArrowUp, File, Folder, Info, RefreshCw } from 'lucide-react';
import type { Dataset } from './datasetsApi';
import type { FsEntry } from '../workspace/workspaceTypes';
import { listFs, inspectFs, readFsText } from '../workspace/workspaceApi';
import { ServerPath } from '../workspace/ServerPath';
import { Dialog } from '../../ui/Dialog';
import { Button } from '../../ui/Button';
import { IconButton } from '../../ui/IconButton';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';

const FIELD = 'min-w-0 rounded-control border border-line bg-raised px-2 py-1.5 text-meta';
const binaryImage = (path: string) => /\.(nii(\.gz)?|mgz|mgh|mif(\.gz)?|dcm|dicom|ima|tck|trk|h5|hdf5|gz|zip|png|jpe?g|gif|pdf)$/i.test(path);
const parentOf = (path: string) => path.split(/[\\/]/).filter(Boolean).slice(0, -1).join('/') || '.';

/** Dataset-only navigation uses the existing scoped read APIs; no write controls. */
export function DatasetFilesDrawer({ dataset, onClose }: { dataset: Dataset; onClose: () => void }) {
  const t = getTranslations(useLanguageStore(s => s.language));
  const client = useQueryClient();
  const [history, setHistory] = useState(['.']);
  const directory = history.at(-1)!;
  const [pathDraft, setPathDraft] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('name');
  const [hidden, setHidden] = useState(false);
  const [selected, setSelected] = useState<FsEntry | null>(null);
  const [jumpError, setJumpError] = useState('');
  const [jumping, setJumping] = useState(false);
  const jumpRequest = useRef<AbortController | null>(null);
  const viewport = useRef<HTMLDivElement | null>(null);
  const savedScroll = useRef(0);
  useEffect(() => {
    const timer = setTimeout(() => setSearch(filter), 200);
    return () => clearTimeout(timer);
  }, [filter]);
  useEffect(() => () => jumpRequest.current?.abort(), []);
  const queryKey = ['dataset-files', dataset.dataset_id, directory, search, sort, hidden];
  const listing = useInfiniteQuery({ queryKey,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => listFs({datasetId:dataset.dataset_id,path:directory,
      filter:search,sort,showHidden:hidden,cursor:pageParam ?? undefined,signal}),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
    staleTime: 10_000, retry: 1 });
  const first = listing.data?.pages[0];
  const items = listing.data?.pages.flatMap(page => page.items) ?? [];
  const parts = first?.relative_path === '.' ? [] : first?.relative_path.split(/[\\/]/).filter(Boolean) ?? [];
  const clearNavigation = () => {
    jumpRequest.current?.abort();
    setJumping(false); setJumpError(''); setPathDraft(null);
    setSelected(null); setFilter(''); setSearch(''); savedScroll.current = 0;
    if (viewport.current) viewport.current.scrollTop = 0;
  };
  const navigate = (path: string, file: FsEntry | null = null) => {
    clearNavigation();
    setHistory(previous => previous.at(-1) === path ? previous : [...previous, path]);
    setSelected(file);
  };
  const openDetails = (entry: FsEntry) => {
    jumpRequest.current?.abort(); setJumping(false); setJumpError('');
    savedScroll.current = viewport.current?.scrollTop ?? 0;
    setSelected(entry);
  };
  const jump = async () => {
    const target = (pathDraft ?? first?.path ?? directory).trim();
    if (!target) return;
    jumpRequest.current?.abort();
    const controller = new AbortController(); jumpRequest.current = controller;
    setJumping(true); setJumpError('');
    try {
      const entry = await inspectFs(target, null, undefined, dataset.dataset_id, controller.signal);
      if (controller.signal.aborted) return;
      if (!entry.exists) throw new Error(t.workspacePathNotExist);
      // Absolute file paths land in their containing directory and open metadata.
      if (entry.type === 'directory') navigate(entry.relative_path);
      else navigate(parentOf(entry.relative_path), entry);
    } catch (error) {
      if (!controller.signal.aborted) setJumpError(error instanceof Error ? error.message : String(error));
    } finally { if (jumpRequest.current === controller) setJumping(false); }
  };
  const more = () => {
    if (listing.hasNextPage && !listing.isFetching) void listing.fetchNextPage();
  };
  return <Dialog title={t.datasetFilesTitle} description={dataset.display_name}
    placement="right" resizeLabel={t.datasetResize} closeLabel={t.workspaceClose} onClose={onClose}
    contentClassName="flex min-h-0 flex-1 flex-col overflow-hidden">
    <div hidden={!!selected} className="min-h-0 flex-1 flex-col" style={{display:selected ? 'none' : 'flex'}}>
      <div className="shrink-0 space-y-2 border-b border-line p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={history.length < 2} onClick={() => {
            clearNavigation(); setHistory(previous => previous.slice(0, -1));
          }}><ArrowLeft aria-hidden="true"/>{t.datasetBack}</Button>
          <Button size="sm" disabled={!parts.length} onClick={() => navigate(parentOf(first!.relative_path))}>
            <ArrowUp aria-hidden="true"/>{t.workspaceParent}</Button>
          <Button size="sm" loading={listing.isFetching && !listing.isFetchingNextPage} onClick={() => {
            setJumpError(''); void client.resetQueries({queryKey,exact:true});
          }}><RefreshCw aria-hidden="true"/>{t.workspaceRefresh}</Button>
        </div>
        <form className="flex min-w-0 gap-2" onSubmit={event => {event.preventDefault(); void jump();}}>
          <input className={`${FIELD} flex-1`} aria-label={t.workspaceJumpAria}
            value={pathDraft ?? first?.path ?? dataset.absolute_path} onChange={event => setPathDraft(event.target.value)}/>
          <Button size="sm" type="submit" loading={jumping}>{t.workspaceJump}</Button>
        </form>
        <nav aria-label={t.workspaceBreadcrumb} className="flex gap-1 overflow-x-auto whitespace-nowrap text-meta text-accent">
          <button type="button" title={dataset.absolute_path} onClick={() => navigate('.')}>{dataset.display_name}</button>
          {parts.map((part,index) => <button type="button" key={index} aria-current={index===parts.length-1?'location':undefined}
            onClick={() => navigate(parts.slice(0,index+1).join('/'))}> / {part}</button>)}
        </nav>
        <div className="flex flex-wrap items-center gap-2">
          <input className={`${FIELD} min-w-24 flex-1`} aria-label={t.workspaceFilterAria} placeholder={t.workspaceFilterPlaceholder}
            value={filter} onChange={event => setFilter(event.target.value)}/>
          <select className={FIELD} aria-label={t.workspaceSortLabel} value={sort} onChange={event => setSort(event.target.value)}>
            <option value="name">{t.workspaceSortName}</option><option value="modified">{t.workspaceSortModified}</option><option value="size">{t.workspaceSortSize}</option>
          </select>
          <label className="flex items-center gap-1 text-meta"><input type="checkbox" checked={hidden} onChange={event=>setHidden(event.target.checked)}/>{t.workspaceShowHidden}</label>
        </div>
        <p className="text-micro text-subtle">{t.datasetNavigationHint}</p>
        {jumpError && <p role="alert" className="break-words text-meta text-danger">{jumpError}</p>}
      </div>
      <div ref={viewport} className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2" aria-label={t.datasetFileList}
        onScroll={event => {
          const node=event.currentTarget;
          if (!listing.isError && node.scrollHeight-node.scrollTop-node.clientHeight<100) more();
        }}>
        {items.map(item => <div key={item.path} className="flex min-w-0 items-center rounded-control hover:bg-hover">
          <button type="button" className="flex min-w-0 flex-1 items-start gap-2 p-2 text-left focus-ring-inset"
            title={item.path} onClick={() => item.type==='directory' ? navigate(item.relative_path) : openDetails(item)}>
            {item.type==='directory' ? <Folder className="mt-1 size-4 shrink-0 text-accent"/> : <File className="mt-1 size-4 shrink-0 text-subtle"/>}
            <span className="min-w-0 flex-1"><span className="block break-all text-meta font-medium">{item.name}</span>
              <span className="mt-1 block text-micro text-subtle">{item.type==='directory' ? t.workspaceEntryDirectory : formatSize(item.size)} · {new Date(item.modified_at).toLocaleString()} · {item.permissions}</span>
            </span>
          </button>
          <IconButton label={`${t.workspaceDetails}: ${item.name}`} icon={<Info/>} onClick={()=>openDetails(item)}/>
        </div>)}
        {listing.isError && <div role="alert" className="space-y-2 p-2 text-meta text-danger">
          <p>{listing.error.message}</p><Button size="sm" onClick={() => listing.isFetchNextPageError ? more() : void listing.refetch()}>{t.retry}</Button>
        </div>}
        {!listing.isPending && !listing.isError && !items.length && <p className="p-3 text-meta text-subtle">{search ? t.datasetNoMatches : t.workspaceEmptyDir}</p>}
        {listing.isFetching && <p role="status" className="p-3 text-meta text-subtle">{t.workspaceLoadingDir}</p>}
        {listing.hasNextPage && <Button size="sm" fullWidth loading={listing.isFetchingNextPage} disabled={listing.isFetching} onClick={more}>{t.workspaceLoadMore}</Button>}
      </div>
      <div className="shrink-0 border-t border-line px-3 py-2 text-micro text-muted">{t.datasetLoadedItems.replace('{{count}}',String(items.length))} · {t.workspaceReadOnly}</div>
    </div>
    {selected && <DatasetFileDetails key={selected.path} datasetId={dataset.dataset_id} entry={selected}
      onOpen={() => navigate(selected.relative_path)} onBack={() => {
        setSelected(null); requestAnimationFrame(() => {if(viewport.current) viewport.current.scrollTop=savedScroll.current;});
      }}/>}
  </Dialog>;
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024*1024) return `${(bytes/1024).toFixed(1)} KB`;
  if (bytes < 1024*1024*1024) return `${(bytes/1024/1024).toFixed(1)} MB`;
  return `${(bytes/1024/1024/1024).toFixed(1)} GB`;
}

function DatasetFileDetails({datasetId,entry,onBack,onOpen}:{datasetId:string;entry:FsEntry;onBack:()=>void;onOpen:()=>void}) {
  const t=getTranslations(useLanguageStore(s=>s.language));
  const [previewOpen,setPreviewOpen]=useState(false);
  const [offsets,setOffsets]=useState([0]);
  const query=useQuery({queryKey:['dataset-file',datasetId,entry.path],
    queryFn:({signal})=>inspectFs(entry.path,null,undefined,datasetId,signal),retry:1});
  const file=query.data ?? entry;
  const canPreview=file.type==='file' && file.readable && !binaryImage(file.path) && !query.isError && query.data?.exists !== false;
  const preview=useQuery({queryKey:['dataset-text',datasetId,entry.path,offsets.at(-1)],
    queryFn:({signal})=>readFsText(null,entry.path,offsets.at(-1),datasetId,signal),enabled:previewOpen && canPreview,retry:false});
  return <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3">
    <Button size="sm" onClick={onBack}><ArrowLeft aria-hidden="true"/>{t.datasetBackToList}</Button>
    <h3 className="mt-3 break-all text-body font-semibold">{file.name}</h3>
    <ServerPath key={file.path} path={file.path}/>
    <dl className="my-3 grid grid-cols-2 gap-2 text-meta">
      <dt className="text-subtle">{t.datasetFileType}</dt><dd>{file.type==='directory'?t.workspaceEntryDirectory:t.datasetTypeFile}</dd>
      <dt className="text-subtle">{t.datasetFileSize}</dt><dd>{formatSize(file.size)}</dd>
      <dt className="text-subtle">{t.datasetFileModified}</dt><dd>{new Date(file.modified_at).toLocaleString()}</dd>
      <dt className="text-subtle">{t.datasetFilePermissions}</dt><dd>{file.permissions} · {t.workspaceReadOnly}</dd>
    </dl>
    <p className="mb-3 text-meta text-muted">{t.datasetFilesHint}</p>
    {query.isError && <p role="alert" className="text-meta text-danger">{query.error.message}</p>}
    {query.data?.exists === false && <p role="alert" className="text-meta text-danger">{t.workspacePathNotExist}</p>}
    {file.type==='directory' && <Button onClick={onOpen}>{t.workspaceOpenDir}</Button>}
    {file.type==='file' && binaryImage(file.path) && <p className="rounded-control bg-well p-3 text-meta text-muted">{t.datasetBinaryHint}</p>}
    {canPreview && !previewOpen && <Button onClick={()=>setPreviewOpen(true)}>{t.workspaceTextPreview}</Button>}
    {previewOpen && canPreview && <section aria-label={t.workspaceTextPreview} className="space-y-3">
      <div className="flex flex-wrap gap-2"><Button size="sm" onClick={()=>setPreviewOpen(false)}>{t.datasetClosePreview}</Button>
        <Button size="sm" disabled={offsets.length<2 || preview.isFetching} onClick={()=>setOffsets(previous=>previous.slice(0,-1))}>{t.runPaginationPrevious}</Button>
        <Button size="sm" disabled={!preview.data?.truncated || preview.isFetching} onClick={()=>setOffsets(previous=>[...previous,preview.data!.next_offset])}>{t.workspaceReadNext}</Button>
      </div>
      {preview.isPending && <p role="status">{t.datasetPreviewLoading}</p>}
      {preview.isError && <div role="alert" className="space-y-2 text-meta text-danger"><p>{preview.error.message}</p><Button size="sm" onClick={()=>void preview.refetch()}>{t.retry}</Button></div>}
      {preview.data && <><pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-control border border-line bg-well p-3 text-meta">{preview.data.text}</pre>
        <p className="text-micro text-muted">{preview.data.truncated ? t.workspacePreviewTruncated : t.datasetPreviewEnd}</p></>}
    </section>}
  </div>;
}
