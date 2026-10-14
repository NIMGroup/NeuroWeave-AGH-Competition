#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/server-env.sh"
umask 077
mkdir -p "$AGH_HOME" "$NW_RUNTIME/logs"
cd "$NW_ROOT"
node "$NW_ROOT/scripts/hackathon/secure-home.mjs"
test -f "$NW_AGH_ENTRY" || { echo 'AGH build is not ready; run scripts/hackathon/install.sh first.'; exit 2; }
node "$NW_AGH_ENTRY" sessions --json > "$NW_RUNTIME/logs/config-startup.json"
tsx "$NW_ROOT/scripts/hackathon/configure-agnes.mjs"
