"""Single coordinator for routing, local inference and typed desktop tools."""
from __future__ import annotations

import json
import logging
import threading

from .cancellation import AgentCancelled, CancellationToken
from .desktop import DesktopController
from .ollama import OllamaError, OllamaProvider
from .provider import LocalModelProvider
from .router import RouteKind, VoiceCommandRouter
from .session import AgentSession
from .tools import ToolRegistry
from .types import AgentEvent, AgentResult, AgentStatus, ToolCall

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are Alice, a local Windows desktop agent. Use only the provided
typed tools. Never produce shell, PowerShell, cmd, scripts or operating-system commands.
Observe structured tool results and do not claim success after a failed tool. Use the
short-term context only to resolve references. Keep the response concise."""


class DesktopAgentService:
    def __init__(self, cfg, *, events=None, automation=None,
                 provider: LocalModelProvider | None = None):
        self.cfg = cfg
        self.events = events or {}
        self.session = AgentSession()
        self.automation = automation or DesktopController(cfg)
        self.provider = provider or OllamaProvider(cfg)
        self.registry = ToolRegistry(self.automation)
        self.router = VoiceCommandRouter(cfg, self.session)
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_token: CancellationToken | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._active_token is not None

    def diagnostics(self):
        """Safe for startup/settings checks: an absent server becomes status, not an exception."""
        return self.provider.probe()

    def cancel(self, reason="Остановлено пользователем") -> bool:
        with self._state_lock:
            token = self._active_token
        if token is None:
            return False
        token.cancel(reason)
        return True

    def execute(self, command: str, *, source="voice") -> AgentResult:
        del source  # reserved for future callers; business logic remains in this service
        command = str(command or "").strip()
        if not command:
            return AgentResult(False, False, AgentStatus.DICTATION, "Пустая команда")
        if not self._run_lock.acquire(blocking=False):
            return AgentResult(True, False, AgentStatus.ERROR,
                               "Alice уже выполняет другую задачу", command=command)
        token = CancellationToken()
        with self._state_lock:
            self._active_token = token
        try:
            return self._execute(command, token)
        except AgentCancelled as exc:
            message = str(exc) or "Отменено"
            self.session.add_turn(command, message, False)
            self._event(AgentStatus.CANCELLED, message, success=False)
            return AgentResult(True, False, AgentStatus.CANCELLED, message, command=command)
        except Exception as exc:
            log.exception("Desktop Agent failed")
            message = f"Не удалось выполнить команду: {exc}"
            self.session.add_turn(command, message, False)
            self._event(AgentStatus.ERROR, message, success=False)
            return AgentResult(True, False, AgentStatus.ERROR, message, command=command)
        finally:
            with self._state_lock:
                self._active_token = None
            self._run_lock.release()

    def _execute(self, command: str, token: CancellationToken) -> AgentResult:
        decision = self.router.route(command)
        if decision.kind == RouteKind.DICTATION:
            return AgentResult(False, True, AgentStatus.DICTATION, "Диктовка", command=command)
        if decision.kind == RouteKind.CANCEL:
            return AgentResult(True, True, AgentStatus.CANCELLED, "Отменено", command=command)

        self._event(AgentStatus.UNDERSTANDING, "Понимаю запрос…")
        if decision.kind == RouteKind.FAST_PATH:
            return self._execute_calls(command, decision.calls, token, used_model=False)

        status = self.provider.probe()
        if not status.connected:
            if decision.kind == RouteKind.AMBIGUOUS:
                return AgentResult(False, True, AgentStatus.DICTATION,
                                   "Неоднозначная фраза оставлена в диктовке", command=command)
            return self._provider_error(command, status.error or "Ollama недоступна")
        if not status.installed:
            return self._provider_error(
                command, status.error or f"Модель {self.provider.model} не установлена")

        if decision.kind == RouteKind.AMBIGUOUS:
            try:
                route = self.provider.classify_intent(command, token)
            except OllamaError as exc:
                return self._provider_error(command, str(exc))
            if route != "action":
                return AgentResult(False, True, AgentStatus.DICTATION, "Диктовка", command=command)
        return self._execute_model(command, token)

    def _execute_calls(self, command, calls, token, *, used_model):
        steps = []
        for call in calls:
            token.raise_if_cancelled()
            self._event(AgentStatus.EXECUTING, f"Выполняю {call.name}…", tool=call.name)
            result = self.registry.execute(call, token)
            steps.append(result)
            self.session.remember_tool(call.name, call.arguments, result)
            if not result.success:
                self.session.add_turn(command, result.message, False)
                self._event(AgentStatus.ERROR, result.message, tool=call.name, success=False)
                return AgentResult(True, False, AgentStatus.ERROR, result.message,
                                   command=command, steps=steps, used_model=used_model)
        message = steps[-1].message if steps else "Готово"
        self.session.add_turn(command, message, True)
        self._event(AgentStatus.SUCCESS, message, success=True)
        return AgentResult(True, True, AgentStatus.SUCCESS, message,
                           command=command, steps=steps, used_model=used_model)

    def _execute_model(self, command: str, token: CancellationToken) -> AgentResult:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "system", "content": "Short context JSON: " + json.dumps(
                self.session.model_context(), ensure_ascii=False)},
            {"role": "user", "content": command},
        ]
        steps = []
        max_steps = max(1, min(32, int(self.cfg.get("max_agent_steps", 8))))
        for _ in range(max_steps):
            token.raise_if_cancelled()
            try:
                response = self.provider.chat(messages, self.registry.schemas(), token)
            except OllamaError as exc:
                return self._provider_error(command, str(exc), steps=steps, used_model=True)
            assistant = dict(response.get("message") or {})
            calls = _parse_tool_calls(assistant.get("tool_calls") or [])
            messages.append(assistant or {"role": "assistant", "content": ""})
            if not calls:
                content = str(assistant.get("content") or "").strip()
                success = bool(steps) and steps[-1].success
                message = content or (steps[-1].message if steps else "Alice не выбрала действие")
                status = AgentStatus.SUCCESS if success else AgentStatus.ERROR
                self.session.add_turn(command, message, success)
                self._event(status, message, success=success)
                return AgentResult(True, success, status, message, command=command,
                                   steps=steps, used_model=True)
            for call in calls:
                token.raise_if_cancelled()
                if len(steps) >= max_steps:
                    return self._step_limit(command, max_steps, steps)
                self._event(AgentStatus.EXECUTING, f"Выполняю {call.name}…", tool=call.name)
                result = self.registry.execute(call, token)
                steps.append(result)
                self.session.remember_tool(call.name, call.arguments, result)
                messages.append({"role": "tool", "tool_name": call.name,
                                 "content": result.for_model()})

        return self._step_limit(command, max_steps, steps)

    def _step_limit(self, command, max_steps, steps):
        message = f"Задача остановлена: достигнут лимит {max_steps} шагов"
        self.session.add_turn(command, message, False)
        self._event(AgentStatus.ERROR, message, success=False)
        return AgentResult(True, False, AgentStatus.ERROR, message,
                           command=command, steps=steps, used_model=True)

    def _provider_error(self, command, message, *, steps=None, used_model=False):
        self.session.add_turn(command, message, False)
        self._event(AgentStatus.ERROR, message, success=False)
        return AgentResult(True, False, AgentStatus.ERROR, message, command=command,
                           steps=steps or [], used_model=used_model)

    def _event(self, status, message, *, tool=None, success=None):
        callback = self.events.get("event")
        if callback:
            try:
                callback(AgentEvent(status, str(message), tool=tool, success=success))
            except Exception:
                log.exception("Desktop Agent event callback failed")


def _parse_tool_calls(raw_calls) -> list[ToolCall]:
    calls = []
    for raw in raw_calls:
        function = raw.get("function") or {}
        name = str(function.get("name") or "")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if name:
            calls.append(ToolCall(name, arguments if isinstance(arguments, dict) else {},
                                  str(raw.get("id") or "")))
    return calls
