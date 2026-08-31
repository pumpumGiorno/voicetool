"""Backend-neutral contract for local inference providers."""
from __future__ import annotations

from typing import Protocol

from .cancellation import CancellationToken


class LocalModelProvider(Protocol):
    model: str

    def probe(self): ...
    def classify_intent(self, command: str, token: CancellationToken) -> str: ...
    def chat(self, messages: list[dict], tools: list[dict], token: CancellationToken) -> dict: ...
