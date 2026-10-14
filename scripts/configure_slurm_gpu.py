#!/usr/bin/env python3
"""Conservative, cluster-specific GPU registration; no daemon restarts by default."""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

CONF = Path('/etc/slurm/slurm.conf')
GRES = Path('/etc/slurm/gres.conf')
BACKUPS = Path('/var/backups/neuroweave-slurm')
NODES = ('node01', 'node02', 'node03')
HOSTS = ('mgr',) + NODES
SCRIPT = '/opt/neuroweave/admin/configure_slurm_gpu.py'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(argv):
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30)
    require(result.returncode == 0,
            '{} failed: {}'.format(shlex.join(argv), result.stderr.strip()))
    return result.stdout.strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def config_fingerprint(data):
    """Ignore blank lines only; preserve every nonempty configuration line."""
    return digest(b'\n'.join(line for line in data.splitlines() if line.strip()))


def fields(line):
    return dict(re.findall(r'(?i)(\w+)\s*=\s*([^\s#]+)', line.split('#', 1)[0]))


def lower_fields(line):
    return {key.lower(): value for key, value in fields(line).items()}


def status_fields(text):
    # scontrol's OS build string contains '#'; it is data, not a config comment.
    return {key.lower(): value for key, value in
            re.findall(r'(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=(\S+)', text)}


def hostlist(value):
    if value == 'node[01-03]':
        return set(NODES)
    result = set(value.split(','))
    require(result <= set(HOSTS), 'Unsupported NodeName expression: ' + value)
    return result


def append_resource(value):
    resources = value.split(',') if value else []
    gpu = [item for item in resources if item.split(':', 1)[0].lower() == 'gpu']
    require(not gpu or gpu == ['gpu:1'], 'Unexpected existing GPU resource: ' + value)
    return value if gpu else ','.join(resources + ['gpu:1'])


def add_field(line, field, value):
    pattern = re.compile(r'(?i)\b' + re.escape(field) + r'\s*=\s*[^\s#]+')
    code, mark, comment = line.partition('#')
    if pattern.search(code):
        return pattern.sub(field + '=' + value, code) + mark + comment
    ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
    return code.rstrip() + ' ' + field + '=' + value + (
        ' #' + comment if mark else ending)


def plan_conf(text):
    lines = text.splitlines(keepends=True)
    seen = set()
    gres_types = []
    global_values = {}
    for index, line in enumerate(lines):
        values = lower_fields(line)
        require(not re.match(r'(?i)^\s*include\b', line),
                'Include files require manual review; no changes made.')
        global_values.update({k: v for k, v in values.items()
                              if k in ('clustername', 'slurmctldhost', 'selecttype')})
        if 'grestypes' in values:
            gres_types.append(index)
            require(len(gres_types) == 1, 'Multiple GresTypes settings require manual review.')
            resources = values['grestypes'].split(',')
            require(len(resources) == len(set(resources)), 'Duplicate GresTypes settings.')
            if 'gpu' not in resources:
                lines[index] = add_field(line, 'GresTypes', ','.join(resources + ['gpu']))
        if 'nodename' not in values:
            require('gres' not in values, 'Unexpected Gres outside a NodeName definition.')
            continue
        if values['nodename'].upper() == 'DEFAULT':
            require('gres' not in values, 'NodeName=DEFAULT Gres requires manual review.')
            continue
        names = hostlist(values['nodename'])
        require(not seen.intersection(names), 'Duplicate NodeName definitions require manual review.')
        seen.update(names)
        if names.intersection(NODES):
            require('mgr' not in names, 'CPU and GPU hosts share a NodeName line; review manually.')
            expected = append_resource(values.get('gres', ''))
            if expected != values.get('gres'):
                lines[index] = add_field(line, 'Gres', expected)
        else:
            require(not any(item.split(':', 1)[0].lower() == 'gpu'
                            for item in values.get('gres', '').split(',')),
                    'mgr unexpectedly has a GPU assignment.')
    require(seen == set(HOSTS), 'Expected exactly mgr and node01, node02, node03.')
    require(global_values.get('clustername') == 'mycluster', 'Unexpected ClusterName.')
    require(global_values.get('slurmctldhost', '').split('(')[0] == 'mgr',
            'Unexpected SlurmctldHost.')
    require(global_values.get('selecttype') == 'select/cons_tres', 'Expected select/cons_tres.')
    if not gres_types:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append('GresTypes=gpu\n')
    return ''.join(lines)


def plan_gres(text):
    active = [lower_fields(line) for line in text.splitlines()
              if line.strip() and not line.lstrip().startswith('#')]
    require(not active or active == [{'name': 'gpu', 'file': '/dev/nvidia0'}],
            'Existing gres.conf differs from the reviewed single-GPU configuration; refusing to overwrite.')
    if active:
        return text
    return text + ('' if not text or text.endswith('\n') else '\n') + 'Name=gpu File=/dev/nvidia0\n'


def check_host(root=True, require_device=True):
    host = socket.gethostname().split('.')[0]
    require(host in HOSTS, 'This script is only for ' + ', '.join(HOSTS))
    if root:
        require(hasattr(os, 'geteuid') and os.geteuid() == 0, 'Run with sudo/root.')
    require(CONF.is_file() and not CONF.is_symlink(), 'Expected regular /etc/slurm/slurm.conf.')
    require(not GRES.is_symlink(), 'Refusing symlinked gres.conf.')
    if host in NODES and require_device:
        device = Path('/dev/nvidia0')
        require(device.exists() and stat.S_ISCHR(device.stat().st_mode),
                'Missing GPU character device /dev/nvidia0.')
    return host


def queue_empty():
    require(not run(['squeue', '--all', '--noheader', '--format=%i']),
            'The cluster has queued/running jobs. No changes are permitted.')


def planned_files(host):
    current = CONF.read_bytes()
    result = {CONF: (current, plan_conf(current.decode('utf-8')).encode('utf-8'))}
    if host in NODES:
        current = GRES.read_bytes() if GRES.exists() else None
        result[GRES] = (current, plan_gres((current or b'').decode('utf-8')).encode('utf-8'))
    elif GRES.exists():
        require(not any(line.strip() and not line.lstrip().startswith('#')
                        for line in GRES.read_text().splitlines()),
                'mgr has an unexpected active gres.conf; review manually.')
    return result


def atomic_write(path, data, mode=0o644, uid=0, gid=0):
    fd, temp = tempfile.mkstemp(prefix='.neuroweave-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def apply_changes(host, changes):
    queue_empty()
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = BACKUPS / (host + '-' + stamp)
    backup.mkdir(parents=True, mode=0o700)
    manifest = {'host': host, 'files': []}
    for path, (before, after) in changes.items():
        require((path.read_bytes() if path.exists() else None) == before,
                'Configuration changed during review; retry.')
        metadata = path.stat() if before is not None else None
        record = {'path': str(path), 'existed': before is not None,
                  'after_sha256': digest(after), 'mode': stat.S_IMODE(metadata.st_mode) if metadata else 0o644,
                  'uid': metadata.st_uid if metadata else 0, 'gid': metadata.st_gid if metadata else 0}
        if before is not None:
            shutil.copy2(path, backup / path.name)
            record['before_sha256'] = digest(before)
        manifest['files'].append(record)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    # Flush all backup content before touching either live file.
    for saved in backup.iterdir():
        with saved.open('rb') as stream:
            os.fsync(stream.fileno())
    for record in manifest['files']:
        path = Path(record['path'])
        atomic_write(path, changes[path][1], record['mode'], record['uid'], record['gid'])
    print('Applied. Backup: ' + str(backup))
    print('Rollback: sudo python3 ' + SCRIPT + ' --rollback ' + str(backup))
    print('No service restarted and no node resumed. Follow deploy/slurm-admin.md.')


def rollback(host, name):
    backup = Path(name).resolve()
    require(backup.parent == BACKUPS.resolve() and backup.name.startswith(host + '-'),
            'Rollback must use a backup from this host under ' + str(BACKUPS))
    manifest = json.loads((backup / 'manifest.json').read_text())
    require(manifest['host'] == host, 'Backup belongs to another host.')
    records = manifest['files']
    for item in records:
        path = Path(item['path'])
        require(path in (CONF, GRES) and not path.is_symlink(), 'Unexpected restore path.')
        current = digest(path.read_bytes()) if path.exists() else None
        allowed = {item['after_sha256'], item.get('before_sha256')}
        require(current in allowed, 'Live file has subsequent edits; refusing rollback: ' + str(path))
        if item['existed']:
            require(digest((backup / path.name).read_bytes()) == item['before_sha256'],
                    'Backup checksum mismatch.')
    queue_empty()
    for item in records:
        path = Path(item['path'])
        if item['existed']:
            atomic_write(path, (backup / path.name).read_bytes(), item['mode'], item['uid'], item['gid'])
        elif path.exists():
            path.unlink()
    print('Configuration restored. Restart/reconfigure the same services before submitting jobs.')


def verify_node():
    host = check_host(root=False)
    require(host in NODES, '--verify-node is only for GPU nodes.')
    require(all(before == after for before, after in planned_files(host).values()),
            'GPU configuration has not been applied.')
    require(run(['systemctl', 'is-active', 'slurmd']) == 'active', 'slurmd is not active.')
    pid = int(run(['systemctl', 'show', 'slurmd', '--property=MainPID', '--value']))
    require(pid > 0, 'slurmd has no MainPID.')
    process = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
    boot = int(re.search(r'^btime (\d+)$', Path('/proc/stat').read_text(), re.M).group(1))
    started = boot + int(process[19]) / os.sysconf('SC_CLK_TCK')
    require(started + 1 >= max(CONF.stat().st_mtime, GRES.stat().st_mtime),
            'Restart slurmd after applying configuration.')
    gpu = run(['nvidia-smi', '--query-gpu=index,name', '--format=csv,noheader'])
    require(len(gpu.splitlines()) == 1 and '5060' in gpu, 'Expected one RTX 5060 GPU.')
    print(json.dumps({'host': host, 'conf_sha256': digest(CONF.read_bytes()),
                      'conf_nonblank_sha256': config_fingerprint(CONF.read_bytes()),
                      'started': started, 'gpu': gpu}))


def verify_registration(node, status, probe, expected):
    require(probe['host'] == node and probe['conf_nonblank_sha256'] == expected,
            node + ': nonempty slurm.conf lines differ from mgr.')
    state = status.get('state', '')
    require(set(re.split(r'[+*~#]', state)) <= {'DOWN', 'IDLE'},
            node + ': unexpected state ' + state + '; inspect slurmd logs first.')
    require(status.get('gres') == 'gpu:1',
            node + ': controller has not registered one GPU.')
    # GRES scheduling works without GPU accounting. This cluster's Slurm 21.08
    # omits gres/gpu from CfgTRES when AccountingStorageTRES does not track it.
    tres = dict(item.split('=', 1) for item in status.get('cfgtres', '').split(',') if '=' in item)
    require('gres/gpu' not in tres or tres['gres/gpu'] == '1',
            node + ': GPU accounting count contradicts registered GRES.')
    registered = status.get('slurmdstarttime', '')
    try:
        registered_time = dt.datetime.fromisoformat(registered).timestamp()
    except ValueError:
        raise RuntimeError(node + ': missing SlurmdStartTime; node is not registered.')
    require(abs(registered_time - probe['started']) < 120,
            node + ': controller registration is stale; check clocks and slurmd logs.')


def finalize(host, ssh_user, *, resume=True):
    require(host == 'mgr', '--finalize is only permitted on mgr.')
    require(re.fullmatch(r'[a-z_][a-z0-9_-]*', ssh_user), 'Invalid SSH user.')
    require(all(before == after for before, after in planned_files(host).values()),
            'Apply mgr configuration first.')
    queue_empty()
    config = run(['scontrol', 'show', 'config'])
    match = re.search(r'^\s*GresTypes\s*=\s*(\S+)', config, re.M)
    require(match and 'gpu' in match.group(1).split(','), 'Controller has not loaded GresTypes=gpu.')
    expected = config_fingerprint(CONF.read_bytes())
    for node in NODES:
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', node,
                   'python3 ' + SCRIPT + ' --verify-node']
        if os.geteuid() == 0 and ssh_user != 'root':
            command = ['runuser', '-u', ssh_user, '--'] + command
        elif os.geteuid() != 0:
            import pwd
            require(pwd.getpwuid(os.geteuid()).pw_name == ssh_user,
                    'Read-only verification must use the current SSH account.')
        probe = json.loads(run(command))
        status = status_fields(run(['scontrol', 'show', 'node', node, '--oneliner']))
        verify_registration(node, status, probe, expected)
        print(node + ': configuration, GPU, daemon restart and registration verified.')
    if not resume:
        print('CHECK PASSED: all finalization checks passed; no node state changed. Run with sudo and --finalize to RESUME.')
        return
    queue_empty()
    run(['scontrol', 'update', 'NodeName=node[01-03]', 'State=RESUME'])
    print(run(['sinfo', '--Node', '--format=%N %t %G']))
    print('RESUME requested. Run the documented Slurm GPU smoke test next.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true', help='Back up and atomically write reviewed GPU settings.')
    action.add_argument('--finalize', action='store_true', help='Verify all GPU nodes, then explicitly RESUME them (mgr only).')
    action.add_argument('--check-finalize', action='store_true', help='Run all finalization checks without sudo or node state changes.')
    action.add_argument('--rollback', metavar='BACKUP_DIRECTORY')
    action.add_argument('--verify-node', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--ssh-user', default='hipeson', help='Existing SSH identity used by mgr finalization (default: hipeson).')
    args = parser.parse_args()
    if args.verify_node:
        verify_node()
        return
    host = check_host(root=not args.check_finalize, require_device=not args.rollback)
    if args.rollback:
        rollback(host, args.rollback)
        return
    if args.finalize or args.check_finalize:
        finalize(host, args.ssh_user, resume=not args.check_finalize)
        return
    queue_empty()
    changes = {path: pair for path, pair in planned_files(host).items() if pair[0] != pair[1]}
    for path, (before, after) in changes.items():
        print(''.join(difflib.unified_diff((before or b'').decode().splitlines(True),
                                         after.decode().splitlines(True),
                                         fromfile=str(path), tofile=str(path) + ' (proposed)')), end='')
    if not changes:
        print('GPU configuration already matches. No changes needed.')
    elif args.apply:
        apply_changes(host, changes)
    else:
        print('DRY RUN: no files or service states changed. Add --apply to write this diff.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
