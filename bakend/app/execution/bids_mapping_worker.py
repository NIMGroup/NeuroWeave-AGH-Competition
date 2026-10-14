"""Stage explicitly mapped files and standard JSON metadata; never infer BIDS entities."""
import argparse
import json
from pathlib import Path
import shutil


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--operation', choices=['metadata','stage'], required=True)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--mapping', type=Path)
    p.add_argument('--name', default='NeuroWeave dataset')
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    out = a.output_dir
    out.mkdir(parents=True, exist_ok=True)
    report = {'filename_checks': [], 'full_bids_validation': False}
    if a.operation == 'stage':
        from bids_validator import BIDSValidator
        if not a.mapping:
            raise ValueError('An explicit source -> BIDS destination JSON mapping is required')
        mapping = json.loads(a.mapping.read_text(encoding='utf-8'))
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError('Mapping must be a nonempty JSON object of relative paths')
        root = a.input.resolve(strict=True)
        validator = BIDSValidator()
        destinations = set()
        pairs = []
        for source, target in mapping.items():
            if not isinstance(source, str) or not isinstance(target, str):
                raise ValueError('Mapping paths must be strings')
            if Path(source).is_absolute() or Path(target).is_absolute() or '..' in Path(target).parts or '\\' in target:
                raise ValueError('Mapping paths must stay inside input and output roots')
            src = (root/source).resolve(strict=True)
            dst = (out/target).resolve()
            if not src.is_relative_to(root) or not src.is_file() or not dst.is_relative_to(out.resolve()):
                raise ValueError('Mapping escapes a bound directory')
            if dst in destinations or target=='dataset_description.json':
                raise ValueError('Duplicate or reserved BIDS destination')
            if not validator.is_bids('/'+target):
                raise ValueError('Invalid BIDS filename: '+target)
            destinations.add(dst)
            pairs.append((src,dst))
        for src,dst in pairs:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src,dst)
            report['filename_checks'].append(dst.relative_to(out.resolve()).as_posix())
    # This is metadata/filename staging, not a claim of full BIDS compliance.
    (out/'dataset_description.json').write_text(json.dumps({
        'Name':a.name, 'BIDSVersion':'1.9.0', 'DatasetType':'raw'
    }, indent=2), encoding='utf-8')
    (out/'staging_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
