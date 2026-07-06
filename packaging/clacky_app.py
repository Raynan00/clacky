"""
PyInstaller entry point for the Clacky .exe.

Double-clicking the built Clacky.exe runs this, which launches the voice
companion (equivalent to `clacky run`). Keys are read from the user config dir
(%LOCALAPPDATA%\\Clacky\\.env) when frozen — see clacky/shell/config.py — so
users just add their keys, no code checkout needed.
"""

import sys

from clacky.companion import launch

if __name__ == "__main__":
    raise SystemExit(launch())
