import type { DataOperation } from './dataTypes';

export type ProposalState = 'active' | 'historical' | 'expired' | 'reviewing' | 'revise' | 'clarify' | 'unavailable' | 'ready' | 'blocked';

/** Run is available only for the current, unblocked, hash-bound proposal. */
export function proposalState(task: DataOperation): ProposalState {
  if (task.status !== 'pending') return 'active';
  const review = task.quality?.proposal_review;
  if (review?.superseded_by && review.superseded_by !== task.id) return 'historical';
  if (task.preview_expired) return 'expired';
  if (review?.decision === 'pending') return 'reviewing';
  if (review?.decision === 'clarify') return 'clarify';
  if (review?.decision === 'unavailable') return 'unavailable';
  if (review?.decision === 'revise' || task.quality?.blocking_issues.length || task.conflicts.length) return 'revise';
  const script = task.phase === 'script' && task.manifest_ready === false;
  const hash = script ? task.script_plan_hash : task.manifest_hash;
  // Generated files retain the review of the exact script that produced them.
  // Their separate Run request still binds to the current manifest hash.
  const generated = task.phase === 'files' && task.manifest_ready !== false && task.script_plan_hash;
  if (review && (!hash || (review.plan_hash !== hash && (!generated || review.plan_hash !== task.script_plan_hash)))) return 'unavailable';
  return hash && (script ? task.can_run_script : task.can_confirm) ? 'ready' : 'blocked';
}

export function proposalFixes(task: DataOperation): { area: string; change: string }[] {
  const review = task.quality?.proposal_review;
  if (review?.fixes?.length) return review.fixes;
  // Older records retain their full evidence in the disclosure below the summary.
  return (review?.issues.length ? review.issues : task.quality?.blocking_issues ?? []).map(issue => {
    const sentence = issue.split(/[。；\n]/)[0];
    return { area: '', change: sentence.length > 110 ? sentence.slice(0, 110) + '…' : sentence };
  });
}

export function proposalRevisionRequest(task: DataOperation, zh: boolean): string {
  const verify = ['unavailable', 'reviewing'].includes(proposalState(task));
  return zh
    ? `请继续处理待运行方案 ${task.id}（${task.title}），${verify ? '重新核验现有方案' : '根据检查问题修订并生成新的待运行版本'}。沿用已确认的目标、范围和条件，代码改动由你完成；不要执行任务，等待我点击运行。`
    : `Continue proposal ${task.id} (${task.title}): ${verify ? 'verify the existing proposal again' : 'resolve its review issues and prepare a new version'}. Keep the accepted goal, scope and constraints. Make any code changes yourself. Do not execute; wait for my Run click.`;
}

export function proposalContinuationRequest(task: DataOperation, zh: boolean): string {
  return zh
    ? `请继续完成此前目标。任务 ${task.id}（${task.title}）已完成分析，原文件尚未修改。读取并复用这次结果，核对它是否已满足原目标；若还需要文件操作，请沿用已确认条件准备完整的可执行操作预览，不重复已完成的分析，不把模型建议当作新增要求。代码由你处理，最终修改等待我点击运行。`
    : `Continue the original goal using the completed analysis from ${task.id} (${task.title}). Original files have not changed. Reuse its results and check what remains; if file changes are required, prepare a complete executable preview using the accepted requirements. Do not repeat the analysis or add requirements from model suggestions. Wait for my Run click before applying changes.`;
}
