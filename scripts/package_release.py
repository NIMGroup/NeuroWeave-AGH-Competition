#!/usr/bin/env python3
"""Package committed public source with revision, per-file hashes and archive SHA-256."""
from pathlib import Path
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile


def package(output: Path, archive_format: str = "tar.gz"):
    root = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)

    if git("diff", "HEAD", "--name-only").strip():
        raise RuntimeError("Commit reviewed changes before packaging")
    revision = git("rev-parse", "HEAD").decode().strip()
    output.mkdir(parents=True, exist_ok=True)
    name = f"NeuroWeave-AGH-AGNES-{revision[:12]}.zip" if archive_format == "zip" else revision + ".tar.gz"
    target = output / name
    if target.exists():
        raise FileExistsError(target)
    files = {}
    modes = {}
    source = git("archive", "--format=tar", "HEAD")
    with tarfile.open(fileobj=io.BytesIO(source)) as archive:
        for member in archive:
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError("Release contains a link or unsupported file")
            parts = Path(member.name).parts
            if any(part in {".git", "runtime", "node_modules", "__pycache__"} for part in parts):
                raise RuntimeError("Release contains private state or generated dependencies")
            if Path(member.name).name == ".env" or member.name.endswith((".sqlite", ".db")):
                raise RuntimeError("Release contains a local environment or database")
            files[member.name] = archive.extractfile(member).read()
            modes[member.name] = member.mode
    files["revision.txt"] = (revision + "\n").encode()
    modes["revision.txt"] = 0o644
    file_manifest = {
        "revision": revision,
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in sorted(files.items())},
    }
    files["FILES.json"] = (json.dumps(file_manifest, ensure_ascii=False, indent=2) + "\n").encode()
    modes["FILES.json"] = 0o644
    if archive_format == "zip":
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as release:
            for name, value in sorted(files.items()):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o100000 | modes[name]) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                release.writestr(info, value)
    else:
        with tarfile.open(target, "w:gz") as release:
            for name, value in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(value), modes[name]
                release.addfile(info, io.BytesIO(value))
    manifest = {
        "revision": revision, "archive": target.name, "files": len(files),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    target.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=["tar.gz", "zip"], default="tar.gz")
    args = parser.parse_args()
    package(args.output, args.format)
