"""Path-scoped views of stored observations; never infer scientific properties."""
from pathlib import PurePosixPath


def observed_inputs(row, raw):
    """Return proven paths and records whose properties belong to that path.

    Sequence profiles use image/bval/bvec rather than the inventory's path key.
    These are fixed tool schemas, not arbitrary strings extracted from text.
    """
    paths, records = set(), {}
    selected = row['arguments'].get('path', row['arguments'].get('selection', {}).get('path', '.'))

    def add(path, record=None):
        if not isinstance(path, str) or not path:
            return
        path = path.replace('\\', '/')
        paths.add(path)
        if record is not None:
            records.setdefault(path, []).append(record)

    def visit(value):
        if isinstance(value, dict):
            if value.get('exists') is False or value.get('status') in {'missing', 'unreadable'}:
                return
            if value.get('status') == 'unknown':
                return
            for key in ('path', 'relative_path', 'name'):
                add(value.get(key), value)
            for key in ('examples', 'paths'):
                if isinstance(value.get(key), list):
                    for path in value[key]:
                        add(path)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(raw)
    if row['tool'] == 'inspect_data_sequences':
        for series in raw.get('series', []):
            # Overall status may be unknown for gradients while the image is
            # observed. Each component retains its own explicit three-state value.
            features = series.get('features', {})
            if features.get('image') == 'found':
                add(series.get('image'), series)
            for kind in ('bval', 'bvec', 'json'):
                if features.get(kind) == 'found':
                    add(series.get(kind), {'gradients': series.get('gradients', {})} if kind != 'json' else {})
    if row['tool'] != 'inspect_data_sequences' and row['tool'].startswith(('inspect_', 'read_')) and raw.get('exists') is not False and 'error' not in raw:
        add(selected, raw)
    for path in list(paths):
        if not PurePosixPath(path).is_absolute():
            joined = (PurePosixPath(selected) / path).as_posix()
            paths.add(joined)
            if path in records:
                records.setdefault(joined, []).extend(records[path])
    return paths, records


def observed_values(records):
    values = {}
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(child, (dict, list)):
                    values.setdefault(key, []).append(child)
                elif isinstance(child, list):
                    values.setdefault(key, []).append(child)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for record in records:
        visit(record)
    return values
