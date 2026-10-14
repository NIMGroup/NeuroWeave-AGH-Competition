import { ui } from '../../lib/libraryText';
import {
  displayedInputSlots,
  activeOutputSlots
} from '../workflows/workflowConditions';
import type { Pipeline } from './libraryApi';

/** Validation occupies its own grid row; it cannot cover or overflow the canvas. */
export function PipelineValidationPanel({
  preview,
  showConditionalInputs
}: {
  preview: Pipeline;
  showConditionalInputs: boolean;
}) {
  return (
    <details
      open
      className="min-h-0 max-h-[25dvh] shrink-0 overflow-auto rounded border border-line bg-raised text-xs"
      aria-label={ui('校验结果')}
    >
      <summary className="sticky top-0 cursor-pointer border-b border-line bg-raised px-3 py-2 font-medium">
        {ui('整体接口 · 已校验')} · {ui('点击收起或展开')}
      </summary>
      <div className="grid gap-3 p-3 sm:grid-cols-2">
        <div>
          <h3 className="mb-1 font-semibold">{ui('输入')}</h3>
          {displayedInputSlots(preview, {}, {}, showConditionalInputs).map(
            (s) => (
              <p key={s.id} className="break-words">
                {s.label} <span className="text-subtle">[{s.type}]</span>
              </p>
            )
          )}
        </div>
        <div>
          <h3 className="mb-1 font-semibold">{ui('输出')}</h3>
          {activeOutputSlots(preview, {}).map((s) => (
            <p key={s.id} className="break-words">
              {s.label} <span className="text-subtle">[{s.type}]</span>
            </p>
          ))}
        </div>
        <p className="break-words sm:col-span-2">
          {ui('执行顺序：')}
          {preview.steps.map((s) => s.title || s.method_ref).join(' → ')}
        </p>
      </div>
    </details>
  );
}
