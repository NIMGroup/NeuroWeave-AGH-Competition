"""Machine-checkable observations, distinct from model interpretation."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
import json
from .evidence_fields import read_field


class FactCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fact_id: str
    field: str = Field(min_length=1, max_length=512, description='原始事实字段，推荐 JSON Pointer，如 /pattern_counts/wm_response.txt/count；兼容无歧义点号路径及数组数字下标。')
    expected: Any = Field(description='小型精确值，最多600字符；使用具体字段路径，不复制整个数组或契约。')
    scope: Literal['observed', 'all'] = 'observed'

    @field_validator('expected')
    @classmethod
    def bounded_expected(cls, value):
        if len(json.dumps(value, ensure_ascii=False, separators=(',', ':'))) > 600:
            raise ValueError('expected过大；请引用具体小型字段，不复制清单或契约')
        return value


def check_observations(checks, sources):
    outcomes = []
    for raw in checks:
        check = FactCheck.model_validate(raw)
        result = sources.get(check.fact_id)
        try:
            current = read_field(result, check.field)
            passed = type(current) is type(check.expected) and current == check.expected
            if check.scope == 'all':
                complete = result.get('scope_complete', result.get('complete_scan')) is True
                if result.get('aggregation_scope') == 'current_file_page':
                    complete = complete and (result.get('snapshot_statistics',{}).get('complete') is True
                        if check.field.startswith(('snapshot_statistics.', '/snapshot_statistics/', 'summary.snapshot_statistics.', 'result.snapshot_statistics.')) else result.get('complete') is True)
                passed = passed and complete
            reason = '' if passed else '值不一致或证据未覆盖完整范围'
        except (KeyError, IndexError, TypeError, ValueError):
            passed, reason = False, '引用字段不存在或事实不可读取'
        outcomes.append({**check.model_dump(), 'passed': passed, 'reason': reason})
    return outcomes
