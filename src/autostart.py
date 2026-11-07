"""Native per-user login startup; never starts a second background service."""
import os
import subprocess
import sys
from pathlib import Path


def launch_command():
    # Preserve a virtualenv launcher symlink; resolving it loses its dependencies at login.
    executable = Path(sys.executable).absolute()
    if getattr(sys, "frozen", False):
        return [str(executable)]
    if sys.platform == "win32" and executable.name.lower() == "python.exe":
        windowed = executable.with_name("pythonw.exe")
        if windowed.exists():
            executable = windowed
    return [str(executable), str(Path(__file__).resolve().parent.parent / "main.py")]


def desktop_entry(command, *, enabled=True, startup=False):
    def quote(value):
        value = value.replace("%", "%%")
        for char in ("\\", '"', "`", "$"):
            value = value.replace(char, "\\" + char)
        return '"' + value + '"'
    args = list(command) + (["--autostart"] if startup else [])
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=VoltGuard",
        "Comment=Automatic laptop charge control", "Exec=" + " ".join(map(quote, args)),
        "Terminal=false", "Categories=Utility;", f"Hidden={str(not enabled).lower()}",
        "X-GNOME-Autostart-enabled=" + str(enabled).lower(), "",
    ])


def configure_autostart(enabled):
    command = launch_command() + ["--autostart"]
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, "VoltGuard", 0, winreg.REG_SZ,
                                 subprocess.list2cmdline(command))
            else:
                try:
                    winreg.DeleteValue(key, "VoltGuard")
                except FileNotFoundError:
                    pass
    elif sys.platform.startswith("linux"):
        target = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart/voltguard.desktop"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desktop_entry(launch_command(), enabled=enabled, startup=True), encoding="utf-8")
    else:
        raise RuntimeError("Automatic login startup supports Windows and Linux.")
