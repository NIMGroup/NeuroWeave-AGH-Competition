"""Read-only output projection from saved contracts; never scan or run a runner."""
from __future__ import annotations

from pathlib import PurePosixPath

from ..parameter_rules import matches
from ..sample_scope import partial_sample_scope
from .output_layout import output_key, path_component, relative_output_key
from .recipe_compiler import RecipeCompilationError


def workflow_output_preview(workflow, compiler, execution, *, offset=0, limit=2):
    layout = execution.output_layout or workflow.output_layout
    result = {
        'workflow_id': workflow.workflow_id, 'revision': workflow.revision,
        'output_layout': layout, 'sample_mode': execution.sample_mode,
        'requested_sample_mode': execution.sample_mode, 'settings_need_change': False,
        'naming_rule': 'step_only',
        'executed': False, 'filesystem_checked': False, 'run_id_assigned': False,
        'status': 'available', 'root_template': None,
        'sample_count': 1, 'excluded_sample_count': 0,
        'unit_count': 0, 'declared_output_count': 0,
        'offset': offset, 'next_offset': None, 'groups': [],
        'entries_truncated': False, 'issues': [],
    }
    if workflow.workspace_root_path:
        root, relative = PurePosixPath(workflow.workspace_root_path), PurePosixPath(workflow.output_relative_path)
        if '..' in relative.parts or '\\' in workflow.output_relative_path:
            result['issues'].append('输出位置含不安全路径；请重新选择输出目录')
        else:
            output = relative if relative.is_absolute() else root / relative
            if not output.is_relative_to(root):
                result['issues'].append('输出位置超出所选目录；请重新选择输出目录')
            else:
                result['root_template'] = str(output / '{new_run_id}')
    else:
        result['issues'].append('尚未选择输出目录；预览只显示运行目录内的结构')
    try:
        units = compiler.compile(workflow)
        scope = partial_sample_scope(workflow)
    except (RecipeCompilationError, ValueError) as exc:
        result.update(status='incomplete', issues=[*result['issues'], str(exc)])
        return result
    requires_batch = scope is not None or any(
        (b.binding_mode == 'per_sample' and len(b.sample_matches) > 1)
        or (b.deferred_resolution and b.per_sample_requested)
        for u in units for b in u.step.input_bindings.values())
    pending_selection = workflow.input_selection == 'user' and any(s.missing_required_inputs for s in workflow.steps)
    if pending_selection:
        result['input_validation']='pending_selection'
        result['issues'].append('输入尚未选择；按登记样本展示可能的输出结构，实际运行范围由用户选择和dry-run确定。')
    elif any(b.deferred_resolution for u in units for b in u.step.input_bindings.values()):
        result['input_validation']='pending_dry_run'
        result['issues'].append('输入覆盖尚待dry-run核查；按已登记样本展示预计输出，不代表这些样本已经合格。')
    batch = execution.sample_mode == 'per_sample' or requires_batch or (
        pending_selection and workflow.dataset and workflow.dataset.inspection.sample_count > 1)
    if batch:
        result['sample_mode']='per_sample'
    if requires_batch and execution.sample_mode != 'per_sample':
        result.update(sample_mode='per_sample', settings_need_change=True)
        result['issues'].append('当前单次运行设置不适用于此方案。下面按必需的“按样本运行”展示预计目录；运行前仍需在运行设置中选择按样本运行并配置节点。')
    result['naming_rule'] = ('sample_id' if layout == 'sample_steps' else 'ordinal_step') if batch else 'step_only'
    if batch:
        inspection = workflow.dataset.inspection if workflow.dataset else None
        samples = inspection.samples if inspection else []
        if not samples or len(samples) != inspection.sample_count or len({s.sample_id for s in samples}) != len(samples):
            result.update(status='incomplete', sample_count=0)
            result['issues'].append('已保存的样本清单不完整或标识重复；无法推断批量目录')
            return result
        eligible = set(scope.eligible_sample_ids) if scope else None
        identities = [(s.sample_id, s.label, i + 1, s.relative_root) for i, s in enumerate(samples)
                      if eligible is None or s.sample_id in eligible]
        result['excluded_sample_count'] = len(samples) - len(identities)
    else:
        identities = [(None, '', 0, None)]
    outputs = [[o for o in u.method.output_slots if matches(o.generated_when, u.step.selected_parameters)] for u in units]
    count = len(identities)
    result.update(sample_count=count, unit_count=count * len(units),
                  declared_output_count=count * sum(len(slots) for slots in outputs))
    if not count:
        result['status'] = 'incomplete'
        result['issues'].append('当前允许的样本范围为空')
        return result
    # Validate the whole namespace using saved identities, without touching any files.
    seen = set()
    for identity, _label, ordinal, _relative_root in identities:
        for unit in units:
            key = relative_output_key(output_key(layout, unit.workspace_key, sample_id=identity,
                                      legacy_prefix=f'sample_{ordinal:04d}__' if identity is not None else ''))
            if key in seen and batch:
                result['issues'].append('样本或步骤输出目录冲突；请检查方案')
                result['status'] = 'incomplete'
                return result
            seen.add(key)
    visible_entries = 0
    for identity, label, ordinal, source_root in identities[offset:offset + limit]:
        folder = path_component(identity) if identity is not None and layout == 'sample_steps' else None
        source_name = (PurePosixPath(source_root).name if source_root not in {None, '', '.'} else
                       PurePosixPath(workflow.dataset.absolute_path).name if identity is not None else None)
        group = {'sample_id': identity, 'sample_label': label, 'source_relative_root': source_root,
                 'output_sample_directory': folder,
                 'folder_matches_input_name': folder == source_name if folder is not None else None,
                 'sample_id_sanitized': folder != identity if folder is not None else False,
                 'steps': []}
        for unit, slots in zip(units, outputs, strict=True):
            directory = relative_output_key(output_key(layout, unit.workspace_key, sample_id=identity,
                                           legacy_prefix=f'sample_{ordinal:04d}__' if identity is not None else ''))
            step = {'step_id': unit.step.id, 'title': unit.step.title, 'relative_directory': directory, 'outputs': []}
            for slot in slots:
                if visible_entries >= 200:
                    result['entries_truncated'] = True
                    break
                template = slot.path_template
                safe = template and not PurePosixPath(template).is_absolute() and '..' not in PurePosixPath(template).parts and '\\' not in template
                step['outputs'].append({'slot_id': slot.id, 'label': slot.label, 'artifact_type': slot.type,
                    'format': slot.format, 'relative_path': template if safe else None,
                    'certainty': 'declared' if safe and '{' not in template else 'dynamic_name' if safe else 'type_only'})
                visible_entries += 1
            group['steps'].append(step)
        result['groups'].append(group)
    result['next_offset'] = offset + len(result['groups']) if offset + len(result['groups']) < count else None
    return result
