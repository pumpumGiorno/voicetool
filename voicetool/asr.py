"""Локальное распознавание речи через faster-whisper (модель работает офлайн)."""
import logging
import os

from . import cuda
from .media import DECODE_HINT, audio_chunks, decode_audio_file, probe_duration  # noqa: F401  (re-export)

log = logging.getLogger(__name__)
VIDEO_HINT = DECODE_HINT  # имя из CLI-версии, оставлено ради обратной совместимости

# Сеть не должна подвешивать приложение навсегда. huggingface_hub по умолчанию
# ждёт ответа без разумного предела; при залипшем соединении программа просто
# висела бы с пустым окном. Значения переопределяются, если пользователь задал свои.
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

_gpu_unusable = False  # выяснили один раз на процесс: второй раз CUDA не трогаем


def _supports_hotwords() -> bool:
    """В faster-whisper 1.0.3+ есть параметр hotwords; на старых версиях его нет."""
    global _has_hotwords
    if _has_hotwords is None:
        import inspect

        from faster_whisper import WhisperModel

        _has_hotwords = "hotwords" in inspect.signature(WhisperModel.transcribe).parameters
        log.debug("faster-whisper %s hotwords", "поддерживает" if _has_hotwords else "не умеет")
    return _has_hotwords


_has_hotwords = None


class ASR:
    """Ленивая обёртка над моделью: грузим её только когда реально нужно распознавать."""

    def __init__(self, cfg, model_size=None, on_status=None):
        self.cfg = cfg
        self.model_size = model_size or cfg.model
        self.on_status = on_status or (lambda msg: None)
        self._model = None
        self.device = ""            # на чём в итоге считаем: cuda | cpu
        self.compute_type = ""
        self.fallback_reason = ""   # почему не GPU, если не GPU

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def model(self):
        if self._model is None:
            self.on_status(f"Загружаю модель Whisper «{self.model_size}»...")
            device, compute, reason = cuda.resolve(self.cfg.device, self.cfg.compute_type)
            self.compute_type = compute
            self.fallback_reason = reason
            log.info("Загружаю модель %s (запрошено device=%s -> %s, compute=%s)%s",
                     self.model_size, self.cfg.device, device, compute,
                     f"; причина отката: {reason}" if reason else "")
            self._model = self._build(device)
            self.device = str(self._model.model.device)
            log.info("Модель %s готова: бэкенд %s (%s)",
                     self.model_size, self.device.upper(), self.compute_type)
            self.on_status(f"Модель «{self.model_size}» готова "
                           f"({'видеокарта' if self.device == 'cuda' else 'процессор'}).")
        return self._model

    def unload(self):
        self._model = None

    def _open(self, device):
        """Создать модель, не выходя в сеть, если она уже скачана.

        faster-whisper при каждом создании модели дёргает HuggingFace, чтобы сверить
        ревизию. При залипшем соединении это виснет насмерть: программа стоит с 0%
        процессора и без единого сообщения. Поэтому сначала пробуем строго локальный
        кэш и идём в сеть только тогда, когда модели действительно нет.
        """
        cuda.prepare()  # объявить папки с CUDA-DLL до того, как CTranslate2 полезет за ними
        from faster_whisper import WhisperModel

        root = self.cfg.models_dir  # None = кэш HuggingFace по умолчанию
        kwargs = dict(device=device, compute_type=self.compute_type or self.cfg.compute_type,
                      download_root=str(root) if root else None)
        try:
            return WhisperModel(self.model_size, local_files_only=True, **kwargs)
        except Exception as e:
            log.info("Модели %s нет в локальном кэше (%s), скачиваю", self.model_size, e)
            self.on_status(f"Скачиваю модель «{self.model_size}» (один раз)...")
            return WhisperModel(self.model_size, **kwargs)

    def _build(self, device):
        """Собрать модель на видеокарте, а если та только притворяется рабочей — на процессоре.

        Видеокарта видна, но CUDA-библиотек нет — обычная ситуация на Windows.
        Проверяем один раз на тишине, чтобы сказать об этом понятным текстом здесь,
        а не упасть в середине распознавания с ошибкой про cublas64_12.dll.

        Откат на процессор молчаливый по умолчанию: на чужой машине без видеокарты
        программа должна просто работать, а не пугать пользователя.
        """
        global _gpu_unusable
        import numpy as np

        if device != "cpu" and _gpu_unusable:
            # Уже выяснили, что CUDA тут не работает. Вместе с устройством надо сменить
            # и точность: float16 подбирался под видеокарту, а на процессоре
            # CTranslate2 на нём падает с «do not support efficient float16».
            device = "cpu"
            self.compute_type = cuda._compute("cpu", self.cfg.compute_type)
        try:
            model = self._open(device)
            if device == "cpu":
                cuda.remember("cpu")
                return model
            # прогон по тишине: DLL могут найтись, но не загрузиться из-за версий
            list(model.transcribe(np.zeros(16000, dtype=np.float32))[0])
            cuda.remember("cuda")
            return model
        except Exception as e:
            if device == "cpu":
                raise
            _gpu_unusable = True
            self.fallback_reason = str(e).strip().splitlines()[0][:200]
            # точность подбиралась под видеокарту: на процессоре float16 не к месту
            self.compute_type = cuda._compute("cpu", self.cfg.compute_type)
            log.warning("GPU недоступен (%s), перехожу на процессор (%s)",
                        self.fallback_reason, self.compute_type)
            self.on_status("Видеокарта недоступна, работаю на процессоре.")
            return self._build("cpu")

    def transcribe_array(self, audio, language=None, hotwords=None):
        """Фраза с микрофона -> (текст, код языка).

        hotwords — список часто используемых слов и имён; подсказка модели, чтобы она
        реже коверкала редкие имена собственные (см. voicetool.vocabulary).
        """
        segments, info = self.model.transcribe(
            audio, language=language, beam_size=self.cfg.beam_size,
            vad_filter=True, condition_on_previous_text=False,
            **self._hint_kwargs(hotwords),
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language

    def transcribe_wake(self, audio, language=None, beam_size=1):
        """Быстрый проход ради одного слова: «Алиса» тут или нет.

        Отличия от обычного распознавания — ради задержки, и каждое осознанно:
          * beam_size=1 (жадный поиск): для одного слова перебор лучей ничего не даёт,
            а времени съедает в разы больше;
          * vad_filter выключен — Recorder уже отрезал тишину по краям, второй VAD
            это лишний прогон нейросети на каждое покашливание;
          * язык берётся из настроек, а не определяется: на фрагменте в одну секунду
            автоопределение часто ошибается (у нас оно давало 'en' с уверенностью 0.38).
        """
        segments, info = self.model.transcribe(
            audio, language=language, beam_size=beam_size,
            vad_filter=False, condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language

    def _hint_kwargs(self, hotwords):
        """hotwords поддерживаются с faster-whisper 1.0.3; на старых версиях — initial_prompt."""
        if not hotwords:
            return {}
        phrase = hotwords if isinstance(hotwords, str) else ", ".join(hotwords)
        if not phrase.strip():
            return {}
        if _supports_hotwords():
            return {"hotwords": phrase}
        return {"initial_prompt": phrase}

    def transcribe_file(self, path, language=None, on_progress=None, should_stop=None,
                        chunk_seconds=None):
        """Файл любой длины -> {language, language_probability, duration, text, segments}.

        Файл читается кусками (см. media.audio_chunks), поэтому память не растёт с длиной
        записи. Язык определяется по первому куску и дальше фиксируется — иначе на длинной
        лекции Whisper может «передумать» посередине.

        on_progress(processed_seconds, total_seconds) вызывается после каждого сегмента,
        should_stop() -> True прерывает работу и возвращает то, что успели распознать.
        """
        chunk_seconds = chunk_seconds or self.cfg.chunk_seconds
        total = probe_duration(path)
        model = self.model
        parts, segments = [], []
        language_probability, detected = 0.0, language
        processed = 0.0

        for offset, audio in audio_chunks(path, self.cfg.sample_rate, chunk_seconds):
            if should_stop and should_stop():
                break
            chunk_segments, info = model.transcribe(
                audio, language=detected, beam_size=self.cfg.beam_size,
                vad_filter=True, condition_on_previous_text=False,
            )
            if detected is None:
                detected = info.language
                language_probability = info.language_probability or 0.0
            chunk_len = len(audio) / self.cfg.sample_rate
            total = max(total, offset + chunk_len)
            for seg in chunk_segments:
                text = seg.text.strip()
                if text:
                    parts.append(text)
                    segments.append({"start": offset + seg.start, "end": offset + seg.end,
                                     "text": text})
                if on_progress:
                    on_progress(min(total, offset + seg.end), total)
                if should_stop and should_stop():
                    break
            processed = offset + chunk_len
            del audio
            if on_progress:
                on_progress(min(total, processed), total)

        return {
            "language": detected,
            "language_probability": language_probability,
            "duration": total or processed,
            "text": " ".join(parts).strip(),
            "segments": segments,
        }
