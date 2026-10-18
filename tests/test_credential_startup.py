import os
os.environ.setdefault('PYSTRAY_BACKEND','dummy')
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from src.credential_store import CredentialStore, CredentialUnavailable, native_backend_id
from src.ui_app import VoltGuardApp
from keyring.errors import KeyringLocked


class BackendBindingTests(unittest.TestCase):
    def test_kde_binding_does_not_fall_back_when_wallet_starts_late(self):
        with patch('sys.platform','linux'),patch.dict(os.environ,{'XDG_CURRENT_DESKTOP':'KDE'}):
            store=CredentialStore()
        self.assertEqual(store.backend_id,'kwallet')
        with patch.object(store,'_ready_backend',side_effect=CredentialUnavailable('wallet_not_ready')):
            with self.assertRaises(CredentialUnavailable):store.read('example@example.invalid')
        native=SimpleNamespace(get_password=MagicMock(return_value='existing-fixture'),set_password=MagicMock())
        with patch.object(store,'_ready_backend',return_value=native):
            self.assertEqual(store.read('example@example.invalid'),'existing-fixture')
        native.get_password.assert_called_once_with('VoltGuard','example@example.invalid')
        native.set_password.assert_not_called()

    def test_locked_wallet_error_does_not_leak_diagnostics(self):
        store=CredentialStore('kwallet')
        with patch.object(store,'_ready_backend',side_effect=RuntimeError('private-payload-fixture')):
            with self.assertRaises(CredentialUnavailable) as caught:store.read('example@example.invalid')
        self.assertNotIn('private-payload-fixture',str(caught.exception))

    def test_not_ready_or_locked_wallet_does_not_request_open_or_unlock(self):
        bus=MagicMock()
        bus.name_has_owner.return_value=False
        store=CredentialStore('kwallet')
        class NativeWallet:
            bus_name='org.kde.kwalletd5'
            object_path='/modules/kwalletd5'
            def __init__(self):raise AssertionError('Native wallet must not be opened')
        fake_dbus=SimpleNamespace(SessionBus=MagicMock(return_value=bus),String=lambda value:value)
        modules={'dbus':fake_dbus,'keyring.backends.kwallet':SimpleNamespace(DBusKeyring=NativeWallet)}
        with patch.dict(sys.modules,modules):
            with self.assertRaises(CredentialUnavailable) as caught:store.read('example@example.invalid')
        self.assertEqual(caught.exception.state,'wallet_not_ready')
        bus.get_object.assert_not_called()
        bus.name_has_owner.return_value=True
        proxy=bus.get_object.return_value
        def method(name,interface):
            if name=='networkWallet':return lambda:'test-wallet'
            if name=='isOpen':return lambda *a,**k:False
            raise AssertionError('Opening/unlocking is forbidden in this test')
        proxy.get_dbus_method.side_effect=method
        with patch.dict(sys.modules,modules):
            with self.assertRaises(CredentialUnavailable) as caught:store.read('example@example.invalid')
        self.assertEqual(caught.exception.state,'wallet_locked')
        self.assertIsNone(store._backend)

    def test_cancelled_access_is_distinct_from_a_missing_password(self):
        store=CredentialStore('kwallet')
        native=SimpleNamespace(get_password=MagicMock(side_effect=KeyringLocked('fixture cancelled')))
        with patch.object(store,'_ready_backend',return_value=native):
            with self.assertRaises(CredentialUnavailable) as caught:store.read('example@example.invalid')
        self.assertEqual(caught.exception.state,'wallet_access_denied')


class StartupRetryTests(unittest.TestCase):
    def make_app(self):
        app=VoltGuardApp.__new__(VoltGuardApp)
        app.closing=False;app._auto_start_requested=True;app._credential_retry_delay=2000
        app.root=MagicMock();app.backend=SimpleNamespace(running=False)
        app.cfg_manager=SimpleNamespace(config={'auto_monitor':True},password='',credential_state='wallet_not_ready',
                                        is_confirmed=lambda:True,reload_password=MagicMock(return_value=False))
        app.entry_password=MagicMock();app.entry_password.get.return_value=''
        app.set_status=MagicMock();app.start_monitor=MagicMock()
        return app

    def test_wait_then_recover_without_window_reopen_or_password_edit(self):
        app=self.make_app()
        app._auto_start()
        app.start_monitor.assert_not_called()
        app.root.after.assert_called_once_with(2000,app._auto_start)
        def recovered():app.cfg_manager.password='existing-fixture';return True
        app.cfg_manager.reload_password.side_effect=recovered
        app._auto_start()
        app.start_monitor.assert_called_once()
        app.entry_password.insert.assert_called_once_with(0,'********')
        app.root.deiconify.assert_not_called();app.root.focus_force.assert_not_called()

    def test_does_not_override_user_edit_or_disabled_automatic_monitor(self):
        app=self.make_app();app.entry_password.get.return_value='edited-fixture'
        app._auto_start()
        app.cfg_manager.reload_password.assert_not_called();app.start_monitor.assert_not_called()
        app=self.make_app();app.cfg_manager.config['auto_monitor']=False
        app._auto_start()
        app.root.after.assert_not_called();app.start_monitor.assert_not_called()

    def test_missing_password_is_not_a_discovery_or_cloud_login_loop(self):
        app=self.make_app();app.cfg_manager.credential_state='missing'
        app._auto_start()
        app.root.after.assert_not_called();app.start_monitor.assert_not_called()

    def test_cancelled_wallet_access_does_not_retry_dialogs(self):
        app=self.make_app();app.cfg_manager.credential_state='wallet_access_denied'
        app._auto_start()
        app.root.after.assert_not_called();app.start_monitor.assert_not_called()
