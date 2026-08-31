"""История: что распознали и когда. Одна строка JSON на запись (history.jsonl).

Формат построчный намеренно: дописать запись — это один append, а битая строка портит
одну запись, а не весь файл.

kind = "voice" (живой режим, идёт в счётчик слов) | "file" (разбор файла, в счётчик НЕ идёт).
"""
import json
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from .retention import append_bounded, trim_lines

log = logging.getLogger(__name__)

MAX_LINES = 10_000
MAX_BYTES = 8 * 1024 * 1024


class History:
    def __init__(self, data_dir: Path, *, enabled=True, max_lines=MAX_LINES,
                 max_bytes=MAX_BYTES):
        self.path = Path(data_dir) / "history.jsonl"
        self.enabled = bool(enabled)
        self.max_lines = max(1, int(max_lines))
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.Lock()
        trim_lines(self.path, max_lines=self.max_lines, max_bytes=self.max_bytes, force=True)

    def add(self, text: str, kind="voice", words=0, source=None, language=None):
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "text": (text or "").strip(),
            "words": int(words or 0),
        }
        if source:
            entry["source"] = str(source)
        if language:
            entry["language"] = language
        if not self.enabled:
            return entry
        try:
            append_bounded(
                self.path, json.dumps(entry, ensure_ascii=False),
                max_lines=self.max_lines, max_bytes=self.max_bytes, lock=self._lock,
            )
        except OSError as e:
            log.error("Не удалось записать историю: %s", e)
        return entry

    def recent(self, limit=200, kind=None):
        """Последние записи, новые сверху. Битые строки просто пропускаем."""
        if not self.path.exists():
            return []
        rows = deque(maxlen=limit if kind is None else MAX_LINES)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if kind and entry.get("kind") != kind:
                        continue
                    rows.append(entry)
        except OSError as e:
            log.error("Не удалось прочитать историю: %s", e)
            return []
        items = list(rows)[-limit:]
        items.reverse()
        return items

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError as e:
            log.error("Не удалось очистить историю: %s", e)

    def trim(self, keep=None):
        """Force retention now; normal writes enforce the same byte bound automatically."""
        return trim_lines(
            self.path, max_lines=keep or self.max_lines,
            max_bytes=self.max_bytes, force=True,
        )
