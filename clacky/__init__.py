"""Clacky — the open-source Windows agent companion with a safety/undo layer.

This package is the CLI core. The agent runtime wraps the Claude Agent SDK;
the file operations, journal, and permission logic are pure and SDK-free so
they can be tested headless.
"""

__version__ = "0.1.0"
