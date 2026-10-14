"""General evidence budgets and context previews; never execution authority."""
from __future__ import annotations

import hashlib
import json
import time


RUNTIME_GUIDANCE = """
围绕本轮完成条件查询关键事实；证据足够后回答或准备待运行方案，不逐项穷举大数据集。
错误先检查参数、工具和处理方向；重复查询无新证据时改变方法，具体说明缺少的能力。
历史检查点只帮助恢复思路，关键状态须重新核验；区分预览遗漏与后端分页，未见内容不等于不存在。
预算由后端控制；停止时报告已知事实、具体阻碍及下一步。回复简洁，仅保留用户需要的结果和状态。
"""


def signature(name, arguments):
    return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)


def preview(result, budget=32000):
    """Keep counts, provenance, pagination and useful examples in valid bounded JSON."""
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) <= budget:
        return result
    important = {'items', 'next_group_offset', 'group_offset', 'next_file_offset', 'selected_format_count', 'scanned_count', 'matched_count', 'invalid_count', 'aggregation_scope', 'id', 'kind', 'status', 'error', 'scope', 'source_scope', 'root_path', 'relative_path', 'mode', 'action_counts', 'quality', 'scan',
                 'can_confirm', 'can_run_script', 'requires_inplace_confirmation', 'requires_overwrite_confirmation',
                 'requires_delete_confirmation', 'selection', 'base_operation_id', 'lineage_root_id', 'path',
                 'conflict_count', 'file_count', 'completed', 'total', 'count', 'has_more', 'next_cursor', 'next_offset',
                 'blocking_issues', 'complete', 'complete_scan', 'coverage', 'page_complete', 'selection', 'view',
                 'total_items', 'offset', 'returned_count', 'files_query', 'group_count', 'groups_truncated',
                 'extension_counts', 'unmatched_files', 'matched_files', 'preserved_files', 'candidate_file_count', 'effect_preview',
                 'scope_complete', 'symlink_policy', 'skipped_symlinks', 'followed_symlinks', 'duplicate_targets',
                 'symlink_examples_truncated', 'warnings', 'coverage_note', 'resolved_path', 'via_path'}
    def reduce(value, rows, text, fields=100):
        if isinstance(value, dict):
            # A relative path conveys the same identity without repeating roots.
            value = dict(value)
            if value.get('relative_path') and value.get('path'):
                value.pop('path')
                if value.get('name') == value['relative_path'].rsplit('/', 1)[-1]:
                    value.pop('name')
            selected = dict(list(value.items())[:fields])
            selected.update({k: v for k, v in value.items() if k in important})
            identities = {'path','destination','relative_path','root_path','pattern','id','inventory_id','next_cursor'}
            return {k[:200]: (v if k in identities and isinstance(v,str) else reduce(v, rows, text, fields))
                    for k, v in selected.items()}
        if isinstance(value, list):
            return [reduce(v, rows, text, fields) for v in value[:rows]]
        if isinstance(value, str) and len(value) > text:
            return value[:text] + '…[预览截断]'
        return value
    for rows, text, fields in ((200, 4000, 100), (64, 2000, 100), (32, 1000, 100), (16, 500, 64), (8, 250, 32), (3, 120, 16), (1, 60, 8)):
        value = reduce(result, rows, text, fields)
        # Keep top-level counters and navigation even for very wide payloads.
        value.update({k: reduce(v, rows, text, fields) for k, v in result.items() if k in important})
        value['model_preview_truncated'] = True
        if isinstance(result.get('symlink_examples'), list) and len(value.get('symlink_examples', [])) < len(result['symlink_examples']):
            value['symlink_examples_truncated'] = True
        if isinstance(result.get('items'), list):
            value['backend_returned_count'] = len(result['items'])
            value['model_visible_count'] = len(value.get('items', []))
        if isinstance(result.get('coverage'), dict):
            original = result['coverage']; coverage = value.get('coverage', {})
            if len(coverage.get('groups', [])) < len(original.get('groups', [])):
                coverage.update(groups_truncated=True, model_visible_group_count=len(coverage.get('groups', [])),
                                backend_returned_group_count=len(original.get('groups', [])))
        value['preview_note'] = '压缩仅影响模型预览，完整结果仍在后端；遗漏条目需缩小分页或范围重新查询。next_cursor仅属于后端原分页。'
        if len(json.dumps(value, ensure_ascii=False)) <= budget:
            return value
    # Very wide records still preserve key navigation and concrete failure facts.
    value = {k: reduce(v, 1, 80, 8) for k, v in result.items() if k in important}
    value.update(model_preview_truncated=True)
    if len(json.dumps(value, ensure_ascii=False)) > budget:
        # Wide nested payloads retain critical counters/decisions, with explicit omissions.
        value = {k: v for k, v in value.items() if not isinstance(v, (dict, list))}
        for key in ('quality', 'scan', 'action_counts', 'selection'):
            if isinstance(result.get(key), dict):
                value[key] = {k: reduce(v, 1, 60, 8) for k, v in result[key].items() if k in important or
                              (key == 'action_counts' and isinstance(v, int))}
        value.update(model_preview_truncated=True, model_visible_count=0,
                     preview_note='宽记录已压缩；须查完整详情核验遗漏字段，不能将遗漏解释为不存在。')
        if len(json.dumps(value, ensure_ascii=False)) > budget:
            # Scope/selection is never fabricated from truncated paths.
            value = {k: v for k, v in value.items() if k in {'id', 'kind', 'status', 'source_scope', 'mode', 'can_confirm',
                     'can_run_script', 'total', 'count', 'has_more', 'next_cursor', 'next_offset', 'file_count', 'conflict_count',
                     'scope_complete', 'symlink_policy', 'skipped_symlinks', 'followed_symlinks', 'duplicate_targets'}}
            value.update(model_preview_truncated=True, details_omitted=True)
    if result.get('effect_preview') and not value.get('effect_preview'):
        for row in result['effect_preview'][:3]:
            candidate = {**value, 'effect_preview': [*value.get('effect_preview', []),
                {k: v for k, v in row.items() if k in {'action', 'path', 'destination'}}]}
            if len(json.dumps(candidate, ensure_ascii=False)) <= budget:
                value = candidate
    return value


def compact_messages(messages, budget=60000):
    """Compact older tool results without breaking function-call protocol or history."""
    indexes = [i for i, m in enumerate(messages) if m.get('role') == 'tool']
    used = sum(len(messages[i].get('content', '')) for i in indexes)
    for i in indexes[:-2]:
        if used <= budget:
            break
        content = messages[i].get('content', '')
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            value = {'content': content}
        # Pages already fit the model budget and their continuation belongs to
        # the visible rows/text. A second generic preview would skip hidden data.
        if (isinstance(value.get('items'), list) and ('offset' in value or 'coverage' in value)
                or any(isinstance(value.get(key), str) for key in ('text', 'content')) and 'next_offset' in value):
            continue
        reduced = json.dumps(preview(value, 1800), ensure_ascii=False)
        messages[i]['content'] = reduced
        used -= len(content) - len(reduced)


class ProgressBudget:
    """Extend for evidence, stop unproductive loops, bound even a changing provider."""
    def __init__(self, clock=time.monotonic, duration=300, reserve_seconds=0):
        self.clock, self.started = clock, clock()
        self.duration = duration
        self.reserve_seconds = reserve_seconds
        self.rounds = self.calls = self.stalled = 0
        self.round_limit, self.call_limit = 10, 20
        self.facts, self.cache = set(), {}
        self.recent = []
        self.inventory_ready = False

    @property
    def remaining_seconds(self):
        return max(0, self.duration - (self.clock() - self.started))

    def stop_reason(self, *, include_rounds=True):
        if self.remaining_seconds <= self.reserve_seconds:
            return 'time_budget'
        if self.stalled >= 6:
            return 'no_progress'
        if (include_rounds and self.rounds >= self.round_limit) or self.calls >= self.call_limit:
            if any(self.recent[-3:]) and self.round_limit < 24 and self.call_limit < 48:
                self.round_limit = min(24, self.round_limit + 6)
                self.call_limit = min(48, self.call_limit + 12)
            else:
                return 'query_budget'
        return None

    def observe(self, result, *, cached=False):
        # Callers supply the evidence actually sent to the model. Backend-only
        # rows cannot make a later, newly visible page appear unproductive.
        self.calls += not cached
        if not cached and result.get('complete_scan') and 'file_count' in result:
            self.inventory_ready = True
        facts = []
        if not cached and 'error' not in result:
            rows = result.get('items')
            if isinstance(rows, list):
                facts = list(rows)
                # Counts/coverage changes count as evidence, cursors alone do not.
                facts += [{k: v for k, v in result.items() if k in
                           {'total', 'count', 'scan', 'quality', 'status', 'file_count', 'completed', 'coverage',
                            'complete_scan', 'source_scope', 'root_path', 'selection'}}]
            else:
                facts = [{k: v for k, v in result.items() if k not in
                          {'updated_at', 'timestamp', 'next_cursor', 'cursor', 'request_id', 'evidence_reference'}}]
        def canonical(value):
            if isinstance(value, dict):
                value = {k: canonical(v) for k, v in value.items() if k not in {'updated_at', 'timestamp', 'evidence_reference', 'inventory_cached', 'metadata_cached', 'snapshot_observed_at', 'inventory_id', 'next_group_offset', 'next_offset', 'next_cursor', 'model_preview_truncated', 'preview_note', 'model_visible_count', 'backend_returned_count'}}
                if value.get('relative_path'):
                    value.pop('path', None)
                    if value.get('name') == value['relative_path'].rsplit('/', 1)[-1]:
                        value.pop('name')
                return value
            if isinstance(value, list):
                return [canonical(v) for v in value]
            return value
        hashes = {hashlib.sha256(json.dumps(canonical(v), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                  for v in facts if v}
        added = bool(hashes - self.facts)
        self.facts.update(hashes)
        self.stalled = 0 if added else self.stalled + 1
        self.recent = [*self.recent[-5:], added]
        return added

    def feedback(self):
        return {'new_evidence': self.recent[-1] if self.recent else False,
                'consecutive_unproductive_calls': self.stalled,
                'rounds_remaining': self.round_limit - self.rounds,
                'calls_remaining': self.call_limit - self.calls,
                'complete_inventory_observed': self.inventory_ready,
                'next_step': ('全量扫描已完成，按统计和已核实样例准备待运行方案或回答；只查会改变方案的关键元数据，不逐项枚举。'
                              if self.inventory_ready else '批量需求优先用 inventory_data_files 的 summary 获取全量统计；工具故障先修参数，不当作需求歧义。'),
                'instruction': ('已有连续查询没有新增事实，请改变工具、范围或处理方法；证据足够时直接准备方案或回答。'
                                if self.stalled >= 3 else '按完成条件决定下一步，避免重复查询。')}


def checkpoint(observations, reason):
    """Small server-owned, scope-bound past evidence; never grants or current truth."""
    values = [preview({'tool': i['tool'], 'arguments': i.get('arguments', {}), 'cached': i.get('cached', False),
                       'result': preview(i['result'], 1200)}, 2200) for i in observations[-8:]]
    return {'stop_reason': reason, 'observed_at': time.time(), 'observations': values,
            'current_state_verified': False,
            'instruction': '这是历史查询证据，不是当前状态或权限。继续时只复查会影响目标判断的事实。'}
