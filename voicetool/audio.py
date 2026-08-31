"""Захват звука: непрерывный поток кадров + нарезка на фразы по паузам."""
import contextlib
import queue
import sys

import numpy as np

FRAME_MS = 30
PRE_ROLL_SEC = 0.4  # сколько звука до начала речи оставлять, чтобы не терять первый слог


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)) + 1e-12))


MIC_HINT = (
    "Не удалось открыть микрофон.\n\n"
    "Проверьте:\n"
    "1. Подключён ли микрофон.\n"
    "2. Разрешён ли доступ к микрофону в Windows "
    "(Параметры → Конфиденциальность → Микрофон).\n"
    "3. Не занят ли микрофон другим приложением."
)


class MicrophoneError(RuntimeError):
    """Микрофон недоступен. Текст исключения показываем пользователю как есть."""


@contextlib.contextmanager
def mic_frames(cfg):
    """Кадры с микрофона. Поток пишет в очередь, чтобы распознавание его не тормозило."""
    import sounddevice as sd

    sr = cfg.sample_rate
    blocksize = int(sr * FRAME_MS / 1000)
    q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=int(30_000 / FRAME_MS))  # ~30 секунд

    def callback(indata, _frames, _time, status):
        if status:
            print(f"[Микрофон] {status}", file=sys.stderr)
        try:
            q.put_nowait(indata[:, 0].copy())
        except queue.Full:  # потребитель отстал — выкидываем самый старый кадр
            with contextlib.suppress(queue.Empty, queue.Full):
                q.get_nowait()
                q.put_nowait(indata[:, 0].copy())

    def generator():
        while True:
            yield q.get()

    try:
        stream = sd.InputStream(samplerate=sr, channels=1, dtype="float32",
                                blocksize=blocksize, device=cfg.input_device, callback=callback)
    except Exception as e:  # sounddevice бросает PortAudioError и просто OSError
        raise MicrophoneError(f"{MIC_HINT}\n\nПодробности: {e}") from e
    with stream:
        yield generator()


def file_frames(path, sample_rate):
    """Те же кадры, но из готового файла — для демонстрации и тестов без микрофона."""
    from .media import decode_audio_file

    audio = decode_audio_file(path, sample_rate)
    step = int(sample_rate * FRAME_MS / 1000)
    for i in range(0, len(audio), step):
        chunk = audio[i:i + step]
        if len(chunk) < step:
            chunk = np.pad(chunk, (0, step - len(chunk)))
        yield chunk


class Recorder:
    """Отдаёт по одной фразе: начало — по громкости, конец — по паузе заданной длины."""

    def __init__(self, frames, cfg, on_level=None, interrupt=None):
        """on_level(rms, speaking) — для индикатора громкости;
        interrupt() -> True — бросить ожидание (пауза, выход, горячая клавиша)."""
        self.frames = iter(frames)
        self.cfg = cfg
        self.frame_sec = FRAME_MS / 1000
        self.threshold = cfg.energy_threshold or None
        self.on_level = on_level
        self.interrupt = interrupt

    def calibrate(self):
        """Порог = фоновый шум * noise_multiplier (но не ниже min_energy)."""
        if self.threshold:
            return self.threshold
        levels = []
        for _ in range(max(1, int(self.cfg.calibration_seconds / self.frame_sec))):
            try:
                levels.append(rms(next(self.frames)))
            except StopIteration:
                break
        noise = float(np.median(levels)) if levels else 0.0
        self.threshold = max(noise * self.cfg.noise_multiplier, self.cfg.min_energy)
        return self.threshold

    def record_utterance(self, silence_seconds, max_seconds=None):
        """Блокируется до конца фразы. None — звук кончился (актуально для файла-источника).

        max_seconds ограничивает длину записи: для ожидания слова-триггера нет смысла
        копить минуту разговора — Whisper потом будет распознавать её целиком впустую.
        """
        max_seconds = max_seconds or self.cfg.max_utterance_seconds
        threshold = self.threshold or self.calibrate()
        pre_roll = []
        pre_roll_len = int(PRE_ROLL_SEC / self.frame_sec)
        collected, speech_frames, silence_sec, recording = [], 0, 0.0, False
        exhausted = True

        for frame in self.frames:
            level = rms(frame)
            loud = level > threshold
            if self.on_level:
                self.on_level(level, loud)
            if not recording:
                # прерывание проверяем только до начала речи: рвать фразу на середине незачем
                if self.interrupt and self.interrupt():
                    return np.zeros(0, dtype=np.float32)
                pre_roll.append(frame)
                del pre_roll[:-pre_roll_len]
                if loud:
                    recording, collected, speech_frames = True, list(pre_roll), 1
                continue

            collected.append(frame)
            if loud:
                speech_frames += 1
                silence_sec = 0.0
            else:
                silence_sec += self.frame_sec
                if silence_sec >= silence_seconds:
                    exhausted = False
                    break
            if len(collected) * self.frame_sec >= max_seconds:
                exhausted = False
                break
        if exhausted and not recording:
            return None  # источник кадров исчерпан (для микрофона не наступает)

        if speech_frames * self.frame_sec < self.cfg.min_speech_seconds:
            return np.zeros(0, dtype=np.float32)  # был только шум
        return np.concatenate(collected)
