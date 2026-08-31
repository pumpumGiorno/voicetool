"""Bounded Windows UI Automation inspection and actions.

Only actionable, visible controls are summarized.  Names read from applications are
explicitly marked as untrusted observations and never become user instructions.
"""
from __future__ import annotations

import ctypes
import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .types import ErrorCode, ToolResult


ACTIONABLE_TYPES = {
    "Button", "CheckBox", "ComboBox", "DataItem", "Edit", "Hyperlink",
    "ListItem", "MenuItem", "RadioButton", "Slider", "Spinner", "TabItem",
    "TreeItem",
}


@dataclass(repr=False)
class UIElement:
    name: str
    control_type: str
    automation_id: str = ""
    enabled: bool = True
    visible: bool = True
    bounds: tuple[int, int, int, int] | None = None
    _native: Any = field(default=None, repr=False, compare=False)

    def public(self) -> dict:
        body = {
            "observed_name": _bounded(self.name),
            "control_type": _bounded(self.control_type, 80),
            "automation_id": _bounded(self.automation_id, 160),
            "enabled": bool(self.enabled),
            "untrusted_observation": True,
        }
        if self.bounds:
            body["bounds"] = list(self.bounds)
        return body


class UIABackend(Protocol):
    def elements(self, window: str, scan_limit: int) -> list[UIElement]: ...
    def invoke(self, element: UIElement) -> None: ...
    def set_value(self, element: UIElement, value: str) -> None: ...


class UIAutomationController:
    def __init__(self, backend: UIABackend | None = None, *, scan_limit=600):
        self.backend = backend or PywinautoBackend()
        self.scan_limit = max(50, min(2_000, int(scan_limit)))

    def get_ui_elements(self, window="", max_elements=80) -> ToolResult:
        try:
            requested = max(1, min(100, int(max_elements)))
        except (TypeError, ValueError):
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "max_elements должен быть от 1 до 100")
        try:
            raw = self.backend.elements(str(window or ""), self.scan_limit)
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UIA_UNAVAILABLE,
                                   f"Windows UI Automation недоступна: {exc}")
        actionable = [item for item in raw if _actionable(item)]
        visible = actionable[:requested]
        return ToolResult.ok(
            f"Найдено actionable UI elements: {len(visible)}",
            data={
                "elements": [item.public() for item in visible],
                "returned": len(visible),
                "truncated": len(actionable) > requested,
                "untrusted_observation": True,
            },
        )

    def find_ui_element(self, name: str, window="", control_type="") -> ToolResult:
        found = self._find(name, window, control_type)
        if isinstance(found, ToolResult):
            return found
        return ToolResult.ok("UI element найден", data={**found.public(), "_element": found})

    def invoke_ui_element(self, name: str, window="", control_type="") -> ToolResult:
        found = self._find(name, window, control_type)
        if isinstance(found, ToolResult):
            return found
        try:
            self.backend.invoke(found)
        except NotImplementedError as exc:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, str(exc))
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UI_ACTION_FAILED,
                                   f"UIA invoke не выполнен: {exc}")
        return ToolResult.ok("UI element активирован через UI Automation",
                             data=found.public())

    def set_ui_value(self, name: str, value: str, window="", control_type="") -> ToolResult:
        if not isinstance(value, str) or len(value) > 20_000:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "UI value должна быть строкой до 20000 символов")
        found = self._find(name, window, control_type)
        if isinstance(found, ToolResult):
            return found
        try:
            self.backend.set_value(found, value)
        except NotImplementedError as exc:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, str(exc))
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UI_ACTION_FAILED,
                                   f"UIA value не установлено: {exc}")
        # The value itself is intentionally absent: activity and tool logs must not retain it.
        return ToolResult.ok("Значение установлено через UI Automation", data={
            **found.public(), "characters": len(value),
        })

    def _find(self, name, window, control_type) -> UIElement | ToolResult:
        query = _norm(name)
        wanted_type = _norm(control_type)
        if not query:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT, "UI element name пуст")
        try:
            items = self.backend.elements(str(window or ""), self.scan_limit)
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UIA_UNAVAILABLE,
                                   f"Windows UI Automation недоступна: {exc}")
        best = None
        best_score = 0.0
        for item in items:
            if not _actionable(item):
                continue
            if wanted_type and wanted_type != _norm(item.control_type):
                continue
            names = [_norm(item.name), _norm(item.automation_id)]
            score = max((_match_score(query, candidate) for candidate in names if candidate),
                        default=0.0)
            if score > best_score:
                best, best_score = item, score
        if best is None or best_score < 0.66:
            return ToolResult.fail(ErrorCode.UI_ELEMENT_NOT_FOUND,
                                   f"Actionable UI element не найден: {name}")
        return best


class PywinautoBackend:
    """Lazy pywinauto adapter; no dependency or desktop is needed for native commands."""

    def elements(self, window: str, scan_limit: int) -> list[UIElement]:
        root = self._root(window)
        result = []
        for wrapper in root.descendants()[:scan_limit]:
            try:
                info = wrapper.element_info
                rect = info.rectangle
                bounds = None
                if rect is not None:
                    bounds = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                result.append(UIElement(
                    str(info.name or ""), str(info.control_type or ""),
                    str(info.automation_id or ""), bool(info.enabled), bool(info.visible),
                    bounds, wrapper,
                ))
            except Exception:
                continue
        return result

    def invoke(self, element: UIElement) -> None:
        wrapper = element._native
        if wrapper is None:
            raise RuntimeError("UIA native element is missing")
        if hasattr(wrapper, "invoke"):
            wrapper.invoke()
            return
        if hasattr(wrapper, "select") and element.control_type in {
                "ComboBox", "ListItem", "MenuItem", "TabItem", "TreeItem"}:
            wrapper.select()
            return
        if hasattr(wrapper, "toggle") and element.control_type in {"CheckBox", "RadioButton"}:
            wrapper.toggle()
            return
        # click_input would silently cross into coordinate automation, so do not use it here.
        raise NotImplementedError("Element does not expose an invokable UI Automation pattern")

    def set_value(self, element: UIElement, value: str) -> None:
        wrapper = element._native
        if wrapper is None:
            raise RuntimeError("UIA native element is missing")
        if hasattr(wrapper, "set_edit_text"):
            wrapper.set_edit_text(value)
            return
        if hasattr(wrapper, "set_value"):
            wrapper.set_value(value)
            return
        pattern = getattr(wrapper, "iface_value", None)
        if pattern is not None and hasattr(pattern, "SetValue"):
            pattern.SetValue(value)
            return
        raise NotImplementedError("Element does not expose the UIA Value pattern")

    @staticmethod
    def _root(window: str):
        if os.name != "nt" or not hasattr(ctypes, "windll"):
            raise RuntimeError("Windows UI Automation is supported only on Windows")
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise RuntimeError("Install pywinauto to enable Windows UI Automation") from exc
        desktop = Desktop(backend="uia")
        if window:
            return desktop.window(title_re=f"(?i).*{re.escape(window)}.*")
        hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        if not hwnd:
            raise RuntimeError("No foreground window")
        return desktop.window(handle=hwnd)


def _actionable(item: UIElement) -> bool:
    return bool(item.enabled and item.visible and item.control_type in ACTIONABLE_TYPES
                and (item.name or item.automation_id))


def _match_score(query: str, candidate: str) -> float:
    if query == candidate:
        return 1.0
    score = difflib.SequenceMatcher(None, query, candidate).ratio()
    if query in candidate or candidate in query:
        score += 0.20
    return min(1.0, score)


def _norm(value) -> str:
    return " ".join(re.sub(r"[^\wа-яё]+", " ", str(value or "").casefold()).split())


def _bounded(value, limit=300) -> str:
    text = str(value or "").replace("\x00", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
