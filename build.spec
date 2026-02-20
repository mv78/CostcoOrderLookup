# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec file for costco-lookup.exe
# Build with:  pyinstaller build.spec --clean --noconfirm
# Output:      dist/costco-lookup.exe
#
# Requires PyInstaller >= 6.0
#
# config.json stays EXTERNAL (alongside the .exe) so users can edit it.
# .token_cache.json and costco_lookup.log are written at runtime next to the .exe.

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_entry_point, collect_all

# Collect keyring backends via entry-point metadata.
# Without this, keyring's plugin discovery finds no backends at runtime
# and raises NoKeyringError even though the .py files are present.
keyring_datas, keyring_hidden = collect_entry_point("keyring.backends")
keyring_hidden += collect_submodules("keyring")

# collect_all does a full filesystem walk of the rich package, which is
# required to include rich._unicode_data.unicode17-0-0 (and future versions).
# That module is loaded dynamically via importlib.import_module() so
# PyInstaller's static analysis never finds it on its own.
rich_datas, rich_binaries, rich_hidden = collect_all('rich')

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=rich_binaries,
    datas=keyring_datas + rich_datas,
    hiddenimports=[
        # keyring — all backends + metadata
        *keyring_hidden,
        'keyring.backends.Windows',
        'keyring.backends._win_crypto',
        'keyring.backends.SecretService',
        'keyring.backends.macOS',
        'keyring.backends.fail',
        'keyring.util.escape',
        'keyring.credentials',
        # beautifulsoup4
        'bs4',
        'html.parser',
        # rich — collected in full via collect_all above
        *rich_hidden,
        # dateutil
        'dateutil',
        'dateutil.relativedelta',
        # requests stack
        'requests',
        'charset_normalizer',
        'idna',
        'certifi',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='costco-lookup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
