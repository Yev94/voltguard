import asyncio
import psutil
import time
from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager

class BatteryBackend:
    def __init__(self, config_manager, log_callback, status_callback):
        self.cfg = config_manager
        self.log = log_callback
        self.set_status = status_callback
        self.running = False
        
        # State Initialization
        self.last_status = None
        self.last_confirmed_command = None
        self.armed_for_on = True
        self.armed_for_off = True
        self.retry_count_on = 0
        self.retry_count_off = 0
        self.was_ac_connected = None
        
    async def _connect_meross(self, require_uuid=True):
        http_client = await MerossHttpClient.async_from_user_password(
            api_base_url="https://iotx-eu.meross.com",
            email=self.cfg.config["email"], password=self.cfg.password
        )
        manager = MerossManager(http_client=http_client)
        await manager.async_init()
        await manager.async_device_discovery()
        
        plugs = manager.find_devices()
        target_uuid = self.cfg.config.get("uuid", "").strip()
        plug = None
        
        if require_uuid and not target_uuid:
            self.log("❌ UUID not configured.", is_error=True)
        elif target_uuid:
            plug = next((p for p in plugs if p.uuid == target_uuid), None)
            if require_uuid and not plug:
                self.log(f"❌ Could not find plug with UUID {target_uuid}.", is_error=True)
                
        return http_client, manager, plugs, plug

    async def _refresh_plug_state(self, plug):
        await plug.async_update()
        return plug.is_on()

    async def async_test_plug(self):
        self.set_status("Testing Hardware...", "orange")
        try:
            http_client, manager, plugs, plug = await self._connect_meross(require_uuid=False)
            
            target_uuid = self.cfg.config.get("uuid", "").strip()
            if not target_uuid:
                self.log(f"📋 Found {len(plugs)} devices on this account:")
                for p in plugs:
                    self.log(f"   🔹 Name: {p.name} | Type: {p.type} | UUID: {p.uuid}")
                self.log("👉 Copy your desired UUID and paste it in the configuration.")
            else:
                if not plug:
                    pass # Logged by _connect_meross
                else:
                    self.log(f"📡 Testing '{plug.name}': Turning ON...")
                    await plug.async_turn_on()
                    await self._refresh_plug_state(plug)
                    await asyncio.sleep(2)
                    self.log(f"📡 Testing '{plug.name}': Turning OFF...")
                    await plug.async_turn_off()
                    await self._refresh_plug_state(plug)
                    self.log("✅ Test Finished OK")
        except Exception as e:
            self.log(f"❌ Hardware Test Error: {e}", is_error=True)
        finally:
            try:
                if 'manager' in locals() and manager: manager.close()
                if 'http_client' in locals() and http_client: await http_client.async_logout()
            except Exception: pass

    async def async_manual_control(self, turn_on: bool):
        action = "Turning ON" if turn_on else "Turning OFF"
        self.set_status(f"{action} Manual...", "orange")
        try:
            http_client, manager, _, plug = await self._connect_meross(require_uuid=True)
            if plug:
                self.log(f"🔌 Manual: {action} '{plug.name}'...")
                if turn_on:
                    await plug.async_turn_on()
                else:
                    await plug.async_turn_off()
                self.last_confirmed_command = "on" if await self._refresh_plug_state(plug) else "off"
                self.log(f"✅ {action} completed.")
        except Exception as e:
            self.log(f"❌ Manual Control Error: {e}", is_error=True)
        finally:
            try:
                if 'manager' in locals() and manager: manager.close()
                if 'http_client' in locals() and http_client: await http_client.async_logout()
            except Exception: pass

    async def monitor_loop(self):
        self.set_status("Connecting...", "orange")
        
        # State Initialization to ensure clean runs
        self.armed_for_on = True
        self.armed_for_off = True
        self.retry_count_on = 0
        self.retry_count_off = 0
        self.last_status = None
        self.was_ac_connected = None
        last_log_time = 0
        
        http_api_client = None
        manager = None
        try:
            http_api_client, manager, _, plug = await self._connect_meross(require_uuid=True)
            if not plug:
                self.running = False
                return
            
            self.last_confirmed_command = "on" if await self._refresh_plug_state(plug) else "off"
            self.log(f"✅ Connected and Bound to '{plug.name}'")
            self.log(f"⚙️ Monitor started | Min: {self.cfg.config['min_bat']}% | Max: {self.cfg.config['max_bat']}% | Check: {self.cfg.config['check_time']}s")
            self.set_status(f"Monitoring ({plug.name})", "#28a745")
            
            while self.running:
                try:
                    battery = psutil.sensors_battery()
                    if battery is None:
                        self.log("❌ Could not read system battery.", is_error=True)
                        break
                        
                    porcentaje = battery.percent
                    enchufado_a_corriente = battery.power_plugged
                    
                    if self.was_ac_connected is not None and self.was_ac_connected != enchufado_a_corriente:
                        estado_str = "lost" if not enchufado_a_corriente else "detected"
                        self.log(f"🔌 AC Power {estado_str}. Verifying real plug state...")
                        self.last_confirmed_command = "on" if await self._refresh_plug_state(plug) else "off"
                        
                    self.was_ac_connected = enchufado_a_corriente
                    
                    bucket = porcentaje // 5
                    enc_pasivo = self.last_confirmed_command if self.last_confirmed_command else "?" 
                    status = f"{bucket}-{enchufado_a_corriente}-{enc_pasivo}"
                    
                    current_time = time.time()
                    time_to_log = (current_time - last_log_time) >= 120  # Log every 2 minutes minimum
                    
                    if status != self.last_status or time_to_log:
                        est = "AC Connected" if enchufado_a_corriente else "Discharging"
                        self.log(f"🔋 Battery: {porcentaje}% | {est} | Last Action: {enc_pasivo.upper() if enc_pasivo != '?' else '?'}")
                        self.last_status = status
                        last_log_time = current_time
                    
                    if porcentaje > self.cfg.config["min_bat"]:
                        self.armed_for_on = True
                        self.retry_count_on = 0
                        
                    if porcentaje < self.cfg.config["max_bat"]:
                        self.armed_for_off = True
                        self.retry_count_off = 0
                    
                    if porcentaje <= self.cfg.config["min_bat"] and not enchufado_a_corriente:
                        if self.armed_for_on:
                            if not await self._refresh_plug_state(plug):
                                if self.retry_count_on >= 3:
                                    self.log(f"❌ Max retries (3) reached. Giving up on turning ON.", is_error=True)
                                    self.armed_for_on = False
                                else:
                                    self.retry_count_on += 1
                                    self.log(f"⚡ Low Battery ({porcentaje}%). Turning ON plug (Attempt {self.retry_count_on}/3).")
                                    await plug.async_turn_on()
                                    if await self._refresh_plug_state(plug):
                                        self.last_confirmed_command = "on"
                                        self.armed_for_on = False
                                        self.retry_count_on = 0
                                    else:
                                        self.log("❌ Could not confirm plug turned ON. Will retry.", is_error=True)
                            else:
                                self.log(f"⚡ Low Battery ({porcentaje}%). Plug was already ON externally.")
                                self.last_confirmed_command = "on"
                                self.armed_for_on = False
                                self.retry_count_on = 0
                            
                    elif porcentaje >= self.cfg.config["max_bat"] and enchufado_a_corriente:
                        if self.armed_for_off:
                            if await self._refresh_plug_state(plug):
                                if self.retry_count_off >= 3:
                                    self.log(f"❌ Max retries (3) reached. Giving up on turning OFF.", is_error=True)
                                    self.armed_for_off = False
                                else:
                                    self.retry_count_off += 1
                                    self.log(f"🛡 Battery Charged ({porcentaje}%). Turning OFF plug (Attempt {self.retry_count_off}/3).")
                                    await plug.async_turn_off()
                                    if not await self._refresh_plug_state(plug):
                                        self.last_confirmed_command = "off"
                                        self.armed_for_off = False
                                        self.retry_count_off = 0
                                    else:
                                        self.log("❌ Could not confirm plug turned OFF. Will retry.", is_error=True)
                            else:
                                self.log(f"🛡 Battery Charged ({porcentaje}%). Plug was already OFF externally.")
                                self.last_confirmed_command = "off"
                                self.armed_for_off = False
                                self.retry_count_off = 0
                
                except Exception as loop_e:
                    self.log(f"❌ Monitor loop iteration error: {loop_e}", is_error=True)
                    for _ in range(5):
                        if not self.running: break
                        await asyncio.sleep(1)
                    continue
                
                for _ in range(self.cfg.config.get("check_time", 60)):
                    if not self.running: break
                    await asyncio.sleep(1)
            
            # Normal Exit
            self.set_status("Stopped", "gray")

        except Exception as e:
            self.log(f"❌ Fatal monitor error: {e}", is_error=True)
            self.set_status("Monitor Error", "red")
        finally:
            self.running = False
            try:
                if manager: manager.close()
                if http_api_client: await http_api_client.async_logout()
            except Exception: pass
