"""Глобальная горячая клавиша (Windows): активировать голосовой ввод без слова «Алиса».

RegisterHotKey работает только в потоке с очередью сообщений, поэтому держим свой
маленький поток с GetMessage-циклом. Qt-шные QShortcut тут не годятся: они срабатывают,
только когда окно приложения в фокусе, а нам нужно ровно наоборот.
"""
import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

IS_WINDOWS = hasattr(ctypes, "windll")
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 1

MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
             "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN}
NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "backspace": 0x08,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}


def parse(combo: str):
    """"Ctrl+Alt+A" или просто "F8" -> (модификаторы, код клавиши). None, если разобрать не вышло.

    Одиночная клавиша разрешена: удобно вешать активацию на F-клавишу или Insert.
    Помните, что одиночная буква/цифра будет перехвачена во всей системе — для них
    лучше оставлять модификатор, а без модификатора использовать F1-F24 и подобные.
    """
    if not combo:
        return None
    mods, key = 0, None
    for part in str(combo).replace("-", "+").split("+"):
        part = part.strip().lower()
        if not part:
            continue
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif part in NAMED_KEYS:
            key = NAMED_KEYS[part]
        elif len(part) == 1 and (part.isalnum()):
            key = ord(part.upper())
        else:
            return None
    if key is None:
        return None
    return mods, key


class HotkeyThread(threading.Thread):
    """Вызывает callback при нажатии комбинации. Останавливается через stop()."""

    def __init__(self, combo, callback):
        super().__init__(name="voicetool-hotkey", daemon=True)
        self.combo = combo
        self.callback = callback
        self.error = None
        self._tid = None
        self._ready = threading.Event()

    def run(self):
        if not IS_WINDOWS:
            self.error = "Глобальные горячие клавиши поддерживаются только на Windows"
            self._ready.set()
            return
        parsed = parse(self.combo)
        if not parsed:
            self.error = f"Не удалось разобрать сочетание «{self.combo}»"
            self._ready.set()
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        mods, key = parsed
        if not user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, key):
            self.error = (f"Сочетание «{self.combo}» уже занято другой программой "
                          f"(код {ctypes.get_last_error()})")
            log.warning(self.error)
            self._ready.set()
            return
        log.info("Горячая клавиша зарегистрирована: %s", self.combo)
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:
                        log.exception("Ошибка обработчика горячей клавиши")
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            log.info("Горячая клавиша снята")

    def wait_ready(self, timeout=2.0):
        self._ready.wait(timeout)
        return self.error

    def stop(self):
        if IS_WINDOWS and self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
