import { useLanguageStore } from '../../stores/languageStore';
import { ArrowDown, BookOpenText, Network, ShieldCheck } from 'lucide-react';
export type CapabilityRecommendationItem = {
  capability_id: string;
  label: string;
  description: string;
  execution_level: 'advisory' | 'planning' | 'structured';
  skill_ids: string[];
  tree_path: string[];
};
export type CapabilityRecommendation = {
  title: string;
  summary: string;
  plan_kind: 'atomic' | 'composite';
  fully_executable: boolean;
  items: CapabilityRecommendationItem[];
  notice: string;
};
type Props = { recommendation: CapabilityRecommendation };
export function CapabilityRecommendationCard({ recommendation }: Props) {
  const zh = useLanguageStore(s => s.language) === 'zh';
  return (
    <div className="mt-3 min-w-0 rounded-card border border-warning-line bg-warning-quiet">
      {' '}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-warning-line px-3 py-2.5">
        {' '}
        <div className="flex min-w-0 items-center gap-2">
          {' '}
          <Network className="size-4 shrink-0 text-warning" />{' '}
          <span className="truncate text-sm font-medium text-warning">
            {' '}
            {recommendation.title}{' '}
          </span>{' '}
        </div>{' '}
        <span className="rounded-chip bg-warning-quiet px-1.5 py-0.5 text-micro font-medium text-warning">
          {' '}
          {zh ? '领域指导' : 'Domain guidance'}{' '}
        </span>{' '}
      </div>{' '}
      <div className="space-y-2 px-3 py-3">
        {' '}
        {recommendation.items.map((item, index) => (
          <div key={item.capability_id} className="min-w-0">
            {' '}
            <div className="flex items-start gap-2 rounded-control border border-warning-line bg-raised/60 p-2.5">
              {' '}
              <span className="grid size-6 shrink-0 place-items-center rounded-full bg-warning-quiet text-meta font-semibold text-warning ">
                {' '}
                {index + 1}{' '}
              </span>{' '}
              <div className="min-w-0 flex-1">
                {' '}
                <div className="flex flex-wrap items-center gap-1.5">
                  {' '}
                  <p className="truncate text-sm font-medium text-ink">{item.label}</p>{' '}
                  <span
                    className={`rounded px-1.5 py-0.5 text-micro font-medium ${item.execution_level === 'structured' ? 'bg-success-quiet text-success' : item.execution_level === 'planning' ? 'bg-warning-quiet text-warning ' : 'bg-well text-muted dark:text-subtle'}`}
                  >
                    {' '}
                    {item.execution_level === 'structured'
                      ? (zh ? '已登记执行方法' : 'Registered method')
                      : item.execution_level === 'planning'
                        ? (zh ? '规划指导' : 'Planning guidance')
                        : (zh ? '尚无执行方法' : 'No executable method')}{' '}
                  </span>{' '}
                </div>{' '}
                <p className="mt-1 text-xs leading-5 text-muted dark:text-subtle">
                  {' '}
                  {item.description}{' '}
                </p>{' '}
                <div className="mt-2 flex flex-wrap gap-1">
                  {' '}
                  {item.skill_ids.map((skillId) => (
                    <span
                      key={skillId}
                      className="inline-flex items-center gap-1 rounded-chip bg-warning-quiet px-2 py-1 font-mono text-micro text-warning dark:text-warning"
                    >
                      {' '}
                      <BookOpenText className="size-3" /> {skillId}{' '}
                    </span>
                  ))}{' '}
                </div>{' '}
              </div>{' '}
            </div>{' '}
            {index < recommendation.items.length - 1 && (
              <div className="grid h-5 place-items-center text-subtle">
                {' '}
                <ArrowDown className="size-3.5" />{' '}
              </div>
            )}{' '}
          </div>
        ))}{' '}
        <div className="flex items-start gap-2 rounded-control border border-warning-line bg-raised/60 px-3 py-2 text-meta leading-5 text-warning">
          {' '}
          <ShieldCheck className="mt-0.5 size-3.5 shrink-0" /> <p>{recommendation.notice}</p>{' '}
        </div>{' '}
      </div>{' '}
    </div>
  );
}
