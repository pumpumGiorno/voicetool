"""Проверка системы: одна структура данных, два представления — консоль и GUI.

Лучше понятное сообщение на старте, чем ImportError в середине распознавания.
"""
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# модуль -> (пакет для pip, зачем нужен)
MODULES = {
    "numpy": ("numpy", "работа со звуком"),
    "faster_whisper": ("faster-whisper", "распознавание речи (офлайн)"),
    "sounddevice": ("sounddevice", "запись с микрофона"),
    "ctranslate2": ("ctranslate2", "движок перевода"),
    "sentencepiece": ("sentencepiece", "токенизатор для перевода"),
}

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


def available(module):
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def require(modules):
    """Останавливает программу с инструкцией, если чего-то не хватает."""
    missing = [m for m in modules if not available(m)]
    if not missing:
        return
    names = " ".join(MODULES.get(m, (m, ""))[0] for m in missing)
    lines = [f"  - {m}: {MODULES.get(m, (m, 'нужен для работы'))[1]}" for m in missing]
    raise SystemExit(
        "Не хватает зависимостей:\n" + "\n".join(lines)
        + f"\n\nУстановите их:\n  pip install {names}\nили сразу все:\n  pip install -r requirements.txt"
    )


def system_report(cfg):
    """[{name, status, detail, hint}] — то же самое показывают и `check`, и экран проверки."""
    rows = [{"name": "Python", "status": OK,
             "detail": f"{sys.version.split()[0]} ({Path(sys.executable).name})", "hint": ""}]

    for module, (pkg, why) in MODULES.items():
        ok = available(module)
        rows.append({
            "name": pkg,
            "status": OK if ok else FAIL,
            "detail": why if ok else "не установлен",
            "hint": "" if ok else f"pip install {pkg}",
        })

    rows.append(_mic_row(cfg))
    rows.append(_translate_row(cfg))
    rows.append(_whisper_row(cfg))
    rows.append(_cuda_row(cfg))
    rows.append(_vocabulary_row(cfg))
    rows.append(_ffmpeg_row())
    rows.append(_storage_row(cfg))
    return rows


def _mic_row(cfg):
    if not available("sounddevice"):
        return {"name": "Микрофон", "status": FAIL, "detail": "sounddevice не установлен",
                "hint": "pip install sounddevice"}
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
        if not inputs:
            return {"name": "Микрофон", "status": FAIL, "detail": "устройств записи не найдено",
                    "hint": "Подключите микрофон и разрешите доступ в параметрах Windows"}
        index = cfg.input_device if cfg.input_device is not None else sd.default.device[0]
        name = next((d["name"] for i, d in inputs if i == index), inputs[0][1]["name"])
        return {"name": "Микрофон", "status": OK, "detail": f"{name} ({len(inputs)} устройств)",
                "hint": ""}
    except Exception as e:
        return {"name": "Микрофон", "status": FAIL, "detail": str(e),
                "hint": "Проверьте, не занят ли микрофон другой программой"}


def _translate_row(cfg):
    if cfg.translator == "none":
        return {"name": "Перевод", "status": INFO, "detail": "отключён в настройках", "hint": ""}
    if not (available("ctranslate2") and available("sentencepiece")):
        return {"name": "Перевод", "status": FAIL, "detail": "нет ctranslate2/sentencepiece",
                "hint": "pip install ctranslate2 sentencepiece"}
    cache = cfg.data_dir / "models" / "translate"
    pairs = sorted(p.name for p in cache.glob("*_*") if p.is_dir()) if cache.is_dir() else []
    if pairs:
        return {"name": "Перевод", "status": OK, "detail": f"модели: {', '.join(pairs)}", "hint": ""}
    if cfg.get("translate_offline_only"):
        return {"name": "Перевод", "status": WARN,
                "detail": "моделей нет, а загрузка запрещена режимом «только офлайн»",
                "hint": "Выключите «только офлайн» или положите модели вручную"}
    return {"name": "Перевод", "status": WARN, "detail": "модели скачаются при первом переводе",
            "hint": ""}


def _whisper_row(cfg):
    if not available("faster_whisper"):
        return {"name": "Модель Whisper", "status": FAIL, "detail": "faster-whisper не установлен",
                "hint": "pip install faster-whisper"}
    found = _find_whisper_model(cfg)
    if found:
        return {"name": "Модель Whisper", "status": OK, "detail": f"{cfg.model} — в кэше", "hint": ""}
    return {"name": "Модель Whisper", "status": WARN,
            "detail": f"{cfg.model} не скачана (~{MODEL_SIZES.get(cfg.model, '?')})",
            "hint": "Скачается автоматически при первом распознавании (нужен интернет)"}


MODEL_SIZES = {"tiny": "75 МБ", "base": "145 МБ", "small": "465 МБ",
               "medium": "1.5 ГБ", "large-v3": "3 ГБ"}


def _find_whisper_model(cfg) -> bool:
    """Модель лежит в кэше HuggingFace или в своей папке — ищем и там, и там."""
    roots = []
    if cfg.models_dir:
        roots.append(cfg.models_dir)
    hub = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    roots.append(Path(hub) if hub else Path.home() / ".cache" / "huggingface" / "hub")
    needle = str(cfg.model).lower().replace("_", "-")
    for root in roots:
        if not root or not Path(root).is_dir():
            continue
        for item in Path(root).iterdir():
            name = item.name.lower()
            if needle in name and ("whisper" in name or item.is_dir()):
                if any(item.rglob("model.bin")) or any(item.rglob("*.bin")):
                    return True
    return False


def _cuda_row(cfg):
    """Главное, что тут должно быть видно: на чём реально считается речь и почему."""
    from . import cuda

    st = cuda.status()
    name = st["name"] or "видеокарта NVIDIA"
    if st["available"]:
        detail = f"{name} — распознавание идёт на видеокарте"
        if cuda.active_device() == "cpu":
            # библиотеки на месте, но модель всё равно поднялась на процессоре
            return {"name": "Бэкенд", "status": WARN,
                    "detail": f"{name} доступна, но модель загружена на процессор",
                    "hint": "Смотрите лог: там причина отката"}
        return {"name": "Бэкенд", "status": OK, "detail": detail, "hint": ""}

    reason = st["reason"] or "видеокарта с CUDA не найдена"
    if st["devices"]:
        # карта есть, не хватает библиотек — это чинится, подскажем как
        return {"name": "Бэкенд", "status": WARN,
                "detail": f"процессор — {reason}",
                "hint": "python tools/install_cuda.py — поставит cuBLAS и cuDNN"}
    return {"name": "Бэкенд", "status": INFO,
            "detail": f"процессор ({reason})", "hint": ""}


def _vocabulary_row(cfg):
    from .vocabulary import Vocabulary

    if not cfg.get("use_vocabulary", True):
        return {"name": "Словарь слов", "status": INFO, "detail": "выключен в настройках",
                "hint": ""}
    vocab = Vocabulary(cfg.data_dir)
    count = len(vocab)
    if count:
        return {"name": "Словарь слов", "status": OK,
                "detail": f"{count} записей — {vocab.path.name}", "hint": ""}
    return {"name": "Словарь слов", "status": INFO,
            "detail": "пуст — добавьте имена, которые модель путает",
            "hint": str(vocab.path)}


def _ffmpeg_row():
    """PyAV декодирует mp3/mp4/mkv сам, ffmpeg нужен только для редких контейнеров."""
    path = shutil.which("ffmpeg")
    if path:
        return {"name": "FFmpeg", "status": OK, "detail": path, "hint": ""}
    if available("av"):
        return {"name": "FFmpeg", "status": INFO, "detail": "не требуется (декодер PyAV встроен)",
                "hint": ""}
    return {"name": "FFmpeg", "status": WARN, "detail": "не найден, и PyAV тоже нет",
            "hint": "winget install Gyan.FFmpeg"}


def _storage_row(cfg):
    data = cfg.data_dir
    try:
        free = shutil.disk_usage(data).free / 1e9
    except OSError as e:
        return {"name": "Диск", "status": WARN, "detail": f"{data}: {e}", "hint": ""}
    status = OK if free > 2 else WARN
    return {"name": "Диск", "status": status, "detail": f"{data} — свободно {free:.1f} ГБ",
            "hint": "" if status == OK else "Меньше 2 ГБ: модели могут не скачаться"}


MARKS = {OK: "v", WARN: "!", FAIL: "x", INFO: "-"}


def report(cfg):
    """Команда check в консоли."""
    rows = system_report(cfg)
    width = max(len(r["name"]) for r in rows)
    for row in rows:
        line = f"  [{MARKS[row['status']]}] {row['name']:<{width}}  {row['detail']}"
        if row["hint"]:
            line += f"\n      -> {row['hint']}"
        print(line)
    problems = sum(1 for r in rows if r["status"] == FAIL)
    print("\n" + ("Всё готово к работе." if not problems else "Есть проблемы — см. подсказки выше."))
    return 1 if problems else 0
