"""Explicit, local reference documents; their content grants no execution capability."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re


@dataclass(frozen=True)
class SkillReference:
    id: str
    title: str
    path: str

    def public_dict(self):
        return {"id": self.id, "title": self.title}


def reference_specs(value):
    entries = value.get("references", [])
    if not isinstance(entries, list):
        raise ValueError("Skill references must be a list")
    result = []
    ids, paths = set(), set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "title", "path"}:
            raise ValueError("Invalid Skill reference fields")
        identity, title, path = entry["id"], entry["title"], entry["path"]
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", identity):
            raise ValueError("Invalid Skill reference identity")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Invalid Skill reference title")
        if not isinstance(path, str) or "\\" in path or any(ord(c) < 32 for c in path):
            raise ValueError("Invalid Skill reference path")
        relative = PurePosixPath(path)
        if (relative.is_absolute() or len(relative.parts) < 2
                or relative.parts[0] != "references" or ".." in relative.parts
                or relative.as_posix() != path or relative.suffix != ".md"):
            raise ValueError("Skill references must be Markdown within references/")
        if identity in ids or path in paths:
            raise ValueError("Duplicate Skill reference")
        ids.add(identity)
        paths.add(path)
        result.append(SkillReference(identity, title.strip(), path))
    return tuple(result)


def reference_path(skill_path, reference):
    root = Path(skill_path).resolve()
    folder = (root / "references").resolve()
    path = (root / reference.path).resolve()
    if not folder.is_relative_to(root) or not path.is_relative_to(folder):
        raise ValueError("Skill reference leaves its registered directory")
    if not path.is_file():
        raise ValueError("Registered Skill reference is missing: " + reference.id)
    return path
