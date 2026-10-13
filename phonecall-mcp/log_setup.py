"""Centralized logging for phonecall-mcp.

All modules should use:
    from log_setup import logger
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Create the shared logger
logger = logging.getLogger("phonecall-mcp")
logger.setLevel(logging.DEBUG)

# File handler — rotating, max 5 MB, 3 backups
_file_handler = RotatingFileHandler(
    LOG_DIR / "phonecall-mcp.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logger.addHandler(_file_handler)

# Stderr handler (for when terminal is open)
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_stderr_handler)
