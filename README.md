<div align="center">

# VoltGuard 🔋🔌

![VoltGuard UI Preview](./image.png)

</div>

A native resident, asynchronous, and modular Windows application designed to **monitor laptop battery**, and automatically turn on/off a **Meross** smart plug based on configurable energy thresholds. Built from scratch prioritizing stability, persistent local connection, and low resource consumption (Daemon).

## 🚀 Key Features

*   **MVC Architecture & Asynchrony:** The core (Backend) handles asynchronous read requests (`meross-iot` and `psutil`) completely decoupled from the visual interface (`customtkinter`).
*   **Resident Mode (System Tray):** The application is designed to run in the background (Daemon). When pressing the close button, it silently hides next to the Windows clock (`pystray`) and continues operating unattended.
*   **Cryptographic Vault:** Uses the native Windows credential manager (`keyring` Module). Your actual Meross account password is never saved in local files, protecting and isolating your credentials from plaintext attacks.
*   **Extreme Resilience & Anti-spam:** If there's a Wi-Fi outage, the app gracefully suspends execution and uses a mathematical rotation function to avoid spamming the hard drive during false network spikes.
*   **Absolute File Agnostic:** Preserves its own fixed save context in the reserved system folder to avoid collisions between packaged executables. Everything lives peacefully on your local system under a unified structure natively protected by Windows.

---

## 💻 Requirements

To run this without executing a precompiled `.exe`, make sure you have the active libraries in your environment:

```bash
pip install meross-iot psutil customtkinter pystray keyring pillow python-dotenv
```

> **OS Warning:** The telemetry logic is exclusively compiled drawing from the `psutil.sensors_battery()` engine.

---

## 📂 File Architecture

This project applies separation of concerns for easier maintenance:

```text
📁 Project Root
├── 📄 main.py (Main entry point of the application)
│
└── 📁 src\
    ├── 📄 __init__.py 
    ├── 📄 logger_config.py (Atomic UTF-8 disk logging system)
    ├── 📄 config_manager.py (Model manager and Keyring Vault validation)
    ├── 📄 battery_backend.py (Background thread backend and Meross logic)
    └── 📄 ui_app.py (CustomTkinter block frontend and async callbacks)
```

### Persistent Paths
Unless modified, the static core of the program will always reside in:
> `C:\Users\YOUR_USER\AppData\Roaming\VoltGuard`

- `config.json`: Stores exclusively the Email, the Plug's UUID, and the statistical flat-time checking thresholds.
- `voltguard.log`: Limited log with auto-deletion (Log rotation at ~1MB max size with 3 time slots on disk) for deep debugging if the visual UI fails.

---

## 🛠 Installation and Usage

1. Go to the project root.
2. Open a terminal.
3. Start it:

```bash
python main.py
```

4. The visual interface will ask on the first login for: **Your email, Password, and the famous Plug UUID**. Once filled in, configure the range fields: `% Min Battery`, `% Max Battery`, and the `Seconds` the engine waits before querying the variable again.

### Where do I find my Meross UUID?
Every Meross IoT hub uniquely associates this Hardware and Cloud identifier. The rustic way to find it is using an async test script, but just starting the app and looking at the top blind log sequence will be enough.
*The log itself will record on the fly and extract for us each unique ID for every internet plug at home if the console fails.*

### Special Active Buttons:

- **🔌 Test Plug**: Pure gold tool. Press this and your Frontend will test the direct asynchrony with the Backend: your plug will attempt to turn on for _2 whole seconds_, and then turn off with a snap. From this, you will get two readings: network latency response, and know if you wrote the UUID correctly.
- **Start Minimized Option**: Perfect for putting this application into the "*Run at Windows startup*" list of your daily routines. It launches loading the backend straight away, skipping the GUI flash.
