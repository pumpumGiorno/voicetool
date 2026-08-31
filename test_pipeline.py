"""Проверки логики без микрофона, модели и графики: python test_pipeline.py

Всё, что требует Whisper, микрофона или живого Windows-окна, лежит в tools/:
  tools/gui_smoke.py          — собрать интерфейс и снять скриншоты всех страниц
  tools/test_inject.py        — вставка текста в Блокнот (кириллица, буфер, Enter)
  tools/test_live_pipeline.py — «Алиса» -> распознавание -> счётчик -> вставка
"""
import json
import sys
import tempfile
import types
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from voicetool import config, hotkey, paths
from voicetool.audio import FRAME_MS, Recorder
from voicetool.counter import WordCounter
from voicetool.history import History
from voicetool.processor import Job, is_supported
from voicetool.subtitles import to_srt, to_vtt
from voicetool.text import count_words, find_wake_word
from voicetool.translate import split_sentences


def test_wake_word():
    aliases = ["алиса", "алис", "alisa"]
    assert find_wake_word("Алиса", aliases) == (True, "")
    assert find_wake_word("Алиса, напомни мне купить молоко", aliases)[1] == "напомни мне купить молоко"
    assert find_wake_word("окей алис включи свет", aliases)[1] == "включи свет"
    assert find_wake_word("Алиса.", aliases) == (True, "")
    assert find_wake_word("сегодня хорошая погода", aliases) == (False, "")
    assert find_wake_word("", aliases) == (False, "")
    # похожие, но чужие слова не должны будить
    assert find_wake_word("алгебра", aliases)[0] is False


def test_count_words():
    assert count_words("напомни мне купить молоко") == 4
    assert count_words("  ") == 0
    assert count_words("по-моему это 2 слова") == 4
    assert count_words(None) == 0


def test_counter_persists():
    with tempfile.TemporaryDirectory() as tmp:
        c = WordCounter(Path(tmp))
        assert c.add("раз два три") == 3
        c.add("четыре")
        assert c.total == 4
        assert WordCounter(Path(tmp)).total == 4, "счётчик должен переживать перезапуск"
        stats = WordCounter(Path(tmp)).stats()
        assert stats["today"] == 4 and stats["week"] == 4
        assert stats["phrases"] == 2
        # битый файл не должен обнулять данные молча
        (Path(tmp) / "word_count.json").write_text("{broken", encoding="utf-8")
        assert WordCounter(Path(tmp)).total == 0
        assert list(Path(tmp).glob("word_count.broken-*.json"))


def test_counter_sessions_and_chart():
    with tempfile.TemporaryDirectory() as tmp:
        c = WordCounter(Path(tmp))
        assert c.start_session() == 1
        assert WordCounter(Path(tmp)).start_session() == 2
        c = WordCounter(Path(tmp))
        c.add("раз два")
        days = c.last_days(7)
        assert len(days) == 7 and days[-1][0] == date.today() and days[-1][1] == 2
        assert days[0][0] == date.today() - timedelta(days=6)


def test_recorder_cuts_on_silence():
    cfg = config.Config(dict(config.DEFAULTS, min_speech_seconds=0.1, energy_threshold=0.01))
    frame = int(cfg.sample_rate * FRAME_MS / 1000)
    quiet = np.zeros(frame, dtype=np.float32)
    loud = np.full(frame, 0.3, dtype=np.float32)
    # 10 кадров тишины, 20 речи, 40 тишины (1.2 с), снова речь
    frames = [quiet] * 10 + [loud] * 20 + [quiet] * 40 + [loud] * 10 + [quiet] * 40
    rec = Recorder(iter(frames), cfg)
    first = rec.record_utterance(silence_seconds=0.9)
    assert first is not None and len(first) > 0
    seconds = len(first) / cfg.sample_rate
    assert 0.9 < seconds < 2.2, seconds  # речь + пре-ролл + пауза
    second = rec.record_utterance(silence_seconds=0.9)
    assert second is not None and len(second) > 0
    assert rec.record_utterance(silence_seconds=0.9) is None  # кадры кончились


def test_recorder_ignores_short_noise():
    cfg = config.Config(dict(config.DEFAULTS, min_speech_seconds=0.5, energy_threshold=0.01))
    frame = int(cfg.sample_rate * FRAME_MS / 1000)
    frames = [np.zeros(frame, dtype=np.float32)] * 5 + [np.full(frame, 0.3, dtype=np.float32)] * 2 \
        + [np.zeros(frame, dtype=np.float32)] * 40
    rec = Recorder(iter(frames), cfg)
    assert len(rec.record_utterance(silence_seconds=0.9)) == 0


def test_recorder_interrupt_and_level():
    """Пауза и горячая клавиша должны выдёргивать Recorder из ожидания речи."""
    cfg = config.Config(dict(config.DEFAULTS, energy_threshold=0.01))
    frame = int(cfg.sample_rate * FRAME_MS / 1000)
    frames = [np.zeros(frame, dtype=np.float32)] * 200
    levels = []
    stop = {"now": False}
    rec = Recorder(iter(frames), cfg,
                   on_level=lambda lvl, loud: (levels.append(lvl), stop.update(now=len(levels) > 3)),
                   interrupt=lambda: stop["now"])
    out = rec.record_utterance(silence_seconds=0.9)
    assert out is not None and len(out) == 0, "прерывание возвращает пустую запись, а не None"
    assert len(levels) < 200, "цикл должен прерваться, а не досмотреть все кадры"


def test_config_merges_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        path.write_text(json.dumps({"wake_word": "джарвис", "лишнее": 1}), encoding="utf-8")
        cfg = config.load(path)
        assert cfg.wake_word == "джарвис"
        assert cfg.silence_seconds == config.DEFAULTS["silence_seconds"]
        cfg2 = config.load(Path(tmp) / "new.json")  # файла нет — создаётся с дефолтами
        assert (Path(tmp) / "new.json").exists() and cfg2.wake_word == "алиса"


def test_config_saves_and_reloads():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        cfg = config.load(path)
        cfg["wake_word"] = "марвин"
        cfg["press_enter"] = True
        cfg.save()
        again = config.load(path)
        assert again.wake_word == "марвин" and again.press_enter is True
        assert "_path" not in json.loads(path.read_text(encoding="utf-8")), \
            "служебные ключи не должны попадать в файл"


def test_press_enter_off_by_default():
    """Условие задачи: сообщение не должно отправляться само."""
    assert config.DEFAULTS["press_enter"] is False
    assert config.DEFAULTS["output_mode"] == "insert"
    assert config.DEFAULTS["insert_into_wake_window"] is True
    assert config.DEFAULTS["start_with_windows"] is False


def test_no_clipboard_anywhere():
    """Условие задачи: буфер обмена не используется вообще, даже как запасной путь."""
    from voicetool import inject

    assert not [n for n in dir(inject) if "clipboard" in n.lower()],         "в модуле ввода не должно остаться функций работы с буфером"
    source = Path(inject.__file__).read_text(encoding="utf-8")
    for forbidden in ("OpenClipboard", "SetClipboardData", "GetClipboardData",
                      "EmptyClipboard", "CF_UNICODETEXT"):
        assert forbidden not in source, f"остался вызов буфера обмена: {forbidden}"
    assert "restore_clipboard" not in config.DEFAULTS
    assert "KEYEVENTF_UNICODE" in source, "ввод должен идти юникодным SendInput"


def test_data_dir_outside_program():
    """Статистика не должна жить рядом с exe — иначе обновление её сотрёт."""
    with tempfile.TemporaryDirectory() as tmp:
        assert paths.resolve_data_dir(tmp) == Path(tmp)
    auto = paths.resolve_data_dir("auto")
    assert auto.is_dir()
    assert paths.app_dir() not in auto.parents and auto != paths.app_dir()


def test_history_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        h = History(Path(tmp))
        h.add("привет мир", kind="voice", words=2)
        h.add("расшифровка лекции", kind="file", words=0, source="lecture.mp4", language="ru")
        rows = h.recent(10)
        assert len(rows) == 2 and rows[0]["kind"] == "file"  # новые сверху
        assert [r["kind"] for r in h.recent(10, kind="voice")] == ["voice"]
        # битая строка не должна ронять чтение
        with open(Path(tmp) / "history.jsonl", "a", encoding="utf-8") as f:
            f.write("{это не json\n")
        assert len(h.recent(10)) == 2
        h.clear()
        assert h.recent(10) == []


def test_subtitles():
    segments = [{"start": 0.0, "end": 2.5, "text": "Первая фраза"},
                {"start": 2.5, "end": 4.0, "text": "Вторая фраза"},
                {"start": 4.0, "end": 4.0, "text": "Нулевая длительность"},
                {"start": 5.0, "end": 6.0, "text": "   "}]
    srt = to_srt(segments)
    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert srt.startswith("1\n")
    assert "Нулевая длительность" in srt and "   " not in srt.split("\n\n")[-1]
    assert srt.count("-->") == 3, "пустой сегмент должен выпасть"
    vtt = to_vtt(segments)
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.500" in vtt
    # куски файла могут наезжать друг на друга — время не должно идти назад
    overlap = to_srt([{"start": 0, "end": 5, "text": "а"}, {"start": 3, "end": 6, "text": "б"}])
    assert "00:00:05,000 --> 00:00:06,000" in overlap


def test_split_sentences():
    assert split_sentences("Раз. Два! Три?") == ["Раз.", "Два!", "Три?"]
    long_one = "слово " * 200
    assert all(len(s) <= 400 for s in split_sentences(long_one))


def test_hotkey_parsing():
    mods, key = hotkey.parse("Ctrl+Alt+A")
    assert mods == hotkey.MOD_CONTROL | hotkey.MOD_ALT and key == ord("A")
    assert hotkey.parse("ctrl+shift+f5")[1] == 0x74
    assert hotkey.parse("F8") == (0, 0x77), "одиночная клавиша разрешена (бинд на одну кнопку)"
    assert hotkey.parse("A") == (0, ord("A")), "одиночная буква тоже разрешена"
    assert hotkey.parse("Escape") == (0, 0x1B)
    assert hotkey.parse("Tab") == (0, 0x09)
    assert hotkey.parse("Windows") == (0, hotkey.VK_LWIN), "одиночная клавиша Windows"
    assert hotkey.parse("Win") == (0, hotkey.VK_LWIN)
    assert hotkey.parse("Win+F8") == (hotkey.MOD_WIN, 0x77), "Win в сочетании — модификатор"
    assert hotkey.parse("PrintScreen") == (0, 0x2C)
    assert hotkey.parse("") is None
    assert hotkey.parse("Ctrl+Чепуха") is None


def test_supported_formats():
    assert is_supported("lecture.mp4") and is_supported("song.MP3") and is_supported("a.mkv")
    assert not is_supported("notes.txt") and not is_supported("archive.zip")


def test_job_report_marks_translation():
    job = Job(path=Path("interview.mp4"), language="en", text="Hello there",
              translation="Привет")
    report = job.report()
    assert "Оригинал:" in report and "Перевод" in report and "Hello there" in report
    ru = Job(path=Path("lecture.mp3"), language="ru", text="Привет")
    assert "Оригинал:" not in ru.report()
    assert ru.words == 1


def test_failed_translation_keeps_transcript():
    """Regression: падение перевода не должно отменять уже готовую расшифровку."""
    from voicetool.processor import DONE, BatchProcessor

    class FakeASR:
        def transcribe_file(self, path, **kwargs):
            return {"language": "en", "language_probability": 0.9, "duration": 3.0,
                    "text": "Hello there", "segments": [{"start": 0, "end": 3, "text": "Hello there"}]}

    class BrokenTranslator:
        def translate(self, text, src):
            raise TypeError("переводчик сломался посреди работы")

    from voicetool import processor as processor_module

    original = processor_module.get_translator
    processor_module.get_translator = lambda cfg, **kw: BrokenTranslator()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.Config(dict(config.DEFAULTS, data_dir=tmp, log_transcripts=False))
            proc = BatchProcessor(cfg, asr_factory=FakeASR)
            audio = Path(tmp) / "talk.mp3"
            audio.write_bytes(b"stub")
            proc.add([audio])
            proc.start()
            proc._thread.join(timeout=10)
            job = proc.jobs[0]
            assert job.status == DONE, f"статус {job.status}, ошибка: {job.error}"
            assert job.text == "Hello there", "расшифровку потеряли из-за перевода"
            assert "перевести не удалось" in job.error, "пользователю не сказали, что перевода нет"
    finally:
        processor_module.get_translator = original


def test_model_prefers_local_cache():
    """Regression: вторая модель в процессе вешала программу на запросе к HuggingFace.

    Лечится тем, что модель сначала открывается строго из локального кэша
    (local_files_only=True) и только при её отсутствии идёт в сеть. Заодно проверяем,
    что неудачная попытка GPU запоминается и CUDA не пробуется по второму разу.
    """
    try:
        import faster_whisper
    except ImportError:
        # This test replaces the model class completely; keep the advertised
        # dependency-free pipeline suite dependency-free on Linux CI.
        faster_whisper = types.ModuleType("faster_whisper")
        faster_whisper.WhisperModel = object
        sys.modules["faster_whisper"] = faster_whisper

    from voicetool import asr as asr_module
    from voicetool import cuda as cuda_module

    calls = []

    class FakeModel:
        def __init__(self, size, **kwargs):
            calls.append(kwargs)
            if not kwargs.get("local_files_only"):
                raise AssertionError("полезли в сеть при наличии модели в кэше")
            self.model = type("M", (), {"device": kwargs.get("device")})()

        def transcribe(self, audio, **kwargs):
            if self.model.device != "cpu":
                raise RuntimeError("cublas64_12.dll not found")  # GPU только притворяется
            return iter(()), None

    original_model = faster_whisper.WhisperModel
    original_flag = asr_module._gpu_unusable
    original_probe = cuda_module.probe
    faster_whisper.WhisperModel = FakeModel
    asr_module._gpu_unusable = False
    cuda_module.probe = lambda: (True, "")  # как будто CUDA на месте
    try:
        cfg = config.Config(dict(config.DEFAULTS, data_dir=tempfile.gettempdir(),
                                 device="auto", compute_type="auto"))
        asr_module.ASR(cfg).model
        assert calls[0]["local_files_only"] is True, "первым делом должен идти локальный кэш"
        assert [c["device"] for c in calls] == ["cuda", "cpu"], "ожидали откат cuda -> cpu"
        assert calls[0]["compute_type"] == "float16", "на видеокарте auto = float16"
        assert calls[1]["compute_type"] == "int8", "на процессоре auto = int8"

        calls.clear()
        asr_module.ASR(cfg).model
        assert [c["device"] for c in calls] == ["cpu"], \
            "неработающий GPU должны были запомнить и не пробовать снова"
    finally:
        faster_whisper.WhisperModel = original_model
        asr_module._gpu_unusable = original_flag
        cuda_module.probe = original_probe


def test_quiet_split_avoids_cutting_speech():
    """Границу куска нужно ставить в тишине, иначе слово рвётся пополам."""
    from voicetool.media import _quiet_split

    sr = 16000
    audio = np.concatenate([
        np.full(sr * 20, 0.3, dtype=np.float32),   # речь 0-20 с
        np.zeros(sr * 6, dtype=np.float32),        # пауза 20-26 с
        np.full(sr * 20, 0.3, dtype=np.float32),   # речь 26-46 с
    ])
    cut = _quiet_split(audio, sr, target=sr * 22)  # целимся в 22-ю секунду
    assert 20 * sr <= cut <= 26 * sr, f"разрез {cut / sr:.1f} с должен попасть в паузу"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for t in tests:
        try:
            t()
            print(f"ok  {t.__name__}")
        except AssertionError as e:
            failed.append(t.__name__)
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} проверок пройдено")
    raise SystemExit(1 if failed else 0)
