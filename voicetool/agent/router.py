"""Deterministic command grammar plus conservative intent routing."""
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


_CANCEL = {"стоп", "остановись", "отмена", "отмени", "прекрати", "stop", "cancel"}
_PRONOUNS = {"его", "ее", "её", "это", "его окно", "ее окно", "её окно"}
_ACTION_OPENERS = re.compile(
    r"^(?:пожалуйста\s+)?(?:открой|запусти|закрой|сделай|поставь|уменьши|увеличь|"
    r"потише|погромче|переключись|сверни|разверни|найди|напиши|введи|выполни|"
    r"организуй|open|launch|close|set|find|type|do)\b",
    re.I,
)
_DESKTOP_OBJECT = re.compile(
    r"\b(?:приложени\w*|окн\w*|громкост\w*|звук\w*|telegram|телеграм\w*|"
    r"chrome|хром\w*|браузер\w*|файл\w*|папк\w*|desktop|windows)\b",
    re.I,
)


class VoiceCommandRouter:
    """Route by grammar/structure; the local model sees only the uncertain middle."""

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
            return RouteDecision(RouteKind.FAST_PATH, 1.0, calls, "validated command grammar")
        if mode == "agent":
            return RouteDecision(RouteKind.AGENT, 1.0, reason="agent mode")

        # Structure and desktop-object evidence are deliberately combined. A single
        # keyword in normal prose is insufficient to turn dictation into an action.
        opener = bool(_ACTION_OPENERS.search(cleaned))
        desktop_object = bool(_DESKTOP_OBJECT.search(cleaned))
        question = cleaned.startswith(("как ", "почему ", "что ", "когда ", "где "))
        if opener and desktop_object and not question:
            return RouteDecision(RouteKind.AGENT, 0.86, reason="imperative desktop request")
        if opener and not question:
            return RouteDecision(RouteKind.AMBIGUOUS, 0.55, reason="action-like but not deterministic")
        return RouteDecision(RouteKind.DICTATION, 0.92, reason="ordinary dictation")

    def _fast_plan(self, text: str) -> list[ToolCall]:
        volume = _volume_call(text)
        if volume:
            return [volume]

        match = re.fullmatch(r"(?:открой|запусти|open|launch)\s+(.+)", text)
        if match:
            target = _target(match.group(1))
            return ([ToolCall("launch_app", {"app_name": target})]
                    if _simple_app_target(target) else [])

        match = re.fullmatch(r"(?:закрой|close)\s+(.+)", text)
        if match:
            target = _target(match.group(1))
            if target in _PRONOUNS:
                target = self.session.app_reference()
            return ([ToolCall("close_app", {"app_name": target})]
                    if _simple_app_target(target) else [])
        return []


def _volume_call(text: str) -> ToolCall | None:
    if re.fullmatch(r"(?:(?:сделай|поставь)\s+)?(?:звук|громкость)\s+(?:на\s+)?\d{1,3}(?:\s*процент\w*|\s*%)?", text):
        value = int(re.search(r"\d{1,3}", text).group())
        if 0 <= value <= 100:
            return ToolCall("set_volume", {"percent": value})
        return None
    if re.fullmatch(r"(?:(?:сделай|поставь)\s+)?(?:потише|тише)|уменьши\s+(?:звук|громкость)", text):
        return ToolCall("change_volume", {"delta": -10})
    if re.fullmatch(r"(?:(?:сделай|поставь)\s+)?(?:погромче|громче)|увеличь\s+(?:звук|громкость)", text):
        return ToolCall("change_volume", {"delta": 10})
    return None


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
    return (0 < len(words) <= 5
            and not re.search(r"\b(?:и|затем|потом|чтобы|после|напиши|введи|найди)\b", value)
            and bool(re.fullmatch(r"[a-zа-я0-9][a-zа-я0-9 ._+-]*", value, re.I)))
