# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PixMatch.

Build from the project root:

    pyinstaller packaging/PixMatch.spec --noconfirm

Produces:
  * Windows : dist/PixMatch/PixMatch.exe  (one-folder)
  * macOS   : dist/PixMatch.app
  * Linux   : dist/PixMatch/PixMatch

One-folder (not one-file) is the default: faster start, and the FFmpeg binary
from imageio-ffmpeg lives beside the executable where it is easy to inspect or
replace.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
ICON_PNG = str(ROOT / "packaging" / "resources" / "icon.png")
ICON_ICO = str(ROOT / "packaging" / "resources" / "icon.ico")
ICON_ICNS = str(ROOT / "packaging" / "resources" / "icon.icns")

datas = [
    (str(ROOT / "packaging" / "resources" / "icon.png"), "resources"),
    (str(ROOT / "app" / "resources" / "country_borders.json"), "app/resources"),
]
binaries = []
hiddenimports = ["app"]

# --- optional packages: bundle them if installed, ignore otherwise ---
for pkg in ("imageio_ffmpeg", "pillow_heif", "pillow_avif"):
    try:
        __import__(pkg)
    except Exception:
        continue
    datas += collect_data_files(pkg)
    binaries += collect_dynamic_libs(pkg)
    hiddenimports.append(pkg)

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "numpy", "scipy", "matplotlib", "PySide6.QtWebEngineCore"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PixMatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=(ICON_ICO if sys.platform == "win32"
          else ICON_ICNS if sys.platform == "darwin"
          else ICON_PNG),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PixMatch",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PixMatch.app",
        icon=ICON_ICNS if Path(ICON_ICNS).exists() else None,
        bundle_identifier="io.github.randallquesadaa.pixmatch",
        info_plist={
            "CFBundleName": "PixMatch",
            "CFBundleDisplayName": "PixMatch",
            "CFBundleShortVersionString": "0.7.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
