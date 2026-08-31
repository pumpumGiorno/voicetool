"""Central semantic design system for the Alice desktop UI."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    background_primary: str = "#0B0C0F"
    background_secondary: str = "#0F1115"
    surface_primary: str = "#15171C"
    surface_secondary: str = "#1B1E24"
    surface_hover: str = "#22262D"
    surface_active: str = "#292D35"
    border_subtle: str = "#292D34"
    border_focus: str = "#FF8126"
    text_primary: str = "#F3F4F6"
    text_secondary: str = "#B4B8C2"
    text_muted: str = "#777D89"
    accent_primary: str = "#FF7A1A"
    accent_hover: str = "#FF913D"
    accent_pressed: str = "#E8660E"
    accent_glow: str = "rgba(255, 122, 26, 0.22)"
    success: str = "#48C78E"
    warning: str = "#DDA63A"
    danger: str = "#EF5A61"


PALETTE = Palette()
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20,
           "2xl": 24, "3xl": 32, "4xl": 40, "5xl": 48}
RADIUS_SCALE = {"small": 6, "medium": 10, "large": 16}
MOTION = {"micro": 130, "regular": 200, "large": 300}
TYPE_SCALE = {"display": 28, "heading": 20, "section": 14,
              "body": 13, "secondary": 12, "caption": 11}
FONT = "'Segoe UI Variable', 'Segoe UI', sans-serif"

# Compatibility aliases for the pre-Stage-5 file/statistics views.
BG = PALETTE.background_primary
SURFACE = PALETTE.surface_primary
SURFACE_2 = PALETTE.surface_secondary
SURFACE_3 = PALETTE.surface_hover
SURFACE_ACTIVE = PALETTE.surface_active
BORDER = PALETTE.border_subtle
TEXT = PALETTE.text_primary
MUTED = PALETTE.text_secondary
DIM = PALETTE.text_muted
ACCENT = PALETTE.accent_primary
ACCENT_HOVER = PALETTE.accent_hover
ACCENT_PRESSED = PALETTE.accent_pressed
ACCENT_SOFT = "rgba(255, 122, 26, 0.11)"
ACCENT_LINE = "rgba(255, 122, 26, 0.38)"
OK = PALETTE.success
WARN = PALETTE.warning
FAIL = PALETTE.danger
RADIUS = RADIUS_SCALE["medium"]
STATUS_COLOR = {"ok": OK, "warn": WARN, "fail": FAIL, "info": MUTED}


def state_color(state: str) -> str:
    state = str(state or "").casefold()
    return {"success": OK, "error": FAIL, "cancelled": DIM,
            "waiting_confirmation": WARN}.get(state, ACCENT)


def stylesheet() -> str:
    """Application stylesheet derived from the semantic palette."""
    return f"""
* {{ font-family: {FONT}; color: {TEXT}; outline: none; }}
QWidget#Root {{
    background: {BG}; border: 1px solid {BORDER};
    border-radius: {RADIUS_SCALE['large']}px;
}}
QWidget#TitleBar {{
    background: {PALETTE.background_secondary}; border-bottom: 1px solid {BORDER};
}}
QLabel#AppTitle {{ font-size: 12px; font-weight: 600; letter-spacing: 1.6px; }}
QLabel#Display {{ font-size: {TYPE_SCALE['display']}px; font-weight: 600; }}
QLabel#H1 {{ font-size: {TYPE_SCALE['heading']}px; font-weight: 600; }}
QLabel#H2 {{ font-size: {TYPE_SCALE['section']}px; font-weight: 600; }}
QLabel#SectionTitle {{
    font-size: 11px; font-weight: 600; letter-spacing: 1.2px; color: {MUTED};
}}
QLabel#Muted, QLabel[muted="true"] {{ color: {MUTED}; font-size: 12px; }}
QLabel#Dim {{ color: {DIM}; font-size: 11px; }}
QLabel#Accent {{ color: {ACCENT}; }}
QLabel#Danger {{ color: {FAIL}; }}

QPushButton#WinBtn, QPushButton#WinBtnClose {{
    background: transparent; border: none; border-radius: 6px;
    font-size: 13px; color: {MUTED}; min-width: 36px; min-height: 28px; padding: 0;
}}
QPushButton#WinBtn:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#WinBtn:pressed {{ background: {SURFACE_2}; }}
QPushButton#WinBtnClose:hover {{ background: {FAIL}; color: white; }}

QWidget#Sidebar {{
    background: {PALETTE.background_secondary}; border-right: 1px solid {BORDER};
}}
QPushButton#NavBtn {{
    background: transparent; border: none; border-radius: 7px; min-height: 38px;
    padding: 0 12px 0 18px; text-align: left; font-size: 13px; color: {MUTED};
}}
QPushButton#NavBtn:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#NavBtn:checked {{
    background: {SURFACE_2}; color: {TEXT}; font-weight: 600;
}}

QFrame#Card {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
}}
QFrame#QuietSurface {{ background: {SURFACE}; border: none; border-radius: {RADIUS}px; }}
QFrame#Inner {{ background: {SURFACE_2}; border: none; border-radius: {RADIUS}px; }}
QFrame#DropZone {{
    background: {SURFACE}; border: 1px dashed {BORDER}; border-radius: {RADIUS}px;
}}
QFrame#DropZoneActive {{
    background: {ACCENT_SOFT}; border: 1px dashed {ACCENT}; border-radius: {RADIUS}px;
}}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; border: none; }}

QPushButton {{
    min-height: 36px; background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 0 16px; font-size: 13px; color: {TEXT};
}}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed {{ background: {SURFACE_ACTIVE}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{
    color: {DIM}; background: {SURFACE}; border-color: {BORDER};
}}
QPushButton#Primary {{
    background: {ACCENT}; border-color: {ACCENT}; color: #17110C; font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER};
}}
QPushButton#Primary:pressed {{
    background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED};
}}
QPushButton#Secondary {{ background: {SURFACE_2}; border-color: {BORDER}; }}
QPushButton#Ghost {{
    background: transparent; border-color: transparent; color: {MUTED};
}}
QPushButton#Ghost:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#Danger {{
    background: {FAIL}; border-color: {FAIL}; color: white; font-weight: 600;
}}
QPushButton#Danger:hover {{ background: #FA7077; border-color: #FA7077; }}
QPushButton#IconButton {{
    min-width: 36px; max-width: 36px; padding: 0;
    background: transparent; border-color: transparent;
}}
QPushButton#IconButton:hover {{ background: {SURFACE_2}; }}
QPushButton#Link {{
    background: transparent; border: none; color: {ACCENT};
    padding: 0 4px; min-height: 26px; text-align: left;
}}
QPushButton#Link:hover {{ color: {ACCENT_HOVER}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    min-height: 36px; background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 0 11px; font-size: 13px;
    selection-background-color: {ACCENT}; selection-color: #17110C;
}}
QPlainTextEdit, QTextEdit {{ padding: 10px; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: #3A3F49;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {MUTED}; margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER};
    selection-background-color: {SURFACE_3}; selection-color: {TEXT}; padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px; border: none;
}}

QCheckBox, QRadioButton {{ font-size: 13px; spacing: 10px; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid {BORDER}; background: {SURFACE_2};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT_LINE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator {{
    width: 16px; height: 16px; border-radius: 9px;
    border: 1px solid {BORDER}; background: {SURFACE_2};
}}
QRadioButton::indicator:checked {{
    border: 5px solid {ACCENT}; background: {BG};
}}
QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 4px;
    height: 7px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QListWidget, QTreeWidget, QTableWidget {{
    background: transparent; border: none; font-size: 13px;
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 7px; }}
QListWidget::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QHeaderView::section {{
    background: transparent; color: {DIM}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 8px; font-size: 11px;
}}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {SURFACE_3}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{
    background: {SURFACE_3}; border-radius: 4px; min-width: 30px;
}}
QMenu {{
    background: {SURFACE}; border: 1px solid {BORDER};
    border-radius: 9px; padding: 6px;
}}
QMenu::item {{ padding: 8px 22px 8px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 10px; }}
QToolTip {{
    background: {SURFACE_2}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 6px 8px;
}}
"""
