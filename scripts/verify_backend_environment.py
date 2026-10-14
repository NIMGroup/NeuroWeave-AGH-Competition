#!/usr/bin/env python3
"""Fail before a release if the active backend differs from requirements.lock."""
from importlib import metadata
from pathlib import Path
from packaging.requirements import Requirement
import json


def verify():
    root = Path(__file__).resolve().parents[1]
    checked, mismatches = 0, []
    for line in (root / "bakend/requirements.lock").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed is None or installed not in requirement.specifier:
            mismatches.append({"package":requirement.name,"expected":str(requirement.specifier),"installed":installed})
        checked += 1
    if mismatches:
        raise RuntimeError("Backend dependency mismatch: " + json.dumps(mismatches))
    print(json.dumps({"locked_packages_verified":checked}))


if __name__ == "__main__":
    verify()
