"""Мелкие переиспользуемые виджеты и рисованная иконка приложения."""
import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .ui_state import AliceState, normalize_state


def card(*, padding=18, spacing=12, name="Card") -> tuple:
    """Карточка с вертикальной раскладкой -> (frame, layout)."""
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(spacing)
    return frame, layout


def label(text, name=None, wrap=False, size=None, color=None) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    style = []
    if size:
        style.append(f"font-size: {size}px")
    if color:
        style.append(f"color: {color}")
    if style:
        lbl.setStyleSheet("; ".join(style))
    return lbl


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.BORDER};")
    return line


def section(title: str) -> QLabel:
    return label(title.upper(), name="SectionTitle")


def spacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w


class StatCard(QFrame):
    """Крупная цифра + подпись. Используется на главной и в статистике."""

    def __init__(self, title, value="0", hint="", accent=False):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)
        self.title = label(title.upper(), name="SectionTitle")
        self.value = label(value)
        self.value.setStyleSheet(
            f"font-size: 26px; font-weight: 700; color: {theme.ACCENT if accent else theme.TEXT};")
        self.hint = label(hint, name="Dim")
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        if hint:
            lay.addWidget(self.hint)

    def set_value(self, value, hint=None):
        self.value.setText(str(value))
        if hint is not None:
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))


WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


class BarChart(QWidget):
    """Столбики «слов по дням» за неделю. Своя отрисовка — без внешних библиотек."""

    def __init__(self, days=None):
        super().__init__()
        self.days = days or []
        self.setMinimumHeight(140)

    def set_days(self, days):
        self.days = list(days)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.days:
            p.setPen(QColor(theme.DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "Пока нет данных")
            return
        top, bottom, gap = 22, 26, 10   # сверху оставляем место под подпись значения
        peak = max((n for _, n in self.days), default=0) or 1
        width = (self.width() - gap * (len(self.days) - 1)) / len(self.days)
        chart_h = self.height() - top - bottom
        font = QFont(self.font())
        font.setPointSizeF(8.0)
        p.setFont(font)
        for i, (day, count) in enumerate(self.days):
            x = i * (width + gap)
            h = max(3.0, chart_h * count / peak)
            rect = QRectF(x, top + chart_h - h, width, h)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            strong = count == peak and count > 0
            grad.setColorAt(0, QColor(theme.ACCENT if strong else "#8A4A18"))
            grad.setColorAt(1, QColor("#5A3010" if not strong else theme.ACCENT_PRESSED))
            path = QPainterPath()
            path.addRoundedRect(rect, 5, 5)
            p.fillPath(path, grad)
            p.setPen(QColor(theme.MUTED if strong else theme.DIM))
            # strftime отдаёт английские названия — берём свои, чтобы не зависеть от локали
            name = WEEKDAYS[day.weekday()] if hasattr(day, "weekday") else str(day)
            p.drawText(QRectF(x, self.height() - bottom + 4, width, 14), Qt.AlignCenter, name)
            if count:
                p.setPen(QColor(theme.TEXT if strong else theme.MUTED))
                p.drawText(QRectF(x, rect.top() - 15, width, 14), Qt.AlignCenter, str(count))


class VoiceWaveform(QWidget):
    """Полоски-эквалайзер: показывают, что микрофон реально слышит звук."""

    BARS = 24

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(34)
        self._level = 0.0
        self._history = [0.0] * self.BARS

    def push(self, level: float):
        self._level = min(1.0, level * 12)
        self._history = self._history[1:] + [self._level]
        self.update()

    def reset(self):
        self._history = [0.0] * self.BARS
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self._history)
        gap = 3
        w = max(2.0, (self.width() - gap * (n - 1)) / n)
        mid = self.height() / 2
        for i, value in enumerate(self._history):
            h = max(3.0, value * (self.height() - 6))
            rect = QRectF(i * (w + gap), mid - h / 2, w, h)
            color = QColor(theme.ACCENT if value > 0.08 else theme.SURFACE_3)
            color.setAlphaF(0.35 + 0.65 * min(1.0, value * 1.5) if value > 0.08 else 1.0)
            path = QPainterPath()
            path.addRoundedRect(rect, w / 2, w / 2)
            p.fillPath(path, color)


class AliceCore(QWidget):
    """Reusable, audio-reactive Alice state visual.

    It uses lightweight painted arcs rather than blur or particles. Ambient animation
    stops whenever the widget is hidden or reduced motion is enabled.
    """

    def __init__(self, size=118):
        super().__init__()
        self.setFixedSize(size, size)
        self._phase = 0.0
        self._state = AliceState.IDLE
        self._amplitude = 0.0
        self._reduce_motion = False
        self._anim = QPropertyAnimation(self, b"phase", self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1800)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)

    def get_phase(self):
        return self._phase

    def set_phase(self, value):
        self._phase = value
        self.update()

    phase = Property(float, get_phase, set_phase)

    def set_state(self, state, *, reduce_motion=None):
        state = normalize_state(state)
        if reduce_motion is not None:
            self._reduce_motion = bool(reduce_motion)
        if state == self._state and reduce_motion is None:
            return
        self._state = state
        animated = state in {
            AliceState.LISTENING, AliceState.PROCESSING, AliceState.EXECUTING,
            AliceState.WAITING_CONFIRMATION,
        }
        if animated and not self._reduce_motion and self.isVisible():
            self._anim.start()
        else:
            self._anim.stop()
        self.update()

    def set_active(self, active: bool):
        """Compatibility with the pre-Stage-5 home page."""
        self.set_state(AliceState.LISTENING if active else AliceState.IDLE)

    def set_amplitude(self, level: float):
        self._amplitude = max(0.0, min(1.0, float(level) * 13.0))
        if self._state == AliceState.LISTENING:
            self.update()

    def hideEvent(self, event):
        self._anim.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.set_state(self._state, reduce_motion=self._reduce_motion)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        base = min(self.width(), self.height()) / 2 - 14
        color = QColor(theme.state_color(self._state.value))
        active = self._state not in {
            AliceState.IDLE, AliceState.SUCCESS, AliceState.ERROR, AliceState.CANCELLED,
        }

        if active:
            pulse = 0.0 if self._reduce_motion else math.sin(self._phase * math.tau) * 0.03
            audio = self._amplitude * 0.07 if self._state == AliceState.LISTENING else 0.0
            for k, span in enumerate((88, 132, 64)):
                radius = base * (0.78 + k * 0.13 + pulse + audio)
                arc = QRectF(c.x() - radius, c.y() - radius, radius * 2, radius * 2)
                ink = QColor(color)
                ink.setAlphaF(0.72 - k * 0.17)
                p.setPen(QPen(ink, 2.4 - k * 0.35, Qt.SolidLine, Qt.RoundCap))
                start = int((self._phase * 360 * (1 if k != 1 else -0.65) + k * 118) * 16)
                p.drawArc(arc, -start, span * 16)

        glow = QColor(color)
        glow.setAlphaF(0.18 if active else 0.08)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, base * 0.88, base * 0.88)

        p.setBrush(QColor(theme.SURFACE_2))
        outline = QColor(color if self._state != AliceState.IDLE else theme.BORDER)
        outline.setAlphaF(0.9)
        p.setPen(QPen(outline, 1.6))
        p.drawEllipse(c, base * 0.62, base * 0.62)
        if self._state == AliceState.SUCCESS:
            _draw_check(p, c, base * 0.33, color)
        elif self._state == AliceState.ERROR:
            _draw_cross(p, c, base * 0.29, color)
        else:
            _draw_core_mark(p, c, base * 0.24,
                            color if self._state != AliceState.IDLE else QColor(theme.MUTED))


# Compatibility names used by existing pages.
LevelMeter = VoiceWaveform
MicOrb = AliceCore


class Button(QPushButton):
    """Consistent button variants: primary, secondary, ghost, danger and icon."""

    NAMES = {
        "primary": "Primary", "secondary": "Secondary", "ghost": "Ghost",
        "danger": "Danger", "icon": "IconButton",
    }

    def __init__(self, text="", *, variant="secondary", parent=None):
        super().__init__(text, parent)
        self.setObjectName(self.NAMES.get(variant, "Secondary"))
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("loading", False)

    def set_loading(self, loading: bool, text=None):
        self.setProperty("loading", bool(loading))
        self.setEnabled(not loading)
        if text is not None:
            self.setText(text)


class StatusIndicator(QFrame):
    def __init__(self, text="", state="info", parent=None):
        super().__init__(parent)
        self.setObjectName("Inner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)
        self.dot = QLabel("●")
        self.text = label(text, name="Muted")
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.set_state(state, text)

    def set_state(self, state, text=None):
        color = {"success": theme.OK, "warning": theme.WARN,
                 "danger": theme.FAIL, "accent": theme.ACCENT}.get(state, theme.DIM)
        self.dot.setStyleSheet(f"color: {color}; font-size: 9px;")
        if text is not None:
            self.text.setText(str(text))


class AgentActivityItem(QFrame):
    def __init__(self, activity, parent=None):
        super().__init__(parent)
        self.setObjectName("QuietSurface")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        row = QHBoxLayout()
        command = activity.get("command") or "Команда"
        row.addWidget(label(command, wrap=True))
        row.addStretch()
        row.addWidget(label(activity.get("time", ""), name="Dim"))
        lay.addLayout(row)
        result = label(activity.get("result") or "Без результата", name="Muted", wrap=True)
        result.setStyleSheet(
            f"color: {theme.OK if activity.get('success') else theme.FAIL}; font-size: 12px;")
        lay.addWidget(result)
        meta = " · ".join(value for value in (
            activity.get("action", ""), activity.get("target", "")) if value)
        if meta:
            lay.addWidget(label(meta, name="Dim", wrap=True))


def _draw_core_mark(p, center, size, color):
    p.save()
    p.setPen(QPen(QColor(color), max(1.8, size * 0.16), Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(center.x() - size, center.y() - size,
                     size * 2, size * 2), 35 * 16, 110 * 16)
    p.drawArc(QRectF(center.x() - size * 0.72, center.y() - size * 0.72,
                     size * 1.44, size * 1.44), 215 * 16, 110 * 16)
    p.restore()


def _draw_check(p, center, size, color):
    p.save()
    p.setPen(QPen(QColor(color), max(2.0, size * 0.18), Qt.SolidLine,
                  Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(QPointF(center.x() - size, center.y()),
               QPointF(center.x() - size * 0.24, center.y() + size * 0.72))
    p.drawLine(QPointF(center.x() - size * 0.24, center.y() + size * 0.72),
               QPointF(center.x() + size, center.y() - size * 0.72))
    p.restore()


def _draw_cross(p, center, size, color):
    p.save()
    p.setPen(QPen(QColor(color), max(2.0, size * 0.18), Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(center.x() - size, center.y() - size),
               QPointF(center.x() + size, center.y() + size))
    p.drawLine(QPointF(center.x() + size, center.y() - size),
               QPointF(center.x() - size, center.y() + size))
    p.restore()


def _draw_mic(p: QPainter, center: QPointF, size: float, color: QColor):
    """Иконка микрофона: капсула + дужка + ножка."""
    p.save()
    pen = QPen(color, max(1.6, size * 0.13), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(color)
    body = QRectF(center.x() - size * 0.30, center.y() - size * 0.85, size * 0.60, size * 1.05)
    path = QPainterPath()
    path.addRoundedRect(body, size * 0.30, size * 0.30)
    p.fillPath(path, color)
    p.setBrush(Qt.NoBrush)
    arc = QRectF(center.x() - size * 0.58, center.y() - size * 0.52, size * 1.16, size * 1.16)
    p.drawArc(arc, 180 * 16, 180 * 16)
    p.drawLine(QPointF(center.x(), center.y() + size * 0.58),
               QPointF(center.x(), center.y() + size * 0.92))
    p.restore()


def app_icon(size=256, listening=False) -> QIcon:
    """Иконка рисуется кодом: не нужен внешний .ico и он не потеряется при сборке."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    path = QPainterPath()
    path.addRoundedRect(r, size * 0.24, size * 0.24)
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0, QColor("#1A1A20"))
    grad.setColorAt(1, QColor("#0A0A0C"))
    p.fillPath(path, grad)
    p.setPen(QPen(QColor(theme.ACCENT if listening else "#3A3A45"), size * 0.025))
    p.drawPath(path)
    _draw_mic(p, QPointF(size / 2, size / 2 - size * 0.02), size * 0.30,
              QColor(theme.ACCENT if listening else "#E8E8EE"))
    p.end()
    return QIcon(pm)


def shadow(widget, blur=36, alpha=170, dy=6):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def human_time(seconds) -> str:
    seconds = int(max(0, seconds or 0))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def human_number(n) -> str:
    """12482 -> «12 482»: узкий пробел читается лучше, чем сплошная цифра."""
    return f"{int(n):,}".replace(",", " ")


def plural_words(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "слово"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "слова"
    return "слов"


def ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, t)))
