"""Deterministic Steam installation, library, manifest and launch support."""
from __future__ import annotations

import difflib
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .cancellation import CancellationToken
from .types import ErrorCode, ToolResult


@dataclass(frozen=True)
class SteamGame:
    app_id: int
    name: str
    install_dir: str
    install_path: str
    library_path: str
    manifest_path: str
    state_flags: int
    installed: bool

    def as_dict(self):
        return asdict(self)


class SteamController:
    ALIASES = {"дота": "dota 2", "дота 2": "dota 2", "dota": "dota 2"}

    def __init__(self, cfg=None, *, steam_path=None, environ=None, starter=None,
                 process_iter=None, cache_seconds=300):
        self.cfg = cfg or {}
        self._provided_path = Path(steam_path) if steam_path else None
        self.environ = dict(os.environ if environ is None else environ)
        self._starter = starter
        self._process_iter = process_iter
        self.cache_seconds = max(0, int(cache_seconds))
        self._cache: list[SteamGame] | None = None
        self._cache_at = 0.0

    def find_installation(self) -> Path | None:
        if self._provided_path and self._provided_path.is_dir():
            return self._provided_path
        registry = _registry_steam_path()
        if registry:
            return registry
        candidates = [Path(value) / "Steam" for value in (
            self.environ.get("PROGRAMFILES(X86)"), self.environ.get("PROGRAMFILES"),
        ) if value]
        candidates.extend((
            Path.home() / ".steam" / "steam",
            Path.home() / ".local" / "share" / "Steam",
        ))
        return next((path for path in candidates if path.is_dir()), None)

    def library_folders(self) -> list[Path]:
        steam = self.find_installation()
        if not steam:
            return []
        roots = [steam]
        config = steam / "steamapps" / "libraryfolders.vdf"
        try:
            parsed = parse_vdf(config.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            parsed = {}
        folders = _case_get(parsed, "libraryfolders")
        if isinstance(folders, dict):
            for key, value in folders.items():
                if not str(key).isdigit():
                    continue
                raw = _case_get(value, "path") if isinstance(value, dict) else value
                if isinstance(raw, str) and raw.strip():
                    roots.append(Path(raw))
        output, seen = [], set()
        for root in roots:
            key = os.path.normcase(os.path.abspath(str(root)))
            if key not in seen and root.is_dir():
                output.append(root)
                seen.add(key)
        return output

    def installed_games(self, refresh=False) -> list[SteamGame]:
        if (self._cache is not None and not refresh
                and time.time() - self._cache_at < self.cache_seconds):
            return list(self._cache)
        games = {}
        for library in self.library_folders():
            steamapps = library / "steamapps"
            try:
                manifests = list(steamapps.glob("appmanifest_*.acf"))
            except OSError:
                manifests = []
            for manifest in manifests:
                game = parse_app_manifest(manifest, library)
                if game and game.installed:
                    games[game.app_id] = game
        self._cache = sorted(games.values(), key=lambda game: game.name.casefold())
        self._cache_at = time.time()
        return list(self._cache)

    def resolve(self, query: str) -> SteamGame | None:
        wanted = _norm(query)
        wanted = self.ALIASES.get(wanted, wanted)
        if not wanted:
            return None
        best, best_score = None, 0.0
        for game in self.installed_games():
            name = _norm(game.name)
            if wanted == name or wanted == str(game.app_id):
                return game
            score = difflib.SequenceMatcher(None, wanted, name).ratio()
            if wanted in name or name in wanted:
                score += 0.24
            if score > best_score:
                best, best_score = game, score
        return best if best_score >= 0.66 else None

    def get_installed(self) -> ToolResult:
        if not self.find_installation():
            return ToolResult.fail(ErrorCode.STEAM_NOT_FOUND, "Steam не найден")
        games = self.installed_games()
        return ToolResult.ok(f"Установлено игр Steam: {len(games)}",
                             data={"games": [game.as_dict() for game in games]})

    def resolve_game(self, game_name: str) -> ToolResult:
        if not self.find_installation():
            return ToolResult.fail(ErrorCode.STEAM_NOT_FOUND, "Steam не найден")
        game = self.resolve(game_name)
        if not game:
            return ToolResult.fail(ErrorCode.STEAM_GAME_NOT_FOUND,
                                   f"Установленная игра Steam не найдена: {game_name}")
        return ToolResult.ok(f"Найдена игра: {game.name}", data=game.as_dict())

    def launch_game(self, game_name: str, token: CancellationToken | None = None) -> ToolResult:
        resolved = self.resolve_game(game_name)
        if not resolved.success:
            return resolved
        game = SteamGame(**resolved.data)
        if self._is_running(game):
            return ToolResult.ok(f"{game.name} уже запущена", data={
                **game.as_dict(), "existing_instance": True, "verified": True,
            })
        if os.name != "nt" and self._starter is None:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION,
                                   "Запуск Steam-игр поддерживается только в Windows")
        uri = f"steam://run/{game.app_id}"
        try:
            (self._starter or os.startfile)(uri)  # type: ignore[attr-defined]
        except PermissionError as exc:
            return ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        except OSError as exc:
            return ToolResult.fail(ErrorCode.STEAM_LAUNCH_FAILED,
                                   f"Steam не смог запустить {game.name}: {exc}")

        timeout = max(0.0, float(self.cfg.get("steam_launch_timeout_seconds", 20.0)))
        deadline = time.monotonic() + timeout
        while True:
            if token:
                token.raise_if_cancelled()
            if self._is_running(game):
                return ToolResult.ok(f"{game.name} запущена через Steam", data={
                    **game.as_dict(), "launch_uri": uri, "verified": True,
                })
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        return ToolResult.fail(
            ErrorCode.ACTION_TIMEOUT,
            f"Steam принял запуск {game.name}, но процесс игры не появился вовремя",
            data={**game.as_dict(), "launch_uri": uri, "verified": False},
        )

    def _is_running(self, game: SteamGame) -> bool:
        install = os.path.normcase(os.path.abspath(game.install_path)).rstrip("\\/")
        for info in self._processes():
            exe = str(info.get("exe") or "")
            cmdline = " ".join(str(part) for part in (info.get("cmdline") or []))
            if exe:
                candidate = os.path.normcase(os.path.abspath(exe))
                if candidate.startswith(install + os.sep):
                    return True
            if re.search(rf"(?:steam://run/|applaunch\s+){game.app_id}\b", cmdline, re.I):
                return True
        return False

    def _processes(self):
        if self._process_iter:
            return list(self._process_iter())
        try:
            import psutil
        except ImportError:
            return []
        output = []
        for process in psutil.process_iter(["name", "exe", "cmdline"]):
            try:
                output.append(dict(process.info))
            except (psutil.Error, OSError):
                pass
        return output


def parse_app_manifest(path: Path, library_path: Path | None = None) -> SteamGame | None:
    path = Path(path)
    try:
        parsed = parse_vdf(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    state = _case_get(parsed, "AppState")
    if not isinstance(state, dict):
        return None
    app_id = str(_case_get(state, "appid") or "")
    name = str(_case_get(state, "name") or "").strip()
    install_dir = str(_case_get(state, "installdir") or "").strip()
    try:
        state_flags = int(str(_case_get(state, "StateFlags") or "0"))
    except ValueError:
        state_flags = 0
    if not app_id.isdigit() or not name or not install_dir:
        return None
    library = Path(library_path) if library_path else path.parent.parent
    install_path = library / "steamapps" / "common" / install_dir
    installed = bool(state_flags & 4) and install_path.is_dir()
    return SteamGame(
        int(app_id), name, install_dir, str(install_path), str(library), str(path),
        state_flags, installed,
    )


def parse_vdf(body: str) -> dict:
    """Parse the quoted-string/object subset used by Steam local VDF files."""
    tokens = []
    for match in re.finditer(r'"((?:\\.|[^"\\])*)"|([{}])', str(body or "")):
        if match.group(2):
            tokens.append(match.group(2))
        else:
            value = match.group(1).replace(r'\"', '"').replace(r"\\", "\\")
            tokens.append(value)
    index = 0

    def read_object():
        nonlocal index
        output = {}
        while index < len(tokens):
            if tokens[index] == "}":
                index += 1
                return output
            key = tokens[index]
            index += 1
            if key == "{" or index >= len(tokens):
                continue
            if tokens[index] == "{":
                index += 1
                value = read_object()
            else:
                value = tokens[index]
                index += 1
            output[key] = value
        return output

    return read_object()


def _case_get(mapping, key):
    if not isinstance(mapping, dict):
        return None
    wanted = str(key).casefold()
    return next((value for name, value in mapping.items()
                 if str(name).casefold() == wanted), None)


def _registry_steam_path() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive, key, names in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", ("SteamPath", "InstallPath")),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", ("InstallPath",)),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                for name in names:
                    try:
                        path = Path(str(winreg.QueryValueEx(handle, name)[0]))
                        if path.is_dir():
                            return path
                    except OSError:
                        pass
        except OSError:
            pass
    return None


def _norm(value: str) -> str:
    value = str(value or "").casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", value).strip()
