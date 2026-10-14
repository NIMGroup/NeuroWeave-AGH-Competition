"""Sourced findings and explicit gaps for bounded specialist assignments."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .agent_claims import FactCheck, check_observations


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requirement_id: str = Field(default='', max_length=80, description='assignment.requirement_items 中的稳定问题 ID；措辞不影响对应关系。')
    question: str = Field(default='', max_length=1600)
    status: Literal['confirmed', 'unknown', 'partial', 'missing', 'ambiguous']
    conclusion: str = Field(min_length=1, max_length=600)
    fact_ids: list[str] = Field(default_factory=list, max_length=40)
    basis: Literal['observation', 'inference'] = Field(default='inference', description='observation 仅表示工具直接观测并通过 checks；字段含义、需求偏好、从计数相等推断逐文件配对属于 inference。')
    checks: list[FactCheck] = Field(default_factory=list, max_length=4, description='引用小型精确字段，不能复制整份契约或清单；全范围断言 scope=all 需要实际完整覆盖。')


def report_for(job, payload, available, sources=None):
    requirements = job.get('requirements') or [job['question']]
    items = job.get('requirement_items') or [{'id':f'r{i+1}', 'question':q} for i,q in enumerate(requirements)]
    warnings = []
    def verified(values):
        if not isinstance(values, list):
            warnings.append('fact_ids 必须为真实事实引用列表')
            return []
        invalid = [r for r in values if not isinstance(r,str) or r not in available]
        if invalid:
            warnings.append('未采用不存在或超出当前会话授权范围的事实引用：'+str(invalid)[:300])
        return list(dict.fromkeys(r for r in values if isinstance(r,str) and r in available))
    refs = verified(payload.get('fact_ids', []))
    findings = []
    for raw in payload.get('findings', []):
        try:
            finding = Finding.model_validate(raw).model_dump()
        except ValueError as exc:
            warnings.append('未采用格式错误的结论：'+str(exc)[:300])
            continue
        item = next((i for i in items if i['id']==finding['requirement_id']), None) if finding['requirement_id'] else next(
            (i for i in items if i['question']==finding['question']), None)
        if not item:
            warnings.append('结论未关联真实分派问题 ID；该问题保持未知')
            continue
        if any(f['requirement_id']==item['id'] for f in findings):
            warnings.append('重复问题结论未采用：'+item['id'])
            continue
        # A check already carries its exact source. Do not require the model to
        # repeat that reference in a second list before verifying the same value.
        finding.update(requirement_id=item['id'],question=item['question'],fact_ids=verified(
            [*finding['fact_ids'], *[c['fact_id'] for c in finding['checks']]]))
        if finding['status'] in {'confirmed','missing','ambiguous'} and not finding['fact_ids']:
            finding['status']='unknown'
            warnings.append('结论缺少有效事实引用，保持未知：'+item['id'])
        findings.append(finding)
        if sources is not None:
            finding['verification'] = check_observations(finding['checks'], sources)
            supported = finding['basis'] == 'observation' and bool(finding['verification']) and all(
                c['passed'] and c['fact_id'] in finding['fact_ids'] for c in finding['verification'])
            finding['observation_verified'] = supported
            if finding['status'] in {'confirmed', 'missing'} and not supported:
                finding['status'] = 'partial' if finding['fact_ids'] else 'unknown'
                warnings.append('结论尚为解释或缺少匹配的完整观测，不作为已确认事实：'+item['id'])
    if not findings:
        # Legacy prose without a per-question finding cannot prove an assignment complete.
        findings = [{'requirement_id':items[0]['id'],'question': requirements[0], 'status': 'unknown',
                     'conclusion': payload['answer'], 'fact_ids': refs}]
    for finding in findings:
        refs = list(dict.fromkeys([*refs, *finding['fact_ids']]))
    answered = {f['requirement_id'] for f in findings if f['status'] in {'confirmed', 'missing'}}
    settled_questions = {i['question'] for i in items if i['id'] in answered}
    unresolved = list(dict.fromkeys([*[q for q in payload.get('unresolved', []) if q not in settled_questions],
        *[i['question'] for i in items if i['id'] not in answered]]))
    return {**payload, 'fact_ids': refs, 'findings': findings, 'unresolved': unresolved,
            'agent': job['agent'], 'assignment_complete': not unresolved, 'report_warnings':warnings,
            'status': 'answered' if not unresolved else 'partial',
            'assessment_kind': 'model_interpretation', 'facts_are_authoritative': True}


def checkpoint_report(job, rows, errors=()):
    refs = [r['id'] for r in rows]
    findings = [{'requirement_id':f'r{i+1}','question': q, 'status': 'unknown', 'fact_ids': [],
                 'conclusion': '本批已保存查询结果，但尚未得到该问题的明确查证结论。'}
                for i,q in enumerate(job.get('requirements') or [job['question']])]
    return {'agent': job['agent'], 'answer': '调查尚未完成；已保存事实，以下问题仍需明确结论。',
            'fact_ids': refs, 'findings': findings,
            'unresolved': [f['question'] for f in findings],
            'query_errors': list(errors)[-8:], 'status': 'partial',
            'assignment_complete': False, 'assessment_kind': 'backend_checkpoint'}
