from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .capability_registry import CapabilityRegistry
from .capability_tree import CapabilityTree
from .skill_loader import SkillLoader


_GENERIC_IDENTITY_PARTS = {
    "app",
    "batch",
    "brain",
    "builder",
    "cleaning",
    "controller",
    "core",
    "data",
    "dataset",
    "image",
    "imaging",
    "model",
    "models",
    "manager",
    "parcellation",
    "neuro",
    "neuroimaging",
    "pipeline",
    "processing",
    "preprocessing",
    "reconstruction",
    "registration",
    "report",
    "research",
    "skill",
    "segmentation",
    "tool",
    "workflow",
    "visualization",
}

_SEARCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "do",
    "for",
    "from",
    "help",
    "i",
    "image",
    "images",
    "in",
    "is",
    "it",
    "method",
    "of",
    "on",
    "perform",
    "please",
    "process",
    "processing",
    "run",
    "skill",
    "the",
    "this",
    "to",
    "tool",
    "use",
    "using",
    "want",
    "with",
    "workflow",
    "一个",
    "进行",
    "使用",
    "处理",
    "帮我",
    "我想",
    "我要",
    "数据",
    "方法",
    "流程",
    "需要",
}

_LATIN_CANONICAL = {
    "harmonisation": "harmonize",
    "harmonization": "harmonize",
    "harmonizing": "harmonize",
    "parcellate": "parcellation",
    "parcellating": "parcellation",
    "preprocessing": "preprocess",
    "preprocessed": "preprocess",
    "reconstruct": "reconstruction",
    "reconstructing": "reconstruction",
    "register": "registration",
    "registering": "registration",
    "segmentation": "segment",
    "segmented": "segment",
    "segmenting": "segment",
    "t1w": "t1",
    "t2w": "t2",
}


def _latin_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9+._-]*", str(text or "").lower())


def _canonical_latin(token: str) -> str:
    return _LATIN_CANONICAL.get(token, token)


def _search_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _latin_words(text):
        pieces = [part for part in re.split(r"[._-]+", raw) if part]
        for piece in pieces:
            canonical = _canonical_latin(piece)
            if len(canonical) >= 2 and canonical not in _SEARCH_STOPWORDS:
                tokens.add(canonical)

    for chunk in re.findall(r"[\u3400-\u9fff]+", str(text or "")):
        if 2 <= len(chunk) <= 12 and chunk not in _SEARCH_STOPWORDS:
            tokens.add(chunk)
        for width in (2, 3, 4):
            if len(chunk) < width:
                continue
            for index in range(len(chunk) - width + 1):
                token = chunk[index : index + width]
                if token not in _SEARCH_STOPWORDS:
                    tokens.add(token)
    return tokens


def _contains_alias(message: str, alias: str) -> bool:
    lowered = message.lower()
    alias = alias.lower().strip()
    if not alias:
        return False
    if re.search(r"[\u3400-\u9fff]", alias):
        return alias in lowered
    parts = [part for part in re.split(r"[^a-z0-9]+", alias) if part]
    return bool(parts) and all(
        re.search(rf"(?<![a-z0-9]){re.escape(part)}(?![a-z0-9])", lowered)
        for part in parts
    )


@dataclass(frozen=True)
class RetrievalCandidate:
    capability_id: str
    skill_id: str
    label: str
    score: float
    match_type: str
    matched_terms: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "skill_id": self.skill_id,
            "label": self.label,
            "score": round(self.score, 3),
            "match_type": self.match_type,
            "matched_terms": list(self.matched_terms),
        }


@dataclass(frozen=True)
class _SearchDocument:
    capability_id: str
    skill_id: str
    label: str
    skill_type: str
    identity_aliases: tuple[str, ...]
    token_weights: dict[str, float]


class CapabilityRetriever:
    """Local sparse retrieval over Skill metadata before model/tree routing.

    The model receives only the small result set. Exact software/Skill names are
    treated as strong anchors; semantic lexical matches and selected Skills are
    candidates, not forced decisions.
    """

    def __init__(
        self,
        tree: CapabilityTree,
        registry: CapabilityRegistry,
        skill_loader: SkillLoader,
    ) -> None:
        self.tree = tree
        self.registry = registry
        self.skill_loader = skill_loader
        self._documents = self._build_documents()
        self._idf = self._build_idf(self._documents)

    def retrieve(
        self,
        message: str,
        selected_skills: list[str] | None = None,
        *,
        limit: int = 8,
    ) -> list[RetrievalCandidate]:
        query_tokens = _search_tokens(message)
        explicit_ids = self.explicit_capability_ids(message)
        selected = {(skill.name if (skill := self.skill_loader.find(item)) else item).lower() for item in selected_skills or []}
        candidates: list[RetrievalCandidate] = []

        for document in self._documents:
            contributions = {
                token: document.token_weights[token] * self._idf.get(token, 1.0)
                for token in query_tokens.intersection(document.token_weights)
            }
            score = sum(contributions.values())
            match_type = "lexical"
            if document.capability_id in explicit_ids:
                score += 100.0
                match_type = "explicit"
            elif not explicit_ids and document.skill_id.lower() in selected:
                score += 12.0
                match_type = "selected"
            if score <= 0:
                continue
            matched_terms = tuple(
                token
                for token, _value in sorted(
                    contributions.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:6]
            )
            candidates.append(
                RetrievalCandidate(
                    capability_id=document.capability_id,
                    skill_id=document.skill_id,
                    label=document.label,
                    score=score,
                    match_type=match_type,
                    matched_terms=matched_terms,
                )
            )

        candidates.sort(
            key=lambda item: (
                0 if item.match_type == "explicit" else 1,
                -item.score,
                item.capability_id,
            )
        )
        return candidates[: max(1, limit)]

    def explicit_capability_ids(self, message: str) -> list[str]:
        full_name_matches = [
            document.capability_id
            for document in self._documents
            if _contains_alias(message, document.skill_id)
        ]
        if full_name_matches:
            return list(dict.fromkeys(full_name_matches))

        matches: list[tuple[int, int, str]] = []
        lowered = message.lower()
        for document in self._documents:
            positions = [
                lowered.find(alias.lower())
                for alias in document.identity_aliases
                if _contains_alias(message, alias)
            ]
            if positions:
                priority = 2 if document.skill_type in {"tool", "software", "model"} else 1
                matches.append(
                    (
                        priority,
                        min(position for position in positions if position >= 0),
                        document.capability_id,
                    )
                )
        if not matches:
            return []
        highest_priority = max(priority for priority, _position, _capability_id in matches)
        return list(
            dict.fromkeys(
                capability_id
                for priority, _position, capability_id in sorted(
                    matches,
                    key=lambda item: (-item[0], item[1], item[2]),
                )
                if priority == highest_priority
            )
        )

    def _build_documents(self) -> list[_SearchDocument]:
        skills = {skill.name: skill for skill in self.skill_loader.load_all()}
        leaves = [node for node in self.tree.leaves() if node.id.startswith(("skill.", "software."))]

        primary_aliases: dict[str, set[str]] = {}
        secondary_aliases: dict[str, set[str]] = {}
        primary_owners: dict[str, set[str]] = {}
        secondary_owners: dict[str, set[str]] = {}
        identity_specificity: dict[str, int] = {}
        for node in leaves:
            skill_id = node.skill_ids[0] if node.skill_ids else node.id.removeprefix("skill.")
            skill = skills.get(skill_id)
            primary = self._identity_alias_candidates(node.id, skill_id, [])
            if skill is not None:
                primary.update(self._identity_alias_candidates(node.id, skill.name, []))
            combined = self._identity_alias_candidates(node.id, skill_id, node.keywords)
            secondary = combined - primary
            primary_aliases[node.id] = primary
            secondary_aliases[node.id] = secondary
            identity_specificity[node.id] = max(
                1,
                len(
                    [
                        part
                        for part in re.split(r"[-_]+", skill_id.lower())
                        if part and part not in _GENERIC_IDENTITY_PARTS
                    ]
                ),
            )
            for alias in primary:
                primary_owners.setdefault(alias, set()).add(node.id)
            for alias in secondary:
                secondary_owners.setdefault(alias, set()).add(node.id)

        documents: list[_SearchDocument] = []
        for node in leaves:
            skill_id = node.skill_ids[0] if node.skill_ids else node.id.removeprefix("skill.")
            skill = skills.get(skill_id)
            methods = self.registry.methods_for(node.id)
            weighted_fields: list[tuple[float, Iterable[str]]] = [
                (6.0, [node.id, node.label, skill_id]),
                (4.0, node.keywords),
                (2.4, [node.description]),
            ]
            if skill is not None:
                weighted_fields.extend(
                    [
                        (5.0, [skill.display_name, skill.name]),
                        (1.8, [skill.description, skill.summary_en, skill.summary_zh]),
                    ]
                )
            method_text: list[str] = []
            option_text: list[str] = []
            for method in methods:
                method_text.extend([method.label, method.description, method.runner_id])
                for parameter in method.parameters:
                    option_text.extend([parameter.label, parameter.description])
                    option_text.extend(
                        text
                        for option in parameter.options
                        for text in (str(option.value), option.label, option.description)
                    )
                option_text.extend(reference.command for reference in method.command_references)
            weighted_fields.extend([(2.5, method_text), (2.0, option_text)])

            token_weights: dict[str, float] = {}
            for weight, values in weighted_fields:
                for value in values:
                    for token in _search_tokens(value):
                        token_weights[token] = max(token_weights.get(token, 0.0), weight)

            safe_primary = {
                alias
                for alias in primary_aliases[node.id]
                if node.id
                in {
                    owner
                    for owner in primary_owners.get(alias, set())
                    if identity_specificity[owner]
                    == min(
                        identity_specificity[item]
                        for item in primary_owners.get(alias, {node.id})
                    )
                }
                and sum(
                    identity_specificity[owner]
                    == identity_specificity[node.id]
                    for owner in primary_owners.get(alias, set())
                )
                == 1
            }
            safe_secondary = {
                alias
                for alias in secondary_aliases[node.id]
                if secondary_owners.get(alias) == {node.id}
                and not (primary_owners.get(alias, set()) - {node.id})
            }
            identity_aliases = tuple(
                sorted(
                    safe_primary | safe_secondary,
                    key=lambda item: (-len(item), item),
                )
            )
            documents.append(
                _SearchDocument(
                    capability_id=node.id,
                    skill_id=skill_id,
                    label=node.label,
                    skill_type=skill.skill_type if skill is not None else "workflow",
                    identity_aliases=identity_aliases,
                    token_weights=token_weights,
                )
            )
        return documents

    @staticmethod
    def _identity_alias_candidates(
        capability_id: str,
        skill_id: str,
        keywords: list[str],
    ) -> set[str]:
        del capability_id
        aliases: set[str] = {skill_id.lower()}
        parts = [part for part in re.split(r"[-_]+", skill_id.lower()) if part]
        meaningful = [part for part in parts if part not in _GENERIC_IDENTITY_PARTS]
        if meaningful:
            aliases.add(" ".join(meaningful))
            aliases.add("-".join(meaningful))
        aliases.update(
            part
            for part in meaningful
            if len(part) >= 3 and part not in _GENERIC_IDENTITY_PARTS
        )
        for keyword in keywords:
            words = _latin_words(keyword)
            if not words or any(word in _GENERIC_IDENTITY_PARTS for word in words):
                continue
            if len(words) == 1 and len(words[0]) >= 3:
                aliases.add(words[0])
            elif 1 < len(words) <= 3:
                aliases.add(" ".join(words))
        return {alias for alias in aliases if len(alias) >= 3}

    @staticmethod
    def _build_idf(documents: list[_SearchDocument]) -> dict[str, float]:
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(document.token_weights)
        total = max(1, len(documents))
        return {
            token: math.log((total + 1) / (frequency + 1)) + 1.0
            for token, frequency in document_frequency.items()
        }
