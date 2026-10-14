#!/usr/bin/env bash
# Run as the environment maintainer after an administrator creates /opt/neuroweave/envs.
set -euo pipefail
umask 022
root=/opt/neuroweave/envs
conda=/opt/miniconda3/bin/conda
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
phase=${1:-scientific}
case "$phase" in
  scientific) name=scientific-py311 ;;
  torch) name=torch-cu128-py311 ;;
  *) echo 'Usage: install_shared_python.sh scientific|torch' >&2; exit 2 ;;
esac
test -d "$root" && test -w "$root" || { echo "Administrator must create writable $root" >&2; exit 1; }
prefix="$root/$name"
test ! -e "$prefix" || { echo "Refusing to modify existing environment: $prefix" >&2; exit 1; }
export PYTHONNOUSERSITE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export MPLBACKEND=Agg
"$conda" create --yes --override-channels --channel conda-forge --prefix "$prefix" python=3.11 pip
python="$prefix/bin/python"
"$python" -m pip install --index-url https://pypi.org/simple --upgrade pip
if [[ "$phase" == torch ]]; then
  "$python" -m pip install --index-url https://download.pytorch.org/whl/cu128 'torch==2.11.0' 'torchvision==0.26.0'
  "$python" -m pip freeze | awk '/^(torch|torchvision)==/' > "$prefix/torch-constraints.txt"
  "$python" -m pip install --index-url https://pypi.org/simple --constraint "$prefix/torch-constraints.txt" --requirement "$project/deploy/python-environments/torch.in"
else
  "$python" -m pip install --index-url https://pypi.org/simple --requirement "$project/deploy/python-environments/scientific.in"
fi
"$python" -m pip check
"$python" -m pip freeze > "$prefix/requirements.lock.txt"
"$conda" list --prefix "$prefix" --explicit > "$prefix/conda-explicit.txt"
# Only this newly created prefix is changed. Other users may read/run, never modify it.
chmod -R a+rX,go-w -- "$prefix"
echo "Installed $prefix"
