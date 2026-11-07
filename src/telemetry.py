"""Bounded local JSONL. Only numeric sensors and fixed controller state are recorded."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler

from src.paths import state_dir
from src.load_sensors import number


class TelemetryRecorder:
    def __init__(self, path=None):
        from pathlib import Path
        path = Path(path) if path else state_dir() / "load-telemetry.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8", delay=True)
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    def record(self, event, sample, battery, phase, reason, action=None):
        payload = {
            "time": datetime.now(timezone.utc).isoformat(), "event": event,
            "cpu_pct": sample.cpu if sample else None, "gpu_pct": sample.gpu if sample else None,
            "gpu_mhz": sample.gpu_mhz if sample else None, "gpu_watts": sample.gpu_watts if sample else None,
            "cpu_mhz": sample.cpu_mhz if sample else None, "gpu_source": sample.source if sample else "unavailable",
            "battery_pct": number(battery.percent, 100) if battery else None,
            "ac": battery.power_plugged if battery and type(battery.power_plugged) is bool else None,
            "phase": phase, "reason": reason, "action": action,
        }
        message = json.dumps(payload, allow_nan=False, separators=(",", ":"))
        self.handler.handle(logging.LogRecord("voltguard.telemetry", logging.INFO, "", 0, message, (), None))

    def close(self):
        self.handler.close()
