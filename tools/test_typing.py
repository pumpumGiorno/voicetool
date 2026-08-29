"""Посимвольная проверка юникодного ввода с точным чтением результата.

Блокнот в Windows 11 — приложение на XAML, и достать из него текст можно только через
буфер обмена, то есть ровно тем механизмом, от которого мы отказались. Поэтому для
проверки «что именно дошло до поля» здесь поднимается собственное окно с полем ввода:
события идут через ту же системную очередь SendInput, что и в чужое приложение,
но результат читается напрямую и без искажений.

Проверка на настоящих сторонних приложениях — в tools/test_inject.py.

    python tools/test_typing.py
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from voicetool import inject  # noqa: E402

CASES = [
    ("латиница", "Hello world 123"),
    ("кириллица", "Привет, как дела?"),
    ("знаки препинания", "Кавычки «ёлочки», тире — и точка."),
    ("эмодзи", "Микрофон 🎙 и огонь 🔥"),
    ("длинная фраза", "Напомни мне завтра купить молоко, хлеб и двести грамм сыра"),
    ("перенос строки", "Первая строка\nвторая строка"),
]
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    field = QPlainTextEdit()
    field.setWindowTitle("Voice Tool — приёмник ввода")
    field.resize(700, 220)
    field.show()
    field.raise_()
    field.activateWindow()
    field.setFocus()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.03)

    hwnd = inject.foreground_window()
    print(f"Окно-приёмник: {inject.window_title(hwnd)!r}\n")

    def type_and_read(text, pre=""):
        """Печатаем из отдельного потока, а цикл событий крутим здесь.

        Так и происходит в жизни: приложение-приёмник разбирает ввод параллельно.
        Если же держать очередь событий закрытой на всё время набора, символы
        разгребаются потом одной кучей, и порядок получается не тот.
        """
        field.setPlainText(pre)
        field.moveCursor(QTextCursor.End)
        field.setFocus()
        app.processEvents()
        time.sleep(0.15)

        done = threading.Event()

        def worker():
            try:
                inject.type_text(text, hwnd=hwnd)
            finally:
                done.set()

        threading.Thread(target=worker, daemon=True).start()
        expected = pre + text
        deadline = time.time() + 15
        while time.time() < deadline:
            app.processEvents()
            if done.is_set() and field.toPlainText() == expected:
                break
            time.sleep(0.005)
        for _ in range(40):        # дать дойти хвосту событий
            app.processEvents()
            time.sleep(0.01)
        return field.toPlainText()

    for name, text in CASES:
        got = type_and_read(text)
        check(f"{name}", got == text, f"ожидали {text!r}, получили {got!r}")

    got = type_and_read("как дела?", pre="Привет, ")
    check("существующий текст сохраняется", got == "Привет, как дела?",
          f"получили {got!r}")

    long_text = "Съешь же ещё этих мягких французских булок да выпей чаю. " * 3
    got = type_and_read(long_text)
    check("длинный текст доходит целиком", got == long_text,
          f"{len(got)} из {len(long_text)} символов")

    field.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
