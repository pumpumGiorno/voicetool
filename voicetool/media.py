"""Потоковое чтение аудиодорожки: файл любой длины разбирается кусками.

Раньше файл декодировался целиком — час записи это ~230 МБ float32 в оперативной памяти,
а четыре часа уже неприятно. Здесь дорожка читается кусками по chunk_seconds, каждый
уходит в Whisper и сразу освобождается.

Резать посреди слова нельзя, поэтому точку разреза ищем по самому тихому месту рядом
с целевой границей — на нормальной записи это пауза между фразами.
"""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SEARCH_SEC = 15.0   # насколько вокруг границы искать тишину
PROBE_SEC = 0.4     # длина окна, по которому меряем тишину

DECODE_HINT = (
    "Не удалось прочитать звук из файла: возможно, это не аудио/видео, "
    "в нём нет звуковой дорожки или формат экзотический.\n"
    "Если файл точно со звуком — поставьте ffmpeg, он нужен как запасной декодер:\n"
    "  winget install Gyan.FFmpeg   (Windows)\n"
    "  brew install ffmpeg          (macOS)\n"
    "  sudo apt install ffmpeg      (Debian/Ubuntu)"
)


def probe_duration(path, default=0.0) -> float:
    """Длительность в секундах из метаданных — нужна для прогресса до начала работы."""
    try:
        import av

        with av.open(str(path)) as container:
            if container.duration:
                return float(container.duration) / av.time_base
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except Exception as e:
        log.debug("Длительность %s не определена: %s", path, e)
    return default


def _raw_frames(path, sample_rate):
    """Моно float32 кусками так, как их отдаёт декодер."""
    import av

    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise RuntimeError("В файле нет звуковой дорожки")
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                yield out.to_ndarray().reshape(-1).astype(np.float32, copy=False)
        for out in resampler.resample(None):  # хвост ресемплера
            yield out.to_ndarray().reshape(-1).astype(np.float32, copy=False)


def _quiet_split(audio: np.ndarray, sample_rate: int, target: int) -> int:
    """Индекс разреза около target, попадающий в самое тихое место (чтобы не резать слово)."""
    search = int(SEARCH_SEC * sample_rate)
    probe = max(1, int(PROBE_SEC * sample_rate))
    lo, hi = max(probe, target - search), min(len(audio) - probe, target + search)
    if hi <= lo:
        return target
    step = max(1, probe // 4)
    window = np.abs(audio)
    best, best_level = target, None
    for i in range(lo, hi, step):
        level = float(window[i:i + probe].mean())
        if best_level is None or level < best_level:
            best, best_level = i + probe // 2, level
    return best


def audio_chunks(path, sample_rate=16000, chunk_seconds=300.0):
    """Генератор (offset_seconds, audio) — куски дорожки по порядку.

    Память под уже отданный кусок освобождается сразу: держим только буфер накопления.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Файл не найден: {p}")

    try:
        frames = _raw_frames(p, sample_rate)
        first = next(frames, None)
    except Exception as first_error:
        yield from _ffmpeg_fallback(p, sample_rate, chunk_seconds, first_error)
        return
    if first is None:
        raise RuntimeError(f"{DECODE_HINT}\n\nЗвуковых данных в файле не найдено.")

    limit = int(chunk_seconds * sample_rate)
    buffer, offset = [first], 0
    size = len(first)
    for frame in frames:
        buffer.append(frame)
        size += len(frame)
        if size >= limit + int(SEARCH_SEC * sample_rate):
            audio = np.concatenate(buffer)
            cut = _quiet_split(audio, sample_rate, limit)
            yield offset / sample_rate, audio[:cut]
            offset += cut
            buffer, size = [audio[cut:].copy()], len(audio) - cut
            del audio
    if size:
        yield offset / sample_rate, np.concatenate(buffer)


def _ffmpeg_fallback(path: Path, sample_rate, chunk_seconds, first_error):
    """Экзотический контейнер: перегоняем в wav через ffmpeg и читаем его."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(f"{DECODE_HINT}\n\nИсходная ошибка: {first_error}") from first_error
    log.info("Встроенный декодер не справился с %s, пробую ffmpeg", path.name)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        cmd = [ffmpeg, "-nostdin", "-loglevel", "error", "-i", str(path),
               "-vn", "-ac", "1", "-ar", str(sample_rate), "-y", str(wav)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not wav.exists():
            raise RuntimeError(f"ffmpeg не смог извлечь звук:\n{proc.stderr.strip()}") from first_error
        yield from audio_chunks(wav, sample_rate, chunk_seconds)


def decode_audio_file(path, sample_rate=16000) -> np.ndarray:
    """Вся дорожка одним массивом. Для коротких файлов и для режима listen --source."""
    parts = [chunk for _, chunk in audio_chunks(path, sample_rate, chunk_seconds=600.0)]
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return parts[0] if len(parts) == 1 else np.concatenate(parts)
