"""История распознавания. Голосовые команды и разобранные файлы помечены по-разному."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..history import History
from . import theme
from .widgets import card, label, plural_words

FILTERS = [("Все", None), ("Голос", "voice"), ("Файлы", "file")]


class HistoryPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(label("История", name="H1"))
        head.addStretch()
        self.filter = QComboBox()
        self.filter.addItems([name for name, _ in FILTERS])
        self.filter.setFixedWidth(130)
        self.filter.currentIndexChanged.connect(self.refresh)
        clear = QPushButton("Очистить")
        clear.setObjectName("Ghost")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self._clear)
        head.addWidget(self.filter)
        head.addWidget(clear)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 8, 0)
        self.body_lay.setSpacing(10)
        self.body_lay.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        kind = FILTERS[self.filter.currentIndex()][1]
        rows = History(self.cfg.data_dir).recent(200, kind=kind)
        if not rows:
            self.body_lay.addWidget(label("Пока пусто.", name="Muted"))
            return
        for row in rows:
            self.body_lay.addWidget(self._entry(row))

    def _entry(self, row):
        frame, lay = card(padding=14, spacing=6)
        head = QHBoxLayout()
        when = row.get("ts", "").replace("T", "  ")
        head.addWidget(label(when, name="Dim"))
        head.addStretch()
        if row.get("kind") == "file":
            tag = label(f"ФАЙЛ · {row.get('source', '')}", name="Dim")
            tag.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px; font-weight: 600;")
        else:
            n = row.get("words", 0)
            tag = label(f"+{n} {plural_words(n)}", name="Accent")
            tag.setStyleSheet(f"color: {theme.ACCENT}; font-size: 12px; font-weight: 600;")
        head.addWidget(tag)
        lay.addLayout(head)

        text = label(row.get("text") or "—", wrap=True, size=13)
        lay.addWidget(text)

        copy = QPushButton("Копировать")
        copy.setObjectName("Link")
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda _=None, t=row.get("text", ""): QApplication.clipboard().setText(t))
        row_btn = QHBoxLayout()
        row_btn.addWidget(copy)
        row_btn.addStretch()
        lay.addLayout(row_btn)
        return frame

    def _clear(self):
        answer = QMessageBox.question(self, "Очистить историю",
                                      "Удалить все записи истории?\n"
                                      "Счётчик слов при этом не изменится.")
        if answer == QMessageBox.Yes:
            History(self.cfg.data_dir).clear()
            self.refresh()
