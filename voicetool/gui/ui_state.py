"""Pure Stage 5 presentation state.

This module deliberately has no Qt imports so state mappings and privacy rules can be
tested in the Linux build environment.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class AliceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    EXECUTING = "executing"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class StatePresentation:
    title: str
    detail: str
    color_key: str
    animated: bool


STATE_PRESENTATION = {
    AliceState.IDLE: StatePresentation("Готова", "Скажите «Алиса» или введите команду",
                                       "muted", False),
    AliceState.LISTENING: StatePresentation("Слушаю", "Говорите — я жду вашу команду",
                                            "accent", True),
    AliceState.PROCESSING: StatePresentation("Обрабатываю", "Распознаю и проверяю запрос",
                                             "accent", True),
    AliceState.EXECUTING: StatePresentation("Выполняю", "Работаю с вашим компьютером",
                                            "accent", True),
    AliceState.WAITING_CONFIRMATION: StatePresentation(
        "Нужно подтверждение", "Проверьте действие перед продолжением", "warning", True),
    AliceState.SUCCESS: StatePresentation("Готово", "Действие выполнено", "success", False),
    AliceState.ERROR: StatePresentation("Не получилось", "Проверьте подробности и повторите",
                                        "danger", False),
    AliceState.CANCELLED: StatePresentation("Отменено", "Действие остановлено", "muted", False),
}

_ALIASES = {
    "idle": AliceState.IDLE,
    "waiting": AliceState.IDLE,
    "paused": AliceState.IDLE,
    "done": AliceState.IDLE,
    "wake": AliceState.LISTENING,
    "recording": AliceState.LISTENING,
    "listening": AliceState.LISTENING,
    "thinking": AliceState.PROCESSING,
    "understanding": AliceState.PROCESSING,
    "processing": AliceState.PROCESSING,
    "executing": AliceState.EXECUTING,
    "waiting_confirmation": AliceState.WAITING_CONFIRMATION,
    "success": AliceState.SUCCESS,
    "error": AliceState.ERROR,
    "cancelled": AliceState.CANCELLED,
}


def normalize_state(value) -> AliceState:
    raw = getattr(value, "value", value)
    return _ALIASES.get(str(raw or "").casefold(), AliceState.IDLE)


def presentation(value, *, detail="") -> StatePresentation:
    base = STATE_PRESENTATION[normalize_state(value)]
    return StatePresentation(base.title, str(detail or base.detail),
                             base.color_key, base.animated)


class EngineHealth(str, Enum):
    RUNNING = "running"
    OFFLINE = "offline"
    MODEL_MISSING = "model_missing"
    DOWNLOADING = "downloading"
    READY = "ready"
    ERROR = "error"


def engine_health(status, *, downloading=False) -> EngineHealth:
    if downloading:
        return EngineHealth.DOWNLOADING
    if status is None:
        return EngineHealth.ERROR
    if not bool(getattr(status, "connected", False)):
        return EngineHealth.OFFLINE
    if not bool(getattr(status, "installed", False)):
        return EngineHealth.MODEL_MISSING
    return EngineHealth.READY


def concise_target(arguments: dict) -> str:
    """Human readable target only; never serialize raw tool arguments."""
    for key in ("app_name", "window", "game_name", "url", "path", "target", "name"):
        value = arguments.get(key)
        if value:
            text = " ".join(str(value).split())
            return text if len(text) <= 90 else text[:89] + "…"
    return ""


def recent_agent_activity(data_dir, limit=100) -> list[dict]:
    """Read structured activity as safe UI rows, omitting prompts and raw tool JSON."""
    path = Path(data_dir) / "agent_activity.jsonl"
    if not path.exists():
        return []
    from collections import deque

    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = deque(handle, maxlen=max(1, int(limit)))
    except OSError:
        return []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        arguments = item.get("safe_arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        rows.append({
            "time": _short_time(item.get("timestamp")),
            "command": str(item.get("user_command") or "Команда скрыта"),
            "result": str(result.get("message") or item.get("error") or "Без результата"),
            "success": bool(result.get("success")),
            "action": str(item.get("tool") or ""),
            "target": concise_target(arguments),
        })
    return rows


def _short_time(value) -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""
