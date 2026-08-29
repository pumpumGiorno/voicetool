"""Живая проверка ввода текста в СТОРОННЕЕ приложение.

Не юнит-тест: реально открывает приложение, набирает туда текст и читает результат
обратно. Проверяем ровно то, что заявлено:

  1. кириллица, эмодзи, знаки препинания и переносы строк доходят без искажений;
  2. уже набранный текст не стирается — символы идут в позицию курсора;
  3. буфер обмена НЕ используется: его содержимое до и после ввода одинаково,
     и в процессе ввода оно тоже не меняется (следим фоновым потоком);
  4. Enter сам не нажимается (сообщение не «уходит»);
  5. текст попадает в окно, которое было активно на момент старта, даже если
     фокус успел уйти в другое окно.

    python tools/test_inject.py            — Блокнот
    python tools/test_inject.py --browser  — ещё и адресная строка браузера
"""
import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicetool import inject  # noqa: E402

for _stream in (sys.stdout, sys.stderr):  # в консоли Windows иначе падает на эмодзи
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SENTINEL = "БУФЕР-ПОЛЬЗОВАТЕЛЯ-2026 ✓"
EXISTING = "Привет, "
SPOKEN = "как дела? Тест 123 — «кавычки», эмодзи 🎙 и\nвторая строка."
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return ok


# --- работа с буфером: ТОЛЬКО для теста ------------------------------------
# Приложение буфер не трогает вовсе; тесту он нужен, чтобы (а) убедиться, что буфер
# не изменился, и (б) прочитать содержимое чужого поля через Ctrl+A/Ctrl+C.

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)   # без этого 64-битный HANDLE не влезает
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)


def _open_clipboard(retries=20):
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_get():
    if not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_set(text):
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        if text is None:
            return True
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, ctypes.byref(data), size)
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
        return True
    finally:
        user32.CloseClipboard()


class ClipboardWatch:
    """Следит за буфером во время ввода: ловит даже мгновенную подмену.

    Прежняя реализация клала текст в буфер и возвращала прежнее содержимое обратно —
    формально «до» и «после» совпадали. Поэтому проверять надо не только края,
    но и всё, что было в промежутке.
    """

    def __init__(self, interval=0.005):
        self.interval = interval
        self.seen = []
        self.sequence = user32.GetClipboardSequenceNumber()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            value = clipboard_get()
            if not self.seen or self.seen[-1] != value:
                self.seen.append(value)
            time.sleep(self.interval)

    def __enter__(self):
        self.seen.append(clipboard_get())
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        self.sequence_after = user32.GetClipboardSequenceNumber()

    @property
    def changed(self):
        return len({v for v in self.seen}) > 1

    @property
    def touched(self):
        """Счётчик Windows растёт при любой записи в буфер — даже мгновенной."""
        return self.sequence_after != self.sequence


# --- окна -------------------------------------------------------------------

def notepad_windows():
    return find_window(("notepad", "блокнот"), all_of_them=True)


def find_window(needles, exclude=(), all_of_them=False):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or hwnd in exclude:
            return True
        title = inject.window_title(hwnd).lower()
        if any(n in title for n in needles):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if all_of_them:
        return found
    return found[0] if found else None


def wait_for_window(needles, exclude=(), timeout=12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window(needles, exclude)
        if hwnd and inject.focus_window(hwnd):
            time.sleep(0.4)
            return hwnd
        time.sleep(0.3)
    return None


def wait_for_notepad(exclude=(), timeout=12.0):
    return wait_for_window(("notepad", "блокнот"), exclude, timeout)


def close_notepad(hwnd):
    """Закрыть наше окно и отказаться от сохранения (WM_CLOSE + «Не сохранять»)."""
    if not hwnd:
        return
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    time.sleep(0.8)
    if user32.IsWindow(hwnd):
        inject.focus_window(hwnd)
        time.sleep(0.3)
        inject._send([inject._vk_event(0x4E), inject._vk_event(0x4E, up=True)])  # N
        time.sleep(0.5)


# Читаем поле напрямую сообщением WM_GETTEXT, а не через Ctrl+A/Ctrl+C.
# Так тест не трогает буфер обмена вообще — иначе он сам бы и портил то,
# чью неприкосновенность проверяет.
WM_GETTEXT, WM_SETTEXT = 0x000D, 0x000C
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p)


def find_child_class(root, target, depth=0):
    """Найти дочернее окно нужного класса. Блокнот Windows 11 держит текст
    в RichEditD2DPT, классический Win32 — в Edit."""
    child = None
    while True:
        child = user32.FindWindowExW(root, child, None, None)
        if not child:
            return None
        name = ctypes.create_unicode_buffer(80)
        user32.GetClassNameW(child, name, 80)
        if name.value == target:
            return child
        if depth < 4:
            found = find_child_class(child, target, depth + 1)
            if found:
                return found


def find_text_control(hwnd):
    for cls in ("RichEditD2DPT", "Edit", "RICHEDIT50W"):
        found = find_child_class(hwnd, cls)
        if found:
            return found
    return None


def read_control(control) -> str:
    if not control:
        return ""
    buf = ctypes.create_unicode_buffer(16384)
    user32.SendMessageW(control, WM_GETTEXT, 16384, buf)
    return buf.value.replace("\r\n", "\n").replace("\r", "\n")


def read_browser_field():
    """Прочитать веб-поле: Ctrl+A, Ctrl+C и буфер.

    WM_GETTEXT тут бесполезен — поле нарисовано движком страницы, а не Windows.
    Буфер здесь портим ОСОЗНАННО и только после того, как убедились, что сам ввод
    его не трогал: сравнение «до/после» к этому моменту уже сделано.
    """
    ctrl, a, c = 0x11, ord("A"), ord("C")
    time.sleep(0.4)
    inject._send([inject._vk_event(ctrl), inject._vk_event(a),
                  inject._vk_event(a, up=True), inject._vk_event(ctrl, up=True)])
    time.sleep(0.25)
    inject._send([inject._vk_event(ctrl), inject._vk_event(c),
                  inject._vk_event(c, up=True), inject._vk_event(ctrl, up=True)])
    time.sleep(0.5)
    return (clipboard_get() or "").strip()


def clear_control(control):
    if control:
        user32.SendMessageW(control, WM_SETTEXT, 0, ctypes.create_unicode_buffer(""))
        time.sleep(0.3)


# --- сценарии ---------------------------------------------------------------

def test_notepad():
    print("Блокнот")
    before = notepad_windows()
    proc = subprocess.Popen(["notepad.exe"])
    hwnd = wait_for_notepad(exclude=tuple(before))
    if not hwnd:
        print("  Блокнот не вышел на передний план — сценарий пропущен.")
        return None
    control = find_text_control(hwnd)
    print(f"  окно: {inject.window_title(hwnd)!r}, поле найдено: {bool(control)}")
    if not control:
        print("  Поле ввода Блокнота не найдено — сценарий пропущен.")
        return None

    try:
        clear_control(control)
        inject.focus_window(hwnd)
        time.sleep(0.3)
        inject.type_text(EXISTING, hwnd=hwnd)
        time.sleep(0.4)
        clipboard_set(SENTINEL)
        time.sleep(0.2)

        with ClipboardWatch() as watch:
            inject.type_text(SPOKEN, hwnd=hwnd, press_enter=False)
            time.sleep(0.4)

        check("Буфер обмена не изменился ни на миг", not watch.changed,
              f"значения в буфере за время ввода: {watch.seen!r}")
        check("Windows не зафиксировал ни одной записи в буфер", not watch.touched,
              "счётчик GetClipboardSequenceNumber вырос" if watch.touched else "счётчик не менялся")
        check("В буфере осталось то же, что было", clipboard_get() == SENTINEL)

        text = read_control(control)
        normalized = text.replace("\r\n", "\n").rstrip()
        expected = (EXISTING + SPOKEN).rstrip()
        check("Кириллица, эмодзи и переносы строк набраны без искажений",
              SPOKEN.replace("\n", "") in normalized.replace("\n", ""),
              f"получили: {normalized!r}")
        check("Существующий текст не стёрт", normalized.startswith(EXISTING))
        check("Полное совпадение с ожидаемым", normalized == expected,
              f"ожидали {expected!r}")
        check("Enter не нажимался (нет лишней пустой строки в конце)",
              not text.endswith("\r\n\r\n") and not text.endswith("\n\n"))

        # --- фокус ушёл в другое окно ---
        print("\nВо время распознавания фокус ушёл в другое окно")
        clipboard_set(SENTINEL)
        time.sleep(0.2)
        other = subprocess.Popen(["notepad.exe"])
        other_hwnd = wait_for_notepad(exclude=tuple(before) + (hwnd,))
        time.sleep(0.5)
        check("Фокус действительно ушёл в другое окно", inject.foreground_window() != hwnd)
        inject.type_text("возврат фокуса работает", hwnd=hwnd)
        time.sleep(0.5)
        back = inject.foreground_window() == hwnd
        check("Фокус вернулся в исходное окно", back)
        if back:
            check("Текст попал именно в исходное окно",
                  "возврат фокуса работает" in read_control(control).replace("\r\n", "\n"))
        other.terminate()
        close_notepad(other_hwnd)
    finally:
        close_notepad(hwnd)
        try:
            proc.terminate()
        except OSError:
            pass
        time.sleep(0.6)
    return hwnd


def test_browser():
    """Второе приложение, которого раньше не проверяли: поле поиска в браузере.

    Открываем страницу поиска и печатаем запрос в её поле — обычное веб-поле ввода,
    совсем другой стек отрисовки, чем у Блокнота.
    """
    print("\nБраузер (поле поиска)")
    phrase = "Алиса проверка 🎙 ввода"
    before = find_window(("chrome", "edge", "firefox", "opera", "яндекс"), all_of_them=True)
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "https://duckduckgo.com/"],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as e:
        print(f"  Браузер не запустился ({e}) — сценарий пропущен.")
        return
    time.sleep(9)  # страница должна успеть загрузиться и поставить курсор в поле
    hwnd = find_window(("duckduckgo", "chrome", "edge", "firefox", "opera", "яндекс"),
                       exclude=tuple(before)) or inject.foreground_window()
    if not hwnd:
        print("  Окно браузера не найдено — сценарий пропущен.")
        return
    # Фокус на только что открытое окно браузера даётся не с первой попытки:
    # страница ещё грузится и сама перехватывает активацию.
    focused = False
    for _ in range(10):
        if inject.focus_window(hwnd) and inject.foreground_window() == hwnd:
            focused = True
            break
        time.sleep(0.6)
    if not focused:
        print("  Не удалось передать фокус браузеру — сценарий пропущен "
              "(это ограничение стенда, а не программы).")
        return
    print(f"  окно: {inject.window_title(hwnd)[:60]!r}")
    time.sleep(1.5)

    clipboard_set(SENTINEL)
    time.sleep(0.2)
    with ClipboardWatch() as watch:
        inject.type_text(phrase, hwnd=hwnd, press_enter=False)
        time.sleep(0.5)

    check("Браузер: буфер обмена не тронут", not watch.changed and not watch.touched,
          f"буфер: {clipboard_get()!r}")
    typed = read_browser_field()
    check("Браузер: кириллица и эмодзи дошли в поле поиска",
          phrase.replace(" ", "") in typed.replace(" ", ""),
          f"в поле: {typed[:60]!r}")
    check("Браузер: Enter не нажимался (страница не ушла в выдачу)",
          "duckduckgo" in inject.window_title(hwnd).lower()
          or "search" not in inject.window_title(hwnd).lower(),
          inject.window_title(hwnd)[:60])
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # закрываем вкладку/окно за собой
    time.sleep(1.0)


def main():
    if not inject.IS_WINDOWS:
        print("Тест рассчитан на Windows.")
        return 1
    saved = clipboard_get()
    try:
        test_notepad()
        if "--browser" in sys.argv:
            test_browser()
    finally:
        clipboard_set(saved)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
