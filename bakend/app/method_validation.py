"""Static contract checks and version-bound execution evidence, never runtime guesses."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def _valid_value(parameter, value):
    kind = parameter.type
    valid = (type(value) is bool if kind == 'boolean' else
             type(value) is int if kind == 'integer' else
             type(value) in (int, float) and math.isfinite(value) if kind == 'number' else
             isinstance(value, str))
    if not valid:
        return False
    if parameter.options and not any(type(value) is type(o.value) and value == o.value for o in parameter.options):
        return False
    if kind in {'integer', 'number'}:
        return ((parameter.minimum is None or value >= parameter.minimum) and
                (parameter.maximum is None or value <= parameter.maximum))
    return True


def contract_errors(method):
    errors = []
    params = {p.id: p for p in method.parameters}
    for group in ('parameters', 'input_slots', 'output_slots'):
        ids = [s.id for s in getattr(method, group)]
        if len(ids) != len(set(ids)) or any(not v.strip() for v in ids):
            errors.append(f'{group}: empty or duplicate field identity')
    for p in method.parameters:
        if p.minimum is not None and p.maximum is not None and p.minimum > p.maximum:
            errors.append(f'{p.id}: minimum exceeds maximum')
        if p.default is not None and not _valid_value(p, p.default):
            errors.append(f'{p.id}: default violates type/options/range')
        values = [json.dumps(o.value, sort_keys=True) for o in p.options]
        if len(values) != len(set(values)):
            errors.append(f'{p.id}: duplicate option value')
        if any(not _valid_value(p, o.value) for o in p.options):
            errors.append(f'{p.id}: option violates type/range')
        if set(p.conflicts_with) - params.keys() or p.id in p.conflicts_with:
            errors.append(f'{p.id}: invalid conflict reference')
    for field in [*method.parameters, *method.input_slots, *method.output_slots]:
        conditions = [getattr(field, key, {}) for key in
                      ('visible_when', 'required_when', 'enabled_when', 'generated_when')]
        conditions.extend(getattr(field, 'required_when_any', []))
        for condition in conditions:
            for key, values in condition.items():
                if key not in params:
                    errors.append(f'{field.id}: unknown condition parameter {key}')
                elif not values or any(not _valid_value(params[key], v) for v in values):
                    errors.append(f'{field.id}: invalid condition values for {key}')
    return errors


def documentation_gaps(method):
    return [f'{group}.{s.id}.description'
            for group in ('parameters', 'input_slots', 'output_slots')
            for s in getattr(method, group) if not s.description.strip()]


def validate_contract(method):
    errors = contract_errors(method)
    if errors:
        raise ValueError(f'Invalid method contract {method.id}: ' + '; '.join(errors))


def execution_fingerprint(method, root: Path):
    from .execution_dependencies import execution_sources
    digest = hashlib.sha256(('dependency-closure-v2:'+method.revision()).encode())
    for path in execution_sources(method,root):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_text(encoding="utf-8").encode())
    return digest.hexdigest()


def verification_status(method, root: Path):
    errors = contract_errors(method)
    gaps = documentation_gaps(method)
    result = {'contract': {'status': 'passed' if not errors else 'invalid', 'errors': errors},
              'documentation': {'status': 'complete' if not gaps else 'incomplete', 'missing': gaps},
              'execution': {'status': 'not_recorded', 'reason': '没有与当前契约及执行代码匹配的运行记录；不表示执行失败。'}}
    evidence_path = root / 'deploy/method-verification' / (method.id + '.json')
    if not evidence_path.is_file():
        return result
    try:
        evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
        if (evidence.get('method_id') != method.id or evidence.get('method_version') != method.version or
                evidence.get('execution_fingerprint') != execution_fingerprint(method, root)):
            result['execution'] = {'status': 'stale', 'reason': '历史运行证据与当前契约或执行代码不匹配，需要重新验证。'}
            return result
        report = (root / evidence['report_path']).resolve()
        report_root = (root / 'deploy/method-verification/reports').resolve()
        if not report.is_relative_to(report_root) or not report.is_file() or report.stat().st_size > 2_000_000:
            raise ValueError('Evidence report is missing or outside evidence directory')
        if hashlib.sha256(report.read_bytes()).hexdigest() != evidence['report_sha256']:
            raise ValueError('Evidence report checksum mismatch')
        if evidence['test_type'] not in {'synthetic', 'real_dataset'} or evidence['result'] not in {'passed', 'failed'}:
            raise ValueError('Invalid evidence result/type')
        result['execution'] = {k: evidence[k] for k in ('test_type', 'tested_at', 'environment', 'report_path')}
        result['execution']['status'] = evidence['result']
    except (OSError, ValueError, KeyError, TypeError):
        result['execution'] = {'status': 'invalid', 'reason': '运行证据格式、报告路径或校验和无效，不能视为验证通过。'}
    return result
