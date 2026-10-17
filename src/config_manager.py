"""Versioned non-secret settings; passwords live only in the OS keyring."""
import json
import os
import tempfile
from pathlib import Path

import keyring

from src.paths import config_dir
from src.logger_config import get_logger

KEYRING_SERVICE = "VoltGuard"
CONFIG_FILE = config_dir() / "config.json"
DEFAULTS = {
    "schema_version": 2, "email": "", "uuid": "", "min_bat": 40, "max_bat": 95,
    "check_time": 60, "start_minimized": False, "auto_monitor": True,
    "start_at_login": True, "confirmed_uuid": "", "approved_uuid": "", "device_name": "", "device_type": "",
}
logger = get_logger()


class ConfigManager:
    def __init__(self, path=None, *, demo=False):
        self.path = Path(path) if path else CONFIG_FILE
        self.config = DEFAULTS.copy()
        self.password = ""
        self.demo = demo
        self.storage_error = ""
        if not demo:
            self.load_config()

    def load_config(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig")) if self.path.exists() else {}
            if not isinstance(data, dict):
                raise ValueError("Invalid settings")
            self.config.update({k: v for k, v in data.items() if k in DEFAULTS})
            # One-time migration to the explicitly chosen 40–95 policy. Keep the old file intact
            # until the user saves; never migrate an old plug directly to active control.
            if data.get("schema_version", 1) < 2:
                self.config.update(min_bat=40, max_bat=95, start_minimized=False,
                                   auto_monitor=True, confirmed_uuid="", schema_version=2)
            if self.config.get("email"):
                self.password = keyring.get_password(KEYRING_SERVICE, self.config["email"]) or ""
        except Exception as error:
            self.storage_error = type(error).__name__
            logger.warning("Settings/keyring unavailable (%s). Open the panel to configure.", self.storage_error)

    def import_legacy(self, path):
        """Explicit local migration. Never prints or copies passwords into settings."""
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        self.config.update({k: data[k] for k in ("email", "uuid", "check_time") if k in data})
        self.config.update(min_bat=40, max_bat=95, start_minimized=False, confirmed_uuid="")
        self.password = data.get("password", "") or self.password
        return self.raw_save()

    def raw_save(self):
        if self.demo:
            return True
        tmp_path = None
        try:
            # Fail closed for missing/insecure backends. Do not fall back to plaintext.
            from keyring.backend import KeyringBackend
            backend = keyring.get_keyring()
            choices = getattr(backend, "backends", [backend])
            secure = next((candidate for candidate in choices
                           if isinstance(candidate, KeyringBackend) and candidate.priority > 0
                           and not type(candidate).__module__.startswith(("keyrings.alt", "keyring.backends.null"))), None)
            if secure is None:
                raise RuntimeError("Secure keyring unavailable")
            if secure is not backend:
                keyring.set_keyring(secure)
            if self.config["email"] and self.password:
                keyring.set_password(KEYRING_SERVICE, self.config["email"], self.password)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            safe = {k: self.config[k] for k in DEFAULTS}
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".config-", delete=False) as stream:
                tmp_path = Path(stream.name)
                json.dump(safe, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.path)
            self.storage_error = ""
            return True
        except Exception as error:
            self.storage_error = type(error).__name__
            logger.error("Could not save settings to secure storage (%s).", self.storage_error)
            return False
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()

    def is_confirmed(self):
        return bool(self.config.get("uuid")) and self.config.get("confirmed_uuid") == self.config["uuid"]
