"""Structured local AgentActivity log with conservative redaction."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..retention import append_bounded, trim_lines
from .safety import safe_arguments, safe_text
from .types import ToolResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentActivity:
    timestamp: str
    user_command: str
    tool: str
    safe_arguments: dict
    result: dict
    duration_ms: int
    confirmation: str
    error: str


MAX_ACTIVITY_LINES = 5_000
MAX_ACTIVITY_BYTES = 5 * 1024 * 1024


class ActivityLogger:
    def __init__(self, data_dir: Path, *, include_command=True, enabled=True,
                 max_lines=MAX_ACTIVITY_LINES, max_bytes=MAX_ACTIVITY_BYTES):
        self.path = Path(data_dir) / "agent_activity.jsonl"
        self.include_command = bool(include_command)
        self.enabled = bool(enabled)
        self.max_lines = max(1, int(max_lines))
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.Lock()
        trim_lines(self.path, max_lines=self.max_lines, max_bytes=self.max_bytes, force=True)

    def write(self, command, tool, arguments, result: ToolResult, *, confirmation="not_required"):
        if not self.enabled:
            return None
        visible_command = (safe_text(command) if self.include_command
                           else "[redacted: transcript logging disabled]")
        activity = AgentActivity(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            user_command=visible_command,
            tool=str(tool or ""),
            safe_arguments=safe_arguments(arguments),
            result={
                "success": bool(result.success),
                "code": str(result.code),
                "message": safe_text(result.message),
            },
            duration_ms=max(0, int(result.duration_ms or 0)),
            confirmation=str(confirmation),
            error="" if result.success else safe_text(result.message),
        )
        try:
            line = json.dumps(asdict(activity), ensure_ascii=False, separators=(",", ":"))
            append_bounded(
                self.path, line, max_lines=self.max_lines,
                max_bytes=self.max_bytes, lock=self._lock,
            )
            return activity
        except OSError as exc:
            log.error("Не удалось записать AgentActivity: %s", exc)
            return None
