#!/usr/bin/env bash
# Shared, non-secret dependency paths. Server state is set by server-env.sh.
NW_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NW_DEPENDENCIES="$NW_ROOT/runtime/hackathon"
export PATH="$NW_DEPENDENCIES/tools/node/bin:$NW_DEPENDENCIES/tools/pnpm/node_modules/.bin:$NW_DEPENDENCIES/agnes-harness/node_modules/.bin:$PATH"
export COREPACK_HOME="$NW_DEPENDENCIES/tools/corepack"
export NW_AGH_ENTRY="$NW_DEPENDENCIES/agnes-harness/packages/cli/dist/local/agnes.mjs"
export NODE_TLS_REJECT_UNAUTHORIZED=1
export PYTHONPATH="$NW_ROOT/bakend"
export UV_CACHE_DIR="$NW_DEPENDENCIES/tools/uv-cache"
