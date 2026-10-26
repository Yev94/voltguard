from collections import namedtuple
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psutil
from src.battery_sensors import read_battery

Battery = namedtuple("Battery", "percent secsleft power_plugged")


class BatterySensorTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.reading = Battery(98.27249676207154, psutil.POWER_TIME_UNKNOWN, None)
        for target, value in (
            ("src.battery_sensors.POWER_SUPPLY_PATH", self.root),
            ("src.battery_sensors.sys", SimpleNamespace(platform="linux")),
        ):
            mocked = patch(target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch("src.battery_sensors.psutil.sensors_battery", side_effect=lambda: self.reading)
        mocked.start()
        self.addCleanup(mocked.stop)

    def supply(self, name, kind, online=None, scope=None, status=None):
        folder = self.root / name
        folder.mkdir()
        for field, value in (("type", kind), ("online", online), ("scope", scope), ("status", status)):
            if value is not None:
                (folder / field).write_text(f"{value}\n")
        return folder

    def test_adp1_recognized_when_battery_is_not_charging(self):
        self.supply("BAT0", "Battery", status="Not charging")
        self.supply("ADP1", "Mains", 1)
        result = read_battery()
        self.assertIs(result.power_plugged, True)
        self.assertEqual(result.percent, self.reading.percent)
        self.assertEqual(result.secsleft, psutil.POWER_TIME_UNLIMITED)

    def test_unplugged_adapter_is_false(self):
        self.supply("ADP1", "Mains", 0)
        self.assertIs(read_battery().power_plugged, False)

    def test_online_usb_supply_wins_over_offline_mains(self):
        self.supply("ADP1", "Mains", 0)
        self.supply("ucsi-source-psy-USBC000:001", "USB", 1, scope="System")
        self.assertIs(read_battery().power_plugged, True)

    def test_all_known_external_supplies_offline(self):
        self.supply("ADP1", "Mains", 0)
        self.supply("usb-c", "USB_PD", 0)
        self.assertIs(read_battery().power_plugged, False)

    def test_battery_and_device_supplies_do_not_imply_laptop_ac(self):
        self.supply("BAT0", "Battery", 1, status="Not charging")
        self.supply("peripheral", "USB", 1, scope="Device")
        self.supply("unknown", "Unknown", 1)
        self.assertIs(read_battery(), self.reading)

    def test_missing_or_invalid_online_remains_unknown(self):
        supply = self.supply("ADP1", "Mains")
        for value in (None, "", "2", "unknown"):
            with self.subTest(value=value):
                if value is not None:
                    (supply / "online").write_text(value)
                self.assertIs(read_battery(), self.reading)

    def test_one_offline_supply_cannot_resolve_an_unreadable_second_supply(self):
        self.supply("ADP1", "Mains", 0)
        self.supply("usb-c", "USB_C")
        self.assertIs(read_battery(), self.reading)

    def test_connected_supply_resolves_ac_even_if_another_is_unreadable(self):
        self.supply("usb-c", "USB_C")
        self.supply("ADP1", "Mains", 1)
        self.assertIs(read_battery().power_plugged, True)

    def test_unreadable_type_does_not_count_as_disconnected(self):
        self.supply("ADP1", "Mains", 0)
        (self.root / "unreadable").mkdir()
        self.assertIs(read_battery(), self.reading)

    def test_absent_sysfs_never_implies_disconnected(self):
        for folder in (self.root, self.root / "missing"):
            with self.subTest(folder=folder), patch("src.battery_sensors.POWER_SUPPLY_PATH", folder):
                self.assertIs(read_battery(), self.reading)

    def test_known_psutil_status_is_preserved(self):
        self.supply("ADP1", "Mains", 1)
        for plugged in (True, False):
            self.reading = self.reading._replace(power_plugged=plugged)
            with self.subTest(plugged=plugged):
                self.assertIs(read_battery(), self.reading)

    def test_missing_battery_is_not_invented_from_ac(self):
        self.supply("ADP1", "Mains", 1)
        self.reading = None
        self.assertIsNone(read_battery())

    def test_windows_keeps_psutil_reading_without_linux_fallback(self):
        self.supply("ADP1", "Mains", 1)
        with patch("src.battery_sensors.sys", SimpleNamespace(platform="win32")):
            self.assertIs(read_battery(), self.reading)

    def test_disconnected_reading_cannot_retain_unlimited_time(self):
        self.supply("ADP1", "Mains", 0)
        self.reading = self.reading._replace(secsleft=psutil.POWER_TIME_UNLIMITED)
        self.assertEqual(read_battery().secsleft, psutil.POWER_TIME_UNKNOWN)
