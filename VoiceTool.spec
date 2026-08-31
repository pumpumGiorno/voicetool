# -*- mode: python ; coding: utf-8 -*-
"""Сборка VoiceTool.exe.

    python -m PyInstaller VoiceTool.spec --noconfirm

Что здесь важно и почему:
  * console=False — GUI без чёрного окна консоли (потоки вывода в app.py подменяются заглушкой);
  * faster_whisper тянет с собой onnx-модель VAD, ctranslate2 и av — свои DLL,
    sounddevice — portaudio.dll. Всё это PyInstaller сам не находит, собираем явно;
  * модели Whisper и перевода в exe НЕ кладутся: они большие и живут в папке пользователя,
    поэтому обновление exe их не трогает.
"""
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = []
binaries = []
hiddenimports = ["voicetool.gui.app"]

# ассеты и библиотеки, которые PyInstaller не видит по импортам
for package in ("faster_whisper", "sounddevice", "sentencepiece", "tokenizers"):
    datas += collect_data_files(package)
for package in ("ctranslate2", "av", "onnxruntime", "sounddevice", "sentencepiece"):
    binaries += collect_dynamic_libs(package)
datas += collect_data_files("onnxruntime")
hiddenimports += collect_submodules("av")
hiddenimports += ["ctranslate2", "sentencepiece", "onnxruntime", "sounddevice"]
hiddenimports += collect_submodules("pycaw")
hiddenimports += collect_submodules("comtypes")

# лишнее в сборке: тянет сотни мегабайт и не используется
excludes = [
    "tkinter", "test", "unittest", "pydoc_data", "matplotlib", "scipy", "pandas",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtDesigner",
    "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtNetworkAuth",
]

a = Analysis(
    ["VoiceTool.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI-приложение, консоль не нужна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="build_assets/VoiceTool.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoiceTool",
)
