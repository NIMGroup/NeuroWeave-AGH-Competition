#!/usr/bin/env python3
"""Cluster-specific SlurmDBD accounting setup for mgr and node01-03.

--check is read-only. --prepare-database runs only on mgr as root; it installs
the SAME Slurm package version, initializes a dedicated MariaDB database and
starts slurmdbd. --apply-config backs up only this host's slurm.conf. Service
restarts are deliberately performed by the interactive cluster wrapper.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile

HOSTS = ("mgr","node01","node02","node03")
CONF = Path("/etc/slurm/slurm.conf")
DBD = Path("/etc/slurm/slurmdbd.conf")
BACKUPS = Path("/var/backups/neuroweave-accounting")
OWNERSHIP = Path("/var/lib/neuroweave/accounting-install.json")
DATABASE = "neuroweave_slurm_acct"
DB_USER = "nw_slurm"
SETTINGS = {
    "AccountingStorageType":"accounting_storage/slurmdbd",
    "AccountingStorageHost":"mgr",
    "AccountingStoragePort":"6819",
    "JobAcctGatherType":"jobacct_gather/linux",
    "JobAcctGatherFrequency":"30",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(argv, *, data=None, timeout=60):
    result = subprocess.run(argv,input=data,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    # SQL and service stderr can contain credentials. Never print captured output on error.
    require(result.returncode == 0, "Command failed: " + argv[0] + " (exit " + str(result.returncode) + ")")
    return result.stdout.strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan_conf(text):
    lines, seen = [], set()
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*=\s*([^#]*)(.*)$",line)
        key = next((key for key in SETTINGS if match and key.lower() == match[1].lower()),None)
        if key:
            require(key not in seen,"Duplicate setting: " + key)
            seen.add(key)
            line = key + "=" + SETTINGS[key] + (" " + match[3] if match[3] else "")
        lines.append(line)
    for key,value in SETTINGS.items():
        if key not in seen:
            lines.append(key + "=" + value)
    return "\n".join(lines) + "\n"


def cluster_name(text):
    values = re.findall(r"(?mi)^\s*ClusterName\s*=\s*([A-Za-z0-9_-]+)\s*(?:#.*)?$",text)
    require(len(values) == 1, "Expected a single simple ClusterName.")
    return values[0]


def idle():
    require(not run(["squeue","--all","--noheader","--format=%i"]),"Cluster has active/pending jobs; pause submissions and wait.")


def atomic(path,data,mode=0o600,owner=None):
    require(not path.is_symlink() and path.parent.resolve() == path.parent,"Configuration path is redirected.")
    fd,temporary = tempfile.mkstemp(prefix=".nw-accounting-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(),mode)
            if owner:
                os.fchown(stream.fileno(),owner.pw_uid,owner.pw_gid)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_database():
    import pwd
    require(socket.gethostname().split(".")[0] == "mgr","Prepare the accounting database on mgr only.")
    idle()
    require(not DBD.is_symlink() and not OWNERSHIP.is_symlink(),"Accounting paths are redirected.")
    managed = OWNERSHIP.is_file()
    if managed:
        require(json.loads(OWNERSHIP.read_text()).get("database") == DATABASE,"Accounting ownership record differs.")
        require(DBD.is_file() and DBD.read_text().startswith("# Managed by NeuroWeave accounting"),"Managed configuration missing.")
    else:
        require(not DBD.exists(),"An existing slurmdbd.conf needs administrator review.")
        # This installer does not alter an unrelated MariaDB installation.
        existing = subprocess.run(["systemctl","is-active","--quiet","mariadb"]).returncode == 0
        require(not existing,"MariaDB is already active; review dedicated database setup manually.")
    version = run(["dpkg-query","-W","-f=${Version}","slurmctld"])
    require(re.fullmatch(r"[0-9A-Za-z.+:~_-]+",version),"Unsupported package version.")
    run(["apt-get","update"],timeout=900)
    run(["apt-get","install","-y","--no-upgrade","mariadb-server","slurmdbd="+version],timeout=1200)
    require(run(["dpkg-query","-W","-f=${Version}","slurmdbd"]) == version,"SlurmDBD and controller package versions differ.")
    run(["systemctl","enable","--now","mariadb"])
    owner = pwd.getpwnam("slurm")
    OWNERSHIP.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if managed:
        password = re.findall(r"(?m)^StoragePass=([0-9a-f]{64})$",DBD.read_text())
        require(len(password) == 1,"Unsupported managed credential format.")
        password = password[0]
    else:
        present = run(["mariadb","--protocol=socket","--batch","--skip-column-names"],data=
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='"+DATABASE+"';\n"
            "SELECT User FROM mysql.user WHERE User='"+DB_USER+"';\n")
        require(not present,"Dedicated database/user already exists without an ownership record.")
        password = secrets.token_hex(32)
        config = ("# Managed by NeuroWeave accounting; credentials must stay on this host.\n"
            "AuthType=auth/munge\nDbdHost=mgr\nDbdPort=6819\nSlurmUser=slurm\n"
            "DebugLevel=info\nLogFile=/var/log/slurmdbd.log\nPidFile=/run/slurmdbd.pid\n"
            "StorageType=accounting_storage/mysql\nStorageHost=localhost\nStoragePort=3306\n"
            "StorageLoc="+DATABASE+"\nStorageUser="+DB_USER+"\nStoragePass="+password+"\n")
        atomic(DBD,config.encode(),owner=owner)
        atomic(OWNERSHIP,json.dumps({"database":DATABASE,"format":1,"slurm_version":version}).encode())
    sql = ("CREATE DATABASE IF NOT EXISTS "+DATABASE+";\n"
        "CREATE USER IF NOT EXISTS '"+DB_USER+"'@'localhost' IDENTIFIED BY '"+password+"';\n"
        "GRANT ALL PRIVILEGES ON "+DATABASE+".* TO '"+DB_USER+"'@'localhost';\n")
    run(["mariadb","--protocol=socket"],data=sql)
    log = Path("/var/log/slurmdbd.log")
    require(not log.is_symlink(),"SlurmDBD log path is redirected.")
    log.touch(exist_ok=True)
    os.chown(log,owner.pw_uid,owner.pw_gid)
    os.chmod(log,0o640)
    run(["systemctl","enable","--now","slurmdbd"])
    run(["systemctl","is-active","--quiet","slurmdbd"])
    print("Dedicated accounting database and slurmdbd are ready; credentials were not printed.")


def apply_config():
    idle()
    before = CONF.read_bytes()
    after = plan_conf(before.decode()).encode()
    if before == after:
        print("Accounting settings already match.")
        return
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    require(BACKUPS.resolve() == BACKUPS,"Backup directory is redirected.")
    stamp = socket.gethostname().split(".")[0] + "-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = BACKUPS / stamp
    backup.mkdir(mode=0o700)
    atomic(backup / "slurm.conf",before)
    info = CONF.stat()
    atomic(backup / "manifest.json",json.dumps({"before":sha(before),"after":sha(after),
        "mode":info.st_mode & 0o777,"uid":info.st_uid,"gid":info.st_gid}).encode())
    atomic(CONF,after,info.st_mode & 0o777)
    os.chown(CONF,info.st_uid,info.st_gid)
    print("Configuration applied; no daemon was restarted. Backup: " + str(backup))


def rollback(backup):
    require(backup.is_absolute() and backup.parent == BACKUPS and backup.resolve() == backup,"Invalid rollback directory.")
    idle()
    item = json.loads((backup / "manifest.json").read_text())
    before = (backup / "slurm.conf").read_bytes()
    require(sha(before) == item["before"] and sha(CONF.read_bytes()) == item["after"],"Rollback checksum mismatch.")
    atomic(CONF,before,item["mode"])
    os.chown(CONF,item["uid"],item["gid"])
    print("Previous slurm.conf restored. Reconfigure/restart daemons after restoring all hosts. Database retained.")


def register_cluster():
    require(socket.gethostname().split(".")[0] == "mgr","Register on mgr only.")
    cluster = cluster_name(CONF.read_text())
    require(plan_conf(CONF.read_text()) == CONF.read_text(),"Controller accounting configuration differs.")
    run(["systemctl","is-active","--quiet","slurmdbd"])
    present = run(["sacctmgr","--noheader","--parsable2","show","cluster","format=Cluster"])
    if cluster not in present.splitlines():
        run(["sacctmgr","--immediate","add","cluster",cluster])
    require(cluster in run(["sacctmgr","--noheader","--parsable2","show","cluster","format=Cluster"]).splitlines(),
            "Cluster registration failed.")
    print("Cluster is registered in persistent accounting: " + cluster)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    for flag in ("check","prepare-database","apply-config","register-cluster"):
        action.add_argument("--"+flag,action="store_true")
    action.add_argument("--rollback",type=Path)
    args = parser.parse_args()
    require(socket.gethostname().split(".")[0] in HOSTS,"Unexpected host.")
    require(CONF.is_file() and not CONF.is_symlink(),"Expected /etc/slurm/slurm.conf.")
    if args.check:
        before = CONF.read_text()
        print("".join(difflib.unified_diff(before.splitlines(True),plan_conf(before).splitlines(True),
            fromfile="slurm.conf",tofile="slurm.conf (accounting proposed)")))
        print("Cluster: "+cluster_name(before)+". No changes applied.")
        return
    require(os.geteuid() == 0,"Run the requested mutation with sudo in a terminal.")
    if args.prepare_database:
        prepare_database()
    elif args.apply_config:
        apply_config()
    elif args.register_cluster:
        register_cluster()
    else:
        rollback(args.rollback)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERROR: " + str(error))
        raise SystemExit(1)
