"""История: что распознали и когда. Одна строка JSON на запись (history.jsonl).

Формат построчный намеренно: дописать запись — это один append, а битая строка портит
одну запись, а не весь файл.

kind = "voice" (живой режим, идёт в счётчик слов) | "file" (разбор файла, в счётчик НЕ идёт).
"""
import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

MAX_LINES = 20_000  # дальше подрезаем: файл не должен расти бесконечно


class History:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "history.jsonl"

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
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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

    def trim(self, keep=MAX_LINES):
        """Оставить последние keep записей. Вызывается при старте приложения."""
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = deque(f, maxlen=keep + 1)
            if len(lines) <= keep:
                return
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(list(lines)[-keep:]), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.error("Не удалось подрезать историю: %s", e)
