import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import type { Dataset } from '../datasets/datasetsApi';
import { useWorkflowPathPreview } from './useWorkflows';
import { fetchSavedInputRules } from './workflowsApi';
import type { InputBinding, InputSelectionMode } from './workflowTypes';
import { useLanguageStore } from '../../stores/languageStore';
import { getTranslations } from '../../lib/i18n';

export function InputRuleEditor({workflowId, stepId, slotId, dataset, binding, disabled, onSelected}: {
  workflowId: string; stepId: string; slotId: string; dataset: Dataset | null;
  binding?: InputBinding; disabled: boolean;
  onSelected: (rootId: string, relativePath: string, mode?: InputSelectionMode) => void;
}) {
  const t = getTranslations(useLanguageStore(s => s.language));
  const [open, setOpen] = useState(false);
  const [rule, setRule] = useState(binding?.path_template ?? '');
  const [mode, setMode] = useState<InputSelectionMode>(binding?.binding_mode === 'single' ? 'single' : 'template');
  const preview = useWorkflowPathPreview(workflowId, stepId, slotId);
  const saved = useQuery({queryKey:['workflow-input-rules', workflowId, stepId, slotId, dataset?.dataset_id],
    enabled:open && Boolean(dataset), queryFn:({signal}) => fetchSavedInputRules(workflowId, stepId, slotId, signal), retry:1});
  const prefix = (dataset?.relative_path ?? '').replace(/\\/g, '/').replace(/^\.\/?/, '').replace(/\/$/, '');
  const relative = prefix ? prefix + '/' + rule : rule;
  // An older response may finish after editing. It cannot approve the new rule.
  const current = preview.variables?.relativePath === relative && preview.variables.bindingMode === mode;
  const result = current ? preview.data : undefined;
  return <div className="min-w-0 w-full">
    <button type="button" disabled={disabled || !dataset} onClick={() => setOpen(v => !v)}
      aria-expanded={open} className="rounded-control border border-line px-2.5 py-1.5 text-xs disabled:opacity-50">
      {t.workflowInputRuleTitle}
    </button>
    {open && dataset && <div className="mt-2 space-y-2 rounded-control border border-line p-3">
      <p className="text-meta leading-5 text-subtle">{t.workflowInputRuleHint}</p>
      <select aria-label={t.workflowInputRuleTitle} value={mode} disabled={disabled}
        onChange={e => {setMode(e.target.value as InputSelectionMode); preview.reset();}}
        className="w-full rounded-control border border-line bg-transparent p-2 text-xs">
        <option value="template">{t.workflowInputRuleBatch}</option>
        <option value="single">{t.workflowInputRuleShared}</option>
      </select>
      <input aria-label={t.workflowInputRuleTitle} value={rule} disabled={disabled}
        onChange={e => {setRule(e.target.value); preview.reset();}}
        placeholder="{sample}/dwi/image.mif"
        className="w-full min-w-0 rounded-control border border-line bg-transparent p-2 font-mono text-xs" />
      <button type="button" disabled={disabled || !rule.trim() || preview.isPending}
        onClick={() => preview.mutate({rootId:dataset.root_id, relativePath:relative, bindingMode:mode})}
        className="inline-flex items-center gap-1 rounded-control border border-line px-2.5 py-1.5 text-xs disabled:opacity-50">
        {preview.isPending && <Loader2 className="size-3 animate-spin" />}{t.workflowInputRulePreview}
      </button>
      {result && <div className="space-y-2 rounded-control bg-well p-2 text-meta">
        <p>{t.workflowInputRulePendingCount.replace('{{count}}', String(result.coverage.total))}</p>
        <ul className="space-y-1 font-mono text-micro">{result.matches.slice(0,8).map(m =>
          <li key={m.sample_id} className="break-all">{m.relative_path}</li>)}</ul>
        <button type="button" disabled={disabled || !result.can_apply}
          onClick={() => onSelected(dataset.root_id, relative, mode)}
          className="rounded-control bg-accent-solid px-2.5 py-1.5 text-xs text-on-accent disabled:opacity-50">
          {t.workflowInputRuleApply}
        </button>
      </div>}
      {preview.error && <p role="alert" className="break-words text-meta text-danger">{preview.error.message}</p>}
      <p className="pt-1 text-meta font-medium">{t.workflowInputRuleReuse}</p>
      {saved.isPending ? <Loader2 className="size-3 animate-spin" /> : saved.error ?
        <p role="alert" className="text-meta text-danger">{saved.error.message}</p> :
        saved.data?.rules.length ? <ul className="space-y-1">{saved.data.rules.map(r =>
          <li key={r.id} className="flex min-w-0 items-start justify-between gap-2">
            <span className="min-w-0 break-all font-mono text-micro">{r.path_template}</span>
            <button type="button" disabled={disabled} onClick={() => {setRule(r.path_template); setMode(r.binding_mode);
              preview.mutate({rootId:r.root_id, relativePath:r.relative_path, bindingMode:r.binding_mode});}}
              className="shrink-0 rounded-control border border-line px-2 py-1 text-xs">{t.workflowInputRulePreview}</button>
          </li>)}</ul> : <p className="text-meta text-subtle">{t.workflowInputRuleNoSaved}</p>}
    </div>}
  </div>;
}
