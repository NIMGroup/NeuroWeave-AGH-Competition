#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/server-env.sh"
cd "$NW_ROOT"
exec "$NW_ROOT/runtime/hackathon/api-venv/bin/python" -m uvicorn app.hackathon_app:build_app --factory --host 127.0.0.1 --port "${NW_BRIDGE_PORT:-8127}" --workers 1 --no-proxy-headers
