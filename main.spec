# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-file spec for PFETL Analytics Studio."""

import sys
from pathlib import Path

import PyInstaller.config
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

UPX_BIN_DIR = Path(r"C:\Tools\_bins_\upx")
if UPX_BIN_DIR.exists():
    PyInstaller.config.CONF["upx_dir"] = str(UPX_BIN_DIR)

REPO_ROOT = Path(SPECPATH).resolve()
SRC_DIR = REPO_ROOT / "src"
PKG_DIR = SRC_DIR / "pfetl_analytics_studio"
ASSETS_DIR = PKG_DIR / "ui" / "assets"
VERSION_FILE = REPO_ROOT / "version_info.txt"

datas = [
    (str(REPO_ROOT / "README.md"), "."),
    (str(ASSETS_DIR / "logo.ico"), "."),
    (str(ASSETS_DIR / "logo.png"), "."),
    (str(ASSETS_DIR), "pfetl_analytics_studio/ui/assets"),
]
binaries = []
hiddenimports = [
    "pfetl_analytics_studio.tray",
    "pfetl_analytics_studio.cli",
    "pfetl_analytics_studio.server",
    "pfetl_analytics_studio.app",
    "pystray._win32",
    "tomllib",
    "win32timezone",
]

for pkg in ("nicegui", "pystray", "PIL"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas.extend(pkg_datas)
    binaries.extend(pkg_bins)
    hiddenimports.extend(pkg_hidden)

for pkg in ("plotly", "pptx", "reportlab", "kaleido"):
    datas.extend(collect_data_files(pkg))

hiddenimports.extend(collect_submodules("pfetl_analytics_studio"))

a = Analysis(
    [str(PKG_DIR / "cli.py")],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "pandas", "pyarrow", "numba",
        "bokeh", "altair", "seaborn",
        "tkinter", "tkinter.test", "idlelib",
        "PyQt5", "PySide2", "PySide6", "PyQt6",
        "openpyxl", "xlrd", "pyxlsb", "odf",
        "sqlalchemy", "boto3", "botocore", "s3fs", "gcsfs", "fsspec",
        "pytest", "pdb", "unittest", "IPython", "jupyter", "notebook", "nbconvert", "nbformat",
        "mypy", "mypyc", "pyright", "pylint", "black", "isort", "ruff",
        "poethepoet", "pyinstaller", "tkreload", "vulture",
        "pydantic.v1", "pydantic.tests", "pydantic._internal.tests",
        "duckdb.tests", "psutil.tests", "PIL.ImageQt",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pfetl-analytics-studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140*.dll", "python3*.dll"],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS_DIR / "logo.ico"),
    version=str(VERSION_FILE) if sys.platform == "win32" and VERSION_FILE.exists() else None,
)