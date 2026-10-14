"""Declared Python result contracts and conservative checks; never execute code."""
import ast
from collections import Counter
from pathlib import PurePosixPath

MAX_CHANGE_EDITS = 100000
MAX_CHANGE_BYTES = 32 * 1024 * 1024
RESULT_CONTRACTS = {
    'input': '/input (read-only)', 'output': '/output', 'execution': 'manual_run_only',
    'verification': {'test_cases': 'Declare callable function cases with args/kwargs/expected; ordinary and edge cases for custom transformations. Executed only with manually approved sandbox Run.',
                     'manifest_invariants': 'Declare selected-file coverage and stem-group extensions when required by goal. Backend checks actual effects independently of stdout.',
                     'semantic_rule': 'Shared text is not automatically redundant. Remove only an observed identity prefix; preserve meaningful category fields. Unknown and exceptional inputs keep safe information or fail before proposing changes.'},
    'changes': {'file': '/output/changes.json', 'schema': {'edits': [{'action': 'copy|rename|write_text|delete',
        'path': 'source-relative path', 'destination': 'target-relative path'}]},
        'max_edits': MAX_CHANGE_EDITS, 'max_bytes': MAX_CHANGE_BYTES,
        'extra_files': False, 'report': 'Print reports to stdout; no additional files in changes mode.',
        'apply': 'Manual second Run after backend validates the generated manifest; overwrite/delete require confirmation.'},
    'files': {'outputs': 'Files under /output; generated files become a separately reviewed manifest.'},
    'analysis': {'outputs': 'Bounded stdout log; capture limits and omissions are reported. Never proves file changes have executed.'},
}


def validate_script(code, result_kind):
    """Reject syntax and provable contract violations, leave dynamic paths to sandbox validation."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f'Python 语法错误，第{exc.lineno}行：{exc.msg}；脚本尚未执行') from exc
    if result_kind != 'changes':
        return
    values = {}

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.BinOp):
            left, right = value(node.left), value(node.right)
            if left is not None and right is not None:
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Div):
                    return str(PurePosixPath(left) / right)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {'Path', 'PurePosixPath'} and len(node.args) == 1:
                return value(node.args[0])
            if isinstance(func, ast.Attribute) and func.attr in {'Path', 'PurePosixPath'} and len(node.args) == 1:
                return value(node.args[0])
            if isinstance(func, ast.Attribute) and func.attr == 'join':
                parts = [value(arg) for arg in node.args]
                if parts and all(p is not None for p in parts):
                    return str(PurePosixPath(*parts))
        return None

    bindings = Counter(n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
    shadowed = {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
    # Only singly bound module-level constants are inferred; branch/loop and shadowing stay unknown.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            resolved = value(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name) and bindings[target.id] == 1 and target.id not in shadowed:
                    values[target.id] = resolved
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        path, mode = None, None
        if isinstance(func, ast.Name) and func.id == 'open' and node.args:
            path = value(node.args[0])
            mode = value(node.args[1]) if len(node.args) > 1 else next((value(k.value) for k in node.keywords if k.arg=='mode'), 'r')
        elif isinstance(func, ast.Attribute) and func.attr in {'write_text', 'write_bytes', 'open'}:
            path = value(func.value)
            mode = 'w' if func.attr != 'open' else value(node.args[0]) if node.args else next((value(k.value) for k in node.keywords if k.arg=='mode'), 'r')
        if path is None or mode is None or not any(flag in mode for flag in 'wax+'):
            continue
        target = PurePosixPath(path)
        target = target if target.is_absolute() else PurePosixPath('/output') / target
        if target != PurePosixPath('/output/changes.json'):
            raise ValueError(f'changes 输出契约冲突，第{node.lineno}行写入 {path}；该模式只允许 /output/changes.json，报告请打印到 stdout。尚未执行脚本。')
