"""Independent ROI output oracle for arbitrary registered server inputs.

Input validation is owned by the AGH verifier. Numeric statistics use
Python's math/statistics and explicit linear interpolation, not the Runner's
NumPy/Pandas aggregation implementation or synthetic expected-value manifest.
"""
import csv
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('roi_input_contract', root/'scripts/hackathon/roi-input-contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def percentile(values, percent):
    position = (len(values)-1) * percent / 100
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] + (values[upper]-values[lower]) * (position-lower)


def verify(request):
    _, metric, atlas = contract.validated_arrays(Path(request['metric']), Path(request['roi']))
    labels = Path(request['labels']).read_text().splitlines() if request.get('labels') else []
    with Path(request['csv']).open(newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != contract.COLUMNS:
            raise ValueError('unexpected_roi_csv_columns')
        rows = list(reader)
    expected = {}
    for label in contract.np.unique(atlas):
        if label <= 0:
            continue
        values = sorted(float(value) for value in metric[atlas == label] if math.isfinite(float(value)))
        if len(values) < request['min_voxels']:
            continue
        expected[int(label)] = {'n_vox': len(values), 'mean': math.fsum(values)/len(values),
            'median': statistics.median(values), 'std': statistics.stdev(values) if len(values)>1 else 0,
            'p05': percentile(values, 5), 'p95': percentile(values, 95),
            'label_name': labels[int(label)-1] if int(label)-1 < len(labels) else ''}
    seen, failures, assertions = set(), [], 0
    for row in rows:
        label = int(row['label'])
        if label in seen or label not in expected:
            failures.append({'label': label, 'reason': 'duplicate_or_unexpected_label'})
            continue
        seen.add(label)
        for field, value in expected[label].items():
            assertions += 1
            passed = row[field] == value if isinstance(value, str) else (
                math.isfinite(float(row[field])) and math.isclose(float(row[field]), value, rel_tol=1e-7, abs_tol=1e-10))
            if not passed:
                failures.append({'label': label, 'field': field, 'actual': row[field], 'expected': value})
    if seen != set(expected):
        failures.append({'reason': 'missing_roi_rows', 'labels': sorted(set(expected)-seen)})
    return {'passed': not failures, 'rows': len(rows), 'assertions': assertions,
            'failures': failures[:32], 'oracle': 'math/statistics plus explicit percentile interpolation',
            'scope': 'ROI values for these exact inputs and min_voxels; no clinical validity claim'}


if __name__ == '__main__':
    try:
        print(json.dumps(verify(json.load(sys.stdin)), allow_nan=False))
    except Exception as error:
        print(json.dumps({'passed': False, 'error': str(error)[:1000]}))
        raise SystemExit(1)
