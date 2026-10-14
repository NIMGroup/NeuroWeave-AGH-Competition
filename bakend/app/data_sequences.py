"""Fixed read-only sequence profiling: small headers, paired gradients, tri-state.

No software is launched and no image array is loaded. A representative profile
applies to that image, never automatically to every registered sample.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import struct
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import workspace_io
from .agent_file_queries import matches, rows_for
from .data_selection import DataSelection
from .operation_context import checkpoint
from .workspace_paths import WorkspacePaths


def file_version(path):
    try:
        stat = path.lstat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    except OSError:
        return None


def read_small(path, maximum=256 * 1024):
    with os.fdopen(workspace_io.open_read(path), 'rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('超过只读元数据大小上限')
    return raw.decode('utf-8-sig')


def gradient_summary(text, vectors=None):
    values = [float(v) for v in text.split()]
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('bval 必须为有限非负数，不能为空')
    counts = Counter(0 if b <= 100 else int(round(b / 50) * 50) for b in values)
    result = {'volume_count': len(values), 'b0_count': counts.get(0, 0),
              'shells': [{'b_value': b, 'count': n} for b, n in sorted(counts.items()) if b],
              'shell_count': sum(b != 0 for b in counts), 'shell_bin_width': 50}
    if vectors is not None:
        rows = [[float(v) for v in line.split()] for line in vectors.splitlines() if line.strip()]
        if any(not math.isfinite(v) for row in rows for v in row):
            raise ValueError('bvec 必须为有限数值')
        lengths = [len(row) for row in rows]
        # FSL 3xN and explicit Nx3 are both accepted, without transposing facts.
        count = lengths[0] if len(rows) == 3 and len(set(lengths)) == 1 else len(rows) if set(lengths) == {3} else None
        result.update(vector_count=count, vector_shape=[len(rows), lengths[0] if len(set(lengths)) == 1 else None],
                      gradients_consistent=count == len(values))
    return result


def image_header(path):
    with os.fdopen(workspace_io.open_read(path), 'rb') as source:
        if path.name.lower().endswith('.mif'):
            parts, total, fields, gradients = [], 0, {}, []
            while total < 65536:
                checkpoint()
                line = source.readline(min(4096, 65536 - total))
                total += len(line)
                text = line.decode('utf-8').strip()
                if text == 'END':
                    break
                if not line:
                    raise ValueError('MIF 头缺少 END')
                parts.append(text)
                if text.startswith('dw_scheme:'):
                    gradients.append([float(v) for v in text.split(':', 1)[1].split(',')])
                elif ':' in text:
                    key, value = text.split(':', 1)
                    fields.setdefault(key, value.strip())
            else:
                raise ValueError('MIF 头超过64 KiB')
            if not parts or parts[0] != 'mrtrix image':
                raise ValueError('不是可识别的 MIF 图像')
            dims = [int(v) for v in fields.get('dim', '').split(',') if v]
            if not dims or any(v < 1 for v in dims):
                raise ValueError('MIF dimensions 无效')
            value = {'format': 'mif', 'dimensions': dims,
                     'volume_count': dims[3] if len(dims) > 3 else 1}
            if gradients:
                if any(len(g) != 4 or not all(math.isfinite(v) for v in g) for g in gradients):
                    raise ValueError('MIF dw_scheme 格式错误')
                value['embedded_gradients'] = gradient_summary(' '.join(str(g[3]) for g in gradients))
                value['embedded_gradients']['gradients_consistent'] = len(gradients) == value['volume_count']
            return value
        stream = gzip.GzipFile(fileobj=source) if path.name.lower().endswith('.gz') else source
        try:
            raw = stream.read(540)
        finally:
            if stream is not source:
                stream.close()
    if len(raw) < 348:
        raise ValueError('NIfTI 头长度不足')
    order = '<' if struct.unpack('<i', raw[:4])[0] in {348, 540} else '>'
    size = struct.unpack(order + 'i', raw[:4])[0]
    if size == 348 and raw[344:347] in {b'n+1', b'ni1'}:
        dims = struct.unpack(order + '8h', raw[40:56])
    elif size == 540 and len(raw) == 540 and raw[4:7] == b'n+2':
        dims = struct.unpack(order + '8q', raw[16:80])
    else:
        raise ValueError('不是可识别的 NIfTI 头')
    if not 1 <= dims[0] <= 7 or any(v < 1 for v in dims[1:dims[0]+1]):
        raise ValueError('NIfTI dimensions 无效')
    shape = list(dims[1:dims[0]+1])
    return {'format': 'nifti', 'dimensions': shape, 'volume_count': shape[3] if len(shape) > 3 else 1}


def profile_sequence(root, relative, policy=None):
    policy = policy or WorkspacePaths(root, allowed_roots=(root,))
    path = policy.resolve(root, relative)
    if policy.sensitive(path):
        raise PermissionError('序列路径不可读取')
    lower = path.name.lower()
    stem = path.name[:-7] if lower.endswith('.nii.gz') else path.stem
    prefix = PurePosixPath(relative).with_name(stem).as_posix()
    result = {'series_id': relative, 'image': relative, 'association': 'same_directory_exact_stem',
              'status': 'unknown', 'issues': [], 'features': {}, 'source_file_versions': {}}
    result['source_file_versions'][relative] = file_version(path)
    try:
        result['header'] = image_header(path)
        result['features']['image'] = 'found'
    except FileNotFoundError:
        result['features']['image'] = 'missing'
        result['status'] = 'missing'
        result['issues'].append('指定影像文件不存在')
    except (OSError, ValueError, EOFError, UnicodeError, struct.error) as exc:
        result['features']['image'] = 'unknown'
        result['issues'].append(str(exc)[:200])
    if file_version(path) != result['source_file_versions'][relative]:
        result['features']['image'] = 'unknown'
        result['issues'].append('读取期间影像头来源变化')
    texts = {}
    for suffix in ('bval', 'bvec', 'json'):
        candidate = prefix + '.' + suffix
        result['source_file_versions'][candidate] = file_version(Path(root) / candidate)
        try:
            companion = policy.resolve(root, candidate)
            if policy.sensitive(companion):
                raise PermissionError('伴随文件路径不可读取')
            resolved_relative = companion.relative_to(root).as_posix()
            result['source_file_versions'][resolved_relative] = file_version(companion)
            texts[suffix] = read_small(companion)
            if (file_version(Path(root) / candidate) != result['source_file_versions'][candidate]
                    or file_version(companion) != result['source_file_versions'][resolved_relative]):
                texts.pop(suffix, None)
                raise ValueError('读取期间伴随文件变化')
            result[suffix] = candidate
            result['features'][suffix] = 'found'
        except FileNotFoundError:
            result['features'][suffix] = 'missing'
        except (OSError, ValueError, UnicodeError) as exc:
            result['features'][suffix] = 'unknown'
            result['issues'].append(suffix + ': ' + str(exc)[:150])
    header = result.get('header', {})
    if 'bval' in texts:
        try:
            result['gradients'] = gradient_summary(texts['bval'], texts.get('bvec'))
        except ValueError as exc:
            result['issues'].append(str(exc)[:200])
    elif header.get('embedded_gradients'):
        result['gradients'] = header['embedded_gradients']
        result['gradient_source'] = 'mif_header'
    gradients = result.get('gradients', {})
    if gradients:
        consistent = gradients.get('gradients_consistent') is True
        count_matches = gradients['volume_count'] == header.get('volume_count')
        result['features']['paired_gradients'] = 'found' if consistent and count_matches else 'unknown'
        if result['features']['image'] != 'missing':
            result['status'] = 'found' if consistent and count_matches and not result['issues'] else 'unknown'
        if not consistent or not count_matches:
            result['issues'].append('梯度、向量与影像 volume 数尚未一致核验')
    else:
        result['features']['paired_gradients'] = 'unknown'
    if 'json' in texts:
        try:
            metadata = json.loads(texts['json'])
            if not isinstance(metadata, dict):
                raise ValueError('JSON 顶层不是对象')
            keys = ('PhaseEncodingDirection', 'TotalReadoutTime', 'EchoTime', 'RepetitionTime',
                    'SeriesDescription', 'ProtocolName', 'GeneratedBy', 'PipelineDescription')
            from .agent_metadata import compact_value
            result['metadata'] = {k: compact_value(metadata[k]) for k in keys if k in metadata}
        except (ValueError, RecursionError) as exc:
            result['issues'].append(str(exc)[:200])
            result['features']['json'] = 'unknown'
    result['features']['preprocessing_verified'] = 'unknown'
    result['notice'] = '核验只适用于此影像；同名伴随文件缺失不证明其他命名映射不存在。文件名及处理说明不证明处理质量。'
    return result


class SequenceQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_scope: Literal['dataset', 'output']
    selection: DataSelection = Field(default_factory=DataSelection)
    patterns: list[str] = Field(default_factory=lambda: ['*.nii', '*.nii.gz', '*.mif'], max_length=8)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4, ge=1, le=8)
    follow_links: bool = False


SEQUENCE_TOOL = {'type': 'function', 'function': {'name': 'inspect_data_sequences',
    'description': '只读按影像序列核验：读取小型 NIfTI/MIF 头、同目录同stem的bval/bvec及必要JSON字段，不读像素、不运行软件。返回各序列梯度壳层、体积数量和found/missing/unknown；样例不代表全数据集，按明确缺口选路径或patterns。',
    'parameters': SequenceQuery.model_json_schema()}}


def inspect_sequences(service, request, scope, arguments):
    query = SequenceQuery.model_validate(arguments)
    if any(not p or len(p) > 200 or '..' in PurePosixPath(p).parts or p.startswith('/') for p in query.patterns):
        raise ValueError('需要安全的相对文件模式')
    source, _ = service._roots(scope)
    inventory, rows = rows_for(service, request, scope, query.selection, query.follow_links)
    images = [r for r in rows if r['path'].lower().endswith(('.nii', '.nii.gz', '.mif'))
              and any(matches(r, p) for p in query.patterns)]
    images.sort(key=lambda r: r['path'])
    selected = images[query.offset:query.offset + query.limit]
    profiles = []
    policy = WorkspacePaths(source, allowed_roots=(source,))
    for row in selected:
        checkpoint('只读核验影像序列和梯度关联')
        profile = profile_sequence(source, row.get('resolved_path', row['path']), policy)
        if row.get('resolved_path'):
            profile.update(via_path=row['path'], writable=False)
        profiles.append(profile)
    complete = bool(inventory['complete_scan'] and inventory['scope_complete'])
    next_offset = query.offset + len(selected)
    return {k: v for k, v in inventory.items() if k in {'source_scope', 'root_path', 'complete_scan',
            'scope_complete', 'index_id', 'index_status', 'warnings', 'skipped_symlinks'}} | {
        'selection': query.selection.model_dump(), 'series': profiles, 'matched_series_count': len(images),
        'source_file_versions': {k: v for p in profiles for k, v in p['source_file_versions'].items()},
        'verified_series_count': sum(p['status'] == 'found' for p in profiles),
        'scope_status': 'found' if images else 'missing' if complete else 'unknown',
        'complete': complete and query.offset == 0 and next_offset >= len(images),
        'coverage_scope': 'selected_series_page', 'offset': query.offset,
        'next_offset': next_offset if next_offset < len(images) else None,
        'notice': '壳层数量属于具体序列，不是整个数据集；旧登记特征不覆盖本次直接核验。未经读取的样本保持未知。'}
