"""Замер задержки живого режима: «Алиса» -> текст.

Меряем ровно то, что чувствует пользователь, и по частям:

  1. обнаружение слова-триггера — прогон короткой фразы через модель триггера;
  2. распознавание команды      — прогон сказанной фразы через основную модель;
  3. загрузка моделей           — разово при включении микрофона.

Фразы берутся из тестовой записи ТЕМ ЖЕ Recorder'ом, что и в живом режиме, поэтому
на вход моделям приходят ровно те куски звука, что и при работе с микрофоном.

    python tools/bench_latency.py                 — текущие настройки
    python tools/bench_latency.py before after    — сравнить два профиля
"""
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import logging  # noqa: E402

logging.getLogger("faster_whisper").setLevel(logging.ERROR)

from voicetool import config  # noqa: E402
from voicetool.asr import ASR  # noqa: E402
from voicetool.audio import Recorder, file_frames  # noqa: E402
from voicetool.text import find_wake_word  # noqa: E402

REPEATS = 5

# «до» — как было до этой итерации: процессор, одна модель small на оба прогона.
# «после» — как стало: видеокарта, лёгкая tiny на слово-триггер, точная модель на команду.
PROFILES = {
    "before": dict(device="cpu", compute_type="int8", model="small", wake_model="",
                   beam_size=5, wake_beam_size=5, language_hint="ru", type_delay_ms=15),
    "after": dict(device="auto", compute_type="auto", model="small", wake_model="tiny",
                  beam_size=5, wake_beam_size=1, language_hint="ru", type_delay_ms=15),
    "after-medium": dict(device="auto", compute_type="auto", model="medium", wake_model="tiny",
                         beam_size=5, wake_beam_size=1, language_hint="ru", type_delay_ms=15),
}


def cut_utterances(cfg, source, limit=4):
    """Достать из записи фразы так же, как это делает живой режим."""
    rec = Recorder(file_frames(source, cfg.sample_rate), cfg)
    rec.threshold = cfg.energy_threshold or cfg.min_energy
    out = []
    while len(out) < limit:
        audio = rec.record_utterance(cfg.wake_silence_seconds)
        if audio is None:
            break
        if len(audio):
            out.append(audio)
    return out


def timed(fn, repeats=REPEATS):
    """Первый прогон прогревочный: он ловит ленивую инициализацию и портит статистику."""
    fn()
    times = []
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return statistics.median(times), min(times), max(times)


def run(name, overrides):
    cfg = config.load()
    cfg.update({k: v for k, v in overrides.items() if k in config.DEFAULTS})

    wake_beam = overrides.get("wake_beam_size", 1)

    print(f"\n{'=' * 62}\nПрофиль «{name}»: устройство={cfg.device} точность={cfg.compute_type} "
          f"модель={cfg.model} модель-триггер={cfg.wake_model or cfg.model}\n{'=' * 62}")

    wake_clips = cut_utterances(cfg, ROOT / "samples" / "ru_live.wav")
    cmd_clips = cut_utterances(cfg, ROOT / "samples" / "ru_live2.wav") or wake_clips
    if not wake_clips:
        print("Не удалось нарезать фразы из тестовой записи")
        return None

    main = ASR(cfg)
    t = time.perf_counter()
    main.model
    load_main = time.perf_counter() - t

    wake_size = (cfg.wake_model or "").strip()
    if wake_size and wake_size != cfg.model:
        wake = ASR(cfg, wake_size)
        t = time.perf_counter()
        wake.model
        load_wake = time.perf_counter() - t
    else:
        wake, load_wake = main, 0.0

    clip = wake_clips[0]
    seconds = len(clip) / cfg.sample_rate
    wake_med, wake_lo, wake_hi = timed(
        lambda: wake.transcribe_wake(clip, cfg.language_hint, beam_size=wake_beam))
    heard, _ = wake.transcribe_wake(clip, cfg.language_hint, beam_size=wake_beam)
    hit, _ = find_wake_word(heard, [cfg.wake_word] + list(cfg.wake_word_aliases))

    cmd = cmd_clips[-1]
    cmd_seconds = len(cmd) / cfg.sample_rate
    cmd_med, cmd_lo, cmd_hi = timed(lambda: main.transcribe_array(cmd, cfg.language_hint))
    text, _ = main.transcribe_array(cmd, cfg.language_hint)

    print(f"  бэкенд                : {main.device.upper()} ({main.compute_type})"
          + (f"; триггер на {wake.device.upper()}" if wake is not main else ""))
    print(f"  загрузка моделей      : {load_main + load_wake:5.2f} с  (разово при включении)")
    print(f"  обнаружение «{cfg.wake_word}»  : {wake_med:5.2f} с  "
          f"(мин {wake_lo:.2f} / макс {wake_hi:.2f}) на {seconds:.1f} с звука"
          f"   {'распознано' if hit else 'НЕ РАСПОЗНАНО'}")
    print(f"  распознавание команды : {cmd_med:5.2f} с  "
          f"(мин {cmd_lo:.2f} / макс {cmd_hi:.2f}) на {cmd_seconds:.1f} с звука")
    # набор текста в чужом окне — тоже часть задержки, которую видит пользователь
    delay = overrides.get("type_delay_ms", cfg.get("type_delay_ms", 15)) / 1000
    typing = len(text) * delay

    print(f"  ИТОГО «Алиса» -> текст: {wake_med + cmd_med:5.2f} с")
    print(f"  + набор в поле        : {typing:5.2f} с ({len(text)} символов по {delay * 1000:.0f} мс)")
    print(f"  = до текста в приложении: {wake_med + cmd_med + typing:5.2f} с")
    print(f"  текст                 : {text[:70]}")
    return {"name": name, "wake": wake_med, "cmd": cmd_med,
            "total": wake_med + cmd_med, "load": load_main + load_wake,
            "typing": typing, "full": wake_med + cmd_med + typing,
            "device": main.device, "hit": hit}


def main():
    names = sys.argv[1:] or ["after"]
    results = [r for r in (run(n, PROFILES.get(n, {})) for n in names) if r]
    if len(results) >= 2:
        a, b = results[0], results[-1]
        print(f"\n{'=' * 62}\nСравнение «{a['name']}» -> «{b['name']}»\n{'=' * 62}")
        for key, title in (("wake", "обнаружение слова-триггера"),
                           ("cmd", "распознавание команды"),
                           ("total", "«Алиса» -> готовый текст"),
                           ("typing", "набор текста в поле"),
                           ("full", "полный путь до текста в поле"),
                           ("load", "загрузка моделей")):
            was, now = a[key], b[key]
            speed = f"в {was / now:.1f} раза быстрее" if now > 0.001 and now < was else \
                    (f"медленнее в {now / was:.1f} раза" if now > was else "без изменений")
            print(f"  {title:<28}: {was:5.2f} с -> {now:5.2f} с   ({speed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
