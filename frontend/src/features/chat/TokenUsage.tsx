import { useLanguageStore } from '../../stores/languageStore';

export type TokenUsageSummary = {
  calls: number;
  reported_calls: number;
  unavailable_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  reasoning_tokens: number | null;
  cached_input_tokens: number | null;
  complete: boolean;
};

export function TokenUsage({ usage, conversation = false }: {
  usage?: TokenUsageSummary | null;
  conversation?: boolean;
}) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const format = (value: number | null | undefined) => value == null
    ? (zh ? '未记录' : 'Not recorded') : value.toLocaleString(zh ? 'zh-CN' : 'en-US');
  return <details className="chat-disclosure min-w-0 text-micro text-subtle">
    <summary className="cursor-pointer">
      {conversation ? (zh ? '会话 Token' : 'Conversation tokens') : 'Token'} {format(usage?.total_tokens)}
      {Boolean(usage?.calls && !usage.complete) && <span> · {zh ? '部分统计' : 'Partial'}</span>}
    </summary>
    <div className="mt-1 space-y-1">
      <p>{zh ? '输入' : 'Input'} {format(usage?.input_tokens)} · {zh ? '输出' : 'Output'} {format(usage?.output_tokens)}</p>
      <p>{zh ? '缓存命中（包含在输入中）' : 'Cached input (included in input)'} {format(usage?.cached_input_tokens)}</p>
      <p>{zh ? '推理（包含在输出中）' : 'Reasoning (included in output)'} {format(usage?.reasoning_tokens)}</p>
      {usage && <p>{zh ? '模型调用' : 'Model calls'} {usage.calls}
        {usage.unavailable_calls > 0 && <> · {usage.unavailable_calls} {zh ? '次用量未完整返回' : 'without complete usage'}</>}
      </p>}
      <p>{zh
        ? '按接口实际返回的用量累计，包含重试、工具循环和记忆整理。未返回的项目显示为未记录；子项只累计接口已报告的数值。'
        : 'Provider-reported usage includes retries, tool loops and memory compaction. Missing values are not recorded; detail totals include reported values only.'}</p>
      {conversation && <p>{zh ? '功能启用前的历史用量无法补记。' : 'Historical usage before tracking was enabled cannot be recovered.'}</p>}
    </div>
  </details>;
}
