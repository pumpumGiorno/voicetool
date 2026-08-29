"""Дымовой тест интерфейса: собрать окно, обойти все страницы, снять скриншоты.

Запускается без человека — падение любой страницы будет видно сразу:
    python tools/gui_smoke.py [папка_для_скриншотов]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from voicetool import config, engine, logs, paths  # noqa: E402
from voicetool.gui import theme  # noqa: E402
from voicetool.gui.app import VoiceToolApp  # noqa: E402


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
    out.mkdir(parents=True, exist_ok=True)

    cfg = config.load()
    paths.migrate_legacy(cfg.data_dir)
    logs.setup(cfg.data_dir)

    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    qapp.setStyleSheet(theme.stylesheet())
    qapp.setQuitOnLastWindowClosed(False)
    app = VoiceToolApp(cfg, qapp)
    app.window.show()

    shots = []
    pages = ["home", "files", "stats", "history", "settings", "check"]

    def grab(name):
        path = out / f"{name}.png"
        app.window.grab().save(str(path))
        shots.append(path)
        print(f"  сняли {path}")

    def step(i=0):
        if i < len(pages):
            app.window.show_page(pages[i])
            QTimer.singleShot(320, lambda: (grab(pages[i]), step(i + 1)))
            return
        # состояния плавающего индикатора
        app.floating.show_wake()
        QTimer.singleShot(500, lambda: (app.floating.grab().save(str(out / "widget_wake.png")),
                                        app.floating.show_recording()))
        QTimer.singleShot(1100, lambda: (app.floating.grab().save(str(out / "widget_rec.png")),
                                         app.floating.show_result(
                                             "Напомни мне завтра купить молоко", 5)))
        QTimer.singleShot(1900, lambda: app.floating.grab().save(str(out / "widget_result.png")))
        QTimer.singleShot(2300, finish)

    def finish():
        app.window.pages["home"].set_state(engine.WAITING)
        grab("home_listening")
        print(f"\nOK: {len(shots) + 3} скриншотов в {out.resolve()}")
        app.quit()

    QTimer.singleShot(400, step)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
