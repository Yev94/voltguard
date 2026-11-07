"""Local read-only utilization sampling, bounded GPU queries and no driver changes."""
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class LoadSample:
    at: float
    cpu: float | None = None
    gpu: float | None = None
    gpu_mhz: float | None = None
    gpu_watts: float | None = None
    cpu_mhz: float | None = None
    source: str = "unavailable"


def number(value, maximum=None):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 and (maximum is None or result <= maximum) else None
    except (ValueError, TypeError):
        return None


class LoadSampler:
    def __init__(self):
        self._primed = False
        self._retry_gpu_at = 0
        self._nvidia = shutil.which("nvidia-smi")
        if not self._nvidia and os.name == "nt":
            candidate = Path(os.environ.get("ProgramW6432", "C:/Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"
            if candidate.is_file():
                self._nvidia = str(candidate)
        self._amd = tuple(Path("/sys/class/drm").glob("card[0-9]*/device/gpu_busy_percent")) if os.name != "nt" else ()

    def _gpu(self, now):
        if now < self._retry_gpu_at:
            return None, None, None, "unavailable"
        try:
            if self._nvidia:
                result = subprocess.run([
                    self._nvidia, "--query-gpu=utilization.gpu,clocks.current.graphics,power.draw",
                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2,
                    check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                readings = []
                for row in result.stdout.strip().splitlines():
                    fields = row.split(",")
                    if len(fields) == 3 and number(fields[0], 100) is not None:
                        readings.append((number(fields[0], 100), number(fields[1]), number(fields[2])))
                if readings:
                    # The busiest GPU drives the policy; clocks/watts refer to that same GPU.
                    return (*max(readings, key=lambda reading: reading[0]), "nvidia")
            if self._amd:
                readings = [number(path.read_text().strip(), 100) for path in self._amd]
                readings = [reading for reading in readings if reading is not None]
                if readings:
                    return max(readings), None, None, "amd_sysfs"
        except (OSError, subprocess.SubprocessError):
            pass
        self._retry_gpu_at = now + 30
        return None, None, None, "unavailable"

    def sample(self):
        cpu = number(psutil.cpu_percent(interval=None), 100)
        if not self._primed:
            cpu = None  # psutil's initial nonblocking value is not a measurement.
            self._primed = True
        gpu, mhz, watts, source = self._gpu(time.monotonic())
        try:
            frequency = psutil.cpu_freq()
            cpu_mhz = number(frequency.current) if frequency else None
        except (OSError, NotImplementedError):
            cpu_mhz = None
        return LoadSample(time.monotonic(), cpu, gpu, mhz, watts, cpu_mhz, source)
