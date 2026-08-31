"""Serializable values at the agent/service/tool boundaries."""
from __future__ import annotations

import json
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


class AgentStatus(str, Enum):
    DICTATION = "dictation"
    UNDERSTANDING = "understanding"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class AgentEvent:
    status: AgentStatus
    message: str
    tool: str | None = None
    success: bool | None = None


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

    @classmethod
    def ok(cls, message="Готово", *, data=None, code="ok"):
        return cls(True, code=code, message=message, data=data or {})

    @classmethod
    def fail(cls, code, message, *, data=None):
        return cls(False, code=str(code), message=str(message), data=data or {})

    def for_model(self) -> str:
        return json.dumps({
            "success": self.success,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "tool": self.tool,
        }, ensure_ascii=False, separators=(",", ":"))


@dataclass
class AgentResult:
    handled: bool
    success: bool
    status: AgentStatus
    message: str
    command: str = ""
    steps: list[ToolResult] = field(default_factory=list)
    used_model: bool = False
