"""Read-only account diagnostic. It never chooses a plug by its position or switches power."""
import asyncio
import getpass
from types import SimpleNamespace
from src.battery_backend import BatteryBackend


async def test_meross():
    email = input("Meross email: ").strip()
    password = getpass.getpass("Meross password (private): ")
    uuid = input("Exact configured charging plug UUID: ").strip()
    cfg = SimpleNamespace(config={"email": email, "uuid": uuid}, password=password)
    try:
        info = await BatteryBackend(cfg, lambda *a, **k: None, lambda *a: None).inspect_plug()
        print("Configured device found:", info['name'], "| model:", info['type'], "| ON:", info['on'])
        print("Read-only check complete. No power commands were sent.")
    except Exception as error:
        print("Could not verify the configured device:", type(error).__name__)


if __name__ == '__main__':
    asyncio.run(test_meross())
