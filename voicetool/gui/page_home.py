"""Главный экран: состояние микрофона, зона перетаскивания файлов, счётчик слов."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import engine
from ..processor import SUPPORTED
from . import theme
from .widgets import (LevelMeter, MicOrb, StatCard, card, human_number, label, plural_words)

STATE_TEXT = {
    engine.IDLE: ("МИКРОФОН ВЫКЛЮЧЕН", "Нажмите «Включить прослушивание»"),
    engine.WAITING: ("ГОТОВ", "Скажите «{wake}»"),
    engine.WAKE: ("СЛУШАЮ", "Говорите — я жду вашу фразу"),
    engine.RECORDING: ("ЗАПИСЬ", "Закончите фразу и сделайте паузу"),
    engine.THINKING: ("РАСПОЗНАЮ", "Секунду..."),
    engine.DONE: ("ГОТОВ", "Скажите «{wake}»"),
    engine.PAUSED: ("ПАУЗА", "Прослушивание приостановлено"),
}


class DropZone(QFrame):
    """Зона Drag & Drop. Принимает и один файл, и несколько сразу."""

    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignCenter)
        title = label("Перетащите сюда аудио или видеофайл", size=15)
        title.setAlignment(Qt.AlignCenter)
        formats = label("MP3 · WAV · M4A · FLAC · OGG · MP4 · MKV · AVI · MOV", name="Dim")
        formats.setAlignment(Qt.AlignCenter)
        self.browse = QPushButton("Выбрать файлы")
        self.browse.setObjectName("Ghost")
        self.browse.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.browse)
        row.addStretch()
        lay.addWidget(title)
        lay.addWidget(formats)
        lay.addSpacing(6)
        lay.addLayout(row)

    def dragEnterEvent(self, event):
        if _paths(event):
            event.acceptProposedAction()
            self.setObjectName("DropZoneActive")
            self._restyle()

    def dragLeaveEvent(self, event):
        self.setObjectName("DropZone")
        self._restyle()

    def dropEvent(self, event):
        files = _paths(event)
        self.setObjectName("DropZone")
        self._restyle()
        if files:
            event.acceptProposedAction()
            self.dropped.emit(files)

    def _restyle(self):
        self.style().unpolish(self)
        self.style().polish(self)


def _paths(event):
    data = event.mimeData()
    if not data.hasUrls():
        return []
    out = []
    for url in data.urls():
        p = Path(url.toLocalFile())
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            out.append(str(p))
    return out


class HomePage(QWidget):
    listen_toggled = Signal(bool)
    files_dropped = Signal(list)
    browse_clicked = Signal()
    open_files_page = Signal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # --- состояние микрофона ---
        status_card, status_lay = card(padding=22, spacing=10)
        status_lay.setAlignment(Qt.AlignHCenter)
        self.orb = MicOrb()
        orb_row = QHBoxLayout()
        orb_row.addStretch()
        orb_row.addWidget(self.orb)
        orb_row.addStretch()
        status_lay.addLayout(orb_row)

        self.state_label = label("МИКРОФОН ВЫКЛЮЧЕН")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setStyleSheet(
            f"font-size: 17px; font-weight: 700; letter-spacing: 2px; color: {theme.TEXT};")
        self.hint_label = label("Нажмите «Включить прослушивание»", name="Muted")
        self.hint_label.setAlignment(Qt.AlignCenter)
        status_lay.addWidget(self.state_label)
        status_lay.addWidget(self.hint_label)

        self.meter = LevelMeter()
        self.meter.setMaximumWidth(360)
        meter_row = QHBoxLayout()
        meter_row.addStretch()
        meter_row.addWidget(self.meter)
        meter_row.addStretch()
        status_lay.addLayout(meter_row)

        self.listen_btn = QPushButton("Включить прослушивание")
        self.listen_btn.setObjectName("Primary")
        self.listen_btn.setCursor(Qt.PointingHandCursor)
        self.listen_btn.setCheckable(True)
        self.listen_btn.clicked.connect(lambda: self.listen_toggled.emit(self.listen_btn.isChecked()))
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.listen_btn)
        btn_row.addStretch()
        status_lay.addSpacing(4)
        status_lay.addLayout(btn_row)

        self.last_label = label("", name="Dim", wrap=True)
        self.last_label.setAlignment(Qt.AlignCenter)
        status_lay.addWidget(self.last_label)
        root.addWidget(status_card)

        # --- перетаскивание файлов ---
        self.drop = DropZone()
        self.drop.dropped.connect(self.files_dropped)
        self.drop.browse.clicked.connect(self.browse_clicked)
        root.addWidget(self.drop)

        # --- счётчик ---
        counters = QHBoxLayout()
        counters.setSpacing(14)
        self.today_card = StatCard("Сегодня", "0", "живой режим", accent=True)
        self.total_card = StatCard("Всего", "0", "живой режим")
        counters.addWidget(self.today_card)
        counters.addWidget(self.total_card)
        root.addLayout(counters)
        root.addStretch()

    # --- обновление ---------------------------------------------------------

    def set_state(self, state, detail=""):
        title, hint = STATE_TEXT.get(state, STATE_TEXT[engine.IDLE])
        self.state_label.setText(title)
        self.hint_label.setText(hint.format(wake=self.cfg.wake_word.capitalize()))
        active = state in (engine.WAKE, engine.RECORDING, engine.THINKING)
        self.orb.set_active(active)
        color = theme.ACCENT if active else (theme.TEXT if state != engine.IDLE else theme.MUTED)
        self.state_label.setStyleSheet(
            f"font-size: 17px; font-weight: 700; letter-spacing: 2px; color: {color};")
        listening = state != engine.IDLE
        self.listen_btn.setChecked(listening)
        self.listen_btn.setText("Остановить прослушивание" if listening else "Включить прослушивание")
        if state == engine.IDLE:
            self.meter.reset()

    def push_level(self, level, speaking):
        self.meter.push(level)

    def set_last(self, text, words):
        if not text:
            self.last_label.setText("")
            return
        short = text if len(text) <= 90 else text[:89] + "…"
        self.last_label.setText(f"Последнее: «{short}»   +{words} {plural_words(words)}")

    def set_counter(self, total, today):
        self.total_card.set_value(human_number(total))
        self.today_card.set_value(human_number(today))
