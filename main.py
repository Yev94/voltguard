import argparse
import os
import sys
from filelock import FileLock, Timeout
from src.paths import config_dir


def run():
    parser = argparse.ArgumentParser(description="VoltGuard: shared Windows/Linux charge controller")
    parser.add_argument("--autostart", action="store_true", help="Login startup with visible panel")
    parser.add_argument("--install-autostart", action="store_true", help="Register this installation at login")
    parser.add_argument("--import-legacy", metavar="CONFIG", help="Migrate a local config into secure OS storage")
    parser.add_argument("--demo", action="store_true", help="Innocuous preview: no account, writes or relay control")
    parser.add_argument("--smoke-test", action="store_true", help="Render demo panel and exit automatically")
    args = parser.parse_args()
    if args.install_autostart:
        from src.autostart import configure_autostart
        configure_autostart(True)
        return 0
    if args.import_legacy:
        from src.config_manager import ConfigManager
        return 0 if ConfigManager().import_legacy(args.import_legacy) else 1
    demo = args.demo or args.smoke_test
    if demo:
        os.environ["VOLTGUARD_DEMO"] = "1"
    folder = config_dir()
    lock = None
    if not demo:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = FileLock(str(folder / "voltguard.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            print("VoltGuard is already running. Open its existing panel from the tray.")
            return 0
    try:
        import customtkinter as ctk
        from src.ui_app import VoltGuardApp
        app_root = ctk.CTk()
        app = VoltGuardApp(app_root, demo=demo, login_start=args.autostart)
        if args.smoke_test:
            app_root.after(1500, app.quit_window)
        app_root.mainloop()
        return 0
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    sys.exit(run())
