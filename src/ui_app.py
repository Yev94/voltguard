import asyncio
import os
import threading
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageTk
import customtkinter as ctk
import pystray

from src.logger_config import get_logger
from src.config_manager import ConfigManager
from src.battery_backend import BatteryBackend

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
    def __init__(self, root):
        self.root = root
        self.root.title("VoltGuard")
        self.root.geometry("500x800")
        self.root.resizable(False, False)

        import sys
        try:
            base_dir = sys._MEIPASS
        except AttributeError:
            base_dir = os.path.dirname(os.path.dirname(__file__))
        
        self._ico_path = os.path.join(base_dir, "logo.ico")
        self._logo_path = os.path.join(base_dir, "logo.png")
        if os.path.exists(self._ico_path):
            self.root.after(100, lambda: self.root.iconbitmap(self._ico_path))

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        self.cfg_manager = ConfigManager()
        self.backend = BatteryBackend(self.cfg_manager, self.log, self.set_status)
        
        self.testing_plug = False
        self.thread = None
        self.tray_icon = None
        self.tray_starting = False
        
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        if self.cfg_manager.config.get("start_minimized"):
            self.root.after(300, self.hide_window)
            self.root.after(500, self.start_monitor)

    # ================= LOGS & STATUS UI =================
    def log(self, msg, is_error=False, is_spam=False):
        if not is_spam:
            if is_error:
                logger.error(msg)
            else:
                logger.info(msg)
        self.root.after(0, self._update_log_area, msg)

    def _update_log_area(self, msg):
        hora = datetime.now().strftime("%H:%M:%S")
        self.log_area.configure(state="normal")
        self.log_area.insert("end", f"[{hora}] {msg}\n")
        self.log_area.see("end")
        self.log_area.configure(state="disabled")

    def set_status(self, text, color="white"):
        self.root.after(0, lambda: self.status_label.configure(text=text, text_color=color))

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
        finally:
            self.tray_starting = False

    def hide_window(self, icon=None, item=None):
        self.root.withdraw()
        if not self.tray_icon and not self.tray_starting:
            self.tray_starting = True
            threading.Thread(target=self._run_tray, daemon=True).start()

    def show_window(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.after(0, self.root.deiconify)

    def quit_window(self, icon=None, item=None):
        self.backend.running = False
        self.testing_plug = False
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.after(0, self.root.destroy)

    # ================= UI & INPUT VALIDATION =================
    def validate_inputs(self):
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
                raise ValueError("Password required.")

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
            if not uuid: raise ValueError("UUID required.")
            
            self.cfg_manager.config["email"] = new_email
            self.cfg_manager.config["min_bat"] = min_bat
            self.cfg_manager.config["max_bat"] = max_bat
            self.cfg_manager.config["check_time"] = check_time
            self.cfg_manager.config["uuid"] = uuid
            self.cfg_manager.config["start_minimized"] = bool(self.chk_minimized.get())
                
            return True, ""
        except ValueError as e:
            return False, str(e)

    def _section_label(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=("Roboto", 11, "bold"),
                     text_color="#8888AA").pack(anchor="w", padx=12, pady=(10, 2))

    def build_ui(self):
        main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
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
        ctk.CTkLabel(title_block, text="VoltGuard",
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
        self.entry_min.insert(0, str(self.cfg_manager.config.get("min_bat", 20)))

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

        self.chk_minimized = ctk.CTkCheckBox(frame_param, text="Start minimized and auto-start monitor",
                                              font=("Roboto", 12), checkbox_width=18, checkbox_height=18,
                                              fg_color=CHK_COLOR, hover_color=CHK_HOVER)
        self.chk_minimized.pack(anchor="w", padx=12, pady=(4, 12))
        if self.cfg_manager.config.get("start_minimized"):
            self.chk_minimized.select()

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

        frame_utils = ctk.CTkFrame(main_frame, fg_color="transparent")
        frame_utils.pack(fill=ctk.X, pady=(0, 6))

        self.btn_test = ctk.CTkButton(frame_utils, text="🔌  Test Plug", command=self.run_test_plug,
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

        if is_running:
            self.btn_start.configure(fg_color=GREEN_OFF, hover_color=GREEN_OFF,
                                     text_color="#2A4A35", command=self._noop)
            self.btn_stop.configure(fg_color=RED, hover_color=RED_HOVER,
                                    text_color="white", command=self.stop_monitor)
            self.btn_test.configure(fg_color=TEAL_OFF, hover_color=TEAL_OFF,
                                    text_color="#1A4040", command=self._noop)
        else:
            self.btn_start.configure(fg_color=GREEN, hover_color=GREEN_HOVER,
                                     text_color="white", command=self.start_monitor)
            self.btn_stop.configure(fg_color=RED_OFF, hover_color=RED_OFF,
                                    text_color="#554444", command=self._noop)
            self.btn_test.configure(fg_color=TEAL, hover_color=TEAL_HOVER,
                                    text_color="white", command=self.run_test_plug)

    # ================= LOGICA DE ARRANQUE =================
    def start_monitor(self):
        if self.backend.running: return
        is_valid, err = self.validate_inputs()
        if not is_valid:
            self.log(f"Validation error: {err}", is_error=True)
            return
        if not self.cfg_manager.raw_save(): return
        
        self.backend.running = True
        self.set_gui_state(True)
        self.set_status("Starting connectivity...", "#17a2b8")
        self.log("---- Starting Main Module ----")
        
        self.thread = threading.Thread(target=self._run_async_wrapper, daemon=True)
        self.thread.start()

    def stop_monitor(self):
        self.log("Requesting safe stop...", is_spam=True)
        self.backend.running = False
        self.set_status("Stopping...", "orange")

    def run_test_plug(self):
        if self.backend.running or self.testing_plug: return
        is_valid, err = self.validate_inputs()
        if not is_valid:
            self.log(f"Validation error: {err}", is_error=True)
            return
        if not self.cfg_manager.raw_save(): return
        
        self.testing_plug = True
        self.set_gui_state(True)
        self.log("Starting Hardware Test Sequence...")
        threading.Thread(target=self._test_wrapper, daemon=True).start()

    # ================= WRAPPERS ASYNCIO =================
    def _run_async_wrapper(self):
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            while self.backend.running:
                try:
                    loop.run_until_complete(self.backend.monitor_loop())
                except Exception as e:
                    if not self.backend.running: break
                    self.log(f"Critical network/Meross failure: {e}. Retrying in 30s.", is_error=True)
                    self.set_status("Reconnecting (Network Error)", "red")
                    
                    for _ in range(30):
                        if not self.backend.running: break
                        time.sleep(1)
        finally:
            loop.close()
            self.root.after(0, self.set_gui_state, False)
            self.root.after(0, self.set_status, "Stopped", "gray")
            self.log("---- Monitor stopped gracefully ----")

    def _test_wrapper(self):
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.backend.async_test_plug())
        finally:
            loop.close()
            self.testing_plug = False
            self.root.after(0, self.set_gui_state, False)
            self.root.after(0, self.set_status, "Stopped", "gray")
