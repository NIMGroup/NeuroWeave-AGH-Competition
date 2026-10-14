"""Opt-in real Docker checks for prebuilt library images; no build or pull."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest

from app.data_sandbox import DataSandbox, SandboxCancelled

pytestmark = pytest.mark.skipif(not os.getenv('NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS_FILE'),
                                reason='Configure audited library images for real Docker validation')


def test_table_image_roundtrips_excel_with_numpy_and_preserves_readonly_input(tmp_path):
    source = tmp_path / 'input'; source.mkdir()
    (source / 'sample.csv').write_text('value\n1\n3\n', encoding='utf8')
    sandbox = DataSandbox()
    with sandbox.execution('table', 'standard'):
        outputs = sandbox.run(source, tmp_path / 'table', """
import pandas as pd, numpy as np, socket
from pathlib import Path
frame = pd.read_csv('/input/sample.csv')
frame['normalized'] = frame['value'] / np.mean(frame['value'])
frame.to_excel('/output/result.xlsx', index=False, engine='openpyxl')
assert pd.read_excel('/output/result.xlsx', engine='openpyxl')['normalized'].tolist() == [.5, 1.5]
try:
    Path('/input/sample.csv').write_text('forbidden')
    raise AssertionError('Input was writable')
except OSError: pass
try:
    socket.create_connection(('1.1.1.1',443), timeout=.5)
    raise AssertionError('Network was enabled')
except OSError: pass
print('table-roundtrip-ok', flush=True)
""")
    assert (outputs / 'result.xlsx').stat().st_size > 1000
    assert (source / 'sample.csv').read_text() == 'value\n1\n3\n'
    assert 'table-roundtrip-ok' in (outputs.parent / 'execution.log').read_text()


def test_imaging_image_roundtrips_synthetic_nifti(tmp_path):
    source = tmp_path / 'input'; source.mkdir()
    sandbox = DataSandbox()
    with sandbox.execution('imaging', 'standard'):
        outputs = sandbox.run(source, tmp_path / 'imaging', """
import nibabel as nib, numpy as np, pandas as pd
image = nib.Nifti1Image(np.arange(24,dtype=np.float32).reshape((2,3,4)), np.eye(4))
nib.save(image, '/output/synthetic.nii.gz')
assert np.array_equal(nib.load('/output/synthetic.nii.gz').get_fdata(), image.get_fdata())
pd.DataFrame({'shape':[str(image.shape)],'mean':[float(image.get_fdata().mean())]}).to_csv('/output/summary.csv',index=False)
print('imaging-roundtrip-ok', flush=True)
""")
    assert (outputs / 'synthetic.nii.gz').is_file() and (outputs / 'summary.csv').is_file()
    assert 'imaging-roundtrip-ok' in (outputs.parent / 'execution.log').read_text()


def test_cancelling_real_library_script_removes_container_without_export(tmp_path):
    source = tmp_path / 'input'; source.mkdir()
    event, sandbox = Event(), DataSandbox()
    directory = tmp_path / 'cancel'
    def run():
        with sandbox.execution('table', 'standard', event):
            sandbox.run(source, directory, "import time; print('started',flush=True); time.sleep(200)")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        deadline = time.monotonic() + 25
        try:
            while time.monotonic() < deadline:
                log = directory / 'execution.log'
                if log.is_file() and 'started' in log.read_text():
                    break
                time.sleep(.1)
            else:
                raise AssertionError('Real script did not start')
        finally:
            event.set()
        with pytest.raises(SandboxCancelled):
            future.result(timeout=10)
    assert not list((directory / 'artifacts').iterdir())
