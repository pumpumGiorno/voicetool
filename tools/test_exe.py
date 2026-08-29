"""Проверка СОБРАННОГО VoiceTool.exe, а не исходников.

Запускает настоящий exe и проверяет сценарий целиком:
  запуск -> окно появилось -> «Алиса» из тестового звука -> распознавание
  -> счётчик вырос -> текст вставлен в Блокнот -> выход по трею не нужен, гасим процесс.

Звук берётся из файла (флаг --source), потому что настоящий микрофон в автотесте
воспроизвести нечем. Весь остальной путь — тот же самый код, что и с микрофоном.

    python tools/test_exe.py [dist/VoiceTool/VoiceTool.exe]
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from test_inject import (clipboard_get, clipboard_set, close_notepad, find_text_control,
                         notepad_windows, read_control, wait_for_notepad)  # noqa: E402

from voicetool import inject  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def find_window(needle):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and needle.lower() in inject.window_title(hwnd).lower():
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def kill_tree(proc):
    """Снять всё дерево процессов.

    Однофайловая сборка запускается двумя процессами: загрузчик распаковывает себя
    и порождает настоящее приложение. proc.terminate() убивает только загрузчик,
    приложение остаётся жить и через защиту «одна копия» ломает следующий запуск.
    """
    if proc.poll() is not None:
        return
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   capture_output=True, check=False)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(1.5)


def wait_for(predicate, timeout, step=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def main():
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "VoiceTool" / "VoiceTool.exe"
    if not exe.exists():
        print(f"Не найден {exe}\nСоберите: python build_exe.py")
        return 1
    sample = ROOT / "samples" / "ru_live.wav"
    data = Path(tempfile.mkdtemp(prefix="voicetool-exe-"))
    env = dict(os.environ, VOICETOOL_DATA_DIR=str(data))
    print(f"EXE:   {exe}\nЗвук:  {sample}\nДанные: {data}\n")

    # --- 1. запуск в фоне (как при автозапуске Windows) ---
    print("Запускаю VoiceTool.exe...")
    proc = subprocess.Popen([str(exe)], env=env, cwd=str(exe.parent))
    hwnd = wait_for(lambda: (find_window("voice tool") or [None])[0], timeout=60)
    check("EXE запустился и показал окно", bool(hwnd),
          inject.window_title(hwnd) if hwnd else "окно не появилось за 60 с")
    check("Процесс не упал", proc.poll() is None,
          f"код выхода {proc.poll()}" if proc.poll() is not None else "")
    if not hwnd:
        proc.terminate()
        return 1

    log_dir = data / "logs"
    check("Лог пишется в папку данных", log_dir.is_dir() and any(log_dir.glob("*.log")))
    cfg_file = data / "config.json"
    check("config.json создан в папке данных (а не рядом с exe)", cfg_file.exists())
    check("Рядом с exe нет пользовательских данных",
          not (exe.parent / "word_count.json").exists()
          and not (exe.parent / "config.json").exists())

    kill_tree(proc)

    # --- 2. полный голосовой сценарий через exe ---
    print("\nГолосовой сценарий через exe: «Алиса» -> речь -> текст в Блокноте")
    before_notepads = notepad_windows()
    notepad = subprocess.Popen(["notepad.exe"])
    target = wait_for_notepad(exclude=tuple(before_notepads))
    if not target:
        print("Блокнот не открылся — сценарий пропущен.")
        return 1
    clipboard_set("БУФЕР-ПОЛЬЗОВАТЕЛЯ")
    time.sleep(0.3)

    counter_file = data / "word_count.json"

    def total_now():
        if not counter_file.exists():
            return 0
        try:
            return json.loads(counter_file.read_text(encoding="utf-8")).get("total") or 0
        except (json.JSONDecodeError, OSError):
            return 0

    # данные CLI-версии переносятся в новую папку автоматически, поэтому смотрим на прирост,
    # а не на сам факт наличия счётчика
    baseline = total_now()
    proc = subprocess.Popen([str(exe), "--tray", "--listen", "--source", str(sample)],
                            env=env, cwd=str(exe.parent))
    print(f"  ждём распознавания (загрузка модели + разбор файла), было {baseline} слов...")

    def waiting():
        # окно консоли, из которой запущен тест, норовит забрать фокус; держим Блокнот
        # активным, иначе exe справедливо вставит текст туда, где стоял курсор
        inject.focus_window(target)
        return total_now() if total_now() > baseline else None

    total = wait_for(waiting, timeout=420, step=1.0)
    check("Счётчик слов вырос после голосовой команды", bool(total),
          f"{baseline} -> {total or total_now()}")

    history = data / "history.jsonl"
    rows = []
    if history.exists():
        rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        rows.reverse()  # новые сверху
    check("Команда записана в историю", bool(rows) and rows[0].get("kind") == "voice",
          rows[0]["text"] if rows else "")

    time.sleep(2.0)
    inject.focus_window(target)
    time.sleep(0.4)
    text = read_control(find_text_control(target)).strip()
    expected = rows[0]["text"].strip() if rows else ""
    check("Текст вставлен в Блокнот собранным exe", bool(expected) and expected in text,
          f"в Блокноте: {text[:70]!r}")
    check("Enter не нажимался", not text.endswith("\n"))

    log_text = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                         for f in sorted((data / "logs").glob("*.log")))
    check("В логе exe есть запись о наборе текста", "Набрано" in log_text,
          next((ln.split("INFO")[-1].strip() for ln in log_text.splitlines()
                if "Набрано" in ln), "")[:70])

    kill_tree(proc)
    check("Процесс завершился по запросу", proc.poll() is not None)
    leftover = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe.name}"],
                              capture_output=True, text=True, check=False).stdout
    check("Не осталось «осиротевших» процессов", exe.name not in leftover,
          "остался работающий процесс приложения")

    close_notepad(target)
    try:
        notepad.terminate()
    except OSError:
        pass
    clipboard_set("")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    print(f"\n(временные данные теста: {data})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
