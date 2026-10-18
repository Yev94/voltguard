#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_root="${XDG_DATA_HOME:-$HOME/.local/share}/voltguard"
app_dir="$install_root/app"
python_bin="${VOLTGUARD_PYTHON:-/usr/bin/python3}"
"$python_bin" -c 'import tkinter, gi, dbus; gi.require_version("AyatanaAppIndicator3", "0.1")' || {
  echo 'Install python3-venv python3-tk python3-gi python3-dbus gir1.2-ayatanaappindicator3-0.1, then run this installer again.' >&2
  exit 1
}
mkdir -p "$app_dir/src"
"$python_bin" -m venv --system-site-packages "$install_root/venv"
"$install_root/venv/bin/python" -m pip install -r "$source_dir/requirements.txt"
cp "$source_dir/main.py" "$source_dir/requirements.txt" "$source_dir/logo.png" "$source_dir/logo.ico" "$app_dir/"
cp "$source_dir"/src/*.py "$app_dir/src/"
"$install_root/venv/bin/python" "$app_dir/main.py" --install-autostart
VOLTGUARD_APP_DIR="$app_dir" "$install_root/venv/bin/python" - <<'PY'
import os, pathlib, sys
app=pathlib.Path(os.environ['VOLTGUARD_APP_DIR'])
sys.path.insert(0,str(app))
from src.autostart import desktop_entry
menu=pathlib.Path(os.environ.get('XDG_DATA_HOME',pathlib.Path.home()/'.local/share'))/'applications/voltguard.desktop'
menu.parent.mkdir(parents=True,exist_ok=True)
menu.write_text(desktop_entry([sys.executable,str(app/'main.py')])+'Icon='+str(app/'logo.png')+'\n',encoding='utf-8')
PY
printf 'Installed VoltGuard in %s. Open it from the application menu. Login startup is enabled.\n' "$app_dir"
