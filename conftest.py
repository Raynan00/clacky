# Repo-root conftest.
#
# 1) Its mere presence puts the repo root on sys.path (pytest prepends the
#    rootdir conftest's directory), so `import clacky` / `import organizer`
#    resolve when running pytest from anywhere in the project.
#
# 2) It relocates pytest's temp dirs out of %TEMP% (…\AppData\Local\Temp\…).
#    Clacky' safety guard refuses to operate anywhere under "appdata" — that is
#    *correct* behavior and stays. But pytest's default `tmp_path` lives under
#    AppData on Windows, so the organize tests would be blocked by the very
#    guard they exist to exercise. We point pytest's basetemp at a dir under the
#    user's home instead — a valid, guard-passing location — so the guard is
#    tested for real rather than worked around.

from pathlib import Path


def pytest_configure(config):
    if not config.option.basetemp:
        config.option.basetemp = str(Path.home() / ".clacky-pytest-tmp")
