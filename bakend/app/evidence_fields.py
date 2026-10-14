"""Exact saved-field access, including literal dots in file/pattern keys."""
from __future__ import annotations


def read_field(value, field):
    """JSON Pointer is unambiguous; old dotted paths remain compatible.

    A legacy path is accepted only when exactly one structural interpretation
    exists. A summary/result prefix is an optional view wrapper, not a field
    requirement on the original observation.
    """
    if field.startswith('/'):
        for segment in field[1:].split('/'):
            segment = segment.replace('~1', '/').replace('~0', '~')
            value = value[int(segment)] if isinstance(value, list) else value[segment]
        return value

    def candidates(current, segments):
        if not segments:
            return [current]
        if isinstance(current, list):
            if not segments[0].isdigit():
                return []
            index = int(segments[0])
            return candidates(current[index], segments[1:]) if index < len(current) else []
        if not isinstance(current, dict):
            return []
        results = []
        for count in range(1, len(segments) + 1):
            key = '.'.join(segments[:count])
            if key in current:
                results.extend(candidates(current[key], segments[count:]))
            if len(results) > 1:
                break
        return results

    segments = field.split('.')
    results = candidates(value, segments)
    if not results and segments[0] in {'summary', 'result'} and segments[0] not in value:
        results = candidates(value, segments[1:])
    if len(results) != 1:
        raise KeyError('字段不存在或点号路径存在歧义；请使用 JSON Pointer')
    return results[0]
