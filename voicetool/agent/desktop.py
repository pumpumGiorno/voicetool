"""Deterministic native Windows automation for Alice Stage 2.

This module deliberately stops before UI Automation, mouse coordinates, screenshots,
vision and Steam-specific control. Model input can select only the typed operations
registered in ``tools.py``; it is never interpreted as a process command line.
"""
from __future__ import annotations

import ctypes
import difflib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .cancellation import CancellationToken
from .types import ErrorCode, ToolResult

IS_WINDOWS = os.name == "nt" and hasattr(ctypes, "windll")

# Opening a file delegates to its registered Windows handler.  Executable/script/project
# formats are intentionally absent: an agent may reveal those with show_in_folder, but it
# must not launch them through a supposedly low-risk document operation.
SAFE_OPEN_FILE_EXTENSIONS = {
    ".txt", ".md", ".log", ".rtf", ".pdf", ".doc", ".docx", ".odt",
    ".xls", ".xlsx", ".ods", ".csv", ".ppt", ".pptx", ".odp",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma",
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".m4v",
    ".zip", ".7z", ".rar", ".json", ".yaml", ".yml", ".xml",
}


@dataclass(frozen=True)
class AppCandidate:
    name: str
    target: str
    source: str
    process_name: str = ""

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str = ""
    minimized: bool = False
    maximized: bool = False

    def as_dict(self):
        return asdict(self)


class AppResolver:
    """Discover applications and resolve human names with persistent aliases."""

    BUILTIN_ALIASES = {
        "телега": "telegram", "тг": "telegram", "телеграм": "telegram",
        "хром": "google chrome", "chrome": "google chrome",
        "дискорд": "discord", "дота": "dota 2", "dota": "dota 2",
        "код": "visual studio code", "вс код": "visual studio code",
        "вскод": "visual studio code", "vscode": "visual studio code",
        "блокнот": "notepad", "проводник": "file explorer",
        "калькулятор": "calculator",
    }
    EXECUTABLE_HINTS = {
        "notepad": "notepad.exe", "calculator": "calc.exe",
        "file explorer": "explorer.exe", "microsoft edge": "msedge.exe",
        "google chrome": "chrome.exe", "visual studio code": "code.exe",
        "telegram": "Telegram.exe", "discord": "Discord.exe", "spotify": "Spotify.exe",
    }
    COMMON_PATTERNS = {
        "google chrome": (
            ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
            ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
            ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
        ),
        "telegram": (
            ("APPDATA", "Telegram Desktop/Telegram.exe"),
            ("LOCALAPPDATA", "Programs/Telegram Desktop/Telegram.exe"),
        ),
        "discord": (("LOCALAPPDATA", "Discord/app-*/Discord.exe"),),
        "visual studio code": (
            ("LOCALAPPDATA", "Programs/Microsoft VS Code/Code.exe"),
            ("PROGRAMFILES", "Microsoft VS Code/Code.exe"),
        ),
    }
    FORBIDDEN = {"cmd", "command prompt", "powershell", "pwsh", "windows terminal", "terminal"}

    def __init__(self, data_dir: Path, *, cache_seconds=180, environ=None):
        self.aliases_path = Path(data_dir) / "app_aliases.json"
        self.cache_seconds = max(0, int(cache_seconds))
        self.environ = dict(os.environ if environ is None else environ)
        self._cache: list[AppCandidate] = []
        self._cache_at = 0.0
        self._lock = threading.RLock()

    def aliases(self) -> dict[str, str]:
        try:
            body = json.loads(self.aliases_path.read_text(encoding="utf-8"))
            if not isinstance(body, dict):
                return {}
            return {_norm(k): str(v).strip() for k, v in body.items()
                    if _norm(k) and str(v).strip()}
        except (OSError, ValueError, TypeError):
            return {}

    def set_alias(self, alias: str, target: str):
        alias = _norm(alias)
        target = str(target or "").strip()
        if (not alias or not target or len(alias) > 120 or len(target) > 32_000
                or any(ch in target for ch in "\r\n\x00") or _forbidden_name(alias)):
            raise ValueError("Некорректный alias приложения")
        body = self.aliases()
        body[alias] = target
        self.aliases_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.aliases_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.aliases_path)

    def remove_alias(self, alias: str) -> bool:
        alias = _norm(alias)
        body = self.aliases()
        if alias not in body:
            return False
        body.pop(alias)
        self.aliases_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.aliases_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.aliases_path)
        return True

    def candidates(self, refresh=False) -> list[AppCandidate]:
        with self._lock:
            if (self._cache and not refresh
                    and time.time() - self._cache_at < self.cache_seconds):
                return list(self._cache)
            found = []
            found.extend(self._hint_candidates())
            found.extend(self._start_menu_candidates())
            found.extend(self._registry_candidates())
            found.extend(self._path_candidates())
            found.extend(self._common_location_candidates())
            found.extend(self._running_candidates())
            deduped = {}
            for item in found:
                if not item.name or not item.target or _forbidden_name(_norm(item.name)):
                    continue
                key = (_norm(item.name), str(item.target).casefold())
                deduped.setdefault(key, item)
            self._cache = list(deduped.values())
            self._cache_at = time.time()
            return list(self._cache)

    def resolve(self, query: str) -> AppCandidate | None:
        raw = str(query or "")
        if not raw.strip() or len(raw) > 260 or any(ch in raw for ch in "\r\n\x00"):
            return None
        query_norm = _norm(raw)
        aliases = self.aliases()
        alias_target = aliases.get(query_norm)
        if alias_target:
            path = Path(alias_target).expanduser()
            if path.is_file():
                if _forbidden_name(_norm(path.stem)):
                    return None
                return AppCandidate(path.stem, str(path), "user_alias", path.name)
            query_norm = _norm(alias_target)
        query_norm = self.BUILTIN_ALIASES.get(query_norm, query_norm)
        if _forbidden_name(query_norm):
            return None

        best, best_score = None, 0.0
        bonuses = {"user_alias": 0.45, "running": 0.34, "start_menu": 0.31,
                   "registry": 0.28, "common": 0.25, "path": 0.18, "known": 0.08}
        for item in self.candidates():
            names = {_norm(item.name), _norm(Path(item.target).stem), _norm(item.process_name)}
            names.discard("")
            if any(_forbidden_name(name) for name in names):
                continue
            exact = query_norm in names
            score = 1.0 if exact else max(
                (difflib.SequenceMatcher(None, query_norm, name).ratio() for name in names),
                default=0.0,
            )
            if not exact and any(query_norm in name or name in query_norm for name in names):
                score += 0.18
            score += bonuses.get(item.source, 0.0)
            if score > best_score:
                best, best_score = item, score
        return best if best_score >= 0.68 else None

    def _hint_candidates(self) -> Iterable[AppCandidate]:
        output = []
        for name, executable in self.EXECUTABLE_HINTS.items():
            target = shutil.which(executable)
            if target:
                output.append(AppCandidate(name, target, "known", executable))
        return output

    def _start_menu_candidates(self) -> Iterable[AppCandidate]:
        if not IS_WINDOWS:
            return []
        roots = [
            Path(self.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path(self.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        ]
        output = []
        for root in roots:
            if not root.is_dir():
                continue
            try:
                output.extend(AppCandidate(path.stem, str(path), "start_menu")
                              for path in root.rglob("*.lnk"))
            except OSError:
                pass
        return output

    def _registry_candidates(self) -> Iterable[AppCandidate]:
        if not IS_WINDOWS:
            return []
        try:
            import winreg
        except ImportError:
            return []
        output = []
        app_paths = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            output.extend(self._registry_app_paths(winreg, hive, app_paths))
        uninstall_roots = (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        )
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for root in uninstall_roots:
                output.extend(self._registry_uninstall(winreg, hive, root))
        return output

    @staticmethod
    def _registry_app_paths(winreg, hive, root):
        output = []
        try:
            with winreg.OpenKey(hive, root) as parent:
                for sub_name in _registry_keys(winreg, parent):
                    try:
                        with winreg.OpenKey(parent, sub_name) as key:
                            target = str(winreg.QueryValueEx(key, "")[0]).strip('"')
                        if target:
                            output.append(AppCandidate(Path(sub_name).stem, target,
                                                       "registry", sub_name))
                    except OSError:
                        pass
        except OSError:
            pass
        return output

    @staticmethod
    def _registry_uninstall(winreg, hive, root):
        output = []
        try:
            with winreg.OpenKey(hive, root) as parent:
                for sub_name in _registry_keys(winreg, parent):
                    try:
                        with winreg.OpenKey(parent, sub_name) as key:
                            name = str(winreg.QueryValueEx(key, "DisplayName")[0]).strip()
                            try:
                                icon = str(winreg.QueryValueEx(key, "DisplayIcon")[0])
                            except OSError:
                                icon = ""
                            try:
                                location = str(winreg.QueryValueEx(key, "InstallLocation")[0])
                            except OSError:
                                location = ""
                        target = icon.split(",", 1)[0].strip('" ')
                        if not Path(target).is_file() and location:
                            executables = list(Path(location).glob("*.exe"))[:1]
                            target = str(executables[0]) if executables else ""
                        if name and target and Path(target).is_file():
                            output.append(AppCandidate(name, target, "registry", Path(target).name))
                    except (OSError, ValueError):
                        pass
        except OSError:
            pass
        return output

    def _path_candidates(self) -> Iterable[AppCandidate]:
        if not IS_WINDOWS:
            return []
        output = []
        for directory in self.environ.get("PATH", "").split(os.pathsep):
            root = Path(directory.strip('"'))
            if not root.is_dir():
                continue
            try:
                for path in list(root.glob("*.exe"))[:300]:
                    output.append(AppCandidate(path.stem, str(path), "path", path.name))
                    if len(output) >= 2000:
                        return output
            except OSError:
                pass
        return output

    def _common_location_candidates(self) -> Iterable[AppCandidate]:
        output = []
        for name, patterns in self.COMMON_PATTERNS.items():
            for env_name, relative in patterns:
                root = Path(self.environ.get(env_name, ""))
                if not root.is_dir():
                    continue
                try:
                    for path in list(root.glob(relative))[:20]:
                        if path.is_file():
                            output.append(AppCandidate(name, str(path), "common", path.name))
                except OSError:
                    pass
        return output

    @staticmethod
    def _running_candidates() -> Iterable[AppCandidate]:
        try:
            import psutil
        except ImportError:
            return []
        output = []
        for process in psutil.process_iter(["name", "exe"]):
            try:
                name = process.info.get("name") or ""
                target = process.info.get("exe") or name
                if name and target:
                    output.append(AppCandidate(Path(name).stem, target, "running", name))
            except (psutil.Error, OSError):
                pass
        return output


class NativeWindowManager:
    """Small Win32 window facade with foreground verification."""

    def list(self) -> list[WindowInfo]:
        if not IS_WINDOWS:
            return []
        user32 = ctypes.windll.user32
        process_names = _process_names()
        output = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            output.append(WindowInfo(
                int(hwnd), buffer.value, int(pid.value), process_names.get(int(pid.value), ""),
                bool(user32.IsIconic(hwnd)), bool(user32.IsZoomed(hwnd)),
            ))
            return True

        user32.EnumWindows(callback_type(visit), 0)
        return output

    def find(self, query: str) -> WindowInfo | None:
        raw = str(query or "").strip()
        if raw.isdigit():
            handle = int(raw)
            return next((window for window in self.list() if window.hwnd == handle), None)
        wanted = AppResolver.BUILTIN_ALIASES.get(_norm(raw), _norm(raw))
        best, best_score = None, 0.0
        for window in self.list():
            values = {_norm(window.title), _norm(Path(window.process).stem)}
            score = max((1.0 if wanted == value else
                         difflib.SequenceMatcher(None, wanted, value).ratio()
                         for value in values if value), default=0.0)
            if any(wanted in value or value in wanted for value in values if value):
                score += 0.25
            if score > best_score:
                best, best_score = window, score
        return best if best_score >= 0.62 else None

    def active(self) -> WindowInfo | None:
        if not IS_WINDOWS:
            return None
        handle = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        return next((window for window in self.list() if window.hwnd == handle), None)

    @staticmethod
    def focus(window: WindowInfo) -> bool:
        if not IS_WINDOWS:
            return False
        from .. import inject

        return bool(inject.focus_window(window.hwnd)
                    and int(ctypes.windll.user32.GetForegroundWindow() or 0) == window.hwnd)

    @staticmethod
    def show(window: WindowInfo, command: int) -> bool:
        if not IS_WINDOWS:
            return False
        user32 = ctypes.windll.user32
        user32.ShowWindow(window.hwnd, command)
        time.sleep(0.08)
        if command == 6:
            return bool(user32.IsIconic(window.hwnd))
        if command == 3:
            return bool(user32.IsZoomed(window.hwnd))
        if command == 9:
            return not bool(user32.IsIconic(window.hwnd))
        return True

    def close(self, window: WindowInfo, timeout=1.5) -> bool:
        if not IS_WINDOWS:
            return False
        ctypes.windll.user32.PostMessageW(window.hwnd, 0x0010, 0, 0)
        deadline = time.monotonic() + max(0.1, float(timeout))
        while time.monotonic() < deadline:
            if not any(item.hwnd == window.hwnd for item in self.list()):
                return True
            time.sleep(0.05)
        return False


class DesktopController:
    """Production coordinator for Stage 2 deterministic Windows actions."""

    def __init__(self, cfg, *, resolver=None, windows=None, input_backend=None,
                 volume_endpoint_factory=None, browser_opener=None, starter=None,
                 explorer_launcher=None):
        self.cfg = cfg
        self.resolver = resolver or AppResolver(
            cfg.data_dir, cache_seconds=cfg.get("app_resolver_cache_seconds", 180))
        self.windows = windows or NativeWindowManager()
        self._input_backend = input_backend
        self._volume_endpoint_factory = volume_endpoint_factory
        self._browser_opener = browser_opener or webbrowser.open
        self._starter = starter
        self._explorer_launcher = explorer_launcher

    def resolve_app(self, app_name: str) -> ToolResult:
        item = self.resolver.resolve(app_name)
        if not item:
            return ToolResult.fail(ErrorCode.APP_NOT_FOUND,
                                   f"Приложение не найдено: {app_name}")
        return ToolResult.ok(f"Найдено: {item.name}", data={"app": item.name, **item.as_dict()})

    def list_installed_apps(self) -> ToolResult:
        apps = self.resolver.candidates()
        return ToolResult.ok(f"Найдено приложений: {len(apps)}",
                             data={"apps": [item.as_dict() for item in apps[:500]]})

    def launch_app(self, app_name: str, token: CancellationToken | None = None) -> ToolResult:
        existing = self.windows.find(app_name)
        if existing:
            focused = self._focus_info(existing)
            if focused.success:
                focused.message = f"{app_name} уже запущено — окно активировано"
                focused.data["app"] = app_name
                focused.data["existing_instance"] = True
                return focused
        item = self.resolver.resolve(app_name)
        if not item:
            return ToolResult.fail(ErrorCode.APP_NOT_FOUND,
                                   f"Приложение не найдено: {app_name}")
        if not IS_WINDOWS and self._starter is None:
            return _unsupported("Запуск приложений")
        try:
            (self._starter or os.startfile)(item.target)  # type: ignore[attr-defined]
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        except OSError as exc:
            return _os_failure("Не удалось запустить приложение", exc)

        timeout = max(0.0, float(self.cfg.get("app_launch_timeout_seconds", 5.0)))
        deadline = time.monotonic() + timeout
        while True:
            if token:
                token.raise_if_cancelled()
            window = self.windows.find(item.process_name or item.name)
            if window:
                focused = self._focus_info(window)
                if not focused.success:
                    focused.data.update({
                        "app": item.name, "target": item.target, "verified": True,
                        "window": window.as_dict(),
                    })
                    return focused
                return ToolResult.ok(f"{item.name} запущено и активировано", data={
                    "app": item.name, "target": item.target, "verified": True,
                    "focused": True, "window": window.as_dict(),
                })
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        return ToolResult.fail(ErrorCode.ACTION_TIMEOUT,
                               f"Запуск {item.name} отправлен, но окно не появилось вовремя",
                               data={"app": item.name, "target": item.target, "verified": False})

    def focus_app(self, app_name: str) -> ToolResult:
        window = self.windows.find(app_name)
        if not window:
            return ToolResult.fail(ErrorCode.WINDOW_NOT_FOUND, f"Окно не найдено: {app_name}")
        result = self._focus_info(window)
        if result.success:
            result.data["app"] = app_name
        return result

    def close_app(self, app_name: str) -> ToolResult:
        window = self.windows.find(app_name)
        if not window:
            return ToolResult.fail(ErrorCode.WINDOW_NOT_FOUND, f"Окно не найдено: {app_name}")
        result = self._close_info(window)
        result.data.setdefault("app", app_name)
        return result

    def get_running_apps(self) -> ToolResult:
        windows = self.windows.list()
        apps = sorted({window.process or window.title for window in windows}, key=str.casefold)
        return ToolResult.ok(f"Открыто приложений: {len(apps)}", data={"apps": apps})

    def list_windows(self) -> ToolResult:
        windows = self.windows.list()
        return ToolResult.ok(f"Открыто окон: {len(windows)}",
                             data={"windows": [window.as_dict() for window in windows]})

    def get_active_window(self) -> ToolResult:
        window = self.windows.active()
        if not window:
            return ToolResult.fail(ErrorCode.WINDOW_NOT_FOUND,
                                   "Не удалось определить активное окно")
        return ToolResult.ok(window.title, data=window.as_dict())

    def focus_window(self, window: str) -> ToolResult:
        info = self.windows.find(window)
        if not info:
            return ToolResult.fail(ErrorCode.WINDOW_NOT_FOUND, f"Окно не найдено: {window}")
        return self._focus_info(info)

    def minimize_window(self, window: str) -> ToolResult:
        return self._show_window(window, 6, "Окно свёрнуто")

    def maximize_window(self, window: str) -> ToolResult:
        return self._show_window(window, 3, "Окно развёрнуто")

    def restore_window(self, window: str) -> ToolResult:
        return self._show_window(window, 9, "Окно восстановлено")

    def close_window(self, window: str) -> ToolResult:
        info = self.windows.find(window)
        if not info:
            return ToolResult.fail(ErrorCode.WINDOW_NOT_FOUND, f"Окно не найдено: {window}")
        return self._close_info(info)

    def _focus_info(self, window: WindowInfo) -> ToolResult:
        try:
            focused = self.windows.focus(window)
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        if not focused:
            return ToolResult.fail(ErrorCode.ACTION_TIMEOUT,
                                   f"Windows не подтвердила фокус окна: {window.title}")
        return ToolResult.ok(f"Окно активировано: {window.title}", data=window.as_dict())

    def _show_window(self, query: str, command: int, message: str) -> ToolResult:
        window = self.windows.find(query)
        if not window:
            return ToolResult.fail(ErrorCode.WINDOW_NOT_FOUND, f"Окно не найдено: {query}")
        try:
            verified = self.windows.show(window, command)
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        if not verified:
            return ToolResult.fail(ErrorCode.ACTION_TIMEOUT,
                                   f"Windows не подтвердила изменение окна: {window.title}")
        return ToolResult.ok(message, data=window.as_dict())

    def _close_info(self, window: WindowInfo) -> ToolResult:
        try:
            closed = self.windows.close(window)
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        if not closed:
            return ToolResult.fail(ErrorCode.ACTION_TIMEOUT,
                                   f"Окно не закрылось вовремя: {window.title}",
                                   data=window.as_dict())
        return ToolResult.ok(f"Окно закрыто: {window.title}", data=window.as_dict())

    def open_url(self, url: str) -> ToolResult:
        try:
            validated = validate_url(url, self.cfg.get("allowed_url_schemes", []))
        except ValueError as exc:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT, str(exc))
        try:
            opened = bool(self._browser_opener(validated, new=2))
        except (OSError, webbrowser.Error) as exc:
            return _os_failure("Не удалось открыть браузер", exc)
        if not opened:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION,
                                   "Системный браузер не принял URL")
        return ToolResult.ok("URL открыт в браузере", data={"url": validated})

    def type_text(self, text: str) -> ToolResult:
        text = str(text or "")
        if not text.strip() or len(text) > 20_000:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "Текст должен содержать от 1 до 20000 символов")
        try:
            ok = bool(self._input().type_text(
                text, pause=max(0, int(self.cfg.get("type_delay_ms", 15))) / 1000))
        except Exception as exc:
            return _input_failure(exc)
        return (ToolResult.ok("Текст введён", data={"characters": len(text), "unicode": True})
                if ok else ToolResult.fail(ErrorCode.INVALID_ARGUMENT, "Текст пуст"))

    def press_key(self, key: str) -> ToolResult:
        return self.press_keys([key])

    def press_keys(self, keys: list[str]) -> ToolResult:
        normalised = [_normalise_key(key) for key in keys]
        if (not normalised or len(normalised) > 5 or any(not key for key in normalised)
                or any(key in {"enter", "return"} for key in normalised)):
            return ToolResult.fail(
                ErrorCode.INVALID_ARGUMENT,
                "Неподдерживаемая клавиша; submit/Enter automation отключена на Stage 2",
            )
        try:
            self._input().press_keys(normalised)
        except Exception as exc:
            return _input_failure(exc)
        return ToolResult.ok("Клавиши нажаты", data={"keys": normalised})

    def _input(self):
        if self._input_backend is None:
            from .. import inject

            self._input_backend = inject
        return self._input_backend

    def get_volume(self) -> ToolResult:
        endpoint = self._volume_endpoint()
        if isinstance(endpoint, ToolResult):
            return endpoint
        try:
            percent = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            muted = bool(endpoint.GetMute())
            return ToolResult.ok(f"Громкость: {percent}%",
                                 data={"percent": percent, "muted": muted})
        except Exception as exc:
            return _os_failure("Не удалось получить громкость", exc)

    def set_volume(self, percent: int) -> ToolResult:
        if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "Громкость должна быть целым числом от 0 до 100")
        endpoint = self._volume_endpoint()
        if isinstance(endpoint, ToolResult):
            return endpoint
        try:
            endpoint.SetMasterVolumeLevelScalar(percent / 100.0, None)
            actual = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            return ToolResult.ok(f"Громкость установлена на {actual}%", data={"percent": actual})
        except Exception as exc:
            return _os_failure("Не удалось изменить громкость", exc)

    def change_volume(self, delta: int) -> ToolResult:
        if isinstance(delta, bool) or not isinstance(delta, int) or not -100 <= delta <= 100:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "Изменение громкости должно быть от -100 до 100")
        current = self.get_volume()
        if not current.success:
            return current
        return self.set_volume(max(0, min(100, int(current.data["percent"]) + delta)))

    def mute_volume(self) -> ToolResult:
        return self._set_mute(True)

    def unmute_volume(self) -> ToolResult:
        return self._set_mute(False)

    def _set_mute(self, value: bool) -> ToolResult:
        endpoint = self._volume_endpoint()
        if isinstance(endpoint, ToolResult):
            return endpoint
        try:
            endpoint.SetMute(bool(value), None)
            actual = bool(endpoint.GetMute())
            return ToolResult.ok("Звук выключен" if actual else "Звук включён",
                                 data={"muted": actual})
        except Exception as exc:
            return _os_failure("Не удалось изменить mute", exc)

    def _volume_endpoint(self):
        if self._volume_endpoint_factory:
            try:
                return self._volume_endpoint_factory()
            except Exception as exc:
                return _os_failure("Volume backend недоступен", exc)
        if not IS_WINDOWS:
            return _unsupported("Управление громкостью")
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
            return _os_failure("Volume backend недоступен", exc)

    def open_file(self, path: str) -> ToolResult:
        return self._open_path(path, directory=False)

    def open_folder(self, path: str) -> ToolResult:
        symbolic = {
            "home": Path.home(), "downloads": Path.home() / "Downloads",
            "desktop": Path.home() / "Desktop", "documents": Path.home() / "Documents",
            "pictures": Path.home() / "Pictures", "music": Path.home() / "Music",
            "videos": Path.home() / "Videos",
        }
        actual = symbolic.get(str(path).casefold(), Path(str(path)).expanduser())
        return self._open_path(str(actual), directory=True)

    def find_file(self, query: str, token: CancellationToken, root="", max_results=20) -> ToolResult:
        query = str(query or "").strip().casefold()
        if not query or len(query) > 260 or any(ch in query for ch in "\r\n\x00"):
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT, "Некорректный запрос поиска")
        limit = max(1, min(100, int(max_results)))
        roots = [Path(root).expanduser()] if root else [
            Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads",
        ]
        if root and not roots[0].is_dir():
            return ToolResult.fail(ErrorCode.FILE_NOT_FOUND, f"Папка не найдена: {root}")
        matches = []
        visited = 0
        try:
            for search_root in roots:
                if not search_root.is_dir():
                    continue
                for current, directories, files in os.walk(search_root):
                    token.raise_if_cancelled()
                    directories[:] = [name for name in directories if not name.startswith(".")]
                    visited += 1
                    if visited > 100_000:
                        return ToolResult.fail(ErrorCode.ACTION_TIMEOUT,
                                               "Поиск остановлен: просмотрено слишком много папок")
                    for name in files:
                        if query in name.casefold():
                            matches.append(str(Path(current) / name))
                            if len(matches) >= limit:
                                break
                    if len(matches) >= limit:
                        break
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        if not matches:
            return ToolResult.fail(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {query}")
        return ToolResult.ok(f"Найдено файлов: {len(matches)}",
                             data={"paths": matches, "path": matches[0]})

    def show_in_folder(self, path: str) -> ToolResult:
        target = Path(str(path or "")).expanduser()
        if not target.exists():
            return ToolResult.fail(ErrorCode.FILE_NOT_FOUND, f"Путь не найден: {path}")
        if not IS_WINDOWS and self._explorer_launcher is None:
            return _unsupported("Показ файла в Проводнике")
        try:
            if self._explorer_launcher:
                self._explorer_launcher(str(target))
            else:
                subprocess.Popen(["explorer.exe", "/select,", str(target)])
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        except OSError as exc:
            return _os_failure("Не удалось открыть Проводник", exc)
        return ToolResult.ok("Файл показан в Проводнике", data={"path": str(target)})

    def _open_path(self, path: str, *, directory: bool) -> ToolResult:
        target = Path(str(path or "")).expanduser()
        if not target.exists() or (directory and not target.is_dir()) or (not directory and not target.is_file()):
            return ToolResult.fail(ErrorCode.FILE_NOT_FOUND, f"Путь не найден: {path}")
        try:
            target = target.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, f"Небезопасный путь: {exc}")
        if not directory and target.suffix.casefold() not in SAFE_OPEN_FILE_EXTENSIONS:
            return ToolResult.fail(
                ErrorCode.ACCESS_DENIED,
                "Этот тип файла нельзя запускать через open_file; используйте show_in_folder",
                data={"path": str(target)},
            )
        if not IS_WINDOWS and self._starter is None:
            return _unsupported("Открытие файлов и папок")
        try:
            (self._starter or os.startfile)(str(target))  # type: ignore[attr-defined]
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        except OSError as exc:
            return _os_failure("Не удалось открыть путь", exc)
        return ToolResult.ok("Путь открыт", data={"path": str(target)})


WindowsAutomation = DesktopController


def validate_url(value: str, allowed_custom_schemes=()) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 4096 or any(ord(ch) < 32 for ch in value):
        raise ValueError("Некорректный URL")
    parsed = urlparse(value)
    scheme = parsed.scheme.casefold()
    allowed = {str(item).casefold() for item in allowed_custom_schemes
               if re.fullmatch(r"[a-z][a-z0-9+.-]*", str(item), re.I)}
    if scheme in {"http", "https"}:
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("HTTP(S) URL должен содержать безопасный hostname без credentials")
        return value
    if scheme and scheme in allowed:
        return value
    raise ValueError("Разрешены только http/https и явно allowlisted custom schemes")


def _registry_keys(winreg, parent):
    index = 0
    while True:
        try:
            yield winreg.EnumKey(parent, index)
            index += 1
        except OSError:
            break


def _process_names():
    try:
        import psutil
    except ImportError:
        return {}
    output = {}
    for process in psutil.process_iter(["pid", "name"]):
        try:
            output[int(process.info["pid"])] = str(process.info.get("name") or "")
        except (psutil.Error, OSError, TypeError, ValueError):
            pass
    return output


def _norm(value: str) -> str:
    value = str(value or "").casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", value).strip()


def _forbidden_name(value: str) -> bool:
    return any(re.search(rf"(?:^|\s){re.escape(name)}(?:\s|$)", value)
               for name in AppResolver.FORBIDDEN)


def _normalise_key(value: str) -> str:
    aliases = {"контрол": "ctrl", "шифт": "shift", "альт": "alt", "вин": "win",
               "эскейп": "escape", "пробел": "space"}
    raw = str(value or "").strip().casefold()
    key = aliases.get(raw, raw)
    if re.fullmatch(r"(?:ctrl|shift|alt|win|tab|escape|space|backspace|delete|home|end|"
                    r"pageup|pagedown|up|down|left|right|f(?:[1-9]|1\d|2[0-4])|[a-z0-9])", key):
        return key
    return ""


def _unsupported(action: str) -> ToolResult:
    return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION,
                           f"{action} поддерживается только в Windows")


def _os_failure(message: str, exc: Exception) -> ToolResult:
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
        return ToolResult.fail(ErrorCode.ACCESS_DENIED, f"{message}: {exc}")
    return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, f"{message}: {exc}")


def _input_failure(exc: Exception) -> ToolResult:
    text = str(exc)
    code = ErrorCode.ACCESS_DENIED if re.search(
        r"access|denied|administrator|администратор|прав", text, re.I
    ) else ErrorCode.UNSUPPORTED_ACTION
    return ToolResult.fail(code, text or "Windows не приняла ввод")
