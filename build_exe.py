#!/usr/bin/env python3
"""Собрать VoiceTool.exe.

    python build_exe.py            — обычная сборка в dist\\VoiceTool\\
    python build_exe.py --onefile  — дополнительно один файл dist\\VoiceTool-portable.exe

Папочная сборка — основная: она запускается быстрее и надёжнее. Однофайловая распаковывает
себя во временную папку при каждом старте (это заметные секунды на 300 МБ), поэтому она
предлагается как запасной вариант, а не как основной.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "build_assets" / "VoiceTool.ico"


def make_icon():
    """Иконка рисуется тем же кодом, что и в интерфейсе — отдельного .ico в репозитории нет."""
    ICON.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from voicetool.gui.widgets import app_icon

        _app = QApplication.instance() or QApplication(sys.argv[:1])
        sizes = [16, 24, 32, 48, 64, 128, 256]
        # QIcon сам сохранит .ico со всеми размерами, если добавить их как pixmap'ы
        icon = QIcon()
        for size in sizes:
            icon.addPixmap(app_icon(size).pixmap(size, size))
        icon.pixmap(256, 256).save(str(ICON.with_suffix(".png")))
        images = [app_icon(s).pixmap(s, s).toImage() for s in sizes]
        _save_ico(images, ICON)
        print(f"[Иконка] {ICON}")
        return True
    except Exception as e:
        print(f"[Иконка] Не удалось нарисовать ({e}) — соберём без неё")
        return False


def _save_ico(images, path):
    """Пишем .ico вручную: Qt умеет читать ico, но не записывать многоразмерный."""
    import struct
    from io import BytesIO

    from PySide6.QtCore import QBuffer, QByteArray

    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for image in images:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QBuffer.WriteOnly)
        image.save(buf, "PNG")
        buf.close()
        blob = bytes(data)
        size = image.width()
        entries.append(struct.pack("<BBBBHHII", size if size < 256 else 0,
                                   size if size < 256 else 0, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(images)))
        for entry in entries:
            f.write(entry)
        for blob in blobs:
            f.write(blob)


def build(onefile=False):
    args = [sys.executable, "-m", "PyInstaller", "VoiceTool.spec", "--noconfirm",
            "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build")]
    if onefile:
        args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
                "--name", "VoiceTool-portable", "--distpath", str(ROOT / "dist"),
                "--workpath", str(ROOT / "build-onefile"),
                "--specpath", str(ROOT / "build-onefile")]
        if ICON.exists():
            args += ["--icon", str(ICON)]
        for package in ("faster_whisper", "sounddevice", "sentencepiece", "tokenizers",
                        "onnxruntime"):
            args += ["--collect-data", package]
        for package in ("pycaw", "comtypes"):
            args += ["--collect-submodules", package]
        args += ["--hidden-import", "psutil"]
        for package in ("ctranslate2", "av", "onnxruntime", "sounddevice", "sentencepiece"):
            args += ["--collect-binaries", package]
        for module in ("tkinter", "matplotlib", "scipy", "pandas",
                       "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.QtQml"):
            args += ["--exclude-module", module]
        args += ["--hidden-import", "voicetool.gui.app", str(ROOT / "VoiceTool.py")]

    print("$ " + " ".join(args[:6]) + " ...")
    result = subprocess.run(args, cwd=ROOT)
    return result.returncode


def main():
    onefile = "--onefile" in sys.argv
    make_icon()
    code = build(onefile=False)
    if code:
        print("\nСборка не удалась.")
        return code
    exe = ROOT / "dist" / "VoiceTool" / "VoiceTool.exe"
    size = sum(f.stat().st_size for f in (ROOT / "dist" / "VoiceTool").rglob("*") if f.is_file())
    print(f"\nГотово: {exe}\nРазмер папки: {size / 1e6:.0f} МБ")

    if onefile:
        code = build(onefile=True)
        single = ROOT / "dist" / "VoiceTool-portable.exe"
        if not code and single.exists():
            print(f"Однофайловая версия: {single} ({single.stat().st_size / 1e6:.0f} МБ)")
    return code


if __name__ == "__main__":
    sys.exit(main())
