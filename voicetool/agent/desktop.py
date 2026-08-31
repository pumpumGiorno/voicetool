"""Narrow Windows backend for Stage 1 typed desktop tools."""
from __future__ import annotations

import ctypes
import difflib
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .types import ToolResult

IS_WINDOWS = os.name == "nt" and hasattr(ctypes, "windll")


@dataclass(frozen=True)
class AppCandidate:
    name: str
    target: str
    source: str


class AppResolver:
    """Resolve names only to known/discovered apps; never accept a command line."""

    ALIASES = {
        "телега": "telegram", "телеграм": "telegram", "тг": "telegram",
        "хром": "google chrome", "chrome": "google chrome",
        "блокнот": "notepad", "проводник": "file explorer",
    }
    KNOWN = {
        "telegram": "Telegram.exe", "google chrome": "chrome.exe",
        "notepad": "notepad.exe", "file explorer": "explorer.exe",
    }
    FORBIDDEN = {"cmd", "command prompt", "powershell", "pwsh", "windows terminal", "terminal"}

    def candidates(self) -> list[AppCandidate]:
        found = [AppCandidate(name, shutil.which(exe) or exe, "known")
                 for name, exe in self.KNOWN.items()]
        if IS_WINDOWS:
            found.extend(self._start_menu())
            found.extend(self._registry())
        return found

    def resolve(self, query: str) -> AppCandidate | None:
        raw = str(query or "")
        if any(char in raw for char in "\r\n\x00"):
            return None
        wanted = _norm(raw)
        wanted = self.ALIASES.get(wanted, wanted)
        if _forbidden_name(wanted):
            return None
        best, best_score = None, 0.0
        bonuses = {"registry": 0.28, "start_menu": 0.25, "known": 0.0}
        for candidate in self.candidates():
            name = _norm(candidate.name)
            if _forbidden_name(name) or _forbidden_name(_norm(Path(candidate.target).stem)):
                continue
            score = (1.0 if wanted == name else difflib.SequenceMatcher(None, wanted, name).ratio())
            if wanted in name or name in wanted:
                score += 0.12
            score += bonuses.get(candidate.source, 0.0)
            if score > best_score:
                best, best_score = candidate, score
        return best if best_score >= 0.72 else None

    def _start_menu(self):
        roots = [
            Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        ]
        result = []
        for root in roots:
            if not root.is_dir():
                continue
            try:
                result.extend(AppCandidate(path.stem, str(path), "start_menu")
                              for path in root.rglob("*.lnk"))
            except OSError:
                pass
        return result

    @staticmethod
    def _registry():
        try:
            import winreg
        except ImportError:
            return []
        result = []
        base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, base) as parent:
                    index = 0
                    while True:
                        try:
                            key_name = winreg.EnumKey(parent, index)
                            index += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(parent, key_name) as key:
                                target = str(winreg.QueryValueEx(key, "")[0]).strip('"')
                            result.append(AppCandidate(Path(key_name).stem, target, "registry"))
                        except OSError:
                            pass
            except OSError:
                pass
        return result


class DesktopController:
    def __init__(self, cfg):
        self.cfg = cfg
        self.resolver = AppResolver()

    def launch_app(self, app_name: str) -> ToolResult:
        if not IS_WINDOWS:
            return _windows_only("запуск приложений")
        existing = self._find_window(app_name)
        if existing:
            self._focus(existing[0])
            return ToolResult.ok(f"{app_name} уже открыто", data={"app": app_name})
        candidate = self.resolver.resolve(app_name)
        if not candidate:
            return ToolResult.fail("app_not_found", f"Приложение не найдено: {app_name}")
        try:
            # The target is resolver-owned and startfile receives no arguments or shell text.
            os.startfile(candidate.target)  # type: ignore[attr-defined]
        except OSError as exc:
            return ToolResult.fail("app_launch_failed", f"Не удалось открыть {candidate.name}: {exc}")
        return ToolResult.ok(f"Открываю {candidate.name}",
                             data={"app": candidate.name, "source": candidate.source})

    def focus_app(self, app_name: str) -> ToolResult:
        if not IS_WINDOWS:
            return _windows_only("переключение приложений")
        window = self._find_window(app_name)
        if not window:
            return ToolResult.fail("window_not_found", f"Окно не найдено: {app_name}")
        self._focus(window[0])
        return ToolResult.ok(f"Окно активировано: {window[1]}", data={"app": app_name})

    def close_app(self, app_name: str) -> ToolResult:
        if not IS_WINDOWS:
            return _windows_only("закрытие приложений")
        window = self._find_window(app_name)
        if not window:
            return ToolResult.fail("window_not_found", f"Окно не найдено: {app_name}")
        ctypes.windll.user32.PostMessageW(window[0], 0x0010, 0, 0)  # WM_CLOSE
        return ToolResult.ok(f"Закрываю {window[1]}", data={"app": app_name})

    def get_running_apps(self) -> ToolResult:
        if not IS_WINDOWS:
            return _windows_only("список приложений")
        titles = [title for _, title in self._windows()]
        return ToolResult.ok(f"Открыто окон: {len(titles)}", data={"windows": titles})

    def get_volume(self) -> ToolResult:
        endpoint = self._volume_endpoint()
        if isinstance(endpoint, ToolResult):
            return endpoint
        try:
            percent = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            return ToolResult.ok(f"Громкость: {percent}%", data={"percent": percent})
        except Exception as exc:
            return ToolResult.fail("volume_failed", str(exc))

    def set_volume(self, percent: int) -> ToolResult:
        endpoint = self._volume_endpoint()
        if isinstance(endpoint, ToolResult):
            return endpoint
        value = max(0, min(100, int(percent)))
        try:
            endpoint.SetMasterVolumeLevelScalar(value / 100.0, None)
            actual = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            return ToolResult.ok(f"Громкость установлена на {actual}%", data={"percent": actual})
        except Exception as exc:
            return ToolResult.fail("volume_failed", str(exc))

    def change_volume(self, delta: int) -> ToolResult:
        current = self.get_volume()
        if not current.success:
            return current
        return self.set_volume(int(current.data["percent"]) + int(delta))

    def _find_window(self, query: str):
        wanted = self.resolver.ALIASES.get(_norm(query), _norm(query))
        aliases = {wanted}
        if wanted == "google chrome":
            aliases.add("chrome")
        return next(((hwnd, title) for hwnd, title in self._windows()
                     if any(name in _norm(title) for name in aliases)), None)

    @staticmethod
    def _windows():
        result = []
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            result.append((int(hwnd), buffer.value))
            return True

        user32.EnumWindows(callback_type(visit), 0)
        return result

    @staticmethod
    def _focus(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)

    @staticmethod
    def _volume_endpoint():
        if not IS_WINDOWS:
            return _windows_only("управление громкостью")
        try:
            from pycaw.pycaw import AudioUtilities

            device = AudioUtilities.GetSpeakers()
            endpoint = getattr(device, "EndpointVolume", None)
            if endpoint is not None:
                return endpoint
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume

            interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))
        except Exception as exc:
            return ToolResult.fail("volume_backend_unavailable", f"pycaw недоступен: {exc}")


def _norm(value: str) -> str:
    value = str(value or "").casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", value).strip()


def _forbidden_name(value: str) -> bool:
    return any(re.search(rf"(?:^|\s){re.escape(name)}(?:\s|$)", value)
               for name in AppResolver.FORBIDDEN)


def _windows_only(action: str) -> ToolResult:
    return ToolResult.fail("windows_only", f"{action.capitalize()} доступны только в Windows")
