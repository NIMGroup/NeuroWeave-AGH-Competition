import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Eye, Loader2 } from 'lucide-react';
import { request } from '../../api/http';
import { useLanguageStore } from '../../stores/languageStore';
import { Button } from '../../ui/Button';
import { OutputPreviewContent } from './OutputPreviewContent';
import type { OutputPreview } from './outputPreviewTypes';
import type { ExecutionOptions, Workflow } from './workflowTypes';

function PreviewBody({ workflow, execution }: { workflow: Workflow; execution: ExecutionOptions }) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ['workflow-output-preview', workflow.workflow_id, workflow.revision, execution, offset],
    queryFn: ({ signal }) => request<OutputPreview>(
      `/api/workflows/${encodeURIComponent(workflow.workflow_id)}/output-preview`,
      { method: 'POST', signal, body: JSON.stringify({ expected_revision: workflow.revision, execution, offset, limit: 2 }) }),
    staleTime: 30_000, retry: false
  });
  // Query keys keep responses from older settings/revisions out of this preview.
  const preview = query.data;
  return <div className="mt-3 space-y-3">
    <p className="text-micro text-muted">{zh
      ? '根据当前方案和运行设置展示预计输出。预览不会执行任务或创建文件；{new_run_id} 是点击运行后分配的新运行目录编号。'
      : 'Expected outputs for the current plan and run settings. Preview does not execute tasks or create files; {new_run_id} is assigned after Run.'}</p>
    {query.isFetching && <p role="status" className="flex items-center gap-2 text-meta text-muted"><Loader2 className="size-3.5 animate-spin" aria-hidden="true" />{zh ? '正在读取输出规则…' : 'Reading output rules…'}</p>}
    {query.error && <div role="alert" className="space-y-2 text-meta text-danger"><p>{query.error.message}</p><Button size="sm" onClick={() => void query.refetch()}>{zh ? '重试预览' : 'Retry preview'}</Button></div>}
    {preview && <>
      <OutputPreviewContent preview={preview} />
      {preview.sample_count > 2 && <div className="flex flex-wrap items-center gap-3 text-meta">
        <Button size="sm" disabled={!offset || query.isFetching} onClick={() => setOffset(previous => Math.max(0, previous - 2))}>{zh ? '上一组' : 'Previous'}</Button>
        <span className="text-muted">{offset + 1}–{Math.min(offset + preview.groups.length, preview.sample_count)} / {preview.sample_count}</span>
        <Button size="sm" disabled={preview.next_offset === null || query.isFetching} onClick={() => setOffset(preview.next_offset ?? offset)}>{zh ? '下一组样本' : 'Next samples'}</Button>
      </div>}
      <p className="text-micro text-subtle">{zh ? '显示各步骤预计生成的主要文件；日志和辅助文件可能另行产生。文件内容执行后才能生成，运行条件和覆盖冲突仍需校验。'
        : 'Shows declared outputs; logs and internal files may be additional. Scientific content is generated during execution. Execution validation still checks readiness and conflicts.'}</p>
    </>}
  </div>;
}

export function WorkflowOutputPreview({ workflow, execution }: { workflow: Workflow; execution: ExecutionOptions }) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const [open, setOpen] = useState(false);
  const settingsKey = JSON.stringify([workflow.workflow_id, workflow.revision, execution]);
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="chat-disclosure mt-3 min-w-0 rounded-control border border-line p-3">
    <summary className="text-meta font-medium text-ink"><Eye className="mr-2 inline size-3.5 text-accent" aria-hidden="true" />{zh ? '执行前输出预览' : 'Output preview before Run'}</summary>
    {open && <PreviewBody key={settingsKey} workflow={workflow} execution={execution} />}
  </details>;
}
