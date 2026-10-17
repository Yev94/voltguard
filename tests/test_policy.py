import math
import unittest
from types import SimpleNamespace
from src.charge_policy import desired_action


def battery(percent, plugged):
    return SimpleNamespace(percent=percent, power_plugged=plugged)


class ChargePolicyTests(unittest.TestCase):
    def test_thresholds_and_hysteresis(self):
        for percent, plugged, expected in [(39,False,'on'),(40,False,'on'),(41,False,None),
                                            (94,True,None),(95,True,'off'),(100,True,'off'),
                                            (40,True,None),(95,False,None),(65,False,None)]:
            with self.subTest(percent=percent, plugged=plugged):
                self.assertEqual(desired_action(battery(percent,plugged)),expected)

    def test_missing_invalid_or_unknown_telemetry_never_switches(self):
        for sample in [None,battery(20,None),battery(100,None),battery(math.nan,False),
                       battery(-1,False),battery(101,True),battery('40',False)]:
            self.assertIsNone(desired_action(sample))
        self.assertIsNone(desired_action(battery(30,False),95,40))
