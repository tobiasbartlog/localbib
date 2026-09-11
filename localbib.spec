# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec für LocalBib (onedir). Bauen via: build_exe.ps1
import os

from PyInstaller.utils.hooks import collect_submodules

# uvicorn lädt Protokoll-/Lifespan-Module dynamisch → alle Submodule einsammeln
hiddenimports = collect_submodules("uvicorn")
hiddenimports += [
    "multipart",          # python-multipart (Form-/Upload-Parsing in FastAPI)
    "python_multipart",
    # webapp.py lädt den Import-Router per importlib-String ("import" ist ein
    # Python-Keyword) — PyInstaller kann dem String nicht folgen; ohne diesen
    # Eintrag crasht die Exe beim Start (ModuleNotFoundError: routers.import).
    # Gefunden im H7-Packaging-Spike (#112).
    "routers.import",
]

datas = [
    ("static", "static"),
    ("templates", "templates"),
]

# VERSION ist generiert, nicht eingecheckt (.gitignore): der Release-Workflow
# schreibt den Tag hinein, bevor er baut (#146), lokal tut das build_exe.ps1.
# Ein Build ohne Tag (z. B. der frozen-build-smoke-Job auf jedem Push) hat die
# Datei schlicht nicht — PyInstaller bricht dann beim datas-Eintrag hart ab.
# Also nur bundeln, wenn sie da ist; ohne sie faellt routers/version.py auf den
# Git-Tag zurueck.
if os.path.exists("VERSION"):
    datas.append(("VERSION", "."))

a = Analysis(
    ["webapp.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nicht genutzte (transitiv eingeschleppte) Schwergewichte ausschließen.
    # Der Code importiert keine davon (grep-verifiziert) → spart ~100 MB.
    excludes=[
        "tkinter", "pytest", "respx", "matplotlib", "pytest_cov",
        "pandas", "numpy", "openpyxl", "PIL", "Pillow",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalBib",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Das Icon wird beim Bauen in die .exe einkompiliert und ist damit die
    # Quelle fuer Desktop-Verknuepfung, Taskleiste und Explorer. Dieselbe .ico
    # liegt unter static/ und wird ueber datas ohnehin mitgebundelt (Favicon).
    icon="static/icons/localbib.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LocalBib",
)
