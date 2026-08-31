"""Validated desktop-computer primitives used only below higher-level automation."""
from __future__ import annotations

import ctypes
import os
import time
from typing import Protocol

from .cancellation import CancellationToken
from .screen import IS_WINDOWS, ScreenBounds, ScreenController, _enable_dpi_awareness
from .types import ErrorCode, ToolResult


class PointerBackend(Protocol):
    def move(self, x: int, y: int) -> None: ...
    def click(self, button: str, *, double: bool = False) -> None: ...
    def drag(self, start: tuple[int, int], end: tuple[int, int], duration: float,
             token: CancellationToken) -> None: ...
    def scroll(self, delta: int) -> None: ...


class DesktopComputer:
    """Small validated surface for coordinate and input actions.

    The agent model is not given these coordinate primitives directly.  They are owned by
    the bounded visual fallback, after native tools, window tools, UIA and shortcuts fail.
    """

    def __init__(self, *, screen=None, pointer=None, input_controller=None):
        self.screen: ScreenController = screen or ScreenController()
        self.pointer: PointerBackend = pointer or (
            WindowsPointerBackend() if IS_WINDOWS else UnavailablePointerBackend())
        self.input_controller = input_controller

    def screenshot(self):
        return self.screen.take_screenshot()

    def get_screen_size(self) -> ToolResult:
        return self.screen.get_screen_size()

    def move(self, x: int, y: int) -> ToolResult:
        point = self._point(x, y)
        if isinstance(point, ToolResult):
            return point
        try:
            self.pointer.move(*point)
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, f"Не удалось переместить мышь: {exc}")
        return ToolResult.ok("Указатель перемещён", data={"x": point[0], "y": point[1]})

    def click(self, x: int, y: int, button="left") -> ToolResult:
        return self._click(x, y, button, double=False)

    def double_click(self, x: int, y: int, button="left") -> ToolResult:
        return self._click(x, y, button, double=True)

    def _click(self, x, y, button, *, double):
        if button not in {"left", "right"}:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "Поддерживаются только left и right mouse buttons")
        moved = self.move(x, y)
        if not moved.success:
            return moved
        try:
            self.pointer.click(button, double=double)
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, f"Не удалось выполнить click: {exc}")
        return ToolResult.ok(
            "Двойной click выполнен" if double else "Click выполнен",
            data={"x": int(x), "y": int(y), "button": button},
        )

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration=0.35,
             token: CancellationToken | None = None) -> ToolResult:
        start = self._point(x1, y1)
        end = self._point(x2, y2)
        if isinstance(start, ToolResult):
            return start
        if isinstance(end, ToolResult):
            return end
        if not isinstance(duration, (int, float)) or not 0.05 <= float(duration) <= 5.0:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "Drag duration должна быть от 0.05 до 5 секунд")
        token = token or CancellationToken()
        try:
            token.raise_if_cancelled()
            self.pointer.drag(start, end, float(duration), token)
            token.raise_if_cancelled()
        except Exception as exc:
            from .cancellation import AgentCancelled
            if isinstance(exc, AgentCancelled):
                raise
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, f"Не удалось выполнить drag: {exc}")
        return ToolResult.ok("Перетаскивание выполнено",
                             data={"from": list(start), "to": list(end)})

    def scroll(self, delta: int) -> ToolResult:
        if isinstance(delta, bool) or not isinstance(delta, int) or not -2400 <= delta <= 2400:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "Scroll delta должна быть целым числом от -2400 до 2400")
        if delta == 0:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT, "Scroll delta не должна быть нулевой")
        try:
            self.pointer.scroll(delta)
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, f"Не удалось выполнить scroll: {exc}")
        return ToolResult.ok("Прокрутка выполнена", data={"delta": delta})

    def type(self, text: str) -> ToolResult:
        if self.input_controller is None:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, "Keyboard backend недоступен")
        return self.input_controller.type_text(text)

    def keypress(self, keys) -> ToolResult:
        if self.input_controller is None:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, "Keyboard backend недоступен")
        if isinstance(keys, str):
            return self.input_controller.press_key(keys)
        return self.input_controller.press_keys(list(keys))

    @staticmethod
    def wait(seconds: float, token: CancellationToken) -> ToolResult:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT, "Wait duration должна быть числом")
        seconds = float(seconds)
        if not 0.05 <= seconds <= 15.0:
            return ToolResult.fail(ErrorCode.INVALID_ARGUMENT,
                                   "Wait duration должна быть от 0.05 до 15 секунд")
        if token.wait(seconds):
            token.raise_if_cancelled()
        return ToolResult.ok(f"Ожидание {seconds:g} с завершено")

    def _point(self, x, y) -> tuple[int, int] | ToolResult:
        if (isinstance(x, bool) or isinstance(y, bool)
                or not isinstance(x, int) or not isinstance(y, int)):
            return ToolResult.fail(ErrorCode.INVALID_COORDINATES,
                                   "Coordinates должны быть целыми числами")
        try:
            bounds: ScreenBounds = self.screen.bounds()
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, str(exc))
        if not bounds.contains(x, y):
            return ToolResult.fail(
                ErrorCode.INVALID_COORDINATES,
                f"Coordinates ({x}, {y}) вне virtual screen",
                data={"bounds": bounds.as_dict()},
            )
        return int(x), int(y)


class WindowsPointerBackend:
    def __init__(self):
        if not (os.name == "nt" and IS_WINDOWS):
            raise RuntimeError("Mouse automation is supported only on Windows")
        _enable_dpi_awareness()
        self.user32 = ctypes.windll.user32

    def move(self, x: int, y: int) -> None:
        if not self.user32.SetCursorPos(int(x), int(y)):
            raise OSError("SetCursorPos failed")

    def click(self, button: str, *, double=False) -> None:
        down, up = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}[button]
        for index in range(2 if double else 1):
            self.user32.mouse_event(down, 0, 0, 0, 0)
            self.user32.mouse_event(up, 0, 0, 0, 0)
            if double and index == 0:
                time.sleep(0.08)

    def drag(self, start, end, duration, token) -> None:
        self.move(*start)
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)
        try:
            steps = max(2, min(120, int(duration * 60)))
            for index in range(1, steps + 1):
                token.raise_if_cancelled()
                ratio = index / steps
                self.move(round(start[0] + (end[0] - start[0]) * ratio),
                          round(start[1] + (end[1] - start[1]) * ratio))
                if token.wait(duration / steps):
                    token.raise_if_cancelled()
        finally:
            self.user32.mouse_event(0x0004, 0, 0, 0, 0)

    def scroll(self, delta: int) -> None:
        self.user32.mouse_event(0x0800, 0, 0, int(delta), 0)


class UnavailablePointerBackend:
    """Keeps construction safe on Linux/headless systems; methods fail only when used."""

    @staticmethod
    def _fail():
        raise RuntimeError("Mouse automation is supported only on Windows")

    def move(self, x, y):
        del x, y
        self._fail()

    def click(self, button, *, double=False):
        del button, double
        self._fail()

    def drag(self, start, end, duration, token):
        del start, end, duration, token
        self._fail()

    def scroll(self, delta):
        del delta
        self._fail()
