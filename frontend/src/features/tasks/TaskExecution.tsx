import { Check, Circle, Loader2, X } from 'lucide-react';
import type { AgentTask, TaskStepStatus } from '../../types/agent';
import { getTranslations } from '../../lib/i18n';
import { useLanguageStore } from '../../stores/languageStore';

type TaskExecutionProps = {
  task: AgentTask;
};

function StepIcon({ status }: { status: TaskStepStatus }) {
  if (status === 'completed') {
    return (
      <div className="flex h-6 w-6 items-center justify-center rounded-full bg-well text-on-accent dark:bg-raised ">
        <Check className="h-3.5 w-3.5" />
      </div>
    );
  }

  if (status === 'running') {
    return (
      <div className="flex h-6 w-6 items-center justify-center rounded-full border border-line-strong ">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div className="flex h-6 w-6 items-center justify-center rounded-full bg-red-500 text-on-accent">
        <X className="h-3.5 w-3.5" />
      </div>
    );
  }

  return (
    <div className="flex h-6 w-6 items-center justify-center">
      <Circle className="h-4 w-4 text-line-strong" />
    </div>
  );
}

export function TaskExecution({ task }: TaskExecutionProps) {
  const language = useLanguageStore((state) => state.language);

  const t = getTranslations(language);

  const statusLabels: Record<AgentTask['status'], string> = {
    planning: t.planning,
    running: t.running,
    completed: t.completed,
    failed: t.failed
  };

  return (
    <div className="mb-6 rounded-card border border-line bg-raised p-4 shadow-sm ">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold">{task.title}</p>

          <p className="mt-1 text-xs text-subtle">{t.agentExecution}</p>
        </div>

        <span className="rounded-full bg-well px-2.5 py-1 text-meta font-medium capitalize text-muted ">
          {statusLabels[task.status]}
        </span>
      </div>

      <div className="space-y-4">
        {task.steps.map((step, index) => (
          <div key={step.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <StepIcon status={step.status} />

              {index < task.steps.length - 1 && (
                <div className="mt-1 h-full min-h-5 w-px bg-well " />
              )}
            </div>

            <div className="min-w-0 pb-1">
              <p className="text-sm font-medium">{step.title}</p>

              <p className="mt-1 text-xs leading-5 text-subtle">{step.description}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
