import asyncio
import os
import threading
import time
from datetime import datetime
from PIL import Image, ImageDraw
import customtkinter as ctk
import pystray

from src.logger_config import get_logger
from src.config_manager import ConfigManager
from src.battery_backend import BatteryBackend

logger = get_logger()

class MerossBatteryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Monitor Batería -> Meross")
        self.root.geometry("450x720")
        self.root.resizable(False, False)
        
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
        self.root.after(0, lambda: self.status_label.configure(text=f"Estado: {text}", text_color=color))

    # ================= PYSTRAY (WINDOW MGMT) =================
    def _crear_icono_tray(self):
        image = Image.new('RGB', (64, 64), color=(30, 30, 30))
        d = ImageDraw.Draw(image)
        d.text((10, 20), "MBM", fill=(0, 255, 150))
        return image

    def _run_tray(self):
        try:
            menu = (
                pystray.MenuItem('Abrir', self.show_window, default=True),
                pystray.MenuItem('Salir', self.quit_window)
            )
            self.tray_icon = pystray.Icon("MerossBattery", self._crear_icono_tray(), "Meross Monitor", menu)
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
                raise ValueError("Email obligatorio.")

            if new_email != current_saved_email and pwd == "********":
                raise ValueError("Si cambias el email, debes volver a escribir la contraseña.")

            if new_email != current_saved_email:
                self.cfg_manager.password = ""

            if not pwd and not self.cfg_manager.password:
                raise ValueError("Contraseña requerida.")

            if pwd and pwd != "********":
                self.cfg_manager.password = pwd

            min_bat = int(self.entry_min.get())
            max_bat = int(self.entry_max.get())
            check_time = int(self.entry_time.get())
            uuid = self.entry_uuid.get().strip()

            if not (1 <= min_bat <= 99): raise ValueError("Batería mínima entre 1 y 99.")
            if not (1 <= max_bat <= 100): raise ValueError("Batería máxima entre 1 y 100.")
            if min_bat >= max_bat: raise ValueError("Mínima debe ser menor que máxima.")
            if check_time < 5: raise ValueError("Chequeo >= 5 segundos.")
            if not uuid: raise ValueError("UUID obligatorio.")
            
            self.cfg_manager.config["email"] = new_email
            self.cfg_manager.config["min_bat"] = min_bat
            self.cfg_manager.config["max_bat"] = max_bat
            self.cfg_manager.config["check_time"] = check_time
            self.cfg_manager.config["uuid"] = uuid
            self.cfg_manager.config["start_minimized"] = bool(self.chk_minimized.get())
                
            return True, ""
        except ValueError as e:
            return False, str(e)

    def build_ui(self):
        main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=10)
        
        lbl_title = ctk.CTkLabel(main_frame, text="Monitor de Batería Meross", font=("Roboto", 20, "bold"))
        lbl_title.pack(pady=(0, 5))
        
        self.status_label = ctk.CTkLabel(main_frame, text="Estado: Detenido", text_color="gray")
        self.status_label.pack(pady=(0, 10))

        frame_cred = ctk.CTkFrame(main_frame)
        frame_cred.pack(fill=ctk.X, pady=5)
        
        ctk.CTkLabel(frame_cred, text="Email de Meross:").pack(anchor="w", padx=10, pady=(5,0))
        self.entry_email = ctk.CTkEntry(frame_cred, placeholder_text="ejemplo@google.com")
        self.entry_email.pack(fill=ctk.X, padx=10, pady=(0, 5))
        self.entry_email.insert(0, self.cfg_manager.config.get("email", ""))
        
        ctk.CTkLabel(frame_cred, text="Contraseña:").pack(anchor="w", padx=10, pady=(5,0))
        self.entry_password = ctk.CTkEntry(frame_cred, placeholder_text="********", show="*")
        self.entry_password.pack(fill=ctk.X, padx=10, pady=(0, 5))
        if self.cfg_manager.password: self.entry_password.insert(0, "********")

        ctk.CTkLabel(frame_cred, text="UUID del Enchufe:").pack(anchor="w", padx=10, pady=(5,0))
        self.entry_uuid = ctk.CTkEntry(frame_cred, placeholder_text="2404083...")
        self.entry_uuid.pack(fill=ctk.X, padx=10, pady=(0, 10))
        self.entry_uuid.insert(0, self.cfg_manager.config.get("uuid", ""))

        frame_param = ctk.CTkFrame(main_frame)
        frame_param.pack(fill=ctk.X, pady=10)
        
        grid_frame = ctk.CTkFrame(frame_param, fg_color="transparent")
        grid_frame.pack(padx=10, pady=10)
        
        ctk.CTkLabel(grid_frame, text="Batería Baja (%):").grid(row=0, column=0, sticky="w", pady=5)
        self.entry_min = ctk.CTkEntry(grid_frame, width=80)
        self.entry_min.grid(row=0, column=1, padx=10)
        self.entry_min.insert(0, str(self.cfg_manager.config.get("min_bat", 20)))
        
        ctk.CTkLabel(grid_frame, text="Batería Completa (%):").grid(row=1, column=0, sticky="w", pady=5)
        self.entry_max = ctk.CTkEntry(grid_frame, width=80)
        self.entry_max.grid(row=1, column=1, padx=10)
        self.entry_max.insert(0, str(self.cfg_manager.config.get("max_bat", 95)))
        
        ctk.CTkLabel(grid_frame, text="Chequeo (segundos):").grid(row=2, column=0, sticky="w", pady=5)
        self.entry_time = ctk.CTkEntry(grid_frame, width=80)
        self.entry_time.grid(row=2, column=1, padx=10)
        self.entry_time.insert(0, str(self.cfg_manager.config.get("check_time", 60)))
        
        self.chk_minimized = ctk.CTkCheckBox(frame_param, text="Abrir minimizada y arrancar monitor")
        self.chk_minimized.pack(pady=10)
        if self.cfg_manager.config.get("start_minimized"): self.chk_minimized.select()
        
        frame_btns = ctk.CTkFrame(main_frame, fg_color="transparent")
        frame_btns.pack(fill=ctk.X, pady=10)
        
        self.btn_start = ctk.CTkButton(frame_btns, text="▶ START", command=self.start_monitor, fg_color="#28a745", hover_color="#218838", width=100)
        self.btn_start.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5)
        
        self.btn_stop = ctk.CTkButton(frame_btns, text="⏹ STOP", command=self.stop_monitor, fg_color="#dc3545", hover_color="#c82333", state="disabled", width=100)
        self.btn_stop.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5)
        
        frame_utils = ctk.CTkFrame(main_frame, fg_color="transparent")
        frame_utils.pack(fill=ctk.X, pady=5)
        
        self.btn_test = ctk.CTkButton(frame_utils, text="🔌 Test Enchufe", command=self.run_test_plug, fg_color="#17a2b8", hover_color="#138496")
        self.btn_test.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5)
        
        self.btn_exit = ctk.CTkButton(frame_utils, text="✖ Salir", command=self.quit_window, fg_color="gray", hover_color="#5a5a5a")
        self.btn_exit.pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5)
        
        self.log_area = ctk.CTkTextbox(main_frame, height=130, font=("Consolas", 11))
        self.log_area.pack(fill=ctk.BOTH, expand=True, pady=10)
        self.log_area.configure(state="disabled")

    def set_gui_state(self, is_running):
        state = "disabled" if is_running else "normal"
        self.entry_email.configure(state=state)
        self.entry_password.configure(state=state)
        self.entry_uuid.configure(state=state)
        self.entry_min.configure(state=state)
        self.entry_max.configure(state=state)
        self.entry_time.configure(state=state)
        self.chk_minimized.configure(state=state)
        
        self.btn_start.configure(state="disabled" if is_running else "normal")
        self.btn_stop.configure(state="normal" if is_running else "disabled")
        self.btn_test.configure(state="disabled" if is_running else "normal")

    # ================= LOGICA DE ARRANQUE =================
    def start_monitor(self):
        if self.backend.running: return
        is_valid, err = self.validate_inputs()
        if not is_valid:
            self.log(f"Error de validación: {err}", is_error=True)
            return
        if not self.cfg_manager.raw_save(): return
        
        self.backend.running = True
        self.set_gui_state(True)
        self.set_status("Iniciando conectividad...", "#17a2b8")
        self.log("---- Arrancando Módulo Principal ----")
        
        self.thread = threading.Thread(target=self._run_async_wrapper, daemon=True)
        self.thread.start()

    def stop_monitor(self):
        self.log("Solicitando detención segura...", is_spam=True)
        self.backend.running = False
        self.set_status("Deteniendo...", "orange")

    def run_test_plug(self):
        if self.backend.running or self.testing_plug: return
        is_valid, err = self.validate_inputs()
        if not is_valid:
            self.log(f"Error de validación: {err}", is_error=True)
            return
        if not self.cfg_manager.raw_save(): return
        
        self.testing_plug = True
        self.set_gui_state(True)
        self.log("Iniciando Secuencia de Prueba de Hardware...")
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
                    self.log(f"Fallo grave en red/Meross: {e}. Reintentando en 30s.", is_error=True)
                    self.set_status("Reconectando (Error Red)", "red")
                    
                    for _ in range(30):
                        if not self.backend.running: break
                        time.sleep(1)
        finally:
            loop.close()
            self.root.after(0, self.set_gui_state, False)
            self.root.after(0, self.set_status, "Detenido", "gray")
            self.log("---- Monitor detenido correctamente ----")

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
            self.root.after(0, self.set_status, "Detenido", "gray")
