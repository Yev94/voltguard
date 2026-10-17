"""User-owned locations shared by source and packaged builds."""
import os
import sys
from pathlib import Path


def config_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "VoltGuard"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voltguard"


def state_dir():
    if sys.platform == "win32":
        return config_dir()
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "voltguard"


def resource_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
