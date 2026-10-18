"""Stable OS-wallet binding. No cloud login, plaintext fallback or cross-wallet migration."""
from contextlib import closing
import os
import sys
from keyring.errors import KeyringLocked

SERVICE = "VoltGuard"


class CredentialUnavailable(Exception):
    def __init__(self, state="wallet_unavailable"):
        self.state = state
        super().__init__(state)


def native_backend_id():
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "kwallet" if "KDE" in os.getenv("XDG_CURRENT_DESKTOP", "").split(":") else "secretservice"
    raise CredentialUnavailable("wallet_unsupported")


class CredentialStore:
    def __init__(self, backend_id=""):
        self.backend_id = backend_id or native_backend_id()
        self._backend = None

    def _ready_backend(self):
        if self.backend_id == "kwallet":
            import dbus
            from keyring.backends.kwallet import DBusKeyring
            bus = dbus.SessionBus()
            if not bus.name_has_owner(DBusKeyring.bus_name):
                raise CredentialUnavailable("wallet_not_ready")
            proxy = bus.get_object(DBusKeyring.bus_name, DBusKeyring.object_path, introspect=False)
            wallet = proxy.get_dbus_method("networkWallet", "org.kde.KWallet")()
            opened = proxy.get_dbus_method("isOpen", "org.kde.KWallet")(dbus.String(wallet), signature="s")
            if not opened:
                # Wait for the user's normal desktop unlock; never initiate an unlock here.
                raise CredentialUnavailable("wallet_locked")
            if self._backend is None:
                class ExistingWallet(DBusKeyring):
                    def _migrate(self, service):
                        # keyring's default migrates every entry in the legacy Python folder.
                        # VoltGuard must only access its own existing service/account.
                        pass
                self._backend = ExistingWallet()
        elif self.backend_id == "secretservice":
            import secretstorage
            from keyring.backends.SecretService import Keyring
            with closing(secretstorage.dbus_init()) as connection:
                collection = secretstorage.get_default_collection(connection)
                if collection.is_locked():
                    raise CredentialUnavailable("wallet_locked")
            if self._backend is None:
                class ExistingSecretService(Keyring):
                    def get_preferred_collection(self):
                        connection = secretstorage.dbus_init()
                        try:
                            collection = secretstorage.get_default_collection(connection)
                            if collection.is_locked():
                                raise CredentialUnavailable("wallet_locked")
                            return collection
                        except Exception:
                            connection.close()
                            raise
                    def unlock(self, item):
                        # keyring normally requests an interactive unlock here.
                        if item.is_locked():
                            raise CredentialUnavailable("wallet_locked")
                self._backend = ExistingSecretService()
        elif self.backend_id == "windows" and sys.platform == "win32":
            if self._backend is None:
                from keyring.backends.Windows import WinVaultKeyring
                self._backend = WinVaultKeyring()
        else:
            raise CredentialUnavailable("wallet_unsupported")
        return self._backend

    def read(self, account):
        try:
            return self._ready_backend().get_password(SERVICE, account)
        except CredentialUnavailable:
            raise
        except KeyringLocked:
            # A denied/cancelled access request must not become an automatic prompt loop.
            raise CredentialUnavailable("wallet_access_denied") from None
        except Exception:
            raise CredentialUnavailable() from None

    def write(self, account, password):
        """Only an explicit password edit/import calls this; settings saves never do."""
        try:
            self._ready_backend().set_password(SERVICE, account, password)
        except CredentialUnavailable:
            raise
        except KeyringLocked:
            # A denied/cancelled access request must not become an automatic prompt loop.
            raise CredentialUnavailable("wallet_access_denied") from None
        except Exception:
            raise CredentialUnavailable() from None
