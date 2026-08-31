"""Мост между рабочими потоками и Qt.

Listener и BatchProcessor живут в своих потоках и зовут обычные функции. Виджеты трогать
из чужого потока нельзя, поэтому колбэки превращаются здесь в сигналы: Qt сам перекинет
их в поток интерфейса (очередь событий).
"""
import threading

from PySide6.QtCore import QObject, Signal

from ..engine import Listener
from ..processor import BatchProcessor


class ListenerBridge(QObject):
    state = Signal(str, str)        # состояние, подробность
    level = Signal(float, bool)     # громкость, речь ли это
    recognized = Signal(str, int)   # текст, сколько слов добавлено
    inserted = Signal(str)
    counter = Signal(int, int)      # всего, добавлено
    status = Signal(str)
    error = Signal(str)
    agent_event = Signal(object)
    agent_result = Signal(object)
    confirmation = Signal(object)
    command_finished = Signal(object)

    def __init__(self, cfg, parent=None, source=None):
        super().__init__(parent)
        self.listener = Listener(cfg, source=source, events={
            "state": self.state.emit,
            "level": self.level.emit,
            "recognized": self.recognized.emit,
            "inserted": self.inserted.emit,
            "counter": self.counter.emit,
            "status": self.status.emit,
            "error": self.error.emit,
            "agent_event": self.agent_event.emit,
            "agent_result": self.agent_result.emit,
            "confirmation": self.confirmation.emit,
        })

    # тонкие обёртки, чтобы окна не лезли внутрь Listener
    def start(self):
        self.listener.start()

    def stop(self):
        self.listener.stop()

    def trigger(self):
        return self.listener.trigger()

    def set_paused(self, value):
        self.listener.set_paused(value)

    def execute_command(self, command):
        """Run a typed command off the Qt thread through the existing agent service."""
        text = str(command or "").strip()
        if not text:
            return

        def run():
            result = self.listener.execute_agent(text)
            self.command_finished.emit(result)

        threading.Thread(target=run, name="voicetool-ui-agent", daemon=True).start()

    def cancel_agent(self, reason="Остановлено пользователем"):
        return self.listener.cancel_agent(reason)

    def resolve_confirmation(self, approved):
        return self.listener.resolve_agent_confirmation(approved)

    def reset_agent(self):
        self.listener.reset_agent()

    @property
    def running(self):
        return self.listener.running

    @property
    def paused(self):
        return self.listener.paused

    def apply_config(self, cfg, source=None):
        """Настройки применяются к работающему слушателю перезапуском: модель могла смениться."""
        was_running = self.running
        if was_running:
            self.stop()
        self.listener = Listener(cfg, source=source or self.listener.source,
                                 events=self.listener.events)
        if was_running:
            self.start()


class ProcessorBridge(QObject):
    queue = Signal(list)
    job = Signal(object)
    progress = Signal(object)
    status = Signal(str)
    finished = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.processor = BatchProcessor(cfg, events={
            "queue": self.queue.emit,
            "job": self.job.emit,
            "progress": self.progress.emit,
            "status": self.status.emit,
            "finished": self.finished.emit,
        })

    def add(self, paths):
        return self.processor.add(paths)

    def start(self):
        self.processor.start()

    def cancel(self):
        self.processor.cancel()

    def clear_finished(self):
        self.processor.clear_finished()

    @property
    def jobs(self):
        return self.processor.jobs

    @property
    def running(self):
        return self.processor.running

    def apply_config(self, cfg):
        self.cfg = cfg
        self.processor.cfg = cfg
        self.processor._asr = None  # модель могла смениться — пересоздадим при следующем файле
