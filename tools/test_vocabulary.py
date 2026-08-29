"""Сколько имён спасает словарь подсказок.

Один и тот же звук прогоняется дважды — без словаря и со словарём — и считается,
сколько ожидаемых слов модель узнала. Разница и есть польза hotwords.

    python tools/test_vocabulary.py            — текущая модель из настроек
    python tools/test_vocabulary.py small medium   — сравнить ещё и модели
"""
import json
import sys
import tempfile
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
from voicetool.media import decode_audio_file  # noqa: E402
from voicetool.vocabulary import Vocabulary  # noqa: E402

SAMPLES = ROOT / "samples" / "vocab"

# То, что пользователь положил бы в vocabulary.txt: имена и названия, которые он
# произносит регулярно. Специально с запасом — в словаре обычно есть и лишнее.
WORDS = [
    "Кузьмина", "Кузьминой", "Ивандар", "Ивандара", "Гильмутдинов", "Гильмутдинову",
    "Хабибуллин", "Севастьянов", "Севастьянову", "Анастасия", "Анастасии",
    "PySide", "ctranslate", "faster-whisper", "WebVTT", "вебвиттиэй",
]


def score(text: str, expected) -> int:
    """Сколько ожидаемых слов реально попало в расшифровку (без учёта регистра)."""
    low = text.lower()
    return sum(1 for word in expected if word.lower() in low)


def run(asr, phrases, hint):
    hits = total = 0
    rows = []
    for item in phrases:
        audio = decode_audio_file(SAMPLES / item["file"], asr.cfg.sample_rate)
        text, _ = asr.transcribe_array(audio, asr.cfg.language_hint, hotwords=hint)
        got = score(text, item["expected"])
        hits += got
        total += len(item["expected"])
        rows.append((item, text, got))
    return hits, total, rows


def main():
    index = SAMPLES / "phrases.json"
    if not index.exists():
        print(f"Нет тестового набора: {index}\nСоздайте его: python tools/make_vocab_samples.py")
        return 1
    phrases = json.loads(index.read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="voicetool-vocab-"))
    vocab = Vocabulary(tmp)
    vocab.path.write_text("\n".join(WORDS), encoding="utf-8")
    hint = vocab.hint()

    models = sys.argv[1:] or [None]
    for size in models:
        cfg = config.load()
        if size:
            cfg["model"] = size
        asr = ASR(cfg)
        asr.model
        title = f"модель {asr.model_size} на {'видеокарте' if asr.device == 'cuda' else 'процессоре'}"
        print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")

        t0 = time.perf_counter()
        off_hits, total, off_rows = run(asr, phrases, None)
        t_off = time.perf_counter() - t0
        t0 = time.perf_counter()
        on_hits, _, on_rows = run(asr, phrases, hint)
        t_on = time.perf_counter() - t0

        print(f"{'фраза':<7} {'ожидали':<28} {'без словаря':<34} со словарём")
        for (item, off_text, off_got), (_, on_text, on_got) in zip(off_rows, on_rows):
            mark = "  " if off_got == on_got else ("+ " if on_got > off_got else "- ")
            expected = ", ".join(item["expected"])
            print(f"{mark}{item['file'][:5]:<5} {expected[:27]:<28} "
                  f"{off_text[:33]:<34} {on_text[:33]}")

        print(f"\n  узнано имён без словаря : {off_hits}/{total} ({off_hits / total:.0%})")
        print(f"  узнано имён со словарём : {on_hits}/{total} ({on_hits / total:.0%})")
        delta = on_hits - off_hits
        print(f"  разница                 : {delta:+d} "
              f"({'словарь помогает' if delta > 0 else 'без изменений' if delta == 0 else 'словарь мешает'})")
        print(f"  время прогона           : {t_off:.1f} с без словаря / {t_on:.1f} с со словарём")
    return 0


if __name__ == "__main__":
    sys.exit(main())
