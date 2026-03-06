"""
BCK Manager - Logging Module
Centralized logging for all operations.
"""

import logging
import os
import sys
from datetime import datetime


def setup_logger(log_file="/var/log/bck_manager.log", debug=False):
    """
    Configure and return the application logger.
    Logs to both file and stdout.
    When *debug* is True the console handler is lowered to DEBUG level,
    showing verbose output including full SMTP session details.
    """
    logger = logging.getLogger("bck_manager")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    # Formatter
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # --- File handler ---
    try:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except PermissionError:
        # Fallback to local log if we can't write to /var/log
        fallback_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "bck_manager.log"
        )
        fh = logging.FileHandler(fallback_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        print(
            f"[WARNING] Cannot write to {log_file}, logging to {fallback_path}"
        )

    # --- Console handler (stdout) ---
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    logger.info("=" * 60)
    logger.info("BCK Manager - Session started")
    logger.info(f"Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    return logger
