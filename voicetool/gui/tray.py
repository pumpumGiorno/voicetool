"""Иконка в системном трее и её меню.

Из-за трея программа продолжает работать с закрытым главным окном — ради этого
плавающий индикатор и существует.
"""
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .widgets import app_icon


class Tray(QSystemTrayIcon):
    show_window = Signal()
    open_page = Signal(str)
    listen_requested = Signal(bool)   # True = включить, False = выключить
    pause_requested = Signal(bool)
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(app_icon(), parent)
        self.setToolTip("Voice Tool")
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self):
        menu = QMenu()
        header = QAction("VOICE TOOL", menu)
        header.setEnabled(False)
        menu.addAction(header)
        menu.addSeparator()

        self.open_action = QAction("Открыть окно", menu)
        self.open_action.triggered.connect(self.show_window)
        menu.addAction(self.open_action)

        self.listen_action = QAction("Включить прослушивание", menu)
        self.listen_action.triggered.connect(
            lambda: self.listen_requested.emit(not self._listening))
        menu.addAction(self.listen_action)

        self.pause_action = QAction("Пауза прослушивания", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.setEnabled(False)
        self.pause_action.toggled.connect(self.pause_requested)
        menu.addAction(self.pause_action)
        menu.addSeparator()

        for title, page in (("Обработать файл", "files"), ("Статистика", "stats"),
                            ("История", "history"), ("Настройки", "settings")):
            action = QAction(title, menu)
            action.triggered.connect(lambda _=False, p=page: self.open_page.emit(p))
            menu.addAction(action)
        menu.addSeparator()

        quit_action = QAction("Выход", menu)
        quit_action.triggered.connect(self.quit_requested)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self._listening = False

    def set_listening(self, listening: bool, paused=False):
        self._listening = listening
        self.listen_action.setText("Отключить микрофон" if listening else "Включить прослушивание")
        self.pause_action.setEnabled(listening)
        self.pause_action.blockSignals(True)
        self.pause_action.setChecked(paused)
        self.pause_action.blockSignals(False)
        state = "пауза" if (listening and paused) else ("слушает" if listening else "выключен")
        self.setToolTip(f"Voice Tool — микрофон {state}")
        self.setIcon(app_icon(listening=listening and not paused))

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window.emit()

    def notify(self, title, message, ms=3500):
        if self.supportsMessages():
            self.showMessage(title, message, app_icon(), ms)
