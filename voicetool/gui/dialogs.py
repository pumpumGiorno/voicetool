"""Focused Stage 5 dialogs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

from .ui_state import concise_target
from .widgets import Button, label, section


class ConfirmationDialog(QDialog):
    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self.setWindowTitle("Подтверждение Alice")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)
        root.addWidget(section("HIGH-IMPACT ACTION"))
        root.addWidget(label("Подтвердите действие", name="H1"))
        root.addWidget(label(str(request.description), wrap=True))

        target = concise_target(getattr(request, "arguments", {}) or {})
        if target:
            root.addWidget(label(f"Цель: {target}", name="Muted", wrap=True))
        tool = str(getattr(request, "tool", "") or "")
        if tool:
            root.addWidget(label(f"Действие: {tool}", name="Dim", wrap=True))
        root.addWidget(label(
            "После подтверждения Alice выполнит это действие немедленно.",
            name="Danger", wrap=True))

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = Button("Отмена", variant="secondary")
        confirm = Button("Подтвердить", variant="danger")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        root.addLayout(buttons)
