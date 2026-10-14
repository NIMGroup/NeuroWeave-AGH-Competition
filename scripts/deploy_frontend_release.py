#!/usr/bin/env python3
"""Build a versioned frontend before activation; retain and restore its previous image."""
from pathlib import Path
import argparse
import datetime as dt
import json
import hashlib
import re
import shutil
import subprocess
import time
import urllib.request

PRODUCTION = Path("/home/hipeson/LocalFlow")
RELEASES = Path("/home/hipeson/.cache/neuroweave/releases")
BACKUPS = Path("/home/hipeson/.cache/neuroweave/frontend-backups")
MANAGED_FILES = ("compose.yaml", "scripts/deploy_frontend_release.py", "README.md",
                 "deploy/frontend-migration.md", "deploy/history-and-existing-inputs.md",
                 "deploy/historical-run-inputs.md", "deploy/workspaces.md")


def require(condition,message):
    if not condition:
        raise RuntimeError(message)


def command(argv,*,cwd=PRODUCTION):
    return subprocess.check_output(argv,cwd=cwd,text=True,timeout=180).strip()


def switch(image):
    require(re.fullmatch(r"sha256:[0-9a-f]{64}",image),"Expected an immutable Docker image ID.")
    command(["docker","tag",image,"localflow-frontend:latest"])
    command(["docker","compose","up","-d","--no-build","--no-deps","--wait","--wait-timeout","120","frontend"])
    command(["docker","compose","exec","-T","gateway","nginx","-t"])
    command(["docker","compose","exec","-T","gateway","nginx","-s","reload"])


def health(revision=None):
    last_error = None
    for _ in range(15):
        try:
            with urllib.request.urlopen("http://192.10.84.6:3000/",timeout=5) as response:
                require(response.status == 200,"Frontend health failed.")
            with urllib.request.urlopen("http://192.10.84.6:3000/api/health?include_runtime=false",timeout=5) as response:
                payload = json.load(response)
                require(payload.get("status") == "ok","API health failed.")
                if revision:
                    require(payload.get("release",{}).get("backend_revision") == revision,"Backend revision differs.")
            return
        except Exception as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("Release health check failed") from last_error


def verify_files(root, manifest):
    for name, digest in manifest.items():
        path = root / name
        require(not Path(name).is_absolute() and path.resolve().is_relative_to(root.resolve()), "Unsafe manifest path")
        require(not path.is_symlink(), "Refusing a redirected release file")
        if digest is None:
            require(not path.exists(), "Unexpected server file: " + name)
        else:
            require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                    "File changed; compare before deploying: " + name)


def install_source(source, backup):
    """Swap only frontend source; retain every old file, including local caches."""
    require(PRODUCTION.resolve() == PRODUCTION, "Production directory is redirected")
    current = PRODUCTION / "frontend"
    require(not current.is_symlink(), "Frontend directory is redirected")
    incoming = source / "frontend"
    require(not incoming.is_symlink() and all(not p.is_symlink() for p in incoming.rglob('*')),
            "Frontend source contains symlinks")
    shutil.copytree(incoming, backup / "incoming-frontend")
    originals = {}
    for name in MANAGED_FILES:
        path = PRODUCTION / name
        require(path.resolve().is_relative_to(PRODUCTION) and not path.is_symlink(), "Redirected server file")
        originals[name] = path.exists()
        if path.exists():
            saved = backup / "files" / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
    (backup / "source-backup.json").write_text(json.dumps(originals))
    current.rename(backup / "previous-frontend")
    (backup / "incoming-frontend").rename(current)
    for name in MANAGED_FILES:
        target = PRODUCTION / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)


def restore_source(backup):
    if not (backup / "source-backup.json").exists():
        return  # Backward compatible with existing image-only backups.
    previous = backup / "previous-frontend"
    if previous.exists():
        current = PRODUCTION / "frontend"
        if current.exists():
            current.rename(backup / "replaced-frontend")
        previous.rename(current)
    for name, existed in json.loads((backup / "source-backup.json").read_text()).items():
        target = PRODUCTION / name
        if existed:
            shutil.copy2(backup / "files" / name, target)
        elif target.exists():
            retained = backup / "added-files" / name
            retained.parent.mkdir(parents=True, exist_ok=True)
            target.rename(retained)


def backend_identity():
    return command(["systemctl", "--user", "show", "localflow-backend.service",
                    "--property=MainPID", "--property=ActiveEnterTimestamp"])


def deploy(source,revision,build_only,activate_only,backend_revision=None,prebuilt=False,expected_files=None):
    require(re.fullmatch("[0-9a-f]{40}",revision),"Expected a full Git revision.")
    require(source == RELEASES / revision and source.resolve() == source,"Unexpected source directory.")
    require((source / "revision.txt").read_text().strip() == revision,"Release marker mismatch.")
    backend_revision = backend_revision or revision
    require(re.fullmatch("[0-9a-f]{40}", backend_revision), "Expected a full backend revision")
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    require(BACKUPS.resolve() == BACKUPS,"Backup directory is redirected.")
    backup = BACKUPS / (revision+"-"+dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(mode=0o700)
    tag = "localflow-frontend:"+revision
    if prebuilt:
        dist = source / "frontend/dist"
        manifest = json.loads((source / "frontend-dist.json").read_text())
        require(set(manifest) == {p.relative_to(dist).as_posix() for p in dist.rglob('*') if p.is_file()},
                "Prebuilt frontend inventory differs")
        verify_files(dist, manifest)
        require(json.loads((dist / "__frontend-version.json").read_text())["revision"] == revision,
                "Prebuilt artifact revision mismatch")
    if not activate_only:
        print(json.dumps({"phase":"building","log":str(backup/"build.log")}),flush=True)
        with (backup / "build.log").open("w") as stream:
            result = subprocess.run(["docker","build","--build-arg","NEUROWEAVE_REVISION="+revision,
                "-f","frontend/Dockerfile.prebuilt" if prebuilt else "frontend/Dockerfile","-t",tag,"."],cwd=source,stdout=stream,stderr=subprocess.STDOUT,timeout=1800)
        require(result.returncode == 0,"Build failed; see retained build.log. Running frontend is unchanged.")
    image = command(["docker","image","inspect",tag,"--format","{{.Id}}"])
    label = command(["docker","image","inspect",tag,"--format",'{{index .Config.Labels "org.opencontainers.image.revision"}}'])
    require(label == revision,"Built image revision label differs.")
    if build_only:
        print(json.dumps({"phase":"built","revision":revision,"image":image}),flush=True)
        return
    previous = command(["docker","inspect","--format","{{.Image}}","localflow-frontend-1"])
    manifest = {"revision":revision,"backend_revision":backend_revision,"previous_image":previous,"image":image}
    (backup / "rollback.json").write_text(json.dumps(manifest,indent=2))
    # A frontend-only release may explicitly target the already verified backend.
    # The default still requires matching revisions for full-stack deployments.
    health(backend_revision)
    identity = backend_identity()
    if expected_files:
        verify_files(PRODUCTION, json.loads(expected_files.read_text()))
    print(json.dumps({"phase":"activating","backup":str(backup)}),flush=True)
    try:
        install_source(source, backup)
        switch(image)
        health(backend_revision)
        require(command(["docker","inspect","--format","{{.Image}}","localflow-frontend-1"]) == image,"Wrong running image.")
        require(backend_identity() == identity, "Backend identity changed during frontend release")
    except Exception:
        restore_source(backup)
        switch(previous)
        health()
        raise
    (backup / "verified.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"phase":"verified",**manifest,"backup":str(backup)}),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--backend-revision", help="Reviewed compatible backend commit; defaults to frontend revision")
    parser.add_argument("--prebuilt", action="store_true", help="Use the verified frontend-dist.json artifact")
    parser.add_argument("--expected-files", type=Path, help="Preflight SHA-256 inventory of server files being replaced")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--build-only",action="store_true")
    group.add_argument("--activate-only",action="store_true")
    parser.add_argument("--rollback",type=Path)
    args = parser.parse_args()
    if args.rollback:
        require(args.rollback.parent == BACKUPS and args.rollback.resolve() == args.rollback,"Invalid rollback directory.")
        manifest = json.loads((args.rollback / "rollback.json").read_text())
        restore_source(args.rollback)
        switch(manifest["previous_image"])
        health()
        print("Previous frontend image restored.")
    else:
        require(args.source is not None and args.revision is not None,"--source and --revision required.")
        deploy(args.source,args.revision,args.build_only,args.activate_only,args.backend_revision,args.prebuilt,args.expected_files)


if __name__ == "__main__":
    main()
