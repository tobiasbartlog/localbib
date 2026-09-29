# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec für LocalBib (onedir). Bauen via: build_exe.ps1
import importlib.util
import os
import sys

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
# Add-ons sind nicht Teil des Binaries (ADR-0021): Sie kommen als installierte
# Bundles oder ueber einen Dev-Suchpfad (LOCALBIB_PLUGIN_DEV_PATHS) und werden
# zur Laufzeit von plugin_loader.py geladen. Deshalb sammelt dieses Spec-File
# kein Plugin-Paket ein.

# Die vollstaendige Standardbibliothek (Contract-Zusage an alle Add-ons, #183):
# PyInstaller buendelt sonst nur die stdlib-Module, die der Kern selbst
# importiert (im Spike fehlten 61 von 290). Ein Add-on bringt seine
# Abhaengigkeiten in vendor/ mit, aber nie die stdlib — braucht ein
# vendorisiertes Paket ein nicht gebuendeltes Modul (numpy.f2py -> fileinput),
# scheitert es erst beim Nutzer. Die Liste entsteht beim Bauen aus
# sys.stdlib_module_names des Build-Interpreters (also passend zu dessen
# Version und Plattform); was dort nicht importierbar ist (Unix-only auf
# Windows und umgekehrt), findet find_spec nicht und faellt heraus. Kosten:
# +0,8 MB. Bewusst ausgelassen: GUI (tkinter — steht auch in excludes unten),
# Test-Suites, Entwicklerwerkzeuge ohne Laufzeitnutzen und CPythons interne
# Testmodule. Wer diese Liste aendert, haelt sie deckungsgleich mit excludes:
# kein Modul darf in beiden stehen (tests/test_frozen_spec.py prueft das).
STDLIB_SKIP = {
    "tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "test",
    "lib2to3", "ensurepip", "venv", "antigravity", "this", "__phello__",
    "pydoc_data",
}
_STDLIB_TEST_PARTS = {"test", "tests", "idle_test"}


def _stdlib_skipped(name):
    return (name in STDLIB_SKIP or name.startswith(("_test", "_xx", "xx"))
            or name == "_ctypes_test")


def _stdlib_hiddenimports():
    found = []
    for name in sorted(sys.stdlib_module_names):
        if _stdlib_skipped(name):
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            continue
        found.append(name)
        if spec.submodule_search_locations:
            found += collect_submodules(
                name,
                filter=lambda m: not (_STDLIB_TEST_PARTS & set(m.split("."))),
            )
    return found


hiddenimports += _stdlib_hiddenimports()

datas = [
    ("static", "static"),
    ("templates", "templates"),
    # plugin_api liest sein Manifest-Schema zur Laufzeit neben manifest.py
    # (SCHEMA_PATH). Ohne diese Datei scheitert in der Exe jede
    # Manifest-Pruefung mit FileNotFoundError, /api/plugins antwortet 500 und
    # kein Add-on laedt (#183).
    ("plugin_api/manifest.schema.json", "plugin_api"),
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
