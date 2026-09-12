#!/usr/bin/env bash
# Build PixMatch.app (macOS).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

echo ">> Using: $($PY --version)"
"$PY" -m pip install --quiet -e ".[dev]"
"$PY" -m pip install --quiet imageio-ffmpeg || echo "   (imageio-ffmpeg optional - skipped)"

# Build an .icns from the PNG if possible (needs macOS 'iconutil').
ICON_PNG="packaging/resources/icon.png"
ICON_ICNS="packaging/resources/icon.icns"
if command -v iconutil >/dev/null 2>&1 && [ ! -f "$ICON_ICNS" ]; then
  TMP="$(mktemp -d)/icon.iconset"; mkdir -p "$TMP"
  for s in 16 32 64 128 256 512; do
    sips -z $s $s "$ICON_PNG" --out "$TMP/icon_${s}x${s}.png" >/dev/null
    d=$((s*2)); sips -z $d $d "$ICON_PNG" --out "$TMP/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$TMP" -o "$ICON_ICNS" && echo ">> icon.icns generated"
fi

rm -rf build dist
"$PY" -m PyInstaller packaging/PixMatch.spec --noconfirm

echo
echo ">> Done:  dist/PixMatch.app"
echo "   (unsigned - first run: right-click > Open, or: xattr -dr com.apple.quarantine dist/PixMatch.app)"
