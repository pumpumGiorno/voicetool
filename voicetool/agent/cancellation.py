"""Cooperative cancellation shared by provider requests and typed tools."""
from __future__ import annotations

import threading
from collections.abc import Callable


class AgentCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()
        self._reason = ""
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason or "Отменено пользователем"

    def cancel(self, reason="Отменено пользователем"):
        callbacks = []
        with self._lock:
            if self._event.is_set():
                return
            self._reason = str(reason or "Отменено пользователем")
            self._event.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def add_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        run_now = False
        with self._lock:
            if self._event.is_set():
                run_now = True
            else:
                self._callbacks.append(callback)
        if run_now:
            callback()
        return callback

    def remove_callback(self, callback: Callable[[], None]):
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def wait(self, timeout=None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self):
        if self.cancelled:
            raise AgentCancelled(self.reason)
