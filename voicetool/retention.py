"""Small atomic retention helpers for user-data text logs."""
from __future__ import annotations

import os
import threading
from collections import deque
from pathlib import Path

_trim_counts = {}
_trim_counts_lock = threading.Lock()


def trim_lines(path, *, max_lines, max_bytes, force=False) -> bool:
    """Keep the newest complete UTF-8 lines within both configured bounds."""
    path = Path(path)
    try:
        if not path.is_file() or (not force and path.stat().st_size <= int(max_bytes)):
            return False
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            recent = deque(handle, maxlen=max(1, int(max_lines)))
    except OSError:
        return False

    budget = max(1024, int(max_bytes) * 4 // 5)
    kept = []
    used = 0
    for line in reversed(recent):
        encoded = line.encode("utf-8")
        if kept and used + len(encoded) > budget:
            break
        if not kept and len(encoded) > budget:
            encoded = encoded[-budget:]
            line = encoded.decode("utf-8", errors="ignore")
        kept.append(line if line.endswith("\n") else line + "\n")
        used += len(encoded)
    kept.reverse()
    temporary = path.with_suffix(path.suffix + ".retention.tmp")
    try:
        temporary.write_text("".join(kept), encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def append_bounded(path, line, *, max_lines, max_bytes, lock=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def write():
        with path.open("a", encoding="utf-8") as handle:
            handle.write(str(line).rstrip("\r\n") + "\n")
        key = str(path.resolve())
        interval = max(1, min(128, int(max_lines) // 10))
        with _trim_counts_lock:
            count = _trim_counts.get(key, 0) + 1
            force = count >= interval
            _trim_counts[key] = 0 if force else count
        trim_lines(path, max_lines=max_lines, max_bytes=max_bytes, force=force)

    if lock is None:
        write()
    else:
        with lock:
            write()
