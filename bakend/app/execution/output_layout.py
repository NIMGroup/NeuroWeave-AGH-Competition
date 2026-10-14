"""Physical output paths are independent of internal graph identifiers."""
from hashlib import sha256
from pathlib import Path, PurePosixPath


def path_component(identity: str) -> str:
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in identity)
    if not safe or safe in {'.', '..'}:
        safe = 'item'
    if safe != identity or len(safe.encode('utf-8')) > 160:
        suffix = sha256(identity.encode('utf-8')).hexdigest()[:12]
        while len(safe.encode('utf-8')) > 140:
            safe = safe[:-1]
        safe = safe + '--' + suffix
    return safe


def output_key(layout: str, step_key: str, *, sample_id: str | None = None,
               legacy_prefix: str = '') -> str:
    if layout == 'legacy':
        return legacy_prefix + step_key
    step = path_component(step_key)
    return path_component(sample_id) + '/' + step if sample_id else 'steps/' + step


def relative_output_key(key: str) -> str:
    # Keep legacy single-component normalization. Nested keys are compiler
    # generated and must be valid relative paths, never shell/user path syntax.
    if '/' not in key:
        key = ''.join(c if c.isalnum() or c in '-_' else '_' for c in key)
    else:
        parts = PurePosixPath(key).parts
        if PurePosixPath(key).is_absolute() or '\\' in key or any(
                p in {'.', '..'} or p != path_component(p) for p in parts):
            raise ValueError('输出目录不是安全的相对路径')
    if not key:
        raise ValueError('步骤输出目录不能为空')
    return str(PurePosixPath(key))


def step_directory(root: Path, key: str) -> Path:
    key = relative_output_key(key)
    resolved_root = root.resolve()
    candidate = (resolved_root / key).resolve()
    candidate.relative_to(resolved_root)
    if candidate == resolved_root:
        raise ValueError('步骤输出目录不能为空')
    return candidate
