"""Decode tool JSON envelopes without repairing or changing code or permissions."""
import json


def tool_arguments(raw):
    try:
        value = json.loads(raw)
        for _ in range(2):
            if isinstance(value, str):
                value = json.loads(value)
            elif isinstance(value, dict) and set(value) == {'arguments'}:
                value = value['arguments']
            else:
                break
        if isinstance(value, str):
            value = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError('工具参数不是完整 JSON；只修正参数序列化，保留已有事实与代码，不重新调查。') from exc
    if not isinstance(value, dict):
        raise ValueError('工具参数应为 JSON 对象；按工具字段直接提交，不套 arguments 字符串。')
    return value
