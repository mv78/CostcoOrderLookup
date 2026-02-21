# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec file for costco-lookup-server.exe (Flask web UI)
# Build with:  pyinstaller build-server.spec --clean --noconfirm
# Output:      dist/costco-lookup-server.exe
#
# Requires PyInstaller >= 6.0
#
# config.json stays EXTERNAL (alongside the .exe) so users can edit it.
# .token_cache.json and costco_lookup.log are written at runtime next to the .exe.
# Templates are bundled into the .exe via datas below.

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_all

# Flask — collect_all ensures Jinja2 template loader and all package data is included.
flask_datas, flask_binaries, flask_hidden = collect_all('flask')
jinja2_datas, jinja2_binaries, jinja2_hidden = collect_all('jinja2')
werkzeug_datas, werkzeug_binaries, werkzeug_hidden = collect_all('werkzeug')
barcode_hidden = collect_submodules('barcode')

a = Analysis(
    ['server.py'],
    pathex=[str(Path('.').resolve())],
    binaries=flask_binaries + jinja2_binaries + werkzeug_binaries,
    datas=flask_datas + jinja2_datas + werkzeug_datas + [
        (str(Path('.').resolve() / 'costco_lookup' / 'templates'), 'costco_lookup/templates'),
    ],
    hiddenimports=[
        # Flask stack
        *flask_hidden,
        *jinja2_hidden,
        *werkzeug_hidden,
        'click',
        'itsdangerous',
        'markupsafe',
        # python-barcode (used by downloader for receipt SVG)
        *barcode_hidden,
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
    name='costco-lookup-server',
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
