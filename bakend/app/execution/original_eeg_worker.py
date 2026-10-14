"""Call independent original EEG functions without changing their formulas."""
import argparse
import importlib.util
from pathlib import Path
import mne

ROOT = Path(__file__).resolve().parents[3]
FUNCTIONS = {
    'bands': 'extract_frequency_bands',
    'erp': 'extract_erp_features',
    'alpha-ratio': 'compute_alpha_asymmetry',
    'features': 'extract_features',
    'microstates': 'run_microstate_analysis',
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--operation', choices=FUNCTIONS, required=True)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    spec = importlib.util.spec_from_file_location('original_eeg', ROOT/'skills/mne-eeg-tool/scripts/eeg_pipeline_reference.py')
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    data = mne.read_epochs(a.input, preload=True) if a.operation in {'erp', 'features'} else original.load_eeg(str(a.input))
    a.output_dir.mkdir(parents=True, exist_ok=True)
    getattr(original, FUNCTIONS[a.operation])(data, str(a.output_dir))


if __name__ == '__main__':
    main()
