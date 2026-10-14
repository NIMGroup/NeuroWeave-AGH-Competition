from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroWeave reviewed table-cleaning worker")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--missing-strategy",
        choices=("keep", "drop_rows", "drop_columns", "mean", "median", "mode", "constant"),
        default="keep",
    )
    parser.add_argument("--missing-threshold", type=float, default=1.0)
    parser.add_argument("--fill-value", default="")
    parser.add_argument("--deduplicate", action="store_true")
    parser.add_argument("--strip-whitespace", action="store_true")
    parser.add_argument("--normalize-columns", action="store_true")
    args = parser.parse_args()

    suffix = args.input.name.lower()
    if suffix.endswith(".csv"):
        frame = pd.read_csv(args.input)
    elif suffix.endswith(".tsv"):
        frame = pd.read_csv(args.input, sep="\t")
    elif suffix.endswith((".xlsx", ".xls")):
        frame = pd.read_excel(args.input)
    else:
        raise ValueError("仅支持 CSV、TSV、XLSX 或 XLS 输入")
    before_shape = frame.shape
    missing_before = int(frame.isna().sum().sum())
    duplicate_before = int(frame.duplicated().sum())

    if args.strip_whitespace:
        for column in frame.select_dtypes(include=["object", "string"]).columns:
            frame[column] = frame[column].map(
                lambda value: value.strip() if isinstance(value, str) else value
            )
    if args.normalize_columns:
        seen: dict[str, int] = {}
        columns: list[str] = []
        for raw in frame.columns:
            name = re.sub(r"[^a-z0-9]+", "_", str(raw).strip().lower()).strip("_") or "column"
            seen[name] = seen.get(name, 0) + 1
            columns.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
        frame.columns = columns
    threshold = max(0.0, min(args.missing_threshold, 1.0))
    if args.missing_strategy == "drop_rows":
        frame = frame.dropna(thresh=max(1, int((1.0 - threshold) * len(frame.columns))))
    elif args.missing_strategy == "drop_columns":
        frame = frame.dropna(axis=1, thresh=max(1, int((1.0 - threshold) * len(frame))))
    elif args.missing_strategy in {"mean", "median"}:
        numeric = frame.select_dtypes(include="number").columns
        values = frame[numeric].mean() if args.missing_strategy == "mean" else frame[numeric].median()
        frame[numeric] = frame[numeric].fillna(values)
    elif args.missing_strategy == "mode":
        for column in frame.columns:
            modes = frame[column].mode(dropna=True)
            if not modes.empty:
                frame[column] = frame[column].fillna(modes.iloc[0])
    elif args.missing_strategy == "constant":
        frame = frame.fillna(args.fill_value)
    if args.deduplicate:
        frame = frame.drop_duplicates()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    report = {
        "schema_version": 1,
        "input": args.input.name,
        "rows_before": before_shape[0],
        "columns_before": before_shape[1],
        "rows_after": int(frame.shape[0]),
        "columns_after": int(frame.shape[1]),
        "missing_before": missing_before,
        "missing_after": int(frame.isna().sum().sum()),
        "duplicates_before": duplicate_before,
        "duplicates_after": int(frame.duplicated().sum()),
        "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "parameters": {
            "missing_strategy": args.missing_strategy,
            "missing_threshold": threshold,
            "deduplicate": args.deduplicate,
            "strip_whitespace": args.strip_whitespace,
            "normalize_columns": args.normalize_columns,
        },
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Cleaned {before_shape[0]}x{before_shape[1]} -> {frame.shape[0]}x{frame.shape[1]}")


if __name__ == "__main__":
    main()
