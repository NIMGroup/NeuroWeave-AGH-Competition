"""Scoped checks of authorized inputs; file metadata is not proof of scientific quality."""

import json
import math
import os
import subprocess
import struct
from pathlib import Path
from .check_cache import checked_file
from ..artifact_semantics import IMAGE_TYPES, canonical_input


def image_header(path):
    if path.name.lower().endswith((".mif", ".nii", ".nii.gz")):

        def read_header():
            from ..data_sequences import image_header as read

            try:
                header = read(path)
                return {
                    **header,
                    "shape": header["dimensions"],
                    "status": "passed",
                    "code": "image_header_readable",
                }
            except (OSError, ValueError, UnicodeError, EOFError, struct.error) as exc:
                return dict(
                    status="error", code="invalid_image_header", reason=str(exc)[:200]
                )

        return checked_file(path, "image-header-v2", read_header)
    if not path.name.lower().endswith((".nii", ".nii.gz", ".mgz", ".mgh")):
        return dict(status="not_checked", code="format_not_checked")
    python = Path(
        os.getenv(
            "LOCALFLOW_SKILL_PYTHON", "/opt/neuroweave/envs/scientific-py311/bin/python"
        )
    )
    worker = Path(__file__).with_name("header_check_worker.py")
    if not python.is_file():
        return dict(status="not_checked", code="reader_unavailable")

    def read():
        try:
            result = subprocess.run(
                [str(python), "-B", "-s", str(worker), str(path)],
                capture_output=True,
                text=True,
                timeout=12,
                shell=False,
            )
            if result.returncode or len(result.stdout) > 10000:
                return dict(status="not_checked", code="reader_failed")
            return json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return dict(status="not_checked", code="reader_failed")

    return checked_file(
        path,
        (
            "image-header-v1",
            str(python),
            python.stat().st_mtime_ns,
            worker.stat().st_mtime_ns,
        ),
        read,
    )


def numeric_table(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("Gradient table exceeds 2 MB")
    rows = [
        [float(v) for v in line.split()]
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not rows or any(not math.isfinite(v) for row in rows for v in row):
        raise ValueError("Empty or non-finite gradient table")
    if len({len(row) for row in rows}) != 1:
        raise ValueError("Ragged gradient table")
    return rows


def gradient_table(path, kind):
    def read():
        table = numeric_table(path)
        if kind == "b_values":
            values = [v for row in table for v in row]
            if min(values) < 0:
                raise ValueError("Negative b-value")
            from ..data_sequences import gradient_summary

            return {
                "count": len(values),
                **gradient_summary(" ".join(map(str, values))),
            }
        if kind == "b_vectors":
            if len(table) != 3 and len(table[0]) != 3:
                raise ValueError("b-vectors require 3xN or Nx3")
            return {"count": len(table[0]) if len(table) == 3 else len(table)}
        if len(table[0]) != 4:
            raise ValueError("MRtrix gradients require 4 columns")
        from ..data_sequences import gradient_summary

        return {
            "count": len(table),
            **gradient_summary(" ".join(str(row[3]) for row in table)),
        }

    return checked_file(path, ("gradient-summary-v2", kind), read)


def scientific_checks(context):
    # Called only after common_preflight authorized every path and provenance.
    from .external_command import ExternalCommandRunner

    rows = []
    images = []
    gradients = {}

    def add(status, code, slot="", **details):
        rows.append(
            dict(
                status=status,
                code=code,
                slot_id=slot,
                step_id=context.step.id,
                details=details,
            )
        )

    from .gradient_policy import gradient_arguments, gradient_slots

    ignored = set()
    profile = {}
    if any(p.id == "gradient_source" for p in context.method.parameters):
        try:
            argv, source, profile = gradient_arguments(context)
            context.prepared_options["gradient_args"] = argv
            ignored = gradient_slots(context, except_source=source)
            add(
                "passed" if source != "deferred" else "deferred",
                "gradient_source_resolved",
                source=source,
                profile=profile,
            )
        except (ValueError, OSError, RuntimeError) as exc:
            add("error", "invalid_gradient_source", reason=str(exc)[:300])
    for slot, binding in context.step.input_bindings.items():
        if slot in ignored:
            continue
        if binding.source == "upstream_output":
            if binding.artifact_type in IMAGE_TYPES:
                add("deferred", "upstream_check_deferred", slot)
            continue
        for path in ExternalCommandRunner.binding_paths(binding):
            if binding.artifact_type in IMAGE_TYPES and path.is_file():
                header = image_header(path)
                add(
                    header["status"],
                    header["code"],
                    slot,
                    metadata={
                        k: v for k, v in header.items() if k not in {"status", "code"}
                    },
                )
                if header["status"] == "passed":
                    images.append((slot, binding.artifact_type, header))
            if (
                binding.artifact_type in {"b_values", "b_vectors", "gradient_table"}
                and path.is_file()
            ):
                try:
                    kind, _ = canonical_input(binding.artifact_type, path, slot)
                    gradients[slot] = gradient_table(path, kind)["count"]
                    add(
                        "passed", "gradient_table_readable", slot, count=gradients[slot]
                    )
                except (ValueError, UnicodeError, OSError) as exc:
                    add("error", "invalid_gradient_table", slot, reason=str(exc))
            if binding.artifact_type == "response_functions" and path.is_file():
                try:

                    def response_summary():
                        table = numeric_table(path)
                        return {"rows": len(table), "coefficients": len(table[0])}

                    response = checked_file(
                        path, "response-summary-v1", response_summary
                    )
                    algorithm = context.step.selected_parameters.get("algorithm")
                    if algorithm == "csd" and response["rows"] != 1:
                        raise ValueError(
                            "单组织CSD响应函数应只有所选壳层的一行；多组织响应请使用对应算法"
                        )
                    if algorithm == "msmt_csd" and profile:
                        shells = profile.get("shell_count", 0) + bool(
                            profile.get("b0_count")
                        )
                        if shells < 3:
                            raise ValueError("三组织MSMT-CSD需要至少三种b值（含b0）")
                        if response["rows"] != shells:
                            raise ValueError("响应函数行数与选用梯度壳层数量不一致")
                    add("passed", "response_function_readable", slot, **response)
                except (ValueError, UnicodeError, OSError) as exc:
                    add("error", "invalid_response_function", slot, reason=str(exc))
    counts = set(gradients.values())
    volumes = {
        h["shape"][3] if len(h["shape"]) > 3 else 1
        for _, kind, h in images
        if kind in {"raw_dwi", "preprocessed_dwi"}
    }
    if len(counts | volumes) > 1:
        add(
            "error",
            "gradient_volume_mismatch",
            counts=sorted(counts),
            volumes=sorted(volumes),
        )
    from ..input_evidence import observed_values

    for slot, kind, h in images:
        definition = next((s for s in context.method.input_slots if s.id == slot), None)
        values = observed_values([h])
        for key, expected in (
            definition.requires_features if definition else {}
        ).items():
            if key not in values:
                add(
                    "review",
                    "input_feature_requires_review",
                    slot,
                    feature=key,
                    expected=expected,
                )
            elif expected not in values[key]:
                add(
                    "error",
                    "input_feature_mismatch",
                    slot,
                    feature=key,
                    expected=expected,
                )
    for slot, kind, h in images:
        if kind == "five_tt_image" and (len(h["shape"]) != 4 or h["shape"][3] != 5):
            add("error", "five_tt_channels", slot, shape=h["shape"])
        if (
            kind in {"label_image", "parcellation_image"}
            and len(h["shape"]) > 3
            and h["shape"][3] != 1
        ):
            add("error", "label_dimensions", slot, shape=h["shape"])
    if len(images) > 1:
        # Registration, ACT and resampling allow differing grids. Do not reject
        # them or claim alignment based only on equal shapes/affines.
        add("review", "spatial_alignment_requires_review", slots=[v[0] for v in images])
    if any(
        kind in {"label_image", "parcellation_image", "brain_mask", "five_tt_image"}
        for _, kind, _ in images
    ):
        add("review", "voxel_values_and_anatomy_not_checked")
    return rows
