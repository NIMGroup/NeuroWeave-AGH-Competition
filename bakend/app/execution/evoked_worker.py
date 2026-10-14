"""Read existing MNE epochs and call the library's existing evoked average API."""
import argparse
from pathlib import Path
import mne


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    epochs = mne.read_epochs(a.input, preload=False)
    if not len(epochs):
        raise ValueError('No accepted epochs to average')
    a.output_dir.mkdir(parents=True, exist_ok=True)
    epochs.average().save(a.output_dir/'average-ave.fif', overwrite=True)


if __name__ == '__main__':
    main()
