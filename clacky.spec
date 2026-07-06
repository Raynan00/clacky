# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the Clacky voice companion (.exe).
# Build on Windows:   pip install pyinstaller   then   pyinstaller clacky.spec
# Output: dist/Clacky/Clacky.exe  (folder build). See docs/BUILDING.md.
#
# The vendored shell imports by BARE name (`from config import`, `from ai import`),
# so we put clacky/shell on the path and bundle those as TOP-LEVEL modules; the
# frozen launcher (clacky/companion.py) then does `import main`.

import os
import sys

sys.path.insert(0, os.path.abspath("clacky/shell"))  # so bare shell imports resolve

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Heavy/awkward third-party packages — grab data, DLLs, and submodules.
for pkg in (
    "PyQt6", "sounddevice", "soundfile", "uiautomation", "comtypes",
    "edge_tts", "faster_whisper", "av", "mss", "anthropic",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as e:
        print(f"[clacky.spec] collect_all({pkg}) skipped: {e}")

# The shell's own bare-name top-level modules + packages.
hiddenimports += [
    "config", "main", "companion_manager", "hotkey",
    "memory_store", "google_workspace",
    "keyboard", "pynput", "dotenv",
]
for pkg in ("ai", "audio", "ui", "screen", "skills", "tutor_features"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception as e:
        print(f"[clacky.spec] collect_submodules({pkg}) skipped: {e}")
hiddenimports += collect_submodules("clacky")

# Runtime data the shell reads by path. Top-level modules see __file__'s parent as
# the bundle root, so SOUL.md goes to ".", and the asset/skill dirs alongside.
datas += [
    ("clacky/shell/SOUL.md", "."),
    ("clacky/shell/skills",  "skills"),
    ("clacky/shell/assets",  "assets"),
]

a = Analysis(
    ["packaging/clacky_app.py"],
    pathex=[".", "clacky/shell"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Heavy libraries from the build machine's site-packages that some transitive
    # hook drags in but Clacky never imports (verified: ~1.8 GB of dead weight —
    # tensorflow alone was 1.1 GB). Without these the zip fits GitHub's 2 GiB
    # release limit with room to spare.
    excludes=[
        "tkinter",
        "tensorflow", "tensorboard", "keras",
        "torch", "torchvision", "torchaudio",
        "jax", "jaxlib",
        "transformers", "sklearn", "scikit-learn",
        "pandas", "matplotlib", "scipy",
        "botocore", "boto3", "grpc",
        "llvmlite", "numba", "imageio_ffmpeg",
        "yt_dlp",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Clacky",
    console=False,          # windowed release build (no console window)
    icon="clacky/shell/assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,              # UPX often trips antivirus; leave off
    name="Clacky",
)
