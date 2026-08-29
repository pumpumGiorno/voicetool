"""Проверка бэкенда: работает ли видеокарта и что будет, если её нет.

Второе важнее первого. На машине без NVIDIA (или после переустановки Windows, когда
CUDA-библиотек ещё нет) программа обязана просто работать на процессоре — без падений,
без зависаний и с понятной записью в логе.

Недоступность CUDA имитируется честно: пути поиска DLL подменяются на пустую папку,
а счётчик устройств — на ноль. Ровно то, что видит faster-whisper на чужом компьютере.

    python tools/test_gpu_fallback.py
"""
import logging
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.getLogger("faster_whisper").setLevel(logging.ERROR)

from voicetool import config, cuda  # noqa: E402
from voicetool import asr as asr_module  # noqa: E402
from voicetool.media import decode_audio_file  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def reset_cuda_state():
    cuda._state.update(prepared=False, dirs=[], available=None, reason="", device=None)
    asr_module._gpu_unusable = False


def main():
    cfg = config.load()
    audio = decode_audio_file(ROOT / "samples" / "ru_live.wav", cfg.sample_rate)

    # --- 1. как есть на этой машине ---
    print("Бэкенд на этой машине")
    reset_cuda_state()
    st = cuda.status()
    print(f"  видеокарта: {st['name'] or '—'}, устройств: {st['devices']}")
    asr = asr_module.ASR(cfg)
    t = time.perf_counter()
    asr.model
    load = time.perf_counter() - t
    text, _ = asr.transcribe_array(audio, cfg.language_hint)
    check("Модель загрузилась", asr.loaded, f"{asr.device} / {asr.compute_type} за {load:.1f} с")
    check("Речь распознана", bool(text), text[:50])
    if st["available"]:
        check("Распознавание идёт на видеокарте", asr.device == "cuda",
              f"бэкенд {asr.device}")
        check("На видеокарте выбрана точность float16", asr.compute_type == "float16",
              asr.compute_type)
        check("Путь к CUDA-библиотекам найден", bool(st["dirs"]), "; ".join(st["dirs"])[:70])
    else:
        check("Видеокарты нет — работаем на процессоре", asr.device == "cpu", st["reason"])

    # --- 2. CUDA «пропала» ---
    print("\nCUDA недоступна (имитация чужого компьютера без NVIDIA)")
    reset_cuda_state()
    empty = Path(tempfile.mkdtemp(prefix="voicetool-nocuda-"))
    real_candidates, real_count = cuda._candidate_dirs, cuda.gpu_count
    cuda._candidate_dirs = lambda: [empty]   # DLL искать негде
    cuda.gpu_count = lambda: 0               # и устройств не видно
    try:
        device, compute, reason = cuda.resolve("auto", "auto")
        check("Выбран процессор", device == "cpu", f"{device} / {compute}")
        check("Точность для процессора — int8", compute == "int8", compute)
        check("Есть внятная причина отката", bool(reason), reason)

        st_off = cuda.status()
        check("Проверка системы это видит", st_off["available"] is False, st_off["reason"])

        asr_cpu = asr_module.ASR(cfg)
        t = time.perf_counter()
        asr_cpu.model
        load_cpu = time.perf_counter() - t
        text_cpu, _ = asr_cpu.transcribe_array(audio, cfg.language_hint)
        check("Программа не упала и загрузила модель",
              asr_cpu.loaded and asr_cpu.device == "cpu", f"за {load_cpu:.1f} с")
        check("Речь распознана и без видеокарты", bool(text_cpu), text_cpu[:50])
        check("Текст тот же, что на видеокарте", text_cpu.strip() == text.strip(),
              f"GPU: {text[:40]!r} / CPU: {text_cpu[:40]!r}")

        # явный запрос видеокарты при её отсутствии тоже не должен ронять программу
        device2, _, reason2 = cuda.resolve("cuda", "auto")
        check("Явный запрос «cuda» без CUDA не роняет программу", device2 == "cpu", reason2)
    finally:
        cuda._candidate_dirs, cuda.gpu_count = real_candidates, real_count
        reset_cuda_state()

    # --- 3. состояние восстановлено ---
    print("\nПосле проверки")
    st_back = cuda.status()
    check("Определение видеокарты снова работает",
          st_back["available"] == bool(st["available"]),
          f"available={st_back['available']}")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
