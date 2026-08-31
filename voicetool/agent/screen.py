"""Private, in-memory screen capture for the local visual fallback.

Screenshots never receive a filesystem path and are kept out of ``ToolResult``'s public
payload.  The capture backend is lazy so ordinary dictation and native tools do not need
Pillow, a desktop session, or even Windows at import time.
"""
from __future__ import annotations

import ctypes
import io
import os
import time
from dataclasses import dataclass
from typing import Callable

from .types import ErrorCode, ToolResult


IS_WINDOWS = os.name == "nt" and hasattr(ctypes, "windll")


@dataclass(frozen=True)
class ScreenBounds:
    left: int
    top: int
    width: int
    height: int
    dpi_scale: float = 1.0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_dict(self) -> dict:
        return {
            "left": self.left, "top": self.top, "width": self.width,
            "height": self.height, "right": self.right, "bottom": self.bottom,
            "dpi_scale": self.dpi_scale,
        }


@dataclass(frozen=True, repr=False)
class ScreenshotFrame:
    """Sensitive pixels held only in memory.

    ``repr`` deliberately omits the bytes so accidental debug logging cannot dump a
    screenshot.  Callers that need pixels must explicitly access ``png``.
    """

    png: bytes
    bounds: ScreenBounds
    width: int
    height: int
    captured_at: float

    def __repr__(self) -> str:
        return (f"ScreenshotFrame(<private>, {self.width}x{self.height}, "
                f"bounds={self.bounds.as_dict()!r})")


class ScreenController:
    def __init__(
        self,
        *,
        bounds_provider: Callable[[], ScreenBounds] | None = None,
        grabber: Callable[[ScreenBounds], object] | None = None,
        is_windows: bool | None = None,
    ):
        self._bounds_provider = bounds_provider or _windows_bounds
        self._grabber = grabber or _pillow_grab
        self._is_windows = IS_WINDOWS if is_windows is None else bool(is_windows)

    def bounds(self) -> ScreenBounds:
        if not self._is_windows:
            raise RuntimeError("Screen capture is supported only on Windows")
        bounds = self._bounds_provider()
        if bounds.width <= 0 or bounds.height <= 0:
            raise RuntimeError("Windows returned invalid virtual-screen bounds")
        return bounds

    def get_screen_size(self) -> ToolResult:
        try:
            bounds = self.bounds()
        except Exception as exc:
            return ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, str(exc))
        return ToolResult.ok("Границы виртуального рабочего стола получены",
                             data=bounds.as_dict())

    def capture(self) -> ScreenshotFrame:
        bounds = self.bounds()
        captured = self._grabber(bounds)
        if isinstance(captured, ScreenshotFrame):
            return captured
        if (isinstance(captured, tuple) and len(captured) == 3
                and isinstance(captured[0], (bytes, bytearray))):
            png, width, height = captured
        else:
            width, height = captured.size
            buffer = io.BytesIO()
            captured.save(buffer, format="PNG", optimize=True)
            png = buffer.getvalue()
        if not png or int(width) <= 0 or int(height) <= 0:
            raise RuntimeError("Screen capture returned an empty image")
        return ScreenshotFrame(bytes(png), bounds, int(width), int(height), time.time())

    def take_screenshot(self) -> ToolResult:
        """Capture pixels in memory; the frame is private and never serialized."""
        try:
            frame = self.capture()
        except Exception as exc:
            return ToolResult.fail(ErrorCode.SCREENSHOT_FAILED,
                                   f"Не удалось сделать локальный снимок: {exc}")
        return ToolResult.ok(
            "Локальный снимок экрана сделан",
            data={
                "width": frame.width,
                "height": frame.height,
                "bounds": frame.bounds.as_dict(),
                "stored_on_disk": False,
                "_screenshot": frame,
            },
        )


def _windows_bounds() -> ScreenBounds:
    if not IS_WINDOWS:
        raise RuntimeError("Screen metrics are supported only on Windows")
    user32 = ctypes.windll.user32
    _enable_dpi_awareness()
    left = int(user32.GetSystemMetrics(76))
    top = int(user32.GetSystemMetrics(77))
    width = int(user32.GetSystemMetrics(78))
    height = int(user32.GetSystemMetrics(79))
    scale = 1.0
    try:
        dpi = int(user32.GetDpiForSystem())
        if dpi > 0:
            scale = dpi / 96.0
    except (AttributeError, OSError):
        pass
    return ScreenBounds(left, top, width, height, scale)


def _enable_dpi_awareness() -> None:
    """Use physical virtual-screen coordinates on mixed-DPI desktops when available."""
    if not IS_WINDOWS:
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def _pillow_grab(_bounds: ScreenBounds):
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError("Для локальных screenshots установите Pillow") from exc
    return ImageGrab.grab(all_screens=True)
