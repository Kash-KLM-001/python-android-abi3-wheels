#!/bin/bash
# Publish the Rust abi3 wheels to this repo's own release (not the cv2 repo).
# Retries forever; verifies the release via GitHub's sha256 digest.
set -u
REPO=Kash-KLM-001/python-android-abi3-wheels
TAG=v0.1.0
DIR=$(cd "$(dirname "$0")" && pwd)/wheels
SUMS=$(cd "$(dirname "$0")" && pwd)/SHA256SUMS
NAMES=("$DIR"/*.whl)
NAMES=("${NAMES[@]##*/}")
NAMES+=("SHA256SUMS")

say() { echo "[$(date '+%F %T')] $*"; }
path_for() { if [ "$1" = "SHA256SUMS" ]; then echo "$SUMS"; else echo "$DIR/$1"; fi; }

for a in $(seq 1 100); do
  if timeout 90 gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    say "release exists"; break
  fi
  say "create release attempt $a"
  timeout 300 gh release create "$TAG" --repo "$REPO" \
    --title "Android arm64 abi3 wheels (Rust/PyO3)" \
    --notes "bcrypt, safetensors, watchfiles (cp38-abi3) and tokenizers (cp39-abi3) for Android arm64-v8a, API 24+. Each wheel includes libpython3.13 (Pydroid) and libpython3.14 (Termux) variants." \
    && break
  sleep 15
done

while true; do
  ok=1
  for name in "${NAMES[@]}"; do
    p=$(path_for "$name")
    want="sha256:$(sha256sum "$p" | cut -d' ' -f1) $(stat -c%s "$p")"
    got=$(timeout 90 gh release view "$TAG" --repo "$REPO" --json assets \
      --jq ".assets[] | select(.name == \"$name\") | .digest + \" \" + (.size|tostring)" 2>/dev/null)
    if [ "$got" = "$want" ]; then say "OK $name"; continue; fi
    ok=0
    if [ -z "$got" ]; then mode=""; else mode="--clobber"; fi
    say "upload $name ${mode:-plain}"
    timeout 1200 gh release upload "$TAG" "$p" --repo "$REPO" $mode || true
  done
  [ "$ok" = 1 ] && { say "ALL ASSETS VERIFIED"; break; }
  sleep 15
done
