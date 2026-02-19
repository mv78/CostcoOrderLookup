"""
logger.py — Centralised logging setup for Costco Order Lookup.

Two handlers:
  - RotatingFileHandler  → costco_lookup.log  (always DEBUG level)
  - StreamHandler        → stderr             (INFO by default, DEBUG with --debug)

Call setup_logging() once at program start (in main.py).
All other modules use logging.getLogger(__name__).
"""

import logging
import logging.handlers

from costco_lookup.paths import BASE_DIR

LOG_FILE = BASE_DIR / "costco_lookup.log"
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(debug: bool = False) -> None:
    """
    Configure root logger.  Safe to call multiple times (no-op after first call).

    Parameters
    ----------
    debug : bool
        If True, set console handler to DEBUG level (very verbose).
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # --- File handler: DEBUG+, rotating, 5 MB × 3 files ---
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # --- Console handler: INFO+ (or DEBUG with --debug) ---
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # Silence noisy third-party loggers
    for noisy in ("urllib3", "requests", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
