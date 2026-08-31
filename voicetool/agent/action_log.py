"""Structured local AgentActivity log with conservative redaction."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

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


class ActivityLogger:
    def __init__(self, data_dir: Path, *, include_command=True):
        self.path = Path(data_dir) / "agent_activity.jsonl"
        self.include_command = bool(include_command)
        self._lock = threading.Lock()

    def write(self, command, tool, arguments, result: ToolResult, *, confirmation="not_required"):
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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(activity), ensure_ascii=False, separators=(",", ":"))
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            log.error("Не удалось записать AgentActivity: %s", exc)
