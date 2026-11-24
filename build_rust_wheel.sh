#!/bin/bash
# Build Android abi3 wheels for the Rust/PyO3 packages in this repo.
# Usage: bash build_rust_wheel.sh bcrypt [safetensors watchfiles tokenizers] | --all
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SDISTS=/tmp/opencode/rust/sdists
OUT=$HERE/wheels
PY=/tmp/opencode/rust_repo/make_rust_wheel.py

fetch_sdist() {  # name version
  local n="$1-$2" f="$1" url
  mkdir -p "$SDISTS"
  if [ -s "$SDISTS/$n.tar.gz" ]; then return; fi
  url=$(python3 - "$1" "$2" <<'EOF'
import json, sys, urllib.request
p, v = sys.argv[1:]
d = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{p}/{v}/json", timeout=60))
print([x for x in d["urls"] if x["packagetype"] == "sdist"][0]["url"])
EOF
)
  for a in $(seq 1 10); do
    [ -s "$SDISTS/$n.tar.gz" ] && break
    echo "fetch $n attempt $a"
    curl -sSL --retry 5 --retry-delay 5 --max-time 900 -o "$SDISTS/$n.tar.gz" "$url" || true
  done
}

build_one() {
  extra=""
  case "$1" in
    bcrypt)      ver=5.0.0;   abi=cp38; feats="pyo3/abi3-py38" ;;
    safetensors) ver=0.8.0;   abi=cp38; feats="pyo3/extension-module" ;;
    watchfiles)  ver=1.3.0;   abi=cp38; feats="" ;;
    tokenizers)  ver=0.23.3;  abi=cp39; feats=""; extra="--abi-min py39" ;;
    orjson)      ver=3.13.0;  abi=cp314; feats=""; extra="--no-limited-api --python-versions 3.14" ;;
    *) echo "unknown package: $1" >&2; return 1 ;;
  esac
  fetch_sdist "$1" "$ver"
  python3 "$PY" --sdist "$SDISTS/$1-$ver.tar.gz" --version "$ver" \
    --abi "$abi" --features "$feats" --out "$OUT" $extra
}

if [ "${1:-}" = "--all" ]; then
  set -- bcrypt safetensors watchfiles tokenizers
fi
for p in "$@"; do build_one "$p"; done
