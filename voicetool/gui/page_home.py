"""Alice-first home screen with factual agent state and compact activity."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import engine
from ..processor import SUPPORTED
from . import theme
from .ui_state import AliceState, normalize_state, presentation
from .widgets import AgentActivityItem, AliceCore, Button, VoiceWaveform, label


class DropZone(QFrame):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(76)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label("Расшифровать аудио или видео", name="H2"))
        text.addWidget(label("Перетащите файл сюда — обработка останется локальной", name="Dim"))
        lay.addLayout(text, 1)
        self.browse = Button("Выбрать файл", variant="ghost")
        lay.addWidget(self.browse)

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
    return [str(path) for url in data.urls()
            if (path := Path(url.toLocalFile())).is_file()
            and path.suffix.lower() in SUPPORTED]


class HomePage(QWidget):
    listen_toggled = Signal(bool)
    files_dropped = Signal(list)
    browse_clicked = Signal()
    command_submitted = Signal(str)
    cancel_requested = Signal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._state = AliceState.IDLE
        self._listening = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(theme.SPACING["lg"])
        scroll.setWidget(body)
        outer.addWidget(scroll)

        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Alice", name="H1"))
        titles.addWidget(label("Локальный помощник для Windows", name="Muted"))
        heading.addLayout(titles)
        heading.addStretch()
        self.privacy = label("LOCAL · PRIVATE", name="SectionTitle")
        self.privacy.setStyleSheet(
            f"color: {theme.DIM}; font-size: 10px; letter-spacing: 1.2px;")
        heading.addWidget(self.privacy)
        root.addLayout(heading)

        core_area = QVBoxLayout()
        core_area.setSpacing(6)
        core_area.setAlignment(Qt.AlignCenter)
        self.orb = AliceCore(184)
        core_area.addWidget(self.orb, 0, Qt.AlignHCenter)
        self.state_label = label("Готова", name="Display")
        self.state_label.setAlignment(Qt.AlignCenter)
        core_area.addWidget(self.state_label)
        self.hint_label = label("Скажите «Алиса» или введите команду", name="Muted", wrap=True)
        self.hint_label.setAlignment(Qt.AlignCenter)
        core_area.addWidget(self.hint_label)
        self.action_label = label("", name="Dim", wrap=True)
        self.action_label.setAlignment(Qt.AlignCenter)
        core_area.addWidget(self.action_label)
        self.meter = VoiceWaveform()
        self.meter.setFixedWidth(280)
        self.meter.setMaximumHeight(30)
        core_area.addWidget(self.meter, 0, Qt.AlignHCenter)
        root.addLayout(core_area, 1)

        command_surface = QFrame()
        command_surface.setObjectName("QuietSurface")
        command = QHBoxLayout(command_surface)
        command.setContentsMargins(10, 8, 10, 8)
        command.setSpacing(8)
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Напишите команду для Alice")
        self.command_input.setAccessibleName("Команда для Alice")
        self.command_input.returnPressed.connect(self._submit)
        command.addWidget(self.command_input, 1)
        self.listen_btn = Button("Слушать", variant="secondary")
        self.listen_btn.setCheckable(True)
        self.listen_btn.clicked.connect(
            lambda: self.listen_toggled.emit(self.listen_btn.isChecked()))
        command.addWidget(self.listen_btn)
        self.run_btn = Button("Выполнить", variant="primary")
        self.run_btn.clicked.connect(self._submit)
        command.addWidget(self.run_btn)
        self.cancel_btn = Button("Отменить", variant="danger")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_requested)
        command.addWidget(self.cancel_btn)
        root.addWidget(command_surface)

        self.last_label = label("Последняя команда появится здесь", name="Dim", wrap=True)
        root.addWidget(self.last_label)

        recent_head = QHBoxLayout()
        recent_head.addWidget(label("Недавняя активность", name="H2"))
        recent_head.addStretch()
        self.activity_hint = label("Нет действий", name="Dim")
        recent_head.addWidget(self.activity_hint)
        root.addLayout(recent_head)
        self.activity = QVBoxLayout()
        self.activity.setSpacing(6)
        self.empty_activity = label(
            "Alice покажет здесь только команды и фактические результаты — без скрытых рассуждений.",
            name="Muted", wrap=True)
        self.activity.addWidget(self.empty_activity)
        root.addLayout(self.activity)

        self.drop = DropZone()
        self.drop.dropped.connect(self.files_dropped)
        self.drop.browse.clicked.connect(self.browse_clicked)
        root.addWidget(self.drop)

    def _submit(self):
        text = self.command_input.text().strip()
        if text:
            self.command_input.clear()
            self.command_submitted.emit(text)

    def set_state(self, state, detail=""):
        raw_state = getattr(state, "value", state)
        ui_state = normalize_state(state)
        self._state = ui_state
        view = presentation(ui_state, detail=detail)
        self.state_label.setText(view.title)
        self.hint_label.setText(view.detail)
        self.orb.set_state(ui_state, reduce_motion=self.cfg.get("reduce_animations", False))
        color = {
            "accent": theme.ACCENT, "success": theme.OK, "warning": theme.WARN,
            "danger": theme.FAIL, "muted": theme.TEXT,
        }[view.color_key]
        self.state_label.setStyleSheet(
            f"font-size: 28px; font-weight: 600; color: {color};")
        busy = raw_state in {
            "understanding", "processing", "executing", "waiting_confirmation"}
        self.cancel_btn.setVisible(busy)
        self.run_btn.setEnabled(not busy)
        if raw_state in {
                engine.IDLE, engine.WAITING, engine.WAKE, engine.RECORDING,
                engine.THINKING, engine.DONE, engine.PAUSED}:
            self._listening = raw_state != engine.IDLE
        self.listen_btn.setChecked(self._listening)
        self.listen_btn.setText("Остановить" if self._listening else "Слушать")
        if ui_state == AliceState.IDLE:
            self.meter.reset()

    def set_agent_event(self, event):
        status = getattr(event, "status", AliceState.IDLE)
        self.set_state(status, getattr(event, "message", ""))
        tool = getattr(event, "tool", None)
        self.action_label.setText(f"Действие: {tool}" if tool else "")

    def push_level(self, level, speaking):
        del speaking
        self.meter.push(level)
        self.orb.set_amplitude(level)

    def set_last(self, text, words=0):
        del words
        short = " ".join(str(text or "").split())
        self.last_label.setText(
            f"Последняя команда: {short[:139] + ('…' if len(short) > 140 else '')}"
            if short else "Последняя команда появится здесь")

    def set_counter(self, total, today):
        self.privacy.setToolTip(f"Распознано сегодня: {today}; всего: {total}")

    def set_activity(self, rows):
        while self.activity.count():
            item = self.activity.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        visible = list(rows or [])[:3]
        self.activity_hint.setText(f"{len(visible)} последних" if visible else "Нет действий")
        if not visible:
            self.activity.addWidget(label(
                "Alice покажет здесь команды и фактические результаты.",
                name="Muted", wrap=True))
            return
        for row in visible:
            self.activity.addWidget(AgentActivityItem(row))
