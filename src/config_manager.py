import os
import json
import keyring
from dotenv import load_dotenv
from src.logger_config import APP_DIR, get_logger

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
KEYRING_SERVICE = "MerossBatteryMonitor"
logger = get_logger()

class ConfigManager:
    def __init__(self):
        self.config = {
            "email": "",
            "min_bat": 20,
            "max_bat": 95,
            "check_time": 60,
            "uuid": "",
            "start_minimized": False
        }
        self.password = ""
        self.load_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
            except Exception as e:
                logger.error(f"Error cargando config.json: {e}")
        
        email = self.config.get("email")
        if email:
            try:
                stored_pass = keyring.get_password(KEYRING_SERVICE, email)
                if stored_pass:
                    self.password = stored_pass
            except Exception as e:
                logger.error(f"Error consultando Keyring: {e}")

        # Fallback env
        if not self.password or not self.config.get("email"):
            load_dotenv()
            if os.getenv("MEROSS_EMAIL"): self.config["email"] = os.getenv("MEROSS_EMAIL")
            if os.getenv("MEROSS_PASSWORD"): self.password = os.getenv("MEROSS_PASSWORD")

    def raw_save(self):
        old_email = None
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    old_email = json.load(f).get("email")
            except: pass

        try:
            safe_config = self.config.copy()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(safe_config, f, indent=4)
                
            if old_email and old_email != self.config["email"]:
                try: keyring.delete_password(KEYRING_SERVICE, old_email)
                except: pass

            if self.config["email"] and self.password:
                keyring.set_password(KEYRING_SERVICE, self.config["email"], self.password)
                
            return True
        except Exception as e:
            logger.error(f"Fallo al guardar config: {e}")
            return False
