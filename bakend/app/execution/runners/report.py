from __future__ import annotations

import html
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


class ReportBuilderRunner:
    runner_id = "report.build"
    label = "NeuroWeave 可复现性报告"

    def runtime_status(self) -> dict[str, object]:
        return {
            "available": True,
            "reason": "使用 NeuroWeave 内置报告 Runner",
            "executable": "python:report-builder",
            "environment_profile": "localflow-backend",
        }

    def preflight(self, context: RunnerContext) -> list[str]:
        try:
            context.output_directory.mkdir(parents=True, exist_ok=True)
            probe = context.output_directory / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return [f"报告输出目录不可写：{exc}"]
        return []

    def run(self, context: RunnerContext) -> RunnerResult:
        parameters = context.step.selected_parameters
        output_format = str(parameters.get("output_format", "html"))
        if output_format not in {"html", "markdown", "json"}:
            raise RuntimeError(f"不支持的报告格式：{output_format}")
        context.log("正在汇总流程、参数和环境快照", 25.0, "info")
        report = self._payload(context)
        if bool(parameters.get("anonymize_paths", True)):
            report = self._anonymize(report, context)
        if not bool(parameters.get("include_commands", True)):
            report.pop("dry_run", None)
        if not bool(parameters.get("include_environment", True)):
            report.pop("environment", None)
        suffix = {"html": ".html", "markdown": ".md", "json": ".json"}[output_format]
        output = context.output_directory / f"localflow-report{suffix}"
        if output_format == "json":
            rendered = json.dumps(report, ensure_ascii=False, indent=2)
            media_type = "application/json"
        elif output_format == "markdown":
            rendered = self._markdown(report)
            media_type = "text/markdown"
        else:
            rendered = self._html(report)
            media_type = "text/html"
        output.write_text(rendered + "\n", encoding="utf-8")
        artifact = ExternalCommandRunner.artifact(
            context,
            output,
            "processing_report",
            "NeuroWeave 处理报告",
            media_type,
            {"format": output_format, "workflow_revision": context.workflow.revision},
        )
        context.log("报告已生成并完成校验和登记", 100.0, "info")
        return RunnerResult(artifacts=[artifact], message=f"已生成 {output_format.upper()} 处理报告。")

    @staticmethod
    def _payload(context: RunnerContext) -> dict[str, Any]:
        workflow = context.workflow
        return {
            "schema_version": 1,
            "workflow": {
                "id": workflow.workflow_id,
                "revision": workflow.revision,
                "title": workflow.title,
                "summary": workflow.summary,
                "original_request": workflow.original_request,
                "planner_source": workflow.planner_source,
                "dataset": workflow.dataset.model_dump(mode="json") if workflow.dataset else None,
                "steps": [
                    {
                        "id": step.id,
                        "order": step.order,
                        "title": step.title,
                        "capability": step.capability,
                        "selected_method_id": step.selected_method_id,
                        "selected_parameters": step.selected_parameters,
                        "input_bindings": {
                            name: binding.model_dump(mode="json")
                            for name, binding in step.input_bindings.items()
                        },
                    }
                    for step in workflow.steps
                ],
            },
            "dry_run": workflow.dry_run_report.model_dump(mode="json") if workflow.dry_run_report else None,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "fsl_root": os.getenv("LOCALFLOW_FSL_ROOT", ""),
                "mrtrix_root": os.getenv("LOCALFLOW_MRTRIX_ROOT", ""),
                "freesurfer_home": os.getenv("LOCALFLOW_FREESURFER_HOME", ""),
                "ants_root": os.getenv("LOCALFLOW_ANTS_ROOT", ""),
            },
        }

    @staticmethod
    def _anonymize(value: Any, context: RunnerContext) -> Any:
        replacements = [(str(Path.home()), "<HOME>")]
        if context.workflow.dataset:
            replacements.append((context.workflow.dataset.absolute_path, "<DATASET>"))

        def scrub(item: Any) -> Any:
            if isinstance(item, dict):
                return {key: scrub(child) for key, child in item.items()}
            if isinstance(item, list):
                return [scrub(child) for child in item]
            if isinstance(item, str):
                result = item
                for target, replacement in replacements:
                    if target:
                        result = result.replace(target, replacement)
                return result
            return item

        return scrub(value)

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        workflow = report["workflow"]
        lines = [f"# {workflow['title']}", "", workflow.get("summary", ""), "", "## 流程步骤", ""]
        for step in workflow["steps"]:
            lines.extend(
                [
                    f"### {step['order']}. {step['title']}",
                    "",
                    f"- Capability: `{step['capability']}`",
                    f"- Method: `{step.get('selected_method_id') or '未选择'}`",
                    f"- Parameters: `{json.dumps(step['selected_parameters'], ensure_ascii=False)}`",
                    "",
                ]
            )
        lines.extend(["## 结构化快照", "", "```json", json.dumps(report, ensure_ascii=False, indent=2), "```"])
        return "\n".join(lines)

    @staticmethod
    def _html(report: dict[str, Any]) -> str:
        workflow = report["workflow"]
        steps = "".join(
            "<section><h2>"
            + html.escape(f"{step['order']}. {step['title']}")
            + "</h2><p><b>Capability:</b> "
            + html.escape(step["capability"])
            + "</p><p><b>Method:</b> "
            + html.escape(str(step.get("selected_method_id") or "未选择"))
            + "</p><pre>"
            + html.escape(json.dumps(step["selected_parameters"], ensure_ascii=False, indent=2))
            + "</pre></section>"
            for step in workflow["steps"]
        )
        snapshot = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
        return (
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<title>NeuroWeave 处理报告</title><style>body{max-width:980px;margin:40px auto;"
            "font:15px/1.6 system-ui;color:#172033}section{border-top:1px solid #dbe3ef;"
            "padding:16px 0}pre{white-space:pre-wrap;background:#f5f7fb;padding:14px;border-radius:8px}"
            "</style><body><h1>" + html.escape(workflow["title"]) + "</h1><p>"
            + html.escape(workflow.get("summary", "")) + "</p>" + steps
            + "<details><summary>完整结构化快照</summary><pre>" + snapshot + "</pre></details></body></html>"
        )
