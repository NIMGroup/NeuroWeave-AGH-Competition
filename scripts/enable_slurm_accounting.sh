#!/usr/bin/env bash
# Interactive operator step. Run as hipeson on mgr; enter passwords only in sudo.
set -euo pipefail
script=/opt/neuroweave/admin/configure_slurm_accounting.py
if [[ $(hostname -s) != mgr || $(id -un) != hipeson || ! -t 0 ]]; then
  printf '%s\n' 'Run interactively as hipeson on mgr.' >&2
  exit 1
fi
idle() {
  local jobs
  jobs=$(squeue --all --noheader --format=%i)
  if [[ -n "$jobs" ]]; then
    printf '%s\n' 'Active/pending jobs found; stopping. Keep all submissions paused.' >&2
    exit 1
  fi
}
printf '%s\n' 'Install MariaDB and the matching SlurmDBD on mgr; enable persistent job accounting.' \
  'Update accounting settings on mgr/node01-03; restart Slurm daemons after checking the queue.' \
  'GPU registration, cgroup settings and scheduling limits are preserved. Keep submissions paused.'
idle
sudo python3 "$script" --prepare-database
sudo python3 "$script" --apply-config
for node in node01 node02 node03; do
  ssh -t -o BatchMode=yes -o ConnectTimeout=10 "$node" sudo python3 "$script" --apply-config
done
sudo python3 "$script" --register-cluster
# Compare nonempty lines, as existing Slurm configs can differ in blank lines.
expected=$(sed '/^[[:space:]]*$/d' /etc/slurm/slurm.conf | sha256sum | cut -d' ' -f1)
for node in node01 node02 node03; do
  actual=$(ssh -o BatchMode=yes "$node" "sed '/^[[:space:]]*$/d' /etc/slurm/slurm.conf | sha256sum" | cut -d' ' -f1)
  [[ "$actual" == "$expected" ]] || { printf 'Configuration differs: %s\n' "$node" >&2; exit 1; }
done
idle
sudo systemctl restart slurmctld
sudo systemctl is-active --quiet slurmctld
idle
sudo systemctl restart slurmd
for node in node01 node02 node03; do
  idle
  ssh -t -o BatchMode=yes "$node" 'sudo systemctl restart slurmd && systemctl is-active --quiet slurmd'
done
scontrol ping
sacctmgr --noheader --parsable2 show cluster format=Cluster
sinfo -N -o '%N %T %G'
printf '%s\n' 'Accounting daemons are configured. Verify new smoke jobs through sacct before considering this complete.'
