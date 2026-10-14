from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class CapabilityNode(BaseModel):
    id: str
    label: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    method_ids: list[str] = Field(default_factory=list)
    pipeline_ids: list[str] = Field(default_factory=list)
    entity_kind: str = 'capability'
    category_ids: list[str] = Field(default_factory=list)
    execution_level: Literal["category", "advisory", "planning", "structured"] = "category"
    children: list["CapabilityNode"] = Field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


class CapabilityTree:
    """Functional navigation with explicit native Skill, Method and Pipeline links."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._signature: tuple[int, int] | None = None
        self._root: CapabilityNode | None = None
        self._nodes: dict[str, CapabilityNode] = {}
        self._parents: dict[str, str | None] = {}

    def compact(self) -> dict[str, Any]:
        self._load()
        assert self._root is not None
        return self._compact_node(self._root)

    @property
    def root_id(self) -> str:
        self._load()
        assert self._root is not None
        return self._root.id

    def node(self, capability_id: str) -> CapabilityNode | None:
        self._load()
        node = self._nodes.get(capability_id)
        return node.model_copy(deep=True) if node else None

    def children(self, capability_id: str) -> list[CapabilityNode]:
        self._load()
        node = self._nodes.get(capability_id)
        if node is None:
            return []
        return [child.model_copy(deep=True) for child in node.children]

    def routing_frontier(
        self,
        parent_ids: list[str],
        *,
        description_chars: int = 180,
        keyword_limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Return only direct children for the current routing level.

        Descendants and Skill ids are deliberately omitted so a model request never
        receives an accidental copy of the full taxonomy or reference document text.
        """
        self._load()
        groups: list[dict[str, Any]] = []
        for parent_id in dict.fromkeys(parent_ids):
            parent = self._nodes.get(parent_id)
            if parent is None or not parent.children:
                continue
            candidates: list[dict[str, Any]] = []
            for child in parent.children:
                candidate: dict[str, Any] = {
                    "id": child.id,
                    "label": child.label,
                    "description": child.description[:description_chars],
                    "node_type": "leaf" if child.is_leaf else "category",
                    "child_count": len(child.children),
                }
                if child.keywords:
                    candidate["keywords"] = child.keywords[:keyword_limit]
                if child.is_leaf:
                    candidate["execution_level"] = child.execution_level
                candidates.append(candidate)
            groups.append(
                {
                    "parent_id": parent_id,
                    "parent_path": self.label_path_for(parent_id),
                    "candidates": candidates,
                }
            )
        return groups

    def next_hops_for(
        self,
        parent_ids: list[str],
        target_leaf_ids: list[str],
    ) -> list[str]:
        """Map known leaf targets to their direct children below this frontier."""
        self._load()
        targets = [
            self.path_for(target_id)
            for target_id in dict.fromkeys(target_leaf_ids)
            if target_id in self._nodes and self._nodes[target_id].is_leaf
        ]
        hops: list[str] = []
        for parent_id in dict.fromkeys(parent_ids):
            parent = self._nodes.get(parent_id)
            if parent is None:
                continue
            child_ids = {child.id for child in parent.children}
            for path in targets:
                if parent_id not in path:
                    continue
                parent_index = path.index(parent_id)
                if parent_index + 1 >= len(path):
                    continue
                hop = path[parent_index + 1]
                if hop in child_ids and hop not in hops:
                    hops.append(hop)
        return hops

    def leaf(self, capability_id: str) -> CapabilityNode | None:
        self._load()
        node = self._nodes.get(capability_id)
        return node.model_copy(deep=True) if node and node.is_leaf else None

    def leaves(self) -> list[CapabilityNode]:
        self._load()
        return [
            node.model_copy(deep=True)
            for node in self._nodes.values()
            if node.is_leaf
        ]

    def path_for(self, capability_id: str) -> list[str]:
        self._load()
        if capability_id not in self._nodes:
            return []
        path: list[str] = []
        current: str | None = capability_id
        while current is not None:
            path.append(current)
            current = self._parents.get(current)
        return list(reversed(path))

    def label_path_for(self, capability_id: str) -> list[str]:
        return [
            self._nodes[node_id].label
            for node_id in self.path_for(capability_id)
        ]

    def covered_skill_ids(self) -> list[str]:
        self._load()
        return sorted(
            {
                skill_id
                for node in self._nodes.values()
                if node.is_leaf
                for skill_id in node.skill_ids
            }
        )

    def stats(self) -> dict[str, int]:
        self._load()
        leaves = [node for node in self._nodes.values() if node.is_leaf]
        return {
            "nodes": len(self._nodes),
            "methods_covered": len({m for node in leaves for m in node.method_ids}),
            "pipelines_covered": len({p for node in leaves for p in node.pipeline_ids}),
            "capabilities": len(leaves),
            "structured": sum(node.execution_level == "structured" for node in leaves),
            "planning": sum(node.execution_level == "planning" for node in leaves),
            "advisory": sum(node.execution_level == "advisory" for node in leaves),
            "skills_covered": len(self.covered_skill_ids()),
        }

    def _load(self) -> None:
        stat = self.path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法载入能力树 {self.path}: {exc}") from exc
        if payload.get("schema_version") != 1 or not isinstance(payload.get("root"), dict):
            raise RuntimeError("能力树格式不正确")
        root = CapabilityNode.model_validate(payload["root"])
        nodes: dict[str, CapabilityNode] = {}
        parents: dict[str, str | None] = {}

        def visit(node: CapabilityNode, parent: str | None) -> None:
            if node.id in nodes:
                raise RuntimeError(f"能力树包含重复节点：{node.id}")
            nodes[node.id] = node
            parents[node.id] = parent
            for child in node.children:
                visit(child, node.id)

        visit(root, None)
        self._signature = signature
        self._root = root
        self._nodes = nodes
        self._parents = parents

    @staticmethod
    def _compact_node(node: CapabilityNode) -> dict[str, Any]:
        result: dict[str, Any] = {"id": node.id, "label": node.label}
        if node.description:
            result["description"] = node.description
        if node.keywords:
            result["keywords"] = node.keywords
        if node.is_leaf:
            result["execution_level"] = node.execution_level
            result['skill_ids'] = node.skill_ids
            result['method_ids'] = node.method_ids
            result['pipeline_ids'] = node.pipeline_ids
        result['entity_kind'] = node.entity_kind
        if node.children:
            result["children"] = [CapabilityTree._compact_node(child) for child in node.children]
        return result
