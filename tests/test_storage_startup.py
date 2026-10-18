import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.credential_store import CredentialUnavailable
from src.config_manager import ConfigManager
from src.autostart import configure_autostart, desktop_entry, launch_command
from src.paths import config_dir
from filelock import FileLock, Timeout


class FakeStore:
    backend_id = 'kwallet'
    def __init__(self, value=None):
        self.value=value
        self.reads=0
        self.writes=[]
        self.unavailable=False
    def read(self, account):
        self.reads+=1
        if self.unavailable: raise CredentialUnavailable('wallet_not_ready')
        return self.value
    def write(self, account, value):
        if self.unavailable: raise CredentialUnavailable('wallet_locked')
        self.writes.append((account,value))
        self.value=value


class SettingsTests(unittest.TestCase):
    def test_legacy_policy_migrates_without_activating_unconfirmed_plug(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            original=json.dumps({'email':'example@example.invalid','uuid':'test-plug','min_bat':20,'max_bat':95,'password':'legacy-secret','start_minimized':True})
            path.write_text(original)
            store=FakeStore('test-secret')
            cfg=ConfigManager(path,credential_store=store)
            self.assertEqual((cfg.config['min_bat'],cfg.config['max_bat']),(40,95))
            self.assertFalse(cfg.config['start_minimized'])
            self.assertFalse(cfg.is_confirmed())
            self.assertEqual(path.read_text(),original)
            self.assertTrue(cfg.raw_save())
            self.assertEqual(store.writes,[])
            written=json.loads(path.read_text())
            self.assertNotIn('password',written)
            self.assertNotIn('legacy-secret',path.read_text())
            self.assertEqual(written['credential_backend'],'kwallet')

    def test_current_settings_save_without_rewriting_a_locked_wallet(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps({'schema_version':2,'email':'example@example.invalid','min_bat':20,'max_bat':95}))
            store=FakeStore('existing-fixture')
            cfg=ConfigManager(path,credential_store=store)
            store.unavailable=True
            self.assertTrue(cfg.raw_save())
            self.assertEqual(store.writes,[])
            self.assertEqual(json.loads(path.read_text())['min_bat'],20)

    def test_failed_explicit_password_edit_preserves_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps({'schema_version':2,'email':'example@example.invalid'}))
            store=FakeStore('existing-fixture')
            cfg=ConfigManager(path,credential_store=store)
            before=path.read_text()
            cfg.password='new-fixture'
            store.unavailable=True
            self.assertFalse(cfg.raw_save())
            self.assertEqual(path.read_text(),before)
            self.assertEqual(store.value,'existing-fixture')

    def test_delayed_wallet_recovers_and_new_process_uses_same_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps({'schema_version':2,'email':'example@example.invalid','min_bat':20,'max_bat':95}))
            store=FakeStore('saved-fixture')
            store.unavailable=True
            cfg=ConfigManager(path,credential_store=store)
            self.assertFalse(cfg.password)
            self.assertEqual(cfg.credential_state,'wallet_not_ready')
            store.unavailable=False
            self.assertTrue(cfg.reload_password())
            self.assertTrue(cfg.raw_save())
            with patch('src.config_manager.CredentialStore',return_value=store) as factory:
                restarted=ConfigManager(path)
            factory.assert_called_once_with('kwallet')
            self.assertEqual(restarted.password,'saved-fixture')
            self.assertEqual(restarted.config['credential_backend'],'kwallet')
            self.assertEqual(restarted.config['min_bat'],20)
            self.assertEqual(store.writes,[])

    def test_repeated_validation_does_not_drop_a_pending_password_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps({'schema_version':2,'email':'example@example.invalid'}))
            store=FakeStore('saved-fixture')
            cfg=ConfigManager(path,credential_store=store)
            cfg.password='edited-fixture'
            cfg.password='edited-fixture'
            self.assertTrue(cfg.reload_password())
            self.assertEqual(cfg.password,'edited-fixture')
            self.assertTrue(cfg.raw_save())
            self.assertTrue(cfg.raw_save())
            self.assertEqual(store.writes,[('example@example.invalid','edited-fixture')])

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
    @unittest.skipIf(sys.platform=='win32','Symlink regression applies to the Linux virtualenv launcher')
    def test_login_preserves_virtualenv_interpreter_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            executable=Path(folder)/'venv/bin/python'
            executable.parent.mkdir(parents=True)
            executable.symlink_to(sys.executable)
            with patch('sys.executable',str(executable)):
                self.assertEqual(launch_command()[0],str(executable))

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
