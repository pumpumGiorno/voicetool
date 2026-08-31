#!/usr/bin/env python3
"""Voice Tool — командная строка. Графическая версия: VoiceTool.py или VoiceTool.exe.

  python voice_tool.py listen          — слушать микрофон
  python voice_tool.py file запись.mp4 — распознать файл (и перевести, если речь не русская)
  python voice_tool.py count | stats   — счётчик слов живого режима
  python voice_tool.py history         — последние распознанные фразы
  python voice_tool.py check           — проверка зависимостей и микрофона
  python voice_tool.py gui             — открыть графический интерфейс
"""
import argparse
import sys
from pathlib import Path

from voicetool import config, deps, logs, paths
from voicetool.counter import WordCounter


def cmd_listen(args, cfg):
    deps.require(["numpy", "faster_whisper"] + ([] if args.source else ["sounddevice"]))
    from voicetool.engine import Listener

    runtime = config.Config(cfg)
    if args.model:
        runtime["model"] = args.model
    if args.silence:
        runtime["silence_seconds"] = args.silence
    runtime["output_mode"] = "insert" if args.insert else "show"
    listener = Listener(runtime, source=args.source, events={
        "status": lambda message: print(f"[Модель] {message}"),
        "recognized": lambda text, words: print(
            f"[Распознано]: {text}\n[Счётчик слов]: +{words}"),
        "agent_result": lambda result: print(f"[Agent]: {result.message}"),
        "error": lambda message: print(f"[Ошибка]: {message}", file=sys.stderr),
    })
    print(f"[Настройки] Пауза до конца команды: {runtime.silence_seconds} с. Ctrl+C — выход.")
    if args.source:
        print(f"[Источник] Файл вместо микрофона: {args.source}")
    listener.start()
    try:
        listener.wait()
    finally:
        listener.stop()
    return 0


def cmd_file(args, cfg):
    deps.require(["numpy", "faster_whisper"])
    from voicetool.processor import DONE, BatchProcessor
    from voicetool.translate import lang_name

    runtime = config.Config(cfg)
    if args.model:
        runtime["model"] = args.model
    if args.no_translate:
        runtime["translator"] = "none"
    tty = bool(getattr(sys.stderr, "isatty", lambda: False)())

    def progress(current):
        if tty and current.duration:
            print(f"\r[Распознавание] {min(100, int(current.progress * 100)):3d}%  "
                  f"{_hms(current.position)} / {_hms(current.duration)}",
                  end="", file=sys.stderr, flush=True)

    processor = BatchProcessor(runtime, events={
        "status": lambda message: print(f"[Модель] {message}"),
        "progress": progress,
    })
    job = processor.add([args.path], language_hint=args.lang)[0]
    if job.error:
        print(f"[Ошибка]: {job.error}", file=sys.stderr)
        return 2
    processor.start()
    try:
        processor.wait()
    finally:
        processor.cancel()
    if job.status != DONE:
        print(f"[Ошибка]: {job.error or job.status}", file=sys.stderr)
        return 2
    if tty:
        print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    print(f"[Определён язык]: {lang_name(job.language)} "
          f"({job.language}, уверенность {job.language_probability:.0%}, "
          f"длительность {int(job.duration // 60)} мин {int(job.duration % 60)} с)")
    print(job.report())
    if job.saved_to:
        print(f"[Сохранено]: {job.saved_to}")
    for fmt in ("srt", "vtt"):
        if getattr(args, fmt):
            out = Path(getattr(args, fmt))
            out.write_text(getattr(job, fmt)(), encoding="utf-8")
            print(f"[Субтитры {fmt.upper()}]: {out}")
    return 0


def _hms(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def cmd_count(args, cfg):
    print(f"[Счётчик слов]: {WordCounter(cfg.data_dir).total}")
    return 0


def cmd_stats(args, cfg):
    s = WordCounter(cfg.data_dir).stats()
    print(f"Всего слов (живой режим): {s['total']}")
    print(f"Сегодня: {s['today']}")
    print(f"За последние 7 дней: {s['week']}")
    print(f"Фраз: {s['phrases']}   Сессий: {s['sessions']}")
    if s["days"]:
        print("\nПо дням:")
        for day, n in s["days"]:
            print(f"  {day}: {n}")
    return 0


def cmd_history(args, cfg):
    from voicetool.history import History

    rows = History(cfg.data_dir).recent(args.limit, kind=args.kind)
    if not rows:
        print("История пуста.")
        return 0
    for row in rows:
        mark = "файл" if row.get("kind") == "file" else "голос"
        words = f"  +{row['words']} слов" if row.get("words") else ""
        source = f"  [{row['source']}]" if row.get("source") else ""
        print(f"{row['ts'].replace('T', ' ')}  ({mark}){source}{words}\n  {row['text']}\n")
    return 0


def cmd_check(args, cfg):
    print(f"Данные: {cfg.data_dir}")
    print(f"Настройки: {cfg.get('_path')}\n")
    return deps.report(cfg)


def cmd_gui(args, cfg):
    from voicetool.gui.app import run

    return run([])


def build_parser():
    p = argparse.ArgumentParser(
        prog="voice_tool.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    listen = sub.add_parser("listen", help="живой режим: ждать слово-триггер и распознавать речь")
    listen.add_argument("--source", help="взять звук из файла вместо микрофона (демо/тест)")
    listen.add_argument("--silence", type=float, help="пауза в секундах до конца команды")
    listen.add_argument("--model", help="размер модели: tiny|base|small|medium|large-v3")
    listen.add_argument("--wake", help="слово-триггер (по умолчанию из config.json)")
    listen.add_argument("--insert", action="store_true",
                        help="вставлять распознанный текст в активное окно (Windows)")
    listen.set_defaults(func=cmd_listen)

    f = sub.add_parser("file", help="распознать аудио/видео файл (+перевод, если речь не русская)")
    f.add_argument("path", help="путь к mp3/wav/m4a/mp4/mkv/avi/...")
    f.add_argument("--lang", help="подсказать язык (по умолчанию определяется сам)")
    f.add_argument("--no-translate", action="store_true", help="не переводить, только оригинал")
    f.add_argument("--model", help="размер модели: tiny|base|small|medium|large-v3")
    f.add_argument("--srt", help="сохранить субтитры SRT в указанный файл")
    f.add_argument("--vtt", help="сохранить субтитры WebVTT в указанный файл")
    f.set_defaults(func=cmd_file)

    sub.add_parser("count", help="показать счётчик слов").set_defaults(func=cmd_count)
    sub.add_parser("stats", help="статистика: всего / сегодня / за неделю").set_defaults(func=cmd_stats)

    h = sub.add_parser("history", help="последние распознанные фразы")
    h.add_argument("--limit", type=int, default=20)
    h.add_argument("--kind", choices=["voice", "file"], help="только голос или только файлы")
    h.set_defaults(func=cmd_history)

    sub.add_parser("check", help="проверить зависимости, микрофон и настройки").set_defaults(func=cmd_check)
    sub.add_parser("gui", help="открыть графический интерфейс").set_defaults(func=cmd_gui)
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # русский текст в консоли Windows
        with_reconfigure = getattr(stream, "reconfigure", None)
        if with_reconfigure:
            with_reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    args = build_parser().parse_args(argv)
    cfg = config.load()
    data_dir = cfg.data_dir
    paths.migrate_legacy(data_dir)
    logs.setup(data_dir, console=False)
    if getattr(args, "wake", None):
        cfg["wake_word"] = args.wake
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        print("\n[Выход]")
        return 0


if __name__ == "__main__":
    sys.exit(main())
