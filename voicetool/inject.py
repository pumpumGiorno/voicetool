"""Ввод распознанного текста в чужое приложение (Windows).

Текст набирается посимвольно через SendInput с флагом KEYEVENTF_UNICODE. Это не то же
самое, что «эмуляция нажатий клавиш»: в поле wScan кладётся сам код символа UTF-16,
а виртуальный код клавиши остаётся нулевым. Windows передаёт приложению готовый символ,
минуя раскладку — поэтому одинаково работают русский, английский, эмодзи и знаки
препинания, независимо от того, какая раскладка включена и есть ли вообще такая клавиша.

Буфер обмена здесь НЕ используется ни при каких обстоятельствах, в том числе как
запасной путь. Содержимое буфера пользователя не читается и не меняется.

Символы вне основной плоскости Юникода (эмодзи) занимают в UTF-16 два кода — они отправляются
двумя событиями подряд, Windows склеивает их обратно в один символ.

Перевод строки через KEYEVENTF_UNICODE не работает: приложения ждут именно клавишу.
Отправляем Shift+Enter — он переводит строку и в Блокноте, и в поле браузера, но,
в отличие от голого Enter, НЕ отправляет сообщение в мессенджерах.
"""
import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

IS_WINDOWS = hasattr(ctypes, "windll")

VK_CONTROL, VK_MENU, VK_SHIFT, VK_RETURN = 0x11, 0x12, 0x10, 0x0D
VK_LWIN = 0x5B
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
ERROR_ACCESS_DENIED = 5

KEY_NAMES = {
    "ctrl": VK_CONTROL, "control": VK_CONTROL, "alt": VK_MENU, "shift": VK_SHIFT,
    "win": VK_LWIN, "windows": VK_LWIN, "enter": VK_RETURN, "return": VK_RETURN,
    "tab": 0x09, "escape": 0x1B, "esc": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{str(i): 0x30 + i for i in range(10)},
    **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},
}

# Темп набора. Символы уходят по одному, с интервалом: приложение должно успеть
# принять и нажатие, и отпускание. Если гнать быстрее, Windows считает клавишу
# зажатой и включает автоповтор — в поле прилетает «Привет ццццццц» вместо фразы.
# 15 мс — проверенный порог для самых медленных полей (Блокнот Windows 11 на XAML;
# на 10 мс он уже теряет символы). Классический Win32-контрол держит и намного быстрее.
CHAR_PAUSE = 0.015
EMOJI_GAP = 0.035    # зазор вокруг суррогатной пары, см. type_line
FOCUS_SETTLE = 0.20  # пауза после возврата фокуса, пока окно ставит курсор в поле

BLOCKED_HINT = (
    "Windows не пропустил ввод в это окно.\n\n"
    "Так бывает с приложениями, запущенными от имени администратора: обычная программа "
    "не может отправлять в них нажатия.\n"
    "Запустите Voice Tool с теми же правами — или переключите режим вывода на "
    "«Только показывать в Voice Tool» и скопируйте текст вручную."
)

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    # argtypes объявлены не для красоты: без них ctypes считает аргумент 32-битным int
    # и обрезает HWND на 64-битной Windows
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.c_void_p)
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class InjectionError(RuntimeError):
    """Ввод не удался. Текст всё равно есть в истории и в интерфейсе."""


# --- окна -------------------------------------------------------------------

def foreground_window():
    """HWND активного окна или None. Запоминается в момент срабатывания «Алисы»."""
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    return hwnd or None


def window_title(hwnd) -> str:
    if not IS_WINDOWS or not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def focus_window(hwnd) -> bool:
    """Вернуть фокус окну, которое было активно при активации.

    Windows не даёт произвольному процессу выдёргивать фокус, поэтому на время вызова
    подключаемся к потоку активного окна (AttachThreadInput) — стандартный приём.
    """
    if not IS_WINDOWS or not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    current = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    attached = bool(target) and bool(user32.AttachThreadInput(current, target, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current, target, False)
    time.sleep(0.05)
    return user32.GetForegroundWindow() == hwnd


# --- события ввода ----------------------------------------------------------

def _unicode_event(code_unit: int, up=False):
    """Один код UTF-16 как событие ввода. wVk=0 — символ идёт мимо раскладки."""
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTUNION(ki=KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=flags,
                                             time=0, dwExtraInfo=None)))


def _vk_event(vk: int, up=False):
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=0,
                                             dwFlags=KEYEVENTF_KEYUP if up else 0,
                                             time=0, dwExtraInfo=None)))


def _send(inputs):
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        code = ctypes.get_last_error()
        if code == ERROR_ACCESS_DENIED:
            raise InjectionError(BLOCKED_HINT)
        raise InjectionError(f"Windows принял {sent} из {len(inputs)} событий ввода "
                             f"(код ошибки {code})")


def _release_modifiers():
    """Отпустить Ctrl/Alt/Shift, если пользователь держит их (например, после горячей клавиши).

    Зажатый Ctrl превратил бы набор текста в череду сочетаний клавиш.
    """
    stuck = [vk for vk in (VK_CONTROL, VK_MENU, VK_SHIFT) if user32.GetAsyncKeyState(vk) & 0x8000]
    if stuck:
        _send([_vk_event(vk, up=True) for vk in stuck])
        time.sleep(0.03)
    return stuck


def send_shift_enter():
    """Перевод строки, который не отправляет сообщение в мессенджере."""
    _send([_vk_event(VK_SHIFT), _vk_event(VK_RETURN),
           _vk_event(VK_RETURN, up=True), _vk_event(VK_SHIFT, up=True)])


def send_enter():
    _send([_vk_event(VK_RETURN), _vk_event(VK_RETURN, up=True)])


def press_keys(keys) -> bool:
    """Press a validated key/chord without interpreting text as a command."""
    if not IS_WINDOWS:
        raise InjectionError("Нажатия клавиш поддерживаются только на Windows")
    names = [str(key).strip().lower() for key in keys]
    virtual = [KEY_NAMES.get(name) for name in names]
    if not names or len(names) > 5 or any(vk is None for vk in virtual):
        raise InjectionError("Неподдерживаемая клавиша или сочетание")
    _send([_vk_event(vk) for vk in virtual]
          + [_vk_event(vk, up=True) for vk in reversed(virtual)])
    return True


def _code_units(ch: str):
    """Символ -> его коды UTF-16. Эмодзи вне BMP дают два кода (суррогатную пару)."""
    raw = ch.encode("utf-16-le", errors="surrogatepass")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def type_line(line: str, pause=CHAR_PAUSE):
    """Набрать одну строку: каждый символ — своя пара «нажатие + отпускание».

    Почему не пачкой. Отправить много событий одним SendInput заманчиво (16 мс на фразу
    вместо 500) и в классических Win32-полях это работает. Но приложения на XAML — тот же
    Блокнот Windows 11 — столько за раз не разбирают: часть символов теряется, а по
    отставшим отпусканиям включается автоповтор, и в поле приезжает «Привет цццццццц».
    Правильный текст важнее трёхсот сэкономленных миллисекунд.

    Суррогатная пара (эмодзи) — отдельный случай. Windows доставляет её быстрее, чем
    разгребается очередь обычных символов, и эмодзи обгоняет соседние буквы: вместо
    «Микрофон 🎙 и огонь 🔥» выходит «Микрофон 🎙🔥 и огонь». Поэтому вокруг пары зазор
    побольше. В диктовке эмодзи редки — на общую скорость это не влияет.
    """
    for ch in line:
        units = _code_units(ch)
        if len(units) > 1:          # эмодзи и прочие символы вне BMP
            time.sleep(EMOJI_GAP)
            _send([e for u in units
                   for e in (_unicode_event(u), _unicode_event(u, up=True))])
            time.sleep(EMOJI_GAP)
            continue
        _send([_unicode_event(units[0]), _unicode_event(units[0], up=True)])
        time.sleep(pause)


def type_unicode(text: str, pause=CHAR_PAUSE):
    """Набрать текст. Переводы строк идут как Shift+Enter."""
    for i, line in enumerate(text.split("\n")):
        if i:
            send_shift_enter()
            time.sleep(pause)
        type_line(line, pause)


# --- основное ---------------------------------------------------------------

def type_text(text, hwnd=None, press_enter=False, pause=None, **_ignored) -> bool:
    """Набрать text в активное текстовое поле. True — набрали.

    hwnd — окно, которое было активно при запуске голосового ввода. Если оно уже не
    на переднем плане (пользователь ушёл в другое приложение) — возвращаем фокус ему.
    Существующий текст поля не трогаем: символы вставляются в позицию курсора.

    Буфер обмена не используется. Если Windows не пропустит ввод — бросаем
    InjectionError с понятным текстом, а не лезем в буфер втихаря.
    """
    if not IS_WINDOWS:
        raise InjectionError("Ввод текста в чужие окна поддерживается только на Windows")
    if not text or not text.strip():
        return False

    switched = bool(hwnd) and user32.GetForegroundWindow() != hwnd
    if hwnd and not focus_window(hwnd):
        log.warning("Не удалось вернуть фокус окну %r, набираю в текущее активное",
                    window_title(hwnd))

    _release_modifiers()
    # Окну, которое только что получило фокус, нужен момент, чтобы поставить курсор
    # в поле. Без этой паузы у длинной фразы теряется начало: «Напомни мне купить
    # молоко» приезжает как «м мне купить молоко».
    time.sleep(FOCUS_SETTLE if switched else 0.02)
    started = time.perf_counter()
    type_unicode(text, pause=CHAR_PAUSE if pause is None else pause)
    if press_enter:
        time.sleep(0.05)
        send_enter()
    log.info("Набрано %d символов за %.2f с в окно %r (буфер обмена не задействован)",
             len(text), time.perf_counter() - started,
             window_title(hwnd) if hwnd else "активное")
    return True
