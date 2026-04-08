import asyncio
import os
import psutil
import time
from src.logger_config import get_logger
from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager

class BatteryBackend:
    def __init__(self, config_manager, log_callback, status_callback):
        self.cfg = config_manager
        self.log = log_callback
        self.set_status = status_callback
        self.running = False
        self.last_status = None
        self.last_action_ts = 0

    async def async_test_plug(self):
        self.set_status("Probando Hardware...", "orange")
        try:
            http_client = await MerossHttpClient.async_from_user_password(
                api_base_url="https://iotx-eu.meross.com",
                email=self.cfg.config["email"], password=self.cfg.password)
            manager = MerossManager(http_client=http_client)
            await manager.async_init()
            await manager.async_device_discovery()
            plugs = manager.find_devices()
            plug = next((p for p in plugs if p.uuid == self.cfg.config["uuid"]), None)
            
            if not plug:
                self.log(f"❌ No se encontró el enchufe con UUID {self.cfg.config['uuid']}.", is_error=True)
            else:
                self.log(f"📡 Testeando '{plug.name}': Encendiendo...")
                await plug.async_turn_on()
                await plug.async_update()
                
                await asyncio.sleep(2)
                
                self.log(f"📡 Testeando '{plug.name}': Apagando...")
                await plug.async_turn_off()
                await plug.async_update()
                
                self.log("✅ Prueba Finalizada OK")
                
        except Exception as e:
            self.log(f"❌ Error de Hardware Test: {e}", is_error=True)
        finally:
            try:
                if 'manager' in locals(): manager.close()
                if 'http_client' in locals(): await http_client.async_logout()
            except: pass

    async def monitor_loop(self):
        self.set_status("Conectando...", "orange")
        http_api_client = None
        manager = None
        try:
            http_api_client = await MerossHttpClient.async_from_user_password(
                api_base_url="https://iotx-eu.meross.com",
                email=self.cfg.config["email"], password=self.cfg.password
            )
            manager = MerossManager(http_client=http_api_client)
            await manager.async_init()
            await manager.async_device_discovery()
            
            plug = next((p for p in manager.find_devices() if p.uuid == self.cfg.config["uuid"]), None)
            if not plug:
                self.log(f"❌ No se encontró el enchufe con UUID {self.cfg.config['uuid']}.", is_error=True)
                self.running = False
                return
            
            await plug.async_update()
            self.log(f"✅ Conectado y Enlazado a '{plug.name}'")
            self.set_status(f"Monitorizando ({plug.name})", "#28a745")
            
            self.last_status = None
            
            while self.running:
                battery = psutil.sensors_battery()
                if battery is None:
                    self.log("❌ No se pudo leer la batería del sistema.", is_error=True)
                    break
                    
                porcentaje = battery.percent
                cargando = battery.power_plugged
                
                await plug.async_update()
                enchufe_encendido = plug.is_on()
                
                bucket = porcentaje // 5
                status = f"{bucket}-{cargando}-{enchufe_encendido}"
                
                if status != self.last_status:
                    est = "Cargando" if cargando else "Descargando"
                    enc = "ON" if enchufe_encendido else "OFF"
                    self.log(f"🔋 Batería: {porcentaje}% | {est} | Enchufe: {enc}")
                    self.last_status = status
                
                now = time.time()
                if porcentaje <= self.cfg.config["min_bat"] and not cargando and not enchufe_encendido:
                    if now - self.last_action_ts > 60:
                        self.log(f"⚡ Batería Baja ({porcentaje}%). Disparando ON.")
                        await plug.async_turn_on()
                        await plug.async_update()
                        self.last_action_ts = now
                elif porcentaje >= self.cfg.config["max_bat"] and cargando and enchufe_encendido:
                    if now - self.last_action_ts > 60:
                        self.log(f"🛡 Batería Cargada ({porcentaje}%). Disparando OFF.")
                        await plug.async_turn_off()
                        await plug.async_update()
                        self.last_action_ts = now
                
                for _ in range(self.cfg.config["check_time"]):
                    if not self.running: break
                    await asyncio.sleep(1)

        finally:
            try:
                if manager: manager.close()
                if http_api_client: await http_api_client.async_logout()
            except: pass
