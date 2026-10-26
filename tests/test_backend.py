import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from meross_iot.model.enums import OnlineStatus
from src.battery_backend import BatteryBackend


def sample(percent, plugged):
    return SimpleNamespace(percent=percent,power_plugged=plugged)


def plug(on=False):
    return SimpleNamespace(uuid='selected',name='Example charging plug',type='mss315',channels=[0],
        online_status=OnlineStatus.ONLINE,is_on=MagicMock(return_value=on),async_update=AsyncMock(),
        async_turn_on=AsyncMock(),async_turn_off=AsyncMock())


class BackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg=SimpleNamespace(config={'email':'test@example.invalid','uuid':'selected','min_bat':40,'max_bat':95,'check_time':60},
                                 password='test-only',is_confirmed=lambda:True)
        self.log=MagicMock()
        self.backend=BatteryBackend(self.cfg,self.log,MagicMock())
        self.backend.running=True

    async def test_boundary_sends_one_command_then_verifies_it(self):
        dev=plug()
        dev.is_on.side_effect=[False,True]
        with patch('src.battery_backend.read_battery',return_value=sample(40,False)):
            await self.backend._poll_once(dev)
            await self.backend._poll_once(dev)
        dev.async_turn_on.assert_awaited_once()
        dev.async_turn_off.assert_not_awaited()

    async def test_upper_boundary_cuts_only_when_ac_is_known_connected(self):
        dev=plug(True)
        dev.is_on.side_effect=[True,False]
        with patch('src.battery_backend.read_battery',return_value=sample(95,True)):
            await self.backend._poll_once(dev)
        dev.async_turn_off.assert_awaited_once()

    async def test_unknown_battery_offline_and_unconfirmed_devices_do_not_switch(self):
        for reading in [None,sample(100,None)]:
            dev=plug()
            with patch('src.battery_backend.read_battery',return_value=reading):
                await self.backend._poll_once(dev)
            expected = 'Battery reading unavailable; waiting' if reading is None else 'Charger status unavailable · 100 %; waiting'
            self.backend.set_status.assert_called_with(expected, 'orange')
            dev.async_turn_on.assert_not_awaited()
            dev.async_turn_off.assert_not_awaited()
        dev=plug();dev.online_status=OnlineStatus.OFFLINE
        with patch('src.battery_backend.read_battery',return_value=sample(40,False)):
            with self.assertRaises(ConnectionError): await self.backend._poll_once(dev)
        dev.async_turn_on.assert_not_awaited()
        self.cfg.is_confirmed=lambda:False
        with self.assertRaises(ValueError): await self.backend.async_manual_control(True)

    async def test_battery_is_rechecked_after_network_delay(self):
        dev=plug()
        with patch('src.battery_backend.read_battery',side_effect=[sample(40,False),sample(70,False)]):
            await self.backend._poll_once(dev)
        dev.async_turn_on.assert_not_awaited()

    async def test_integer_presentation_does_not_round_the_control_reading(self):
        dev=plug(True)
        reading=sample(94.63,True)
        with patch('src.battery_backend.read_battery',return_value=reading):
            await self.backend._poll_once(dev)
        self.log.assert_called_once_with('Battery: 95 % | AC: True')
        self.assertIn('Monitoring · 95 % ·',self.backend.set_status.call_args.args[0])
        self.assertEqual(reading.percent,94.63)
        dev.async_turn_off.assert_not_awaited()
        dev.async_update.assert_not_awaited()

    async def test_readonly_discovery_binds_exact_uuid_and_cleans_up(self):
        selected=plug();other=plug();other.uuid='other'
        http=SimpleNamespace(async_logout=AsyncMock())
        manager=SimpleNamespace(async_init=AsyncMock(),async_device_discovery=AsyncMock(),
                                find_devices=MagicMock(return_value=[other,selected]),close=MagicMock())
        with patch('src.battery_backend.MerossHttpClient.async_from_user_password',AsyncMock(return_value=http)),patch('src.battery_backend.MerossManager',return_value=manager):
            info=await self.backend.async_test_plug()
        self.assertEqual(info['uuid'],'selected')
        selected.async_turn_on.assert_not_awaited();selected.async_turn_off.assert_not_awaited()
        other.async_update.assert_not_awaited()
        manager.close.assert_called_once();http.async_logout.assert_awaited_once()

    async def test_missing_target_does_not_pick_the_first_device(self):
        dev=plug();dev.uuid='other'
        http=SimpleNamespace(async_logout=AsyncMock())
        manager=SimpleNamespace(async_init=AsyncMock(),async_device_discovery=AsyncMock(),find_devices=MagicMock(return_value=[dev]),close=MagicMock())
        with patch('src.battery_backend.MerossHttpClient.async_from_user_password',AsyncMock(return_value=http)),patch('src.battery_backend.MerossManager',return_value=manager):
            with self.assertRaises(ValueError): await self.backend.inspect_plug()
        dev.async_turn_on.assert_not_awaited();dev.async_turn_off.assert_not_awaited()
        manager.close.assert_called_once();http.async_logout.assert_awaited_once()

    async def test_failed_relay_commands_are_capped(self):
        dev=plug(False)
        with patch('src.battery_backend.read_battery',return_value=sample(40,False)):
            for _ in range(3):
                self.backend._last_action_at=float('-inf')
                with self.assertRaises(RuntimeError):
                    await self.backend._poll_once(dev)
            self.backend._last_action_at=float('-inf')
            await self.backend._poll_once(dev)
        self.assertEqual(dev.async_turn_on.await_count,3)
