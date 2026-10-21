"""Single asynchronous controller. Discovery never changes a relay."""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

import psutil
from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager
from meross_iot.model.enums import OnlineStatus
from src.presentation import battery_percentage
from src.load_policy import PowerPolicy, SAMPLE_SECONDS, validate_load_settings
from src.load_sensors import LoadSampler
from src.telemetry import TelemetryRecorder


class BatteryBackend:
    def __init__(self, config_manager, log_callback, status_callback, metrics_callback=None, *, clock=None):
        self.cfg = config_manager
        self.log = log_callback
        self.set_status = status_callback
        self.running = False
        self.last_confirmed_command = None
        self._attempts = {"on": 0, "off": 0}
        self._last_action_at = float("-inf")
        self._last_log_at = float("-inf")
        self.set_metrics = metrics_callback or (lambda text: None)
        self.policy = PowerPolicy()
        self._sample = None
        self._telemetry = None
        self._reason = "battery"
        self._last_decision = None
        self._sample_battery = None
        self._now = clock or time.monotonic

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
        decision = self.policy.decide(battery, self._sample, self.cfg.config, self._now())
        action = decision.action
        self._reason = decision.reason
        key = (action, decision.reason, self.policy.phase)
        if key != self._last_decision:
            self._record("decision", battery, action)
            self._last_decision = key
        if battery is None or battery.power_plugged is None:
            self.set_status("Battery reading unavailable; waiting", "orange")
            return
        if action is None:
            self._attempts = {"on": 0, "off": 0}
        if self._now() - self._last_log_at >= 120:
            self.log(f"Battery: {battery_percentage(battery.percent)} | AC: {battery.power_plugged}")
            self._last_log_at = self._now()
        suffix = " · Load assist" if self.policy.active else ""
        self.set_status(f"Monitoring · {battery_percentage(battery.percent)} · {self.cfg.config['min_bat']}–{self.cfg.config['max_bat']}%{suffix}", "#2DB87A")
        if action is None or self._attempts[action] >= 3 or self._now() - self._last_action_at < 30:
            return
        self._require_confirmation()
        state = await self._refresh_plug_state(plug)
        wanted = action == "on"
        if state == wanted:
            self.last_confirmed_command = action
            if wanted and self.policy.powered_since is None:
                self.policy.confirm_on(self._now())
            return
        # Network work may take time. Recheck battery immediately before the command.
        current = psutil.sensors_battery()
        latest = self.policy.decide(current, self._sample, self.cfg.config, self._now())
        if not self.running or latest.action != action:
            return
        self._attempts[action] += 1
        self._last_action_at = self._now()
        command = plug.async_turn_on if wanted else plug.async_turn_off
        self._reason = latest.reason
        self._record("command_requested", current, action)
        try:
            await asyncio.wait_for(command(), timeout=20)
            if await self._refresh_plug_state(plug) != wanted:
                raise RuntimeError("Relay acknowledgement failed")
        except Exception:
            self._record("command_failed", current, action)
            raise
        self.last_confirmed_command = action
        if wanted:
            self.policy.confirm_on(self._now())
        self._record("command_confirmed", current, action)
        cause = "Load assist" if latest.reason.startswith("load_") else "Battery threshold reached"
        self.log(f"{cause}; plug {action.upper()} confirmed ({latest.reason}).")

    def _record(self, event, battery, action=None):
        recorder = self._telemetry
        if recorder is not None:
            try:
                recorder.record(event, self._sample, battery, self.policy.phase, self._reason, action)
            except (OSError, ValueError, TypeError):
                self.log("Local telemetry unavailable; controller remains active.", is_error=True)
                self._telemetry.close()
                self._telemetry = None

    def mark_stutter(self):
        if self._telemetry is None:
            self.log("Enable load assist and start monitoring to record a stutter mark.")
            return
        self._record("stutter_mark", self._sample_battery)
        self.log("Stutter marked in local sensor history; no relay command sent.")

    async def _sample_loop(self):
        sampler = LoadSampler()
        # psutil's nonblocking CPU baselines are per-thread. Keep all calls on one worker.
        worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voltguard-sensors")
        try:
            await self._sample_with_worker(sampler, worker)
        finally:
            worker.shutdown(wait=False, cancel_futures=True)

    async def _sample_with_worker(self, sampler, worker):
        last_summary = float("-inf")
        while self.running:
            started = time.monotonic()
            try:
                self._sample = await asyncio.get_running_loop().run_in_executor(worker, sampler.sample)
                battery = psutil.sensors_battery()
                self._sample_battery = battery
                self._record("sample", battery)
                def pct(value):
                    return "n/a" if value is None else f"{value:.0f}%"
                text = f"CPU {pct(self._sample.cpu)} · GPU {pct(self._sample.gpu)} · {self.policy.phase}"
                self.set_metrics(text)
                if started - last_summary >= 60:
                    self.log(f"Load: {text} | AC: {battery.power_plugged if battery else 'unknown'}")
                    last_summary = started
            except (OSError, RuntimeError):
                self._sample = None
                self.set_metrics("CPU/GPU sensors unavailable; waiting")
            await self._sleep(max(1, SAMPLE_SECONDS - (time.monotonic() - started)))

    async def _sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while self.running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(1, remaining))

    async def monitor_loop(self):
        self._require_confirmation()
        settings = validate_load_settings(self.cfg.config)
        self.policy = PowerPolicy()
        self._last_decision = None
        self._sample = None
        sample_task = None
        if settings["load_enabled"]:
            if os.environ.get("VOLTGUARD_DEMO") != "1":
                try:
                    self._telemetry = TelemetryRecorder()
                except OSError:
                    self.log("Local telemetry unavailable; controller remains active.", is_error=True)
            self.log("Load assist enabled: CPU or GPU high; both idle to release. Local telemetry every 5s.")
            sample_task = asyncio.create_task(self._sample_loop())
        try:
            await self._monitor_connections()
        finally:
            if sample_task:
                sample_task.cancel()
                await asyncio.gather(sample_task, return_exceptions=True)
            if self._telemetry:
                self._telemetry.close()
                self._telemetry = None
            self.set_metrics("Stopped · sensor history retained locally")

    async def _monitor_connections(self):
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
                        interval = SAMPLE_SECONDS if self.cfg.config.get("load_enabled") else max(5, self.cfg.config["check_time"])
                        await self._sleep(interval)
            except Exception as error:
                self.log(f"Connection/control unavailable ({type(error).__name__}); retrying in {backoff}s.", is_error=True)
                self.set_status("Connection unavailable; waiting", "orange")
                await self._sleep(backoff)
                backoff = min(backoff * 2, 120)
        self.set_status("Stopped", "gray")
