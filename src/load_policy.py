"""Stateful load override. Pure decisions; never reads sensors or switches hardware."""
import math
from dataclasses import dataclass

SAMPLE_SECONDS = 5
LOAD_DEFAULTS = {
    "load_enabled": False,
    "load_cpu_on": 75, "load_cpu_off": 35,
    "load_gpu_on": 50, "load_gpu_off": 20,
    "load_on_seconds": 15, "load_off_seconds": 180, "load_min_on_seconds": 300,
}


def validate_load_settings(config):
    settings = {**LOAD_DEFAULTS, **{k: config[k] for k in LOAD_DEFAULTS if k in config}}
    if type(settings["load_enabled"]) is not bool:
        raise ValueError("Load assist must be enabled or disabled.")
    for unit in ("cpu", "gpu"):
        low, high = settings[f"load_{unit}_off"], settings[f"load_{unit}_on"]
        if type(low) is not int or type(high) is not int or not 0 <= low < high <= 100:
            raise ValueError(f"{unit.upper()}: idle threshold must be below the high threshold (0–100%).")
    for key, minimum in (("load_on_seconds", 5), ("load_off_seconds", 30), ("load_min_on_seconds", 60)):
        value = settings[key]
        if type(value) is not int or not minimum <= value <= 3600:
            raise ValueError(f"{key}: choose {minimum}–3600 seconds.")
    return settings


def known_percent(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


@dataclass(frozen=True)
class Decision:
    action: str | None
    reason: str


class PowerPolicy:
    def __init__(self):
        self.active = False
        self.high_since = self.low_since = self.active_since = self.last_sample = None
        self.release_pending = False
        self.powered_since = None
        self.battery_cycle = False
        self.initialized = False
        self.phase = "disabled"

    def confirm_on(self, now):
        if self.active:
            self.powered_since = now

    def decide(self, battery, sample, config, now):
        from src.charge_policy import desired_action

        settings = validate_load_settings(config)
        normal = desired_action(battery, config["min_bat"], config["max_bat"])
        valid_battery = (battery is not None and known_percent(battery.percent)
                         and type(battery.power_plugged) is bool)
        if not valid_battery:
            self.high_since = self.low_since = None
            self.phase = "waiting_battery"
            return Decision(None, "battery_unavailable")
        if not settings["load_enabled"]:
            self.phase = "disabled"
            return Decision(normal, "battery")

        # A charge cycle begun at the battery minimum must finish even if load subsides.
        if not self.initialized:
            self.battery_cycle = battery.power_plugged and battery.percent < config["max_bat"]
            self.initialized = True
        if battery.percent <= config["min_bat"]:
            self.battery_cycle = True
        elif battery.percent >= config["max_bat"]:
            self.battery_cycle = False

        fresh = sample is not None and 0 <= now - sample.at <= 2 * SAMPLE_SECONDS
        cpu = sample.cpu if fresh and known_percent(sample.cpu) else None
        gpu = sample.gpu if fresh and known_percent(sample.gpu) else None
        high = ((cpu is not None and cpu >= settings["load_cpu_on"])
                or (gpu is not None and gpu >= settings["load_gpu_on"]))
        low = (cpu is not None and gpu is not None and cpu < settings["load_cpu_off"]
               and gpu < settings["load_gpu_off"])
        if self.last_sample is not None and now - self.last_sample > 2 * SAMPLE_SECONDS:
            self.high_since = self.low_since = None
        self.last_sample = now
        if high:
            self.high_since = now if self.high_since is None else self.high_since
        else:
            self.high_since = None
        if low:
            self.low_since = now if self.low_since is None else self.low_since
        else:
            self.low_since = None
        if not self.active and self.high_since is not None and now - self.high_since >= settings["load_on_seconds"]:
            self.active = True
            self.release_pending = False
            self.active_since = now
            self.powered_since = now if battery.power_plugged else None
        if self.active and battery.power_plugged and self.powered_since is None:
            self.powered_since = now
        if (self.active and self.low_since is not None
                and now - self.low_since >= settings["load_off_seconds"]
                and self.powered_since is not None
                and now - self.powered_since >= settings["load_min_on_seconds"]):
            self.active = False
            self.release_pending = True

        self.phase = ("idle_pending" if self.active and low else "active" if self.active
                      else "high_pending" if high else "waiting_sensors" if cpu is None or gpu is None
                      else "idle")
        if self.active:
            return Decision("on" if not battery.power_plugged else None, "load_active")
        # Do not disconnect an already powered GPU while a high-load timer qualifies.
        if high and battery.power_plugged:
            return Decision(None, "load_qualifying")
        if self.release_pending:
            if self.battery_cycle:
                self.release_pending = False
            elif not low or now - self.low_since < settings["load_off_seconds"]:
                return Decision(None, "load_release_wait")
            elif battery.power_plugged:
                return Decision("off", "load_released")
            else:
                self.release_pending = False
        if self.battery_cycle and not battery.power_plugged:
            return Decision("on", "battery_cycle")
        return Decision(normal, "battery")
