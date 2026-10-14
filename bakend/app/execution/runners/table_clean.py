from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


@lru_cache(maxsize=8)
def _probe_pandas(python: str) -> tuple[bool, str]:
    executable = Path(python)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return False, f"未找到表格处理 Python：{executable}"
    code = "import importlib.util,json;print(json.dumps([m for m in ['pandas'] if importlib.util.find_spec(m) is None]))"
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and dependency probe
            [python, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=20, check=False,
        )
        missing = json.loads(completed.stdout.strip() or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"无法探测表格环境：{exc}"
    if completed.returncode != 0:
        return False, "表格环境探测失败：" + (completed.stderr.strip()[-300:] or str(completed.returncode))
    if missing:
        return False, "Scientific Python 缺少模块：" + ", ".join(missing)
    return True, "NeuroClaw 兼容的 pandas 环境可用"


class TableCleanRunner(ExternalCommandRunner):
    runner_id = "table.clean"
    label = "表格清洗"

    def __init__(self) -> None:
        self.executable = Path(os.getenv("LOCALFLOW_SKILL_PYTHON", "/opt/software/fsl/bin/python"))
        self.worker = Path(__file__).resolve().parents[1] / "table_clean_worker.py"

    def runtime_status(self) -> dict[str, object]:
        available, reason = _probe_pandas(str(self.executable))
        if available and not self.worker.is_file():
            available, reason = False, f"表格清洗 Worker 不存在：{self.worker}"
        return {
            "available": available,
            "reason": reason,
            "executable": str(self.executable),
            "environment_profile": "scientific-python",
            "environment_source": "NeuroClaw-compatible profile",
            "required_modules": ["pandas"],
        }

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        if context.step.input_bindings.get("table") is None:
            problems.append("缺少输入表格")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        source = self.required_binding(context, "table")
        output = context.output_directory / "cleaned.csv"
        report = context.output_directory / "cleaning-report.json"
        log = context.output_directory / "table-cleaning.log"
        argv = [
            str(self.executable), str(self.worker),
            "--input", str(source), "--output", str(output), "--report", str(report),
            "--missing-strategy", str(self.parameter(context, "missing_strategy", "keep")),
            "--missing-threshold", str(float(self.parameter(context, "missing_threshold", 1.0))),
            "--fill-value", str(self.parameter(context, "fill_value", "")),
        ]
        for parameter, flag in (
            ("deduplicate", "--deduplicate"),
            ("strip_whitespace", "--strip-whitespace"),
            ("normalize_columns", "--normalize-columns"),
        ):
            if bool(self.parameter(context, parameter, False)):
                argv.append(flag)
        self.execute(
            context,
            argv,
            environment={"PYTHONNOUSERSITE": "1"},
            log_path=log,
        )
        artifacts = [
            self.artifact(context, output, "generated_file", "清洗后表格", "text/csv"),
            self.artifact(context, report, "generated_file", "表格清洗报告", "application/json"),
            self.artifact(context, log, "execution_log", "表格清洗日志", "text/plain"),
        ]
        context.log("清洗表格、报告和日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="表格清洗完成。")
