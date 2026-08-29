"""Мелкие переиспользуемые виджеты и рисованная иконка приложения."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Property, QPropertyAnimation
from PySide6.QtGui import (QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme


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


class LevelMeter(QWidget):
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


class MicOrb(QWidget):
    """Круглый индикатор состояния на главном экране: пульсирует, когда идёт запись."""

    def __init__(self, size=118):
        super().__init__()
        self.setFixedSize(size, size)
        self._phase = 0.0
        self._active = False
        self._anim = QPropertyAnimation(self, b"phase", self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1600)
        self._anim.setLoopCount(-1)

    def get_phase(self):
        return self._phase

    def set_phase(self, value):
        self._phase = value
        self.update()

    phase = Property(float, get_phase, set_phase)

    def set_active(self, active: bool):
        if active == self._active:
            return
        self._active = active
        self._anim.start() if active else self._anim.stop()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        base = min(self.width(), self.height()) / 2 - 12

        if self._active:
            for k in range(3):
                t = (self._phase + k / 3.0) % 1.0
                r = base * (0.72 + 0.5 * t)
                color = QColor(theme.ACCENT)
                color.setAlphaF(max(0.0, 0.32 * (1 - t)))
                p.setPen(QPen(color, 2))
                p.drawEllipse(c, r, r)

        glow = QColor(theme.ACCENT)
        glow.setAlphaF(0.16 if self._active else 0.08)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, base * 0.92, base * 0.92)

        p.setBrush(QColor(theme.SURFACE_2))
        p.setPen(QPen(QColor(theme.ACCENT if self._active else theme.BORDER), 2))
        p.drawEllipse(c, base * 0.7, base * 0.7)
        _draw_mic(p, c, base * 0.44, QColor(theme.ACCENT if self._active else theme.MUTED))


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
