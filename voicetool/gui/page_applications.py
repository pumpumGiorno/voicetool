"""Detected applications and persistent aliases backed by Stage 2 AppResolver."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..agent.desktop import AppResolver
from .widgets import Button, divider, label, section


class ApplicationsPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.resolver = AppResolver(
            cfg.data_dir, cache_seconds=cfg.get("app_resolver_cache_seconds", 180))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Applications", name="H1"))
        titles.addWidget(label("Обнаруженные приложения и ваши голосовые имена", name="Muted"))
        head.addLayout(titles)
        head.addStretch()
        refresh = Button("Обновить", variant="secondary")
        refresh.clicked.connect(lambda: self.refresh(force=True))
        head.addWidget(refresh)
        root.addLayout(head)

        aliases = QFrame()
        aliases.setObjectName("QuietSurface")
        alias_lay = QVBoxLayout(aliases)
        alias_lay.setContentsMargins(18, 16, 18, 16)
        alias_lay.setSpacing(12)
        alias_lay.addWidget(section("ALIASES"))
        add = QHBoxLayout()
        self.alias = QLineEdit()
        self.alias.setPlaceholderText("Например: телега")
        self.alias.setAccessibleName("Имя приложения для Alice")
        self.target = QLineEdit()
        self.target.setPlaceholderText("Telegram или путь к приложению")
        self.target.setAccessibleName("Целевое приложение")
        add.addWidget(self.alias, 1)
        add.addWidget(self.target, 2)
        add_btn = Button("Добавить", variant="primary")
        add_btn.clicked.connect(self._add_alias)
        add.addWidget(add_btn)
        alias_lay.addLayout(add)
        self.alias_list = QListWidget()
        self.alias_list.setMaximumHeight(150)
        alias_lay.addWidget(self.alias_list)
        remove = Button("Удалить выбранный alias", variant="ghost")
        remove.clicked.connect(self._remove_alias)
        alias_lay.addWidget(remove, 0, Qt.AlignRight)
        root.addWidget(aliases)

        apps = QFrame()
        apps.setObjectName("QuietSurface")
        apps_lay = QVBoxLayout(apps)
        apps_lay.setContentsMargins(18, 16, 18, 16)
        apps_lay.setSpacing(10)
        bar = QHBoxLayout()
        bar.addWidget(section("DETECTED APPLICATIONS"))
        bar.addStretch()
        self.count = label("", name="Dim")
        bar.addWidget(self.count)
        apps_lay.addLayout(bar)
        apps_lay.addWidget(divider())
        self.app_list = QListWidget()
        self.app_list.setAlternatingRowColors(True)
        apps_lay.addWidget(self.app_list)
        self.empty = label(
            "Приложения не найдены. На Windows список появится после обновления.",
            name="Muted", wrap=True)
        apps_lay.addWidget(self.empty)
        root.addWidget(apps, 1)

    def refresh(self, force=False):
        self._load_aliases()
        self.app_list.clear()
        try:
            candidates = self.resolver.candidates(refresh=force)
        except Exception as exc:
            self.empty.setText(f"Не удалось просканировать приложения: {exc}")
            self.empty.show()
            self.count.setText("Ошибка")
            return
        candidates = sorted(candidates, key=lambda item: (item.name.casefold(), item.source))
        for candidate in candidates[:400]:
            title = candidate.name or candidate.process_name or "Приложение"
            target = str(candidate.target)
            item = QListWidgetItem(f"{title}    ·    {candidate.source}\n{target}")
            item.setToolTip(target)
            self.app_list.addItem(item)
        self.count.setText(f"{len(candidates)} найдено")
        self.empty.setVisible(not candidates)

    def _load_aliases(self):
        self.alias_list.clear()
        for alias, target in sorted(self.resolver.aliases().items()):
            item = QListWidgetItem(f"{alias}  →  {target}")
            item.setData(Qt.UserRole, alias)
            self.alias_list.addItem(item)

    def _add_alias(self):
        alias = self.alias.text().strip()
        target = self.target.text().strip()
        try:
            self.resolver.set_alias(alias, target)
        except ValueError as exc:
            QMessageBox.warning(self, "Alias приложения", str(exc))
            return
        self.alias.clear()
        self.target.clear()
        self.refresh()

    def _remove_alias(self):
        item = self.alias_list.currentItem()
        if item and self.resolver.remove_alias(item.data(Qt.UserRole)):
            self.refresh()
