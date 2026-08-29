"""Экран статистики: только живой режим (текст из файлов сюда не попадает)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ..counter import WordCounter
from . import theme
from .widgets import BarChart, StatCard, card, human_number, label, section


class StatsPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(label("Статистика", name="H1"))
        head.addStretch()
        root.addLayout(head)
        root.addWidget(label("Считаются только слова, сказанные в живом голосовом режиме. "
                             "Расшифровки файлов в счётчик не попадают.", name="Muted", wrap=True))

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {
            "total": StatCard("Всего слов", "0", accent=True),
            "today": StatCard("Сегодня", "0"),
            "week": StatCard("Последние 7 дней", "0"),
            "phrases": StatCard("Фраз", "0"),
            "sessions": StatCard("Сессий", "0"),
            "average": StatCard("Слов на фразу", "0"),
        }
        for i, key in enumerate(("total", "today", "week", "phrases", "sessions", "average")):
            grid.addWidget(self.cards[key], i // 3, i % 3)
        root.addLayout(grid)

        chart_card, chart_lay = card(padding=18, spacing=12)
        chart_lay.addWidget(section("Слова по дням"))
        self.chart = BarChart()
        chart_lay.addWidget(self.chart)
        root.addWidget(chart_card)

        self.updated = label("", name="Dim")
        root.addWidget(self.updated)
        root.addStretch()

    def refresh(self):
        stats = WordCounter(self.cfg.data_dir).stats()
        self.cards["total"].set_value(human_number(stats["total"]))
        self.cards["today"].set_value(human_number(stats["today"]))
        self.cards["week"].set_value(human_number(stats["week"]))
        self.cards["phrases"].set_value(human_number(stats["phrases"]))
        self.cards["sessions"].set_value(human_number(stats["sessions"]))
        phrases = stats["phrases"] or 0
        average = round(stats["total"] / phrases, 1) if phrases else 0
        self.cards["average"].set_value(average)
        self.chart.set_days(stats["last_days"])
        self.updated.setText(f"Данные: {self.cfg.data_dir}")
