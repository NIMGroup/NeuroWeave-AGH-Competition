#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/server-env.sh"
cd "$NW_ROOT"
for attempt in $(seq 1 120); do
  [[ -f "$AGH_HOME/data/daemon/owner.json" ]] && break
  sleep 0.25
done
[[ -f "$AGH_HOME/data/daemon/owner.json" ]] || { echo 'Start the dedicated AGH daemon before Web'; exit 1; }
exec node "$NW_AGH_ENTRY" serve --port "${NW_SERVER_WEB_PORT:-4187}"
