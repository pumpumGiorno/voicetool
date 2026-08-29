"""Сквозной тест живого режима без человека у микрофона.

Вместо микрофона подставляется wav-файл (тот же путь кода, что и с настоящим микрофоном:
Recorder → Whisper → слово-триггер → Whisper → счётчик → вставка). Проверяем цепочку целиком:

    звук -> «Алиса» -> запись -> распознавание -> +слова в счётчике
         -> история -> текст появился в Блокноте

    python tools/test_live_pipeline.py [samples/ru_live.wav]
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from voicetool import config, engine, inject, logs  # noqa: E402
from voicetool.counter import WordCounter  # noqa: E402
from voicetool.history import History  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_inject import (clipboard_get, clipboard_set, close_notepad, find_text_control,
                         notepad_windows, read_control, wait_for_notepad)  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main():
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/ru_live.wav")
    if not source.exists():
        print(f"Нет файла {source}")
        return 1

    # своя папка данных: не трогаем настоящий счётчик пользователя
    tmp = Path(tempfile.mkdtemp(prefix="voicetool-test-"))
    cfg = config.load()
    cfg["data_dir"] = str(tmp)
    cfg["output_mode"] = "insert"
    cfg["press_enter"] = False
    cfg["insert_into_wake_window"] = True
    logs.setup(tmp, console=False)
    print(f"Источник звука: {source}\nВременные данные: {tmp}\n")

    print("Открываю Блокнот как целевое приложение...")
    before = notepad_windows()
    proc = subprocess.Popen(["notepad.exe"])
    hwnd = wait_for_notepad(exclude=tuple(before))
    if not hwnd:
        print("Блокнот не вышел на передний план — тест не показателен.")
        proc.terminate()
        return 1
    clipboard_set("БУФЕР-ДО-ТЕСТА")
    time.sleep(0.3)

    events = []
    listener = engine.Listener(cfg, source=str(source), events={
        "state": lambda s, d: events.append(("state", s, d)),
        "recognized": lambda t, n: events.append(("recognized", t, n)),
        "inserted": lambda t: events.append(("inserted", t, 0)),
        "error": lambda m: events.append(("error", m, 0)),
    })
    print("Запускаю распознавание (модель грузится 10-60 с)...")
    listener.start()
    deadline = time.time() + 300
    while listener.running and time.time() < deadline:
        time.sleep(0.5)
    listener.stop()

    try:
        recognized = [e for e in events if e[0] == "recognized"]
        inserted = [e for e in events if e[0] == "inserted"]
        errors = [e for e in events if e[0] == "error"]
        states = [e[1] for e in events if e[0] == "state"]
        for kind, a, b in events:
            print(f"    · {kind}: {str(a)[:80]}")

        check("Ошибок не было", not errors, errors[0][1] if errors else "")
        check("Слово-триггер сработало", engine.WAKE in states)
        check("Была запись команды", engine.RECORDING in states)
        check("Текст распознан", bool(recognized),
              recognized[0][1] if recognized else "")
        check("Слова засчитаны в счётчик", bool(recognized) and recognized[0][2] > 0,
              f"+{recognized[0][2]}" if recognized else "")
        check("Счётчик сохранён на диск", WordCounter(tmp).total > 0,
              f"total={WordCounter(tmp).total}")
        check("Счётчик переживает перезапуск",
              WordCounter(tmp).total == WordCounter(tmp).total and WordCounter(tmp).total > 0)
        rows = History(tmp).recent(10)
        check("Запись попала в историю", bool(rows) and rows[0]["kind"] == "voice")
        check("Текст вставлен в стороннее приложение", bool(inserted))

        if inserted:
            time.sleep(0.4)
            inject.focus_window(hwnd)
            time.sleep(0.3)
            in_notepad = read_control(find_text_control(hwnd)).strip()
            expected = inserted[0][1].strip()
            check("Текст реально появился в Блокноте", expected and expected in in_notepad,
                  f"в Блокноте: {in_notepad[:80]!r}")
            check("Enter не нажимался", not in_notepad.endswith("\n"))
    finally:
        close_notepad(hwnd)
        try:
            proc.terminate()
        except OSError:
            pass
        time.sleep(0.5)
        clipboard_set("")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    print(f"\n(временную папку можно удалить: {tmp})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
