# -*- mode: python ; coding: utf-8 -*-


from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

extra_data = collect_data_files("customtkinter") + copy_metadata("meross-iot")
extra_imports = collect_submodules("keyring.backends")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('logo.png', '.'), ('logo.ico', '.')] + extra_data,
    hiddenimports=extra_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VoltGuard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['logo.ico'],
)
