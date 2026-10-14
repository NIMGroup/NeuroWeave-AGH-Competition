#!/usr/bin/env python3
"""Explicit administrator build; never called by chat or the running backend.

Run with Linux amd64 Python 3.11 and Docker. Downloads hashed wheels into a
private build context; installs nothing into the backend virtual environment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

BASE = 'public.ecr.aws/docker/library/python@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce'


def run(args, capture=False):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['table', 'imaging', 'all'], default='all')
    parser.add_argument('--docker', default='docker')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11) or sys.platform != 'linux':
        parser.error('Use Linux amd64 Python 3.11 to select the audited wheels')
    root = Path(__file__).resolve().parents[1] / 'deploy/data-python'
    audited = json.loads((root / 'wheels.json').read_text(encoding='utf8'))['packages']
    registry = json.loads(args.output.read_text(encoding='utf8')) if args.output.exists() else {}
    for profile in ('table', 'imaging') if args.profile == 'all' else (args.profile,):
        lock = root / (profile + '.lock')
        content = lock.read_bytes()
        lock_hash = hashlib.sha256(content).hexdigest()
        expected = {}
        for line in content.decode().splitlines():
            name, version = line.split(' ', 1)[0].split('==')
            assert audited[name]['version'] == version and audited[name]['sha256'] in line
            expected[name] = version
        with tempfile.TemporaryDirectory(prefix='neuroweave-image-') as temp:
            context = Path(temp)
            shutil.copyfile(root / 'Dockerfile', context / 'Dockerfile')
            shutil.copyfile(lock, context / 'requirements.lock')
            run([sys.executable, '-m', 'pip', 'download', '--index-url', 'https://pypi.org/simple', '--require-hashes',
                 '--only-binary=:all:', '-r', str(lock), '--dest', str(context / 'wheels')])
            tag = 'neuroweave-data-' + profile + ':' + lock_hash[:12]
            run([args.docker, 'build', '--network=none', '--pull=false', '--build-arg', 'BASE=' + BASE,
                 '--build-arg', 'PROFILE=' + profile, '--build-arg', 'LOCK_SHA256=' + lock_hash, '-t', tag, str(context)])
        image = run([args.docker, 'image', 'inspect', '--format', '{{.Id}}', tag], True).strip()
        code = 'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ' + repr(list(expected)) + '}))'
        actual = json.loads(run([args.docker, 'run', '--rm', '--network=none', '--read-only', '--user=65534:65534', image,
                                'python', '-I', '-c', code], True))
        if actual != expected:
            raise ValueError('Installed library versions differ from the audited lock')
        registry[profile] = {'image': image, 'packages': actual, 'lock_sha256': lock_hash, 'base': BASE}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(registry, indent=2) + '\n', encoding='utf8')
    temporary.chmod(0o600)
    os.replace(temporary, args.output)
    print(json.dumps(registry, indent=2))


if __name__ == '__main__':
    main()
