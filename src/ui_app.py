import asyncio
import os
import threading
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageTk
from tkinter import messagebox
import queue
import sys
from src.autostart import configure_autostart
from src.paths import resource_dir
import customtkinter as ctk
import pystray

from src.logger_config import get_logger
from src.config_manager import ConfigManager
from src.battery_backend import BatteryBackend
from src.load_policy import LOAD_DEFAULTS, validate_load_settings
from src.load_report import summarize
from src import __version__

logger = get_logger()

GREEN        = "#2DB87A"
GREEN_HOVER  = "#249960"
GREEN_OFF    = "#1A3A2A"
RED          = "#E0404A"
RED_HOVER    = "#C0323B"
RED_OFF      = "#3A1A1A"
TEAL         = "#17B8C8"
TEAL_HOVER   = "#109AAA"
TEAL_OFF     = "#0F2E30"
YELLOW       = "#EAB308"
YELLOW_HOVER = "#CA9A06"
YELLOW_OFF   = "#713F12"
BLUE         = "#3B82F6"
BLUE_HOVER   = "#2563EB"
BLUE_OFF     = "#1E3A8A"
GRAY_BTN     = "#5A5A5A"
GRAY_HOVER   = "#444444"
GRAY_OFF     = "#2A2A2A"
BG_CARD      = "#141414"
BG_INNER     = "#1C1C1C"
INPUT_BG     = "#222222"
INPUT_BORDER = "#333333"
INPUT_FOCUS  = "#444444"
CHK_COLOR    = "#2DB87A"
CHK_HOVER    = "#249960"

class VoltGuardApp:
    def __init__(self, root, *, demo=False, login_start=False):
        self.root = root
        self.demo = demo
        self.ui_queue = queue.Queue()
        self.closing = False
        self._auto_start_requested = False
        self._credential_retry_delay = 2000
        self.root.after(50, self._drain_ui_queue)
        self.root.title(f"VoltGuard · {__version__}")
        self.root.geometry(f"500x{min(960, max(500, self.root.winfo_screenheight() - 120))}")
        self.root.resizable(False, True)

        base_dir = resource_dir()
        
        self._ico_path = os.path.join(base_dir, "logo.ico")
        self._logo_path = os.path.join(base_dir, "logo.png")
        if sys.platform == "win32" and os.path.exists(self._ico_path):
            self.root.iconbitmap(self._ico_path)
        elif os.path.exists(self._logo_path):
            self._tk_icon = ImageTk.PhotoImage(Image.open(self._logo_path))
            self.root.iconphoto(True, self._tk_icon)

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        self.cfg_manager = ConfigManager(demo=demo)
        self.backend = BatteryBackend(self.cfg_manager, self.log, self.set_status, self.set_metrics)
        
        self.testing_plug = False
        self.thread = None
        self.tray_icon = None
        self.tray_starting = False
        
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        if demo:
            self.set_status("Preview · 40–95% · no hardware control", "#17B8C8")
            for widget in (self.btn_start, self.btn_on, self.btn_off, self.btn_test,
                           self.btn_save, self.chk_login, self.chk_auto, self.chk_minimized):
                widget.configure(state="disabled")
            self.log("Preview only. No account is connected and no plug will be switched.")
        else:
            if self.cfg_manager.storage_error:
                self.log("Secure settings are unavailable; configure the panel before starting.", is_error=True)
            if self.cfg_manager.config.get("start_minimized") and not login_start:
                self.root.after(300, self.hide_window)
            if self.cfg_manager.config.get("auto_monitor"):
                self._auto_start_requested = True
                self.root.after(800, self._auto_start)

    def post_ui(self, callback, *args):
        self.ui_queue.put((callback, args))

    def _drain_ui_queue(self):
        if self.closing:
            return
        try:
            while True:
                callback, args = self.ui_queue.get_nowait()
                callback(*args)
                if self.closing:
                    return
        except queue.Empty:
            pass
        self.root.after(50, self._drain_ui_queue)

    def _auto_start(self):
        if self.closing or not self._auto_start_requested or self.backend.running:
            return
        if not self.cfg_manager.config.get("auto_monitor"):
            self._auto_start_requested = False
            return
        if not self.cfg_manager.is_confirmed():
            self.set_status("Verify the charging plug to enable monitoring", "orange")
            return
        if not self.cfg_manager.password:
            # Do not replace a password being edited or start a login behind the user's input.
            if self.entry_password.get() not in ("", "********"):
                self.set_status("Password edited; save and start when ready", "orange")
                return
            if not self.cfg_manager.reload_password():
                state = self.cfg_manager.credential_state
                if state == "missing":
                    self.set_status("No saved password in the configured system wallet", "orange")
                    return
                if state not in {"wallet_not_ready", "wallet_locked", "wallet_unavailable"}:
                    self.set_status("System wallet access unavailable; saved credentials are preserved", "orange")
                    return
                self.set_status("Waiting for the system wallet; saved credentials are preserved", "orange")
                self.root.after(self._credential_retry_delay, self._auto_start)
                self._credential_retry_delay = min(self._credential_retry_delay * 2, 30000)
                return
            self.entry_password.delete(0, "end")
            self.entry_password.insert(0, "********")
        self._auto_start_requested = False
        self.start_monitor()

    def save_settings(self):
        valid, error = self.validate_inputs(test_mode=True)
        if not valid:
            self.log(error, is_error=True)
            return False
        if not self.cfg_manager.raw_save():
            self.log("Could not save: unlock or configure the system credential wallet.", is_error=True)
            return False
        try:
            configure_autostart(bool(self.chk_login.get()))
        except Exception as error:
            self.log(f"Could not update login startup ({type(error).__name__}).", is_error=True)
            return False
        self.log("Settings saved. Login startup opens this panel visibly.")
        return True

    # ================= LOGS & STATUS UI =================
    def log(self, msg, is_error=False, is_spam=False):
        if not is_spam:
            if is_error:
                logger.error(msg)
            else:
                logger.info(msg)
        self.post_ui(self._update_log_area, msg)

    def _update_log_area(self, msg):
        hora = datetime.now().strftime("%H:%M:%S")
        self.log_area.configure(state="normal")
        self.log_area.insert("end", f"[{hora}] {msg}\n")
        self.log_area.see("end")
        self.log_area.configure(state="disabled")

    def set_status(self, text, color="white"):
        self.post_ui(lambda: self.status_label.configure(text=text, text_color=color))

    def set_metrics(self, text):
        self.post_ui(lambda: self.metrics_label.configure(text=text))

    # ================= PYSTRAY (WINDOW MGMT) =================
    def _crear_icono_tray(self):
        if os.path.exists(self._logo_path):
            return Image.open(self._logo_path).resize((64, 64))
        image = Image.new('RGB', (64, 64), color=(30, 30, 46))
        d = ImageDraw.Draw(image)
        d.text((10, 20), "VG", fill=(79, 142, 247))
        return image

    def _run_tray(self):
        try:
            menu = (
                pystray.MenuItem('Open', self.show_window, default=True),
                pystray.MenuItem('Exit', self.quit_window)
            )
            self.tray_icon = pystray.Icon("VoltGuard", self._crear_icono_tray(), "VoltGuard", menu)
            self.tray_icon.run()
        except Exception as error:
            self.log(f"Tray unavailable ({type(error).__name__}); keeping the panel visible.", is_error=True)
            self.post_ui(self.root.deiconify)
        finally:
            self.tray_starting = False

    def hide_window(self, icon=None, item=None):
        if self.demo:
            self.quit_window()
            return
        if not pystray.Icon.HAS_MENU:
            self.log("Tray menu unavailable; keeping the panel visible.")
            return
        self.root.withdraw()
        if not self.tray_icon and not self.tray_starting:
            self.tray_starting = True
            threading.Thread(target=self._run_tray, daemon=True).start()

    def show_window(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.post_ui(self.root.deiconify)

    def quit_window(self, icon=None, item=None):
        self.backend.running = False
        self.testing_plug = False
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.post_ui(self._destroy_window)

    def _destroy_window(self):
        self.closing = True
        self.root.destroy()

    # ================= UI & INPUT VALIDATION =================
    def validate_inputs(self, test_mode=False):
        try:
            current_saved_email = self.cfg_manager.config.get("email", "").strip()
            new_email = self.entry_email.get().strip()
            pwd = self.entry_password.get()

            if not new_email:
                raise ValueError("Email required.")

            if new_email != current_saved_email and pwd == "********":
                raise ValueError("If you change the email, you must re-enter the password.")

            if new_email != current_saved_email:
                self.cfg_manager.password = ""

            if not pwd and not self.cfg_manager.password:
                self.cfg_manager.reload_password()
                if not self.cfg_manager.password:
                    raise ValueError("System wallet unavailable or no saved password; credentials have not been changed.")

            if pwd and pwd != "********":
                self.cfg_manager.password = pwd

            min_bat = int(self.entry_min.get())
            max_bat = int(self.entry_max.get())
            check_time = int(self.entry_time.get())
            uuid = self.entry_uuid.get().strip()

            if not (1 <= min_bat <= 99): raise ValueError("Minimum battery between 1 and 99.")
            if not (1 <= max_bat <= 100): raise ValueError("Maximum battery between 1 and 100.")
            if min_bat >= max_bat: raise ValueError("Minimum must be less than maximum.")
            if check_time < 5: raise ValueError("Check interval >= 5 seconds.")
            if not test_mode and not uuid: raise ValueError("UUID required.")
            load_settings = validate_load_settings({
                "load_enabled": bool(self.chk_load.get()),
                **{key: int(entry.get()) for key, entry in self.load_entries.items()},
            })
            
            if new_email != current_saved_email or uuid != self.cfg_manager.config.get("uuid", ""):
                self.cfg_manager.config["confirmed_uuid"] = ""
            self.cfg_manager.config["email"] = new_email
            self.cfg_manager.config["min_bat"] = min_bat
            self.cfg_manager.config["max_bat"] = max_bat
            self.cfg_manager.config["check_time"] = check_time
            self.cfg_manager.config["uuid"] = uuid
            self.cfg_manager.config["start_minimized"] = bool(self.chk_minimized.get())
            self.cfg_manager.config["auto_monitor"] = bool(self.chk_auto.get())
            self.cfg_manager.config["start_at_login"] = bool(self.chk_login.get())
            self.cfg_manager.config.update(load_settings)
                
            return True, ""
        except ValueError as e:
            return False, str(e)

    def _section_label(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=("Roboto", 11, "bold"),
                     text_color="#8888AA").pack(anchor="w", padx=12, pady=(10, 2))

    def build_ui(self):
        main_frame = ctk.CTkScrollableFrame(self.root, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=18, pady=10)

        # ── Header: logo + title ──────────────────────────────────────────────
        header = ctk.CTkFrame(main_frame, fg_color=BG_CARD, corner_radius=14)
        header.pack(fill=ctk.X, pady=(0, 10))

        if os.path.exists(self._logo_path):
            pil_logo = Image.open(self._logo_path)
            ctk_logo = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(52, 52))
            ctk.CTkLabel(header, image=ctk_logo, text="").pack(side=ctk.LEFT, padx=(14, 8), pady=12)

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.pack(side=ctk.LEFT, pady=12)
        ctk.CTkLabel(title_block, text=f"VoltGuard {__version__}",
                     font=("Roboto", 18, "bold"), text_color="white").pack(anchor="w")
        ctk.CTkLabel(title_block, text="Automatic charge control",
                     font=("Roboto", 11), text_color="#8888AA").pack(anchor="w")
        self.status_label = ctk.CTkLabel(title_block, text="Stopped",
                                         font=("Roboto", 11, "bold"), text_color="#888899")
        self.status_label.pack(anchor="w")

        # ── Credentials ───────────────────────────────────────────────────────
        frame_cred = ctk.CTkFrame(main_frame, fg_color=BG_CARD, corner_radius=12)
        frame_cred.pack(fill=ctk.X, pady=4)

        self._section_label(frame_cred, "MEROSS CREDENTIALS")

        entry_cfg = {"fg_color": INPUT_BG, "border_color": INPUT_BORDER, "border_width": 1}

        ctk.CTkLabel(frame_cred, text="Email:", font=("Roboto", 12),
                     text_color="#CCCCDD").pack(anchor="w", padx=12)
        self.entry_email = ctk.CTkEntry(frame_cred, placeholder_text="ejemplo@gmail.com", **entry_cfg)
        self.entry_email.pack(fill=ctk.X, padx=12, pady=(2, 6))
        self.entry_email.insert(0, self.cfg_manager.config.get("email", ""))

        ctk.CTkLabel(frame_cred, text="Password:", font=("Roboto", 12),
                     text_color="#CCCCDD").pack(anchor="w", padx=12)
        self.entry_password = ctk.CTkEntry(frame_cred, placeholder_text="••••••••", show="*", **entry_cfg)
        self.entry_password.pack(fill=ctk.X, padx=12, pady=(2, 6))
        if self.cfg_manager.password:
            self.entry_password.insert(0, "********")

        ctk.CTkLabel(frame_cred, text="Plug UUID:", font=("Roboto", 12),
                     text_color="#CCCCDD").pack(anchor="w", padx=12)
        self.entry_uuid = ctk.CTkEntry(frame_cred, placeholder_text="2404083...", **entry_cfg)
        self.entry_uuid.pack(fill=ctk.X, padx=12, pady=(2, 12))
        self.entry_uuid.insert(0, self.cfg_manager.config.get("uuid", ""))

        # ── Parameters ────────────────────────────────────────────────────────
        frame_param = ctk.CTkFrame(main_frame, fg_color=BG_CARD, corner_radius=12)
        frame_param.pack(fill=ctk.X, pady=4)

        self._section_label(frame_param, "CHARGE PARAMETERS")

        grid_frame = ctk.CTkFrame(frame_param, fg_color="transparent")
        grid_frame.pack(fill=ctk.X, padx=12, pady=(0, 4))
        grid_frame.columnconfigure(1, weight=1)

        lbl_style = {"font": ("Roboto", 12), "text_color": "#CCCCDD"}
        entry_style = {"width": 80, "fg_color": INPUT_BG, "border_color": INPUT_BORDER, "border_width": 1}

        ctk.CTkLabel(grid_frame, text="Minimum battery (%):", **lbl_style).grid(row=0, column=0, sticky="w", pady=4)
        self.entry_min = ctk.CTkEntry(grid_frame, **entry_style)
        self.entry_min.grid(row=0, column=1, sticky="e", pady=4)
        self.entry_min.insert(0, str(self.cfg_manager.config.get("min_bat", 40)))

        ctk.CTkLabel(grid_frame, text="Maximum battery (%):", **lbl_style).grid(row=1, column=0, sticky="w", pady=4)
        self.entry_max = ctk.CTkEntry(grid_frame, **entry_style)
        self.entry_max.grid(row=1, column=1, sticky="e", pady=4)
        self.entry_max.insert(0, str(self.cfg_manager.config.get("max_bat", 95)))

        ctk.CTkLabel(grid_frame, text="Check interval (sec):", **lbl_style).grid(row=2, column=0, sticky="w", pady=4)
        self.entry_time = ctk.CTkEntry(grid_frame, **entry_style)
        self.entry_time.grid(row=2, column=1, sticky="e", pady=4)
        self.entry_time.insert(0, str(self.cfg_manager.config.get("check_time", 60)))

        # Focus glow on all entries
        for entry in (self.entry_email, self.entry_password, self.entry_uuid,
                      self.entry_min, self.entry_max, self.entry_time):
            entry.bind("<FocusIn>", lambda e, w=entry: w.configure(border_color=INPUT_FOCUS))
            entry.bind("<FocusOut>", lambda e, w=entry: w.configure(border_color=INPUT_BORDER))

        self.chk_minimized = ctk.CTkCheckBox(frame_param, text="Hide window when opened manually",
                                              font=("Roboto", 12), checkbox_width=18, checkbox_height=18,
                                              fg_color=CHK_COLOR, hover_color=CHK_HOVER)
        self.chk_minimized.pack(anchor="w", padx=12, pady=(4, 4))
        if self.cfg_manager.config.get("start_minimized"):
            self.chk_minimized.select()

        self.chk_auto = ctk.CTkCheckBox(frame_param, text="Start charge monitoring automatically",
                                        font=("Roboto", 12), checkbox_width=18, checkbox_height=18,
                                        fg_color=CHK_COLOR, hover_color=CHK_HOVER)
        self.chk_auto.pack(anchor="w", padx=12, pady=4)
        if self.cfg_manager.config.get("auto_monitor"):
            self.chk_auto.select()
        self.chk_login = ctk.CTkCheckBox(frame_param, text="Open panel at Windows / Linux login",
                                         font=("Roboto", 12), checkbox_width=18, checkbox_height=18,
                                         fg_color=CHK_COLOR, hover_color=CHK_HOVER)
        self.chk_login.pack(anchor="w", padx=12, pady=(4, 8))
        if self.cfg_manager.config.get("start_at_login"):
            self.chk_login.select()

        # Load assist uses the same controller and confirmed plug as battery monitoring.
        frame_load = ctk.CTkFrame(main_frame, fg_color=BG_CARD, corner_radius=12)
        frame_load.pack(fill=ctk.X, pady=4)
        self._section_label(frame_load, "PERFORMANCE · CPU / GPU")
        self.chk_load = ctk.CTkCheckBox(frame_load, text="Keep charger ON during sustained load",
                                      font=("Roboto", 12), checkbox_width=18, checkbox_height=18,
                                      fg_color=CHK_COLOR, hover_color=CHK_HOVER)
        self.chk_load.pack(anchor="w", padx=12, pady=(4, 6))
        if self.cfg_manager.config.get("load_enabled"):
            self.chk_load.select()
        ctk.CTkLabel(frame_load, text="CPU OR GPU high → ON. Both idle → release.\nHigh load can keep charging above the battery maximum.",
                     font=("Roboto", 11), text_color="#CCCCDD", justify="left").pack(anchor="w", padx=12, pady=(0, 6))
        load_grid = ctk.CTkFrame(frame_load, fg_color="transparent")
        load_grid.pack(fill=ctk.X, padx=12)
        load_grid.columnconfigure(1, weight=1)
        self.load_entries = {}
        fields = (
            ("load_cpu_on", "CPU high (%):"), ("load_cpu_off", "CPU idle below (%):"),
            ("load_gpu_on", "GPU high (%):"), ("load_gpu_off", "GPU idle below (%):"),
            ("load_on_seconds", "High load for (sec):"),
            ("load_off_seconds", "Both idle for (sec):"),
            ("load_min_on_seconds", "Minimum assist time (sec):"),
        )
        for row, (key, label) in enumerate(fields):
            ctk.CTkLabel(load_grid, text=label, **lbl_style).grid(row=row, column=0, sticky="w", pady=3)
            entry = ctk.CTkEntry(load_grid, **entry_style)
            entry.grid(row=row, column=1, sticky="e", pady=3)
            entry.insert(0, str(self.cfg_manager.config.get(key, LOAD_DEFAULTS[key])))
            entry.bind("<FocusIn>", lambda e, w=entry: w.configure(border_color=INPUT_FOCUS))
            entry.bind("<FocusOut>", lambda e, w=entry: w.configure(border_color=INPUT_BORDER))
            self.load_entries[key] = entry
        self.metrics_label = ctk.CTkLabel(frame_load, text="CPU / GPU: waiting for monitoring",
                                         font=("Roboto", 11), text_color=TEAL, wraplength=400, justify="left")
        self.metrics_label.pack(anchor="w", padx=12, pady=(6, 3))
        report_buttons = ctk.CTkFrame(frame_load, fg_color="transparent")
        report_buttons.pack(fill=ctk.X, padx=12, pady=5)
        ctk.CTkButton(report_buttons, text="Mark stutter", command=self.backend.mark_stutter,
                      width=140, fg_color=GRAY_BTN, hover_color=GRAY_HOVER).pack(side=ctk.LEFT, padx=(0, 8))
        ctk.CTkButton(report_buttons, text="24h sensor report", command=self.show_load_report,
                      width=140, fg_color=GRAY_BTN, hover_color=GRAY_HOVER).pack(side=ctk.LEFT)
        ctk.CTkLabel(frame_load, text="Local sensor history every 5s; no accounts or device IDs.\nSave settings, then START. STOP keeps the current relay state.",
                     font=("Roboto", 10), text_color="#888899", justify="left").pack(anchor="w", padx=12, pady=(0, 10))
        self.btn_save = ctk.CTkButton(frame_load, text="Save settings", command=self.save_settings,
                                      fg_color=GRAY_BTN, hover_color=GRAY_HOVER, height=28)
        self.btn_save.pack(fill=ctk.X, padx=12, pady=(0, 10))

        # ── Action buttons ────────────────────────────────────────────────────
        frame_btns = ctk.CTkFrame(main_frame, fg_color="transparent")
        frame_btns.pack(fill=ctk.X, pady=(8, 4))

        self.btn_start = ctk.CTkButton(frame_btns, text="▶  START", command=self.start_monitor,
                                       fg_color=GREEN, hover_color=GREEN_HOVER,
                                       text_color="white",
                                       font=("Roboto", 13, "bold"), height=40, corner_radius=10)
        self.btn_start.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=(0, 4))

        self.btn_stop = ctk.CTkButton(frame_btns, text="⏹  STOP",
                                      fg_color=RED_OFF, hover_color=RED_OFF,
                                      text_color="#554444",
                                      font=("Roboto", 13, "bold"), height=40, corner_radius=10)
        self.btn_stop.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=(4, 0))

        frame_manual = ctk.CTkFrame(main_frame, fg_color="transparent")
        frame_manual.pack(fill=ctk.X, pady=(0, 6))

        self.btn_on = ctk.CTkButton(frame_manual, text="⚡  Turn ON", command=self.manual_turn_on,
                                    fg_color=YELLOW, hover_color=YELLOW_HOVER, text_color="black",
                                    font=("Roboto", 12), height=36, corner_radius=10)
        self.btn_on.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=(0, 4))

        self.btn_off = ctk.CTkButton(frame_manual, text="⏏  Turn OFF", command=self.manual_turn_off,
                                     fg_color=BLUE, hover_color=BLUE_HOVER, text_color="white",
                                     font=("Roboto", 12), height=36, corner_radius=10)
        self.btn_off.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=(4, 0))

        frame_utils = ctk.CTkFrame(main_frame, fg_color="transparent")
        frame_utils.pack(fill=ctk.X, pady=(0, 6))

        self.btn_test = ctk.CTkButton(frame_utils, text="🔌  Verify Plug", command=self.run_test_plug,
                                      fg_color=TEAL, hover_color=TEAL_HOVER,
                                      text_color="white",
                                      font=("Roboto", 12), height=36, corner_radius=10)
        self.btn_test.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=(0, 4))

        self.btn_exit = ctk.CTkButton(frame_utils, text="✖  Exit", command=self.quit_window,
                                      fg_color=GRAY_BTN, hover_color=GRAY_HOVER,
                                      text_color="white",
                                      font=("Roboto", 12), height=36, corner_radius=10)
        self.btn_exit.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=(4, 0))

        # ── Log area ──────────────────────────────────────────────────────────
        log_frame = ctk.CTkFrame(main_frame, fg_color=BG_CARD, corner_radius=12)
        log_frame.pack(fill=ctk.BOTH, expand=True, pady=(4, 0))
        self._section_label(log_frame, "ACTIVITY LOG")

        self.log_area = ctk.CTkTextbox(log_frame, font=("Consolas", 11),
                                       fg_color=BG_INNER, text_color="#B0B8CC",
                                       corner_radius=8, border_width=0)
        self.log_area.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_area.configure(state="disabled")

    def show_load_report(self):
        if self.demo:
            messagebox.showinfo("Load sensor report", "Preview only. Local sensor history is not accessed.", parent=self.root)
            return
        try:
            report = summarize()
        except OSError:
            report = "Local sensor history is unavailable."
        messagebox.showinfo("Load sensor report · last 24h", report, parent=self.root)

    def _noop(self):
        pass

    def set_gui_state(self, is_running):
        state = "disabled" if is_running else "normal"
        self.entry_email.configure(state=state)
        self.entry_password.configure(state=state)
        self.entry_uuid.configure(state=state)
        self.entry_min.configure(state=state)
        self.entry_max.configure(state=state)
        self.entry_time.configure(state=state)
        self.chk_minimized.configure(state=state)
        self.chk_auto.configure(state=state)
        self.chk_login.configure(state=state)
        self.btn_save.configure(state=state)
        self.chk_load.configure(state=state)
        for entry in self.load_entries.values():
            entry.configure(state=state)

        if is_running:
            self.btn_start.configure(fg_color=GREEN_OFF, hover_color=GREEN_OFF,
                                     text_color="#2A4A35", command=self._noop)
            self.btn_stop.configure(fg_color=RED, hover_color=RED_HOVER,
                                    text_color="white", command=self.stop_monitor)
            self.btn_test.configure(fg_color=TEAL_OFF, hover_color=TEAL_OFF,
                                    text_color="#1A4040", command=self._noop)
            self.btn_on.configure(fg_color=YELLOW_OFF, hover_color=YELLOW_OFF,
                                  text_color="#4A3010", command=self._noop)
            self.btn_off.configure(fg_color=BLUE_OFF, hover_color=BLUE_OFF,
                                   text_color="#1E3050", command=self._noop)
        else:
            self.btn_start.configure(fg_color=GREEN, hover_color=GREEN_HOVER,
                                     text_color="white", command=self.start_monitor)
            self.btn_stop.configure(fg_color=RED_OFF, hover_color=RED_OFF,
                                    text_color="#554444", command=self._noop)
            self.btn_test.configure(fg_color=TEAL, hover_color=TEAL_HOVER,
                                    text_color="white", command=self.run_test_plug)
            self.btn_on.configure(fg_color=YELLOW, hover_color=YELLOW_HOVER,
                                  text_color="black", command=self.manual_turn_on)
            self.btn_off.configure(fg_color=BLUE, hover_color=BLUE_HOVER,
                                   text_color="white", command=self.manual_turn_off)

    # ================= LOGICA DE ARRANQUE =================
    def start_monitor(self):
        if self.demo or self.backend.running: return
        is_valid, err = self.validate_inputs()
        if not is_valid:
            self.log(f"Validation error: {err}", is_error=True)
            return
        if not self.cfg_manager.is_confirmed():
            self.log("Verify and confirm the laptop charging plug before starting.", is_error=True)
            return
        if not self.cfg_manager.raw_save():
            self.log("Secure credential storage unavailable; monitoring was not started.", is_error=True)
            return
        self.backend.running = True
        self.set_gui_state(True)
        self.set_status("Starting connectivity...", "#17a2b8")
        self.log("---- Starting Main Module ----")
        
        self.thread = threading.Thread(target=self._run_async_wrapper, daemon=True)
        self.thread.start()

    def stop_monitor(self):
        self._auto_start_requested = False
        self.log("Requesting safe stop...", is_spam=True)
        self.backend.running = False
        self.set_status("Stopping...", "orange")

    def run_test_plug(self):
        if self.demo or self.backend.running or self.testing_plug: return
        is_valid, err = self.validate_inputs(test_mode=True)
        if not is_valid:
            self.log(f"Validation error: {err}", is_error=True)
            return
        if not self.cfg_manager.raw_save(): return
        
        self.testing_plug = True
        self.set_gui_state(True)
        self.log("Checking the configured plug without switching it…")
        threading.Thread(target=self._test_wrapper, daemon=True).start()

    def manual_turn_on(self):
        self._manual_action(True)

    def manual_turn_off(self):
        self._manual_action(False)

    def _manual_action(self, turn_on):
        if not self.cfg_manager.is_confirmed():
            self.log("Verify and confirm the laptop charging plug first.", is_error=True)
            return
        if self.demo or self.backend.running or self.testing_plug: return
        is_valid, err = self.validate_inputs()
        if not is_valid:
            self.log(f"Validation error: {err}", is_error=True)
            return
        if not self.cfg_manager.raw_save(): return
        
        self.testing_plug = True
        self.set_gui_state(True)
        action = "ON" if turn_on else "OFF"
        self.log(f"Manual control: Turning {action}...")
        threading.Thread(target=self._manual_wrapper, args=(turn_on,), daemon=True).start()

    # ================= WRAPPERS ASYNCIO =================
    def _run_async_wrapper(self):
        if os.name == 'nt':
            if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            while self.backend.running:
                try:
                    loop.run_until_complete(self.backend.monitor_loop())
                except Exception as e:
                    if not self.backend.running: break
                    self.log(f"Monitor unavailable ({type(e).__name__}). Retrying in 30s.", is_error=True)
                    self.set_status("Reconnecting (Network Error)", "red")
                    
                    for _ in range(30):
                        if not self.backend.running: break
                        time.sleep(1)
        finally:
            loop.close()
            self.post_ui(self.set_gui_state, False)
            self.post_ui(self.set_status, "Stopped", "gray")
            self.log("---- Monitor stopped gracefully ----")

    def _test_wrapper(self):
        info = None
        try:
            info = asyncio.run(self.backend.inspect_plug())
        except Exception as error:
            self.log(f"Plug verification failed ({type(error).__name__}). Check the account credentials.", is_error=True)
        finally:
            self.testing_plug = False
            self.post_ui(self._finish_verification, info)

    def _finish_verification(self, info):
        self.set_gui_state(False)
        self.set_status("Stopped", "gray")
        if info:
            self._confirm_plug(info)

    def _manual_wrapper(self, turn_on):
        if os.name == 'nt':
            if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.backend.async_manual_control(turn_on))
        finally:
            loop.close()
            self.testing_plug = False
            self.post_ui(self.set_gui_state, False)
            self.post_ui(self.set_status, "Stopped", "gray")

    def _confirm_plug(self, info):
        approved = self.cfg_manager.config.get("approved_uuid") == info["uuid"]
        if approved or messagebox.askyesno("Confirm laptop charging plug",
                               f"Found: {info['name']} ({info['type']}).\n"
                               "Is this the plug powering THIS laptop charger?\n"
                               "No power switching has been performed."):
            self.cfg_manager.config.update(confirmed_uuid=info["uuid"], approved_uuid=info["uuid"],
                                           device_name=info["name"], device_type=info["type"])
            if self.cfg_manager.raw_save():
                self.log("Charging plug verified. Ready for automatic monitoring.")
                if self.cfg_manager.config.get("auto_monitor"):
                    self.start_monitor()
