#!/usr/bin/env bash
# Dedicated server state and immutable Worker. No model secrets in this file.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
NW_RUNTIME="${NW_SERVER_STATE:-$NW_ROOT/runtime/server}"
if [[ -f "$NW_RUNTIME/deployment.env" ]]; then source "$NW_RUNTIME/deployment.env"; fi
export NW_HACKATHON_RUNTIME="$NW_RUNTIME"
export AGH_HOME="$NW_RUNTIME/agh-home"
export AGNES_PROFILE=local-dev
export NEUROWEAVE_HACKATHON=true
export NEUROWEAVE_AGH_SERVER=true
export NW_BRIDGE_TOKEN_FILE="$NW_RUNTIME/secrets/bridge-token"
export LOCALFLOW_DATABASE_PATH="$NW_RUNTIME/state.sqlite"
export LOCALFLOW_DATA_ROOTS="${NW_SERVER_DATA_ROOTS:-共享数据::/home/data;服务器目录::/}"
export LOCALFLOW_RUN_INPUT_ROOTS="${NW_SERVER_RUN_INPUT_ROOTS:-/}"
export WORKSPACE_DEFAULT_ROOT="${NW_SERVER_OUTPUT_ROOT:-/home/data/neuroweave/competition/agh-agnes}"
export WORKSPACE_ALLOWED_ROOTS="$WORKSPACE_DEFAULT_ROOT"
export LOCALFLOW_OUTPUT_ROOT="$WORKSPACE_DEFAULT_ROOT"
export LOCALFLOW_SLURM_OUTPUT_ROOT="$WORKSPACE_DEFAULT_ROOT"
export LOCALFLOW_SLURM_WORKER_ROOT="${NW_SERVER_WORKER_ROOT:?Set the immutable competition worker release}"
export LOCALFLOW_SLURM_PYTHON="${NW_SERVER_SCIENTIFIC_PYTHON:-/opt/neuroweave/envs/scientific-py311/bin/python}"
export LOCALFLOW_SKILL_PYTHON="$LOCALFLOW_SLURM_PYTHON"
export LOCALFLOW_SLURM_ENABLED=true
export LOCALFLOW_RUN_WORKERS=1
export NW_BRIDGE_PORT="${NW_BRIDGE_PORT:-8127}"
export NW_SERVER_WEB_PORT="${NW_SERVER_WEB_PORT:-4187}"
export NW_SERVER_WS_PORT="${NW_SERVER_WS_PORT:-46173}"
export AGNES_WEB_ORIGIN="${NW_SERVER_WEB_ORIGIN:-http://127.0.0.1:$NW_SERVER_WEB_PORT}"
export NEUROWEAVE_RELEASE_REVISION="${NEUROWEAVE_RELEASE_REVISION:-unversioned}"
if [[ -f "$NW_RUNTIME/science-paths.env" ]]; then source "$NW_RUNTIME/science-paths.env"; fi
