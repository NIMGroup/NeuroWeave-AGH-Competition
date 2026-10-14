"""WorkflowBindings: dataset and historical binding mutations under the shared session lock."""

from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path

from ..dataset_models import DatasetPathPreview
from ..historical_inputs import HistoricalInputs
from ..workflow_models import (
    InputBinding,
    Workflow,
)
from . import contracts

if TYPE_CHECKING:
    from ..dataset_service import DatasetService
    from ..local_store import LocalStore
    from .session import WorkflowSession
    from .state import WorkflowState


class WorkflowBindings:
    def __init__(
        self,
        session: WorkflowSession,
        state: WorkflowState,
        store: LocalStore,
        dataset_service: DatasetService,
    ) -> None:
        self.session = session
        self.state = state
        self.store = store
        self.dataset_service = dataset_service

    def bind_input(
        self,
        workflow_id: str,
        step_id: str,
        slot_id: str,
        root_id: str,
        relative_path: str,
        expected_revision: int,
        binding_mode: str = 'auto',
    ) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            if workflow.status == "confirmed":
                raise ValueError("流程已经确认，不能继续修改输入")
            if workflow.locked_at is None and workflow.input_selection != 'user':
                raise ValueError("请先确定流程链，再配置输入")
            step = contracts.step(workflow, step_id)
            method = contracts.selected_method(step)
            slot = next(
                (item for item in method.input_slots if item.id == slot_id), None
            )
            if slot is None:
                raise ValueError(f"方法 {method.label} 没有输入槽位 {slot_id}")
            if "existing_path" not in slot.source_options:
                raise ValueError(f"输入 {slot.label} 不允许绑定已有路径")
            if workflow.input_selection == 'user':
                from .user_inputs import selected_rule
                binding = selected_rule(workflow, slot, self.dataset_service, root_id, relative_path, binding_mode)
                if not workflow.task_contract.allows_slot(slot, binding.path_template):
                    raise ValueError('所选输入规则不符合当前任务的输入范围限制')
                step.input_bindings[slot.id] = binding
                contracts.clear_review(workflow)
                contracts.advance(workflow)
                self.state.refresh(workflow)
                self.session.save(workflow, 'workflow_input_selected', {'step_id':step_id,'slot_id':slot_id,
                    'path_template':binding.path_template,'binding_mode':binding.binding_mode,'selected_by':'user'})
                return workflow.model_copy(deep=True)
            root, candidate, normalized = self.dataset_service.resolve_artifact_path(
                root_id,
                relative_path,
            )
            if slot.binding == "folder" and not candidate.is_dir():
                raise ValueError(f"输入 {slot.label} 必须选择文件夹")
            if slot.binding == "file" and not candidate.is_file():
                raise ValueError(f"输入 {slot.label} 必须选择文件")
            if candidate.is_file() and slot.formats:
                lowered = candidate.name.lower()
                if not any(lowered.endswith(item.lower()) for item in slot.formats):
                    raise ValueError(
                        f"输入 {slot.label} 仅接受：{', '.join(slot.formats)}"
                    )
            messages = ["路径存在且位于后端授权的数据根目录中。"]
            if slot.companions:
                messages.append(
                    "仍需在 dry-run 检查配套文件：" + ", ".join(slot.companions)
                )
            preview = None
            if workflow.dataset_id:
                preview = self.dataset_service.preview_sample_mapping(
                    workflow.dataset_id,
                    root_id,
                    relative_path,
                    binding=slot.binding,
                    formats=slot.formats,
                )
                workflow.dataset = self.dataset_service.get(workflow.dataset_id)
                if not preview.can_apply:
                    raise ValueError(
                        "样本路径规则未覆盖全部样本，请检查匹配预览后重新选择"
                    )
                messages = [*preview.messages, *messages]
            label = candidate.name or root.label
            if preview and preview.binding_mode == "per_sample":
                label = f"{preview.coverage.matched} 个样本 · {preview.path_template}"
            step.input_bindings[slot.id] = InputBinding(
                slot_id=slot.id,
                artifact_type=slot.type,
                source="existing_path",
                label=label,
                root_id=root.root_id,
                relative_path=normalized,
                absolute_path=str(candidate),
                binding_mode=preview.binding_mode if preview else "single",
                dataset_id=workflow.dataset_id if preview else None,
                path_template=preview.path_template if preview else "",
                variables=preview.variables if preview else [],
                coverage=preview.coverage if preview else None,
                sample_matches=preview.matches if preview else [],
                status="validated" if preview else "selected",
                messages=messages,
            )
            contracts.clear_review(workflow)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(
                workflow,
                "workflow_input_bound",
                {
                    "step_id": step_id,
                    "slot_id": slot_id,
                    "relative_path": normalized,
                    "binding_mode": preview.binding_mode if preview else "single",
                    "path_template": preview.path_template if preview else "",
                },
            )
            return workflow.model_copy(deep=True)

    def preview_input_mapping(
        self,
        workflow_id: str,
        step_id: str,
        slot_id: str,
        root_id: str,
        relative_path: str,
        binding_mode: str = 'auto',
    ) -> DatasetPathPreview:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            if workflow.status == "confirmed":
                raise ValueError("流程已经确认，不能继续修改输入")
            if workflow.locked_at is None and workflow.input_selection != 'user':
                raise ValueError("请先确定流程链，再配置输入")
            if not workflow.dataset_id:
                raise ValueError("当前流程没有关联数据集，无法生成样本路径规则")
            step = contracts.step(workflow, step_id)
            method = contracts.selected_method(step)
            slot = next(
                (item for item in method.input_slots if item.id == slot_id), None
            )
            if slot is None:
                raise ValueError(f"方法 {method.label} 没有输入槽位 {slot_id}")
            if "existing_path" not in slot.source_options:
                raise ValueError(f"输入 {slot.label} 不允许绑定已有路径")
            if workflow.input_selection == 'user':
                from .user_inputs import rule_preview
                return rule_preview(workflow, slot, self.dataset_service, root_id, relative_path, binding_mode)
            return self.dataset_service.preview_sample_mapping(
                workflow.dataset_id,
                root_id,
                relative_path,
                binding=slot.binding,
                formats=slot.formats,
            )

    def clear_input(self, workflow_id, step_id, slot_id, expected_revision):
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            if workflow.status == 'confirmed':
                raise ValueError('已确认的流程不能修改输入')
            step = contracts.step(workflow, step_id)
            binding = step.input_bindings.get(slot_id)
            if binding and binding.source == 'upstream_output':
                raise ValueError('上游连接应通过修改流程步骤调整，不能按外部输入清除')
            step.input_bindings.pop(slot_id, None)
            contracts.clear_review(workflow)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(workflow, 'workflow_input_cleared', {'step_id':step_id,'slot_id':slot_id})
            return workflow.model_copy(deep=True)

    def saved_input_rules(self, workflow_id, step_id, slot_id):
        import json
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            step = contracts.step(workflow, step_id)
            slot = next(s for s in contracts.selected_method(step).input_slots if s.id == slot_id)
            dataset = workflow.dataset
        if not dataset:
            return {'rules':[]}
        with self.store.connection() as db:
            rows = db.execute('SELECT workflow_id,payload_json FROM workflows WHERE dataset_id=? AND workflow_id<>? ORDER BY updated_at DESC LIMIT 40',
                              (dataset.dataset_id, workflow_id)).fetchall()
        rules, seen = [], set()
        for row in rows:
            past = json.loads(row['payload_json'])
            for prior in past.get('steps', []):
                if prior.get('selected_method_id') != step.selected_method_id:
                    continue
                binding = prior.get('input_bindings', {}).get(slot_id, {})
                path = binding.get('path_template')
                if (not binding.get('confirmed_by_user') or not path or binding.get('history')
                        or binding.get('root_id') != dataset.root_id or binding.get('artifact_type') != slot.type):
                    continue
                mode = 'template' if binding.get('variables') else 'single'
                identity = (path, mode)
                if identity in seen:
                    continue
                seen.add(identity)
                rules.append({'id':row['workflow_id']+':'+prior['id']+':'+slot_id, 'title':past['title'],
                    'root_id':dataset.root_id,'path_template':path,
                    'relative_path':(Path(dataset.relative_path)/path).as_posix(), 'binding_mode':mode,
                    'validation':'pending_dry_run'})
        return {'rules':rules[:8]}

    def historical_input_target(
        self, workflow_id, step_id, slot_id, expected_revision=None
    ):
        workflow = self.session.require(workflow_id)
        if expected_revision is not None:
            contracts.check_revision(workflow, expected_revision)
        if workflow.status == "confirmed" or (workflow.locked_at is None and workflow.input_selection != 'user'):
            raise ValueError("请先锁定可编辑的流程链，再选择历史输入")
        step = contracts.step(workflow, step_id)
        slot = next(
            (
                item
                for item in contracts.selected_method(step).input_slots
                if item.id == slot_id
            ),
            None,
        )
        if slot is None or "existing_path" not in slot.source_options:
            raise ValueError("当前方法输入不接受已有文件")
        return workflow.model_copy(deep=True), slot

    def historical_input_candidates(self, workflow_id, step_id, slot_id, offset=0):
        with self.session.lock:
            workflow, slot = self.historical_input_target(workflow_id, step_id, slot_id)
        return HistoricalInputs(self.store).candidates(workflow, slot, offset=offset)

    def preview_historical_input(self, workflow_id, step_id, slot_id, request):
        with self.session.lock:
            workflow, slot = self.historical_input_target(
                workflow_id, step_id, slot_id, request.expected_revision
            )
        preview, _ = HistoricalInputs(self.store).preview(
            workflow, slot, request.run_id, request.group_id, allow_partial=True
        )
        with self.session.lock:
            contracts.check_revision(
                self.session.require(workflow_id), request.expected_revision
            )
        return preview

    def bind_historical_input(self, workflow_id, step_id, slot_id, request):
        with self.session.lock:
            workflow, slot = self.historical_input_target(
                workflow_id, step_id, slot_id, request.expected_revision
            )
        # Filesystem work is outside the global workflow lock. Apply rechecks the
        # source and uses CAS so a preview can never overwrite newer parameters.
        preview, binding = HistoricalInputs(self.store).preview(
            workflow,
            slot,
            request.run_id,
            request.group_id,
            allow_partial=request.allow_partial,
        )
        if not preview["can_apply"]:
            raise ValueError(
                "历史输出未能唯一覆盖所有样本；可明确选择仅使用匹配样本，至少需匹配一个样本"
            )
        with self.session.lock:
            current, _ = self.historical_input_target(
                workflow_id, step_id, slot_id, request.expected_revision
            )
            binding.confirmed_by_user = True
            contracts.step(current, step_id).input_bindings[slot_id] = binding
            contracts.clear_review(current)
            contracts.advance(current)
            self.state.refresh(current)
            self.session.save(
                current,
                "workflow_historical_input_bound",
                {
                    "step_id": step_id,
                    "slot_id": slot_id,
                    "run_id": request.run_id,
                    "group_id": request.group_id,
                    "sample_count": preview["coverage"]["matched"],
                },
            )
            return current.model_copy(deep=True)
