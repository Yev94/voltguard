"""Format telemetry for display only; charge policy always uses the original reading."""
import math


def battery_percentage(percent):
    if not isinstance(percent, (int, float)) or not math.isfinite(percent):
        return "—"
    return f"{percent:.0f} %"
