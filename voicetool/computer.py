"""Управление компьютером для голосового агента: окна, мышь, клавиатура, скриншот, запуск программ.

Всё здесь — переиспользуемые примитивы, а не обработчики под конкретные приложения:
агент комбинирует их сам («найди окно Telegram» + «кликни сюда» + «набери текст»),
поэтому механизм обобщается на любую программу.

Ввод текста делает существующий inject.type_text (посимвольный SendInput, без буфера
обмена) — здесь добавлены только мышь, нажатия «настоящих» клавиш и работа с экраном.

Скриншоты — через Pillow (ImageGrab), это единственная новая зависимость.
"""
import ctypes
import logging
import os
import shutil
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path

from . import hotkey, inject

log = logging.getLogger(__name__)

IS_WINDOWS = inject.IS_WINDOWS

# имена модификаторов -> виртуальные коды клавиш (в hotkey.MODIFIERS лежат MOD_*-флаги
# для RegisterHotKey, а SendInput нужны именно VK-коды)
MOD_VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "windows": 0x5B}

MOUSEEVENTF_MOVE_ABS = 0x0001 | 0x8000   # MOVE | ABSOLUTE
MOUSE_DOWN_UP = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
INPUT_MOUSE = 0

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = inject.user32

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class _MUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("padding", ctypes.c_byte * 32)]

    class MINPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _MUNION)]

    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.EnumWindows.argtypes = (ctypes.c_void_p, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)

    def _dpi_aware():
        """Без DPI-осведомлённости координаты скриншота и клика не совпадают при масштабе 125%+."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    _dpi_aware()


class ComputerError(RuntimeError):
    """Действие не выполнено: программа не найдена, окно не открылось и т.п."""


# --- проверка окружения -------------------------------------------------------

def check_requirements(cfg):
    """Список проблем (пустой = всё готово). Вызывается на старте режима агента."""
    problems = []
    if not IS_WINDOWS:
        problems.append("Голосовой агент управляет компьютером только на Windows.")
    try:
        import PIL  # noqa: F401
    except ImportError:
        problems.append("Не установлен Pillow (скриншоты экрана): pip install Pillow")
    if not (cfg.get("agent_llm_api_key") or os.environ.get("OPENAI_API_KEY")):
        problems.append("Не задан ключ LLM API: agent_llm_api_key в config.json "
                        "или переменная окружения OPENAI_API_KEY")
    return problems


# --- экран ---------------------------------------------------------------------

def screenshot():
    """(png_bytes, ширина, высота) основного экрана. Требует Pillow."""
    try:
        from PIL import ImageGrab
    except ImportError:
        raise ComputerError("Pillow не установлен — скриншоты недоступны (pip install Pillow)")
    import io

    image = ImageGrab.grab()
    # уменьшаем большие экраны: модели хватает 1600 px по ширине, а токенов уходит меньше
    if image.width > 1600:
        ratio = 1600 / image.width
        image = image.resize((1600, int(image.height * ratio)))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), image.width, image.height


def screen_size():
    if not IS_WINDOWS:
        return 0, 0
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


# --- мышь ------------------------------------------------------------------------

def click(x, y, button="left", double=False, screenshot_size=None):
    """Клик по координатам. Если координаты взяты со скриншота, screenshot_size=(w, h)
    пересчитает их в реальные пиксели экрана (скриншот мог быть уменьшен)."""
    if not IS_WINDOWS:
        raise ComputerError("Мышь поддерживается только на Windows")
    sw, sh = screen_size()
    if screenshot_size and screenshot_size[0]:
        x = int(x * sw / screenshot_size[0])
        y = int(y * sh / screenshot_size[1])
    x, y = max(0, min(x, sw - 1)), max(0, min(y, sh - 1))
    down, up = MOUSE_DOWN_UP.get(button, MOUSE_DOWN_UP["left"])
    nx, ny = int(x * 65535 / max(1, sw - 1)), int(y * 65535 / max(1, sh - 1))

    def event(flags):
        return MINPUT(type=INPUT_MOUSE,
                      u=_MUNION(mi=MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                                              dwFlags=MOUSEEVENTF_MOVE_ABS | flags,
                                              time=0, dwExtraInfo=None)))

    def send(*events):
        array = (MINPUT * len(events))(*events)
        if user32.SendInput(len(events), ctypes.cast(array, ctypes.c_void_p),
                            ctypes.sizeof(MINPUT)) != len(events):
            raise ComputerError("Windows отклонил событие мыши")

    send(event(0))            # сначала переместить курсор
    time.sleep(0.05)
    send(event(down), event(up))
    if double:
        time.sleep(0.08)
        send(event(down), event(up))
    log.info("Клик %s по (%d, %d)%s", button, x, y, " (двойной)" if double else "")
    return x, y


# --- клавиатура -------------------------------------------------------------------

def press_keys(combo: str):
    """Нажать сочетание («ctrl+f», «enter», «esc») как настоящие клавиши.

    Для ввода текста НЕ подходит — текст набирает inject.type_text. Это для навигации:
    Enter, Tab, Esc, Ctrl+F и т.п.
    """
    if not IS_WINDOWS:
        raise ComputerError("Клавиатура поддерживается только на Windows")
    vks = []
    for part in str(combo).replace("-", "+").split("+"):
        part = part.strip().lower()
        if not part:
            continue
        if part in MOD_VK:
            vks.append(MOD_VK[part])
        elif part in hotkey.NAMED_KEYS:
            vks.append(hotkey.NAMED_KEYS[part])
        elif len(part) == 1 and part.isalnum():
            vks.append(ord(part.upper()))
        else:
            raise ComputerError(f"Неизвестная клавиша: {part!r}")
    if not vks:
        raise ComputerError(f"Пустое сочетание клавиш: {combo!r}")
    events = [inject._vk_event(vk) for vk in vks]
    events += [inject._vk_event(vk, up=True) for vk in reversed(vks)]
    inject._send(events)
    log.info("Нажато: %s", combo)


def type_text(text: str, press_enter=False):
    """Набрать текст в активное окно (символы Юникода, без буфера обмена)."""
    inject.type_text(text, press_enter=press_enter)


# --- окна --------------------------------------------------------------------------

def list_windows():
    """[(hwnd, заголовок)] всех видимых окон с непустым заголовком."""
    if not IS_WINDOWS:
        return []
    result = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = inject.window_title(hwnd)
            if title:
                result.append((hwnd, title))
        return True

    user32.EnumWindows(callback, 0)
    return result


def focus_window_by_title(fragment: str):
    """Найти окно по части заголовка (нечётко) и сделать активным. Возвращает заголовок."""
    windows = list_windows()
    fragment_low = fragment.lower()
    best, best_score = None, 0.0
    for hwnd, title in windows:
        low = title.lower()
        score = 1.0 if fragment_low in low else SequenceMatcher(None, fragment_low, low).ratio()
        if score > best_score:
            best, best_score = (hwnd, title), score
    if not best or best_score < 0.55:
        titles = ", ".join(t for _, t in windows[:12])
        raise ComputerError(f"Окно с заголовком похожим на {fragment!r} не найдено. "
                            f"Открытые окна: {titles}")
    if not inject.focus_window(best[0]):
        raise ComputerError(f"Не удалось активировать окно {best[1]!r}")
    time.sleep(0.3)  # окну нужно время подняться на передний план
    return best[1]


# --- запуск программ -----------------------------------------------------------------

START_MENU_DIRS = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]


def _start_menu_shortcut(name: str):
    """Ярлык из меню «Пуск», лучше всего похожий на name (нечёткий поиск)."""
    name_low = name.lower()
    best, best_score = None, 0.0
    for root in START_MENU_DIRS:
        if not root.is_dir():
            continue
        for lnk in root.rglob("*.lnk"):
            stem = lnk.stem.lower()
            score = 1.0 if name_low in stem else SequenceMatcher(None, name_low, stem).ratio()
            if score > best_score:
                best, best_score = lnk, score
    return (best, best_score) if best else (None, 0.0)


def open_app(name: str) -> str:
    """Запустить программу по человеческому названию. Возвращает, что именно запущено.

    Порядок: исполняемый файл в PATH -> ярлык меню «Пуск» (нечётко) -> оболочка Windows.
    Steam-игры и сайты агент открывает отдельным действием open_url (steam://..., https://...).
    """
    if not IS_WINDOWS:
        raise ComputerError("Запуск программ поддерживается только на Windows")
    exe = shutil.which(name) or shutil.which(f"{name}.exe")
    if exe:
        subprocess.Popen([exe], close_fds=True)
        return f"запущен файл {exe}"
    lnk, score = _start_menu_shortcut(name)
    if lnk and score >= 0.6:
        os.startfile(str(lnk))
        return f"запущен ярлык «{lnk.stem}» из меню Пуск"
    try:
        os.startfile(name)  # оболочка сама знает про алиасы приложений
        return f"передано оболочке Windows: {name}"
    except OSError:
        hint = f" Похожий ярлык: «{lnk.stem}» (совпадение слабое)." if lnk else ""
        raise ComputerError(f"Программа {name!r} не найдена: нет ни в PATH, ни в меню «Пуск».{hint}")


def open_url(url: str) -> str:
    """Открыть ссылку или URI-схему (https://, steam://rungameid/570 и т.п.)."""
    if not IS_WINDOWS:
        raise ComputerError("Открытие ссылок поддерживается только на Windows")
    if "://" not in url:
        raise ComputerError(f"Это не похоже на ссылку: {url!r}")
    os.startfile(url)
    return f"открыто: {url}"
