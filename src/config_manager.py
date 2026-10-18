"""Versioned non-secret settings; passwords live only in the OS keyring."""
import json
import os
import tempfile
from pathlib import Path

from src.credential_store import CredentialStore, CredentialUnavailable

from src.paths import config_dir
from src.logger_config import get_logger

KEYRING_SERVICE = "VoltGuard"
CONFIG_FILE = config_dir() / "config.json"
DEFAULTS = {
    "schema_version": 2, "email": "", "uuid": "", "min_bat": 40, "max_bat": 95,
    "check_time": 60, "start_minimized": False, "auto_monitor": True,
    "start_at_login": True, "confirmed_uuid": "", "approved_uuid": "", "device_name": "", "device_type": "", "credential_backend": "",
}
logger = get_logger()


class ConfigManager:
    def __init__(self, path=None, *, demo=False, credential_store=None):
        self.path = Path(path) if path else CONFIG_FILE
        self.config = DEFAULTS.copy()
        self._password = ""
        self._password_changed = False
        self.credential_state = "empty"
        self._credential_store = credential_store
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
            self.reload_password()
        except Exception as error:
            self.storage_error = type(error).__name__
            logger.warning("Settings/keyring unavailable (%s). Open the panel to configure.", self.storage_error)

    @property
    def password(self):
        return self._password

    @password.setter
    def password(self, value):
        self._password_changed = (self._password_changed or value != self._password) if value else False
        self._password = value

    def reload_password(self):
        """Retry a delayed wallet without reloading settings or overwriting a user's edit."""
        if self.demo or not self.config.get("email"):
            return False
        if self._password_changed or self._password:
            return bool(self._password)
        try:
            if self._credential_store is None:
                self._credential_store = CredentialStore(self.config.get("credential_backend", ""))
            value = self._credential_store.read(self.config["email"])
            self.credential_state = "ready" if value else "missing"
            if value:
                self._password = value
                self.config["credential_backend"] = self._credential_store.backend_id
            return bool(value)
        except CredentialUnavailable as error:
            self.credential_state = error.state
            return False

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
            # Si solo se guardan ajustes -> conservar la credencial y su almacén sin reescribirla.
            if self._password_changed:
                if self._credential_store is None:
                    self._credential_store = CredentialStore(self.config.get("credential_backend", ""))
                self._credential_store.write(self.config["email"], self._password)
                self.config["credential_backend"] = self._credential_store.backend_id
                self._password_changed = False
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
