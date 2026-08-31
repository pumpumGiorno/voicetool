"""Real Local AI and agent diagnostics page."""
from __future__ import annotations

import os
import platform

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import cuda
from ..agent.ollama import OllamaProvider
from .ui_state import EngineHealth, engine_health
from .widgets import Button, StatusIndicator, divider, label, section


class _Probe(QThread):
    completed = Signal(object, object, object)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        status = hardware = vision = None
        try:
            provider = OllamaProvider(self.cfg)
            status = provider.probe()
            vision = provider.vision_status()
        except Exception as exc:
            status = exc
        try:
            hardware = cuda.status()
        except Exception as exc:
            hardware = {"available": False, "reason": str(exc), "name": "", "devices": 0}
        self.completed.emit(status, hardware, vision)


class AgentPage(QWidget):
    settings_changed = Signal(dict)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._probe = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Agent", name="H1"))
        titles.addWidget(label("Локальный движок, ограничения и состояние", name="Muted"))
        head.addLayout(titles)
        head.addStretch()
        self.test_btn = Button("Проверить соединение", variant="secondary")
        self.test_btn.clicked.connect(self.refresh)
        head.addWidget(self.test_btn)
        root.addLayout(head)

        status_surface = QFrame()
        status_surface.setObjectName("QuietSurface")
        status_lay = QVBoxLayout(status_surface)
        status_lay.setContentsMargins(18, 16, 18, 16)
        status_lay.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(section("LOCAL AI"))
        top.addStretch()
        self.health = StatusIndicator("Проверка…", "info")
        top.addWidget(self.health)
        status_lay.addLayout(top)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(24)
        self.form.setVerticalSpacing(10)
        self.values = {}
        for key, title in (
            ("model", "Выбранная модель"), ("server", "Сервер"),
            ("hardware", "Оборудование"), ("vram", "VRAM"),
            ("context", "Контекст"), ("keep_alive", "Keep loaded"),
            ("vision", "Vision"), ("steps", "Max Agent Steps"),
        ):
            value = label("—", name="Muted", wrap=True)
            self.values[key] = value
            self.form.addRow(title, value)
        status_lay.addLayout(self.form)
        self.error = label("", name="Danger", wrap=True)
        status_lay.addWidget(self.error)
        root.addWidget(status_surface)

        config_surface = QFrame()
        config_surface.setObjectName("QuietSurface")
        config_lay = QVBoxLayout(config_surface)
        config_lay.setContentsMargins(18, 16, 18, 16)
        config_lay.setSpacing(12)
        config_lay.addWidget(section("AGENT LIMITS"))
        self.enabled = QCheckBox("Local Agent включён")
        self.strict = QCheckBox("Strict Local mode")
        self.vision = QCheckBox("Разрешить локальный Vision fallback")
        for widget in (self.enabled, self.strict, self.vision):
            config_lay.addWidget(widget)
        form = QFormLayout()
        self.model = QLineEdit()
        self.model.setPlaceholderText("qwen3.5:9b")
        self.context = QSpinBox()
        self.context.setRange(1024, 131072)
        self.context.setSingleStep(1024)
        self.steps = QSpinBox()
        self.steps.setRange(1, 32)
        form.addRow("Ollama model", self.model)
        form.addRow("Context", self.context)
        form.addRow("Max Agent Steps", self.steps)
        config_lay.addLayout(form)
        config_lay.addWidget(divider())
        actions = QHBoxLayout()
        actions.addStretch()
        save = Button("Сохранить", variant="primary")
        save.clicked.connect(self._save)
        actions.addWidget(save)
        config_lay.addLayout(actions)
        root.addWidget(config_surface)
        root.addStretch()
        self._load()

    def _load(self):
        self.enabled.setChecked(bool(self.cfg.get("agent_enabled", True)))
        self.strict.setChecked(bool(self.cfg.get("strict_local_ai", True)))
        self.vision.setChecked(bool(self.cfg.get("vision_enabled", True)))
        self.model.setText(str(self.cfg.get("ollama_model", "qwen3.5:9b")))
        self.context.setValue(int(self.cfg.get("ollama_context", 8192)))
        self.steps.setValue(int(self.cfg.get("max_agent_steps", 8)))

    def refresh(self):
        if self._probe and self._probe.isRunning():
            return
        self.test_btn.set_loading(True, "Проверяю…")
        self.health.set_state("info", "Проверка…")
        self.error.clear()
        self._probe = _Probe(self.cfg, self)
        self._probe.completed.connect(self._show_probe)
        self._probe.start()

    def set_agent_state(self, state):
        value = str(getattr(state, "value", state) or "")
        if value in {"understanding", "processing", "executing", "waiting_confirmation"}:
            self.health.set_state("accent", "Running")
        elif value == "success":
            self.health.set_state("success", "Ready")

    def _show_probe(self, status, hardware, vision):
        self.test_btn.set_loading(False, "Проверить соединение")
        if isinstance(status, Exception):
            health = EngineHealth.ERROR
            message = str(status)
            connected = installed = False
            model = str(self.cfg.get("ollama_model", "—"))
            server = str(self.cfg.get("ollama_url", "—"))
        else:
            health = engine_health(status)
            message = str(getattr(status, "error", "") or "")
            connected = bool(getattr(status, "connected", False))
            installed = bool(getattr(status, "installed", False))
            model = str(getattr(status, "model", "—"))
            server = str(getattr(status, "server", "—"))
        titles = {
            EngineHealth.READY: ("success", "Ready"),
            EngineHealth.RUNNING: ("success", "Running"),
            EngineHealth.OFFLINE: ("danger", "Offline"),
            EngineHealth.MODEL_MISSING: ("warning", "Model missing"),
            EngineHealth.DOWNLOADING: ("accent", "Downloading"),
            EngineHealth.ERROR: ("danger", "Error"),
        }
        tone, title = titles[health]
        self.health.set_state(tone, title)
        self.values["model"].setText(model + (" · установлена" if installed else ""))
        self.values["server"].setText(server + (" · доступен" if connected else ""))
        hw_name = str((hardware or {}).get("name") or "")
        hw_ok = bool((hardware or {}).get("available"))
        self.values["hardware"].setText(
            (hw_name or "CUDA GPU") if hw_ok else
            f"CPU · {(hardware or {}).get('reason') or platform.processor() or 'система'}")
        self.values["vram"].setText(_vram_text())
        self.values["context"].setText(str(self.cfg.get("ollama_context", 8192)))
        self.values["keep_alive"].setText(str(self.cfg.get("ollama_keep_alive", "10m")))
        if isinstance(vision, tuple):
            self.values["vision"].setText("Ready" if vision[0] else f"Недоступен · {vision[1]}")
        else:
            self.values["vision"].setText("Не проверен")
        self.values["steps"].setText(str(self.cfg.get("max_agent_steps", 8)))
        self.error.setText(message)

    def _save(self):
        values = {
            "agent_enabled": self.enabled.isChecked(),
            "strict_local_ai": self.strict.isChecked(),
            "vision_enabled": self.vision.isChecked(),
            "ollama_model": self.model.text().strip() or "qwen3.5:9b",
            "ollama_context": self.context.value(),
            "max_agent_steps": self.steps.value(),
        }
        changed = {key: value for key, value in values.items()
                   if self.cfg.get(key) != value}
        if not changed:
            return
        self.cfg.update(values)
        self.cfg.save()
        self.settings_changed.emit(changed)
        self.refresh()


def _vram_text():
    """Report only observed VRAM; never invent a hardware value."""
    try:
        import shutil
        import subprocess

        exe = shutil.which("nvidia-smi")
        if not exe:
            return "Не определено"
        result = subprocess.run(
            [exe, "--query-gpu=memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4, check=False,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else "Не определено"
    except (OSError, IndexError, subprocess.SubprocessError):
        return "Не определено"
