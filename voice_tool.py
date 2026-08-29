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
from voicetool.counter import WordCounter, append_log, save_transcript
from voicetool.text import find_wake_word
from voicetool.translate import get_translator, lang_name


def cmd_listen(args, cfg):
    deps.require(["numpy", "faster_whisper"] + ([] if args.source else ["sounddevice"]))
    import contextlib

    from voicetool.asr import ASR
    from voicetool.audio import Recorder, file_frames, mic_frames
    from voicetool.history import History

    wake_variants = [cfg.wake_word] + list(cfg.wake_word_aliases)
    asr = ASR(cfg, args.model, on_status=lambda m: print(f"[Модель] {m}"))
    counter = WordCounter(cfg.data_dir)
    history = History(cfg.data_dir)
    _ = asr.model  # прогреваем модель заранее, иначе потеряем первую фразу

    if args.source:
        print(f"[Источник] Файл вместо микрофона: {args.source}")
        source = contextlib.nullcontext(file_frames(args.source, cfg.sample_rate))
    else:
        source = mic_frames(cfg)

    with source as frames:
        rec = Recorder(frames, cfg)
        if args.source:
            rec.threshold = cfg.energy_threshold or cfg.min_energy
        else:
            print(f"[Калибровка] Замеряю фоновый шум {cfg.calibration_seconds:.0f} с, помолчите...")
            print(f"[Калибровка] Порог громкости: {rec.calibrate():.4f}")
        print(f"[Настройки] Пауза до конца команды: {args.silence or cfg.silence_seconds} с. "
              f"Ctrl+C — выход.")

        while True:
            print(f'\n[Ожидание слова "{cfg.wake_word}"...]')
            audio = rec.record_utterance(cfg.wake_silence_seconds)
            if audio is None:
                break
            if not len(audio):
                continue
            heard, _ = asr.transcribe_array(audio, cfg.language_hint)
            hit, tail = find_wake_word(heard, wake_variants)
            if not hit:
                if heard:
                    print(f"[Мимо]: {heard}")
                continue
            print(f"[Триггер]: {heard}")

            command = tail
            if not command:
                print("[Слушаю...]")
                audio = rec.record_utterance(args.silence or cfg.silence_seconds)
                if audio is None:
                    break
                if len(audio):
                    command, _ = asr.transcribe_array(audio, cfg.language_hint)
            if not command.strip():
                print("[Пусто]: после слова-триггера ничего не распознано")
                continue

            print(f"[Распознано]: {command}")
            added = counter.add(command)
            history.add(command, kind="voice", words=added)
            print(f"[Счётчик слов]: {counter.total} (+{added})")
            if cfg.log_transcripts:
                append_log(cfg.data_dir, command)
            if args.insert:
                _insert(command, cfg)
    return 0


def _insert(text, cfg):
    from voicetool import inject

    try:
        inject.type_text(text, press_enter=bool(cfg.press_enter),
                         pause=max(0, int(cfg.get("type_delay_ms", 10))) / 1000)
        print("[Набрано] в активное окно (буфер обмена не задействован)")
    except Exception as e:
        print(f"[Ввод не удался]: {e}", file=sys.stderr)


def cmd_file(args, cfg):
    deps.require(["numpy", "faster_whisper"])
    from voicetool.asr import ASR
    from voicetool.history import History
    from voicetool.subtitles import to_srt, to_vtt

    asr = ASR(cfg, args.model, on_status=lambda m: print(f"[Модель] {m}"))
    tty = bool(getattr(sys.stderr, "isatty", lambda: False)())

    def progress(done, total):
        if tty and total:
            print(f"\r[Распознавание] {min(100, int(done / total * 100)):3d}%  "
                  f"{_hms(done)} / {_hms(total)}", end="", file=sys.stderr, flush=True)

    try:
        result = asr.transcribe_file(args.path, language=args.lang, on_progress=progress)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"\n[Ошибка]: {e}", file=sys.stderr)
        return 2
    if tty:
        print("\r" + " " * 60 + "\r", end="", file=sys.stderr)

    lang, text = result["language"], result["text"]
    print(f"[Определён язык]: {lang_name(lang)} "
          f"({lang}, уверенность {result['language_probability']:.0%}, "
          f"длительность {int(result['duration'] // 60)} мин {int(result['duration'] % 60)} с)")
    if not text:
        print("[Текст]: речь не распознана")
        return 0

    body = [f"Файл: {args.path}", f"Язык: {lang_name(lang)} ({lang})", "", text]
    if lang == cfg.translate_to:
        print(f"[Текст]: {text}")
    else:
        print(f"[Оригинал]: {text}")
        translator = get_translator(cfg, enabled=not args.no_translate)
        if translator is None:
            print("[Перевод]: отключён")
        else:
            try:
                translated = translator.translate(text, lang)
                print(f"[Перевод на {lang_name(cfg.translate_to)}]: {translated}")
                body += ["", f"Перевод ({lang_name(cfg.translate_to)}):", translated]
            except RuntimeError as e:
                print(f"[Перевод не выполнен]: {e}", file=sys.stderr)

    if cfg.log_transcripts:
        saved = save_transcript(cfg.data_dir, args.path, "\n".join(body))
        print(f"[Сохранено]: {saved}")
    # текст из файла в счётчик слов не попадает — это только живой режим
    History(cfg.data_dir).add(text[:400], kind="file", words=0,
                              source=Path(args.path).name, language=lang)

    for fmt, render in (("srt", to_srt), ("vtt", to_vtt)):
        if getattr(args, fmt):
            out = Path(getattr(args, fmt))
            out.write_text(render(result["segments"]), encoding="utf-8")
            print(f"[Субтитры {fmt.upper()}]: {out}")
    return 0


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


def _hms(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


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
