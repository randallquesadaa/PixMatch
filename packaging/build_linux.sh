#!/usr/bin/env bash
# Build the Linux executable.  Run from anywhere; paths are resolved here.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

echo ">> Using: $($PY --version)"
"$PY" -m pip install --quiet --upgrade pyinstaller
"$PY" -m pip install --quiet -r requirements.txt
"$PY" -m pip install --quiet imageio-ffmpeg || echo "   (imageio-ffmpeg optional - skipped)"

rm -rf build dist
"$PY" -m PyInstaller packaging/PixMatch.spec --noconfirm

echo
echo ">> Done:  dist/PixMatch/PixMatch"
"$ROOT/dist/PixMatch/PixMatch" --version 2>/dev/null || true
