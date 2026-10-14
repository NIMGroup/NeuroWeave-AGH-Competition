#!/usr/bin/env bash
# Run interactively as hipeson on mgr. This wrapper DOES apply configuration.
set -eu
set -o pipefail

admin_script=/opt/neuroweave/admin/configure_slurm_gpu.py

if [ "$(hostname -s)" != mgr ] || [ "$(id -un)" != hipeson ]; then
  printf '%s\n' 'ERROR: Run this script as hipeson in an interactive terminal on mgr.' >&2
  exit 1
fi
if [ ! -t 0 ] || [ ! -t 1 ]; then
  printf '%s\n' 'ERROR: An interactive terminal is required for sudo password prompts.' >&2
  exit 1
fi
if [ ! -r "$admin_script" ]; then
  printf 'ERROR: Missing administrator script: %s\n' "$admin_script" >&2
  exit 1
fi

require_empty_queue() {
  local jobs
  jobs=$(squeue --all --noheader --format=%i)
  if [ -n "$jobs" ]; then
    printf '%s\n' 'ERROR: The cluster has queued/running jobs; stopping without further changes.' >&2
    return 1
  fi
}

printf '%s\n' \
  'This command will apply GPU configuration on mgr and node01-03,' \
  'restart slurmctld on mgr and slurmd on all four hosts,' \
  'then verify GPU nodes and explicitly resume them.' \
  'Any failed command stops the sequence. Keep other job submissions paused.' \
  'Enter sudo passwords directly at the terminal when prompted.'

require_empty_queue
sudo python3 "$admin_script" --apply
for node in node01 node02 node03; do
  ssh -t -o BatchMode=yes -o ConnectTimeout=10 "$node" \
    sudo python3 "$admin_script" --apply
done

require_empty_queue
sudo systemctl restart slurmctld
sudo systemctl is-active --quiet slurmctld

require_empty_queue
sudo systemctl restart slurmd
sudo systemctl is-active --quiet slurmd
for node in node01 node02 node03; do
  require_empty_queue
  ssh -t -o BatchMode=yes -o ConnectTimeout=10 "$node" \
    'sudo systemctl restart slurmd && systemctl is-active --quiet slurmd'
done

printf '%s\n' 'Waiting 5 seconds for node registration before final verification...'
sleep 5
require_empty_queue
sudo python3 "$admin_script" --finalize
printf '%s\n' 'GPU node configuration and explicit RESUME completed. Slurm CUDA smoke tests are next.'
