"""Экран обработки файлов: очередь, прогресс, результат, экспорт TXT/SRT/VTT."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget)

from ..processor import CANCELLED, DONE, FAILED, QUEUED, RUNNING, SUPPORTED
from ..translate import lang_name
from . import theme
from .widgets import card, human_time, label, section

MARKS = {QUEUED: "○", RUNNING: "◐", DONE: "✓", FAILED: "✕", CANCELLED: "—"}
MARK_COLOR = {QUEUED: theme.DIM, RUNNING: theme.ACCENT, DONE: theme.OK,
              FAILED: theme.FAIL, CANCELLED: theme.DIM}

FILE_FILTER = ("Аудио и видео (" + " ".join(f"*{e}" for e in sorted(SUPPORTED)) + ");;Все файлы (*)")


class FilesPage(QWidget):
    add_requested = Signal(list)
    start_requested = Signal()
    cancel_requested = Signal()
    clear_requested = Signal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.current = None
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(label("Обработка файлов", name="H1"))
        header.addStretch()
        self.add_btn = QPushButton("Добавить файлы")
        self.add_btn.setObjectName("Ghost")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._browse)
        self.start_btn = QPushButton("Начать обработку")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self.start_requested)
        self.start_btn.setEnabled(False)   # пока очередь пуста, кнопка ни к чему
        self.cancel_btn = QPushButton("Отменить")
        self.cancel_btn.setObjectName("Ghost")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_requested)
        header.addWidget(self.add_btn)
        header.addWidget(self.cancel_btn)
        header.addWidget(self.start_btn)
        root.addLayout(header)

        # --- очередь ---
        queue_card, queue_lay = card(padding=14, spacing=8)
        row = QHBoxLayout()
        row.addWidget(section("Очередь"))
        row.addStretch()
        self.clear_btn = QPushButton("Очистить готовые")
        self.clear_btn.setObjectName("Link")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_requested)
        row.addWidget(self.clear_btn)
        queue_lay.addLayout(row)
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(150)
        self.queue_list.itemClicked.connect(self._pick)
        self.empty_hint = label("Очередь пуста — перетащите файлы сюда или нажмите «Добавить файлы»",
                                name="Dim")
        queue_lay.addWidget(self.empty_hint)
        queue_lay.addWidget(self.queue_list)
        # без этого пустая карточка растягивается на пол-экрана
        queue_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        root.addWidget(queue_card)

        # --- прогресс текущего файла ---
        self.progress_card, prog_lay = card(padding=16, spacing=8)
        self.file_name = label("—", name="H2")
        self.lang_label = label("Язык: определение...", name="Muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.time_label = label("00:00 / 00:00", name="Dim")
        prog_lay.addWidget(self.file_name)
        prog_lay.addWidget(self.lang_label)
        prog_lay.addWidget(self.bar)
        prog_lay.addWidget(self.time_label)
        self.progress_card.setVisible(False)
        root.addWidget(self.progress_card)

        # --- результат ---
        self.result_card, res_lay = card(padding=16, spacing=10)
        res_head = QHBoxLayout()
        self.result_title = label("Результат", name="H2")
        res_head.addWidget(self.result_title)
        res_head.addStretch()
        for text, slot in (("Копировать", self._copy), ("TXT", lambda: self._save("txt")),
                           ("SRT", lambda: self._save("srt")), ("VTT", lambda: self._save("vtt"))):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            res_head.addWidget(btn)
        res_lay.addLayout(res_head)
        self.result_meta = label("", name="Muted")
        res_lay.addWidget(self.result_meta)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(220)
        res_lay.addWidget(self.result_text)
        self.result_card.setVisible(False)
        root.addWidget(self.result_card, 1)
        root.addStretch()
        self.set_queue([])   # пустой список прячем сразу, иначе карточка занимает пол-экрана

    # --- drag & drop --------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        files = [str(Path(u.toLocalFile())) for u in event.mimeData().urls()
                 if Path(u.toLocalFile()).is_file()]
        if files:
            self.add_requested.emit(files)
            event.acceptProposedAction()

    def _browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите аудио или видео", "", FILE_FILTER)
        if files:
            self.add_requested.emit(files)

    # --- обновление ---------------------------------------------------------

    def set_queue(self, jobs):
        self.queue_list.clear()
        for job in jobs:
            item = QListWidgetItem(f"{MARKS.get(job.status, '·')}  {job.name}")
            item.setData(Qt.UserRole, job)
            item.setForeground(_brush(MARK_COLOR.get(job.status, theme.TEXT)))
            if job.status == FAILED and job.error:
                item.setToolTip(job.error)
            self.queue_list.addItem(item)
        self.queue_list.setVisible(bool(jobs))
        self.empty_hint.setVisible(not jobs)
        pending = any(j.status == QUEUED for j in jobs)
        self.start_btn.setEnabled(pending)

    def set_running(self, running: bool):
        self.cancel_btn.setVisible(running)
        self.start_btn.setVisible(not running)
        self.add_btn.setEnabled(True)

    def show_progress(self, job):
        self.progress_card.setVisible(True)
        self.file_name.setText(job.name)
        self.bar.setValue(int(job.progress * 1000))
        lang = lang_name(job.language) if job.language else "определение..."
        self.lang_label.setText(f"Язык: {lang}")
        self.time_label.setText(
            f"{human_time(job.position)} / {human_time(job.duration)}"
            f"   ·   {int(job.progress * 100)}%")

    def show_result(self, job):
        self.current = job
        if job.status == FAILED:
            self.result_card.setVisible(True)
            self.result_title.setText(f"Ошибка — {job.name}")
            self.result_meta.setText("")
            self.result_text.setPlainText(job.error or "Неизвестная ошибка")
            return
        if not job.text:
            return
        self.result_card.setVisible(True)
        self.result_title.setText(job.name)
        self.result_meta.setText(
            f"Язык: {lang_name(job.language)}   ·   {human_time(job.duration)}   ·   "
            f"{job.words} слов   ·   в счётчик слов НЕ идёт (только живой режим)")
        body = []
        if job.translation:
            body = [f"Оригинал ({lang_name(job.language)}):", job.text, "",
                    "Перевод (русский):", job.translation]
        else:
            body = [job.text]
        if job.error:  # расшифровка удалась, но что-то по пути не сложилось (например, перевод)
            body += ["", f"⚠ {job.error}"]
        if job.saved_to:
            body += ["", f"Сохранено: {job.saved_to}"]
        self.result_text.setPlainText("\n".join(body))

    def _pick(self, item):
        job = item.data(Qt.UserRole)
        if job and job.status in (DONE, FAILED):
            self.show_result(job)

    # --- экспорт ------------------------------------------------------------

    def _copy(self):
        if not self.current:
            return
        text = self.current.translation or self.current.text
        QApplication.clipboard().setText(text)
        self.result_meta.setText("Скопировано в буфер обмена")

    def _save(self, fmt):
        job = self.current
        if not job or not job.text:
            return
        if fmt in ("srt", "vtt") and not job.segments:
            QMessageBox.information(self, "Субтитры",
                                    "Для этого файла нет сегментов с таймкодами.")
            return
        default = str(Path.home() / "Documents" / f"{job.path.stem}.{fmt}")
        path, _ = QFileDialog.getSaveFileName(self, f"Сохранить {fmt.upper()}", default,
                                              f"{fmt.upper()} (*.{fmt})")
        if not path:
            return
        content = {"txt": job.report(), "srt": job.srt(), "vtt": job.vtt()}[fmt]
        try:
            Path(path).write_text(content, encoding="utf-8")
            self.result_meta.setText(f"Сохранено: {path}")
        except OSError as e:
            QMessageBox.warning(self, "Не удалось сохранить", str(e))


def _brush(color):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(color))
