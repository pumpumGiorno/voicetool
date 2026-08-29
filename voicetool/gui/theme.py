"""Тёмная тема: почти чёрный фон, оранжевый акцент, серый второстепенный текст.

Палитра лежит в одном месте, чтобы оттенки не расползались по виджетам.
"""

BG = "#0A0A0C"          # фон окна
SURFACE = "#131318"     # карточки
SURFACE_2 = "#1B1B22"   # поля ввода, вложенные блоки
SURFACE_3 = "#24242C"   # ховер
BORDER = "#26262F"
TEXT = "#F2F2F5"
MUTED = "#8B8B99"
DIM = "#5A5A66"

ACCENT = "#FF7A18"      # основной оранжевый
ACCENT_HOVER = "#FF9038"
ACCENT_PRESSED = "#E5670C"
ACCENT_SOFT = "rgba(255, 122, 24, 0.14)"
ACCENT_LINE = "rgba(255, 122, 24, 0.35)"

OK = "#4CC38A"
WARN = "#E0A32E"
FAIL = "#E5484D"

RADIUS = 12
FONT = "'Segoe UI', 'Inter', system-ui, sans-serif"

STATUS_COLOR = {"ok": OK, "warn": WARN, "fail": FAIL, "info": MUTED}


def stylesheet() -> str:
    return f"""
* {{
    font-family: {FONT};
    color: {TEXT};
    outline: none;
}}
QWidget#Root {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: {RADIUS + 2}px;
}}
QWidget#TitleBar {{
    background: transparent;
    border-bottom: 1px solid {BORDER};
}}
QLabel#AppTitle {{
    font-size: 13px; font-weight: 600; letter-spacing: 2px; color: {TEXT};
}}
QLabel#Muted, QLabel[muted="true"] {{ color: {MUTED}; font-size: 12px; }}
QLabel#Dim {{ color: {DIM}; font-size: 11px; }}
QLabel#H1 {{ font-size: 22px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#SectionTitle {{
    font-size: 11px; font-weight: 700; letter-spacing: 1.4px; color: {MUTED};
}}
QLabel#Accent {{ color: {ACCENT}; }}

/* --- кнопки окна --- */
QPushButton#WinBtn, QPushButton#WinBtnClose {{
    background: transparent; border: none; border-radius: 6px;
    font-size: 14px; color: {MUTED}; min-width: 36px; min-height: 28px; padding: 0;
}}
QPushButton#WinBtn:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#WinBtn:pressed {{ background: {SURFACE_2}; }}
QPushButton#WinBtnClose:hover {{ background: {FAIL}; color: #FFFFFF; }}
QPushButton#WinBtnClose:pressed {{ background: #B4363A; color: #FFFFFF; }}

/* --- навигация --- */
QWidget#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QPushButton#NavBtn {{
    background: transparent; border: none; border-radius: 9px;
    padding: 10px 14px; text-align: left; font-size: 13px; color: {MUTED};
}}
QPushButton#NavBtn:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#NavBtn:checked {{
    background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600;
}}

/* --- карточки --- */
QFrame#Card {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
}}
QFrame#Inner {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px;
}}
QFrame#DropZone {{
    background: {SURFACE}; border: 2px dashed {BORDER}; border-radius: {RADIUS}px;
}}
QFrame#DropZoneActive {{
    background: {ACCENT_SOFT}; border: 2px dashed {ACCENT}; border-radius: {RADIUS}px;
}}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; border: none; }}

/* --- кнопки --- */
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 16px; font-size: 13px; color: {TEXT};
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {ACCENT_LINE}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {BORDER}; background: {SURFACE}; }}
QPushButton#Primary {{
    background: {ACCENT}; border: none; color: #14100C; font-weight: 600;
    padding: 10px 20px;
}}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#Primary:disabled {{ background: {SURFACE_3}; color: {DIM}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid {BORDER}; }}
QPushButton#Ghost:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#Link {{
    background: transparent; border: none; color: {ACCENT}; padding: 2px 4px;
    text-align: left; font-size: 12px;
}}
QPushButton#Link:hover {{ color: {ACCENT_HOVER}; text-decoration: underline; }}

/* --- поля --- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 10px; font-size: 13px; selection-background-color: {ACCENT};
    selection-color: #14100C;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {MUTED}; width: 0; height: 0; margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT}; padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}

/* --- чекбоксы --- */
QCheckBox {{ font-size: 13px; spacing: 10px; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid {BORDER}; background: {SURFACE_2};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT_LINE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton {{ font-size: 13px; spacing: 10px; padding: 4px 0; }}
QRadioButton::indicator {{
    width: 16px; height: 16px; border-radius: 9px;
    border: 1px solid {BORDER}; background: {SURFACE_2};
}}
QRadioButton::indicator:checked {{
    border: 5px solid {ACCENT}; border-radius: 8px; background: {BG};
}}

/* --- прогресс --- */
QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 5px; height: 8px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

/* --- списки и таблицы --- */
QListWidget, QTreeWidget, QTableWidget {{
    background: transparent; border: none; font-size: 13px;
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}

/* --- скроллбар --- */
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 5px; min-width: 30px; }}

/* --- меню трея --- */
QMenu {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px;
}}
QMenu::item {{ padding: 8px 22px 8px 14px; border-radius: 7px; font-size: 13px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 10px; }}
QToolTip {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px;
}}
"""
