import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from keyring.backend import KeyringBackend
from src.config_manager import ConfigManager
from src.autostart import configure_autostart, desktop_entry, launch_command
from src.paths import config_dir
from filelock import FileLock, Timeout


class SecureTestKeyring(KeyringBackend):
    priority = 1
    def get_password(self, service, username): return None
    def set_password(self, service, username, password): pass
    def delete_password(self, service, username): pass


class SettingsTests(unittest.TestCase):
    def test_legacy_policy_migrates_without_activating_unconfirmed_plug(self):
        with tempfile.TemporaryDirectory() as folder, patch('keyring.get_password',return_value='test-secret'):
            path=Path(folder)/'config.json'
            original=json.dumps({'email':'example@example.invalid','uuid':'test-plug','min_bat':20,'max_bat':95,'password':'legacy-secret','start_minimized':True})
            path.write_text(original)
            cfg=ConfigManager(path)
            self.assertEqual((cfg.config['min_bat'],cfg.config['max_bat']),(40,95))
            self.assertFalse(cfg.config['start_minimized'])
            self.assertFalse(cfg.is_confirmed())
            self.assertEqual(path.read_text(),original)
            with patch('keyring.get_keyring',return_value=SecureTestKeyring()),patch('keyring.set_password') as save:
                self.assertTrue(cfg.raw_save())
                save.assert_called_once_with('VoltGuard','example@example.invalid','test-secret')
            written=json.loads(path.read_text())
            self.assertNotIn('password',written)
            self.assertNotIn('legacy-secret',path.read_text())

    def test_current_settings_are_preserved_and_failed_vault_does_not_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps({'schema_version':2,'min_bat':45,'max_bat':90}))
            cfg=ConfigManager(path)
            self.assertEqual(cfg.config['min_bat'],45)
            before=path.read_text()
            with patch('keyring.get_keyring',side_effect=RuntimeError('private detail')):
                self.assertFalse(cfg.raw_save())
            self.assertEqual(path.read_text(),before)

    def test_paths_without_appdata(self):
        with patch('sys.platform','linux'),patch.dict(os.environ,{'XDG_CONFIG_HOME':'/tmp/vg-config'}):
            self.assertEqual(config_dir(),Path('/tmp/vg-config/voltguard'))
        with patch('sys.platform','win32'),patch.dict(os.environ,{'APPDATA':'C:/Example/Roaming'}):
            self.assertEqual(config_dir(),Path('C:/Example/Roaming/VoltGuard'))

    def test_second_instance_cannot_take_the_control_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'instance.lock')
            with FileLock(path):
                with self.assertRaises(Timeout):
                    FileLock(path).acquire(timeout=0)


class StartupTests(unittest.TestCase):
    def test_linux_native_autostart_and_path_quoting(self):
        entry=desktop_entry(['/home/example/My App/python','/home/example/50%/main.py'],startup=True)
        self.assertIn('"/home/example/My App/python"',entry)
        self.assertIn('50%%',entry)
        self.assertIn('"--autostart"',entry)
        self.assertNotIn('--minimized',entry)
        with tempfile.TemporaryDirectory() as folder,patch('sys.platform','linux'),patch.dict(os.environ,{'XDG_CONFIG_HOME':folder}):
            configure_autostart(True)
            self.assertIn('Hidden=false',(Path(folder)/'autostart/voltguard.desktop').read_text())

    @unittest.skipUnless(sys.platform=='win32','Requires real Windows registry')
    def test_windows_native_registry_matches_the_real_launch_command(self):
        import winreg
        keypath=r'Software\Microsoft\Windows\CurrentVersion\Run'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,keypath) as key:
                original=winreg.QueryValueEx(key,'VoltGuard')
        except FileNotFoundError:
            original=None
        try:
            with patch('src.autostart.launch_command', return_value=launch_command()+['--smoke-test']):
                configure_autostart(True)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,keypath) as key:
                command,kind=winreg.QueryValueEx(key,'VoltGuard')
            self.assertEqual(kind,winreg.REG_SZ)
            self.assertIn('--autostart',command)
            self.assertIn('main.py',command)
            # Execute exactly the native registry command, in harmless preview mode.
            result=subprocess.run(command,timeout=45)
            self.assertEqual(result.returncode,0)
        finally:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,keypath) as key:
                if original:
                    winreg.SetValueEx(key,'VoltGuard',0,original[1],original[0])
                else:
                    winreg.DeleteValue(key,'VoltGuard')
