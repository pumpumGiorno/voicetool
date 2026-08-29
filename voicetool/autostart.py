"""Автозапуск вместе с Windows — ключ в HKCU\\...\\Run.

HKCU, а не HKLM: не нужны права администратора и настройка не трогает других пользователей.
Приложение при автозапуске стартует свёрнутым в трей (--tray), чтобы не мешать входу в систему.
"""
import logging
import os
import sys
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VoiceTool"


def supported() -> bool:
    return os.name == "nt"


def _command() -> str:
    """Строка запуска. Для exe — он сам, для исходников — python + VoiceTool.py."""
    if paths.is_frozen():
        return f'"{Path(sys.executable).resolve()}" --tray'
    entry = paths.app_dir() / "VoiceTool.py"
    return f'"{Path(sys.executable).resolve()}" "{entry}" --tray'


def enabled() -> bool:
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(value: bool) -> bool:
    """True/False — включить или выключить. Возвращает получившееся состояние."""
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if value:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
                log.info("Автозапуск включён: %s", _command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                    log.info("Автозапуск выключен")
                except FileNotFoundError:
                    pass
        return value
    except OSError as e:
        log.error("Не удалось изменить автозапуск: %s", e)
        return enabled()
