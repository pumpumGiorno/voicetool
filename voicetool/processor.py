"""Разбор аудио/видео файлов: очередь, прогресс, перевод, сохранение результата.

Работает в отдельном потоке, наружу отдаёт события колбэками — как и Listener.
Текст из файлов НИКОГДА не попадает в счётчик слов: счётчик считает только живой режим.
В историю такие записи пишутся с kind="file", чтобы их было видно отдельно.
"""
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .counter import save_transcript
from .history import History
from .subtitles import to_srt, to_vtt
from .text import count_words
from .translate import get_translator, lang_name

log = logging.getLogger(__name__)

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".aiff", ".alac"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".ts"}
SUPPORTED = AUDIO_EXT | VIDEO_EXT

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"


def is_supported(path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED


@dataclass
class Job:
    path: Path
    language_hint: str | None = None
    status: str = QUEUED
    progress: float = 0.0        # 0..1
    position: float = 0.0        # секунд обработано
    duration: float = 0.0
    language: str = ""
    language_probability: float = 0.0
    text: str = ""
    translation: str = ""
    segments: list = field(default_factory=list)
    error: str = ""
    saved_to: str = ""
    target: str = "ru"      # на какой язык переводим — для подписи в отчёте

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def words(self) -> int:
        return count_words(self.text)

    def report(self) -> str:
        """Текст, который сохраняем в файл и показываем в интерфейсе."""
        head = [f"Файл: {self.path}", f"Язык: {lang_name(self.language)} ({self.language})", ""]
        if self.translation:
            return "\n".join(head + ["Оригинал:", self.text, "",
                                     f"Перевод ({lang_name(self.target)}):", self.translation])
        return "\n".join(head + [self.text])

    def srt(self) -> str:
        return to_srt(self.segments)

    def vtt(self) -> str:
        return to_vtt(self.segments)


class BatchProcessor:
    """Очередь файлов. add() можно вызывать и во время работы — попадёт в хвост."""

    def __init__(self, cfg, asr_factory=None, events=None):
        self.cfg = cfg
        self.events = events or {}
        self.jobs = []
        self._asr_factory = asr_factory or self._default_asr
        self._asr = None
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = threading.Event()

    def _default_asr(self):
        from .asr import ASR

        return ASR(self.cfg, on_status=lambda m: self._emit("status", m))

    # --- очередь ------------------------------------------------------------

    def add(self, paths, language_hint=None):
        added = []
        with self._lock:
            known = {str(j.path) for j in self.jobs if j.status in (QUEUED, RUNNING)}
            for p in paths:
                p = Path(p).expanduser()
                if str(p) in known:
                    continue
                job = Job(path=p, target=self.cfg.translate_to,
                          language_hint=language_hint)
                if not p.exists():
                    job.status, job.error = FAILED, "Файл не найден"
                elif not is_supported(p):
                    job.status, job.error = FAILED, f"Формат {p.suffix or '(без расширения)'} не поддерживается"
                self.jobs.append(job)
                added.append(job)
        self._emit("queue", list(self.jobs))
        return added

    def clear_finished(self):
        with self._lock:
            self.jobs = [j for j in self.jobs if j.status in (QUEUED, RUNNING)]
        self._emit("queue", list(self.jobs))

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, name="voicetool-files", daemon=True)
        self._thread.start()

    def cancel(self, wait=0.0):
        self._cancel.set()
        return self.wait(wait) if wait and wait > 0 else not self.running

    def wait(self, timeout=None) -> bool:
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        stopped = not thread or not thread.is_alive()
        if stopped and self._thread is thread:
            self._thread = None
        if not stopped:
            log.warning("Обработчик файлов не завершился за %.1f с", float(timeout or 0))
        return stopped

    # --- работа -------------------------------------------------------------

    def _emit(self, name, *args):
        cb = self.events.get(name)
        if cb:
            try:
                cb(*args)
            except Exception:
                log.exception("Ошибка в обработчике события %s", name)

    def _next(self):
        with self._lock:
            for job in self.jobs:
                if job.status == QUEUED:
                    return job
        return None

    def _run(self):
        history = History(
            self.cfg.data_dir, enabled=bool(self.cfg.get("log_transcripts", True)))
        try:
            while not self._cancel.is_set():
                job = self._next()
                if job is None:
                    break
                job.status = RUNNING
                self._emit("job", job)
                try:
                    self._process(job, history)
                    job.status = CANCELLED if self._cancel.is_set() else DONE
                except (FileNotFoundError, RuntimeError, OSError) as e:
                    job.status, job.error = FAILED, str(e)
                    log.error("Файл %s: %s", job.name, e)
                except Exception as e:
                    job.status, job.error = FAILED, f"Непредвиденная ошибка: {e}"
                    log.exception("Файл %s не обработан", job.name)
                self._emit("job", job)
            # отменённые задачи из очереди убираем, чтобы очередь не «зависала»
            if self._cancel.is_set():
                with self._lock:
                    for job in self.jobs:
                        if job.status == QUEUED:
                            job.status = CANCELLED
                self._emit("queue", list(self.jobs))
        finally:
            self._emit("finished")

    def _process(self, job, history):
        if self._asr is None:
            self._asr = self._asr_factory()

        def progress(done, total):
            job.position, job.duration = done, total
            job.progress = min(1.0, done / total) if total else 0.0
            self._emit("progress", job)

        result = self._asr.transcribe_file(job.path, language=job.language_hint,
                                           on_progress=progress,
                                           should_stop=self._cancel.is_set,
                                           chunk_seconds=self.cfg.chunk_seconds)
        job.language = result["language"] or ""
        job.language_probability = float(result.get("language_probability") or 0.0)
        job.duration = result["duration"] or job.duration
        job.text = result["text"]
        job.segments = result["segments"]
        if not job.text:
            job.error = "Речь в файле не распознана"
            return

        if job.language and job.language != self.cfg.translate_to:
            job.translation = self._translate(job)

        if self.cfg.log_transcripts:
            job.saved_to = str(save_transcript(self.cfg.data_dir, str(job.path), job.report()))
        history.add(job.text[:400], kind="file", words=0,
                    source=job.name, language=job.language)
        log.info("Готово: %s (%s, %d слов)", job.name, job.language, job.words)

    def _translate(self, job):
        """Ошибка перевода не должна отменять удачную расшифровку.

        Перевод — приятное дополнение; текст, ради которого файл и обрабатывали, уже готов.
        Поэтому ловим здесь всё: нет модели, нет сети, битый архив — что угодно.
        """
        translator = get_translator(self.cfg)
        if translator is None:
            return ""
        try:
            return translator.translate(job.text, job.language)
        except Exception as e:
            log.warning("Перевод %s не выполнен: %s", job.name, e)
            job.error = f"Текст распознан, но перевести не удалось: {e}"
            self._emit("status", f"Перевод не выполнен: {e}")
            return ""
