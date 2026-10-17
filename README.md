<div align="center">
<img src="./logo.png" width="128" />

# VoltGuard 🔋🔌

Automatic laptop charge control · Windows + Linux · v1.1.0

![VoltGuard UI](./image.png)
</div>

VoltGuard uses one Python codebase to read the laptop battery with `psutil` and control the **exact configured Meross smart plug**, through the Meross EU cloud. Internet and a supported single-outlet Meross plug are required. It does not change firmware or OS power profiles.

The default policy is **ON at or below 40% while disconnected from AC**, and **OFF at or above 95% while connected to AC**, checked every 60 seconds. In between, it leaves the relay alone. Missing battery readings, unknown AC status, offline devices and unknown relay state never trigger a power command. Commands are acknowledged, rate limited and retried at most three times per threshold crossing. Only one controller can run per user.

## Install or update

Download the distributions from [GitHub Releases](https://github.com/Yev94/voltguard/releases). CI artifacts are available on the [Actions page](https://github.com/Yev94/voltguard/actions) for branch builds. The old `Release/VoltGuard_Installer.exe` retained in the repository is **v1.0.0 for Windows**, not the new cross-platform installer.

### Windows

Run `VoltGuard_1.1.0_Windows_Setup.exe`. The installer offers login startup, enabled by default. It registers the installed application under the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` with `--autostart`. No administrator access or Windows service is required. The panel opens visibly when that user signs in.

The existing `%APPDATA%\VoltGuard\config.json` location is retained. The first v1.1 load migrates old 15–85 or 20–95 limits to **40–95**, shows the panel and requires the charging plug to be verified again. The password stays in Windows Credential Locker. Normal subsequent settings edits are preserved.

### Linux (including KDE Plasma / Wayland)

Extract `VoltGuard-1.1.0-Linux.tar.gz`. On Ubuntu/Kubuntu install desktop dependencies if missing:

```bash
sudo apt install python3-venv python3-tk python3-gi gir1.2-ayatanaappindicator3-0.1
bash scripts/install-linux.sh
```

The installer copies only application code/assets into `${XDG_DATA_HOME:-~/.local/share}/voltguard/app` and installs dependencies in a sibling virtual environment using system Python. This location is stable and independent of the source checkout/worktree. No existing configuration, profile or installation is purged. Re-run the installer to update.

Open **VoltGuard** from the application menu. Per-user login startup is registered at `${XDG_CONFIG_HOME:-~/.config}/autostart/voltguard.desktop`. It opens the same visible panel; no background service or second implementation is created. The tray uses AppIndicator/Ayatana on Linux. If a usable tray cannot be created, the panel stays visible.

Passwords use the secure desktop keyring (KWallet / Secret Service); unlock the wallet privately if prompted. VoltGuard will not save a password into JSON or fall back to an insecure plaintext backend.

## Configure and verify the charging plug

1. Enter your Meross email, password and the exact UUID of the plug powering this laptop. Password input is masked. Do not paste credentials into logs, issues or chat.
2. Keep the 40% / 95% limits or choose a valid range. **Save settings** stores the password in the OS keyring and the other settings in the user config file.
3. **Verify Plug** checks only the configured UUID and reads its model/status. It does **not** turn it on or off. Confirm in the local dialog that this is the laptop charger before enabling control. Changing email or UUID invalidates that confirmation.
4. **START** begins monitoring. **STOP** stops monitoring without changing the current relay state. Manual ON/OFF buttons remain available after verification.

The controls **Start charge monitoring automatically**, **Open panel at Windows / Linux login**, and **Hide window when opened manually** are independent. Login startup always opens the panel visibly. Closing the window hides it to the tray where supported; use **Exit** to stop the application.

A password inherited from a legacy plaintext project config may be migrated locally, without printing it:

```bash
/path/to/voltguard/venv/bin/python /path/to/voltguard/app/main.py --import-legacy /path/to/old/config.json
```

This imports the account/UUID, stores its password in the OS keyring, applies 40–95 and leaves hardware control unconfirmed. The original file remains untouched. Credentials are not synchronized between Windows and Linux; each OS uses its own secure wallet.

### User files

| File | Windows | Linux |
| --- | --- | --- |
| Non-secret settings | `%APPDATA%\VoltGuard\config.json` | `${XDG_CONFIG_HOME:-~/.config}/voltguard/config.json` |
| Rotating logs | `%APPDATA%\VoltGuard\voltguard.log` | `${XDG_STATE_HOME:-~/.local/state}/voltguard/voltguard.log` |
| Instance lock | Settings directory | Settings directory |

Logs avoid passwords, tokens and device UUIDs. A failed network connection retries with backoff; stopping or losing connectivity does not send a compensating ON/OFF command. The laptop must be awake, signed in and connected to the Internet for charge control to operate.

## Development and validation

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --demo          # harmless GUI preview; no account or switching
python main.py --smoke-test    # render preview and exit
python main.py                # normal application
```

On headless Linux, use `PYSTRAY_BACKEND=dummy xvfb-run -a python main.py --smoke-test`. The manual `test_meross.py` diagnostic is also read-only; it never selects a device by position.

GitHub Actions runs mocked battery/network tests and GUI smoke tests on **Windows and Ubuntu**. Windows CI also checks the native startup registry entry, builds with PyInstaller + Inno Setup and smoke-tests the packaged executable. Linux CI packages the shared source and native installer script. Version tags publish both distributions only after the jobs succeed. CI does not authenticate to a real Meross account or switch hardware. A physical Windows login and the next real Linux login remain separate operational checks.

To build Windows locally:

```powershell
python -m pip install -r requirements.txt 'pyinstaller>=6.16,<7'
python -m PyInstaller --noconfirm VoltGuard.spec
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' VoltGuard.iss
```
