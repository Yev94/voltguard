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
        self.last_command = None

    async def async_test_plug(self):
        self.set_status("Testing Hardware...", "orange")
        try:
            http_client = await MerossHttpClient.async_from_user_password(
                api_base_url="https://iotx-eu.meross.com",
                email=self.cfg.config["email"], password=self.cfg.password)
            manager = MerossManager(http_client=http_client)
            await manager.async_init()
            await manager.async_device_discovery()
            plugs = manager.find_devices()
            
            target_uuid = self.cfg.config.get("uuid", "").strip()
            if not target_uuid:
                self.log(f"📋 Found {len(plugs)} devices on this account:")
                for p in plugs:
                    self.log(f"   🔹 Name: {p.name} | Type: {p.type} | UUID: {p.uuid}")
                self.log("👉 Copy your desired UUID and paste it in the configuration.")
            else:
                plug = next((p for p in plugs if p.uuid == target_uuid), None)
                
                if not plug:
                    self.log(f"❌ Could not find plug with UUID {target_uuid}.", is_error=True)
                else:
                    self.log(f"📡 Testing '{plug.name}': Turning ON...")
                    await plug.async_turn_on()
                    await plug.async_update()
                    
                    await asyncio.sleep(2)
                    
                    self.log(f"📡 Testing '{plug.name}': Turning OFF...")
                    await plug.async_turn_off()
                    await plug.async_update()
                    
                    self.log("✅ Test Finished OK")
                
        except Exception as e:
            self.log(f"❌ Hardware Test Error: {e}", is_error=True)
        finally:
            try:
                if 'manager' in locals(): manager.close()
                if 'http_client' in locals(): await http_client.async_logout()
            except: pass

    async def async_manual_control(self, turn_on: bool):
        action = "Turning ON" if turn_on else "Turning OFF"
        self.set_status(f"{action} Manual...", "orange")
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
                self.log(f"❌ Could not find plug with UUID {self.cfg.config['uuid']}.", is_error=True)
            else:
                self.log(f"🔌 Manual: {action} '{plug.name}'...")
                if turn_on:
                    await plug.async_turn_on()
                else:
                    await plug.async_turn_off()
                await plug.async_update()
                self.log(f"✅ {action} completed.")
                
        except Exception as e:
            self.log(f"❌ Manual Control Error: {e}", is_error=True)
        finally:
            try:
                if 'manager' in locals(): manager.close()
                if 'http_client' in locals(): await http_client.async_logout()
            except: pass

    async def monitor_loop(self):
        self.set_status("Connecting...", "orange")
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
                self.log(f"❌ Could not find plug with UUID {self.cfg.config['uuid']}.", is_error=True)
                self.running = False
                return
            
            await plug.async_update()
            self.log(f"✅ Connected and Bound to '{plug.name}'")
            self.set_status(f"Monitoring ({plug.name})", "#28a745")
            
            self.last_status = None
            
            while self.running:
                battery = psutil.sensors_battery()
                if battery is None:
                    self.log("❌ Could not read system battery.", is_error=True)
                    break
                    
                porcentaje = battery.percent
                cargando = battery.power_plugged
                
                bucket = porcentaje // 5
                
                # El estado del enchufe ahora es pasivo en el log, usamos last_command o "Desconocido" inicialmente
                enc_pasivo = self.last_command if self.last_command else "?" 
                status = f"{bucket}-{cargando}-{enc_pasivo}"
                
                if status != self.last_status:
                    est = "Charging" if cargando else "Discharging"
                    self.log(f"🔋 Battery: {porcentaje}% | {est} | Plug Cache: {enc_pasivo.upper() if enc_pasivo != '?' else '?'}")
                    self.last_status = status
                
                # Lógica simplificada basada solo en batería:
                if porcentaje <= self.cfg.config["min_bat"] and not cargando:
                    if self.last_command != "on":
                        # Toca encender. Comprobamos su estado real primero.
                        await plug.async_update()
                        if not plug.is_on():
                            self.log(f"⚡ Low Battery ({porcentaje}%). Triggering ON.")
                            await plug.async_turn_on()
                        else:
                            self.log(f"⚡ Low Battery ({porcentaje}%). Enchufe ya estaba ON externamente.")
                        self.last_command = "on"
                        
                elif porcentaje >= self.cfg.config["max_bat"] and cargando:
                    if self.last_command != "off":
                        # Toca apagar. Comprobamos su estado real primero.
                        await plug.async_update()
                        if plug.is_on():
                            self.log(f"🛡 Battery Charged ({porcentaje}%). Triggering OFF.")
                            await plug.async_turn_off()
                        else:
                            self.log(f"🛡 Battery Charged ({porcentaje}%). Enchufe ya estaba OFF externamente.")
                        self.last_command = "off"
                
                for _ in range(self.cfg.config["check_time"]):
                    if not self.running: break
                    await asyncio.sleep(1)

        finally:
            try:
                if manager: manager.close()
                if http_api_client: await http_api_client.async_logout()
            except: pass
