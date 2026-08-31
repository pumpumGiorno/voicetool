"""Small short-lived context used only for useful command references."""
from __future__ import annotations

import threading
from collections import deque

from .types import ToolResult


class AgentSession:
    def __init__(self, max_turns=6):
        self._lock = threading.RLock()
        self._last_app = ""
        self._last_window = ""
        self._last_file = ""
        self._last_url = ""
        self._turns = deque(maxlen=max(1, int(max_turns)))

    def remember_tool(self, tool: str, arguments: dict, result: ToolResult):
        if not result.success:
            return
        with self._lock:
            if tool in {"launch_app", "focus_app", "close_app"}:
                app = str(result.data.get("app") or arguments.get("app_name") or "").strip()
                if app:
                    self._last_app = app
            elif tool in {"focus_window", "minimize_window", "maximize_window",
                          "restore_window", "close_window"}:
                window = str(result.data.get("title") or arguments.get("window") or "").strip()
                if window:
                    self._last_window = window
            elif tool in {"open_file", "open_folder", "find_file", "show_in_folder"}:
                path = str(result.data.get("path") or arguments.get("path") or "").strip()
                if path:
                    self._last_file = path
            elif tool == "open_url":
                self._last_url = str(result.data.get("url") or arguments.get("url") or "").strip()

    def reference(self, kind="app") -> str:
        with self._lock:
            if kind == "window":
                return self._last_window or self._last_app
            if kind == "file":
                return self._last_file
            if kind == "url":
                return self._last_url
            return self._last_app

    def app_reference(self) -> str:
        return self.reference("app")

    def add_turn(self, command: str, summary: str, success: bool):
        with self._lock:
            self._turns.append({
                "command": str(command), "summary": str(summary), "success": bool(success),
            })

    def model_context(self) -> dict:
        with self._lock:
            return {
                "last_app": self._last_app,
                "last_window": self._last_window,
                "last_file": self._last_file,
                "last_url": self._last_url,
                "recent_turns": list(self._turns),
            }
