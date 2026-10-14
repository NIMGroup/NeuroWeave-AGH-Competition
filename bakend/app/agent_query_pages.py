"""Keep projected navigation aligned with rows and text actually shown to the model."""
import base64
import json


def project_page(original, budget):
    """Fit complete row prefixes before deriving navigation; never skip hidden rows."""
    if not isinstance(original.get('items'), list) or not ('offset' in original or 'coverage' in original):
        return None
    keys = ('id', 'kind', 'status', 'source_scope', 'root_path', 'relative_path', 'path', 'selection',
            'file_count', 'total_bytes', 'complete_scan', 'inventory_id', 'view', 'offset', 'total_items',
            'returned_count', 'has_more', 'next_cursor', 'next_offset', 'complete', 'selected_format_count',
            'scanned_count', 'matched_count', 'invalid_count', 'missing_counts', 'group_count', 'next_group_offset',
            'next_file_offset', 'aggregation_scope', 'group_counts_additive', 'snapshot_statistics', 'file_offset', 'schema_groups', 'schema_group_count',
            'schema_groups_truncated', 'errors', 'snapshot_id', 'snapshot_context', 'snapshot_reused',
            'skipped_symlinks', 'symlink_examples', 'symlink_examples_truncated', 'scope_complete', 'warnings',
            'index_id', 'index_status', 'index_reused', 'scanned_entries', 'query_complete', 'snapshot_observed_at',
            'pattern_counts', 'object_count', 'ready_object_count', 'ambiguous_counts', 'exceptions',
            'exception_count', 'coverage_complete', 'input_coverage_complete', 'object_scope', 'unassigned_file_count', 'format', 'format_note', 'note', 'next_step')
    value = {k: original[k] for k in keys if k in original}
    if value.get('relative_path') is not None:
        value.pop('path', None)
    def lean(row):
        row = dict(row)
        if row.get('relative_path'):
            row.pop('path', None)
        if isinstance(row.get('examples'), list):
            row['examples'] = row['examples'][:1]
        return row
    value['items'] = [lean(row) for row in original['items']]
    coverage = original.get('coverage')
    if isinstance(coverage, dict):
        value['coverage'] = {k: v for k, v in coverage.items() if k != 'groups'}
        value['coverage']['groups'] = [lean(row) for row in coverage.get('groups', [])]
    if len(json.dumps(value, ensure_ascii=False)) > budget and original.get('view') == 'summary':
        # Summary examples already live inside each group; don't repeat long paths.
        value['items'] = []
    for optional in ('schema_groups', 'errors', 'note', 'next_step', 'snapshot_observed_at', 'warnings'):
        if len(json.dumps(value, ensure_ascii=False)) > budget and optional in value:
            value.pop(optional)
            value[optional + '_omitted'] = True
    while len(json.dumps(value, ensure_ascii=False)) > budget:
        if len(value.get('coverage', {}).get('groups', [])) > 1:
            value['coverage']['groups'].pop()
        elif len(value['items']) > (0 if original.get('view') == 'summary' else 1):
            value['items'].pop()
        else:
            break
    if len(value['items']) < len(original['items']) or len(value.get('coverage', {}).get('groups', [])) < len((coverage or {}).get('groups', [])):
        value['model_preview_truncated'] = True
    return value


def aligned_page(original, value):
    value = dict(value)
    value.pop("_page_cursor", None)
    value.pop("_item_offsets", None)
    rows = original.get("items")
    if isinstance(rows, list) and len(value.get("items", [])) < len(rows):
        visible = len(value.get("items", []))
        value.update(backend_returned_count=len(rows), returned_count=visible,
                     model_visible_count=visible, page_complete=False)
        if original.get("view") != "summary":
            value["has_more"] = True
            positions = original.get("_item_offsets", [])
            offset = positions[visible-1] if visible and len(positions) >= visible else original.get("offset", 0) + visible
            if "_page_cursor" in original:
                cursor = {**original["_page_cursor"], "offset": offset}
                value["next_cursor"] = base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode()
            elif "offset" in original:
                value["next_offset"] = offset
                if 'next_group_offset' in original:
                    value['next_group_offset'] = offset
    original_coverage = original.get("coverage", {})
    coverage = value.get("coverage", {})
    if isinstance(original_coverage, dict) and isinstance(coverage, dict):
        if original_coverage:
            coverage = dict(coverage)
            coverage.update({k: original_coverage[k] for k in ('group_offset', 'next_group_offset', 'group_count', 'complete_scan') if k in original_coverage})
            value['coverage'] = coverage
        groups = original_coverage.get("groups", [])
        visible = len(coverage.get("groups", []))
        if visible < len(groups):
            coverage = dict(coverage)
            coverage.update(groups_truncated=True, model_visible_group_count=visible,
                            backend_returned_group_count=len(groups),
                            next_group_offset=original_coverage.get("group_offset", 0) + visible,
                            summarized_file_count=sum(v.get("count", 0) for v in coverage.get("groups", [])))
            value["coverage"] = coverage
    for key in ("text", "content"):
        text, shown = original.get(key), value.get(key)
        if isinstance(text, str) and isinstance(shown, str) and text != shown:
            shown = shown.split("…[预览截断]", 1)[0]
            if text.startswith(shown):
                value[key] = shown
                value["truncated"] = True
                offset = original.get("byte_offset", original.get("offset", 0))
                value["next_offset"] = offset + (len(shown.encode("utf-8")) if "byte_offset" in original else len(shown))
    return value
