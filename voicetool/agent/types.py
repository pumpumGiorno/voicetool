"""Serializable values at the agent/service/tool boundaries."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode:
    """Stable machine-readable errors returned by native Stage 2 tools."""

    APP_NOT_FOUND = "APP_NOT_FOUND"
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    STEAM_NOT_FOUND = "STEAM_NOT_FOUND"
    STEAM_GAME_NOT_FOUND = "STEAM_GAME_NOT_FOUND"
    STEAM_LAUNCH_FAILED = "STEAM_LAUNCH_FAILED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFIRMATION_REJECTED = "CONFIRMATION_REJECTED"
    CANCELLED = "CANCELLED"


class AgentStatus(str, Enum):
    DICTATION = "dictation"
    UNDERSTANDING = "understanding"
    EXECUTING = "executing"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class AgentEvent:
    status: AgentStatus
    message: str
    tool: str | None = None
    success: bool | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass
class ToolResult:
    success: bool
    code: str = "ok"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    tool: str = ""
    duration_ms: int = 0

    @classmethod
    def ok(cls, message="Готово", *, data=None, code="ok"):
        return cls(True, code=code, message=message, data=data or {})

    @classmethod
    def fail(cls, code, message, *, data=None):
        return cls(False, code=str(code), message=str(message), data=data or {})

    def as_dict(self) -> dict[str, Any]:
        data = {
            key: value for key, value in self.data.items()
            if not str(key).startswith("_") and key not in {
                "clipboard", "image", "image_base64",
            }
        }
        return {
            "success": self.success,
            "code": self.code,
            "message": self.message,
            "data": data,
            "tool": self.tool,
            "duration_ms": self.duration_ms,
        }

    def for_model(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass
class AgentResult:
    handled: bool
    success: bool
    status: AgentStatus
    message: str
    command: str = ""
    steps: list[ToolResult] = field(default_factory=list)
    used_model: bool = False


class ConfirmationRequest:
    """One high-impact tool approval, asked immediately before execution."""

    def __init__(self, tool, arguments, description, *, risk="high"):
        self.tool = str(tool)
        self.arguments = dict(arguments or {})
        self.description = str(description)
        self.risk = str(risk)
        self.created_at = time.time()
        self._event = threading.Event()
        self._approved: bool | None = None
        self._lock = threading.Lock()

    @property
    def approved(self) -> bool | None:
        return self._approved

    def resolve(self, approved: bool) -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._approved = bool(approved)
            self._event.set()
            return True

    def wait(self, token, timeout=120.0) -> bool:
        deadline = time.monotonic() + max(0.1, float(timeout))
        while not self._event.wait(0.05):
            token.raise_if_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("Время ожидания подтверждения истекло")
        token.raise_if_cancelled()
        return bool(self._approved)
