"""Где лежат данные пользователя.

Правило одно: данные никогда не живут рядом с программой. Обновление VoiceTool.exe
не должно стирать счётчик слов, историю и скачанные модели.
"""
import os
import shutil
import sys
from pathlib import Path

LEGACY_DIR = Path.home() / ".voice_tool"  # где данные лежали у CLI-версии
ENV_DATA_DIR = "VOICETOOL_DATA_DIR"       # переопределение папки данных (portable-режим, тесты)


def is_frozen() -> bool:
    """True, когда код запущен из собранного PyInstaller экземпляра."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Папка с самой программой (для exe — рядом с ним, для исходников — корень репозитория)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    """%APPDATA%\\VoiceTool на Windows, ~/.voice_tool на остальных системах.

    VOICETOOL_DATA_DIR перекрывает и это: иначе папка с CUDA-библиотеками искалась бы
    в системной папке даже у portable-копии на флешке.
    """
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(os.path.expanduser(override))
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "VoiceTool"
    return LEGACY_DIR


def resolve_data_dir(value) -> Path:
    """Значение data_dir из конфига -> реальный путь. "auto"/пусто = системная папка.

    Переменная окружения VOICETOOL_DATA_DIR перекрывает всё: так удобно держать
    portable-копию на флешке и так же работают автотесты, не трогая данные пользователя.
    """
    if os.environ.get(ENV_DATA_DIR):
        path = Path(os.path.expanduser(os.environ[ENV_DATA_DIR]))
    elif not value or value == "auto":
        path = default_data_dir()
    else:
        path = Path(os.path.expanduser(str(value)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_legacy(target: Path) -> bool:
    """Один раз перенести данные CLI-версии из ~/.voice_tool в новую папку.

    Копируем, а не двигаем: если что-то пойдёт не так, старые данные останутся на месте.
    Повторный запуск ничего не делает — маркер .migrated лежит в целевой папке.
    """
    marker = target / ".migrated"
    if marker.exists() or not LEGACY_DIR.is_dir() or LEGACY_DIR.resolve() == target.resolve():
        return False
    moved = False
    for item in LEGACY_DIR.iterdir():
        dest = target / item.name
        if dest.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
            moved = True
        except OSError:
            pass  # не смогли перенести один файл — не повод ломать запуск
    marker.write_text("legacy data imported from ~/.voice_tool\n", encoding="utf-8")
    return moved


def config_path(data_dir: Path) -> Path:
    """Собранное приложение хранит config.json в данных, исходники — в папке проекта.

    Иначе настройки, сделанные в GUI, потерялись бы при обновлении exe.
    """
    if is_frozen() or os.environ.get(ENV_DATA_DIR):
        return data_dir / "config.json"
    return app_dir() / "config.json"
