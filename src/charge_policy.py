"""No network, UI or OS side effects: decide only from known battery readings."""
import math


def desired_action(battery, minimum=40, maximum=95):
    if battery is None or not (1 <= minimum < maximum <= 100):
        return None
    percent = battery.percent
    if not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 0 <= percent <= 100:
        return None
    if battery.power_plugged is False and percent <= minimum:
        return "on"
    if battery.power_plugged is True and percent >= maximum:
        return "off"
    return None
