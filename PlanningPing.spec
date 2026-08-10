# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

SELENIUM_BROWSER_IMPORTS = [
    "selenium.webdriver.chrome.options",
    "selenium.webdriver.chrome.service",
    "selenium.webdriver.chrome.webdriver",
    "selenium.webdriver.edge.options",
    "selenium.webdriver.edge.service",
    "selenium.webdriver.edge.webdriver",
]

TKINTERDND_DATA = collect_data_files("tkinterdnd2")

a = Analysis(
    ["src\\planning_ping\\__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        ("src\\planning_ping\\data\\planning_authorities.geojson", "planning_ping\\data"),
        *TKINTERDND_DATA,
    ],
    hiddenimports=SELENIUM_BROWSER_IMPORTS,
    hookspath=["tools\\pyinstaller_hooks"],
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
    name="PlanningPing",
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
)
