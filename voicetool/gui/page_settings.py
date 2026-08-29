"""Экран настроек. Каждый переключатель реально меняет поведение программы.

Настройки сохраняются в config.json и применяются: то, что требует перезапуска
прослушивания (модель, микрофон), перезапускает слушателя само.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import autostart, hotkey
from . import theme
from .widgets import card, divider, label, section

MODELS = ["tiny", "base", "small", "medium", "large-v3"]
MODEL_HINT = {"tiny": "75 МБ · быстрая, менее точная", "base": "145 МБ",
              "small": "465 МБ · разумный баланс", "medium": "1.5 ГБ · точнее, медленнее",
              "large-v3": "3 ГБ · самая точная, требует мощный ПК"}
DEVICES = [("auto", "Автоматически"), ("cpu", "Процессор"), ("cuda", "Видеокарта (CUDA)")]
COMPUTE = ["int8", "int8_float16", "float16", "float32"]
OUTPUT_MODES = [
    ("insert", "Вставлять в активное приложение"),
    ("insert_show", "Вставлять + показывать в Voice Tool"),
    ("show", "Только показывать в Voice Tool"),
]


class SettingsPage(QWidget):
    saved = Signal(dict)          # изменившиеся ключи
    restart_listener = Signal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.fields = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Настройки", name="H1"))
        head.addStretch()
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setObjectName("Primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._save)
        reset = QPushButton("Сбросить")
        reset.setObjectName("Ghost")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self.load)
        head.addWidget(reset)
        head.addWidget(self.save_btn)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 10, 0)
        root.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        root.addWidget(self._voice_section())
        root.addWidget(self._output_section())
        root.addWidget(self._whisper_section())
        root.addWidget(self._vocabulary_section())
        root.addWidget(self._ui_section())
        root.addWidget(self._translate_section())
        root.addStretch()
        self.load()

    # --- секции -------------------------------------------------------------

    def _form(self, title):
        frame, lay = card(padding=18, spacing=12)
        lay.addWidget(section(title))
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        lay.addLayout(form)
        return frame, lay, form

    def _voice_section(self):
        frame, lay, form = self._form("Голосовой режим")
        self.fields["wake_word"] = QLineEdit()
        self.fields["wake_word"].setPlaceholderText("алиса")
        form.addRow("Слово-триггер", self.fields["wake_word"])

        self.fields["wake_word_aliases"] = QLineEdit()
        self.fields["wake_word_aliases"].setPlaceholderText("алиса, алис, alisa")
        form.addRow("Как его может расслышать Whisper", self.fields["wake_word_aliases"])

        for key, title, lo, hi, step, suffix in (
            ("silence_seconds", "Пауза после речи", 0.3, 10.0, 0.1, " с"),
            ("min_speech_seconds", "Минимальная длина речи", 0.1, 5.0, 0.1, " с"),
            ("wake_silence_seconds", "Пауза для слова-триггера", 0.2, 3.0, 0.1, " с"),
            ("noise_multiplier", "Порог начала речи (× фоновый шум)", 1.5, 12.0, 0.5, ""),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setSuffix(suffix)
            self.fields[key] = spin
            form.addRow(title, spin)

        spin = QSpinBox()
        spin.setRange(5, 600)
        spin.setSuffix(" с")
        self.fields["max_utterance_seconds"] = spin
        form.addRow("Максимальная длина команды", spin)

        self.fields["input_device"] = QComboBox()
        self._fill_devices()
        form.addRow("Микрофон", self.fields["input_device"])

        lay.addWidget(label("Слово-триггер ловит отдельная лёгкая модель (по умолчанию tiny) — "
                            "она отвечает за то, как быстро появляется кружок после «Алисы». "
                            "Саму фразу распознаёт основная модель.",
                            name="Dim", wrap=True))
        return frame

    def _output_section(self):
        frame, lay, form = self._form("Куда попадает распознанный текст")
        self.mode_buttons = {}
        for value, title in OUTPUT_MODES:
            radio = QRadioButton(title)
            self.mode_buttons[value] = radio
            lay.addWidget(radio)

        lay.addWidget(divider())
        for key, title in (
            ("insert_into_wake_window",
             "Вставлять результат в приложение, активное при активации «Алиса»"),
            ("press_enter", "Автоматически нажимать Enter после распознавания"),
        ):
            box = QCheckBox(title)
            self.fields[key] = box
            lay.addWidget(box)
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix(" мс")
        self.fields["type_delay_ms"] = spin
        form.addRow("Пауза между символами", spin)

        lay.addWidget(label("Текст набирается посимвольно напрямую в поле ввода: буфер обмена "
                            "не используется и не меняется. Enter по умолчанию выключен — "
                            "сообщение не отправится само. Существующий текст не стирается: "
                            "символы идут в позицию курсора.",
                            name="Dim", wrap=True))
        lay.addWidget(label("Пауза между символами: 15 мс держат даже медленные поля "
                            "(Блокнот Windows 11). Меньше — быстрее, но некоторые приложения "
                            "начнут терять символы.", name="Dim", wrap=True))
        return frame

    def _whisper_section(self):
        frame, lay, form = self._form("Whisper")
        self.fields["model"] = QComboBox()
        for m in MODELS:
            self.fields["model"].addItem(f"{m}  —  {MODEL_HINT[m]}", m)
        form.addRow("Модель", self.fields["model"])

        self.fields["wake_model"] = QComboBox()
        self.fields["wake_model"].addItem("Та же, что основная", "")
        for m in MODELS:
            self.fields["wake_model"].addItem(m, m)
        form.addRow("Модель для слова-триггера", self.fields["wake_model"])

        self.fields["device"] = QComboBox()
        for value, title in DEVICES:
            self.fields["device"].addItem(title, value)
        form.addRow("Устройство", self.fields["device"])

        self.fields["compute_type"] = QComboBox()
        self.fields["compute_type"].addItems(COMPUTE)
        form.addRow("Точность", self.fields["compute_type"])

        self.fields["language_hint"] = QComboBox()
        self.fields["language_hint"].addItem("Русский (быстрее)", "ru")
        self.fields["language_hint"].addItem("Английский", "en")
        self.fields["language_hint"].addItem("Определять автоматически", None)
        form.addRow("Язык живого режима", self.fields["language_hint"])

        spin = QSpinBox()
        spin.setRange(30, 1800)
        spin.setSingleStep(30)
        spin.setSuffix(" с")
        self.fields["chunk_seconds"] = spin
        form.addRow("Кусок при разборе файла", spin)

        self.backend_label = label("", name="Dim", wrap=True)
        lay.addWidget(self.backend_label)
        lay.addWidget(label("«Автоматически» берёт видеокарту, если на компьютере есть рабочая "
                            "CUDA, и молча переходит на процессор, если её нет. "
                            "«Точность» на видеокарте по умолчанию float16, на процессоре int8.",
                            name="Dim", wrap=True))
        return frame

    def _vocabulary_section(self):
        frame, lay, form = self._form("Словарь имён и редких слов")
        self.fields["use_vocabulary"] = QCheckBox(
            "Подсказывать модели слова из словаря (меньше искажённых имён)")
        lay.addWidget(self.fields["use_vocabulary"])
        self.vocab_label = label("", name="Dim", wrap=True)
        lay.addWidget(self.vocab_label)
        row = QHBoxLayout()
        open_btn = QPushButton("Открыть словарь")
        open_btn.setObjectName("Ghost")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(self._open_vocabulary)
        row.addWidget(open_btn)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(label("Обычный текстовый файл: по слову или фразе в строке. "
                            "Сохранили — подсказка обновится сама, перезапуск не нужен.",
                            name="Dim", wrap=True))
        return frame

    def _open_vocabulary(self):
        from ..vocabulary import Vocabulary

        from .page_check import open_path

        vocab = Vocabulary(self.cfg.data_dir)
        open_path(vocab.ensure_file())

    def _ui_section(self):
        frame, lay, form = self._form("Интерфейс и фон")
        for key, title in (
            ("start_with_windows", "Запускать Voice Tool вместе с Windows"),
            ("minimize_to_tray", "При закрытии окна сворачивать в трей (работать в фоне)"),
            ("show_floating_widget", "Показывать голосовой индикатор поверх окон"),
            ("show_notifications", "Показывать уведомления"),
            ("start_listening_on_launch", "Включать прослушивание сразу при запуске"),
            ("hotkey_enabled", "Включить горячую клавишу"),
        ):
            box = QCheckBox(title)
            self.fields[key] = box
            lay.addWidget(box)

        self.fields["hotkey"] = QLineEdit()
        self.fields["hotkey"].setPlaceholderText("Ctrl+Alt+A")
        form.addRow("Горячая клавиша", self.fields["hotkey"])
        self.hotkey_hint = label("", name="Dim", wrap=True)
        lay.addWidget(self.hotkey_hint)
        self.fields["hotkey"].textChanged.connect(self._check_hotkey)
        return frame

    def _translate_section(self):
        frame, lay, form = self._form("Перевод")
        self.fields["translator"] = QCheckBox("Переводить иностранную речь на русский")
        lay.addWidget(self.fields["translator"])
        self.fields["translate_offline_only"] = QCheckBox(
            "Только офлайн: не скачивать модели перевода из интернета")
        lay.addWidget(self.fields["translate_offline_only"])
        self.fields["log_transcripts"] = QCheckBox("Сохранять расшифровки и лог в папку данных")
        lay.addWidget(self.fields["log_transcripts"])
        lay.addWidget(label("Аудио никогда не отправляется в интернет. Сеть нужна один раз — "
                            "чтобы скачать модель распознавания или пару языков для перевода.",
                            name="Dim", wrap=True))
        return frame

    # --- данные -------------------------------------------------------------

    def _fill_devices(self):
        box = self.fields["input_device"]
        box.clear()
        box.addItem("По умолчанию", None)
        try:
            import sounddevice as sd

            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    box.addItem(f"[{i}] {d['name']}", i)
        except Exception:
            pass  # без микрофона список просто останется коротким

    def _check_hotkey(self, text):
        if not text.strip():
            self.hotkey_hint.setText("")
            return
        ok = hotkey.parse(text) is not None
        self.hotkey_hint.setText(
            "" if ok else "Не похоже на сочетание. Пример: Ctrl+Alt+A (модификатор обязателен).")
        self.hotkey_hint.setStyleSheet(f"color: {theme.WARN}; font-size: 11px;" if not ok else "")

    def load(self):
        cfg = self.cfg
        self.fields["wake_word"].setText(cfg.wake_word)
        self.fields["wake_word_aliases"].setText(", ".join(cfg.wake_word_aliases))
        for key in ("silence_seconds", "min_speech_seconds", "wake_silence_seconds",
                    "noise_multiplier"):
            self.fields[key].setValue(float(cfg[key]))
        self.fields["max_utterance_seconds"].setValue(int(cfg.max_utterance_seconds))
        self.fields["type_delay_ms"].setValue(int(cfg.get("type_delay_ms", 15)))
        self.fields["chunk_seconds"].setValue(int(cfg.chunk_seconds))
        _select(self.fields["input_device"], cfg.input_device)
        _select(self.fields["model"], cfg.model)
        _select(self.fields["wake_model"], cfg.wake_model or "")
        _select(self.fields["device"], cfg.device)
        self.fields["compute_type"].setCurrentText(cfg.compute_type)
        _select(self.fields["language_hint"], cfg.language_hint)

        mode = cfg.output_mode if cfg.output_mode in self.mode_buttons else "insert"
        self.mode_buttons[mode].setChecked(True)
        for key in ("insert_into_wake_window", "press_enter",
                    "minimize_to_tray", "show_floating_widget", "show_notifications",
                    "start_listening_on_launch", "hotkey_enabled", "translate_offline_only",
                    "log_transcripts", "use_vocabulary"):
            self.fields[key].setChecked(bool(cfg[key]))
        self.fields["translator"].setChecked(cfg.translator != "none")
        self._refresh_backend_label()
        self._refresh_vocab_label()
        self.fields["start_with_windows"].setChecked(autostart.enabled())
        self.fields["start_with_windows"].setEnabled(autostart.supported())
        self.fields["hotkey"].setText(cfg.hotkey)

    def collect(self) -> dict:
        aliases = [a.strip().lower() for a in self.fields["wake_word_aliases"].text().split(",")
                   if a.strip()]
        wake = self.fields["wake_word"].text().strip().lower() or "алиса"
        if wake not in aliases:
            aliases.insert(0, wake)
        values = {
            "wake_word": wake,
            "wake_word_aliases": aliases,
            "max_utterance_seconds": self.fields["max_utterance_seconds"].value(),
            "type_delay_ms": self.fields["type_delay_ms"].value(),
            "chunk_seconds": self.fields["chunk_seconds"].value(),
            "input_device": self.fields["input_device"].currentData(),
            "model": self.fields["model"].currentData(),
            "wake_model": self.fields["wake_model"].currentData(),
            "device": self.fields["device"].currentData(),
            "compute_type": self.fields["compute_type"].currentText(),
            "language_hint": self.fields["language_hint"].currentData(),
            "output_mode": next(v for v, b in self.mode_buttons.items() if b.isChecked()),
            "translator": "argos" if self.fields["translator"].isChecked() else "none",
            "hotkey": self.fields["hotkey"].text().strip() or "Ctrl+Alt+A",
        }
        for key in ("silence_seconds", "min_speech_seconds", "wake_silence_seconds",
                    "noise_multiplier"):
            values[key] = round(self.fields[key].value(), 2)
        for key in ("insert_into_wake_window", "press_enter",
                    "minimize_to_tray", "show_floating_widget", "show_notifications",
                    "start_listening_on_launch", "hotkey_enabled", "translate_offline_only",
                    "log_transcripts", "use_vocabulary"):
            values[key] = self.fields[key].isChecked()
        return values

    def _save(self):
        values = self.collect()
        if self.fields["hotkey_enabled"].isChecked() and hotkey.parse(values["hotkey"]) is None:
            QMessageBox.warning(self, "Горячая клавиша",
                                "Сочетание не распознано. Пример: Ctrl+Alt+A.")
            return
        # ключи, из-за которых слушателя надо перезапустить
        restart_keys = {"wake_word", "wake_word_aliases", "model", "wake_model", "device",
                        "compute_type", "language_hint", "input_device", "silence_seconds",
                        "min_speech_seconds", "wake_silence_seconds", "noise_multiplier",
                        "max_utterance_seconds"}
        changed = {k: v for k, v in values.items() if self.cfg.get(k) != v}
        self.cfg.update(values)
        self.cfg.save()

        if autostart.supported():
            autostart.set_enabled(self.fields["start_with_windows"].isChecked())
        self.saved.emit(changed)
        if restart_keys & set(changed):
            self.restart_listener.emit()
        self.save_btn.setText("Сохранено ✓")
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1600, lambda: self.save_btn.setText("Сохранить"))


    def _refresh_backend_label(self):
        """Показать, что реально будет использовано — видеокарта или процессор."""
        from .. import cuda

        st = cuda.status()
        if st["available"]:
            name = st["name"] or "видеокарта"
            self.backend_label.setText(f"Сейчас доступна видеокарта: {name} (CUDA)")
            self.backend_label.setStyleSheet(f"color: {theme.OK}; font-size: 11px;")
        else:
            self.backend_label.setText(f"Видеокарта не используется: {st['reason'] or 'CUDA не найдена'}. "
                                       f"Работа идёт на процессоре.")
            self.backend_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")

    def _refresh_vocab_label(self):
        from ..vocabulary import Vocabulary

        vocab = Vocabulary(self.cfg.data_dir)
        self.vocab_label.setText(f"{len(vocab)} записей · {vocab.path}")


def _select(combo: QComboBox, value):
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)
