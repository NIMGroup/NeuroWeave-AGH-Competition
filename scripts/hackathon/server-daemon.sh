#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/server-env.sh"
cd "$NW_ROOT"
exec node "$NW_ROOT/runtime/hackathon/agnes-harness/packages/cli/dist/local/daemon.mjs" --home "$AGH_HOME" --profile "$AGNES_PROFILE" --workspace "$NW_ROOT" --local-web-addr "127.0.0.1:${NW_SERVER_WS_PORT:-46173}" --local-web-origin "$AGNES_WEB_ORIGIN"
