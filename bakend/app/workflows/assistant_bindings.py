"""Bind explicitly selected paths to a draft; retain complete sample validation."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..data_selection import DataSelection
from ..operation_context import checkpoint
from ..workflow_models import InputBinding
from . import contracts


class AssistantInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    method_id: str = Field(min_length=1, max_length=200)
    slot_id: str = Field(min_length=1, max_length=100)
    path: str = Field(default="", max_length=4096, description='数据集内已有事实或用户声明的代表路径；声明未给路径时dry-run定位。')
    declaration_fact_id: str = ""
    step_id: str = Field(default='', max_length=100, description='同一方法出现多次时必须指定步骤。')
    per_sample: bool = Field(default=True, description='按完整样本清单展开并保留逐样本状态；默认为共同满足必需输入的样本准备方案。')

    @field_validator('path')
    @classmethod
    def safe_path(cls, value):
        return DataSelection(path=value).path if value else ""


def bind_assistant_inputs(workflow, request, datasets, specs, *, preflight=False):
    if not specs:
        return []
    if workflow.input_selection == 'user' and not preflight:
        reports = []
        for spec in specs:
            candidates = [s for s in workflow.steps if s.selected_method_id == spec.method_id
                          and (not spec.step_id or s.id == spec.step_id)]
            if len(candidates) != 1:
                raise ValueError('输入建议必须对应一个明确的真实方法步骤')
            step = candidates[0]
            slot = next((s for s in contracts.selected_method(step).input_slots if s.id == spec.slot_id), None)
            if not slot or 'existing_path' not in slot.source_options:
                raise ValueError('输入建议必须对应真实方法的外部输入槽')
            if spec.path:
                dataset = workflow.dataset
                step.input_suggestions[slot.id] = {'path':spec.path,
                    'relative_path':(Path(dataset.relative_path)/spec.path).as_posix() if dataset else spec.path,
                    'root_id':dataset.root_id if dataset else None,
                    'binding_mode':'per_sample' if spec.per_sample else 'single',
                    'source':'assistant', 'validation':'unselected', 'declaration_fact_id':spec.declaration_fact_id}
                reports.append({'step_id':step.id,'slot_id':slot.id,'path':spec.path,'validation':'suggestion_only'})
        return reports
    if not workflow.dataset:
        raise ValueError('科学输入绑定需要当前已选数据集；不能使用模型指定的服务器根目录')
    dataset = datasets.get(workflow.dataset_id)
    if any(spec.per_sample for spec in specs) and dataset.inspection.sample_count != len(dataset.inspection.samples):
        raise ValueError('已登记样本列表尚不完整，不能声称全部样本输入已绑定；请先核实数据集样本范围')
    root_path = Path(dataset.absolute_path)
    used = set()
    reports = []
    previews = {}
    checked_templates = set()
    for spec in specs:
        checkpoint('核对完整输入映射')
        candidates = [s for s in workflow.steps if s.selected_method_id==spec.method_id
                      and (not spec.step_id or s.id==spec.step_id)]
        if len(candidates)!=1:
            raise ValueError(f'{spec.method_id} 必须对应一个明确步骤；重复方法请指定step_id')
        step=candidates[0]
        if (step.id,spec.slot_id) in used:
            raise ValueError('同一输入槽不能重复绑定')
        used.add((step.id,spec.slot_id))
        method=contracts.selected_method(step)
        slot=next((v for v in method.input_slots if v.id==spec.slot_id),None)
        if not slot or 'existing_path' not in slot.source_options:
            raise ValueError(f'{spec.method_id}/{spec.slot_id} 不是可绑定已有文件的真实输入槽')
        if spec.slot_id in step.input_bindings and step.input_bindings[spec.slot_id].source=='upstream_output':
            raise ValueError('已有上游连接的输入不能同时绑定外部文件；先修正流程图')
        if not preflight:
            if not spec.path and not spec.declaration_fact_id:
                raise ValueError('未给路径的输入需有当前会话声明')
            if spec.path and not workflow.task_contract.allows_slot(slot,spec.path):
                raise ValueError('输入规则超出本次允许范围')
            binding = InputBinding(slot_id=slot.id, artifact_type=slot.type, source='existing_path',
                dataset_id=dataset.dataset_id, root_id=dataset.root_id, path_template=spec.path,
                declaration_fact_id=spec.declaration_fact_id, deferred_resolution=True,
                per_sample_requested=spec.per_sample, status='planned',
                label=spec.path or slot.label, messages=['规划输入规则；路径、样本覆盖及内容尚待dry-run检查。'])
            step.input_bindings[slot.id]=binding
            reports.append({'step_id':step.id,'slot_id':slot.id,'path_template':spec.path,
                            'validation':'pending_dry_run','declaration_fact_id':spec.declaration_fact_id})
            continue
        relative=(Path(dataset.relative_path)/spec.path).as_posix()
        root,candidate,normalized=datasets.resolve_artifact_path(dataset.root_id,relative)
        if not candidate.is_relative_to(root_path.resolve()):
            raise PermissionError('绑定路径超出当前数据集')
        # Query permission through a link never permits a scientific mutable input tree.
        from ..workspace_paths import WorkspacePaths
        candidate=WorkspacePaths(root_path,allowed_roots=(root_path,)).resolve(root_path,spec.path,write=True)
        status,message=datasets._path_match_status(candidate,slot.binding,slot.formats)
        if status!='matched':
            raise ValueError(f'{step.id}/{slot.id}：{message}')
        preview=None
        if spec.per_sample:
            key=(spec.path,slot.binding,tuple(slot.formats))
            if key not in previews:
                previews[key]=datasets.preview_sample_mapping(dataset.dataset_id,dataset.root_id,relative,
                                                     binding=slot.binding,formats=slot.formats)
            preview=previews[key]
            if slot.binding == 'file' and dataset.inspection.sample_count > 1 and preview.binding_mode != 'per_sample':
                raise ValueError(f'{step.id}/{slot.id} 代表路径不能展开到已登记样本；请提供样本内的真实路径，单一文件不能证明全量覆盖')
            if not preview.can_apply and workflow.task_contract.sample_policy == 'all':
                missing=[m.sample_id for m in preview.matches if m.status!='matched']
                raise ValueError(f'{step.id}/{slot.id} 未覆盖全部样本：匹配{preview.coverage.matched}/{preview.coverage.total}；异常样本{missing[:8]}')
            if not preview.can_apply and not preview.coverage.matched:
                raise ValueError(f'{step.id}/{slot.id} 没有任何样本匹配此输入；需要其他真实输入或方法，不能形成可运行方案')
        paths=[m.absolute_path for m in preview.matches if m.status=='matched'] if preview else [str(candidate)]
        policy=WorkspacePaths(root_path,allowed_roots=(root_path,))
        if preview and preview.binding_mode=='per_sample':
            samples={s.sample_id:s for s in dataset.inspection.samples}
            for match in preview.matches:
                if match.status=='matched':
                    # Match previews normalize paths; retain the original template
                    # here so a link in another subject cannot hide behind resolve().
                    original=preview.path_template.format_map(samples[match.sample_id].entities)
                    if original not in checked_templates:
                        checkpoint()
                        policy.resolve(root_path,original,write=True)
                        checked_templates.add(original)
        for path in paths:
            checkpoint()
            policy.resolve(root_path,path,write=True)
        if any(not workflow.task_contract.allows_slot(slot,p) for p in paths):
            raise ValueError(f'{step.id}/{slot.id} 输入映射超出本次允许的已有输入范围')
        binding=InputBinding(slot_id=slot.id,artifact_type=slot.type,source='existing_path',
            confirmed_by_user=workflow.input_selection == 'user',
            allow_partial=bool(preview and preview.binding_mode=='per_sample' and workflow.task_contract.sample_policy=='eligible'),
            root_id=root.root_id,relative_path=normalized,absolute_path=str(candidate),
            label=f'{preview.coverage.matched} 个样本 · {preview.path_template}' if preview else candidate.name,
            binding_mode=preview.binding_mode if preview else 'single',dataset_id=dataset.dataset_id,
            path_template=preview.path_template if preview else spec.path,
            variables=preview.variables if preview else [],coverage=preview.coverage if preview else None,
            sample_matches=preview.matches if preview else [],status='validated',
            messages=['实际路径已按输入格式逐样本核对；未匹配状态保留，运行范围为必需输入共同匹配的样本，执行前仍需预检及点击运行。'])
        step.input_bindings[slot.id]=binding
        reports.append({'step_id':step.id,'slot_id':slot.id,'path_template':binding.path_template,
                        'binding_mode':binding.binding_mode,'coverage':preview.coverage.model_dump() if preview else {'total':1,'matched':1}})
    return reports
