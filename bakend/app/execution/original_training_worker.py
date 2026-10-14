"""Manage original project trainers; their model, loss and training loops are unchanged.

Bind the original dataset root to a read-only user directory and redirect the
original checkpoint/log outputs into this job. No replacement training is used.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
TRAINERS = {
    'bnt': 'skills/bnt/scripts/train_reference.py',
    'brainnetcnn': 'models/brainnetcnn/scripts/train.py',
    'combraintf': 'skills/combraintf/scripts/train_reference.py',
}


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            stream.write(value)
            stream.flush()
        return len(value)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--model', choices=TRAINERS, required=True)
    p.add_argument('--fmri-root', type=Path, required=True)
    p.add_argument('--labels-csv', type=Path, required=True)
    p.add_argument('--atlas', required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a, original_arguments = p.parse_known_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', a.atlas) or '..' in a.atlas:
        raise ValueError('Atlas must be one directory name')
    sys.path.insert(0, str(ROOT))
    from models.bnt.scripts import data_adapter as dataset
    import pandas as pd
    import torch
    dataset.FMRI_ROOT = a.fmri_root.resolve(strict=True)
    atlas = (dataset.FMRI_ROOT/a.atlas).resolve(strict=True)
    if not atlas.is_relative_to(dataset.FMRI_ROOT):
        raise ValueError('Atlas escapes bound input directory')
    files = sorted(atlas.glob('sub-*.pt'))
    if not files:
        raise ValueError('Expected <atlas>/sub-<subject_id>.pt tensor dictionaries')
    # Read the original CLI contract for validation too; no independent defaults.
    spec = importlib.util.spec_from_file_location('neuroweave_original_trainer', ROOT/TRAINERS[a.model])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.argv = [str(ROOT/TRAINERS[a.model]), '--atlas', a.atlas,
                '--labels-csv', str(a.labels_csv), *original_arguments]
    if a.model == 'brainnetcnn':
        sys.argv += ['--save-dir', str(out/'checkpoints')]
        if '--device' in sys.argv:
            index = sys.argv.index('--device') + 1
            if sys.argv[index] == 'auto':
                # Placement is an adapter concern; torch.device has no "auto".
                sys.argv[index] = 'cuda' if torch.cuda.is_available() else 'cpu'
    args = module.parse_args()
    if args.fold >= args.kfold:
        raise ValueError('fold must be smaller than kfold')
    labels = pd.read_csv(a.labels_csv, dtype={args.subject_col: str})
    if labels[args.subject_col].duplicated().any():
        raise ValueError('Duplicate subject identifiers in labels')
    table = labels.set_index(args.subject_col)
    shape = None
    selected_labels = []
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(atlas):
            raise ValueError('Subject file escapes bound atlas')
        sid = path.stem.removeprefix('sub-')
        if sid not in table.index:
            raise ValueError('Every subject tensor must have an explicit label: '+sid)
        record = torch.load(path, weights_only=True)
        matrix = record.get('fc_matrix', record.get('node_features'))
        if matrix is None:
            raise ValueError('Original dataset requires fc_matrix or node_features')
        matrix = torch.as_tensor(matrix)
        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not torch.isfinite(matrix).all():
            raise ValueError('Original dataset requires finite square Fisher-z FC matrices')
        if shape is not None and matrix.shape != shape:
            raise ValueError('All subjects must use the same ROI order and size')
        shape = matrix.shape
        if a.model == 'combraintf' and not record.get('roi_names') and record.get('n_rois') != shape[0]:
            raise ValueError('ComBrainTF requires original roi_names or a matching n_rois')
        selected_labels.append(table.loc[sid, args.label_col])
    labels = pd.Series(selected_labels)
    if args.task == 'classification':
        if labels.isna().any() or any(v != int(v) or not 0 <= v < args.nclass for v in labels):
            raise ValueError('Class labels must be integers in [0, nclass)')
        if len(labels.unique()) < 2 or labels.value_counts().min() < args.kfold:
            raise ValueError('Each class needs at least kfold subjects')
    elif len(labels) < max(2*args.kfold, 10) or not __import__('numpy').isfinite(labels.astype(float)).all():
        raise ValueError('Insufficient subjects or invalid regression labels')
    if len(files) - (len(files)+args.kfold-1)//args.kfold < args.batch_size:
        raise ValueError('Original trainer drops incomplete batches; reduce batch_size')
    with (out/'training.log').open('w', encoding='utf-8') as handle:
        with contextlib.redirect_stdout(Tee(sys.stdout, handle)):
            module.main()
    (out/'execution.json').write_text(json.dumps({
        'trainer': TRAINERS[a.model], 'subjects': len(files), 'atlas': a.atlas,
        'model': a.model, 'completed': True,
        'cuda_used': bool(torch.cuda.is_initialized() and torch.cuda.max_memory_allocated() > 0),
        'torch_version': str(torch.__version__),
        'note': 'Original trainer output; BNT and ComBrainTF references do not save checkpoints.'
    }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
