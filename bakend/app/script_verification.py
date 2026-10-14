"""Declared function cases run only inside the manually approved sandbox."""
import ast
import json
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScriptCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=160)
    function: str = Field(pattern=r'^[A-Za-z_]\w*$', max_length=100)
    args: list[Any] = Field(default_factory=list, max_length=12)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    expected: Any

    @model_validator(mode='after')
    def bounded(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False)) > 12000:
            raise ValueError('单个函数验证案例超过 12000 字符')
        return self


class ManifestInvariants(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cover_selected_files: bool = False
    preserve_stem_groups: list[str] = Field(default_factory=list, max_length=12,
        description='需保持同名关联的扩展名，如 .json/.nii.gz；后端按来源组独立检查。')

    @model_validator(mode='after')
    def extensions(self):
        if any(not e.startswith('.') or len(e) < 2 or len(e) > 32 or any(c in e for c in '/\\') for e in self.preserve_stem_groups):
            raise ValueError('关联扩展名必须是 . 开头的安全文件后缀')
        if len(set(self.preserve_stem_groups)) != len(self.preserve_stem_groups):
            raise ValueError('关联扩展名不能重复')
        return self


def checked_cases(code, cases):
    names = {n.name for n in ast.parse(code).body if isinstance(n, ast.FunctionDef)}
    for case in cases:
        if case.function not in names:
            raise ValueError('验证案例必须引用脚本中真实定义的函数：'+case.function)


def sandbox_program(code, cases):
    if not cases:
        return code
    # No expression evaluation, imports, host probes or execution before Run.
    checks = [case.model_dump() for case in cases]
    return ('import json as _nw_json\n'
            'def _nw_equal(a, b):\n'
            '    if isinstance(a, bool) or isinstance(b, bool):\n'
            '        return type(a) is type(b) and a == b\n'
            '    if isinstance(a, (int, float)) and isinstance(b, (int, float)):\n'
            '        return a == b and a not in (float("inf"), float("-inf"))\n'
            '    if isinstance(a, dict) and isinstance(b, dict):\n'
            '        return a.keys() == b.keys() and all(_nw_equal(a[k], b[k]) for k in a)\n'
            '    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):\n'
            '        return len(a) == len(b) and all(_nw_equal(x,y) for x,y in zip(a,b))\n'
            '    return type(a) is type(b) and a == b\n'
            '_nw_source = '+repr(code)+'\n'
            '_nw_scope = {"__name__": "__main__", "__file__": "/script.py"}\n'
            'exec(compile(_nw_source, "/script.py", "exec"), _nw_scope)\n'
            '_nw_cases = _nw_json.loads('+repr(json.dumps(checks,ensure_ascii=False))+')\n'
            'for _nw_case in _nw_cases:\n'
            '    _nw_actual = _nw_scope[_nw_case["function"]](*_nw_case["args"], **_nw_case["kwargs"])\n'
            '    if not _nw_equal(_nw_actual, _nw_case["expected"]):\n'
            '        raise ValueError("方案边界案例未通过：" + _nw_case["name"])\n'
            'print("NEUROWEAVE_FUNCTION_CASES_PASSED=" + str(len(_nw_cases)))\n')


def verify_manifest(edits, request, input_rows, allowed_paths=None):
    """Check effects against frozen pre-execution input, independent of script stdout."""
    from collections import defaultdict
    from pathlib import PurePosixPath
    paths = [e.path for e in edits]
    destinations = [e.destination or e.path for e in edits if e.action != 'delete']
    if len(set(destinations)) != len(destinations):
        raise ValueError('生成清单存在重复目标；未接受可运行方案')
    checks = [{'criterion': 'unique_destinations', 'passed': True, 'count': len(destinations)}]
    invariants = request.manifest_invariants
    if invariants.cover_selected_files:
        expected = {row['path'] for row in input_rows if request.selection.matches(row['path']) and
                    (allowed_paths is None or row['path'] in allowed_paths)}
        if set(paths) != expected or len(paths) != len(expected):
            raise ValueError('生成清单没有完整且唯一覆盖所选文件；不得用样例或文件数相等替代路径集合核验')
        checks.append({'criterion':'selected_file_coverage','passed':True,'count':len(expected)})
    extensions = invariants.preserve_stem_groups
    if extensions:
        groups = defaultdict(dict)
        source_groups = defaultdict(set)
        for row in input_rows:
            p = PurePosixPath(row['path'])
            suffix = next((e for e in sorted(extensions,key=len,reverse=True) if p.name.endswith(e)), None)
            if suffix:
                source_groups[(str(p.parent),p.name[:-len(suffix)])].add(suffix)
        for edit in edits:
            p=PurePosixPath(edit.path)
            suffix=next((e for e in sorted(extensions,key=len,reverse=True) if p.name.endswith(e)),None)
            if suffix:
                target=PurePosixPath(edit.destination or edit.path)
                if edit.action=='delete' or not target.name.endswith(suffix):
                    raise ValueError('同名关联组不能丢失成员或改变声明的扩展名')
                groups[(str(p.parent),p.name[:-len(suffix)])][suffix]=(str(target.parent),target.name[:-len(suffix)])
        if any(len(set(group.values())) != 1 for group in groups.values()):
            raise ValueError('生成清单破坏来源文件的同名关联关系')
        if any(set(group) != source_groups[key] for key,group in groups.items()):
            raise ValueError('生成清单遗漏来源同名关联组的成员')
        checks.append({'criterion':'stem_group_preservation','passed':True,'group_count':len(groups)})
    return {'checks':checks, 'input_snapshot': {'basis':'pre_execution_regular_file_snapshot',
                'source_file_count':len(input_rows), 'selection':request.selection.model_dump(),
                'notice':'覆盖核验针对本次运行前冻结的所选普通文件；登记概要与其他范围的统计不能替代此快照。'},
            'function_case_count':len(request.test_cases),
            'function_cases_status':'passed' if request.test_cases else 'not_declared',
            'source':'backend_manifest_validation', 'semantic_notice':'路径、覆盖及声明的不变量已验证；字段意义与用户偏好仍需主控按原需求审阅。'}
