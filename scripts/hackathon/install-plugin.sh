#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/server-env.sh"
cd "$NW_ROOT"
umask 077
mkdir -p "$NW_RUNTIME/logs" "$AGH_HOME/profiles/local-dev"
node scripts/hackathon/secure-home.mjs
trap 'node "$NW_AGH_ENTRY" daemon stop > "$NW_RUNTIME/logs/plugin-failure-stop.log" 2>&1 || true' ERR
# AGH rejects updates to a package referenced by the boot profile. Enter a
# restricted maintenance profile, perform its reviewed update, then restore.
if [[ -f "$AGH_HOME/data/daemon/owner.json" ]]; then
  node "$NW_AGH_ENTRY" daemon stop > "$NW_RUNTIME/logs/plugin-maintenance-stop.log" 2>&1
fi
cat > "$AGH_HOME/profiles/local-dev/profile.yaml" <<'EOF'
name: local-dev
computerUse: { enabled: false }
approvals: { mode: manual }
policy:
  capabilityCeiling: [tools, hooks, services, slots, ui, resources]
  workspacePackages: deny
EOF
node "$NW_AGH_ENTRY" sessions --json > "$NW_RUNTIME/logs/plugin-maintenance-start.json"
tsx "$NW_ROOT/scripts/hackathon/install-plugin.mjs"
node "$NW_AGH_ENTRY" daemon stop > "$NW_RUNTIME/logs/plugin-maintenance-stop.log" 2>&1
cp "$NW_ROOT/integrations/agh/profile.yaml" "$AGH_HOME/profiles/local-dev/profile.yaml"
node "$NW_AGH_ENTRY" sessions --json > "$NW_RUNTIME/logs/plugin-ready-sessions.json"
