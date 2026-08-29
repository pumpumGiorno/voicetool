"""Главное окно: своя рамка, боковая навигация, страницы.

Рамка своя (FramelessWindowHint), поэтому изменение размера сделано вручную через
WM_NCHITTEST — так остаются рабочими и системный Snap, и перетаскивание между мониторами.
"""
import ctypes
import ctypes.wintypes
import sys

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import engine
from . import theme
from .page_check import CheckPage
from .page_files import FilesPage
from .page_history import HistoryPage
from .page_home import HomePage
from .page_settings import SettingsPage
from .page_stats import StatsPage
from .widgets import app_icon, divider, label, shadow

BORDER = 6  # ширина зоны захвата для изменения размера

PAGES = [
    ("home", "Главная"),
    ("files", "Файлы"),
    ("stats", "Статистика"),
    ("history", "История"),
    ("settings", "Настройки"),
    ("check", "Проверка"),
]


class MainWindow(QWidget):
    closed_to_tray = Signal()
    quit_requested = Signal()
    listen_toggled = Signal(bool)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Voice Tool")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1000, 720)
        self.setMinimumSize(860, 620)
        self._drag_offset = None

        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(10, 10, 10, 10)
        self.root = QWidget()
        self.root.setObjectName("Root")
        shadow(self.root, blur=40, alpha=190, dy=4)
        wrapper.addWidget(self.root)

        outer = QVBoxLayout(self.root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._title_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._sidebar())

        self.stack = QStackedWidget()
        self.pages = {
            "home": HomePage(cfg),
            "files": FilesPage(cfg),
            "stats": StatsPage(cfg),
            "history": HistoryPage(cfg),
            "settings": SettingsPage(cfg),
            "check": CheckPage(cfg),
        }
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(24, 22, 24, 20)
        content_lay.addWidget(self.stack)
        for key, _ in PAGES:
            self.stack.addWidget(self.pages[key])
        body.addWidget(content, 1)
        outer.addLayout(body, 1)
        outer.addWidget(self._status_bar())

        self.show_page("home")

    # --- шапка --------------------------------------------------------------

    def _title_bar(self):
        bar = QWidget()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(46)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 8, 0)
        lay.setSpacing(10)

        dot = QLabel("◉")
        dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 14px;")
        title = QLabel("VOICE TOOL")
        title.setObjectName("AppTitle")
        lay.addWidget(dot)
        lay.addWidget(title)
        lay.addStretch()

        self.mic_pill = QLabel("микрофон выключен")
        self.mic_pill.setObjectName("Muted")
        lay.addWidget(self.mic_pill)
        lay.addSpacing(8)

        for text, name, slot in (("─", "WinBtn", self.showMinimized),
                                 ("□", "WinBtn", self._toggle_max),
                                 ("✕", "WinBtnClose", self.close)):
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.setCursor(Qt.ArrowCursor)
            btn.clicked.connect(slot)
            lay.addWidget(btn)
        bar.mouseDoubleClickEvent = lambda e: self._toggle_max()
        self._title_widget = bar
        return bar

    def _sidebar(self):
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(196)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(12, 16, 12, 14)
        lay.setSpacing(4)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = {}
        for key, title in PAGES:
            btn = QPushButton(f"  {title}")
            btn.setObjectName("NavBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            lay.addWidget(btn)
        lay.addStretch()

        self.hotkey_hint = label("", name="Dim", wrap=True)
        lay.addWidget(self.hotkey_hint)
        return side

    def _status_bar(self):
        bar = QWidget()
        bar.setFixedHeight(30)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 6)
        self.status_label = label("", name="Dim")
        lay.addWidget(self.status_label)
        lay.addStretch()
        self.offline_label = label("Работает локально · аудио не уходит в интернет", name="Dim")
        lay.addWidget(self.offline_label)
        return bar

    # --- навигация ----------------------------------------------------------

    def show_page(self, key):
        self.stack.setCurrentWidget(self.pages[key])
        self.nav_buttons[key].setChecked(True)
        refresh = getattr(self.pages[key], "refresh", None)
        if refresh:
            refresh()

    def set_status(self, text):
        self.status_label.setText(text)

    def set_mic_state(self, state):
        titles = {engine.IDLE: "микрофон выключен", engine.WAITING: "жду «{w}»",
                  engine.WAKE: "слушаю", engine.RECORDING: "запись",
                  engine.THINKING: "распознаю", engine.DONE: "жду «{w}»",
                  engine.PAUSED: "пауза"}
        text = titles.get(state, "").format(w=self.cfg.wake_word)
        color = theme.ACCENT if state not in (engine.IDLE, engine.PAUSED) else theme.MUTED
        self.mic_pill.setText(text)
        self.mic_pill.setStyleSheet(f"color: {color}; font-size: 12px;")

    def set_hotkey_hint(self, text):
        self.hotkey_hint.setText(text)

    # --- поведение окна -----------------------------------------------------

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, event):
        # у развёрнутого окна свои поля: иначе тень «съедает» край экрана
        if event.type() == event.Type.WindowStateChange:
            margin = 0 if self.isMaximized() else 10
            self.layout().setContentsMargins(margin, margin, margin, margin)
        super().changeEvent(event)

    def closeEvent(self, event):
        if self.cfg.minimize_to_tray:
            event.ignore()
            self.hide()
            self.closed_to_tray.emit()
        else:
            event.accept()
            self.quit_requested.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_title(event.position().toPoint()):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset and event.buttons() & Qt.LeftButton:
            if self.isMaximized():
                self.showNormal()
                self._drag_offset = QPoint(self.width() // 2, 20)
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def _on_title(self, pos):
        rect = QRect(self._title_widget.mapTo(self, QPoint(0, 0)), self._title_widget.size())
        child = self.childAt(pos)
        return rect.contains(pos) and not isinstance(child, QPushButton)

    def nativeEvent(self, event_type, message):
        """Изменение размера за края окна — как у обычного окна Windows."""
        if sys.platform != "win32" or event_type != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message != 0x0084 or self.isMaximized():  # WM_NCHITTEST
            return super().nativeEvent(event_type, message)

        ratio = self.devicePixelRatioF()
        x = ctypes.c_short(msg.lParam & 0xFFFF).value / ratio - self.frameGeometry().x()
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value / ratio - self.frameGeometry().y()
        w, h = self.width(), self.height()
        pad = 10  # прозрачное поле вокруг Root, в нём и ловим края
        left, right = x < pad + BORDER, x > w - pad - BORDER
        top, bottom = y < pad + BORDER, y > h - pad - BORDER
        code = {(True, False, True, False): 13,   # HTTOPLEFT
                (False, True, True, False): 14,   # HTTOPRIGHT
                (True, False, False, True): 16,   # HTBOTTOMLEFT
                (False, True, False, True): 17,   # HTBOTTOMRIGHT
                (True, False, False, False): 10,  # HTLEFT
                (False, True, False, False): 11,  # HTRIGHT
                (False, False, True, False): 12,  # HTTOP
                (False, False, False, True): 15,  # HTBOTTOM
                }.get((left, right, top, bottom))
        if code:
            return True, code
        return super().nativeEvent(event_type, message)
