"""Пользовательский словарь имён и редких слов.

Whisper коверкает то, чего почти не встречал: фамилии, названия проектов, термины.
Переобучать модель ради этого не нужно — ей можно подсказать список ожидаемых слов.

  * faster-whisper 1.0.3+ принимает `hotwords` — список слов подмешивается в подсказку
    на каждом окне распознавания, влияет именно на выбор слов;
  * на версиях постарше тот же список уходит в `initial_prompt` (см. asr._hint_kwargs).

Файл лежит в папке данных: %APPDATA%\\VoiceTool\\vocabulary.txt — обычный текст,
по слову или фразе в строке, строки с # игнорируются. Правится любым редактором,
перечитывается автоматически, как только файл изменился.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

FILENAME = "vocabulary.txt"
MAX_ENTRIES = 200   # длинная подсказка сама начинает сбивать модель
MAX_CHARS = 900     # и упирается в контекст Whisper

TEMPLATE = """\
# Словарь Voice Tool: имена и слова, которые модель распознаёт неправильно.
#
# По одной записи в строке. Строки, начинающиеся с #, игнорируются.
# Файл перечитывается сам — перезапускать программу не нужно.
#
# Пишите слово так, как оно должно выглядеть в тексте:
#
#   Ивандар
#   Анастасия Кузьмина
#   PySide
#   ctranslate2
#
# Слишком длинный список вредит: держите здесь то, что реально путается,
# первые {max_entries} записей.
"""


class Vocabulary:
    """Список подсказок из файла. Перечитывается, когда файл изменился."""

    def __init__(self, data_dir):
        self.path = Path(data_dir) / FILENAME
        self._mtime = None
        self._words = []

    def ensure_file(self) -> Path:
        """Создать файл-заготовку, если его ещё нет — чтобы было что открыть и править."""
        if not self.path.exists():
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(TEMPLATE.format(max_entries=MAX_ENTRIES), encoding="utf-8")
                log.info("Создан словарь: %s", self.path)
            except OSError as e:
                log.error("Не удалось создать словарь %s: %s", self.path, e)
        return self.path

    @property
    def words(self):
        """Актуальный список. Файл читается только когда реально поменялся."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime, self._words = None, []
            return self._words
        if mtime != self._mtime:
            self._mtime = mtime
            self._words = self._read()
            log.info("Словарь перечитан: %d записей из %s", len(self._words), self.path.name)
        return self._words

    def _read(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            log.error("Словарь %s не прочитан: %s", self.path, e)
            return []
        words, seen = [], set()
        for line in lines:
            word = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
            key = word.lower()
            if word and key not in seen:
                seen.add(key)
                words.append(word)
            if len(words) >= MAX_ENTRIES:
                break
        return words

    def hint(self) -> str:
        """Строка-подсказка для модели. Пустая — значит подсказывать нечего."""
        words = self.words
        if not words:
            return ""
        phrase, total = [], 0
        for word in words:
            total += len(word) + 2
            if total > MAX_CHARS:
                break
            phrase.append(word)
        return ", ".join(phrase)

    def __len__(self):
        return len(self.words)
