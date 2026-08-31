"""Compact, privacy-safe command history."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..history import History
from .ui_state import recent_agent_activity
from .widgets import AgentActivityItem, Button, label

FILTERS = [("Все", "all"), ("Agent", "agent"), ("Голос", "voice"), ("Файлы", "file")]


class HistoryPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("History", name="H1"))
        titles.addWidget(label(
            "Команды и результаты без скрытых рассуждений и служебных промптов",
            name="Muted"))
        head.addLayout(titles)
        head.addStretch()
        self.filter = QComboBox()
        self.filter.addItems([name for name, _ in FILTERS])
        self.filter.setFixedWidth(130)
        self.filter.currentIndexChanged.connect(self.refresh)
        clear = Button("Очистить", variant="ghost")
        clear.clicked.connect(self._clear)
        head.addWidget(self.filter)
        head.addWidget(clear)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 8, 0)
        self.body_lay.setSpacing(7)
        self.body_lay.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        selected = FILTERS[self.filter.currentIndex()][1]
        count = 0
        if selected in {"all", "agent"}:
            for row in recent_agent_activity(self.cfg.data_dir, 200):
                self.body_lay.addWidget(AgentActivityItem(row))
                count += 1
        if selected in {"all", "voice", "file"}:
            kind = None if selected == "all" else selected
            for row in History(self.cfg.data_dir).recent(200, kind=kind):
                if row.get("kind") == "agent":
                    continue
                self.body_lay.addWidget(self._transcript(row))
                count += 1
        if not count:
            title = "История Agent пока пуста" if selected == "agent" else "История пока пуста"
            self.body_lay.addWidget(label(title, name="H2"))
            self.body_lay.addWidget(label(
                "После первой команды здесь появятся время, команда и фактический результат.",
                name="Muted", wrap=True))

    def _transcript(self, row):
        activity = {
            "time": str(row.get("ts", "")).replace("T", " ")[11:16],
            "command": row.get("text") or "—",
            "result": ("Распознано из файла" if row.get("kind") == "file"
                       else "Распознано голосом"),
            "success": True,
            "action": "transcription",
            "target": str(row.get("source") or ""),
        }
        item = AgentActivityItem(activity)
        copy = QPushButton("Копировать", item)
        copy.setObjectName("Link")
        copy.clicked.connect(
            lambda _=None, text=row.get("text", ""):
            QApplication.clipboard().setText(text))
        item.layout().addWidget(copy, 0, Qt.AlignLeft)
        return item

    def _clear(self):
        answer = QMessageBox.question(
            self, "Очистить историю",
            "Удалить историю распознавания? Agent Activity хранится отдельно для безопасности.")
        if answer == QMessageBox.Yes:
            History(self.cfg.data_dir).clear()
            self.refresh()
