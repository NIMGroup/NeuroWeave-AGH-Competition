"""Bounded, fixed read-only metadata profiling over the selected file snapshot."""
import csv
import hashlib
import io
import json
import os
import time
import math
from pathlib import PurePosixPath
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .assistant_inventory import inventory_query, bounded_rows
from .data_selection import DataSelection
from .operation_context import checkpoint
from .workspace_paths import WorkspacePaths
from . import workspace_io


class MetadataQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_scope: Literal["dataset", "output"]
    selection: DataSelection = Field(default_factory=DataSelection)
    format: Literal["json", "csv", "file", "text"] = "json"
    fields: list[str] = Field(default_factory=list, max_length=20)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    inventory_id: str | None = None
    offset: int = Field(default=0, ge=0)
    max_files: int = Field(default=10000, ge=1, le=10000)
    group_offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    def checked(self):
        if any(not isinstance(v, str) or not v or len(v) > 200 for v in self.fields + self.group_by):
            raise ValueError("字段必须为 1–200 字符的名称；JSON 支持点号嵌套键")
        if self.format == "csv" and set(self.fields + self.group_by) - {"columns", "row_count"}:
            raise ValueError("CSV 只读概况支持 columns/row_count；任意行计算请准备待运行脚本")
        supported = {'file': {'path','name','extension','size','parent','sample'},
                     'text': {'line_count','numeric_count','numeric_min','numeric_max','unique_numeric_values','numeric_row_lengths'}}
        if self.format in supported and set(self.fields + self.group_by) - supported[self.format]:
            raise ValueError('该格式仅支持这些只读字段：' + ', '.join(sorted(supported[self.format])))
        return self


METADATA_TOOL = {"type": "function", "function": {
    "name": "inspect_data_metadata",
    "description": "只读批量概况：format=json读取文档字段；csv支持columns/row_count；file支持path/name/extension/size/parent/sample；text支持行数与数值概况。按group_by聚合，返回覆盖、缺失和异常。文件名模式及输入配齐用query_data_files。分页按next_group_offset/next_file_offset；不运行任意代码。",
    "parameters": MetadataQuery.model_json_schema()}}


def field(value, key):
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def compact_value(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= 500:
        return value
    return {"preview": encoded[:200], "sha256": hashlib.sha256(encoded.encode()).hexdigest(), "truncated": True}


def inspect_metadata(service, request, scope, arguments):
    query = MetadataQuery.model_validate(arguments).checked()
    source, _ = service._roots(scope)
    inventory = inventory_query(service, request, scope, {
        "selection": query.selection.model_dump(), "view": "summary",
        **({"inventory_id": query.inventory_id} if query.inventory_id else {})})
    snapshot = next(v for v in request._inventory_snapshots.values() if v["id"] == inventory["inventory_id"])
    paths = [PurePosixPath(row['path']) for row in snapshot['rows']]
    snapshot_statistics = {'file_count': len(paths), 'parent_count': len({str(p.parent) for p in paths}),
                           'sample_count': len({p.parts[0] if len(p.parts)>1 else '.' for p in paths}),
                           'complete': inventory['scope_complete'] and inventory['complete_scan']}
    suffix = "." + query.format
    text_suffixes = ('.txt','.bval','.bvec','.tsv','.csv')
    files = [v for v in snapshot["rows"] if query.format=='file' or
             (v['path'].lower().endswith(text_suffixes) if query.format=='text' else v["path"].lower().endswith(suffix))]
    key = json.dumps(query.model_dump(exclude={"inventory_id", "group_offset", "limit"}), sort_keys=True)
    reports = snapshot.setdefault("metadata_reports", {})
    cached = key in reports
    if not cached:
        policy = WorkspacePaths(source, allowed_roots=(source,))
        groups, missing, schemas = {}, Counter(), Counter()
        errors, scanned, bytes_read = [], 0, 0
        invalid = 0
        started = time.monotonic()
        selected = files[query.offset:query.offset + query.max_files]
        for row in selected:
            checkpoint()
            if time.monotonic() - started > 45 or bytes_read >= 32 * 1024 * 1024:
                break
            relative = row["path"]
            scanned += 1
            try:
                path = policy.resolve(source, relative)
                if path.is_symlink() or policy.sensitive(path):
                    raise PermissionError("元数据路径不可读取")
                if query.format=='file':
                    current=path.stat()
                    p=PurePosixPath(relative)
                    document={'path':relative,'name':p.name,'extension':'.nii.gz' if p.name.lower().endswith('.nii.gz') else p.suffix,
                              'size':current.st_size,'parent':str(p.parent),'sample':p.parts[0] if len(p.parts)>1 else '.'}
                    raw=b''
                else:
                    with os.fdopen(workspace_io.open_read(path), "rb") as stream:
                        raw = stream.read(256 * 1024 + 1)
                    bytes_read += len(raw)
                    if len(raw) > 256 * 1024:
                        raise ValueError("文件超过 256 KiB 的只读概况上限")
                    text = raw.decode("utf-8-sig")
                if query.format == "json":
                    document = json.loads(text)
                    if not isinstance(document, dict):
                        raise ValueError("JSON 顶层须为对象")
                elif query.format=='csv':
                    reader = csv.reader(io.StringIO(text))
                    columns = next(reader, [])
                    document = {"columns": columns, "row_count": sum(1 for _ in reader)}
                elif query.format=='text':
                    lines=text.splitlines(); numbers=[]; widths=[]
                    for line in lines:
                        tokens=line.split()
                        values_in_line=[]
                        for token in tokens:
                            try: number=float(token)
                            except ValueError: continue
                            if math.isfinite(number):values_in_line.append(number)
                        numbers.extend(values_in_line);widths.append(len(values_in_line))
                    document={'line_count':len(lines),'numeric_count':len(numbers),
                              'numeric_min':min(numbers) if numbers else None,'numeric_max':max(numbers) if numbers else None,
                              'unique_numeric_values':sorted(set(numbers)), 'numeric_row_lengths':widths}
                schemas[tuple(sorted(document))] += 1
                values = {key: compact_value(field(document, key)) for key in query.fields}
                for name in query.fields:
                    if values[name] is None:
                        missing[name] += 1
                by = query.group_by or query.fields[:2]
                group_values = {key: compact_value(field(document, key)) for key in by}
                identity = json.dumps(group_values, ensure_ascii=False, sort_keys=True)
                group = groups.setdefault(identity, {"values": group_values, "count": 0, "examples": []})
                group["count"] += 1
                if len(group["examples"]) < 2:
                    group["examples"].append({"path": relative, "values": values})
            except (OSError, ValueError, RecursionError, csv.Error) as exc:
                invalid += 1
                if len(errors) < 5:
                    errors.append({"path": relative, "error": str(exc)[:300]})
        next_file = query.offset + scanned
        report = {"inventory_id": inventory["inventory_id"], "source_scope": scope["source_scope"],
                  "root_path": str(source), "file_count": inventory["file_count"],
                  "selected_format_count": len(files), "scanned_count": scanned, "matched_count": scanned - invalid,
                  "invalid_count": invalid, "missing_counts": dict(missing), "errors": errors,
                  "aggregation_scope": "current_file_page", "file_offset": query.offset,
                  "group_counts_additive": False, "snapshot_statistics": snapshot_statistics,
                  "complete": inventory['complete_scan'] and query.offset == 0 and next_file >= len(files),
                  "complete_scan": inventory['complete_scan'], 'scope_complete':inventory['scope_complete'],
                  'index_status':inventory.get('index_status'), 'format':query.format,
                  "next_file_offset": next_file if next_file < len(files) else None,
                  "schema_groups": [{"fields": list(fields)[:40], "field_count": len(fields), "count": count}
                                    for fields, count in schemas.most_common(8)],
                  "schema_group_count": len(schemas), "schema_groups_truncated": len(schemas) > 8,
                  "groups": sorted(groups.values(), key=lambda v: (-v["count"], json.dumps(v["values"], sort_keys=True))),
                  "group_count": len(groups), "bytes_read": bytes_read,
                  "note": "统计仅覆盖已读取的当前文件页；异常与缺失不会推断为已知值。历史快照不是执行授权，准备及运行方案另行校验。"}
        if not files:
            report['format_note']='当前索引范围内没有兼容该format的文件；这不表示范围内没有其他类型文件。文件名与属性使用query_data_files或format=file。'
        if len(reports) >= 4:
            reports.pop(next(iter(reports)))
        reports[key] = report
    report = reports[key]
    page, next_group = bounded_rows(report["groups"], query.group_offset, query.limit, budget=2500)
    return {k: v for k, v in report.items() if k != "groups"} | {
        "metadata_cached": cached, "items": page, "offset": query.group_offset,
        "returned_count": len(page), "next_offset": next_group, "next_group_offset": next_group,
        "has_more": next_group is not None}
