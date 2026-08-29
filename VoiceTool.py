#!/usr/bin/env python3
"""Точка входа графического приложения. Из неё же собирается VoiceTool.exe.

  python VoiceTool.py             — открыть окно
  python VoiceTool.py --tray      — запустить свёрнутым в трей (используется автозапуском)
  python VoiceTool.py --listen    — сразу включить прослушивание
  python VoiceTool.py файл.mp4    — открыть и обработать файл

Командная строка старой версии осталась на месте: python voice_tool.py ...
"""
import sys


def main():
    try:
        from voicetool.gui.app import run
    except ImportError as e:
        _fatal("Не хватает библиотеки для интерфейса.\n\n"
               f"{e}\n\nУстановите зависимости:\n  pip install -r requirements.txt")
        return 2
    return run()


def _fatal(message):
    """Показать ошибку окном, если получится, и обязательно записать её в поток вывода."""
    print(message, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv[:1])
        QMessageBox.critical(None, "Voice Tool", message)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
