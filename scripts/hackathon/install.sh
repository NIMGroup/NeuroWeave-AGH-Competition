#!/usr/bin/env bash
# Install pinned service dependencies only; no server/model/scientific execution.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$NW_ROOT"
umask 077
for required in git curl tar xz rg sha256sum; do
  command -v "$required" >/dev/null || { printf 'Missing required command: %s\n' "$required"; exit 1; }
done
mkdir -p "$NW_DEPENDENCIES/tools" "$NW_DEPENDENCIES/logs"
if [[ ! -x "$NW_DEPENDENCIES/tools/node/bin/node" ]]; then
  archive=node-v24.21.0-linux-x64.tar.xz
  curl --fail --location --proto '=https' "https://nodejs.org/dist/v24.21.0/$archive" -o "$NW_DEPENDENCIES/tools/$archive"
  curl --fail --location --proto '=https' https://nodejs.org/dist/v24.21.0/SHASUMS256.txt -o "$NW_DEPENDENCIES/tools/SHASUMS256.txt"
  (cd "$NW_DEPENDENCIES/tools"; rg "  $archive$" SHASUMS256.txt | sha256sum -c -)
  tar -xJf "$NW_DEPENDENCIES/tools/$archive" -C "$NW_DEPENDENCIES/tools"
  mv "$NW_DEPENDENCIES/tools/node-v24.21.0-linux-x64" "$NW_DEPENDENCIES/tools/node"
fi
[[ "$(node --version)" == v24.21.0 ]]
if [[ ! -x "$NW_DEPENDENCIES/tools/pnpm/node_modules/.bin/pnpm" ]]; then
  npm install --prefix "$NW_DEPENDENCIES/tools/pnpm" --no-audit --no-fund pnpm@10.34.5
fi
[[ "$(pnpm --version)" == 10.34.5 ]]
uv_bin="$(command -v uv || true)"
[[ -n "$uv_bin" ]] || { echo 'Install uv 0.11.28 from https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
[[ "$($uv_bin --version)" == 'uv 0.11.28'* ]] || { echo 'This installer requires uv 0.11.28'; exit 1; }
export UV_PYTHON_INSTALL_DIR="$NW_DEPENDENCIES/tools/managed-python"
if [[ ! -x "$NW_DEPENDENCIES/tools/python/bin/python3.11" ]]; then
  "$uv_bin" python install 3.11.15
  python_path="$($uv_bin python find --managed-python 3.11.15)"
  ln -s "$(dirname "$(dirname "$python_path")")" "$NW_DEPENDENCIES/tools/python"
fi
[[ -x "$NW_DEPENDENCIES/api-venv/bin/python" ]] || "$uv_bin" venv --python "$NW_DEPENDENCIES/tools/python/bin/python3.11" "$NW_DEPENDENCIES/api-venv"
"$uv_bin" pip sync --python "$NW_DEPENDENCIES/api-venv/bin/python" bakend/requirements-dev.lock
"$NW_DEPENDENCIES/api-venv/bin/python" -m pip check
if [[ ! -d "$NW_DEPENDENCIES/agnes-harness/.git" ]]; then
  git clone https://github.com/AgnesAI-Labs/agnes-harness.git "$NW_DEPENDENCIES/agnes-harness"
  git -C "$NW_DEPENDENCIES/agnes-harness" checkout --detach dc267c3c78429933e2b3d8c5d544e56bb980a574
fi
[[ "$(git -C "$NW_DEPENDENCIES/agnes-harness" rev-parse HEAD)" == dc267c3c78429933e2b3d8c5d544e56bb980a574 ]]
agh_changes="$(git -C "$NW_DEPENDENCIES/agnes-harness" status --porcelain --untracked-files=no)"
[[ -z "$agh_changes" || "$agh_changes" == ' M packages/web/src/app.ts' ]]
node scripts/hackathon/apply-web-adapter.mjs "$NW_DEPENDENCIES/agnes-harness"
(cd "$NW_DEPENDENCIES/agnes-harness"; pnpm install --frozen-lockfile; pnpm --filter @agnes/cli build:local)
(cd frontend; npm ci)
printf '%s\n' 'Dependencies installed. Configure deployment.env/science-paths.env and an immutable Slurm Worker, then run prepare-server.sh. See deploy/hackathon/README.md.'
