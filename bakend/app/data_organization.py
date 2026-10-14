"""Complete inventory naming, immutable recipes and explainable coverage."""
import fnmatch
import hashlib
import json
import re
from .operation_context import checkpoint
from pathlib import Path

from .data_models import DataEdit, GeneratedDataPlan, NamingRule, OrganizeRequest
from .data_selection import DataSelection, selected_inventory


def suffix_and_stem(relative):
    name = Path(relative).name
    suffix = ".nii.gz" if name.lower().endswith(".nii.gz") else Path(name).suffix
    return suffix, name[:-len(suffix)] if suffix else name


def coverage_groups(rows, limit=60):
    """Group the entire snapshot, never extrapolate a paginated sample."""
    groups, extensions = {}, {}
    for row in rows:
        suffix, stem = suffix_and_stem(row["path"])
        extensions[suffix.lower() or "(none)"] = extensions.get(suffix.lower() or "(none)", 0) + 1
        pattern = re.sub(r"\d+", "*", stem) + suffix
        group = groups.setdefault(pattern, {"pattern": pattern, "count": 0, "examples": []})
        group["count"] += 1
        if len(group["examples"]) < 2:
            group["examples"].append(row["path"])
    ordered = sorted(groups.values(), key=lambda v: (-v["count"], v["pattern"]))
    return {"file_count": len(rows), "extension_counts": extensions, "groups": ordered[:limit],
            "group_count": len(groups), "groups_truncated": len(groups) > limit,
            "summarized_file_count": sum(v["count"] for v in ordered[:limit]), "complete_scan": True}


def excluded_path(relative, patterns):
    candidates = [relative, *[p.as_posix() for p in Path(relative).parents if p.as_posix() != "."]]
    return any(fnmatch.fnmatchcase(p, pattern) for p in candidates for pattern in patterns)


def legacy_recipe(service, task):
    """Read the exact successful tool event for old previews; never rewrite them."""
    with service.store.connection() as db:
        row = db.execute("""SELECT payload FROM workspace_tool_events
            WHERE json_extract(payload,'$.data_operation.id')=?
              AND json_extract(payload,'$.tool')='propose_data_organization'
            ORDER BY id DESC LIMIT 1""", (task['id'],)).fetchone()
    if not row:
        return None
    event = json.loads(row[0])
    if event.get('data_operation', {}).get('manifest_hash') != task['manifest_hash']:
        return None
    try:
        request = OrganizeRequest(**task['scope'], **event.get('arguments', {}))
    except (ValueError, TypeError):
        return None
    return {'path': request.path or '.', 'rules': [r.model_dump() for r in request.rules],
            'exclude_paths': request.exclude_paths, 'duplicate_policy': request.duplicate_policy or 'number',
            'unmatched_policy': request.unmatched_policy or 'skip', 'require_complete': request.require_complete,
            'provenance': 'frozen_tool_event'}


def organize(service, request):
    scope = {k: getattr(request, k) for k in ("conversation_id", "dataset_id", "workspace_id", "source_scope", "mode")}
    base, previous = None, {}
    if request.base_operation_id:
        base = service._raw(request.base_operation_id, 'preview')
        service._owner(base, request.conversation_id)
        if base['scope'] != scope:
            raise PermissionError("基准方案不属于当前数据集、输出目录或操作模式")
        if base['status'] in {'validating', 'running', 'queued', 'analyzing', 'cancel_requested', 'rolling_back', 'interrupted'}:
            raise ValueError("基准操作正在执行或需要恢复，不能重复整理同一范围")
        previous = base.get('recipe') or legacy_recipe(service, base) or {}
        if not previous:
            raise ValueError("旧批次没有保存规则；请从实际文件检查并明确提供完整规则")
    rules = [] if request.replace_rules else previous.get('rules', [])[:]
    rules = [v for v in rules if v['pattern'] not in request.remove_patterns]
    merged = {v['pattern']: v for v in rules}
    if len({r.pattern for r in request.rules}) != len(request.rules):
        raise ValueError('同一模式只能定义一条规则；修正名称、匹配对象或优先级会更新整条规则')
    for rule in request.rules:
        merged[rule.pattern] = rule.model_dump()
    if not merged or len(merged) > 100:
        raise ValueError("需要 1 到 100 条有效规则；增量修改请指定 base_operation_id")
    rules = [NamingRule.model_validate(v) for v in merged.values()]
    exclusions = list(dict.fromkeys([*[v for v in previous.get('exclude_paths', []) if v not in request.remove_exclusions], *request.exclude_paths]))
    if len(exclusions) > 100 or any(len(v) > 4096 for v in exclusions):
        raise ValueError("排除路径超出限制")
    path = request.path if request.path is not None else previous.get('path', '.')
    duplicate = request.duplicate_policy or previous.get('duplicate_policy', 'number')
    unmatched_policy = request.unmatched_policy or previous.get('unmatched_policy', 'skip')
    require_complete = request.require_complete if 'require_complete' in request.model_fields_set else previous.get('require_complete', False)
    selection = request.selection.model_dump()
    if 'selection' not in request.model_fields_set and previous.get('selection'):
        selection = previous['selection']
    selection = DataSelection.model_validate({**selection, 'path': path if request.path is not None or 'selection' not in request.model_fields_set else request.selection.path})
    path = selection.path
    inventory = selected_inventory(service, scope, selection)
    all_files = {r['path'] for r in inventory['items']}
    excluded = {p for p in all_files if excluded_path(p, exclusions)}
    files = all_files - excluded
    groups, conflicts, ambiguous = {}, [], set()
    # Images first so associated files are always assigned to their image family.
    ordered = sorted(files, key=lambda p: (not p.lower().endswith(('.nii', '.nii.gz')), p))
    grouped = set()
    for relative in ordered:
        checkpoint()
        if relative in grouped:
            continue
        suffix, stem = suffix_and_stem(relative)
        image = suffix.lower() in {'.nii', '.nii.gz'}
        candidates = []
        for rule in rules:
            if rule.kind == 'image' and not image:
                continue
            value = stem if rule.match_on == 'stem' else Path(relative).name
            if fnmatch.fnmatchcase(value, rule.pattern) and not any(fnmatch.fnmatchcase(value, p) for p in rule.exclude):
                candidates.append(rule)
        if not candidates:
            continue
        highest = max(r.priority for r in candidates)
        names = {r.name for r in candidates if r.priority == highest}
        if len(names) != 1:
            conflicts.append(f"多个同优先级规则匹配：{relative}")
            ambiguous.add(relative)
            if image:
                source_stem = relative[:-len(suffix)]
                ambiguous.update(source_stem + s for s in ('.json', '.bval', '.bvec') if source_stem + s in files)
            continue
        name = next(iter(names))
        source_stem = relative[:-len(suffix)] if suffix else relative
        family = [relative] + ([source_stem + s for s in ('.json', '.bval', '.bvec') if source_stem + s in files] if image else [])
        grouped.update(family)
        groups.setdefault((Path(relative).parent.as_posix(), name), []).append((relative, suffix, source_stem, family))
    edits, selected = [], set()
    for (parent, name), entries in groups.items():
        checkpoint()
        if len(entries) > 1 and duplicate == 'conflict':
            conflicts.append(f"同目录存在多个 {name}：{parent}")
        for index, (relative, suffix, source_stem, family) in enumerate(entries, 1):
            target = name + (f'_run-{index:02}' if len(entries) > 1 else '')
            for member in family:
                ending = suffix if member == relative else member[len(source_stem):]
                destination = (Path(parent) / (target + ending)).as_posix()
                selected.add(member)
                if request.mode == 'inplace' and member == destination:
                    continue
                edits.append(DataEdit(action='copy' if request.mode == 'copy' else 'rename', path=member, destination=destination))
    preserved = set()
    if unmatched_policy == 'preserve':
        # Unknown semantic labels stay distinct under their original names.
        preserved = files - selected - ambiguous
        for relative in sorted(preserved):
            if request.mode == 'copy':
                edits.append(DataEdit(action='copy', path=relative, destination=relative))
        selected.update(preserved)
    source, target = service._roots(scope)
    if edits:
        plan = GeneratedDataPlan(**scope, title=request.title, edits=edits)
        task = service._propose(plan, conflicts=conflicts, ready=False, status='preparing')
    else:
        task = service._record(scope, source, target, request.title, [], conflicts, ready=False, status='preparing')
    unmatched = [r for r in inventory['items'] if r['path'] in files - selected]
    scan = {k: v for k, v in inventory.items() if k != 'items'}
    fingerprint = hashlib.sha256(json.dumps(inventory['items'], sort_keys=True).encode()).hexdigest()
    scan.update(matched_files=len(selected), excluded_files=len(excluded), preserved_files=len(preserved),
                unmatched_files=len(unmatched), unchanged_files=len(selected) - len(edits) if request.mode == 'inplace' else 0,
                unmatched_policy=unmatched_policy, unmatched_snapshot=True, source_fingerprint=fingerprint,
                unmatched_examples=[r['path'] for r in unmatched[:5]])
    regression = 0
    if base and not request.replace_rules and previous.get('path', '.') == path:
        old_selected = {r['path'] for r in service.repository.items(base['id'])}
        regression = len((old_selected & files) - selected)
        scan.update(previous_file_count=base.get('scan', {}).get('file_count'),
                    previous_matched_files=base.get('scan', {}).get('matched_files', base['file_count']),
                    source_changed=base.get('scan', {}).get('source_fingerprint') != fingerprint)
    issues = []
    if not inventory['file_count'] and inventory.get('skipped_symlinks'):
        issues.append('no_eligible_files')
    if conflicts:
        issues.append('conflicts')
    if require_complete and unmatched:
        issues.append('unmatched_files')
    if require_complete and regression:
        issues.append('coverage_regression')
    lineage = task['id']
    if base:
        lineage = previous.get('lineage_root_id') or base['id']
        parent = previous.get('base_operation_id') if not previous.get('lineage_root_id') else None
        seen = {base['id']}
        while parent:
            if parent in seen or len(seen) >= 100:
                raise ValueError('规则继承关系无效或过长')
            seen.add(parent)
            ancestor = service._raw(parent, 'preview')
            service._owner(ancestor, request.conversation_id)
            if ancestor['scope'] != scope:
                raise PermissionError('规则继承链不能跨越数据范围')
            recipe = ancestor.get('recipe') or legacy_recipe(service, ancestor) or {}
            lineage = recipe.get('lineage_root_id') or parent
            parent = recipe.get('base_operation_id') if not recipe.get('lineage_root_id') else None
    task.update(scan=scan, recipe={'path': path, 'selection': selection.model_dump(), 'rules': [r.model_dump() for r in rules],
                'exclude_paths': exclusions, 'duplicate_policy': duplicate, 'unmatched_policy': unmatched_policy,
                'require_complete': require_complete, 'base_operation_id': request.base_operation_id, 'lineage_root_id': lineage},
                quality={'complete': not issues, 'blocking_issues': issues, 'regressed_files': regression,
                         'accounted_files': len(selected) + len(excluded) + len(unmatched)})
    task['status'] = 'pending'
    task['manifest_ready'] = True
    if not edits and not unmatched and not conflicts and not issues:
        task['status'] = 'succeeded'  # Already in the desired state; no write occurred.
    with service.store.transaction() as db:
        db.execute('INSERT INTO data_operation_inventories VALUES(?,?)', (task['id'], json.dumps(unmatched, ensure_ascii=False)))
    # Finish metadata for a proposal already recorded; the thread boundary drains
    # this short section before reporting cancellation, leaving no preparing row.
    service._save(task)
    return service._finish_request(task, request.execution)
