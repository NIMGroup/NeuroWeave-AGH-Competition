from __future__ import annotations

"""Audit configured NeuroWeave runners without executing scientific jobs.

Run inside the backend container with::

    python -m app.execution.runtime_audit

The audit checks registered runner availability, executable shebangs, missing
ELF shared libraries and known delegated helpers used by composite tools.
"""

import json
import os
import shutil
import subprocess
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .runner_registry import RunnerRegistry
from .runners.freesurfer import FreeSurferReconAllRunner
from .runners.mrtrix_pipeline import MrtrixFiveTtGenRunner


SYSTEM_COMMANDS = (
    "bash",
    "tcsh",
    "csh",
    "bc",
    "perl",
    "awk",
    "sed",
    "grep",
    "find",
    "hostname",
    "uname",
    "date",
    "cut",
    "sort",
    "tr",
    "wc",
    "gzip",
    "tar",
)


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _missing_libraries(path: Path) -> list[str]:
    if not _is_elf(path) or shutil.which("ldd") is None:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 - diagnostic argv, no shell
            ["ldd", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"ldd probe failed: {exc}"]
    return [line.strip() for line in completed.stdout.splitlines() if "not found" in line]


def _shebang_problem(path: Path) -> str | None:
    if not path.is_file() or _is_elf(path):
        return None
    try:
        first = path.open("rb").readline(512).decode("utf-8", errors="replace").strip()
    except OSError as exc:
        return f"cannot read: {exc}"
    if not first.startswith("#!"):
        return None
    interpreter = first[2:].strip().split()[0]
    if Path(interpreter).is_absolute() and Path(interpreter).is_file():
        return None
    if not Path(interpreter).is_absolute() and shutil.which(Path(interpreter).name):
        return None
    return f"missing shebang interpreter: {interpreter}"


def _known_delegated_tools() -> dict[str, Path]:
    freesurfer = Path(
        os.getenv(
            "LOCALFLOW_FREESURFER_HOME",
            "/opt/software/freesurfer-7.4.1/usr/local/freesurfer/7.4.1",
        )
    )
    fsl = Path(os.getenv("LOCALFLOW_FSL_ROOT", "/opt/software/fsl"))
    mrtrix = Path(os.getenv("LOCALFLOW_MRTRIX_ROOT", "/opt/software/mrtrix3-master"))
    return {
        "freesurfer.UpdateNeeded": freesurfer / "bin" / "UpdateNeeded",
        "freesurfer.fs-check-version": freesurfer / "bin" / "fs-check-version",
        "freesurfer.fs_temp_file": freesurfer / "bin" / "fs_temp_file",
        "freesurfer.nu_correct": freesurfer / "mni" / "bin" / "nu_correct",
        "freesurfer.gauss_4dfp": freesurfer / "bin" / "gauss_4dfp",
        "freesurfer.mri_convert": freesurfer / "bin" / "mri_convert",
        "freesurfer.mri_normalize": freesurfer / "bin" / "mri_normalize",
        "freesurfer.mri_watershed": freesurfer / "bin" / "mri_watershed",
        "freesurfer.mri_em_register": freesurfer / "bin" / "mri_em_register",
        "freesurfer.mri_mask": freesurfer / "bin" / "mri_mask",
        "freesurfer.mri_segment": freesurfer / "bin" / "mri_segment",
        "fsl.bet": fsl / "bin" / "bet",
        "fsl.fast": fsl / "bin" / "fast",
        "fsl.first": fsl / "bin" / "first",
        "fsl.fslmaths": fsl / "bin" / "fslmaths",
        "fsl.standard_space_roi": fsl / "bin" / "standard_space_roi",
        "mrtrix.mrconvert": mrtrix / "bin" / "mrconvert",
        "mrtrix.mrthreshold": mrtrix / "bin" / "mrthreshold",
        "mrtrix.mrgrid": mrtrix / "bin" / "mrgrid",
        "mrtrix.maskfilter": mrtrix / "bin" / "maskfilter",
        "mrtrix.mrtransform": mrtrix / "bin" / "mrtransform",
        "mrtrix.meshconvert": mrtrix / "bin" / "meshconvert",
        "mrtrix.mesh2voxel": mrtrix / "bin" / "mesh2voxel",
        "mrtrix.mrcalc": mrtrix / "bin" / "mrcalc",
        "mrtrix.mrcat": mrtrix / "bin" / "mrcat",
        "mrtrix.mrmath": mrtrix / "bin" / "mrmath",
    }


def _freesurfer_smoke_checks() -> dict[str, Any]:
    """Start lightweight helpers to validate csh and bundled Perl modules."""

    runner = FreeSurferReconAllRunner()
    environment = {
        **os.environ,
        **runner.execution_environment(Path("/tmp/localflow-freesurfer-audit")),
    }
    commands = {
        "UpdateNeeded": (
            str(runner.update_needed),
            "/tmp/localflow-audit-missing-target",
            "/tmp/localflow-audit-missing-source",
        ),
        "nu_correct": (str(runner.nu_correct), "-version"),
    }
    checks: dict[str, Any] = {}
    for label, argv in commands.items():
        try:
            completed = subprocess.run(  # noqa: S603 - fixed diagnostic argv, no shell
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=20,
                check=False,
                env=environment,
            )
            checks[label] = {
                "ok": completed.returncode == 0,
                "return_code": completed.returncode,
                "output": completed.stdout.strip()[-500:],
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks[label] = {"ok": False, "error": str(exc)}
    return checks


def _runner_environment_checks() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    five_tt_environment = MrtrixFiveTtGenRunner.execution_environment()
    for command in (
        "5ttgen",
        "mrconvert",
        "mrcalc",
        "mrcat",
        "mrgrid",
        "mrmath",
        "mrthreshold",
        "maskfilter",
        "meshconvert",
        "mesh2voxel",
        "bet",
        "fast",
        "first",
        "fslmaths",
        "standard_space_roi",
    ):
        resolved = shutil.which(command, path=five_tt_environment["PATH"])
        checks[f"mrtrix.5ttgen:{command}"] = {
            "ok": resolved is not None,
            "resolved": resolved,
        }
    return checks


def _deep_software_scan() -> dict[str, Any]:
    """Scan every top-level executable in software roots used by runners."""

    roots = {
        "freesurfer.bin": Path(
            os.getenv(
                "LOCALFLOW_FREESURFER_HOME",
                "/opt/software/freesurfer-7.4.1/usr/local/freesurfer/7.4.1",
            )
        )
        / "bin",
        "freesurfer.mni": Path(
            os.getenv(
                "LOCALFLOW_FREESURFER_HOME",
                "/opt/software/freesurfer-7.4.1/usr/local/freesurfer/7.4.1",
            )
        )
        / "mni"
        / "bin",
        "fsl.bin": Path(os.getenv("LOCALFLOW_FSL_ROOT", "/opt/software/fsl")) / "bin",
        "mrtrix.bin": Path(
            os.getenv("LOCALFLOW_MRTRIX_ROOT", "/opt/software/mrtrix3-master")
        )
        / "bin",
        "ants.bin": Path(os.getenv("LOCALFLOW_ANTS_ROOT", "/opt/software/ants-2.6.5"))
        / "bin",
    }
    paths: dict[str, Path] = {}
    seen: set[Path] = set()
    for root_label, root in roots.items():
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            resolved = path.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            paths[f"{root_label}:{path.name}"] = path

    shebang_problems = {
        label: problem
        for label, path in paths.items()
        if (problem := _shebang_problem(path)) is not None
    }
    elf_paths = {label: path for label, path in paths.items() if _is_elf(path)}
    missing_libraries: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(elf_paths)))) as executor:
        futures = {
            label: executor.submit(_missing_libraries, path)
            for label, path in elf_paths.items()
        }
        for label, future in futures.items():
            missing = future.result()
            if missing:
                missing_libraries[label] = missing
    return {
        "roots": {label: str(path) for label, path in roots.items()},
        "executable_count": len(paths),
        "elf_count": len(elf_paths),
        "shebang_problems": shebang_problems,
        "missing_libraries": missing_libraries,
    }


def build_report(*, deep: bool = False) -> dict[str, Any]:
    registry = RunnerRegistry()
    runners = registry.public_catalog()
    executable_paths: dict[str, Path] = {}
    available_runner_ids = {
        str(item["runner_id"]) for item in runners if item.get("available")
    }
    for item in runners:
        for key, value in item.items():
            if "executable" in key and isinstance(value, str) and value.startswith("/"):
                executable_paths[f"{item['runner_id']}.{key}"] = Path(value)
    executable_paths.update(_known_delegated_tools())

    paths: dict[str, Any] = {}
    for label, path in sorted(executable_paths.items()):
        exists = path.is_file()
        path_report: dict[str, Any] = {
            "path": str(path),
            "exists": exists,
            "executable": bool(exists and os.access(path, os.X_OK)),
        }
        if exists:
            shebang = _shebang_problem(path)
            libraries = _missing_libraries(path)
            if shebang:
                path_report["shebang_problem"] = shebang
            if libraries:
                path_report["missing_libraries"] = libraries
        paths[label] = path_report

    system = {command: shutil.which(command) for command in SYSTEM_COMMANDS}
    blocking_paths = {
        label: details
        for label, details in paths.items()
        if (
            label.startswith(("freesurfer.", "fsl.", "mrtrix."))
            or label.rsplit(".executable", 1)[0] in available_runner_ids
        )
        and label != "fsl.eddy.cuda_executable"
        and (
            not details["exists"]
            or not details["executable"]
            or details.get("shebang_problem")
            or details.get("missing_libraries")
        )
    }
    unavailable_runners = [item for item in runners if not item.get("available")]
    smoke_checks = _freesurfer_smoke_checks()
    environment_checks = _runner_environment_checks()
    report = {
        "ok": not any(value is None for value in system.values())
        and not blocking_paths
        and all(check.get("ok") for check in smoke_checks.values())
        and all(check.get("ok") for check in environment_checks.values()),
        "ready_runner_count": len(runners) - len(unavailable_runners),
        "registered_runner_count": len(runners),
        "system_commands": system,
        "runners": runners,
        "unavailable_runners": unavailable_runners,
        "path_checks": paths,
        "blocking_path_checks": blocking_paths,
        "smoke_checks": smoke_checks,
        "runner_environment_checks": environment_checks,
    }
    if deep:
        report["deep_software_scan"] = _deep_software_scan()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit NeuroWeave runner environments")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="also inspect every executable in configured scientific software bins",
    )
    arguments = parser.parse_args()
    report = build_report(deep=arguments.deep)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
