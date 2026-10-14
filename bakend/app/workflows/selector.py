"""CapabilitySelector: bounded capability hints; never decides to execute."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..models import (
    ChatRequest,
)
from ..task_scope import existing_input_capabilities

if TYPE_CHECKING:
    from ..capability_registry import CapabilityRegistry
    from ..capability_retriever import CapabilityRetriever
    from ..capability_tree import CapabilityTree
    from ..skill_loader import SkillLoader


class CapabilitySelector:
    def __init__(
        self,
        registry: CapabilityRegistry,
        capability_tree: CapabilityTree | None,
        skill_loader: SkillLoader | None,
        capability_retriever: CapabilityRetriever | None,
    ) -> None:
        self.registry = registry
        self.capability_tree = capability_tree
        self.skill_loader = skill_loader
        self.capability_retriever = capability_retriever

    def prune_generic_skill_leaves(self, capability_ids: list[str]) -> list[str]:
        """Drop a whole-Skill leaf when a more specific leaf from it was selected."""
        if self.capability_tree is None:
            return capability_ids
        specific_skill_ids = {
            skill_id
            for capability_id in capability_ids
            if not capability_id.startswith("skill.")
            for node in [self.capability_tree.leaf(capability_id)]
            if node is not None
            for skill_id in node.skill_ids
        }
        return [
            capability_id
            for capability_id in capability_ids
            if not (
                capability_id.startswith("skill.")
                and (node := self.capability_tree.leaf(capability_id)) is not None
                and specific_skill_ids.intersection(node.skill_ids)
            )
        ]

    def drop_shadowed_modality_skills(
        self,
        capability_ids: list[str],
        explicit_skill_ids: list[str],
    ) -> list[str]:
        """A named concrete tool supersedes broad modality Skills for the same turn."""
        if self.capability_tree is None or self.skill_loader is None:
            return capability_ids

        explicit = set(explicit_skill_ids)
        has_specific_tool = False
        for capability_id in explicit:
            node = self.capability_tree.leaf(capability_id)
            for skill_id in node.skill_ids if node is not None else []:
                skill = self.skill_loader.find(skill_id)
                if skill is not None and skill.skill_type in {
                    "tool",
                    "software",
                    "model",
                }:
                    has_specific_tool = True
                    break
            if has_specific_tool:
                break
        if not has_specific_tool:
            return capability_ids

        filtered: list[str] = []
        for capability_id in capability_ids:
            if capability_id in explicit or not capability_id.startswith("skill."):
                filtered.append(capability_id)
                continue
            node = self.capability_tree.leaf(capability_id)
            skill_types = {
                skill.skill_type
                for skill_id in node.skill_ids
                if node is not None
                if (skill := self.skill_loader.find(skill_id)) is not None
            }
            if "modality" not in skill_types:
                filtered.append(capability_id)
        return filtered

    def specific_intent_catalog(
        self,
        allowed: list[str],
        included_skill_capabilities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.capability_tree is None:
            return []
        included_skills = set(included_skill_capabilities or [])
        catalog: list[dict[str, Any]] = []
        for node in self.capability_tree.leaves():
            if node.id not in allowed:
                continue
            if node.id.startswith("skill.") and node.id not in included_skills:
                continue
            methods = self.registry.methods_for(node.id)
            if not methods:
                continue
            # Task names now come from explicit Skill references, not a synthetic
            # "operation" parameter that used to mix many execution contracts.
            operation_options = [
                {
                    "value": method.method_id,
                    "label": method.label,
                    "description": (method.skill_role or method.description)[:220],
                    "entry_kind": method.entry_kind,
                }
                for method in methods[:24]
            ]
            catalog.append(
                {
                    "capability_id": node.id,
                    "label": node.label,
                    "description": node.description,
                    "keywords": node.keywords[:8],
                    "input_types": sorted(
                        {item for method in methods for item in method.input_types}
                    ),
                    "output_types": sorted(
                        {item for method in methods for item in method.output_types}
                    ),
                    "task_options": operation_options,
                }
            )
        return catalog

    def heuristic_capabilities(
        self,
        request: ChatRequest,
        allowed: list[str],
    ) -> list[str]:
        """Deterministic fallback used only when the router model is unavailable."""
        lowered = request.message.lower()
        conceptual = any(
            token in lowered
            for token in (
                "是什么",
                "什么意思",
                "有什么区别",
                "原理",
                "介绍",
                "解释",
                "了解",
                "怎么理解",
                "what is",
                "explain",
            )
        )
        explicit_execution = any(
            token in lowered
            for token in (
                "做一下",
                "处理这个",
                "开始处理",
                "帮我做",
                "帮我处理",
                "运行",
                "执行",
                "进行",
                "生成",
                "run ",
                "execute",
            )
        )
        if conceptual and not explicit_execution:
            return []
        actionable = any(
            token in lowered
            for token in (
                "做",
                "处理",
                "整理",
                "转换",
                "配准",
                "预处理",
                "分割",
                "拟合",
                "去除",
                "提取",
                "重建",
                "训练",
                "预测",
                "清洗",
                "校正",
                "可视化",
                "使用",
                "采用",
                "用",
                "计算",
                "生成",
                "检查",
                "分析",
                "流程",
                "organize",
                "convert",
                "register",
                "registration",
                "preprocess",
                "calculate",
                "run",
                "use ",
                "using ",
                "segment",
                "segmentation",
                "进行",
                "执行",
                "完成",
                "开始",
                "想要",
                "需要",
                "希望",
                "帮我",
            )
        )
        if not actionable:
            return []

        dwi_context = bool(
            re.search(r"\b(dwi|dti|diffusion|qsiprep|eddy|dtifit)\b", lowered)
            or any(term in lowered for term in ("扩散磁共振", "扩散成像", "扩散指标"))
        )
        explicit_skill_capabilities = self.explicit_skill_capabilities(
            request.message,
            allowed,
        )
        patterns: list[tuple[str, tuple[str, ...]]] = [
            ("dwi.inspect", ("巡检", "检查数据", "检查这个数据", "inspect")),
            ("dwi.bids", ("bids", "整理数据", "组织数据")),
            (
                "image.registration",
                ("图像配准", "影像配准", "registration", "flirt", "ants", "syn"),
            ),
            ("dwi.report", ("报告", "qc", "质量控制", "结果汇总")),
            (
                "dwi.response",
                (
                    "响应函数",
                    "response function",
                    "dwi2response",
                    "dhollander",
                    "tournier",
                ),
            ),
            ("dwi.fod", ("纤维方向分布", "dwi2fod", "msmt-csd", "msmt_csd", "fod")),
            ("dwi.five_tt", ("5tt", "five tissue", "5ttgen", "五组织")),
            (
                "dwi.tractography",
                ("纤维追踪", "tractography", "probtrackx", "tckgen"),
            ),
            (
                "dwi.connectome",
                ("结构连接组", "connectome", "连接矩阵", "tck2connectome"),
            ),
        ]
        if not explicit_skill_capabilities:
            patterns.append(
                (
                    "skill.smri-skill",
                    (
                        "组织分割",
                        "组织分类",
                        "灰质白质脑脊液",
                        "tissue segmentation",
                        "tissue classification",
                        "gm/wm/csf",
                    ),
                )
            )
        if dwi_context:
            patterns.append(
                ("dwi.preprocess", ("预处理", "eddy", "topup", "qsiprep", "运动校正"))
            )
        matched: list[tuple[int, str]] = []
        for capability_id, terms in patterns:
            if capability_id in existing_input_capabilities(request.message):
                continue
            positions = [
                lowered.find(term) for term in terms if lowered.find(term) >= 0
            ]
            if positions and capability_id in allowed:
                matched.append((min(positions), capability_id))
        if (
            re.search(r"\b(fa|md|ad|rd|dti|dtifit|tensor)\b", lowered)
            or "张量" in lowered
            or "扩散指标" in lowered
        ) and "dwi.tensor_metrics" in allowed:
            matched.append((len(lowered), "dwi.tensor_metrics"))

        if matched:
            return list(
                dict.fromkeys(
                    capability_id for _position, capability_id in sorted(matched)
                )
            )

        if (
            any(term in lowered for term in ("dwi", "dti", "扩散磁共振", "扩散成像"))
            and "dwi.preprocess" in allowed
        ):
            return ["dwi.preprocess"]

        if explicit_skill_capabilities:
            return explicit_skill_capabilities

        return []

    def explicit_skill_capabilities(
        self,
        message: str,
        allowed: list[str],
    ) -> list[str]:
        """Match software/Skill names in the current turn before sticky selections."""
        if self.capability_retriever is not None:
            retrieved = [
                capability_id
                for capability_id in self.capability_retriever.explicit_capability_ids(
                    message
                )
                if capability_id in allowed
            ]
            if retrieved:
                return retrieved[:4]

        lowered = message.lower()
        normalized_text = re.sub(r"[^a-z0-9]+", " ", lowered)
        explicit_aliases: dict[str, tuple[str, ...]] = {
            "skill.asl-skill": ("脑灌注", "动脉自旋标记"),
            "skill.dwi-skill": ("扩散磁共振", "扩散成像", "纤维追踪"),
            "skill.eeg-skill": ("脑电",),
            "skill.fmri-skill": ("功能磁共振", "功能连接"),
            "skill.meg-skill": ("脑磁",),
            "skill.pet-skill": ("正电子发射",),
            "skill.smri-skill": ("结构磁共振", "结构像"),
            "skill.table-cleaning": ("表格清洗", "缺失值", "异常值"),
            "skill.image-batch-processing": ("影像批处理", "图像批处理"),
            "skill.freesurfer-tool": (
                "FreeSurfer",
                "recon-all",
                "aseg",
                "aparc",
                "皮层重建",
                "皮层分割",
                "皮层下分割",
            ),
            "skill.wmh-segmentation": ("WMH", "白质高信号", "白质病灶"),
        }
        advisory_matches: list[tuple[int, str]] = []
        for node in self.capability_tree.leaves() if self.capability_tree else []:
            if node.id not in allowed or not node.id.startswith("skill."):
                continue
            skill_name = node.skill_ids[0] if node.skill_ids else ""
            parts = [
                part
                for part in re.split(r"[-_]+", skill_name.lower())
                if part not in {"skill", "tool"}
            ]
            name_match = bool(parts) and all(
                re.search(rf"\b{re.escape(part)}\b", normalized_text) for part in parts
            )
            terms = [node.label, *explicit_aliases.get(node.id, ())]
            positions = [
                lowered.find(term.lower())
                for term in terms
                if len(term.strip()) >= 3 and lowered.find(term.lower()) >= 0
            ]
            if name_match:
                positions.append(
                    min(
                        position
                        for part in parts
                        if (position := normalized_text.find(part)) >= 0
                    )
                )
            if positions:
                advisory_matches.append((min(positions), node.id))
        return list(
            dict.fromkeys(
                capability_id for _position, capability_id in sorted(advisory_matches)
            )
        )[:4]
