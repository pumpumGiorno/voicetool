"""Small short-lived context used only for useful command references."""
from __future__ import annotations

import threading
from collections import deque

from .types import ToolResult


class AgentSession:
    def __init__(self, max_turns=6):
        self._lock = threading.RLock()
        self._last_app = ""
        self._turns = deque(maxlen=max(1, int(max_turns)))

    def remember_tool(self, tool: str, arguments: dict, result: ToolResult):
        if not result.success or tool not in {"launch_app", "focus_app", "close_app"}:
            return
        app = str(result.data.get("app") or arguments.get("app_name") or "").strip()
        if app:
            with self._lock:
                self._last_app = app

    def add_turn(self, command: str, summary: str, success: bool):
        with self._lock:
            self._turns.append({
                "command": str(command), "summary": str(summary), "success": bool(success),
            })

    def app_reference(self) -> str:
        with self._lock:
            return self._last_app

    def model_context(self) -> dict:
        with self._lock:
            return {"last_app": self._last_app, "recent_turns": list(self._turns)}
