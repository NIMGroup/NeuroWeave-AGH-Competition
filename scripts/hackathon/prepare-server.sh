#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/server-env.sh"
umask 077
if [[ "$NW_SERVER_MODE" == execution ]]; then
  [[ -d "$LOCALFLOW_SLURM_WORKER_ROOT/bakend" ]] || { echo 'Missing immutable worker release; use NW_SERVER_MODE=planning to plan without it'; exit 1; }
  [[ -x "$LOCALFLOW_SLURM_PYTHON" ]] || { echo 'Missing scientific Python runtime; use NW_SERVER_MODE=planning to plan without it'; exit 1; }
fi
mkdir -p "$NW_RUNTIME/secrets" "$NW_RUNTIME/logs" "$AGH_HOME/profiles/local-dev" "$LOCALFLOW_OUTPUT_ROOT"
if [[ ! -f "$NW_BRIDGE_TOKEN_FILE" ]]; then
  node -e 'require("node:fs").writeFileSync(process.env.NW_BRIDGE_TOKEN_FILE,require("node:crypto").randomBytes(32).toString("hex"),{flag:"wx",mode:0o600})'
fi
node "$NW_ROOT/scripts/hackathon/secure-home.mjs"
cp "$NW_ROOT/integrations/agh/profile.yaml" "$AGH_HOME/profiles/local-dev/profile.yaml"
node "$NW_ROOT/scripts/hackathon/build-native-sdk.mjs"
(umask 022; cd "$NW_ROOT/frontend"; npm run build:agh; VITE_AGH_SERVER=true npm run build)
bash "$NW_ROOT/scripts/hackathon/install-plugin.sh"
node "$NW_AGH_ENTRY" daemon stop > "$NW_RUNTIME/logs/preparation-stop.log" 2>&1
printf '%s\n' 'Server prepared; maintenance daemon stopped. Start server-backend.sh, server-daemon.sh and server-web.sh. Configure Agnes through official settings.'
