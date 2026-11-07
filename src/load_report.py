"""Read the bounded local sensor history; no credentials, hardware or network access."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics

from src.paths import state_dir


def summarize(path=None, hours=24):
    path = Path(path) if path else state_dir() / "load-telemetry.jsonl"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = []
    skipped = 0
    for file in [Path(str(path) + f".{index}") for index in (3, 2, 1)] + [path]:
        if not file.exists():
            continue
        with file.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    timestamp = datetime.fromisoformat(row["time"])
                    if timestamp >= cutoff:
                        events.append(row)
                except (ValueError, KeyError, TypeError):
                    skipped += 1  # May include a partially written last line; never alter it.
    samples = [row for row in events if row.get("event") == "sample"]
    commands = Counter(row.get("action") for row in events if row.get("event") == "command_confirmed")
    lines = [f"Last {hours:g}h · {len(samples)} local samples · confirmed ON {commands['on']} / OFF {commands['off']}"]
    if not samples:
        lines.append("No samples yet. Enable load assist and start monitoring.")
    for ac, label in ((False, "Battery"), (True, "AC")):
        group = [row for row in samples if row.get("ac") is ac]
        parts = []
        for field, name in (("cpu_pct", "CPU %"), ("gpu_pct", "GPU %"), ("gpu_mhz", "GPU MHz"), ("gpu_watts", "GPU W")):
            values = [row[field] for row in group if type(row.get(field)) in (float, int)]
            if values:
                parts.append(f"{name}: median {statistics.median(values):.1f}, max {max(values):.1f}")
        lines.append(f"{label} · {len(group)} samples · " + (" | ".join(parts) or "no readings"))
    missing = sum(row.get("cpu_pct") is None or row.get("gpu_pct") is None for row in samples)
    lines.append(f"Missing CPU/GPU readings: {missing}. Stutter marks: {sum(row.get('event') == 'stutter_mark' for row in events)}.")
    for row in [row for row in events if row.get("event") == "stutter_mark"][-3:]:
        lines.append(f"Stutter {row['time']} · AC {row.get('ac')} · CPU {row.get('cpu_pct')}% · GPU {row.get('gpu_pct')}% · {row.get('gpu_mhz')} MHz / {row.get('gpu_watts')} W")
    if skipped:
        lines.append(f"Incomplete/invalid history lines skipped: {skipped}.")
    lines.append("Utilization and clocks help compare episodes; this does not measure frame times or prove the cause of stutter.")
    return "\n\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path)
    parser.add_argument("--hours", type=float, default=24)
    args = parser.parse_args()
    if not 0 < args.hours <= 720:
        parser.error("hours must be in (0, 720]")
    print(summarize(args.path, args.hours))
