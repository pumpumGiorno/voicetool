"""Проверка обработки файлов: очередь, длинная запись, перевод, субтитры, ошибки.

Требует установленную модель Whisper — идёт минуты. Счётчик слов не трогает
(это условие задачи: файлы в счётчик не попадают), данные пишет во временную папку.

    python tools/test_files.py [минут_в_длинном_файле]
"""
import os
import shutil
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

from voicetool import config, logs  # noqa: E402
from voicetool.counter import WordCounter  # noqa: E402
from voicetool.history import History  # noqa: E402
from voicetool.media import audio_chunks, probe_duration  # noqa: E402
from voicetool.processor import DONE, FAILED, BatchProcessor  # noqa: E402

results = []
# Длина синтетической «длинной лекции». На процессоре модель small разбирает запись
# примерно за 2-4× её длительности, поэтому по умолчанию берём 12 минут: этого хватает
# на 3 куска. Больше — первым аргументом: python tools/test_files.py 24
LONG_MINUTES = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def make_long_wav(source: Path, dest: Path, minutes=LONG_MINUTES, sample_rate=16000):
    """Склеиваем короткую запись саму с собой — получаем длинный файл для проверки кусков."""
    from voicetool.media import decode_audio_file

    audio = decode_audio_file(source, sample_rate)
    repeats = int(minutes * 60 * sample_rate / len(audio)) + 1
    pcm = (np.clip(np.tile(audio, repeats), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return dest


def run_queue(cfg, paths, timeout=1800):
    events = []
    proc = BatchProcessor(cfg, events={"progress": lambda j: events.append(j.progress)})
    proc.add(paths)
    proc.start()
    deadline = time.time() + timeout
    while proc.running and time.time() < deadline:
        time.sleep(1.0)
    return proc.jobs, events


def main():
    tmp = Path(tempfile.mkdtemp(prefix="voicetool-files-"))
    cfg = config.load()
    real_data = cfg.data_dir
    cfg["data_dir"] = str(tmp)
    logs.setup(tmp, console=False)

    # модели перевода живут в папке данных; во временной их нет, и тест полез бы качать
    # 200 МБ из интернета. Если у пользователя они уже есть — переиспользуем.
    have_translate = (real_data / "models" / "translate").is_dir()
    if have_translate:
        shutil.copytree(real_data / "models" / "translate", tmp / "models" / "translate")
    else:
        print("  (моделей перевода нет — проверка перевода потребует интернета)")
    samples = ROOT / "samples"
    print(f"Временные данные: {tmp}\n")

    # --- 1. очередь: русский mp3 + английский mp4 + битые входы ---
    print("Очередь: mp3 (ru), mp4 (en+перевод), несуществующий файл, чужой формат, битый файл")
    broken = tmp / "broken.mp3"
    broken.write_bytes(b"\x00\x01\x02 not really an mp3" * 100)
    alien = tmp / "notes.txt"
    alien.write_text("это не аудио", encoding="utf-8")

    jobs, _ = run_queue(cfg, [samples / "ru_lecture.mp3", samples / "en_interview.mp4",
                              tmp / "нет-такого.mp3", alien, broken])
    by_name = {j.name: j for j in jobs}

    ru = by_name["ru_lecture.mp3"]
    check("MP3 с русской речью распознан", ru.status == DONE and ru.words > 5,
          f"{ru.language}, {ru.words} слов: {ru.text[:50]}")
    check("Русский текст не переводится", ru.status == DONE and not ru.translation)
    check("Есть сегменты с таймкодами", len(ru.segments) > 1)
    check("SRT собирается", "-->" in ru.srt() and ru.srt().startswith("1\n"))
    check("VTT собирается", ru.vtt().startswith("WEBVTT"))
    check("Расшифровка сохранена на диск", bool(ru.saved_to) and Path(ru.saved_to).exists())

    en = by_name["en_interview.mp4"]
    check("MP4 с английской речью распознан", en.status == DONE and en.language == "en",
          f"{en.language}: {en.text[:50]}")
    check("Иностранная речь переведена офлайн", bool(en.translation),
          en.translation[:60] if en.translation else "перевода нет")
    check("В отчёте есть и оригинал, и перевод",
          "Оригинал:" in en.report() and "Перевод" in en.report())

    check("Несуществующий файл: понятная ошибка",
          by_name["нет-такого.mp3"].status == FAILED
          and "не найден" in by_name["нет-такого.mp3"].error.lower(),
          by_name["нет-такого.mp3"].error)
    check("Неподдерживаемый формат отклонён",
          by_name["notes.txt"].status == FAILED and "формат" in by_name["notes.txt"].error.lower(),
          by_name["notes.txt"].error)
    check("Битый файл не роняет очередь",
          by_name["broken.mp3"].status == FAILED and bool(by_name["broken.mp3"].error),
          by_name["broken.mp3"].error.splitlines()[0][:70])

    # --- 2. счётчик слов не трогается файлами ---
    check("Файлы НЕ попали в счётчик слов", WordCounter(tmp).total == 0,
          f"в счётчике {WordCounter(tmp).total}")
    rows = History(tmp).recent(20)
    check("Файлы помечены в истории отдельно",
          bool(rows) and all(r["kind"] == "file" and r["words"] == 0 for r in rows),
          f"{len(rows)} записей")

    # --- 3. длинная запись читается кусками ---
    print(f"\nДлинная запись (~{LONG_MINUTES} мин): проверяем чтение кусками")
    long_file = make_long_wav(samples / "ru_lecture.mp3", tmp / "long_lecture.wav")
    size_mb = long_file.stat().st_size / 1e6
    duration = probe_duration(long_file)
    check("Длинный файл создан", duration > LONG_MINUTES * 60 * 0.9,
          f"{duration / 60:.1f} мин, {size_mb:.0f} МБ")

    chunk_sec = 300
    chunks = []
    peak = 0
    for offset, audio in audio_chunks(long_file, 16000, chunk_sec):
        chunks.append((offset, len(audio) / 16000))
        peak = max(peak, audio.nbytes)
    expect = max(2, int(LONG_MINUTES * 60 / chunk_sec))
    check("Файл разбит на несколько кусков", len(chunks) >= expect,
          f"{len(chunks)} кусков по ~{chunk_sec // 60} мин")
    check("Куски идут подряд без дыр",
          all(abs(chunks[i][0] - (chunks[i - 1][0] + chunks[i - 1][1])) < 0.05
              for i in range(1, len(chunks))))
    check("Сумма кусков равна длительности файла",
          abs(sum(d for _, d in chunks) - duration) < 2.0,
          f"{sum(d for _, d in chunks):.1f} с против {duration:.1f} с")
    # Смысл нарезки в том, что пик памяти определяется длиной КУСКА, а не длиной файла.
    # Поэтому сравниваем не с долей файла (на коротком файле это ничего не доказывает),
    # а с бюджетом куска: сам кусок плюс окно поиска тишины по обе стороны границы.
    from voicetool.media import SEARCH_SEC

    budget_mb = (chunk_sec + 2 * SEARCH_SEC) * 16000 * 4 / 1e6
    whole_mb = duration * 16000 * 4 / 1e6
    check("Пик памяти ограничен размером куска, а не длиной файла",
          peak / 1e6 <= budget_mb,
          f"пик {peak / 1e6:.0f} МБ при бюджете {budget_mb:.0f} МБ "
          f"(весь файл занял бы {whole_mb:.0f} МБ)")

    print("  распознаю длинную запись целиком (это несколько минут)...")
    started = time.time()
    jobs, progress = run_queue(cfg, [long_file])
    job = jobs[-1]
    check("Длинная запись обработана", job.status == DONE and job.words > 100,
          f"{job.words} слов за {time.time() - started:.0f} с")
    check("Прогресс приходил в интерфейс", len(progress) > 5 and progress[-1] > 0.9,
          f"{len(progress)} обновлений, последнее {progress[-1]:.0%}")
    check("Таймкоды доходят до конца файла",
          job.segments and job.segments[-1]["end"] > duration * 0.9,
          f"последний сегмент на {job.segments[-1]['end'] / 60:.1f} мин" if job.segments else "")
    check("Счётчик слов по-прежнему нулевой", WordCounter(tmp).total == 0)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    print(f"\n(временные данные теста: {tmp})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
