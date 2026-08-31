"""Плавающий индикатор в левом верхнем углу экрана.

Требования, которые определили реализацию:
  * поверх всех окон, но НЕ забирает фокус — иначе Telegram потеряет курсор в поле ввода
    и вставлять текст будет некуда. Отсюда WS_EX_NOACTIVATE и Qt.WindowDoesNotAcceptFocus;
  * не ловит мышь — сквозь него можно кликать (Qt.WA_TransparentForMouseEvents);
  * плавно появляется и плавно исчезает;
  * главное окно для этого открывать не нужно.

Состояния: ожидание (маленькая точка) -> «Алиса» (круг вырос) -> запись (волны)
-> результат (короткая карточка с текстом) -> исчезновение.
"""
import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import theme
from .ui_state import AliceState, normalize_state, presentation
from .widgets import _draw_mic, plural_words

MARGIN = 24          # отступ от угла экрана
ORB = 62             # диаметр круга
CARD_W, CARD_H = 320, 122
RESULT_MS = 4200     # сколько показывать распознанный текст
ANIMATED_STATES = {"wake", "recording", "thinking", "understanding", "executing",
                   "waiting_confirmation"}


class FloatingWidget(QWidget):
    def __init__(self, reduce_motion=False):
        super().__init__(None)
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # не мешаем работать мышью
        self.setFixedSize(CARD_W + 24, CARD_H + 24)

        self._state = "idle"
        self._reduce_motion = bool(reduce_motion)
        self._text = ""
        self._words = 0
        self._level = 0.0
        self._grow = 0.0          # 0 = точка ожидания, 1 = активный круг
        self._phase = 0.0
        self._card = 0.0          # 0 = только круг, 1 = развёрнутая карточка

        self._pulse = QTimer(self)
        self._pulse.setInterval(33)
        self._pulse.timeout.connect(self._tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.fade_out)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(220)
        self._fade_hides = False   # чтобы не дёргать disconnect() вслепую
        self._grow_anim = QPropertyAnimation(self, b"grow", self)
        self._grow_anim.setDuration(260)
        self._grow_anim.setEasingCurve(QEasingCurve.OutBack)
        self._card_anim = QPropertyAnimation(self, b"card", self)
        self._card_anim.setDuration(240)
        self._card_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.move_to_corner()

    # --- анимируемые свойства ----------------------------------------------

    def get_grow(self):
        return self._grow

    def set_grow(self, v):
        self._grow = v
        self.update()

    grow = Property(float, get_grow, set_grow)

    def get_card(self):
        return self._card

    def set_card(self, v):
        self._card = v
        self.update()

    card = Property(float, get_card, set_card)

    # --- позиционирование ---------------------------------------------------

    def move_to_corner(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        self.move(QPoint(geo.left() + MARGIN, geo.top() + MARGIN))

    # --- управление состоянием ---------------------------------------------

    def show_idle(self):
        """Спокойная точка: программа слушает, но слова-триггера ещё не было."""
        if self._state in ("success", "error", "cancelled") and self._hide_timer.isActive():
            return
        self._set_state("idle", grow=0.0, card=0.0)
        self._fade_in(0.55)

    def show_wake(self):
        self._hide_timer.stop()
        self._set_state("wake", grow=1.0, card=0.0)
        self._fade_in(1.0)

    def show_recording(self):
        self._hide_timer.stop()
        self._set_state("recording", grow=1.0, card=0.0)
        self._fade_in(1.0)

    def show_thinking(self):
        self._hide_timer.stop()
        self._set_state("thinking", grow=1.0, card=0.0)
        self._fade_in(1.0)

    def show_result(self, text, words=0):
        self._text = text
        self._words = words
        self._set_state("result", grow=1.0, card=1.0)
        self._fade_in(1.0)
        self._hide_timer.start(RESULT_MS)

    def show_error(self, message):
        self._text = message
        self._words = -1
        self._set_state("result", grow=1.0, card=1.0)
        self._fade_in(1.0)
        self._hide_timer.start(RESULT_MS)

    def show_agent_state(self, state, message=""):
        """Render factual Stage 1–4 agent progress with the same visual language."""
        state = normalize_state(state)
        view = presentation(state, detail=message)
        self._text = view.detail
        self._words = 0
        self._set_state(state.value, grow=1.0, card=1.0)
        self._fade_in(1.0)
        if state in {AliceState.SUCCESS, AliceState.ERROR, AliceState.CANCELLED}:
            self._hide_timer.start(2600 if state == AliceState.SUCCESS else 4200)
        else:
            self._hide_timer.stop()

    def fade_out(self):
        """Вернуться к минимальному состоянию, если слушаем, иначе исчезнуть совсем."""
        self._hide_timer.stop()
        self._pulse.stop()
        if self._reduce_motion:
            self._card = 0.0
            self._grow = 0.0
            self._state = "idle"
            self.setWindowOpacity(0.55)
            self.update()
            return
        self._card_anim.stop()
        self._card_anim.setStartValue(self._card)
        self._card_anim.setEndValue(0.0)
        self._card_anim.start()
        self._state = "idle"
        self._grow_anim.stop()
        self._grow_anim.setStartValue(self._grow)
        self._grow_anim.setEndValue(0.0)
        self._grow_anim.start()
        self._fade_in(0.55)

    def dismiss(self):
        """Полностью убрать (микрофон выключен)."""
        self._hide_timer.stop()
        self._pulse.stop()
        if self._reduce_motion:
            self._fade.stop()
            self.hide()
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        if not self._fade_hides:
            self._fade.finished.connect(self.hide)
            self._fade_hides = True
        self._fade.start()

    def push_level(self, level: float):
        self._level = min(1.0, level * 14)

    def set_reduce_motion(self, value):
        self._reduce_motion = bool(value)
        if self._reduce_motion:
            self._pulse.stop()
            self._fade.stop()
            self._grow_anim.stop()
            self._card_anim.stop()
            self.update()

    def _set_state(self, state, grow, card):
        self._state = state
        if self._reduce_motion:
            self._pulse.stop()
            self._grow = grow
            self._card = card
            self.update()
            return
        for anim, target, current in ((self._grow_anim, grow, self._grow),
                                      (self._card_anim, card, self._card)):
            anim.stop()
            anim.setStartValue(current)
            anim.setEndValue(target)
            anim.start()
        if state in ANIMATED_STATES and not self._pulse.isActive() and not self._reduce_motion:
            self._pulse.start()
        elif state not in ANIMATED_STATES:
            self._pulse.stop()

    def _fade_in(self, opacity):
        if self._reduce_motion:
            self._fade.stop()
            self.setWindowOpacity(opacity)
            if not self.isVisible():
                self.show()
                self.raise_()
            return
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
        self._fade.stop()
        if self._fade_hides:
            self._fade.finished.disconnect(self.hide)
            self._fade_hides = False
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(opacity)
        self._fade.start()

    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        self._level *= 0.86
        self.update()

    # --- отрисовка ----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pad = 12
        size = 16 + (ORB - 16) * self._grow
        cx, cy = pad + ORB / 2, pad + ORB / 2

        if self._card > 0.01:
            self._paint_card(p, pad, cx, cy)

        active = self._state in (
            "recording", "wake", "thinking", "listening", "processing",
            "executing", "waiting_confirmation",
        )
        if self._state == "recording":
            wave = 1.0 + 0.10 * math.sin(self._phase * 2 * math.pi * 3) + self._level * 0.28
            for k in range(3):
                t = (self._phase * 1.6 + k / 3.0) % 1.0
                r = size / 2 * (1.0 + 0.85 * t)
                color = QColor(theme.ACCENT)
                color.setAlphaF(max(0.0, 0.40 * (1 - t)))
                p.setPen(QPen(color, 2))
                p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
            size *= wave
        elif self._state in ("thinking", "processing", "executing", "waiting_confirmation"):
            self._paint_spinner(p, cx, cy, size / 2 + 8)

        halo = QColor(theme.state_color(self._state))
        halo.setAlphaF(0.20 if active else 0.10)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(cx - size / 2 - 5, cy - size / 2 - 5, size + 10, size + 10))

        p.setBrush(QColor(theme.SURFACE))
        p.setPen(QPen(QColor(theme.state_color(self._state) if active else theme.BORDER), 2))
        p.drawEllipse(QRectF(cx - size / 2, cy - size / 2, size, size))
        if self._grow > 0.35:
            color = QColor(theme.state_color(self._state) if active else theme.MUTED)
            color.setAlphaF(min(1.0, (self._grow - 0.35) / 0.4))
            _draw_mic(p, self.rect().topLeft() + QPoint(int(cx), int(cy)), size * 0.26, color)

    def _paint_spinner(self, p, cx, cy, r):
        color = QColor(theme.state_color(self._state))
        color.setAlphaF(0.85)
        p.setPen(QPen(color, 2.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        start = int(self._phase * 360 * 16 * 2)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), -start, 110 * 16)

    def _paint_card(self, p, pad, cx, cy):
        alpha = self._card
        w = CARD_W * alpha
        x = cx + ORB / 2 - 6
        rect = QRectF(x, pad + 2, max(1.0, w), CARD_H)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)

        bg = QColor(theme.SURFACE)
        bg.setAlphaF(0.97 * alpha)
        p.fillPath(path, bg)
        border = QColor(theme.state_color(self._state))
        border.setAlphaF(0.45 * alpha)
        p.setPen(QPen(border, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if alpha < 0.55:
            return

        p.setClipPath(path)
        inner = rect.adjusted(16, 13, -14, -12)
        title = QFont(self.font())
        title.setPointSizeF(8.5)
        title.setBold(True)
        title.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        p.setFont(title)
        head = QColor(theme.state_color(self._state)
                      if self._state not in ("result",) else
                      (theme.ACCENT if self._words >= 0 else theme.FAIL))
        head.setAlphaF(alpha)
        p.setPen(head)
        headers = {
            "listening": "СЛУШАЮ", "processing": "ОБРАБАТЫВАЮ",
            "executing": "ВЫПОЛНЯЮ", "waiting_confirmation": "НУЖНО ПОДТВЕРЖДЕНИЕ",
            "success": "ГОТОВО", "error": "ОШИБКА", "cancelled": "ОТМЕНЕНО",
        }
        p.drawText(inner, Qt.AlignTop | Qt.AlignLeft,
                   headers.get(self._state,
                               "РАСПОЗНАНО" if self._words >= 0 else "ОШИБКА"))

        body = QFont(self.font())
        body.setPointSizeF(10.5)
        p.setFont(body)
        text_color = QColor(theme.TEXT)
        text_color.setAlphaF(alpha)
        p.setPen(text_color)
        text_rect = QRectF(inner.left(), inner.top() + 20, inner.width(), inner.height() - 42)
        p.drawText(text_rect, Qt.TextWordWrap | Qt.AlignTop | Qt.AlignLeft,
                   _elide(self._text, 150))

        if self._words > 0:
            foot = QFont(self.font())
            foot.setPointSizeF(9.0)
            p.setFont(foot)
            muted = QColor(theme.MUTED)
            muted.setAlphaF(alpha)
            p.setPen(muted)
            p.drawText(inner, Qt.AlignBottom | Qt.AlignLeft,
                       f"+{self._words} {plural_words(self._words)}")
        p.setClipping(False)

    def hideEvent(self, event):
        self._pulse.stop()
        super().hideEvent(event)


def _elide(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
