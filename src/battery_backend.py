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
        self.set_status("Testing Hardware...", "orange")
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
                
                await plug.async_update()
                enchufe_encendido = plug.is_on()
                
                bucket = porcentaje // 5
                status = f"{bucket}-{cargando}-{enchufe_encendido}"
                
                if status != self.last_status:
                    est = "Charging" if cargando else "Discharging"
                    enc = "ON" if enchufe_encendido else "OFF"
                    self.log(f"🔋 Battery: {porcentaje}% | {est} | Plug: {enc}")
                    self.last_status = status
                
                now = time.time()
                if porcentaje <= self.cfg.config["min_bat"] and not cargando and not enchufe_encendido:
                    if now - self.last_action_ts > 60:
                        self.log(f"⚡ Low Battery ({porcentaje}%). Triggering ON.")
                        await plug.async_turn_on()
                        await plug.async_update()
                        self.last_action_ts = now
                elif porcentaje >= self.cfg.config["max_bat"] and cargando and enchufe_encendido:
                    if now - self.last_action_ts > 60:
                        self.log(f"🛡 Battery Charged ({porcentaje}%). Triggering OFF.")
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
