"""Battery readings with a Linux fallback for differently named AC adapters."""
import sys
from pathlib import Path

import psutil

POWER_SUPPLY_PATH = Path("/sys/class/power_supply")
EXTERNAL_TYPES = {
    "Mains", "USB", "USB_DCP", "USB_CDP", "USB_ACA", "USB_C",
    "USB_PD", "USB_PD_DRP", "Wireless",
}


def _linux_power_plugged():
    try:
        supplies = list(POWER_SUPPLY_PATH.iterdir())
    except OSError:
        return None
    seen_offline = False
    unknown = False
    for supply in supplies:
        try:
            supply_type = (supply / "type").read_text().strip()
        except (OSError, UnicodeError):
            unknown = True
            continue
        if supply_type not in EXTERNAL_TYPES:
            continue
        try:
            scope = (supply / "scope").read_text().strip()
        except FileNotFoundError:
            scope = ""  # Older AC adapters do not expose scope.
        except (OSError, UnicodeError):
            unknown = True
            continue
        if scope == "Device":
            continue
        try:
            online = (supply / "online").read_text().strip()
        except (OSError, UnicodeError):
            unknown = True
            continue
        if online == "1":
            return True
        if online == "0":
            seen_offline = True
        else:
            unknown = True
    return False if seen_offline and not unknown else None


def read_battery():
    battery = psutil.sensors_battery()
    if battery is None or battery.power_plugged is not None or sys.platform != "linux":
        return battery
    plugged = _linux_power_plugged()
    if plugged is None:
        return battery
    seconds = battery.secsleft
    if plugged:
        seconds = psutil.POWER_TIME_UNLIMITED
    elif seconds == psutil.POWER_TIME_UNLIMITED:
        seconds = psutil.POWER_TIME_UNKNOWN
    return battery._replace(power_plugged=plugged, secsleft=seconds)
