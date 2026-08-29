"""Запуск приложения: собирает окно, трей, плавающий индикатор и рабочие потоки.

Здесь же живёт политика «одно приложение за раз» и обработка аргументов командной
строки (--tray для автозапуска, путь к файлу для «Открыть с помощью»).
"""
import argparse
import io
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QSharedMemory, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import config, engine, hotkey, logs, paths
from ..counter import WordCounter
from ..processor import DONE, FAILED, SUPPORTED
from . import theme
from .bridge import ListenerBridge, ProcessorBridge
from .main_window import MainWindow
from .page_check import open_path
from .tray import Tray
from .voice_widget import FloatingWidget
from .widgets import app_icon

log = logging.getLogger(__name__)


class VoiceToolApp:
    def __init__(self, cfg, qapp: QApplication, source=None):
        self.cfg = cfg
        self.qapp = qapp
        self.source = source   # файл вместо микрофона: демонстрация и автотесты
        self.window = MainWindow(cfg)
        self.tray = Tray(qapp)
        self.floating = FloatingWidget()
        self.listener = ListenerBridge(cfg, qapp, source=source)
        self.files = ProcessorBridge(cfg, qapp)
        self.hotkey_thread = None

        self._wire_window()
        self._wire_tray()
        self._wire_listener()
        self._wire_files()
        self.tray.show()
        self.refresh_counter()
        self.apply_hotkey()

    # --- связи --------------------------------------------------------------

    def _wire_window(self):
        home = self.window.pages["home"]
        home.listen_toggled.connect(self.set_listening)
        home.files_dropped.connect(self.add_files)
        home.browse_clicked.connect(self.window.pages["files"]._browse)
        self.window.quit_requested.connect(self.quit)
        self.window.closed_to_tray.connect(
            lambda: self.tray.notify("Voice Tool работает в фоне",
                                     "Программа свёрнута в трей и продолжает слушать микрофон."
                                     if self.listener.running else
                                     "Программа свёрнута в трей. Значок — рядом с часами.")
            if self.cfg.show_notifications else None)

        settings = self.window.pages["settings"]
        settings.saved.connect(self.on_settings_saved)
        settings.restart_listener.connect(self.restart_listener)

        files_page = self.window.pages["files"]
        files_page.add_requested.connect(self.add_files)
        files_page.start_requested.connect(self.files.start)
        files_page.cancel_requested.connect(self.files.cancel)
        files_page.clear_requested.connect(self.files.clear_finished)

    def _wire_tray(self):
        self.tray.show_window.connect(self.show_window)
        self.tray.open_page.connect(self.open_page)
        self.tray.listen_requested.connect(self.set_listening)
        self.tray.pause_requested.connect(self.set_paused)
        self.tray.quit_requested.connect(self.quit)

    def _wire_listener(self):
        self.listener.state.connect(self.on_state)
        self.listener.level.connect(self.on_level)
        self.listener.recognized.connect(self.on_recognized)
        self.listener.counter.connect(self.on_counter)
        self.listener.status.connect(self.window.set_status)
        self.listener.error.connect(self.on_error)

    def _wire_files(self):
        page = self.window.pages["files"]
        self.files.queue.connect(page.set_queue)
        self.files.progress.connect(page.show_progress)
        self.files.job.connect(self.on_job)
        self.files.status.connect(self.window.set_status)
        self.files.finished.connect(self.on_files_finished)

    # --- прослушивание ------------------------------------------------------

    def set_listening(self, on: bool):
        if on and not self.listener.running:
            self.window.set_status("Запускаю микрофон...")
            self.listener.start()
        elif not on and self.listener.running:
            self.listener.stop()
            self.floating.dismiss()
        self.tray.set_listening(self.listener.running, self.listener.paused)

    def set_paused(self, value: bool):
        self.listener.set_paused(value)
        self.tray.set_listening(self.listener.running, value)

    def restart_listener(self):
        was = self.listener.running
        self.listener.apply_config(self.cfg, source=self.source)
        self._wire_listener()
        if was:
            self.listener.start()

    def trigger_hotkey(self):
        """Горячая клавиша: если микрофон выключен — включаем и сразу слушаем команду."""
        if not self.listener.running:
            self.set_listening(True)
            QTimer.singleShot(1500, self.listener.trigger)
        else:
            self.listener.trigger()

    def apply_hotkey(self):
        if self.hotkey_thread:
            self.hotkey_thread.stop()
            self.hotkey_thread = None
        if not self.cfg.hotkey_enabled:
            self.window.set_hotkey_hint("")
            return
        self.hotkey_thread = hotkey.HotkeyThread(self.cfg.hotkey, self._hotkey_fired)
        self.hotkey_thread.start()
        error = self.hotkey_thread.wait_ready()
        self.window.set_hotkey_hint(error or f"Горячая клавиша: {self.cfg.hotkey}")

    def _hotkey_fired(self):
        # приходит из чужого потока — переносим в поток интерфейса
        QTimer.singleShot(0, self.trigger_hotkey)

    # --- события слушателя --------------------------------------------------

    def on_state(self, state, detail):
        self.window.pages["home"].set_state(state, detail)
        self.window.set_mic_state(state)
        self.tray.set_listening(self.listener.running, self.listener.paused)
        if not self.cfg.show_floating_widget:
            self.floating.hide()
            return
        if state == engine.WAITING:
            self.floating.show_idle()
        elif state == engine.WAKE:
            self.floating.show_wake()
        elif state == engine.RECORDING:
            self.floating.show_recording()
        elif state == engine.THINKING:
            self.floating.show_thinking()
        elif state in (engine.IDLE, engine.PAUSED):
            self.floating.dismiss()

    def on_level(self, level, speaking):
        self.window.pages["home"].push_level(level, speaking)
        self.floating.push_level(level)

    def on_recognized(self, text, words):
        self.window.pages["home"].set_last(text, words)
        if self.cfg.show_floating_widget:
            self.floating.show_result(text, words)
        if self.cfg.output_mode in ("show", "insert_show"):
            self.show_window()
        page = self.window.pages["history"]
        if self.window.stack.currentWidget() is page:
            page.refresh()

    def on_counter(self, total, added):
        self.refresh_counter()

    def on_error(self, message):
        log.error("Ошибка слушателя: %s", message)
        self.window.set_status(message)
        if self.cfg.show_floating_widget:
            self.floating.show_error(message)
        if "икрофон" in message:
            self.set_listening(False)
            self.show_error_dialog("Микрофон", message)
        elif self.cfg.show_notifications:
            self.tray.notify("Voice Tool", message)

    def show_error_dialog(self, title, message):
        box = QMessageBox(self.window if self.window.isVisible() else None)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        box.setText(message.split("\n\n")[0])
        detail = message.split("\n\n", 1)[1] if "\n\n" in message else ""
        if detail:
            box.setInformativeText(detail)
        open_log = box.addButton("Открыть лог", QMessageBox.ActionRole)
        box.addButton("Закрыть", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_log:
            open_path(logs.today_log(self.cfg.data_dir))

    # --- файлы --------------------------------------------------------------

    def add_files(self, files):
        supported = [f for f in files if Path(f).suffix.lower() in SUPPORTED]
        skipped = len(files) - len(supported)
        self.files.add(files)
        self.open_page("files")
        if supported:
            self.files.start()
        if skipped:
            self.window.set_status(f"Пропущено файлов неподдерживаемого формата: {skipped}")

    def on_job(self, job):
        page = self.window.pages["files"]
        page.set_queue(self.files.jobs)
        page.set_running(self.files.running)
        if job.status in (DONE, FAILED):
            page.show_result(job)
            if self.cfg.show_notifications and job.status == DONE:
                self.tray.notify("Файл обработан", f"{job.name} — {job.words} слов")
        else:
            page.show_progress(job)

    def on_files_finished(self):
        page = self.window.pages["files"]
        page.set_running(False)
        page.set_queue(self.files.jobs)
        self.window.set_status("Обработка файлов завершена")

    # --- прочее -------------------------------------------------------------

    def refresh_counter(self):
        stats = WordCounter(self.cfg.data_dir).stats()
        self.window.pages["home"].set_counter(stats["total"], stats["today"])
        page = self.window.pages["stats"]
        if self.window.stack.currentWidget() is page:
            page.refresh()

    def on_settings_saved(self, changed):
        if "hotkey" in changed or "hotkey_enabled" in changed:
            self.apply_hotkey()
        if not self.cfg.show_floating_widget:
            self.floating.dismiss()
        self.files.apply_config(self.cfg)
        self.window.set_status("Настройки сохранены")

    def show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def open_page(self, key):
        self.show_window()
        self.window.show_page(key)

    def quit(self):
        log.info("Выход")
        if self.hotkey_thread:
            self.hotkey_thread.stop()
        self.listener.stop()
        self.files.cancel()
        self.floating.hide()
        self.tray.hide()
        self.qapp.quit()


def _silence_missing_streams():
    """У собранного GUI-exe нет консоли: sys.stdout/err = None, и любой print падал бы."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, io.StringIO())


def run(argv=None):
    parser = argparse.ArgumentParser(prog="VoiceTool", add_help=False)
    parser.add_argument("--tray", action="store_true", help="запуск свёрнутым в трей")
    parser.add_argument("--listen", action="store_true", help="сразу включить прослушивание")
    parser.add_argument("--source", help="взять звук из файла вместо микрофона (демо и автотесты)")
    parser.add_argument("files", nargs="*", help="файлы для обработки")
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    _silence_missing_streams()
    cfg = config.load()
    data_dir = cfg.data_dir
    paths.migrate_legacy(data_dir)
    logs.setup(data_dir)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    qapp.setApplicationName("Voice Tool")
    qapp.setOrganizationName("VoiceTool")
    qapp.setWindowIcon(app_icon())
    qapp.setQuitOnLastWindowClosed(False)   # живём в трее
    qapp.setStyleSheet(theme.stylesheet())

    guard = QSharedMemory("VoiceTool-single-instance")
    if not guard.create(1):
        QMessageBox.information(None, "Voice Tool",
                                "Voice Tool уже запущен — значок рядом с часами.")
        return 0

    app = VoiceToolApp(cfg, qapp, source=args.source)
    app._guard = guard  # держим ссылку, иначе блокировка снимется сборщиком мусора

    if not args.tray:
        app.show_window()
    if args.files:
        app.add_files(args.files)
    if args.listen or cfg.start_listening_on_launch:
        QTimer.singleShot(400, lambda: app.set_listening(True))

    log.info("Интерфейс запущен (tray=%s, data=%s, source=%s)", args.tray, data_dir, args.source)
    return qapp.exec()
