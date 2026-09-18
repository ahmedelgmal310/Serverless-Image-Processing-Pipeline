#!/usr/bin/env bash
#
# Builds the Pillow Lambda layer (Linux/macOS equivalent of build-layer.ps1).
#
# Pillow ships compiled C extensions, so the flags below force pip to fetch the
# Linux x86_64 wheel no matter which machine runs this script. No Docker needed.
#
# Usage:  ./scripts/build-layer.sh

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="$root/layers/pillow/python"
reqs="$root/scripts/pillow-requirements.txt"

echo "Building Pillow layer -> $target"

rm -rf "$target"
mkdir -p "$target"

python3 -m pip install \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target "$target" \
  --requirement "$reqs"

# Metadata directories add megabytes to the layer for no runtime benefit.
find "$target" -maxdepth 1 -name '*.dist-info' -type d -exec rm -rf {} +

echo "Layer ready: $(du -sh "$target" | cut -f1)"
