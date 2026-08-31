"""Счётчик слов и логи. Данные лежат отдельно от кода и переживают обновление программы."""
import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from .text import count_words
from .retention import append_bounded

TRANSCRIPT_LOG_MAX_LINES = 5_000
TRANSCRIPT_LOG_MAX_BYTES = 5 * 1024 * 1024
_transcript_lock = threading.Lock()


class WordCounter:
    """total + разбивка по дням в одном JSON. Запись атомарная: временный файл + replace."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "word_count.json"
        self.data = self._read()

    def _read(self):
        if not self.path.exists():
            return {"total": 0, "days": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("total"), int):
                raise ValueError("неожиданная структура файла")
            if not isinstance(data.get("days", {}), dict):
                raise ValueError("неожиданная структура файла")
            data.setdefault("days", {})
            return data
        except (json.JSONDecodeError, ValueError, OSError) as e:
            # лучше отложить непонятный файл в сторону, чем молча обнулить счётчик
            backup = self.path.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json")
            self.path.rename(backup)
            print(f"[Счётчик] Файл повреждён ({e}), сохранён как {backup.name}, начинаю с нуля.")
            return {"total": 0, "days": {}}

    def add(self, text: str) -> int:
        """Только живой режим! Текст из файлов сюда не попадает — это условие задачи."""
        n = count_words(text)
        if n:
            today = date.today().isoformat()
            self.data["total"] += n
            self.data["days"][today] = self.data["days"].get(today, 0) + n
            self.data["phrases"] = self.data.get("phrases", 0) + 1
            self.data["updated"] = datetime.now().isoformat(timespec="seconds")
            self._write()
        return n

    def start_session(self):
        """Одно включение прослушивания = одна сессия."""
        self.data["sessions"] = self.data.get("sessions", 0) + 1
        self.data["updated"] = datetime.now().isoformat(timespec="seconds")
        self._write()
        return self.data["sessions"]

    def reload(self):
        """Перечитать файл: счётчик могли поменять из другого процесса (CLI рядом с GUI)."""
        self.data = self._read()
        return self

    def _write(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def total(self) -> int:
        return self.data["total"]

    def stats(self):
        days = self.data.get("days", {})
        today = date.today()
        week = sum(n for d, n in days.items() if _days_ago(d, today) < 7)
        return {
            "total": self.data["total"],
            "today": days.get(today.isoformat(), 0),
            "week": week,
            "phrases": self.data.get("phrases", 0),
            "sessions": self.data.get("sessions", 0),
            "days": sorted(days.items(), reverse=True)[:7],
            "last_days": self.last_days(7),
        }

    def last_days(self, count=7):
        """[(дата, слов)] за последние count дней подряд, включая нулевые — для графика."""
        days = self.data.get("days", {})
        today = date.today()
        out = []
        for back in range(count - 1, -1, -1):
            day = today - timedelta(days=back)
            out.append((day, days.get(day.isoformat(), 0)))
        return out


def append_log(data_dir: Path, line: str, name="transcripts.log"):
    append_bounded(
        Path(data_dir) / name,
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}",
        max_lines=TRANSCRIPT_LOG_MAX_LINES,
        max_bytes=TRANSCRIPT_LOG_MAX_BYTES,
        lock=_transcript_lock,
    )


def save_transcript(data_dir: Path, source: str, body: str) -> Path:
    out_dir = Path(data_dir) / "files"
    out_dir.mkdir(exist_ok=True)
    stem = Path(source).stem[:60].strip() or "audio"
    safe = "".join(c for c in stem if c.isalnum() or c in " _-.").strip() or "audio"
    path = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{safe}.txt"
    path.write_text(body, encoding="utf-8")
    return path


def _days_ago(day: str, today: date) -> int:
    """Битую дату в файле игнорируем, а не роняем всю статистику."""
    try:
        return (today - date.fromisoformat(day)).days
    except ValueError:
        return 10 ** 6
