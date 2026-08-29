"""Фоновый слушатель: микрофон -> слово-триггер -> VAD -> Whisper -> вставка текста.

Живёт в отдельном потоке и ничего не знает про Qt: наружу отдаёт события через
колбэки, GUI переводит их в сигналы. Так же его можно использовать из CLI и из тестов.

Схема:

    Микрофон -> Recorder (VAD по громкости) -> Whisper (короткая фраза)
             -> поиск «Алиса» -> Recorder ещё раз -> Whisper -> текст
             -> счётчик слов + история + вставка в активное приложение

Слово-триггер ищется полноценным Whisper, а не отдельным детектором: готовой офлайн-модели
на слово «Алиса» нет, а обучать свою — это отдельный проект и лишние зависимости.
Кто хочет сэкономить процессор, ставит "wake_model": "tiny" — тогда фраза-триггер
слушается маленькой моделью, а сама команда — основной.
"""
import logging
import queue
import threading
import time

from .audio import Recorder, file_frames, mic_frames
from .counter import WordCounter, append_log
from .history import History
from .text import count_words, find_wake_word, strip_trigger
from .vocabulary import Vocabulary

log = logging.getLogger(__name__)

# состояния, которые понимает интерфейс
IDLE = "idle"          # микрофон выключен
WAITING = "waiting"    # ждём слово-триггер
WAKE = "wake"          # услышали «Алиса»
RECORDING = "recording"
THINKING = "thinking"  # идёт распознавание
DONE = "done"
PAUSED = "paused"


class Listener:
    """Один поток прослушивания. start()/stop()/pause() безопасны из GUI-потока."""

    def __init__(self, cfg, events=None, source=None):
        self.cfg = cfg
        self.source = source            # путь к файлу вместо микрофона (демо и тесты)
        self.events = events or {}
        self.counter = WordCounter(cfg.data_dir)
        self.history = History(cfg.data_dir)
        self.vocabulary = Vocabulary(cfg.data_dir)

        self._thread = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._force = threading.Event()  # горячая клавиша: слушать команду без слова-триггера
        self._asr = None
        self._wake_asr = None
        self.state = IDLE
        # голосовой агент («Алиса, сделай …») — живёт в своём потоке, слушатель
        # продолжает работать и передаёт ему стоп-слово и фразу подтверждения
        self._agent = None
        self._agent_thread = None
        self._agent_answers = queue.Queue()

    # --- управление ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, name="voicetool-listener", daemon=True)
        self._thread.start()

    def stop(self, wait=6.0):
        """wait с запасом: поток может доигрывать фразу, а микрофон нужно освободить
        до того, как его откроет новый слушатель."""
        self._stop.set()
        if self._agent is not None:
            self._agent.stop()
            self._agent_answers.put("")  # разблокировать возможное ожидание подтверждения
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=wait)
        self._thread = None
        self._emit_state(IDLE)

    def set_paused(self, value: bool):
        self._paused.set() if value else self._paused.clear()
        if self.running:
            self._emit_state(PAUSED if value else WAITING)

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def trigger(self):
        """Ручная активация (горячая клавиша или кнопка): слушаем команду сразу."""
        if not self.running:
            return False
        self._force.set()
        return True

    # --- события ------------------------------------------------------------

    def _emit(self, name, *args):
        cb = self.events.get(name)
        if cb:
            try:
                cb(*args)
            except Exception:
                log.exception("Ошибка в обработчике события %s", name)

    def _emit_state(self, state, detail=""):
        self.state = state
        self._emit("state", state, detail)

    # --- главный цикл -------------------------------------------------------

    def _interrupt(self):
        return self._stop.is_set() or self._paused.is_set() or self._force.is_set()

    def _run(self):
        try:
            self._loop()
        except Exception as e:
            log.exception("Прослушивание остановлено из-за ошибки")
            self._emit("error", str(e))
        finally:
            self._emit_state(IDLE)

    def _loop(self):
        from .asr import ASR

        self._asr = ASR(self.cfg, on_status=lambda m: self._emit("status", m))
        wake_size = (self.cfg.wake_model or "").strip()
        self._wake_asr = ASR(self.cfg, wake_size, on_status=lambda m: self._emit("status", m)) \
            if wake_size and wake_size != self.cfg.model else self._asr

        self._emit_state(THINKING, "Загрузка модели")
        self._asr.model            # греем заранее, иначе потеряем первую фразу
        self._wake_asr.model
        if self.cfg.get("use_vocabulary", True):
            self.vocabulary.ensure_file()
            log.info("Словарь подсказок: %d записей", len(self.vocabulary))
        self.counter.reload().start_session()
        self._emit("counter", self.counter.total, 0)

        wake_variants = [self.cfg.wake_word] + list(self.cfg.wake_word_aliases)
        frames_ctx = _source_context(self.cfg, self.source)
        with frames_ctx as frames:
            rec = Recorder(frames, self.cfg,
                           on_level=lambda lvl, loud: self._emit("level", lvl, loud),
                           interrupt=self._interrupt)
            if self.source:
                rec.threshold = self.cfg.energy_threshold or self.cfg.min_energy
            else:
                self._emit_state(THINKING, "Калибровка микрофона")
                threshold = rec.calibrate()
                log.info("Порог громкости: %.4f", threshold)
            self._emit_state(WAITING)

            while not self._stop.is_set():
                if self._paused.is_set() and not self._force.is_set():
                    time.sleep(0.1)
                    continue
                if self._force.is_set():
                    self._force.clear()
                    self._emit_state(WAKE, "Горячая клавиша")
                    self._handle_command(rec, wake_window=_active_window(self.cfg))
                    if self._stop.is_set():
                        break
                    self._emit_state(WAITING if not self._paused.is_set() else PAUSED)
                    continue

                audio = rec.record_utterance(
                    self.cfg.wake_silence_seconds,
                    max_seconds=self.cfg.get("wake_max_seconds", 5.0))
                if audio is None:
                    break                      # источник кадров кончился (файл)
                if not len(audio) or self._interrupt():
                    continue

                heard, _ = self._wake_asr.transcribe_wake(
                    audio, self.cfg.language_hint, beam_size=self.cfg.wake_beam_size)
                hit, tail = find_wake_word(heard, wake_variants)
                if not hit:
                    if heard:
                        log.debug("Мимо: %s", heard)
                    continue

                log.info("Слово-триггер: %r", heard)
                # окно запоминаем СРАЗУ: пока идёт распознавание, пользователь может уйти в другое
                wake_window = _active_window(self.cfg)
                self._emit_state(WAKE, heard)
                self._handle_command(rec, tail=tail, wake_window=wake_window)
                if self._stop.is_set():
                    break
                self._emit_state(WAITING if not self._paused.is_set() else PAUSED)

    def _handle_command(self, rec, tail="", wake_window=None):
        command = tail
        if not command:
            self._emit_state(RECORDING)
            audio = rec.record_utterance(self.cfg.silence_seconds)
            if audio is None or self._stop.is_set():
                return
            if not len(audio):
                self._emit_state(DONE, "")
                return
            self._emit_state(THINKING)
            command, _ = self._asr.transcribe_array(audio, self.cfg.language_hint,
                                                    hotwords=self._hotwords())

        command = (command or "").strip()
        if not command:
            log.info("После слова-триггера ничего не распознано")
            self._emit_state(DONE, "")
            return

        if self._route_to_agent(command, wake_window):
            return

        log.info("Распознано: %s", command)
        added = self.counter.add(command)
        self.history.add(command, kind="voice", words=added)
        if self.cfg.log_transcripts:
            append_log(self.cfg.data_dir, command)
        self._emit("counter", self.counter.total, added)
        self._emit("recognized", command, added)
        self._emit_state(DONE, command)

        if self.cfg.output_mode in ("insert", "insert_show"):
            self._insert(command, wake_window)

    # --- голосовой агент ------------------------------------------------------

    def _agent_busy(self) -> bool:
        return self._agent_thread is not None and self._agent_thread.is_alive()

    def _route_to_agent(self, command, wake_window) -> bool:
        """True — фраза относится к агенту (стоп, подтверждение или новая команда)."""
        from . import agent as agent_mod

        if self._agent_busy():
            if agent_mod.is_stop_phrase(command, self.cfg):
                log.info("Стоп-слово: прерываю агента")
                self._agent.stop()
                self._agent_answers.put("")   # разблокировать ожидание подтверждения
                self._emit("agent", "stopped", "Остановлено стоп-словом")
                self._emit_state(DONE, "агент остановлен")
                return True
            # агент ждёт подтверждения? любая фраза — ответ ему
            self._agent_answers.put(command)
            self._emit_state(DONE, command)
            return True

        if not self.cfg.get("agent_enabled", True):
            return False
        triggers = [self.cfg.get("agent_trigger", "сделай")] + \
            list(self.cfg.get("agent_trigger_aliases", []))
        task = strip_trigger(command, triggers)
        if task is None:
            return False
        if not task:
            self._emit("agent", "failed", "После «сделай» не услышал, что именно сделать")
            self._emit_state(DONE, command)
            return True

        self.history.add(command, kind="voice", words=0)
        self._start_agent(task)
        self._emit_state(DONE, command)
        return True

    def _start_agent(self, task):
        from . import agent as agent_mod, computer

        problems = computer.check_requirements(self.cfg)
        if problems:
            for p in problems:
                log.warning("Агент недоступен: %s", p)
            self._emit("agent", "failed", " ".join(problems))
            return

        # очистить очередь ответов от мусора прошлых запусков
        while not self._agent_answers.empty():
            try:
                self._agent_answers.get_nowait()
            except queue.Empty:
                break

        def confirm() -> bool:
            """Ждать голосовое подтверждение: следующая распознанная фраза — ответ."""
            timeout = float(self.cfg.get("agent_confirm_timeout", 30))
            try:
                answer = self._agent_answers.get(timeout=timeout)
            except queue.Empty:
                return False
            return agent_mod.is_confirm_phrase(answer, self.cfg)

        self._agent = agent_mod.Agent(
            self.cfg,
            on_event=lambda stage, text: self._emit("agent", stage, text),
            confirm=confirm)
        self._agent_thread = threading.Thread(
            target=self._agent.run, args=(task,), name="voicetool-agent", daemon=True)
        self._agent_thread.start()
        log.info("Агент запущен: %s", task)

    def _hotwords(self):
        """Подсказка модели из пользовательского словаря — или ничего."""
        if not self.cfg.get("use_vocabulary", True):
            return ""
        return self.vocabulary.hint()

    def _insert(self, text, wake_window):
        """Набрать текст в чужом приложении. Буфер обмена не используется."""
        from . import inject

        try:
            target = wake_window if self.cfg.insert_into_wake_window else None
            inject.type_text(text, hwnd=target, press_enter=bool(self.cfg.press_enter),
                             pause=max(0, int(self.cfg.get("type_delay_ms", 10))) / 1000)
            self._emit("inserted", text)
        except Exception as e:
            log.exception("Не удалось набрать текст")
            # запасного пути через буфер обмена намеренно нет — говорим прямо
            self._emit("error", f"Текст распознан, но ввести его в приложение не удалось: {e}")


def _active_window(cfg):
    """HWND окна, активного прямо сейчас. Нужен, только если вставляем в него же."""
    if not cfg.insert_into_wake_window or cfg.output_mode == "show":
        return None
    from . import inject

    hwnd = inject.foreground_window()
    if hwnd:
        log.info("Целевое окно: %r", inject.window_title(hwnd))
    return hwnd


def _source_context(cfg, source):
    import contextlib

    if source:
        return contextlib.nullcontext(file_frames(source, cfg.sample_rate))
    return mic_frames(cfg)


def preview_words(text) -> int:
    return count_words(text)
