import { useLanguageStore } from '../../stores/languageStore';

export type Verification = {
  contract: {status: 'passed' | 'invalid'; errors: string[]};
  documentation: {status: 'complete' | 'incomplete'; missing: string[]};
  execution: {status: 'not_recorded' | 'stale' | 'invalid' | 'passed' | 'failed'; reason?: string;
    test_type?: 'synthetic' | 'real_dataset'; tested_at?: string; environment?: string; report_path?: string};
};

export function MethodVerification({value}: {value?: Verification}) {
  const zh = useLanguageStore(s => s.language) === 'zh';
  if (!value) return null;
  const run = value.execution;
  const labels = {not_recorded: zh ? '未记录当前版本运行验证' : 'No execution evidence for this version',
    stale: zh ? '历史证据已过期，需重新验证' : 'Stale evidence; rerun required',
    invalid: zh ? '证据无效' : 'Invalid evidence',
    passed: zh ? '运行测试通过' : 'Execution test passed', failed: zh ? '运行测试失败' : 'Execution test failed'};
  return <section className="my-3 space-y-1 rounded-control border border-line p-3 text-meta" aria-label={zh ? '方法验证状态' : 'Method verification'}>
    <p>{zh ? '契约检查：' : 'Contract checks: '}{value.contract.status === 'passed' ? (zh ? '通过' : 'Passed') : (zh ? '未通过' : 'Invalid')}</p>
    <p>{zh ? '参数与端口说明：' : 'Parameter and port descriptions: '}{value.documentation.status === 'complete' ? (zh ? '已填写' : 'Present') : (zh ? '仍有缺项' : 'Incomplete')}</p>
    <p>{zh ? '实际执行：' : 'Execution: '}{labels[run.status]}</p>
    {run.test_type && <p>{run.test_type === 'synthetic' ? (zh ? '合成数据验证' : 'Synthetic data') : (zh ? '指定真实数据验证' : 'Specific real dataset')} · {run.tested_at} · {run.environment}</p>}
    {run.reason && <p className="break-words text-muted">{run.reason}</p>}
    {run.report_path && <p className="break-all text-muted">{zh ? '报告：' : 'Report: '}{run.report_path}</p>}
    <p className="text-muted">{zh ? '当前环境探测与运行验证独立；合成测试不证明真实数据的科学有效性。' : 'Runtime availability and execution evidence are independent. Synthetic tests do not validate scientific results on real data.'}</p>
  </section>;
}
