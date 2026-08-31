"""Deterministic Stage 2 grammar before local-model routing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .session import AgentSession
from .types import ToolCall


class RouteKind(str, Enum):
    DICTATION = "dictation"
    FAST_PATH = "fast_path"
    AGENT = "agent"
    AMBIGUOUS = "ambiguous"
    CANCEL = "cancel"


@dataclass
class RouteDecision:
    kind: RouteKind
    confidence: float
    calls: list[ToolCall] = field(default_factory=list)
    reason: str = ""


SITE_URLS = {
    "youtube": "https://www.youtube.com", "ютуб": "https://www.youtube.com",
    "google": "https://www.google.com", "гугл": "https://www.google.com",
    "github": "https://github.com", "гитхаб": "https://github.com",
}
FOLDER_ALIASES = {
    "загрузки": "downloads", "загрузок": "downloads", "downloads": "downloads",
    "рабочий стол": "desktop", "desktop": "desktop",
    "документы": "documents", "documents": "documents",
    "изображения": "pictures", "картинки": "pictures", "pictures": "pictures",
    "музыка": "music", "music": "music", "видео": "videos", "videos": "videos",
    "домашняя": "home", "home": "home",
}
_CANCEL = {"стоп", "остановись", "отмена", "отмени", "прекрати", "stop", "cancel"}
_PRONOUNS = {"его", "ее", "её", "это", "его окно", "ее окно", "её окно"}
_ACTION_OPENERS = re.compile(
    r"^(?:пожалуйста\s+)?(?:открой|запусти|закрой|сделай|поставь|уменьши|увеличь|"
    r"выключи|включи|потише|погромче|переключись|сверни|разверни|восстанови|"
    r"найди|напиши|введи|нажми|выполни|организуй|open|launch|close|set|find|type|press|do)\b",
    re.I,
)
_DESKTOP_OBJECT = re.compile(
    r"\b(?:приложени\w*|окн\w*|громкост\w*|звук\w*|telegram|телеграм\w*|"
    r"chrome|хром\w*|браузер\w*|файл\w*|папк\w*|youtube|ютуб|desktop|windows)\b",
    re.I,
)


class VoiceCommandRouter:
    def __init__(self, cfg, session: AgentSession):
        self.cfg = cfg
        self.session = session

    def route(self, text: str) -> RouteDecision:
        cleaned = _clean(text)
        if cleaned in _CANCEL:
            return RouteDecision(RouteKind.CANCEL, 1.0, reason="explicit cancellation")
        mode = str(self.cfg.get("voice_mode", "smart") or "smart").lower()
        if mode == "dictation" or not bool(self.cfg.get("agent_enabled", True)):
            return RouteDecision(RouteKind.DICTATION, 1.0, reason="dictation mode")

        calls = self._fast_plan(cleaned)
        if calls:
            return RouteDecision(RouteKind.FAST_PATH, 1.0, calls, "validated native grammar")
        if mode == "agent":
            return RouteDecision(RouteKind.AGENT, 1.0, reason="agent mode")

        opener = bool(_ACTION_OPENERS.search(cleaned))
        desktop_object = bool(_DESKTOP_OBJECT.search(cleaned))
        question = cleaned.startswith(("как ", "почему ", "что ", "когда ", "где "))
        if opener and desktop_object and not question:
            return RouteDecision(RouteKind.AGENT, 0.86, reason="imperative desktop request")
        if opener and not question:
            return RouteDecision(RouteKind.AMBIGUOUS, 0.55, reason="action-like request")
        return RouteDecision(RouteKind.DICTATION, 0.92, reason="ordinary dictation")

    def _fast_plan(self, text: str) -> list[ToolCall]:
        segments = _split_steps(text)
        if not segments:
            return []
        calls = []
        for segment in segments:
            call = self._parse_segment(segment)
            if call is None:
                return []
            calls.append(call)
        return calls

    def _parse_segment(self, segment: str) -> ToolCall | None:
        segment = segment.strip(" ,.!?—-")
        volume = _volume_call(segment)
        if volume:
            return volume

        match = re.fullmatch(r"(?:напиши|введи|набери|type)\s*:?[ ]*(.+)", segment)
        if match:
            text = match.group(1).strip()
            return ToolCall("type_text", {"text": text}) if text else None

        match = re.fullmatch(r"(?:нажми|press)\s+(.+)", segment)
        if match:
            keys = _normalise_keys(match.group(1))
            if not keys:
                return None
            return ToolCall("press_keys" if len(keys) > 1 else "press_key",
                            {"keys": keys} if len(keys) > 1 else {"key": keys[0]})

        match = re.fullmatch(r"(?:найди|поищи|find)\s+(?:файл|file)\s+(.+)", segment)
        if match:
            return ToolCall("find_file", {"query": match.group(1).strip(' :"')})

        match = re.fullmatch(r"(?:покажи|открой расположение)\s+(.+?)\s+(?:в папке|в проводнике)", segment)
        if match:
            return ToolCall("show_in_folder", {"path": match.group(1).strip(' "')})

        match = re.fullmatch(r"(?:открой|зайди на|перейди на|open)\s+(.+)", segment)
        if match:
            target = _target(match.group(1))
            if target in FOLDER_ALIASES:
                return ToolCall("open_folder", {"path": FOLDER_ALIASES[target]})
            if target.startswith(("папку ", "folder ")):
                return ToolCall("open_folder", {"path": target.split(" ", 1)[1]})
            if target.startswith(("файл ", "file ")):
                return ToolCall("open_file", {"path": target.split(" ", 1)[1]})
            if target in SITE_URLS:
                return ToolCall("open_url", {"url": SITE_URLS[target]})
            if re.fullmatch(r"https?://\S+", target):
                return ToolCall("open_url", {"url": target})
            if re.fullmatch(r"(?:[\w-]+\.)+[a-zа-я]{2,}(?:/\S*)?", target, re.I):
                return ToolCall("open_url", {"url": "https://" + target})
            return (ToolCall("launch_app", {"app_name": target})
                    if _simple_app_target(target) else None)

        match = re.fullmatch(r"(?:запусти|launch|start)\s+(.+)", segment)
        if match:
            target = _target(match.group(1))
            return (ToolCall("launch_app", {"app_name": target})
                    if _simple_app_target(target) else None)

        match = re.fullmatch(r"(?:закрой|close)\s+(.+)", segment)
        if match:
            target = self._resolve_reference(_target(match.group(1)), "app")
            if target.startswith("окно "):
                return ToolCall("close_window", {"window": target[5:]})
            return ToolCall("close_app", {"app_name": target}) if target else None

        match = re.fullmatch(r"(?:переключись на|переключи на|вернись в|focus)\s+(.+)", segment)
        if match:
            target = self._resolve_reference(_target(match.group(1)), "app")
            return ToolCall("focus_app", {"app_name": target}) if target else None

        for pattern, tool in (
            (r"(?:сверни|minimi[sz]e)\s+(.+)", "minimize_window"),
            (r"(?:разверни|maximi[sz]e)\s+(.+)", "maximize_window"),
            (r"(?:восстанови|restore)\s+(.+)", "restore_window"),
        ):
            match = re.fullmatch(pattern, segment)
            if match:
                target = self._resolve_reference(_target(match.group(1)), "window")
                return ToolCall(tool, {"window": target}) if target else None
        return None

    def _resolve_reference(self, target: str, kind: str) -> str:
        if target in _PRONOUNS:
            return self.session.reference(kind)
        return target


def _split_steps(text: str) -> list[str]:
    parts = re.split(
        r"\s*(?:;|,\s*(?=(?:открой|запусти|закрой|найди|сверни|разверни|напиши|введи))|"
        r"\s+(?:а\s+)?(?:потом|затем|после этого)\s+|"
        r"\s+и\s+(?=(?:открой|запусти|закрой|найди|сверни|разверни|напиши|введи|набери)))\s*",
        re.sub(r"\s+", " ", text.strip()), flags=re.I,
    )
    return [part.strip() for part in parts if part.strip()]


def _volume_call(text: str) -> ToolCall | None:
    if re.fullmatch(r"(?:выключи звук|без звука|замьюти|mute)", text):
        return ToolCall("mute_volume", {})
    if re.fullmatch(r"(?:включи звук|размьюти|unmute)", text):
        return ToolCall("unmute_volume", {})
    match = re.fullmatch(
        r"(?:(?:сделай|поставь)\s+)?(?:звук|громкость)\s+(?:на\s+)?(\d{1,3})"
        r"(?:\s*процент\w*|\s*%)?", text,
    )
    if match and 0 <= int(match.group(1)) <= 100:
        return ToolCall("set_volume", {"percent": int(match.group(1))})
    if re.fullmatch(r"(?:(?:сделай|поставь)\s+)?(?:потише|тише)|уменьши\s+(?:звук|громкость)", text):
        return ToolCall("change_volume", {"delta": -10})
    if re.fullmatch(r"(?:(?:сделай|поставь)\s+)?(?:погромче|громче)|увеличь\s+(?:звук|громкость)", text):
        return ToolCall("change_volume", {"delta": 10})
    return None


def _normalise_keys(value: str) -> list[str]:
    aliases = {"контрол": "ctrl", "шифт": "shift", "альт": "alt", "вин": "win",
               "эскейп": "escape", "пробел": "space"}
    parts = re.split(r"\s*(?:\+| плюс )\s*", value.casefold())
    keys = [aliases.get(part.strip(), part.strip()) for part in parts if part.strip()]
    allowed = re.compile(r"^(?:ctrl|shift|alt|win|tab|escape|space|backspace|delete|home|end|"
                         r"pageup|pagedown|up|down|left|right|f(?:[1-9]|1\d|2[0-4])|[a-z0-9])$")
    return keys if 0 < len(keys) <= 5 and all(allowed.fullmatch(key) for key in keys) else []


def _clean(text: str) -> str:
    value = str(text or "").strip().lower().replace("ё", "е")
    value = re.sub(r"^(?:алиса|alice)[\s,:—-]+", "", value)
    return re.sub(r"\s+", " ", value).strip(" .!?")


def _target(value: str) -> str:
    value = value.strip().lower().replace("ё", "е")
    value = re.sub(r"^(?:приложение|программу)\s+", "", value)
    value = re.sub(r"\s+пожалуйста$", "", value)
    return value.strip(" \"'.,!?")


def _simple_app_target(value: str) -> bool:
    words = value.split()
    return (0 < len(words) <= 6
            and not re.search(r"\b(?:и|затем|потом|чтобы|после|напиши|введи|найди)\b", value)
            and bool(re.fullmatch(r"[a-zа-я0-9][a-zа-я0-9 ._+-]*", value, re.I)))
