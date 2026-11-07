import logging
import os
from logging.handlers import RotatingFileHandler
from src.paths import state_dir

APP_DIR = state_dir()
LOG_FILE = APP_DIR / "voltguard.log"


def get_logger(name="voltguard"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Third-party transport diagnostics may contain cloud payloads or device IDs.
    logging.getLogger("meross_iot").setLevel(logging.CRITICAL + 1)
    if not logger.handlers and os.environ.get("VOLTGUARD_DEMO") == "1":
        logger.addHandler(logging.NullHandler())
    if not logger.handlers:
        APP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(handler)
    return logger
