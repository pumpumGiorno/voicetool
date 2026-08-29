"""Проверка интерфейса без человека: страницы, настройки, drag & drop, трей, счётчик.

Окно реально создаётся и отрисовывается, виджеты нажимаются программно. Настройки
пишутся во временную папку — конфиг пользователя не трогаем.

    python tools/test_gui.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("VOICETOOL_DATA_DIR", tempfile.mkdtemp(prefix="voicetool-gui-"))

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from voicetool import config, engine, logs  # noqa: E402
from voicetool.counter import WordCounter  # noqa: E402
from voicetool.gui import theme  # noqa: E402
from voicetool.gui.app import VoiceToolApp  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main():
    data = Path(os.environ["VOICETOOL_DATA_DIR"])
    cfg = config.load()
    logs.setup(data, console=False)
    print(f"Данные теста: {data}\n")

    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    qapp.setStyleSheet(theme.stylesheet())
    qapp.setQuitOnLastWindowClosed(False)
    app = VoiceToolApp(cfg, qapp)
    app.window.show()
    qapp.processEvents()

    # --- страницы ---
    print("Страницы")
    for key in ("home", "files", "stats", "history", "settings", "check"):
        app.window.show_page(key)
        qapp.processEvents()
        page = app.window.stack.currentWidget()
        check(f"Страница «{key}» открывается", page is app.window.pages[key]
              and page.isVisible() and page.width() > 100)

    # --- счётчик на главной ---
    print("\nСчётчик слов")
    counter = WordCounter(data)
    counter.add("раз два три четыре пять")
    app.refresh_counter()
    qapp.processEvents()
    shown = app.window.pages["home"].total_card.value.text().replace(" ", " ")
    check("Счётчик на главной обновился", shown.strip().endswith("5"), f"показано {shown!r}")
    app.window.show_page("stats")
    qapp.processEvents()
    check("Статистика показывает те же числа",
          app.window.pages["stats"].cards["total"].value.text().strip().endswith("5"))
    check("График получил данные по дням",
          len(app.window.pages["stats"].chart.days) == 7)

    # --- перезапуск: счётчик переживает ---
    check("Счётчик прочитан с диска заново", WordCounter(data).total == 5)

    # --- настройки реально сохраняются ---
    print("\nНастройки")
    settings = app.window.pages["settings"]
    app.window.show_page("settings")
    qapp.processEvents()
    settings.fields["wake_word"].setText("марвин")
    settings.fields["press_enter"].setChecked(True)
    settings.fields["silence_seconds"].setValue(3.5)
    settings.mode_buttons["show"].setChecked(True)
    settings.fields["show_floating_widget"].setChecked(False)
    settings.fields["hotkey"].setText("Ctrl+Alt+Q")
    settings._save()
    qapp.processEvents()

    saved = json.loads((data / "config.json").read_text(encoding="utf-8"))
    check("Слово-триггер сохранено", saved["wake_word"] == "марвин")
    check("Слово-триггер добавлено в варианты произношения", "марвин" in saved["wake_word_aliases"])
    check("Enter сохранён", saved["press_enter"] is True)
    check("Пауза сохранена", abs(saved["silence_seconds"] - 3.5) < 0.01)
    check("Режим вывода сохранён", saved["output_mode"] == "show")
    check("Индикатор выключен", saved["show_floating_widget"] is False)
    check("Горячая клавиша сохранена", saved["hotkey"] == "Ctrl+Alt+Q")
    check("Изменения видны работающему приложению",
          cfg.wake_word == "марвин" and cfg.press_enter is True)
    check("Подсказка на главной обновилась под новое слово",
          "Марвин" in (app.window.pages["home"].hint_label.text()
                       if app.window.pages["home"].set_state(engine.WAITING) is None else ""))

    settings.fields["hotkey"].setText("не сочетание")
    check("Неверное сочетание отмечено подсказкой",
          bool(settings.hotkey_hint.text()), settings.hotkey_hint.text()[:50])
    settings.fields["hotkey"].setText("Ctrl+Alt+A")

    # --- drag & drop ---
    print("\nDrag & drop")
    home = app.window.pages["home"]
    dropped = []
    home.files_dropped.connect(lambda files: dropped.extend(files))
    mime = QMimeData()
    sample = ROOT / "samples" / "ru_lecture.mp3"
    mime.setUrls([QUrl.fromLocalFile(str(sample)), QUrl.fromLocalFile(str(ROOT / "README.md"))])
    event = QDropEvent(QPoint(30, 30), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    home.drop.dropEvent(event)
    qapp.processEvents()
    check("Аудиофайл принят зоной перетаскивания", dropped == [str(sample)],
          f"принято: {dropped}")
    check("Посторонний файл отброшен", str(ROOT / "README.md") not in dropped)

    # --- трей ---
    print("\nТрей и окно")
    check("Значок в трее создан", app.tray.isSystemTrayAvailable() and app.tray.isVisible())
    menu = app.tray.contextMenu()
    titles = [a.text() for a in menu.actions() if a.text()]
    check("В меню трея есть все пункты",
          all(t in titles for t in ("Открыть окно", "Включить прослушивание",
                                    "Пауза прослушивания", "Обработать файл",
                                    "Статистика", "История", "Настройки", "Выход")),
          ", ".join(titles))
    app.tray.open_page.emit("stats")
    qapp.processEvents()
    check("Пункт трея открывает нужную страницу",
          app.window.stack.currentWidget() is app.window.pages["stats"])

    cfg["minimize_to_tray"] = True
    app.window.close()
    qapp.processEvents()
    check("Закрытие окна сворачивает в трей, а не выходит",
          not app.window.isVisible() and app.tray.isVisible())
    app.show_window()
    qapp.processEvents()
    check("Окно возвращается из трея", app.window.isVisible())

    # --- автозапуск с Windows ---
    # Пишем в HKCU\...\Run и сразу возвращаем как было — состояние пользователя не меняется.
    print("\nАвтозапуск")
    from voicetool import autostart

    was_enabled = autostart.enabled()
    check("Автозапуск поддерживается на этой системе", autostart.supported())
    if autostart.supported():
        autostart.set_enabled(True)
        check("Автозапуск включается", autostart.enabled())
        autostart.set_enabled(False)
        check("Автозапуск выключается", not autostart.enabled())
        autostart.set_enabled(was_enabled)
        check("Исходное состояние автозапуска восстановлено",
              autostart.enabled() == was_enabled, f"было {was_enabled}")

    # --- горячая клавиша ---
    print("\nГорячая клавиша")
    from voicetool import hotkey as hk
    from voicetool import inject

    fired = []
    thread = hk.HotkeyThread("Ctrl+Alt+F9", lambda: fired.append(1))
    thread.start()
    error = thread.wait_ready()
    check("Горячая клавиша зарегистрирована в системе", not error, error or "")
    if not error:
        inject._send([inject._vk_event(inject.VK_CONTROL), inject._vk_event(inject.VK_MENU),
                      inject._vk_event(0x78), inject._vk_event(0x78, up=True),
                      inject._vk_event(inject.VK_MENU, up=True),
                      inject._vk_event(inject.VK_CONTROL, up=True)])
        import time as _t

        for _ in range(30):
            qapp.processEvents()
            if fired:
                break
            _t.sleep(0.1)
        check("Нажатие сочетания вызвало обработчик", bool(fired))
    thread.stop()

    # --- проверка системы ---
    app.window.show_page("check")
    qapp.processEvents()
    rows = app.window.pages["check"].rows
    check("Проверка системы собрала отчёт", len(rows) >= 10, f"{len(rows)} строк")
    check("FFmpeg показан как «не требуется», а не как ошибка",
          any(r["name"] == "FFmpeg" and r["status"] in ("ok", "info") for r in rows),
          next((r["detail"] for r in rows if r["name"] == "FFmpeg"), ""))

    app.quit()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
