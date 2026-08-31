"""Логи в data_dir/logs/ГГГГ-ММ-ДД.log — по одному файлу на день.

Пользователю в интерфейсе показываем короткое понятное сообщение, а полный traceback
уходит сюда: без него диагностировать «не работает микрофон» невозможно.
"""
import logging
import logging.handlers
import sys
from datetime import date, datetime
from pathlib import Path

KEEP_DAYS = 30
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 2
_configured = False


def setup(data_dir: Path, level=logging.INFO, console=True) -> Path:
    """Настроить корневой логгер. Возвращает путь к сегодняшнему файлу."""
    global _configured
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{date.today():%Y-%m-%d}.log"
    if _configured:
        return path

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
                            datefmt="%H:%M:%S")
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if console and sys.stderr:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream.setLevel(logging.WARNING)
        root.addHandler(stream)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    _configured = True
    _cleanup(log_dir)
    logging.getLogger("voicetool").info("=== старт, лог: %s ===", path)
    return path


def today_log(data_dir: Path) -> Path:
    return Path(data_dir) / "logs" / f"{date.today():%Y-%m-%d}.log"


def _cleanup(log_dir: Path):
    """Старые логи чистим сами: иначе за год накопится триста файлов."""
    for old in log_dir.glob("*.log*"):
        try:
            age = (datetime.now() - datetime.fromtimestamp(old.stat().st_mtime)).days
            if age > KEEP_DAYS:
                old.unlink()
        except OSError:
            pass
