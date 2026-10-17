"""Single asynchronous controller. Discovery never changes a relay."""
import asyncio
import time
from contextlib import asynccontextmanager

import psutil
from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager
from meross_iot.model.enums import OnlineStatus
from src.charge_policy import desired_action
from src.presentation import battery_percentage


class BatteryBackend:
    def __init__(self, config_manager, log_callback, status_callback):
        self.cfg = config_manager
        self.log = log_callback
        self.set_status = status_callback
        self.running = False
        self.last_confirmed_command = None
        self._attempts = {"on": 0, "off": 0}
        self._last_action_at = float("-inf")
        self._last_log_at = float("-inf")

    @asynccontextmanager
    async def connection(self, *, require_uuid=True):
        http_client = None
        manager = None
        try:
            if not self.cfg.config.get("email") or not self.cfg.password:
                raise ValueError("Meross credentials required")
            http_client = await asyncio.wait_for(MerossHttpClient.async_from_user_password(
                api_base_url="https://iotx-eu.meross.com",
                email=self.cfg.config["email"], password=self.cfg.password), timeout=30)
            manager = MerossManager(http_client=http_client)
            await asyncio.wait_for(manager.async_init(), timeout=30)
            await asyncio.wait_for(manager.async_device_discovery(), timeout=30)
            devices = manager.find_devices()
            target = self.cfg.config.get("uuid", "").strip()
            plug = next((dev for dev in devices if dev.uuid == target), None) if target else None
            if require_uuid and plug is None:
                raise ValueError("Configured plug not found")
            if plug is not None:
                if not str(plug.type).lower().startswith("mss") or not all(
                    callable(getattr(plug, name, None)) for name in ("async_turn_on", "async_turn_off", "is_on")
                ):
                    raise ValueError("Target is not a supported Meross plug")
                # Channel zero means the whole relay on single-outlet plugs. Do not guess on a strip.
                channels = getattr(plug, "channels", [])
                if channels and len(channels) > 1:
                    raise ValueError("Multi-outlet devices require an explicit channel configuration")
            yield devices, plug
        finally:
            if manager is not None:
                try:
                    manager.close()
                except Exception:
                    pass
            if http_client is not None:
                try:
                    await asyncio.wait_for(http_client.async_logout(), timeout=5)
                except Exception:
                    pass

    async def _refresh_plug_state(self, plug):
        if plug.online_status != OnlineStatus.ONLINE:
            raise ConnectionError("Plug offline")
        await asyncio.wait_for(plug.async_update(), timeout=20)
        state = plug.is_on()
        if type(state) is not bool:
            raise ValueError("Unknown relay state")
        return state

    async def inspect_plug(self):
        """Read-only lookup of the exact configured UUID. No relay commands."""
        async with self.connection() as (_, plug):
            state = await self._refresh_plug_state(plug)
            return {"name": plug.name, "type": plug.type, "uuid": plug.uuid, "on": state}

    async def async_test_plug(self):
        # The former two-second ON/OFF test is now read-only.
        return await self.inspect_plug()

    def _require_confirmation(self):
        if not self.cfg.is_confirmed():
            raise ValueError("Verify and confirm the laptop charging plug first")

    async def async_manual_control(self, turn_on):
        self._require_confirmation()
        try:
            async with self.connection() as (_, plug):
                state = await self._refresh_plug_state(plug)
                if state != turn_on:
                    command = plug.async_turn_on if turn_on else plug.async_turn_off
                    await asyncio.wait_for(command(), timeout=20)
                if await self._refresh_plug_state(plug) != turn_on:
                    raise RuntimeError("Relay acknowledgement failed")
                self.log("Manual command confirmed: " + ("ON" if turn_on else "OFF"))
        except Exception as error:
            self.log(f"Manual control unavailable ({type(error).__name__}).", is_error=True)

    async def _poll_once(self, plug):
        battery = psutil.sensors_battery()
        action = desired_action(battery, self.cfg.config["min_bat"], self.cfg.config["max_bat"])
        if battery is None or battery.power_plugged is None:
            self.set_status("Battery reading unavailable; waiting", "orange")
            return
        if action is None:
            self._attempts = {"on": 0, "off": 0}
        if time.monotonic() - self._last_log_at >= 120:
            self.log(f"Battery: {battery_percentage(battery.percent)} | AC: {battery.power_plugged}")
            self._last_log_at = time.monotonic()
        self.set_status(f"Monitoring · {battery_percentage(battery.percent)} · {self.cfg.config['min_bat']}–{self.cfg.config['max_bat']}%", "#2DB87A")
        if action is None or self._attempts[action] >= 3 or time.monotonic() - self._last_action_at < 30:
            return
        self._require_confirmation()
        state = await self._refresh_plug_state(plug)
        wanted = action == "on"
        if state == wanted:
            self.last_confirmed_command = action
            return
        # Network work may take time. Recheck battery immediately before the command.
        current = psutil.sensors_battery()
        if not self.running or desired_action(current, self.cfg.config["min_bat"], self.cfg.config["max_bat"]) != action:
            return
        self._attempts[action] += 1
        self._last_action_at = time.monotonic()
        command = plug.async_turn_on if wanted else plug.async_turn_off
        await asyncio.wait_for(command(), timeout=20)
        if await self._refresh_plug_state(plug) != wanted:
            raise RuntimeError("Relay acknowledgement failed")
        self.last_confirmed_command = action
        self.log(f"Battery threshold reached; plug {action.upper()} confirmed.")

    async def _sleep(self, seconds):
        for _ in range(int(seconds)):
            if not self.running:
                break
            await asyncio.sleep(1)

    async def monitor_loop(self):
        self._require_confirmation()
        backoff = 15
        self._attempts = {"on": 0, "off": 0}
        while self.running:
            try:
                self.set_status("Connecting to Meross…", "orange")
                async with self.connection() as (_, plug):
                    await self._refresh_plug_state(plug)
                    backoff = 15
                    self.log(f"Monitor started | Min: {self.cfg.config['min_bat']}% | Max: {self.cfg.config['max_bat']}%")
                    while self.running:
                        await self._poll_once(plug)
                        await self._sleep(max(5, self.cfg.config["check_time"]))
            except Exception as error:
                self.log(f"Connection/control unavailable ({type(error).__name__}); retrying in {backoff}s.", is_error=True)
                self.set_status("Connection unavailable; waiting", "orange")
                await self._sleep(backoff)
                backoff = min(backoff * 2, 120)
        self.set_status("Stopped", "gray")
