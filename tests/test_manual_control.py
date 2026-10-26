import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from meross_iot.model.enums import OnlineStatus
from src.battery_backend import BatteryBackend
from src.ui_app import VoltGuardApp


def battery(percent=60, ac=False):
    return SimpleNamespace(percent=percent, power_plugged=ac)


def plug(on=False):
    return SimpleNamespace(online_status=OnlineStatus.ONLINE, async_update=AsyncMock(),
                           is_on=MagicMock(return_value=on), async_turn_on=AsyncMock(), async_turn_off=AsyncMock())


class ManualBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0
        cfg = SimpleNamespace(config={"min_bat": 20, "max_bat": 95, "check_time": 60}, is_confirmed=lambda: True)
        self.backend = BatteryBackend(cfg, MagicMock(), MagicMock(), clock=lambda: self.now)
        self.backend.running = True
        self.done = MagicMock()

    async def test_manual_uses_shared_connection_and_automatic_control_resumes_after_grace(self):
        dev = plug()
        dev.is_on.side_effect = [False, True]
        self.backend.connection = MagicMock(side_effect=AssertionError("Do not open another connection"))
        self.assertTrue(self.backend.queue_manual(True, self.done))
        with patch("src.battery_backend.read_battery", return_value=battery(100, True)):
            self.assertTrue(await self.backend._process_manual(dev))
            self.now = 5
            await self.backend._poll_once(dev)
            dev.async_turn_off.assert_not_awaited()
            self.now = 31
            dev.is_on.side_effect = [True, False]
            await self.backend._poll_once(dev)
        dev.async_turn_on.assert_awaited_once()
        dev.async_turn_off.assert_awaited_once()
        self.done.assert_called_once_with(True)
        self.backend.connection.assert_not_called()
        self.assertTrue(self.backend.running)

    async def test_monitor_loop_processes_manual_then_keeps_polling_same_connection(self):
        dev = plug()
        dev.is_on.side_effect = [False, False, True]
        entered = MagicMock()
        @asynccontextmanager
        async def connected():
            entered()
            yield [], dev
        async def pause(seconds, **kwargs):
            if self.backend._poll_once.await_count:
                self.backend.running = False
            await asyncio.sleep(0)
        self.backend.connection = connected
        self.backend._sleep = pause
        self.backend._poll_once = AsyncMock()
        self.backend.queue_manual(True, self.done)
        with patch("src.battery_backend.read_battery", return_value=battery()):
            await self.backend._monitor_connections()
        self.done.assert_called_once_with(True)
        self.backend._poll_once.assert_awaited_once()
        entered.assert_called_once()
        dev.async_turn_on.assert_awaited_once()

    async def test_manual_queued_during_automatic_refresh_preempts_opposite_command(self):
        dev = plug(True)
        async def enqueue():
            dev.async_update.side_effect = None
            self.assertTrue(self.backend.queue_manual(True, self.done))
        dev.async_update.side_effect = enqueue
        with patch("src.battery_backend.read_battery", return_value=battery(100, True)):
            await self.backend._poll_once(dev)
            dev.async_turn_off.assert_not_awaited()
            await self.backend._process_manual(dev)
        dev.async_turn_off.assert_not_awaited()
        self.done.assert_called_once_with(True)

    async def test_failed_manual_ack_releases_buttons_without_stopping_monitor(self):
        dev = plug(False)
        self.backend.queue_manual(True, self.done)
        with patch("src.battery_backend.read_battery", return_value=battery()):
            await self.backend._process_manual(dev)
        self.done.assert_called_once_with(False)
        dev.async_turn_on.assert_awaited_once()
        self.assertTrue(self.backend.running)
        self.assertTrue(self.backend.queue_manual(False, MagicMock()))
        self.backend._cancel_pending_manual("fixture cleanup")

    async def test_duplicate_request_is_rejected_and_expired_request_is_not_sent(self):
        dev = plug()
        self.assertTrue(self.backend.queue_manual(True, self.done))
        self.assertFalse(self.backend.queue_manual(False, MagicMock()))
        self.now = 31
        await self.backend._process_manual(dev)
        dev.async_update.assert_not_awaited()
        dev.async_turn_on.assert_not_awaited()
        self.done.assert_called_once_with(False)
        self.assertTrue(self.backend.queue_manual(False, MagicMock()))
        self.backend._cancel_pending_manual("fixture cleanup")

    async def test_stop_or_expiry_during_network_read_cancels_unsent_manual_command(self):
        for stop in (True, False):
            self.setUp()
            dev = plug()
            async def interrupt():
                if stop:
                    self.backend.running = False
                else:
                    self.now = 31
            dev.async_update.side_effect = interrupt
            self.backend.queue_manual(True, self.done)
            await self.backend._process_manual(dev)
            dev.async_turn_on.assert_not_awaited()
            self.done.assert_called_once_with(False)

    async def test_network_failure_discards_pending_request_and_monitor_remains_enabled(self):
        self.backend.queue_manual(True, self.done)
        @asynccontextmanager
        async def offline():
            raise ConnectionError("fixture offline")
            yield  # pragma: no cover
        async def pause(seconds, **kwargs):
            self.assertTrue(self.backend.running)
            self.done.assert_called_once_with(False)
            self.backend.running = False  # End this test after the first reconnect backoff.
        self.backend.connection = offline
        self.backend._sleep = pause
        await self.backend._monitor_connections()
        self.done.assert_called_once_with(False)
        self.assertFalse(await self.backend._process_manual(plug()))

    async def test_manual_request_wakes_a_long_automatic_check_interval(self):
        self.backend.queue_manual(True, self.done)
        await asyncio.wait_for(self.backend._sleep(60, wake_for_manual=True), 0.1)
        self.done.assert_not_called()
        self.backend._cancel_pending_manual("fixture cleanup")

    async def test_stop_clears_request_so_it_cannot_run_after_a_later_restart(self):
        self.backend.queue_manual(True, self.done)
        self.backend.running = False
        self.backend.connection = MagicMock(side_effect=AssertionError("Stopped monitor must not connect"))
        await self.backend.monitor_loop()
        self.done.assert_called_once_with(False)
        self.backend.running = True
        self.assertFalse(await self.backend._process_manual(plug()))

    async def test_direct_manual_connection_is_blocked_while_monitor_owns_connection(self):
        self.backend.connection = MagicMock()
        self.assertFalse(await self.backend.async_manual_control(True))
        self.backend.connection.assert_not_called()

    async def test_standalone_manual_control_still_works_without_starting_monitor(self):
        self.backend.running = False
        dev = plug(); dev.is_on.side_effect = [False, True]
        @asynccontextmanager
        async def connected():
            yield [], dev
        self.backend.connection = connected
        with patch("src.battery_backend.read_battery", return_value=battery()):
            self.assertTrue(await self.backend.async_manual_control(True))
        self.assertFalse(self.backend.running)
        dev.async_turn_on.assert_awaited_once()


class ManualUITests(unittest.TestCase):
    def app(self):
        app = VoltGuardApp.__new__(VoltGuardApp)
        app.demo = False; app.testing_plug = False; app.manual_pending = False
        app.backend = SimpleNamespace(running=True, queue_manual=MagicMock(return_value=True))
        app.cfg_manager = SimpleNamespace(is_confirmed=lambda: True, raw_save=MagicMock())
        app.log = MagicMock(); app.post_ui = MagicMock(); app.validate_inputs = MagicMock()
        app.set_status = MagicMock(); app.root = MagicMock()
        for name in ("btn_on", "btn_off", "btn_start", "btn_stop", "btn_test", "btn_save",
                     "entry_email", "entry_password", "entry_uuid", "entry_min", "entry_max", "entry_time",
                     "chk_minimized", "chk_auto", "chk_login", "chk_load"):
            setattr(app, name, MagicMock())
        app.load_entries = {}
        return app

    def test_running_monitor_keeps_manual_buttons_enabled(self):
        app = self.app()
        app.set_gui_state(True)
        self.assertEqual(app.btn_on.configure.call_args.kwargs["state"], "normal")
        self.assertEqual(app.btn_on.configure.call_args.kwargs["command"], app.manual_turn_on)
        self.assertEqual(app.btn_off.configure.call_args.kwargs["state"], "normal")
        self.assertEqual(app.btn_off.configure.call_args.kwargs["command"], app.manual_turn_off)

    def test_manual_queues_without_resaving_credentials_or_stopping_monitor_and_restores_buttons(self):
        app = self.app()
        app._manual_action(True)
        self.assertTrue(app.manual_pending)
        self.assertTrue(app.backend.running)
        app.validate_inputs.assert_not_called()
        app.cfg_manager.raw_save.assert_not_called()
        self.assertEqual(app.btn_on.configure.call_args.kwargs["state"], "disabled")
        completion = app.backend.queue_manual.call_args.args[1]
        completion(True)
        app.post_ui.assert_called_once_with(app._finish_manual, True, False)
        app._finish_manual(True, False)
        self.assertFalse(app.manual_pending)
        self.assertTrue(app.backend.running)
        app.set_status.assert_not_called()
        self.assertEqual(app.btn_on.configure.call_args.kwargs["state"], "normal")

    def test_monitor_cannot_start_during_standalone_manual_work(self):
        app = self.app(); app.backend.running = False; app.manual_pending = True
        app.start_monitor()
        app.validate_inputs.assert_not_called()

    def test_login_auto_start_waits_for_manual_work_instead_of_creating_a_second_connection(self):
        app = self.app(); app.backend.running = False; app.manual_pending = True
        app.closing = False; app._auto_start_requested = True
        app._auto_start()
        app.root.after.assert_called_once_with(250, app._auto_start)
        self.assertTrue(app._auto_start_requested)
