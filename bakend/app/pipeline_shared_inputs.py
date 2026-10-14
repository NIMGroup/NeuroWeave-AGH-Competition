"""Merge external input ports without inventing a new execution primitive.

Every consumer still uses RecipeCompiler's workflow_input binding. The shared
public contract is the intersection of accepted files and the union of needs.
"""
import copy


def merge_shared_inputs(groups, inputs, steps):
    public = {slot['id']: slot for slot in inputs}
    by_step = {step['id']: step for step in steps}
    replacements, merged, used = {}, {}, set()
    for index, group in enumerate(groups):
        if not 2 <= len(group) <= 500:
            raise ValueError('共享输入组必须包含 2–500 个输入端口')
        members, source_ids = [], []
        for port in group:
            key = (port.step_id, port.input_slot_id)
            if key in used:
                raise ValueError('输入端口不能重复加入共享组')
            used.add(key)
            step = by_step.get(port.step_id)
            binding = step and step['input_bindings'].get(port.input_slot_id)
            if not binding:
                raise ValueError('共享输入引用了不存在的步骤或输入端口')
            if binding['source'] != 'workflow_input':
                raise ValueError('已连接上游输出的输入不能同时共享外部文件，请先解除上游连接')
            source_ids.append(binding['slot_id'])
            members.append(public[binding['slot_id']])
        first = members[0]
        for field in ('type', 'cardinality', 'binding'):
            if any(slot[field] != first[field] for slot in members[1:]):
                raise ValueError('共享输入的类型、文件数量和文件/目录形式必须一致：' + field)
        formats = [set(slot['formats']) for slot in members if slot['formats']]
        common_formats = set.intersection(*formats) if formats else set()
        if formats and not common_formats:
            raise ValueError('共享输入没有共同支持的文件格式')
        common_sources = set.intersection(*(set(slot['source_options']) for slot in members))
        if not common_sources:
            raise ValueError('共享输入没有共同支持的文件来源')
        shared_id = f'shared_input_{index + 1}'
        item = copy.deepcopy(first)
        conditions = []
        for slot in members:
            if slot.get('required_when'):
                conditions.append(slot['required_when'])
            conditions.extend(slot.get('required_when_any', []))
        item.update(id=shared_id, label='共享输入 · ' + ' / '.join(slot['label'] for slot in members),
                    description='这些输入共用同一份文件；按样本运行时共用该样本对应的文件。\n' +
                        '\n'.join(slot['label'] + '：' + slot['description'] for slot in members),
                    required=any(slot['required'] for slot in members), required_when={},
                    required_when_any=conditions, formats=sorted(common_formats),
                    source_options=sorted(common_sources))
        for field in ('validation', 'companions'):
            item[field] = list(dict.fromkeys(value for slot in members for value in slot[field]))
        merged[shared_id] = item
        for port, source_id in zip(group, source_ids):
            replacements[source_id] = shared_id
            by_step[port.step_id]['input_bindings'][port.input_slot_id] = {'source': 'workflow_input', 'slot_id': shared_id}
    result, emitted = [], set()
    for slot in inputs:
        shared_id = replacements.get(slot['id'])
        if not shared_id:
            result.append(slot)
        elif shared_id not in emitted:
            result.append(merged[shared_id]); emitted.add(shared_id)
    return result
