"""Проверка системы — то же, что CLI-команда check, но с кнопками.

«Исправить проблемы» не пытается чинить всё подряд: он делает ровно две понятные вещи —
скачивает модель распознавания и открывает папку данных / лог. Всё остальное показывает
командой, которую можно скопировать.
"""
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import deps, logs
from . import theme
from .widgets import card, label, section

MARK = {"ok": "✓", "warn": "!", "fail": "✕", "info": "—"}


class ModelDownloader(QThread):
    """Скачивание модели в отдельном потоке: интерфейс не должен замирать."""
    done = Signal(bool, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        try:
            from ..asr import ASR

            ASR(self.cfg).model
            self.done.emit(True, f"Модель «{self.cfg.model}» готова.")
        except Exception as e:
            self.done.emit(False, str(e))


class CheckPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.downloader = None
        self.rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(label("Проверка системы", name="H1"))
        head.addStretch()
        refresh = QPushButton("Проверить снова")
        refresh.setObjectName("Ghost")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        root.addLayout(head)

        self.rows_card, self.rows_lay = card(padding=16, spacing=2)
        root.addWidget(self.rows_card)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        actions = QHBoxLayout()
        self.fix_btn = QPushButton("Скачать модель распознавания")
        self.fix_btn.setObjectName("Primary")
        self.fix_btn.setCursor(Qt.PointingHandCursor)
        self.fix_btn.clicked.connect(self._download_model)
        for text, slot in (("Открыть папку данных", self._open_data),
                           ("Открыть лог", self._open_log),
                           ("Скопировать отчёт", self._copy)):
            btn = QPushButton(text)
            btn.setObjectName("Ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        actions.addStretch()
        actions.addWidget(self.fix_btn)
        root.addLayout(actions)

        self.paths_card, paths_lay = card(padding=16, spacing=6)
        paths_lay.addWidget(section("Где что лежит"))
        self.paths_label = label("", name="Muted", wrap=True)
        self.paths_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        paths_lay.addWidget(self.paths_label)
        root.addWidget(self.paths_card)
        root.addStretch()

    def refresh(self):
        while self.rows_lay.count():
            item = self.rows_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = deps.system_report(self.cfg)
        for row in self.rows:
            self.rows_lay.addWidget(self._row(row))

        needs_model = any(r["name"] == "Модель Whisper" and r["status"] != "ok" for r in self.rows)
        self.fix_btn.setVisible(needs_model)
        self.paths_label.setText(
            f"Данные и статистика: {self.cfg.data_dir}\n"
            f"Настройки: {self.cfg.get('_path')}\n"
            f"Логи: {self.cfg.data_dir / 'logs'}\n"
            f"Модели перевода: {self.cfg.data_dir / 'models' / 'translate'}\n"
            f"Модели Whisper: {self.cfg.models_dir or 'кэш HuggingFace в профиле пользователя'}")

    def _row(self, row):
        frame = QFrame()
        frame.setMinimumHeight(34)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(12)
        color = theme.STATUS_COLOR[row["status"]]
        mark = label(MARK[row["status"]])
        mark.setFixedWidth(18)
        mark.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: 700;")
        name = label(row["name"])
        name.setFixedWidth(150)
        name.setStyleSheet("font-size: 13px;")
        detail = label(row["detail"], name="Muted", wrap=True)
        lay.addWidget(mark)
        lay.addWidget(name)
        lay.addWidget(detail, 1)
        if row["hint"]:
            hint = label(row["hint"], name="Dim")
            hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(hint)
        return frame

    # --- действия -----------------------------------------------------------

    def _download_model(self):
        if self.downloader and self.downloader.isRunning():
            return
        size = deps.MODEL_SIZES.get(self.cfg.model, "?")
        answer = QMessageBox.question(
            self, "Скачать модель",
            f"Модель «{self.cfg.model}» (~{size}) будет скачана из интернета один раз.\n"
            f"После этого распознавание работает полностью офлайн.\n\nПродолжить?")
        if answer != QMessageBox.Yes:
            return
        self.progress.setVisible(True)
        self.fix_btn.setEnabled(False)
        self.downloader = ModelDownloader(self.cfg, self)
        self.downloader.done.connect(self._download_done)
        self.downloader.start()

    def _download_done(self, ok, message):
        self.progress.setVisible(False)
        self.fix_btn.setEnabled(True)
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "Модель", message if ok else f"Не удалось скачать модель:\n{message}")
        self.refresh()

    def _open_data(self):
        open_path(self.cfg.data_dir)

    def _open_log(self):
        path = logs.today_log(self.cfg.data_dir)
        if path.exists():
            open_path(path)
        else:
            QMessageBox.information(self, "Лог", f"Файл ещё не создан:\n{path}")

    def _copy(self):
        lines = [f"{MARK[r['status']]} {r['name']}: {r['detail']}"
                 + (f"  -> {r['hint']}" if r["hint"] else "") for r in self.rows]
        lines.append(f"\nДанные: {self.cfg.data_dir}")
        QApplication.clipboard().setText("\n".join(lines))


def open_path(path):
    """Открыть файл или папку системным способом."""
    path = str(path)
    try:
        if sys.platform == "win32":
            import os

            os.startfile(path)  # noqa: S606 — путь наш, не пользовательский ввод
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass
