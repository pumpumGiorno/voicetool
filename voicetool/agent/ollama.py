"""Ollama provider using only the local native HTTP API."""
from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from .cancellation import AgentCancelled, CancellationToken


class OllamaError(RuntimeError):
    pass


@dataclass
class OllamaStatus:
    connected: bool
    server: str
    model: str
    installed: bool = False
    error: str = ""
    models: tuple[str, ...] = ()
    latency_ms: int = 0


class OllamaProvider:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base_url = validate_base_url(
            cfg.get("ollama_url", "http://127.0.0.1:11434"),
            strict=bool(cfg.get("strict_local_ai", True)),
        )
        self.model = str(cfg.get("ollama_model", "qwen3.5:9b") or "qwen3.5:9b")
        self.timeout = max(2.0, float(cfg.get("ollama_timeout_seconds", 90)))
        self._opener = urllib.request.build_opener()

    def probe(self) -> OllamaStatus:
        started = time.perf_counter()
        try:
            body = self._request("GET", "/api/tags", timeout=2.5)
            models = tuple(str(row.get("name") or row.get("model") or "")
                           for row in body.get("models", []))
            installed = _model_matches(self.model, models)
            error = "" if installed else f"Модель {self.model} не установлена"
            return OllamaStatus(True, self.base_url, self.model, installed, error, models,
                                int((time.perf_counter() - started) * 1000))
        except OllamaError as exc:
            return OllamaStatus(False, self.base_url, self.model, error=str(exc),
                                latency_ms=int((time.perf_counter() - started) * 1000))

    def classify_intent(self, command: str, token: CancellationToken) -> str:
        token.raise_if_cancelled()
        schema = {
            "type": "object",
            "properties": {"route": {"type": "string", "enum": ["action", "dictation"]}},
            "required": ["route"], "additionalProperties": False,
        }
        response = self._request("POST", "/api/chat", {
            "model": self.model, "stream": False, "think": False, "format": schema,
            "options": {"temperature": 0, "num_ctx": 1024},
            "messages": [
                {"role": "system", "content": "Classify as desktop action or ordinary dictation."},
                {"role": "user", "content": command},
            ],
        }, timeout=min(self.timeout, 30.0))
        token.raise_if_cancelled()
        try:
            route = json.loads((response.get("message") or {}).get("content") or "{}").get("route")
        except (TypeError, json.JSONDecodeError):
            route = "dictation"
        return "action" if route == "action" else "dictation"

    def chat(self, messages: list[dict], tools: list[dict], token: CancellationToken) -> dict:
        token.raise_if_cancelled()
        payload = {
            "model": self.model, "messages": messages, "tools": tools,
            "stream": True, "think": False, "keep_alive": self.cfg.get("ollama_keep_alive", "10m"),
            "options": {
                "num_ctx": int(self.cfg.get("ollama_context", 8192)),
                "temperature": float(self.cfg.get("ollama_temperature", 0.1)),
            },
        }
        return self._stream_chat(payload, token)

    def _stream_chat(self, payload: dict, token: CancellationToken) -> dict:
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        )
        combined = {"role": "assistant", "content": "", "tool_calls": []}
        try:
            response = self._opener.open(request, timeout=self.timeout)
            closer = token.add_callback(response.close)
            try:
                for raw in response:
                    token.raise_if_cancelled()
                    if not raw.strip():
                        continue
                    item = json.loads(raw.decode("utf-8"))
                    if item.get("error"):
                        raise OllamaError(str(item["error"]))
                    message = item.get("message") or {}
                    combined["content"] += str(message.get("content") or "")
                    combined["tool_calls"].extend(message.get("tool_calls") or [])
            finally:
                token.remove_callback(closer)
                response.close()
        except AgentCancelled:
            raise
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
            if token.cancelled:
                token.raise_if_cancelled()
            raise OllamaError(_network_error(exc)) from exc
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama вернула некорректный JSON") from exc
        if not combined["tool_calls"]:
            combined.pop("tool_calls")
        return {"message": combined}

    def _request(self, method, path, payload=None, *, timeout=None):
        request = urllib.request.Request(
            self.base_url + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"), method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            raise OllamaError(f"Ollama HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise OllamaError(_network_error(exc)) from exc
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama вернула некорректный JSON") from exc


def validate_base_url(value, *, strict=True) -> str:
    parsed = urlparse(str(value or "").strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("Некорректный адрес Ollama")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Адрес Ollama не должен содержать credentials или query")
    if strict and not _local_host(parsed.hostname):
        raise ValueError("Ollama разрешена только на localhost или в приватной сети")
    return f"{parsed.scheme}://{parsed.netloc}"


def _local_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        value = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host.lower().endswith(".local")
    return value.is_loopback or value.is_private or value.is_link_local


def _model_matches(requested: str, installed: tuple[str, ...]) -> bool:
    expected = requested.lower().removesuffix(":latest")
    return any(name.lower().removesuffix(":latest") == expected for name in installed)


def _network_error(exc) -> str:
    reason = getattr(exc, "reason", exc)
    return "Ollama не запущена или недоступна" if isinstance(
        reason, (ConnectionRefusedError, TimeoutError, socket.timeout, OSError)
    ) else f"Ошибка соединения с Ollama: {reason}"
