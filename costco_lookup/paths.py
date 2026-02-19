"""
paths.py — Single source of truth for runtime file locations.

Problem:
    PyInstaller onefile bundles extract to a temp dir (sys._MEIPASS).
    Path(__file__).parent.parent resolves to that temp dir, not the .exe's
    real directory, so config.json / .token_cache.json / costco_lookup.log
    would be created in a folder that is wiped on each run.

Solution:
    BASE_DIR points to the directory that CONTAINS the running program:
      - Frozen (.exe):  Path(sys.executable).parent  →  folder with the .exe
      - Script mode:    Path(__file__).parent.parent  →  project root
"""

import sys
from pathlib import Path


def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller onefile: sys.executable is the .exe itself
        return Path(sys.executable).parent
    # Normal Python: __file__ is costco_lookup/paths.py → go up two levels
    return Path(__file__).parent.parent


BASE_DIR: Path = _resolve_base_dir()
