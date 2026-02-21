"""
paths.py — Single source of truth for runtime file locations.

Two distinct path concepts:

BASE_DIR — mutable runtime files (config.json, .token_cache.json, logs, invoices/)
    - Frozen (.exe):  Path(sys.executable).parent  →  folder with the .exe
    - Script mode:    Path(__file__).parent.parent  →  project root

TEMPLATE_DIR — read-only bundled assets (Jinja2 templates)
    - Frozen (.exe):  sys._MEIPASS / "costco_lookup" / "templates"
                      PyInstaller extracts datas[] here; NOT the .exe folder
    - Script mode:    Path(__file__).parent / "templates"  →  costco_lookup/templates/

Never use BASE_DIR for templates in frozen mode — PyInstaller extracts bundled
data to a temp sys._MEIPASS directory, not alongside the .exe.
"""

import sys
from pathlib import Path


def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller onefile: sys.executable is the .exe itself
        return Path(sys.executable).parent
    # Normal Python: __file__ is costco_lookup/paths.py → go up two levels
    return Path(__file__).parent.parent


def _resolve_template_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller extracts bundled datas[] to sys._MEIPASS at runtime
        return Path(sys._MEIPASS) / "costco_lookup" / "templates"
    # Normal Python: templates live in the same package directory as this file
    return Path(__file__).parent / "templates"


BASE_DIR: Path = _resolve_base_dir()
TEMPLATE_DIR: Path = _resolve_template_dir()
