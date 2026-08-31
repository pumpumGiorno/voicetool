"""Small short-lived context for useful app, window, file and Steam references."""
from __future__ import annotations

import threading
from collections import deque

from .types import ToolResult


class AgentSession:
    def __init__(self, max_turns=6, max_targets=12):
        self._lock = threading.RLock()
        self._last_opened_app = ""
        self._last_active_app = ""
        self._last_window = ""
        self._last_file = ""
        self._last_url = ""
        self._last_steam_game = ""
        self._tool_targets = deque(maxlen=max(1, int(max_targets)))
        self._turns = deque(maxlen=max(1, int(max_turns)))

    def remember_tool(self, tool: str, arguments: dict, result: ToolResult):
        if not result.success:
            return
        data = result.data or {}
        target = ""
        with self._lock:
            if tool == "launch_app":
                target = str(data.get("app") or arguments.get("app_name") or "").strip()
                self._last_opened_app = target or self._last_opened_app
                self._last_active_app = target or self._last_active_app
            elif tool == "focus_app":
                target = str(data.get("app") or arguments.get("app_name") or "").strip()
                self._last_active_app = target or self._last_active_app
            elif tool == "close_app":
                target = str(data.get("app") or arguments.get("app_name") or "").strip()
            elif tool in {"focus_window", "minimize_window", "maximize_window",
                          "restore_window", "close_window"}:
                target = str(data.get("title") or arguments.get("window") or "").strip()
                if target:
                    self._last_window = target
                if tool in {"focus_window", "restore_window", "maximize_window"} and target:
                    self._last_active_app = target
            elif tool in {"open_file", "open_folder", "find_file", "show_in_folder"}:
                target = str(data.get("path") or arguments.get("path")
                             or arguments.get("query") or "").strip()
                self._last_file = target or self._last_file
            elif tool == "open_url":
                target = str(data.get("url") or arguments.get("url") or "").strip()
                self._last_url = target or self._last_url
            elif tool in {"resolve_steam_game", "launch_steam_game"}:
                target = str(data.get("name") or arguments.get("game_name") or "").strip()
                self._last_steam_game = target or self._last_steam_game
                if tool == "launch_steam_game" and target:
                    self._last_opened_app = target
                    self._last_active_app = target
            elif tool == "resolve_app":
                target = str(data.get("app") or arguments.get("app_name") or "").strip()
            if target:
                self._tool_targets.append({"tool": str(tool), "target": target})

    def reference(self, kind="app") -> str:
        with self._lock:
            if kind == "window":
                return self._last_window or self._last_active_app or self._last_steam_game
            if kind == "file":
                return self._last_file
            if kind == "url":
                return self._last_url
            if kind == "steam_game":
                return self._last_steam_game
            return self._last_active_app or self._last_opened_app or self._last_steam_game

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
                "last_opened_app": self._last_opened_app,
                "last_active_app": self._last_active_app,
                "last_window": self._last_window,
                "last_file": self._last_file,
                "last_url": self._last_url,
                "last_steam_game": self._last_steam_game,
                "last_tool_targets": list(self._tool_targets),
                "recent_turns": list(self._turns),
            }
