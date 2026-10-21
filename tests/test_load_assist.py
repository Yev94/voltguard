import json
import asyncio
import math
from pathlib import Path
import subprocess
import tempfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from meross_iot.model.enums import OnlineStatus
from src.battery_backend import BatteryBackend
from src.config_manager import ConfigManager
from src.load_policy import LOAD_DEFAULTS, PowerPolicy, validate_load_settings
from src.load_report import summarize
from src.load_sensors import LoadSample, LoadSampler
from src.telemetry import TelemetryRecorder


def battery(percent=60, ac=False):
    return SimpleNamespace(percent=percent, power_plugged=ac)


def settings(**changes):
    return {"min_bat": 40, "max_bat": 95, **LOAD_DEFAULTS, "load_enabled": True, **changes}


class LoadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = PowerPolicy()
        self.cfg = settings()

    def tick(self, at, cpu=10, gpu=5, percent=60, ac=False):
        return self.policy.decide(battery(percent, ac), LoadSample(at, cpu, gpu), self.cfg, at)

    def activate(self, cpu=10, gpu=60):
        for at in range(0, 16, 5):
            result = self.tick(at, cpu, gpu)
        self.assertEqual(result.action, "on")
        self.policy.confirm_on(15)

    def test_either_gpu_or_cpu_sustained_load_qualifies_and_short_bursts_do_not(self):
        for cpu, gpu in ((10, 50), (75, 5)):
            self.setUp()
            for at in (0, 5, 10):
                self.assertIsNone(self.tick(at, cpu, gpu).action)
            self.assertEqual(self.tick(15, cpu, gpu).action, "on")
        self.setUp()
        self.tick(0, gpu=99); self.tick(5, gpu=99); self.tick(10, gpu=0)
        self.assertIsNone(self.tick(15, gpu=99).action)

    def test_high_load_keeps_ac_above_battery_maximum_then_waits_both_timers(self):
        self.activate()
        self.assertIsNone(self.tick(20, gpu=80, percent=100, ac=True).action)
        for at in range(25, 315, 5):
            self.assertIsNone(self.tick(at, percent=100, ac=True).action)
        decision = self.tick(315, percent=100, ac=True)
        self.assertEqual((decision.action, decision.reason), ("off", "load_released"))

    def test_idle_requires_both_metrics_and_strict_lower_thresholds(self):
        self.activate()
        for at in range(20, 400, 5):
            self.assertIsNone(self.tick(at, cpu=35, gpu=19, ac=True).action)
        self.assertTrue(self.policy.active)
        for at in range(400, 580, 5):
            self.assertIsNone(self.tick(at, ac=True).action)
        self.assertEqual(self.tick(580, ac=True).action, "off")

    def test_minimum_battery_cycle_wins_over_idle_release(self):
        for at in range(0, 16, 5):
            self.tick(at, gpu=80, percent=20)
        for at in range(20, 340, 5):
            self.assertIsNone(self.tick(at, percent=60, ac=True).action)
        self.assertEqual(self.tick(340, percent=95, ac=True).action, "off")

    def test_minimum_on_time_starts_at_acknowledged_power_not_qualification(self):
        for at in range(0, 16, 5):
            self.tick(at, gpu=80)
        # A delayed relay/cloud acknowledgement must not consume the minimum powered time.
        for at in range(20, 100, 5):
            self.assertEqual(self.tick(at).action, "on")
        self.policy.confirm_on(100)
        for at in range(100, 400, 5):
            self.assertIsNone(self.tick(at, ac=True).action)
        self.assertEqual(self.tick(400, ac=True).action, "off")

    def test_unknown_and_stale_sensors_do_not_mean_idle(self):
        self.activate()
        for at in range(20, 400, 5):
            self.assertIsNone(self.tick(at, cpu=None, gpu=None, ac=True).action)
        self.assertTrue(self.policy.active)
        self.assertIsNone(self.policy.decide(battery(100, True), LoadSample(0, 0, 0), self.cfg, 400).action)
        self.assertTrue(self.policy.active)

    def test_sampling_gap_and_unknown_battery_reset_qualification(self):
        self.tick(0, gpu=90); self.tick(5, gpu=90)
        self.assertIsNone(self.tick(100, gpu=90).action)
        self.policy.decide(None, LoadSample(105, 10, 90), self.cfg, 105)
        self.assertIsNone(self.tick(110, gpu=90).action)
        self.assertIsNone(self.tick(120, gpu=90).action)
        self.assertEqual(self.tick(125, gpu=90).action, "on")
        for value in (None, math.nan, -1, 101):
            self.assertIsNone(self.tick(130, percent=value).action)

    def test_new_spike_cancels_pending_disconnect_and_requires_new_idle_period(self):
        self.activate()
        for at in range(20, 316, 5):
            result = self.tick(at, ac=True)
        self.assertEqual(result.action, "off")
        self.assertIsNone(self.tick(316, gpu=80, ac=True).action)
        self.assertIsNone(self.tick(320, ac=True).action)
        for at in range(325, 500, 5):
            self.assertIsNone(self.tick(at, ac=True).action)
        self.assertEqual(self.tick(500, ac=True).action, "off")

    def test_disabled_mode_retains_original_battery_policy(self):
        self.cfg["load_enabled"] = False
        self.assertEqual(self.tick(0, gpu=99, percent=95, ac=True).action, "off")
        self.assertIsNone(self.tick(15, gpu=99).action)

    def test_bad_settings_are_rejected_and_existing_profiles_stay_opt_in(self):
        for changes in ({"load_gpu_off": 50}, {"load_cpu_on": math.nan},
                        {"load_on_seconds": 0}, {"load_off_seconds": 2},
                        {"load_enabled": "false"}, {"load_min_on_seconds": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_load_settings(settings(**changes))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"schema_version": 2, "min_bat": 20, "max_bat": 95}))
            cfg = ConfigManager(path)
            self.assertFalse(cfg.config["load_enabled"])
            cfg.config.update(settings(min_bat=20))
            self.assertTrue(cfg.raw_save())
            loaded = ConfigManager(path)
            self.assertTrue(loaded.config["load_enabled"])
            self.assertEqual(loaded.config["min_bat"], 20)


class LoadBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0
        self.cfg = SimpleNamespace(config=settings(), is_confirmed=lambda: True)
        self.backend = BatteryBackend(self.cfg, MagicMock(), MagicMock(), clock=lambda: self.now)
        self.backend.running = True
        self.dev = SimpleNamespace(online_status=OnlineStatus.ONLINE, async_update=AsyncMock(),
                                   is_on=MagicMock(return_value=False), async_turn_on=AsyncMock(), async_turn_off=AsyncMock())

    async def tick(self, at, gpu, percent=60, ac=False):
        self.now = at
        self.backend._sample = LoadSample(at, 10, gpu)
        with patch("src.battery_backend.psutil.sensors_battery", return_value=battery(percent, ac)):
            await self.backend._poll_once(self.dev)

    async def test_real_policy_path_sends_load_on_and_logs_acknowledged_reason(self):
        for at in (0, 5, 10):
            await self.tick(at, 90)
        self.dev.async_turn_on.assert_not_awaited()
        self.dev.is_on.side_effect = [False, True]
        await self.tick(15, 90)
        self.dev.async_turn_on.assert_awaited_once()
        self.dev.async_turn_off.assert_not_awaited()
        self.assertIn("load_active", self.backend.log.call_args.args[0])

    async def test_load_spike_during_network_read_cancels_idle_off(self):
        for at in (0, 5, 10, 15):
            self.backend.policy.decide(battery(), LoadSample(at, 10, 90), self.cfg.config, at)
        self.backend.policy.confirm_on(15)
        for at in range(20, 315, 5):
            self.backend.policy.decide(battery(60, True), LoadSample(at, 10, 5), self.cfg.config, at)
        self.dev.is_on.return_value = True
        async def spike():
            self.backend._sample = LoadSample(315, 10, 90)
        self.dev.async_update.side_effect = spike
        await self.tick(315, 5, ac=True)
        self.dev.async_turn_off.assert_not_awaited()

    async def test_stop_during_network_read_cancels_load_command(self):
        for at in (0, 5, 10):
            await self.tick(at, 90)
        async def stop():
            self.backend.running = False
        self.dev.async_update.side_effect = stop
        await self.tick(15, 90)
        self.dev.async_turn_on.assert_not_awaited()

    async def test_cloud_failure_does_not_stop_local_samples_and_stop_closes_history(self):
        recorder = MagicMock()
        @asynccontextmanager
        async def offline():
            raise ConnectionError("fixture offline")
            yield  # pragma: no cover
        async def pause(seconds):
            await asyncio.sleep(0.01)
            if recorder.record.called:
                self.backend.running = False
        self.backend.connection = offline
        self.backend._sleep = pause
        with patch.dict("os.environ", {"VOLTGUARD_DEMO": "0"}), \
             patch("src.battery_backend.TelemetryRecorder", return_value=recorder), \
             patch("src.battery_backend.LoadSampler") as sampler, \
             patch("src.battery_backend.psutil.sensors_battery", return_value=battery()):
            sampler.return_value.sample.return_value = LoadSample(0, 10, 50)
            await asyncio.wait_for(self.backend.monitor_loop(), 2)
        self.assertTrue(any(call.args[0] == "sample" for call in recorder.record.call_args_list))
        recorder.close.assert_called_once()
        self.dev.async_turn_on.assert_not_awaited()
        self.dev.async_turn_off.assert_not_awaited()


class LoadSensorsAndHistoryTests(unittest.TestCase):
    def test_initial_cpu_value_is_discarded_and_busiest_nvidia_gpu_is_used(self):
        with patch("src.load_sensors.shutil.which", return_value="fake-nvidia"), \
             patch("src.load_sensors.psutil.cpu_percent", side_effect=[0, 80]), \
             patch("src.load_sensors.psutil.cpu_freq", return_value=None), \
             patch("src.load_sensors.subprocess.run", return_value=SimpleNamespace(stdout="5, 210, N/A\n90, 1500, 80\n")) as run:
            sampler = LoadSampler()
            self.assertIsNone(sampler.sample().cpu)
            sample = sampler.sample()
            self.assertEqual((sample.cpu, sample.gpu, sample.gpu_mhz, sample.gpu_watts), (80, 90, 1500, 80))
            self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_gpu_timeout_is_unknown_and_backed_off_not_zero(self):
        with patch("src.load_sensors.shutil.which", return_value="fake-nvidia"), \
             patch("src.load_sensors.subprocess.run", side_effect=subprocess.TimeoutExpired("fake", 2)) as run:
            sampler = LoadSampler()
            self.assertEqual(sampler._gpu(100), (None, None, None, "unavailable"))
            self.assertEqual(sampler._gpu(105), (None, None, None, "unavailable"))
            run.assert_called_once()

    def test_linux_amd_sysfs_and_invalid_sensor_values(self):
        with tempfile.TemporaryDirectory() as folder, patch("src.load_sensors.shutil.which", return_value=None):
            path = Path(folder) / "gpu_busy_percent"
            sampler = LoadSampler(); sampler._amd = (path,)
            path.write_text("82")
            self.assertEqual(sampler._gpu(0), (82, None, None, "amd_sysfs"))
            path.write_text("nan")
            self.assertEqual(sampler._gpu(5), (None, None, None, "unavailable"))

    def test_bounded_history_and_report_contain_only_sensors_and_controller_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "load.jsonl"
            recorder = TelemetryRecorder(path)
            recorder.handler.maxBytes = 500
            for index in range(8):
                recorder.record("sample", LoadSample(index, 10, 60, 1200, 50), battery(ac=bool(index % 2)), "active", "load_active")
            recorder.record("command_confirmed", LoadSample(9, 10, 60), battery(), "active", "load_active", "on")
            recorder.record("stutter_mark", LoadSample(10, 10, 60), battery(math.nan), "active", "load_active")
            recorder.close()
            self.assertLessEqual(len(list(Path(folder).iterdir())), 4)
            for file in Path(folder).iterdir():
                for row in file.read_text().splitlines():
                    data = json.loads(row)
                    self.assertNotIn("email", data); self.assertNotIn("uuid", data); self.assertNotIn("password", data)
            with path.open("a") as stream:
                stream.write('{"time":')
            report = summarize(path)
            self.assertIn("confirmed ON 1 / OFF 0", report)
            self.assertIn("Stutter marks: 1", report)
            self.assertIn("Incomplete/invalid history lines skipped: 1", report)
