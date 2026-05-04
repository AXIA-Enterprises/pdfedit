#!/usr/bin/env python3
"""Basic Mac PDF editor — open, add/erase text (Google Fonts), and add form fields."""

from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import fitz  # PyMuPDF
from PyQt6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSettings,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Org/app/domain — used both for QApplication identity and QSettings storage.
APP_ORG = "AXIA Enterprises"
APP_ORG_DOMAIN = "github.com/AXIA-Enterprises"
APP_NAME = "PDFEdit"

APP_DIR = Path.home() / ".pdfedit"
FONT_CACHE = APP_DIR / "fonts"
FONT_CACHE.mkdir(parents=True, exist_ok=True)

# Saved-annotation stroke colors. Match the rubber-band preview so what the
# user sees while dragging is what the saved annotation looks like.
ANNOTATION_COLORS = {
    "highlight": (1.0, 0.95, 0.0),
    "underline": (0.235, 0.51, 0.86),
    "strikeout": (0.235, 0.51, 0.86),
}

# Built-in PDF base14 fonts — no download needed, ship with every PDF reader.
BUILTIN_FONTS = ["Times", "Helvetica", "Courier"]
BUILTIN_FONT_ALIAS = {"Times": "tiro", "Helvetica": "helv", "Courier": "cour"}

# Common system fonts users expect in a font picker. Surfaced only when the
# host actually has them installed (see installed_system_fonts()). Arial /
# Times New Roman / Courier New collapse to the matching base14 alias when
# baking; the rest are looked up on disk and embedded.
COMMON_SYSTEM_FONTS = [
    "Arial", "Times New Roman", "Calibri", "Verdana", "Georgia", "Tahoma",
    "Trebuchet MS", "Courier New", "Comic Sans MS", "Impact", "Arial Black",
]

# Three system families map cleanly onto base14 — embedding nothing keeps the
# saved PDF tiny. Tuple shape matches BASE14_VARIANTS: (regular, bold, italic, bold-italic).
SYSTEM_FONT_BASE14_ALIAS = {
    "Arial":           ("helv", "hebo", "heit", "hebi"),
    "Times New Roman": ("tiro", "tibo", "tiit", "tibi"),
    "Courier New":     ("cour", "cobo", "coit", "cobi"),
}

POPULAR_FONTS = [
    "Roboto", "Open Sans", "Lato", "Montserrat", "Oswald", "Source Sans 3",
    "Raleway", "Poppins", "Noto Sans", "Roboto Condensed", "Inter",
    "Roboto Mono", "Merriweather", "Playfair Display", "PT Sans", "Ubuntu",
    "Nunito", "Rubik", "Work Sans", "Fira Sans", "Quicksand", "Bebas Neue",
    "Inconsolata", "Dancing Script", "Pacifico", "Lobster", "Comfortaa",
    "Caveat", "Shadows Into Light", "Permanent Marker", "Anton", "DM Sans",
    "Manrope", "Karla", "Cabin", "Source Code Pro", "Crimson Text",
    "Libre Baskerville", "Josefin Sans", "Arvo", "Bitter",
]

# UA without woff2 support → Google returns TTF
_OLD_UA = "Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 5.1)"


# sfnt/OpenType/PostScript magic numbers; anything else won't load as a font and
# would just corrupt the cache + crash PyMuPDF if we wrote it through.
_FONT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")

# Network-fetch hardening for Google Fonts.
_GOOGLE_FONTS_CSS_HOST = "fonts.googleapis.com"
_GOOGLE_FONTS_CDN_HOST = "fonts.gstatic.com"
_MAX_FONT_BYTES = 10 * 1024 * 1024  # 10 MB — generous for any single font weight.


def fetch_google_font(family: str) -> Path | None:
    """Download a Google Font TTF (regular weight) and cache it locally.

    Hardened: CSS source is locked to fonts.googleapis.com, the extracted TTF
    URL must live on fonts.gstatic.com, and the download is capped at
    _MAX_FONT_BYTES. Any deviation fails closed (None).
    """
    cached = FONT_CACHE / f"{family.replace(' ', '_')}.ttf"
    if cached.exists() and cached.stat().st_size > 0:
        return cached
    url = f"https://{_GOOGLE_FONTS_CSS_HOST}/css2?family={quote(family)}&display=swap"
    tmp = cached.with_suffix(cached.suffix + ".tmp")
    try:
        # Defense in depth — the URL above is hard-coded, but assert the host
        # anyway so refactors can't silently widen the allow-list.
        if urlparse(url).hostname != _GOOGLE_FONTS_CSS_HOST:
            print(f"[fonts] {family}: refusing CSS host", file=sys.stderr)
            return None
        req = Request(url, headers={"User-Agent": _OLD_UA})
        css = urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
        m = re.search(r"src:\s*url\((https?://[^)]+\.ttf)\)", css)
        if not m:
            return None
        ttf_url = m.group(1)
        if urlparse(ttf_url).hostname != _GOOGLE_FONTS_CDN_HOST:
            print(f"[fonts] {family}: refusing TTF host {urlparse(ttf_url).hostname!r}",
                  file=sys.stderr)
            return None
        # read(N+1) so we can detect "longer than the cap" without
        # downloading the whole oversize payload.
        data = urlopen(ttf_url, timeout=20).read(_MAX_FONT_BYTES + 1)
        if len(data) > _MAX_FONT_BYTES:
            print(f"[fonts] {family}: TTF exceeds {_MAX_FONT_BYTES} byte cap",
                  file=sys.stderr)
            return None
        if not data.startswith(_FONT_MAGICS):
            print(f"[fonts] {family}: invalid TTF magic, refusing to cache",
                  file=sys.stderr)
            return None
        tmp.write_bytes(data)
        os.replace(tmp, cached)
        return cached
    except Exception as exc:
        print(f"[fonts] {family}: {exc}", file=sys.stderr)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return None


# --- Local system-font discovery -------------------------------------------
# Cross-platform: macOS / Windows / Linux directories users actually install
# fonts into. Paths that don't exist on the current host are skipped.
_SYSTEM_FONT_DIRS = [
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
]

# Hand-curated filename hints — the on-disk names don't always match the
# user-facing family name (e.g. Trebuchet MS → trebuc.ttf on Windows).
_SYSTEM_FONT_HINTS = {
    "Calibri": ["Calibri.ttf", "Calibri.ttc", "calibri.ttf"],
    "Verdana": ["Verdana.ttf", "verdana.ttf"],
    "Georgia": ["Georgia.ttf", "georgia.ttf"],
    "Tahoma": ["Tahoma.ttf", "tahoma.ttf"],
    "Trebuchet MS": ["Trebuchet MS.ttf", "trebuc.ttf"],
    "Comic Sans MS": ["Comic Sans MS.ttf", "comic.ttf"],
    "Impact": ["Impact.ttf", "impact.ttf"],
    "Arial Black": ["Arial Black.ttf", "ariblk.ttf"],
    "Arial": ["Arial.ttf", "Arial.ttc", "arial.ttf"],
    "Times New Roman": ["Times New Roman.ttf", "Times New Roman.ttc", "times.ttf"],
    "Courier New": ["Courier New.ttf", "Courier New.ttc", "cour.ttf"],
}

_system_font_cache: dict[str, Path | None] = {}


def find_system_font(family: str) -> Path | None:
    """Locate an installed system font file for `family`. Cross-platform."""
    if family in _system_font_cache:
        return _system_font_cache[family]
    hints = _SYSTEM_FONT_HINTS.get(
        family,
        [f"{family}.ttf", f"{family}.ttc", f"{family.replace(' ', '')}.ttf"],
    )
    for d in _SYSTEM_FONT_DIRS:
        if not d.exists():
            continue
        for hint in hints:
            try:
                matches = list(d.rglob(hint))
            except (PermissionError, OSError):
                continue
            for m in matches:
                if m.is_file():
                    _system_font_cache[family] = m
                    return m
    _system_font_cache[family] = None
    return None


_installed_system_fonts_cache: list[str] | None = None


def installed_system_fonts() -> list[str]:
    """Return COMMON_SYSTEM_FONTS filtered to those Qt sees as installed.

    Must be called after a QApplication has been created — QFontDatabase needs
    a live app to enumerate. Result is cached for the process lifetime.
    """
    global _installed_system_fonts_cache
    if _installed_system_fonts_cache is not None:
        return _installed_system_fonts_cache
    try:
        from PyQt6.QtGui import QFontDatabase
        installed = set(QFontDatabase.families())
        _installed_system_fonts_cache = [
            f for f in COMMON_SYSTEM_FONTS if f in installed
        ]
    except Exception:
        _installed_system_fonts_cache = []
    return _installed_system_fonts_cache


def parse_page_range(spec: str, max_pages: int) -> list[int]:
    """Parse a 1-based page range spec like "1,3-5,8" → sorted 0-based indices.

    - Handles single pages, dashes (inclusive ranges), commas, whitespace.
    - Reversed dash order is normalized ("5-3" == "3-5").
    - Out-of-range entries are clamped/dropped.
    - Empty string → empty list.
    - Malformed (non-numeric) segments raise ValueError.
    - De-duplicates overlapping ranges.
    """
    s = (spec or "").strip()
    if not s:
        return []
    seen: set[int] = set()
    for raw in s.split(","):
        seg = raw.strip()
        if not seg:
            continue
        if "-" in seg:
            a_str, b_str = seg.split("-", 1)
            a_str = a_str.strip()
            b_str = b_str.strip()
            try:
                a = int(a_str)
                b = int(b_str)
            except ValueError as exc:
                raise ValueError(f"Invalid page range segment: {seg!r}") from exc
            if a > b:
                a, b = b, a
            for p in range(a, b + 1):
                if 1 <= p <= max_pages:
                    seen.add(p - 1)
        else:
            try:
                p = int(seg)
            except ValueError as exc:
                raise ValueError(f"Invalid page number: {seg!r}") from exc
            if 1 <= p <= max_pages:
                seen.add(p - 1)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Theme system
# ---------------------------------------------------------------------------

LIGHT_PALETTE: dict[str, str] = {
    "bg":            "#FFFFFF",
    "surface":       "#F7F8FA",
    "surface-2":     "#EEF1F5",
    "border":        "#E1E5EB",
    "border-strong": "#C8CFD9",
    "text":          "#0F172A",
    "text-muted":    "#5B6573",
    "text-subtle":   "#8A94A6",
    "accent":        "#2563EB",
    "accent-hover":  "#1D4ED8",
    "accent-soft":   "#EFF4FF",
    "danger":        "#DC2626",
    "success":       "#16A34A",
}

DARK_PALETTE: dict[str, str] = {
    "bg":            "#0B0F17",
    "surface":       "#111827",
    "surface-2":     "#1A2233",
    "border":        "#22304A",
    "border-strong": "#2E3D5C",
    "text":          "#E6EAF2",
    "text-muted":    "#9AA4B8",
    "text-subtle":   "#6B7588",
    "accent":        "#3B82F6",
    "accent-hover":  "#60A5FA",
    "accent-soft":   "#162A4F",
    "danger":        "#F87171",
    "success":       "#34D399",
}

THEME_SETTINGS_KEY = "theme"
THEME_VALID_NAMES = ("light", "dark", "system")

UI_FONT_STACK = '-apple-system, "SF Pro Text", "Inter", "Segoe UI Variable", system-ui, sans-serif'
MONO_FONT_STACK = '"SF Mono", "JetBrains Mono", "Menlo", monospace'
UI_FONT_PT = 13

_active_palette: dict[str, str] = LIGHT_PALETTE
_active_theme_name: str = "light"


def _resolve_system_theme() -> str:
    """Return 'light' or 'dark' based on OS preference; fall back to light."""
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        from PyQt6.QtCore import Qt as _Qt
        if scheme == _Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    return "light"


def _palette_for(name: str) -> tuple[str, dict[str, str]]:
    if name == "system":
        resolved = _resolve_system_theme()
        return resolved, (DARK_PALETTE if resolved == "dark" else LIGHT_PALETTE)
    if name == "dark":
        return "dark", DARK_PALETTE
    return "light", LIGHT_PALETTE


def _build_qss(p: dict[str, str]) -> str:
    return f"""
* {{
    font-family: {UI_FONT_STACK};
    font-size: {UI_FONT_PT}pt;
}}
QWidget {{
    background-color: {p['bg']};
    color: {p['text']};
}}
QMainWindow, QDialog {{
    background-color: {p['bg']};
    color: {p['text']};
}}
QToolTip {{
    background-color: {p['surface-2']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 4px;
    padding: 4px 8px;
}}

/* ---- Toolbars ---- */
QToolBar {{
    background-color: {p['surface']};
    border: 0px;
    border-bottom: 1px solid {p['border']};
    padding: 4px 6px;
    spacing: 2px;
}}
QToolBar::separator {{
    background: {p['border']};
    width: 1px;
    margin: 4px 6px;
}}
QToolButton {{
    background: transparent;
    color: {p['text']};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 12px;
}}
QToolButton:hover {{
    background-color: {p['surface-2']};
    border-color: {p['border']};
}}
QToolButton:pressed, QToolButton:checked {{
    background-color: {p['accent-soft']};
    border-color: {p['accent']};
    color: {p['accent']};
}}
QToolButton::menu-indicator {{ image: none; width: 0px; }}

/* ---- Menu bar / Menus ---- */
QMenuBar {{
    background-color: {p['surface']};
    color: {p['text']};
    border-bottom: 1px solid {p['border']};
    padding: 2px 4px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background-color: {p['surface-2']};
}}
QMenu {{
    background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 4px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 14px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {p['accent-soft']};
    color: {p['text']};
}}
QMenu::separator {{
    height: 1px;
    background: {p['border']};
    margin: 4px 6px;
}}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: {p['surface-2']};
    border-color: {p['border-strong']};
}}
QPushButton:pressed {{
    background-color: {p['surface-2']};
}}
QPushButton:disabled {{
    color: {p['text-subtle']};
    background-color: {p['surface']};
    border-color: {p['border']};
}}
QPushButton:default {{
    background-color: {p['accent']};
    color: #FFFFFF;
    border-color: {p['accent']};
}}
QPushButton:default:hover {{
    background-color: {p['accent-hover']};
    border-color: {p['accent-hover']};
}}

/* ---- Inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {p['bg']};
    color: {p['text']};
    selection-background-color: {p['accent']};
    selection-color: #FFFFFF;
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 4px 8px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 2px solid {p['accent']};
    padding: 3px 7px;
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {p['text-subtle']};
    background-color: {p['surface']};
}}
QComboBox::drop-down {{
    border: 0px;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    selection-background-color: {p['accent-soft']};
    selection-color: {p['text']};
    outline: 0;
}}

/* ---- Checkboxes / Radios ---- */
QCheckBox, QRadioButton {{
    background: transparent;
    color: {p['text']};
    spacing: 6px;
}}

/* ---- Labels ---- */
QLabel {{
    background: transparent;
    color: {p['text']};
}}

/* ---- Dock widgets ---- */
QDockWidget {{
    color: {p['text']};
    titlebar-close-icon: none;
}}
QDockWidget::title {{
    background-color: {p['surface']};
    color: {p['text-muted']};
    padding: 6px 10px;
    border-bottom: 1px solid {p['border']};
}}

/* ---- Tabs ---- */
QTabWidget::pane {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    background-color: {p['bg']};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {p['text-muted']};
    padding: 6px 14px;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    color: {p['text']};
    background-color: {p['bg']};
    border: 1px solid {p['border']};
    border-bottom-color: {p['bg']};
}}
QTabBar::tab:hover:!selected {{
    color: {p['text']};
    background-color: {p['surface-2']};
}}

/* ---- Tree / List ---- */
QTreeWidget, QListWidget, QTreeView, QListView {{
    background-color: {p['bg']};
    alternate-background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    outline: 0;
}}
QTreeWidget::item, QListWidget::item, QTreeView::item, QListView::item {{
    padding: 4px 6px;
    border: 0;
}}
QTreeWidget::item:selected, QListWidget::item:selected,
QTreeView::item:selected, QListView::item:selected {{
    background-color: {p['accent-soft']};
    color: {p['text']};
    border-left: 2px solid {p['accent']};
}}
QHeaderView::section {{
    background-color: {p['surface']};
    color: {p['text-muted']};
    border: 0;
    border-right: 1px solid {p['border']};
    border-bottom: 1px solid {p['border']};
    padding: 6px 8px;
}}

/* ---- Status bar ---- */
QStatusBar {{
    background-color: {p['surface']};
    color: {p['text-muted']};
    border-top: 1px solid {p['border']};
}}
QStatusBar QLabel {{
    color: {p['text-muted']};
}}

/* ---- Scrollbars ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p['border-strong']};
    border-radius: 4px;
    min-width: 24px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {p['text-subtle']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    background: transparent;
    border: 0;
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---- Splitter ---- */
QSplitter::handle {{
    background-color: {p['border']};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ---- Group box ---- */
QGroupBox {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 8px;
    color: {p['text']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {p['text-muted']};
}}

/* ---- Slider ---- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {p['border']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {p['accent']};
    width: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
"""


def _qpalette_for(p: dict[str, str]) -> QPalette:
    """Build a QPalette so native dialogs (file/color) blend with the theme."""
    qp = QPalette()
    bg = QColor(p["bg"])
    surface = QColor(p["surface"])
    text = QColor(p["text"])
    muted = QColor(p["text-muted"])
    accent = QColor(p["accent"])
    border = QColor(p["border"])
    qp.setColor(QPalette.ColorRole.Window, bg)
    qp.setColor(QPalette.ColorRole.WindowText, text)
    qp.setColor(QPalette.ColorRole.Base, bg)
    qp.setColor(QPalette.ColorRole.AlternateBase, surface)
    qp.setColor(QPalette.ColorRole.ToolTipBase, surface)
    qp.setColor(QPalette.ColorRole.ToolTipText, text)
    qp.setColor(QPalette.ColorRole.Text, text)
    qp.setColor(QPalette.ColorRole.Button, surface)
    qp.setColor(QPalette.ColorRole.ButtonText, text)
    qp.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    qp.setColor(QPalette.ColorRole.Highlight, accent)
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(p["text-subtle"]))
    qp.setColor(QPalette.ColorRole.Mid, border)
    qp.setColor(QPalette.ColorRole.Dark, QColor(p["border-strong"]))
    qp.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))
    qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, muted)
    qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, muted)
    qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, muted)
    return qp


def apply_theme(app: "QApplication", name: str) -> None:
    """Apply the named theme ('light' / 'dark' / 'system') to `app`."""
    global _active_palette, _active_theme_name
    if name not in THEME_VALID_NAMES:
        name = "system"
    resolved, palette = _palette_for(name)
    _active_palette = palette
    _active_theme_name = resolved
    if app is None:
        return
    try:
        font = QFont()
        font.setPointSize(UI_FONT_PT)
        app.setFont(font)
    except Exception:
        pass
    try:
        app.setPalette(_qpalette_for(palette))
    except Exception:
        pass
    app.setStyleSheet(_build_qss(palette))


def current_theme_name() -> str:
    """Read the persisted theme preference; defaults to 'system'."""
    try:
        s = QSettings()
        v = s.value(THEME_SETTINGS_KEY, "system")
        if isinstance(v, str) and v in THEME_VALID_NAMES:
            return v
    except Exception:
        pass
    return "system"


def set_theme(app: "QApplication", name: str) -> None:
    """Persist `name` under QSettings and apply it to `app`."""
    if name not in THEME_VALID_NAMES:
        name = "system"
    try:
        s = QSettings()
        s.setValue(THEME_SETTINGS_KEY, name)
    except Exception:
        pass
    apply_theme(app, name)


def current_accent_color() -> QColor:
    """Return the active theme's accent color as a QColor (with alpha 220)."""
    c = QColor(_active_palette.get("accent", "#2563EB"))
    return c


# Snapshot of stock palette/font constants — captured at import time so the
# Settings dialog "Reset to defaults" button can restore them after the user
# has mutated LIGHT_PALETTE / DARK_PALETTE / UI_FONT_PT.
_DEFAULT_LIGHT_PALETTE: dict[str, str] = dict(LIGHT_PALETTE)
_DEFAULT_DARK_PALETTE: dict[str, str] = dict(DARK_PALETTE)
_DEFAULT_UI_FONT_PT = UI_FONT_PT


def _shade(hex_color: str, factor: float) -> str:
    """Lighten (factor>1) or darken (factor<1) a #RRGGBB color."""
    try:
        c = QColor(hex_color)
        if not c.isValid():
            return hex_color
        h, s, l, a = c.getHsl()
        if l < 0:
            l = c.lightness()
        new_l = max(0, min(255, int(l * factor)))
        out = QColor.fromHsl(c.hslHue(), c.hslSaturation(), new_l, a)
        return out.name()
    except Exception:
        return hex_color


def _read_auto_open_field_properties() -> bool:
    try:
        v = QSettings().value(AUTO_OPEN_FIELD_PROPERTIES_KEY, True)
    except Exception:
        return True
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() not in ("false", "0", "no", "")
    try:
        return bool(int(v))
    except Exception:
        return bool(v)


def _load_persisted_appearance() -> None:
    """Apply UI_FONT_PT and accent overrides saved in QSettings, in-place.

    Called once at app start before the first apply_theme() so a returning
    user sees the same font size and accent color they last picked.
    """
    global UI_FONT_PT
    try:
        s = QSettings()
        sz = s.value("uiFontPt")
        if sz is not None:
            try:
                v = int(sz)
                if 8 <= v <= 36:
                    UI_FONT_PT = v
            except (TypeError, ValueError):
                pass
        accent = s.value("accentColor")
        if isinstance(accent, str) and QColor(accent).isValid():
            LIGHT_PALETTE["accent"] = accent
            DARK_PALETTE["accent"] = accent
            LIGHT_PALETTE["accent-hover"] = _shade(accent, 0.85)
            DARK_PALETTE["accent-hover"] = _shade(accent, 1.18)
    except Exception:
        pass


def _read_form_panel_default_visible() -> bool:
    try:
        v = QSettings().value(FORM_BUILDER_PANEL_DEFAULT_VISIBLE_KEY, False)
    except Exception:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    try:
        return bool(int(v))
    except Exception:
        return bool(v)


class SettingsDialog(QDialog):
    """Adobe-Acrobat-style preferences dialog.

    Theme/font/accent changes apply live (no Apply button) — the dialog mutates
    the module-level palette/font constants and re-applies the QSS so every
    open widget repaints immediately. Editor + panel settings persist via
    QSettings and are read back on next launch.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(440, 540)

        self._app = QApplication.instance()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 14)
        outer.setSpacing(14)

        # ---- Section 1: Appearance --------------------------------------
        appearance = QGroupBox("Appearance")
        af = QFormLayout(appearance)
        af.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        af.setHorizontalSpacing(14)
        af.setVerticalSpacing(10)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("System", "system")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        cur = current_theme_name()
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == cur:
                self.theme_combo.setCurrentIndex(i)
                break
        self.theme_combo.currentIndexChanged.connect(self._on_theme_index_changed)
        af.addRow("Theme:", self.theme_combo)

        font_row = QWidget()
        fr = QHBoxLayout(font_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.setSpacing(8)
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(10, 18)
        self.font_slider.setSingleStep(1)
        self.font_slider.setPageStep(1)
        self.font_slider.setValue(int(UI_FONT_PT))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(10, 18)
        self.font_spin.setValue(int(UI_FONT_PT))
        self.font_slider.valueChanged.connect(self._on_font_slider_changed)
        self.font_spin.valueChanged.connect(self._on_font_spin_changed)
        fr.addWidget(self.font_slider, 1)
        fr.addWidget(self.font_spin)
        af.addRow("UI font size:", font_row)

        self.accent_btn = QPushButton()
        self.accent_btn.setMinimumHeight(26)
        self.accent_btn.clicked.connect(self._on_accent_button_clicked)
        self._refresh_accent_swatch()
        af.addRow("Accent color:", self.accent_btn)

        self.reset_appearance_btn = QPushButton("Reset to defaults")
        self.reset_appearance_btn.clicked.connect(self.reset_appearance)
        af.addRow("", self.reset_appearance_btn)

        outer.addWidget(appearance)

        # ---- Section 2: Editor ------------------------------------------
        editor = QGroupBox("Editor")
        ef = QFormLayout(editor)
        ef.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        ef.setHorizontalSpacing(14)
        ef.setVerticalSpacing(10)

        self.field_pattern_edit = QLineEdit()
        self.field_pattern_edit.setPlaceholderText(DEFAULT_FIELD_NAME_PATTERN)
        try:
            stored_pat = QSettings().value(
                DEFAULT_FIELD_NAME_PATTERN_KEY, DEFAULT_FIELD_NAME_PATTERN
            )
        except Exception:
            stored_pat = DEFAULT_FIELD_NAME_PATTERN
        if isinstance(stored_pat, str):
            self.field_pattern_edit.setText(stored_pat)
        self.field_pattern_edit.editingFinished.connect(
            self._on_field_pattern_changed
        )
        ef.addRow("Default field-name pattern:", self.field_pattern_edit)

        self.auto_open_chk = QCheckBox("Auto-open Properties on field create")
        self.auto_open_chk.setChecked(_read_auto_open_field_properties())
        self.auto_open_chk.toggled.connect(self._on_auto_open_toggled)
        ef.addRow("", self.auto_open_chk)

        outer.addWidget(editor)

        # ---- Section 3: Form Builder Panel ------------------------------
        panel_box = QGroupBox("Form Builder Panel")
        pf = QFormLayout(panel_box)
        pf.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        pf.setHorizontalSpacing(14)
        pf.setVerticalSpacing(10)

        self.show_panel_chk = QCheckBox("Show panel by default for new documents")
        self.show_panel_chk.setChecked(_read_form_panel_default_visible())
        self.show_panel_chk.toggled.connect(self._on_show_panel_toggled)
        pf.addRow("", self.show_panel_chk)

        outer.addWidget(panel_box)

        # ---- Section 4: Advanced ----------------------------------------
        adv = QGroupBox("Advanced")
        avf = QVBoxLayout(adv)
        avf.setContentsMargins(10, 10, 10, 10)
        self.reset_all_btn = QPushButton("Reset all settings to defaults")
        self.reset_all_btn.clicked.connect(self._on_reset_all_clicked)
        avf.addWidget(self.reset_all_btn)
        outer.addWidget(adv)

        outer.addStretch(1)

        # ---- Close button -----------------------------------------------
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.close_btn)
        outer.addLayout(bottom)

    # ----- Theme -------------------------------------------------------------

    def _on_theme_index_changed(self, _idx: int) -> None:
        name = self.theme_combo.currentData()
        if not isinstance(name, str):
            return
        self._on_theme_changed(name)

    def _on_theme_changed(self, name: str) -> None:
        if name not in THEME_VALID_NAMES:
            name = "system"
        set_theme(self._app, name)

    # ----- Font size ---------------------------------------------------------

    def _on_font_slider_changed(self, value: int) -> None:
        if self.font_spin.value() != value:
            self.font_spin.blockSignals(True)
            self.font_spin.setValue(value)
            self.font_spin.blockSignals(False)
        self._apply_font_size(value)

    def _on_font_spin_changed(self, value: int) -> None:
        if self.font_slider.value() != value:
            self.font_slider.blockSignals(True)
            self.font_slider.setValue(value)
            self.font_slider.blockSignals(False)
        self._apply_font_size(value)

    def _apply_font_size(self, value: int) -> None:
        global UI_FONT_PT
        UI_FONT_PT = int(value)
        try:
            QSettings().setValue("uiFontPt", int(value))
        except Exception:
            pass
        apply_theme(self._app, current_theme_name())

    # ----- Accent color ------------------------------------------------------

    def _refresh_accent_swatch(self) -> None:
        accent = LIGHT_PALETTE.get("accent", "#2563EB")
        self.accent_btn.setText(accent.upper())
        # Inline style overrides theme QSS for this single button.
        text_color = "#FFFFFF" if QColor(accent).lightness() < 160 else "#0F172A"
        self.accent_btn.setStyleSheet(
            f"QPushButton {{ background-color: {accent}; color: {text_color}; "
            f"border: 1px solid {accent}; border-radius: 6px; padding: 4px 10px; }}"
        )

    def _on_accent_button_clicked(self) -> None:
        start = QColor(LIGHT_PALETTE.get("accent", "#2563EB"))
        c = QColorDialog.getColor(start, self, "Choose accent color")
        if c.isValid():
            self._on_accent_chosen(c)

    def _on_accent_chosen(self, color: QColor) -> None:
        if not isinstance(color, QColor) or not color.isValid():
            return
        hex_name = color.name()
        LIGHT_PALETTE["accent"] = hex_name
        DARK_PALETTE["accent"] = hex_name
        LIGHT_PALETTE["accent-hover"] = _shade(hex_name, 0.85)
        DARK_PALETTE["accent-hover"] = _shade(hex_name, 1.18)
        try:
            QSettings().setValue("accentColor", hex_name)
        except Exception:
            pass
        apply_theme(self._app, current_theme_name())
        self._refresh_accent_swatch()

    def reset_appearance(self) -> None:
        global UI_FONT_PT
        LIGHT_PALETTE.clear()
        LIGHT_PALETTE.update(_DEFAULT_LIGHT_PALETTE)
        DARK_PALETTE.clear()
        DARK_PALETTE.update(_DEFAULT_DARK_PALETTE)
        UI_FONT_PT = _DEFAULT_UI_FONT_PT
        try:
            s = QSettings()
            s.remove("accentColor")
            s.remove("uiFontPt")
        except Exception:
            pass
        self.font_slider.blockSignals(True)
        self.font_spin.blockSignals(True)
        self.font_slider.setValue(int(UI_FONT_PT))
        self.font_spin.setValue(int(UI_FONT_PT))
        self.font_slider.blockSignals(False)
        self.font_spin.blockSignals(False)
        apply_theme(self._app, current_theme_name())
        self._refresh_accent_swatch()

    # ----- Editor section ----------------------------------------------------

    def _on_field_pattern_changed(self) -> None:
        pat = self.field_pattern_edit.text().strip() or DEFAULT_FIELD_NAME_PATTERN
        try:
            QSettings().setValue(DEFAULT_FIELD_NAME_PATTERN_KEY, pat)
        except Exception:
            pass

    def _on_auto_open_toggled(self, checked: bool) -> None:
        try:
            QSettings().setValue(AUTO_OPEN_FIELD_PROPERTIES_KEY, bool(checked))
        except Exception:
            pass

    # ----- Form Builder panel section ---------------------------------------

    def _on_show_panel_toggled(self, checked: bool) -> None:
        try:
            QSettings().setValue(
                FORM_BUILDER_PANEL_DEFAULT_VISIBLE_KEY, bool(checked)
            )
        except Exception:
            pass

    # ----- Reset all ---------------------------------------------------------

    def _on_reset_all_clicked(self) -> None:
        resp = QMessageBox.question(
            self,
            "Reset all settings",
            "Clear every saved preference and restore defaults?\n\n"
            "Theme, font size, and accent color will revert immediately. "
            "Other changes take effect on next launch.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.reset_all()

    def reset_all(self) -> None:
        try:
            s = QSettings()
            s.clear()
        except Exception:
            pass
        self.reset_appearance()
        # Re-sync UI controls in this dialog to defaults.
        self.field_pattern_edit.blockSignals(True)
        self.field_pattern_edit.setText(DEFAULT_FIELD_NAME_PATTERN)
        self.field_pattern_edit.blockSignals(False)
        self.auto_open_chk.blockSignals(True)
        self.auto_open_chk.setChecked(True)
        self.auto_open_chk.blockSignals(False)
        self.show_panel_chk.blockSignals(True)
        self.show_panel_chk.setChecked(False)
        self.show_panel_chk.blockSignals(False)
        # Snap theme combo back to "System".
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == "system":
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(i)
                self.theme_combo.blockSignals(False)
                break
        set_theme(self._app, "system")


class WatermarkDialog(QDialog):
    """Configure a text watermark — text, font, size, opacity, rotation, color, range."""

    def __init__(self, parent=None, page_count: int = 1):
        super().__init__(parent)
        self.setWindowTitle("Watermark")
        self.setMinimumWidth(420)

        self.text_edit = QLineEdit("DRAFT")

        self.font_box = QComboBox()
        self.font_box.setEditable(True)
        self.font_box.addItems(BUILTIN_FONTS)
        self.font_box.insertSeparator(self.font_box.count())
        sys_fonts = installed_system_fonts()
        if sys_fonts:
            self.font_box.addItems(sys_fonts)
            self.font_box.insertSeparator(self.font_box.count())
        self.font_box.addItems(POPULAR_FONTS)
        self.font_box.setCurrentText("Helvetica")

        self.size_box = QSpinBox()
        self.size_box.setRange(8, 400)
        self.size_box.setValue(72)

        self.opacity_box = QDoubleSpinBox()
        self.opacity_box.setRange(0.05, 1.0)
        self.opacity_box.setSingleStep(0.05)
        self.opacity_box.setDecimals(2)
        self.opacity_box.setValue(0.30)

        self.rotation_box = QSpinBox()
        self.rotation_box.setRange(-180, 180)
        self.rotation_box.setValue(45)

        self.color = QColor(128, 128, 128)
        self.color_btn = QPushButton(self.color.name())
        self.color_btn.clicked.connect(self._pick_color)
        self._update_color_btn()

        self.all_pages_chk = QCheckBox("All pages")
        self.all_pages_chk.setChecked(True)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText(f"e.g. 1,3-5  (1–{page_count})")
        self.range_edit.setEnabled(False)
        self.all_pages_chk.toggled.connect(
            lambda on: self.range_edit.setEnabled(not on)
        )

        form = QFormLayout()
        form.addRow("Text:", self.text_edit)
        form.addRow("Font:", self.font_box)
        form.addRow("Size:", self.size_box)
        form.addRow("Opacity:", self.opacity_box)
        form.addRow("Rotation°:", self.rotation_box)
        form.addRow("Color:", self.color_btn)
        form.addRow("", self.all_pages_chk)
        form.addRow("Pages:", self.range_edit)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(bb)

    def _pick_color(self):
        c = QColorDialog.getColor(self.color, self, "Watermark Color")
        if c.isValid():
            self.color = c
            self._update_color_btn()

    def _update_color_btn(self):
        self.color_btn.setText(self.color.name())
        self.color_btn.setStyleSheet(
            f"background:{self.color.name()};"
            f"color:{'white' if self.color.lightness() < 128 else 'black'};"
        )

    def values(self) -> dict:
        return {
            "text": self.text_edit.text(),
            "family": self.font_box.currentText().strip() or "Helvetica",
            "size": int(self.size_box.value()),
            "opacity": float(self.opacity_box.value()),
            "rotation": int(self.rotation_box.value()),
            "color": self.color,
            "all_pages": self.all_pages_chk.isChecked(),
            "range": self.range_edit.text(),
        }


class AddTextDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Text")
        self.setMinimumWidth(380)

        self.text_edit = QLineEdit()
        self.font_box = QComboBox()
        self.font_box.setEditable(True)
        self.font_box.addItems(BUILTIN_FONTS)
        self.font_box.insertSeparator(self.font_box.count())
        sys_fonts = installed_system_fonts()
        if sys_fonts:
            self.font_box.addItems(sys_fonts)
            self.font_box.insertSeparator(self.font_box.count())
        self.font_box.addItems(POPULAR_FONTS)
        self.font_box.setCurrentText("Times")  # default
        self.size_box = QSpinBox()
        self.size_box.setRange(4, 288)
        self.size_box.setValue(12)
        self.color = QColor(0, 0, 0)
        self.color_btn = QPushButton()
        self.color_btn.clicked.connect(self._pick_color)
        self._update_color_btn()

        self.preview = QLabel("Sample text")
        self.preview.setMinimumHeight(40)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_preview()

        self.text_edit.textChanged.connect(self._update_preview)
        self.font_box.currentTextChanged.connect(self._update_preview)
        self.size_box.valueChanged.connect(self._update_preview)

        form = QFormLayout()
        form.addRow("Text:", self.text_edit)
        form.addRow("Font:", self.font_box)
        form.addRow("Size:", self.size_box)
        form.addRow("Color:", self.color_btn)
        form.addRow("Preview:", self.preview)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            QLabel("Tip: any Google Fonts family name works — fonts are cached.")
        )
        layout.addWidget(bb)

    def _pick_color(self):
        c = QColorDialog.getColor(self.color, self, "Text Color")
        if c.isValid():
            self.color = c
            self._update_color_btn()
            self._update_preview()

    def _update_color_btn(self):
        self.color_btn.setText(self.color.name())
        self.color_btn.setStyleSheet(
            f"background:{self.color.name()};"
            f"color:{'white' if self.color.lightness() < 128 else 'black'};"
        )

    def _update_preview(self):
        sample = self.text_edit.text() or "Sample text"
        self.preview.setText(sample)
        f = QFont(self.font_box.currentText().strip() or "Helvetica")
        f.setPointSize(int(self.size_box.value()))
        self.preview.setFont(f)
        self.preview.setStyleSheet(f"color:{self.color.name()};")

    def values(self):
        return (
            self.text_edit.text(),
            self.font_box.currentText().strip(),
            self.size_box.value(),
            self.color,
        )


# (label, width_inches, height_inches) — None means insert separator
PAGE_PRESETS: list[tuple[str, float | None, float | None]] = [
    ("US Letter (8.5 × 11 in)", 8.5, 11.0),
    ("US Legal (8.5 × 14 in)", 8.5, 14.0),
    ("Tabloid / Ledger (11 × 17 in)", 11.0, 17.0),
    ("Executive (7.25 × 10.5 in)", 7.25, 10.5),
    ("__sep__", None, None),
    ("A3 (297 × 420 mm)", 11.6929, 16.5354),
    ("A4 (210 × 297 mm)", 8.2677, 11.6929),
    ("A5 (148 × 210 mm)", 5.8268, 8.2677),
    ("__sep__", None, None),
    ("ANSI A (8.5 × 11)", 8.5, 11.0),
    ("ANSI B (11 × 17)", 11.0, 17.0),
    ("ANSI C (17 × 22)", 17.0, 22.0),
    ("ANSI D (22 × 34)", 22.0, 34.0),
    ("ANSI E (34 × 44)", 34.0, 44.0),
    ("__sep__", None, None),
    ("ARCH A (9 × 12)", 9.0, 12.0),
    ("ARCH B (12 × 18)", 12.0, 18.0),
    ("ARCH C (18 × 24)", 18.0, 24.0),
    ("ARCH D (24 × 36)", 24.0, 36.0),
    ("ARCH E1 (30 × 42)", 30.0, 42.0),
    ("ARCH E (36 × 48)", 36.0, 48.0),
    ("__sep__", None, None),
    ("Custom", None, None),
]


class NewPDFDialog(QDialog):
    """Pick a page preset (or custom W/H), orientation, and page count."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New PDF")
        self.setMinimumWidth(420)
        self._suppress = False  # avoid recursive combo/spin signals

        self.preset = QComboBox()
        for i, (label, w, h) in enumerate(PAGE_PRESETS):
            if label == "__sep__":
                self.preset.insertSeparator(self.preset.count())
            else:
                self.preset.addItem(label, (w, h))
        # Default to US Letter
        self.preset.setCurrentIndex(0)
        self.preset.currentIndexChanged.connect(self._on_preset)

        self.width_in = QDoubleSpinBox()
        self.width_in.setRange(0.5, 200.0)
        self.width_in.setDecimals(3)
        self.width_in.setSingleStep(0.25)
        self.width_in.setSuffix(" in")
        self.width_in.setValue(8.5)
        self.width_in.valueChanged.connect(self._on_dim_edit)

        self.height_in = QDoubleSpinBox()
        self.height_in.setRange(0.5, 200.0)
        self.height_in.setDecimals(3)
        self.height_in.setSingleStep(0.25)
        self.height_in.setSuffix(" in")
        self.height_in.setValue(11.0)
        self.height_in.valueChanged.connect(self._on_dim_edit)

        self.portrait = QRadioButton("Portrait")
        self.landscape = QRadioButton("Landscape")
        self.portrait.setChecked(True)
        self.portrait.toggled.connect(self._on_orientation)

        self.pages = QSpinBox()
        self.pages.setRange(1, 1000)
        self.pages.setValue(1)

        orient = QHBoxLayout()
        orient.addWidget(self.portrait)
        orient.addWidget(self.landscape)
        orient.addStretch()

        form = QFormLayout()
        form.addRow("Page size:", self.preset)
        form.addRow("Width:", self.width_in)
        form.addRow("Height:", self.height_in)
        form.addRow("Orientation:", orient)
        form.addRow("Pages:", self.pages)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(bb)

    def _on_preset(self):
        data = self.preset.currentData()
        if not data:
            return
        w, h = data
        if w is None or h is None:  # Custom
            return
        self._suppress = True
        # Apply preset, then respect current orientation
        if self.landscape.isChecked():
            self.width_in.setValue(max(w, h))
            self.height_in.setValue(min(w, h))
        else:
            self.width_in.setValue(min(w, h))
            self.height_in.setValue(max(w, h))
        self._suppress = False

    def _on_dim_edit(self):
        if self._suppress:
            return
        # User typed a custom size — flip combo to "Custom"
        for i in range(self.preset.count()):
            if self.preset.itemText(i) == "Custom":
                self._suppress = True
                self.preset.setCurrentIndex(i)
                self._suppress = False
                return

    def _on_orientation(self):
        if self._suppress:
            return
        w = self.width_in.value()
        h = self.height_in.value()
        if self.landscape.isChecked() and w < h:
            self._suppress = True
            self.width_in.setValue(h)
            self.height_in.setValue(w)
            self._suppress = False
        elif self.portrait.isChecked() and w > h:
            self._suppress = True
            self.width_in.setValue(h)
            self.height_in.setValue(w)
            self._suppress = False

    def values(self) -> tuple[float, float, int]:
        """Returns (width_pt, height_pt, page_count). 1 inch = 72 PDF points."""
        return (
            self.width_in.value() * 72.0,
            self.height_in.value() * 72.0,
            self.pages.value(),
        )


CURSIVE_FONTS = ["Dancing Script", "Pacifico", "Caveat", "Permanent Marker",
                 "Lobster", "Shadows Into Light"]


# Family names already registered with QFontDatabase. Set by both the
# pre-fetch thread (after a successful download) and on-demand fetches
# inside the SignatureDialog. Read from any thread; only mutated on the
# Qt main thread (signal/slot), so no extra lock needed.
_loaded_font_families: set[str] = set()


def _register_font_file(family: str, ttf_path: Path) -> bool:
    """Register a TTF with QFontDatabase if not already registered.
    Returns True on success (or already-registered)."""
    if family in _loaded_font_families:
        return True
    try:
        from PyQt6.QtGui import QFontDatabase
        fid = QFontDatabase.addApplicationFont(str(ttf_path))
        if fid >= 0:
            _loaded_font_families.add(family)
            return True
    except Exception as exc:
        print(f"[fonts] register {family}: {exc}", file=sys.stderr)
    return False


class _FontPrefetchThread(QThread):
    """Walk CURSIVE_FONTS once at startup and pull each TTF into the cache.

    Runs off the GUI thread so the network calls in fetch_google_font()
    can't freeze the window. Failures are logged and skipped — the
    SignatureDialog handles the absent-cache case at use-time.
    """

    fetched = pyqtSignal(str, str)  # family, path-or-empty-string

    def run(self):
        for family in CURSIVE_FONTS:
            try:
                p = fetch_google_font(family)
            except Exception as exc:
                print(f"[fonts] prefetch {family}: {exc}", file=sys.stderr)
                p = None
            self.fetched.emit(family, str(p) if p else "")


_font_prefetch_thread: _FontPrefetchThread | None = None


def start_font_prefetch(parent: QObject | None = None) -> _FontPrefetchThread:
    """Kick off the cursive-font pre-fetch once, on the QApplication thread.

    Idempotent: subsequent calls return the existing thread. Caller must
    hold a reference (we stash one in a module global) so the QThread
    isn't garbage-collected mid-run.
    """
    global _font_prefetch_thread
    if _font_prefetch_thread is not None:
        return _font_prefetch_thread
    th = _FontPrefetchThread(parent)

    def _on_fetched(family: str, path: str) -> None:
        if path:
            _register_font_file(family, Path(path))

    th.fetched.connect(_on_fetched)
    _font_prefetch_thread = th
    th.start()
    return th


class _FontFetchSignals(QObject):
    """Signal carrier for _FontFetchTask (QRunnable can't emit directly)."""

    done = pyqtSignal(str, str)  # family, path-or-empty-string


class _FontFetchTask(QRunnable):
    """One-shot Google-font fetch run on the QThreadPool. Used when the user
    picks a font in the SignatureDialog that wasn't pre-fetched at startup."""

    def __init__(self, family: str):
        super().__init__()
        self.family = family
        self.signals = _FontFetchSignals()

    def run(self):
        try:
            p = fetch_google_font(self.family)
        except Exception as exc:
            print(f"[fonts] on-demand {self.family}: {exc}", file=sys.stderr)
            p = None
        self.signals.done.emit(self.family, str(p) if p else "")


class _DrawCanvas(QWidget):
    """Tiny widget for capturing freehand strokes with mouse/trackpad.
    Strokes are stored already-normalized 0..1 against the widget size at
    capture time, so resizing the dialog doesn't warp them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 160)
        self.setStyleSheet("background: white; border: 1px solid #ccc;")
        self.strokes: list[list[tuple[float, float]]] = []
        self._drawing = False
        self.stroke_color: QColor = QColor(0, 0, 0)

    def set_stroke_color(self, c: QColor) -> None:
        self.stroke_color = QColor(c)
        self.update()

    def _norm(self, ev) -> tuple[float, float]:
        w = max(1, self.width())
        h = max(1, self.height())
        nx = max(0.0, min(1.0, ev.position().x() / w))
        ny = max(0.0, min(1.0, ev.position().y() / h))
        return (nx, ny)

    def clear(self):
        self.strokes = []
        self.update()

    def undo_stroke(self):
        if self.strokes:
            self.strokes.pop()
            self.update()

    def normalized_strokes(self) -> list[list[tuple[float, float]]]:
        return [list(s) for s in self.strokes if len(s) >= 2]

    def mousePressEvent(self, ev):
        self._drawing = True
        self.strokes.append([self._norm(ev)])
        self.update()

    def mouseMoveEvent(self, ev):
        if not self._drawing or not self.strokes:
            return
        self.strokes[-1].append(self._norm(ev))
        self.update()

    def mouseReleaseEvent(self, ev):
        self._drawing = False

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        pen = QPen(QColor(self.stroke_color), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        w = self.width()
        h = self.height()
        for s in self.strokes:
            if len(s) < 2:
                continue
            for (ax, ay), (bx, by) in zip(s[:-1], s[1:]):
                p.drawLine(QPointF(ax * w, ay * h), QPointF(bx * w, by * h))


class _CursivePreview(QLabel):
    """Live preview of typed text in a chosen cursive font."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setStyleSheet("background: white; border: 1px solid #ccc; padding: 8px;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


class SignatureDialog(QDialog):
    """Two ways to make a signature: type your name in a cursive font, or draw one."""

    PREVIEW_POINT_SIZE = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Signature")
        self.setMinimumWidth(480)
        self.result_data: dict | None = None
        self._type_color: QColor = QColor(0, 0, 0)
        self._draw_color: QColor = QColor(0, 0, 0)
        self._pending_family: str | None = None  # family being fetched on-demand
        self._size_pt: int = 24

        tabs = QTabWidget()

        # --- Type tab ---
        type_widget = QWidget()
        ty = QVBoxLayout(type_widget)
        self.type_input = QLineEdit()
        self.type_input.setPlaceholderText("Your name")
        self.type_font = QComboBox()
        self.type_font.addItems(CURSIVE_FONTS)
        self.type_font.setCurrentText("Dancing Script")
        self.type_preview = _CursivePreview()
        self.type_status = QLabel("")
        self.type_status.setStyleSheet("color: #888; font-size: 11px;")

        self.type_size = QSpinBox()
        self.type_size.setRange(10, 96)
        self.type_size.setValue(self._size_pt)
        self.type_size.setSuffix(" pt")
        self.type_color_btn = QPushButton("Color…")
        self.type_color_btn.clicked.connect(self._pick_type_color)
        self._refresh_type_color_swatch()

        # Split signal handlers: text changes only re-set the preview text
        # (cheap), font changes go through the font-loader path.
        self.type_input.textChanged.connect(self._refresh_text)
        self.type_font.currentTextChanged.connect(self._refresh_font)
        self.type_size.valueChanged.connect(self._refresh_size)

        ty.addWidget(QLabel("Type your name:"))
        ty.addWidget(self.type_input)
        ty.addWidget(QLabel("Style:"))
        ty.addWidget(self.type_font)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size:"))
        size_row.addWidget(self.type_size)
        size_row.addSpacing(12)
        size_row.addWidget(QLabel("Color:"))
        size_row.addWidget(self.type_color_btn)
        size_row.addStretch()
        ty.addLayout(size_row)
        ty.addWidget(QLabel("Preview:"))
        ty.addWidget(self.type_preview)
        ty.addWidget(self.type_status)
        type_hint = QLabel("You can drag the corners to resize after placing on the page.")
        type_hint.setStyleSheet("color: #666; font-size: 11px;")
        type_hint.setWordWrap(True)
        ty.addWidget(type_hint)
        tabs.addTab(type_widget, "Type signature")

        # --- Draw tab ---
        draw_widget = QWidget()
        dw = QVBoxLayout(draw_widget)
        dw.addWidget(QLabel("Sign with your mouse or trackpad:"))
        self.draw_canvas = _DrawCanvas()
        dw.addWidget(self.draw_canvas)
        self.draw_color_btn = QPushButton("Color…")
        self.draw_color_btn.clicked.connect(self._pick_draw_color)
        self._refresh_draw_color_swatch()
        undo_stroke_btn = QPushButton("Undo last stroke")
        undo_stroke_btn.clicked.connect(self.draw_canvas.undo_stroke)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.draw_canvas.clear)
        btn_row = QHBoxLayout()
        btn_row.addWidget(QLabel("Color:"))
        btn_row.addWidget(self.draw_color_btn)
        btn_row.addStretch()
        btn_row.addWidget(undo_stroke_btn)
        btn_row.addWidget(clear_btn)
        dw.addLayout(btn_row)
        draw_hint = QLabel("You can drag the corners to resize after placing on the page.")
        draw_hint.setStyleSheet("color: #666; font-size: 11px;")
        draw_hint.setWordWrap(True)
        dw.addWidget(draw_hint)
        tabs.addTab(draw_widget, "Draw signature")

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(bb)
        self._tabs = tabs
        # Default to the Type tab and force a preview repaint so the user
        # sees the correct font/text the moment the dialog opens.
        tabs.setCurrentIndex(0)
        tabs.currentChanged.connect(self._on_tab_changed)
        # Initial paint
        self._refresh_text()
        self._refresh_font(self.type_font.currentText())

    def _refresh_type_color_swatch(self) -> None:
        c = self._type_color.name()
        self.type_color_btn.setStyleSheet(
            f"QPushButton {{ background: {c}; color: white; padding: 4px 12px; }}"
        )

    def _refresh_draw_color_swatch(self) -> None:
        c = self._draw_color.name()
        self.draw_color_btn.setStyleSheet(
            f"QPushButton {{ background: {c}; color: white; padding: 4px 12px; }}"
        )

    def _pick_type_color(self) -> None:
        c = QColorDialog.getColor(self._type_color, self, "Signature color")
        if c.isValid():
            self._type_color = c
            self._refresh_type_color_swatch()
            self.type_preview.setStyleSheet(
                f"background: white; border: 1px solid #ccc; padding: 8px; color: {c.name()};"
            )

    def _pick_draw_color(self) -> None:
        c = QColorDialog.getColor(self._draw_color, self, "Signature color")
        if c.isValid():
            self._draw_color = c
            self._refresh_draw_color_swatch()
            self.draw_canvas.set_stroke_color(c)

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 0:
            # Force a repaint so the preview is current after a tab switch.
            self._refresh_text()
            self._refresh_font(self.type_font.currentText())

    def _refresh_text(self) -> None:
        text = self.type_input.text() or "Your Name"
        # Strip any " (font failed to load)" suffix from a previous render.
        self.type_preview.setText(text)

    def _refresh_size(self, value: int) -> None:
        self._size_pt = int(value)
        f = self.type_preview.font()
        f.setPointSize(max(10, min(96, self._size_pt + 10)))
        self.type_preview.setFont(f)

    def _refresh_font(self, family: str) -> None:
        if not family:
            return
        # Already loaded → set immediately.
        if family in _loaded_font_families:
            self._apply_preview_font(family, status="")
            return
        # Try cached file on disk before going to the network.
        cached = FONT_CACHE / f"{family.replace(' ', '_')}.ttf"
        if cached.exists() and cached.stat().st_size > 0:
            if _register_font_file(family, cached):
                self._apply_preview_font(family, status="")
                return
        # Otherwise kick off a non-blocking fetch.
        self._pending_family = family
        f = QFont()
        f.setItalic(True)
        f.setPointSize(self.PREVIEW_POINT_SIZE)
        self.type_preview.setFont(f)
        self.type_status.setText(f"(loading {family}…)")
        task = _FontFetchTask(family)
        task.signals.done.connect(self._on_font_fetch_done)
        QThreadPool.globalInstance().start(task)

    def _apply_preview_font(self, family: str, status: str = "") -> None:
        f = QFont(family)
        f.setPointSize(self.PREVIEW_POINT_SIZE)
        self.type_preview.setFont(f)
        self.type_status.setText(status)

    def _on_font_fetch_done(self, family: str, path: str) -> None:
        # Drop late results for a font the user has already moved past.
        if family != self.type_font.currentText():
            return
        if path:
            ok = _register_font_file(family, Path(path))
            if ok:
                self._apply_preview_font(family, status="")
                return
        # Fallback: show system default with a visible failure indicator so
        # the user knows the choice didn't take effect.
        self.type_preview.setFont(QFont())
        self.type_status.setText(f"(font “{family}” failed to load — using system default)")

    def _accept(self):
        if self._tabs.currentIndex() == 0:
            text = self.type_input.text().strip()
            if not text:
                QMessageBox.information(self, "No name", "Type your name first.")
                return
            self.result_data = {
                "kind": "typed",
                "text": text,
                "family": self.type_font.currentText(),
                "color": self._type_color.name(),
                "size_pt": int(self._size_pt),
            }
        else:
            strokes = self.draw_canvas.normalized_strokes()
            strokes = [s for s in strokes if len(s) >= 2]
            if not strokes:
                QMessageBox.information(self, "No drawing", "Draw your signature first.")
                return
            self.result_data = {
                "kind": "drawn",
                "strokes": strokes,
                "color": self._draw_color.name(),
            }
        self.accept()


PAGE_MARGIN = 14

# PyMuPDF base14 font aliases keyed by family → (regular, bold, italic, bold-italic)
BASE14_VARIANTS = {
    "Times": ("tiro", "tibo", "tiit", "tibi"),
    "Helvetica": ("helv", "hebo", "heit", "hebi"),
    "Courier": ("cour", "cobo", "coit", "cobi"),
}


class _ResizeHandle(QGraphicsRectItem):
    """Bottom-right corner handle for resizing a TextBoxItem's text width."""

    SIZE = 10

    def __init__(self, parent: "TextBoxItem"):
        super().__init__(0, 0, self.SIZE, self.SIZE, parent)
        self.box = parent
        self.setBrush(QBrush(QColor(60, 130, 220)))
        self.setPen(QPen(QColor(255, 255, 255), 1))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAcceptHoverEvents(True)
        self._dragging = False
        self._press_scene_x = 0.0
        self._initial_text_width = 0.0
        self.hide()  # only show when parent selected

    def mousePressEvent(self, ev):
        self._dragging = True
        self._press_scene_x = ev.scenePos().x()
        self._initial_text_width = self.box.textWidth()
        # Snapshot before the resize so undo restores the prior width.
        try:
            self.box.view.window_._snapshot()
        except Exception:
            pass
        ev.accept()

    def mouseMoveEvent(self, ev):
        if not self._dragging:
            return
        delta = ev.scenePos().x() - self._press_scene_x
        new_w = max(20.0, self._initial_text_width + delta)
        self.box.setTextWidth(new_w)
        # Reflect back into PDF-space stored width
        z = self.box.view.zoom
        self.box.pdf_w = new_w / z
        self.box.position_handle()
        ev.accept()

    def mouseReleaseEvent(self, ev):
        self._dragging = False
        self.box.view.window_._mark_dirty()
        ev.accept()


class TextBoxItem(QGraphicsTextItem):
    """A movable, editable text overlay tied to a specific PDF page.
    Stays in the scene as a Qt item until baked into the PDF on save."""

    DISPLAY_NAME = "Text box"

    def __init__(
        self,
        view,
        page_idx: int,
        pdf_x: float,
        pdf_y: float,
        pdf_w: float,
        text: str = "",
        family: str = "Helvetica",
        size_pt: float = 14,
        color: QColor | None = None,
    ):
        super().__init__()
        self.view = view
        self.page_idx = page_idx
        self.pdf_x = pdf_x
        self.pdf_y = pdf_y
        self.pdf_w = pdf_w
        self.family = family
        self.size_pt = size_pt
        self.color = color or QColor(0, 0, 0)
        self.bold = False
        self.italic = False
        self.underline = False
        self.strike = False

        self.setPlainText(text)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges, True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        self._handle = _ResizeHandle(self)
        self.refresh()

    # --- presentation ---
    def refresh(self):
        """Recompute scene position, font size, and styling from PDF coords + zoom."""
        if not self.view._page_geom or self.page_idx >= len(self.view._page_geom):
            return
        top = self.view._page_geom[self.page_idx][0]
        z = self.view.zoom
        self.setPos(PAGE_MARGIN + self.pdf_x * z, top + self.pdf_y * z)
        self.setTextWidth(max(20.0, self.pdf_w * z))
        f = QFont(self.family)
        f.setPointSizeF(max(2.0, self.size_pt * z))
        f.setBold(self.bold)
        f.setItalic(self.italic)
        f.setUnderline(self.underline)
        f.setStrikeOut(self.strike)
        self.setFont(f)
        self.setDefaultTextColor(self.color)
        self.position_handle()

    def position_handle(self):
        br = self.boundingRect()
        self._handle.setPos(br.right() - _ResizeHandle.SIZE,
                            br.bottom() - _ResizeHandle.SIZE)

    def paint(self, painter, option, widget=None):
        # Draw a subtle border when selected so the user sees the box bounds.
        if self.isSelected():
            painter.save()
            pen = QPen(QColor(60, 130, 220), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect())
            painter.restore()
        super().paint(painter, option, widget)

    # --- events ---
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            geom = self.view._page_geom
            if self.page_idx < len(geom):
                top = geom[self.page_idx][0]
                z = self.view.zoom
                p = self.pos()
                self.pdf_x = (p.x() - PAGE_MARGIN) / z
                self.pdf_y = (p.y() - top) / z
                w = self.view.window_
                if not w.dirty:
                    w._mark_dirty()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._handle.setVisible(bool(value))
            # Notify window to refresh format toolbar state
            self.view.window_.refresh_format_toolbar()
        return super().itemChange(change, value)

    def _is_editing(self) -> bool:
        return bool(
            self.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
        )

    def enter_edit_mode(self, reason=Qt.FocusReason.MouseFocusReason):
        """Switch into text-editor interaction and grab focus.

        Used both by the auto-edit path (right after creation) and by the
        click-once-when-already-selected ("PowerPoint pattern") path.
        """
        if not self._is_editing():
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        # Make sure the view itself owns keyboard focus too — otherwise the
        # scene's focus item won't actually receive key events.
        try:
            self.view.setFocus(reason)
        except Exception:
            pass
        self.setFocus(reason)

    def mousePressEvent(self, ev):
        # Track press location so mouseReleaseEvent can decide whether this
        # was a click (enter edit mode) or a drag (move the box).
        self._press_pos = ev.scenePos() if ev.button() == Qt.MouseButton.LeftButton else None
        self._press_was_selected = self.isSelected()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        # Keynote/PowerPoint pattern: releasing a left-click on a textbox
        # that was already selected on press (and hasn't been dragged) enters
        # edit mode. A press on an unselected box still just selects it,
        # and a press+drag still moves it (because we only fire on no-drag).
        try:
            press = getattr(self, "_press_pos", None)
            was_sel = getattr(self, "_press_was_selected", False)
            if (
                ev.button() == Qt.MouseButton.LeftButton
                and press is not None
                and was_sel
                and not self._is_editing()
            ):
                delta = ev.scenePos() - press
                # Treat <4px movement as a click, not a drag.
                if abs(delta.x()) < 4 and abs(delta.y()) < 4:
                    super().mouseReleaseEvent(ev)
                    self.enter_edit_mode()
                    return
        finally:
            self._press_pos = None
            self._press_was_selected = False
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        # Fallback: explicit double-click also enters edit mode.
        if not self._is_editing():
            self.enter_edit_mode()
        super().mouseDoubleClickEvent(ev)

    def focusOutEvent(self, ev):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.position_handle()
        super().focusOutEvent(ev)

    # --- bake into PDF ---
    def to_pdf(self, page):
        """Render this textbox into the given fitz.Page."""
        text = self.toPlainText()
        if not text.strip():
            return
        # Pick PyMuPDF font name based on family + bold/italic.
        # Resolution chain: base14 → system-font-to-base14 alias → local
        # system font file (embedded) → Google Fonts (embedded) → helv.
        # Note: embedded system/Google fonts use the regular weight only —
        # bold/italic on those renders as regular in the saved PDF.
        b, i = self.bold, self.italic
        fontname = None
        fontfile = None
        if self.family in BASE14_VARIANTS:
            reg, bo, it_, bi = BASE14_VARIANTS[self.family]
            fontname = bi if b and i else (bo if b else (it_ if i else reg))
        elif self.family in SYSTEM_FONT_BASE14_ALIAS:
            reg, bo, it_, bi = SYSTEM_FONT_BASE14_ALIAS[self.family]
            fontname = bi if b and i else (bo if b else (it_ if i else reg))
        else:
            sys_path = find_system_font(self.family) if self.family else None
            if sys_path:
                fontname = "sys_" + re.sub(r"[^A-Za-z0-9]", "", self.family)
                fontfile = str(sys_path)
            else:
                ttf = fetch_google_font(self.family) if self.family else None
                if ttf:
                    fontname = "gf_" + re.sub(r"[^A-Za-z0-9]", "", self.family)
                    fontfile = str(ttf)
                else:
                    fontname = "helv"

        rgb = (self.color.redF(), self.color.greenF(), self.color.blueF())
        # Tall rect so wrapped text isn't truncated; height is virtually unbounded
        rect = fitz.Rect(
            self.pdf_x, self.pdf_y,
            self.pdf_x + self.pdf_w, self.pdf_y + 4000,
        )
        try:
            if fontfile:
                page.insert_font(fontname=fontname, fontfile=fontfile)
            page.insert_textbox(
                rect, text,
                fontname=fontname, fontsize=self.size_pt,
                color=rgb, align=fitz.TEXT_ALIGN_LEFT,
            )
        except Exception:
            page.insert_text(
                (self.pdf_x, self.pdf_y + self.size_pt), text,
                fontname=fontname, fontsize=self.size_pt, color=rgb,
            )
            return

        if self.underline or self.strike:
            # Use the laid-out word boxes inside our rect to drive the lines.
            try:
                words = page.get_text("words", clip=rect)
            except Exception:
                words = []
            from collections import defaultdict
            lines = defaultdict(list)
            for w in words:
                lines[(w[5], w[6])].append(w)
            for key in sorted(lines.keys()):
                ws = lines[key]
                x0 = min(w[0] for w in ws)
                y0 = min(w[1] for w in ws)
                x1 = max(w[2] for w in ws)
                y1 = max(w[3] for w in ws)
                if self.underline:
                    yu = y1 + 0.5
                    page.draw_line((x0, yu), (x1, yu),
                                    color=rgb, width=max(0.5, self.size_pt * 0.05))
                if self.strike:
                    ym = y0 + (y1 - y0) * 0.55
                    page.draw_line((x0, ym), (x1, ym),
                                    color=rgb, width=max(0.5, self.size_pt * 0.05))

    # --- serialize for undo ---
    def serialize(self) -> dict:
        return {
            "kind": "text",
            "page_idx": self.page_idx,
            "pdf_x": self.pdf_x,
            "pdf_y": self.pdf_y,
            "pdf_w": self.pdf_w,
            "text": self.toPlainText(),
            "family": self.family,
            "size_pt": self.size_pt,
            "color": self.color.name(),
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "strike": self.strike,
        }

    @classmethod
    def deserialize(cls, view, d: dict) -> "TextBoxItem":
        item = cls(
            view, d["page_idx"], d["pdf_x"], d["pdf_y"], d["pdf_w"],
            text=d.get("text", ""),
            family=d.get("family", "Helvetica"),
            size_pt=d.get("size_pt", 14),
            color=QColor(d.get("color", "#000000")),
        )
        item.bold = d.get("bold", False)
        item.italic = d.get("italic", False)
        item.underline = d.get("underline", False)
        item.strike = d.get("strike", False)
        item.refresh()
        return item


def _typed_signature_strokes(
    text: str, family: str, target_w: float, target_h: float
) -> tuple[list[list[tuple[float, float]]], float, float]:
    """Render `text` in `family` to a QPainterPath, then sample the path into
    polyline strokes normalized 0..1 against the rendered text's bbox.

    Returns (strokes, pdf_w, pdf_h). pdf_w/pdf_h are sized so the typed text
    fits inside (target_w, target_h) without distorting its natural aspect.
    Empty strokes returned if the path is empty (caller falls back).
    """
    if not text.strip():
        return [], 0.0, 0.0
    f = QFont(family)
    f.setPointSize(48)
    path = QPainterPath()
    path.addText(0.0, 0.0, f, text)
    br = path.boundingRect()
    if br.width() <= 0 or br.height() <= 0:
        return [], 0.0, 0.0
    polys = path.toSubpathPolygons()
    strokes: list[list[tuple[float, float]]] = []
    bw = br.width()
    bh = br.height()
    bx = br.x()
    by = br.y()
    for poly in polys:
        s: list[tuple[float, float]] = []
        for pt in poly:
            nx = (pt.x() - bx) / bw
            ny = (pt.y() - by) / bh
            s.append((max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))))
        if len(s) >= 2:
            strokes.append(s)
    if not strokes:
        return [], 0.0, 0.0
    # Fit inside target rect preserving aspect.
    natural_aspect = bw / bh
    rect_aspect = (target_w / target_h) if target_h > 0 else natural_aspect
    if rect_aspect > natural_aspect:
        sig_h = target_h
        sig_w = sig_h * natural_aspect
    else:
        sig_w = target_w
        sig_h = sig_w / natural_aspect
    return strokes, sig_w, sig_h


class _SignatureResizeHandle(QGraphicsRectItem):
    """Bottom-right corner handle for resizing a SignatureItem in 2D."""

    SIZE = 10

    def __init__(self, parent: "SignatureItem"):
        super().__init__(0, 0, self.SIZE, self.SIZE, parent)
        self.sig = parent
        self.setBrush(QBrush(QColor(60, 130, 220)))
        self.setPen(QPen(QColor(255, 255, 255), 1))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAcceptHoverEvents(True)
        self._dragging = False
        self._press_scene = QPointF(0.0, 0.0)
        self._initial_pdf_w = 0.0
        self._initial_pdf_h = 0.0
        self.hide()

    def mousePressEvent(self, ev):
        self._dragging = True
        self._press_scene = ev.scenePos()
        self._initial_pdf_w = self.sig.pdf_w
        self._initial_pdf_h = self.sig.pdf_h
        try:
            self.sig.view.window_._snapshot()
        except Exception:
            pass
        ev.accept()

    def mouseMoveEvent(self, ev):
        if not self._dragging:
            return
        z = self.sig.view.zoom
        delta = ev.scenePos() - self._press_scene
        new_w = max(10.0, self._initial_pdf_w + delta.x() / z)
        new_h = max(10.0, self._initial_pdf_h + delta.y() / z)
        self.sig.pdf_w = new_w
        self.sig.pdf_h = new_h
        self.sig.refresh()
        ev.accept()

    def mouseReleaseEvent(self, ev):
        self._dragging = False
        try:
            self.sig.view.window_._mark_dirty()
        except Exception:
            pass
        ev.accept()


class SignatureItem(QGraphicsPathItem):
    """A drawn (mouse/trackpad) signature overlay. Strokes stored in PDF-space coords."""

    DISPLAY_NAME = "Signature"

    def __init__(self, view, page_idx: int, pdf_x: float, pdf_y: float,
                 pdf_w: float, pdf_h: float, strokes: list, color: QColor | None = None,
                 width_pt: float = 1.5):
        super().__init__()
        self.view = view
        self.page_idx = page_idx
        self.pdf_x = pdf_x
        self.pdf_y = pdf_y
        self.pdf_w = pdf_w
        self.pdf_h = pdf_h
        # strokes: list of list of (x, y) in normalized 0..1 coords relative to bbox
        self.strokes = strokes
        self.color = color or QColor(0, 0, 0)
        self.width_pt = width_pt

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges, True)
        self._handle = _SignatureResizeHandle(self)
        self.refresh()

    def refresh(self):
        if not self.view._page_geom or self.page_idx >= len(self.view._page_geom):
            return
        top = self.view._page_geom[self.page_idx][0]
        z = self.view.zoom
        self.setPos(PAGE_MARGIN + self.pdf_x * z, top + self.pdf_y * z)
        # Build a QPainterPath from normalized strokes scaled to pdf bbox * zoom
        path = QPainterPath()
        w_px = self.pdf_w * z
        h_px = self.pdf_h * z
        for stroke in self.strokes:
            if not stroke:
                continue
            sx, sy = stroke[0]
            path.moveTo(sx * w_px, sy * h_px)
            for x, y in stroke[1:]:
                path.lineTo(x * w_px, y * h_px)
        self.setPath(path)
        pen = QPen(self.color, max(1.0, self.width_pt * z))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.position_handle()

    def position_handle(self):
        z = self.view.zoom
        w_px = self.pdf_w * z
        h_px = self.pdf_h * z
        self._handle.setPos(
            w_px - _SignatureResizeHandle.SIZE,
            h_px - _SignatureResizeHandle.SIZE,
        )

    def boundingRect(self):
        z = self.view.zoom
        w_px = self.pdf_w * z
        h_px = self.pdf_h * z
        # Pad by half the pen width so brush strokes near edges aren't clipped.
        pad = max(1.0, self.width_pt * z) / 2 + 1.0
        return QRectF(-pad, -pad, w_px + 2 * pad, h_px + 2 * pad)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.save()
            pen = QPen(QColor(60, 130, 220), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            z = self.view.zoom
            painter.drawRect(QRectF(0, 0, self.pdf_w * z, self.pdf_h * z))
            painter.restore()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            geom = self.view._page_geom
            if self.page_idx < len(geom):
                top = geom[self.page_idx][0]
                z = self.view.zoom
                p = self.pos()
                self.pdf_x = (p.x() - PAGE_MARGIN) / z
                self.pdf_y = (p.y() - top) / z
                w = self.view.window_
                if not w.dirty:
                    w._mark_dirty()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._handle.setVisible(bool(value))
            self.view.window_.refresh_format_toolbar()
        return super().itemChange(change, value)

    def to_pdf(self, page):
        rgb = (self.color.redF(), self.color.greenF(), self.color.blueF())
        for stroke in self.strokes:
            if len(stroke) < 2:
                continue
            pts = [
                (self.pdf_x + x * self.pdf_w, self.pdf_y + y * self.pdf_h)
                for (x, y) in stroke
            ]
            try:
                page.draw_polyline(pts, color=rgb, width=self.width_pt)
            except Exception:
                # Fallback: pairwise lines
                for a, b in zip(pts[:-1], pts[1:]):
                    page.draw_line(a, b, color=rgb, width=self.width_pt)

    def serialize(self) -> dict:
        return {
            "kind": "signature",
            "page_idx": self.page_idx,
            "pdf_x": self.pdf_x, "pdf_y": self.pdf_y,
            "pdf_w": self.pdf_w, "pdf_h": self.pdf_h,
            "strokes": self.strokes,
            "color": self.color.name(),
            "width_pt": self.width_pt,
        }

    @classmethod
    def deserialize(cls, view, d: dict) -> "SignatureItem":
        return cls(
            view, d["page_idx"], d["pdf_x"], d["pdf_y"],
            d["pdf_w"], d["pdf_h"], d["strokes"],
            color=QColor(d.get("color", "#000000")),
            width_pt=d.get("width_pt", 1.5),
        )


class _ImageResizeHandle(QGraphicsRectItem):
    """Bottom-right corner handle for resizing an ImageOverlayItem in 2D."""

    SIZE = 10

    def __init__(self, parent: "ImageOverlayItem"):
        super().__init__(0, 0, self.SIZE, self.SIZE, parent)
        self.img = parent
        self.setBrush(QBrush(QColor(60, 130, 220)))
        self.setPen(QPen(QColor(255, 255, 255), 1))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAcceptHoverEvents(True)
        self._dragging = False
        self._press_scene = QPointF(0.0, 0.0)
        self._initial_pdf_w = 0.0
        self._initial_pdf_h = 0.0
        self.hide()

    def mousePressEvent(self, ev):
        self._dragging = True
        self._press_scene = ev.scenePos()
        self._initial_pdf_w = self.img.pdf_w
        self._initial_pdf_h = self.img.pdf_h
        try:
            self.img.view.window_._snapshot()
        except Exception:
            pass
        ev.accept()

    def mouseMoveEvent(self, ev):
        if not self._dragging:
            return
        z = self.img.view.zoom
        delta = ev.scenePos() - self._press_scene
        new_w = max(10.0, self._initial_pdf_w + delta.x() / z)
        new_h = max(10.0, self._initial_pdf_h + delta.y() / z)
        self.img.pdf_w = new_w
        self.img.pdf_h = new_h
        self.img.refresh()
        ev.accept()

    def mouseReleaseEvent(self, ev):
        self._dragging = False
        try:
            self.img.view.window_._mark_dirty()
        except Exception:
            pass
        ev.accept()


class ImageOverlayItem(QGraphicsPixmapItem):
    """A movable, resizable image overlay tied to a specific PDF page.

    Stays as a Qt scene item until baked into the PDF on save.
    """

    DISPLAY_NAME = "Image"

    def __init__(self, view, page_idx: int, path: str,
                 pdf_x: float, pdf_y: float, pdf_w: float, pdf_h: float):
        super().__init__()
        self.view = view
        self.page_idx = page_idx
        self.path = path
        self.pdf_x = pdf_x
        self.pdf_y = pdf_y
        self.pdf_w = pdf_w
        self.pdf_h = pdf_h
        self._source_pixmap = QPixmap(path)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges, True)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._handle = _ImageResizeHandle(self)
        self.refresh()

    def refresh(self):
        if not self.view._page_geom or self.page_idx >= len(self.view._page_geom):
            return
        top = self.view._page_geom[self.page_idx][0]
        z = self.view.zoom
        self.setPos(PAGE_MARGIN + self.pdf_x * z, top + self.pdf_y * z)
        w_px = max(1, int(self.pdf_w * z))
        h_px = max(1, int(self.pdf_h * z))
        if not self._source_pixmap.isNull():
            self.setPixmap(self._source_pixmap.scaled(
                w_px, h_px,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self.position_handle()

    def position_handle(self):
        z = self.view.zoom
        self._handle.setPos(
            self.pdf_w * z - _ImageResizeHandle.SIZE,
            self.pdf_h * z - _ImageResizeHandle.SIZE,
        )

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.save()
            pen = QPen(QColor(60, 130, 220), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            z = self.view.zoom
            painter.drawRect(QRectF(0, 0, self.pdf_w * z, self.pdf_h * z))
            painter.restore()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            geom = self.view._page_geom
            if self.page_idx < len(geom):
                top = geom[self.page_idx][0]
                z = self.view.zoom
                p = self.pos()
                self.pdf_x = (p.x() - PAGE_MARGIN) / z
                self.pdf_y = (p.y() - top) / z
                w = self.view.window_
                if not w.dirty:
                    w._mark_dirty()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._handle.setVisible(bool(value))
            self.view.window_.refresh_format_toolbar()
        return super().itemChange(change, value)

    def to_pdf(self, page):
        rect = fitz.Rect(
            self.pdf_x, self.pdf_y,
            self.pdf_x + self.pdf_w, self.pdf_y + self.pdf_h,
        )
        page.insert_image(rect, filename=self.path, keep_proportion=True)

    def serialize(self) -> dict:
        return {
            "kind": "image",
            "page_idx": self.page_idx,
            "path": self.path,
            "pdf_x": self.pdf_x,
            "pdf_y": self.pdf_y,
            "pdf_w": self.pdf_w,
            "pdf_h": self.pdf_h,
        }

    @classmethod
    def deserialize(cls, view, d: dict) -> "ImageOverlayItem":
        return cls(
            view, d["page_idx"], d["path"],
            d["pdf_x"], d["pdf_y"], d["pdf_w"], d["pdf_h"],
        )


class PDFView(QGraphicsView):
    """Renders all PDF pages stacked vertically and dispatches mouse interactions."""

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window_ = window
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QBrush(QColor(60, 60, 60)))

        self.doc: fitz.Document | None = None
        self.page_idx = 0  # current visible page (for status bar)
        self.zoom = 1.6
        self.mode = "select"
        self._start_scene: QPointF | None = None
        self._start_page: int | None = None
        self._start_pdf: tuple[float, float] | None = None
        self._rubber: QGraphicsRectItem | None = None
        # per-page (top_y_in_scene, bottom_y_in_scene, pdf_width, pdf_height)
        self._page_geom: list[tuple[float, float, float, float]] = []
        # Floating overlay items (TextBoxItem, SignatureItem) — survive render_all()
        self.overlays: list = []
        # Spacebar-held → temporary pan mode
        self._space_pan = False
        self._saved_drag_mode = QGraphicsView.DragMode.NoDrag

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().valueChanged.connect(self._update_visible_page)
        self.set_mode("select")

    def clear_overlays(self):
        """Detach and forget all floating overlays — call before swapping documents."""
        for ov in self.overlays:
            # Release editor + focus first so Qt doesn't warn about removing a
            # focused item that's actively in TextEditorInteraction mode.
            if isinstance(ov, QGraphicsTextItem):
                ov.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                ov.clearFocus()
            if ov.scene() is self.scene_:
                self.scene_.removeItem(ov)
        self.overlays = []
        # Search highlights belong to the scene that's about to be cleared too.
        self._search_items = []

    def load(self, path: str) -> bool:
        self.clear_overlays()
        if self.doc:
            self.doc.close()
            self.doc = None
        doc = fitz.open(path)
        if doc.needs_pass:
            pwd, ok = QInputDialog.getText(
                self, "Password required",
                f"Enter password for {os.path.basename(path)}:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not doc.authenticate(pwd):
                doc.close()
                QMessageBox.warning(
                    self, "Cannot open",
                    "Wrong password — file not opened.",
                )
                return False
        self.doc = doc
        self.page_idx = 0
        self.render_all()
        return True

    def page_count(self) -> int:
        return len(self.doc) if self.doc else 0

    def set_mode(self, mode: str):
        self.mode = mode
        if mode == "select":
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def render_all(self, preserve_scroll: bool = False):
        """Render every page stacked vertically. Optionally preserve scroll fraction."""
        if not self.doc:
            return
        # Save scroll fraction so zoom/edits don't jump the user to the top.
        v_bar = self.verticalScrollBar()
        h_bar = self.horizontalScrollBar()
        v_frac = v_bar.value() / max(1, v_bar.maximum())
        h_frac = h_bar.value() / max(1, h_bar.maximum())

        # Detach overlay items so scene.clear() doesn't delete them.
        for ov in self.overlays:
            if ov.scene() is self.scene_:
                self.scene_.removeItem(ov)

        self.scene_.clear()
        # scene.clear() destroyed the C++ objects backing _search_items.
        self._search_items = []
        self._page_geom = []
        mat = fitz.Matrix(self.zoom, self.zoom)
        y = PAGE_MARGIN
        max_w = 0
        for i in range(len(self.doc)):
            page = self.doc[i]
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format.Format_RGB888,
            ).copy()
            pm = QPixmap.fromImage(img)
            item = self.scene_.addPixmap(pm)
            item.setPos(PAGE_MARGIN, y)
            self._page_geom.append((y, y + pm.height(), page.rect.width, page.rect.height))
            y += pm.height() + PAGE_MARGIN
            max_w = max(max_w, pm.width())
        self.scene_.setSceneRect(0, 0, max_w + 2 * PAGE_MARGIN, y)

        # Re-attach overlays at their (possibly new-zoom) positions, but only
        # if their page_idx is still in range (a page may have been deleted).
        kept = []
        for ov in self.overlays:
            if 0 <= ov.page_idx < len(self._page_geom):
                ov.refresh()
                self.scene_.addItem(ov)
                kept.append(ov)
        self.overlays = kept

        # Search highlights are direct scene children; scene.clear() killed them.
        # Redraw if there's an active search.
        win = self.window_
        if getattr(win, "_search_results", None):
            self._search_items = []  # the old refs were deleted by scene.clear()
            self.show_search_overlays(win._search_results, win._search_idx)

        if preserve_scroll:
            v_bar.setValue(int(v_frac * v_bar.maximum()))
            h_bar.setValue(int(h_frac * h_bar.maximum()))

    def scroll_to_page(self, idx: int):
        if not self.doc or not self._page_geom:
            return
        idx = max(0, min(idx, len(self._page_geom) - 1))
        top = self._page_geom[idx][0]
        self.verticalScrollBar().setValue(
            int(top - PAGE_MARGIN) if top > PAGE_MARGIN else 0
        )
        self.page_idx = idx

    def _update_visible_page(self):
        """Track which page the viewport is centered on for the status bar."""
        if not self._page_geom:
            return
        center_y = self.mapToScene(self.viewport().rect().center()).y()
        for i, (top, bottom, _, _) in enumerate(self._page_geom):
            if top <= center_y <= bottom:
                if i != self.page_idx:
                    self.page_idx = i
                    self.window_._refresh_page_label()
                return

    def _locate(self, scene_pt: QPointF) -> tuple[int, float, float] | None:
        """Map a scene point to (page_idx, pdf_x, pdf_y), or None if not on a page."""
        for i, (top, bottom, w, h) in enumerate(self._page_geom):
            if top <= scene_pt.y() <= bottom:
                px = (scene_pt.x() - PAGE_MARGIN) / self.zoom
                py = (scene_pt.y() - top) / self.zoom
                if 0 <= px <= w and 0 <= py <= h:
                    return i, px, py
                return None
        return None

    def _project_to_page(self, scene_pt: QPointF, page_idx: int) -> tuple[float, float]:
        """Project an arbitrary scene point onto the bounds of a specific page."""
        top, bottom, w, h = self._page_geom[page_idx]
        px = max(0.0, min(w, (scene_pt.x() - PAGE_MARGIN) / self.zoom))
        py = max(0.0, min(h, (scene_pt.y() - top) / self.zoom))
        return px, py

    def _pdf_rect_to_scene(self, page_idx: int, r) -> QRectF:
        top = self._page_geom[page_idx][0]
        return QRectF(
            PAGE_MARGIN + r.x0 * self.zoom,
            top + r.y0 * self.zoom,
            (r.x1 - r.x0) * self.zoom,
            (r.y1 - r.y0) * self.zoom,
        )

    def show_search_overlays(self, results, current_idx=-1):
        """Draw transient overlays for search results. Cleared on next render_all()."""
        # remove any prior overlays
        for it in getattr(self, "_search_items", []):
            try:
                self.scene_.removeItem(it)
            except Exception:
                pass
        self._search_items = []
        for i, (page_idx, rect) in enumerate(results):
            if page_idx >= len(self._page_geom):
                continue
            scene_rect = self._pdf_rect_to_scene(page_idx, rect)
            item = QGraphicsRectItem(scene_rect)
            item.setPen(QPen(Qt.PenStyle.NoPen))
            if i == current_idx:
                item.setBrush(QBrush(QColor(255, 140, 0, 130)))  # orange = current
            else:
                item.setBrush(QBrush(QColor(255, 230, 0, 110)))  # yellow = matches
            self.scene_.addItem(item)
            self._search_items.append(item)

    def scroll_to_pdf_rect(self, page_idx: int, rect):
        scene_rect = self._pdf_rect_to_scene(page_idx, rect)
        self.centerOn(scene_rect.center())

    # --- keyboard ---
    def keyPressEvent(self, ev):
        # Spacebar held → temporarily pan the canvas (Figma/Illustrator convention)
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat() and not self._space_pan:
            # Don't pan if user is typing inside a text box, or if a mouse
            # button is currently held (prevents mid-drag mode flips).
            if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
                return super().keyPressEvent(ev)
            focused = self.scene_.focusItem()
            if isinstance(focused, QGraphicsTextItem) and bool(
                focused.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
            ):
                return super().keyPressEvent(ev)
            self._space_pan = True
            self._saved_drag_mode = self.dragMode()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            ev.accept()
            return
        # Delete / Backspace removes selected overlays (when not editing text)
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            # Decide what (if anything) to delete first so we only snapshot
            # when there's actually a deletion.
            to_remove = []
            for it in self.overlays:
                if not it.isSelected():
                    continue
                if isinstance(it, QGraphicsTextItem) and bool(
                    it.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
                ):
                    continue  # don't delete the box while typing in it
                to_remove.append(it)
            if to_remove:
                self.window_._snapshot()
                for it in to_remove:
                    self.scene_.removeItem(it)
                    self.overlays.remove(it)
                self.window_._mark_dirty()
                self.window_.refresh_format_toolbar()
                ev.accept()
                return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat() and self._space_pan:
            # Don't switch drag modes mid-drag — wait for mouse release.
            if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
                return super().keyReleaseEvent(ev)
            self._space_pan = False
            self.setDragMode(self._saved_drag_mode)
            cursor = (
                Qt.CursorShape.ArrowCursor if self.mode == "select"
                else Qt.CursorShape.CrossCursor
            )
            self.viewport().setCursor(cursor)
            ev.accept()
            return
        super().keyReleaseEvent(ev)

    # --- mouse ---
    def _hit_widget(self, ev):
        """Map a mouse event to (page_idx, widget) if it lands on a form field."""
        if not self.doc:
            return None
        sp = self.mapToScene(ev.pos())
        loc = self._locate(sp)
        if loc is None:
            return None
        page_idx, px, py = loc
        widget = self.window_._widget_at(page_idx, px, py)
        if widget is None:
            return None
        return page_idx, widget

    def mousePressEvent(self, ev):
        # Right-click on a form widget (in any mode that lets us see the page) →
        # contextMenuEvent handles it. Don't start a rubber-band drag here.
        if (
            ev.button() == Qt.MouseButton.RightButton
            and not self._space_pan
            and self.doc
        ):
            hit = self._hit_widget(ev)
            if hit is not None:
                ev.accept()
                return
        if self._space_pan or self.mode == "select" or not self.doc:
            return super().mousePressEvent(ev)
        sp = self.mapToScene(ev.pos())
        loc = self._locate(sp)
        if loc is None:
            return  # click landed in the gutter between pages
        self._start_scene = sp
        self._start_page, sx, sy = loc
        self._start_pdf = (sx, sy)

        if self.mode in ("erase", "form-text", "form-check", "highlight",
                          "underline", "strikeout",
                          "add-text", "signature", "image"):
            if self.mode == "highlight":
                line_color = QColor(245, 220, 20, 220)
                fill = QColor(245, 220, 20, 90)
            elif self.mode in ("underline", "strikeout"):
                line_color = QColor(60, 130, 220, 220)
                fill = QColor(60, 130, 220, 30)
            elif self.mode in ("add-text", "signature", "image"):
                line_color = QColor(60, 130, 220, 220)
                fill = QColor(60, 130, 220, 50)
            else:
                line_color = QColor(220, 60, 60, 220)
                fill = QColor(220, 60, 60, 50)
            self._rubber = QGraphicsRectItem(QRectF(sp, sp))
            self._rubber.setPen(QPen(line_color, 1, Qt.PenStyle.DashLine))
            self._rubber.setBrush(QBrush(fill))
            self.scene_.addItem(self._rubber)

    def mouseMoveEvent(self, ev):
        if self._start_scene is not None and self._rubber is not None:
            cur = self.mapToScene(ev.pos())
            self._rubber.setRect(QRectF(self._start_scene, cur).normalized())
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if (
            self._space_pan
            or self.mode == "select"
            or not self.doc
            or self._start_scene is None
            or self._start_page is None
            or self._start_pdf is None
        ):
            self._start_scene = self._start_page = self._start_pdf = None
            if self._rubber is not None:
                self.scene_.removeItem(self._rubber)
                self._rubber = None
            return super().mouseReleaseEvent(ev)

        end_scene = self.mapToScene(ev.pos())
        # End point is clamped to the start page so drags can't cross page boundaries.
        ex, ey = self._project_to_page(end_scene, self._start_page)
        sx, sy = self._start_pdf
        page = self._start_page
        rx0, ry0, rx1, ry1 = min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey)

        # Tear down the rubber band BEFORE dispatching, since the action may
        # re-render the scene and invalidate this item.
        if self._rubber is not None:
            self.scene_.removeItem(self._rubber)
            self._rubber = None
        mode = self.mode
        self._start_scene = self._start_page = self._start_pdf = None

        if mode == "add-text":
            self.window_.do_add_text(page, rx0, ry0, rx1, ry1)
        elif mode == "signature":
            self.window_.do_signature(page, rx0, ry0, rx1, ry1)
        elif mode == "image":
            self.window_.do_insert_image(page, rx0, ry0, rx1, ry1)
        elif mode == "erase":
            self.window_.do_erase(page, rx0, ry0, rx1, ry1)
        elif mode == "highlight":
            self.window_.do_highlight(page, rx0, ry0, rx1, ry1)
        elif mode == "underline":
            self.window_.do_underline(page, rx0, ry0, rx1, ry1)
        elif mode == "strikeout":
            self.window_.do_strikeout(page, rx0, ry0, rx1, ry1)
        elif mode == "sticky":
            self.window_.do_sticky(page, sx, sy)
        elif mode == "form-text":
            self.window_.do_form_text(page, rx0, ry0, rx1, ry1)
        elif mode == "form-multiline":
            self.window_.do_form_multiline(page, rx0, ry0, rx1, ry1)
        elif mode == "form-check":
            self.window_.do_form_check(page, rx0, ry0, rx1, ry1)
        elif mode == "form-radio":
            self.window_.do_form_radio(page, rx0, ry0, rx1, ry1)
        elif mode == "form-combo":
            self.window_.do_form_combo(page, rx0, ry0, rx1, ry1)
        elif mode == "form-list":
            self.window_.do_form_list(page, rx0, ry0, rx1, ry1)
        elif mode == "form-signature":
            self.window_.do_form_signature(page, rx0, ry0, rx1, ry1)
        elif mode == "form-date":
            self.window_.do_form_date(page, rx0, ry0, rx1, ry1)
        elif mode == "form-button":
            self.window_.do_form_button(page, rx0, ry0, rx1, ry1)

    def mouseDoubleClickEvent(self, ev):
        # Double-click a form field in select mode → open Field Properties.
        if (
            self.mode == "select"
            and not self._space_pan
            and self.doc
            and ev.button() == Qt.MouseButton.LeftButton
        ):
            hit = self._hit_widget(ev)
            if hit is not None:
                page_idx, widget = hit
                ev.accept()
                self.window_.edit_widget_properties(page_idx, widget)
                return
        super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev):
        if not self.doc or self._space_pan:
            return super().contextMenuEvent(ev)
        sp = self.mapToScene(ev.pos())
        loc = self._locate(sp)
        if loc is None:
            return super().contextMenuEvent(ev)
        page_idx, px, py = loc
        widget = self.window_._widget_at(page_idx, px, py)
        if widget is None:
            return super().contextMenuEvent(ev)
        menu = QMenu(self)
        edit_act = menu.addAction("Field Properties…")
        del_act = menu.addAction("Delete Field")
        chosen = menu.exec(ev.globalPos())
        if chosen is edit_act:
            self.window_.edit_widget_properties(page_idx, widget)
        elif chosen is del_act:
            self.window_.delete_widget(page_idx, widget)
        ev.accept()

    def wheelEvent(self, ev):
        mods = ev.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            factor = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.zoom = max(0.3, min(self.zoom * factor, 6.0))
            self.render_all(preserve_scroll=True)
        else:
            super().wheelEvent(ev)


# PDF /F annotation flag for "Hidden" — bit 2 (value 2). PyMuPDF doesn't expose
# a PDF_FIELD_DISPLAY_HIDDEN constant, so we use the raw spec value.
_PDF_FIELD_DISPLAY_HIDDEN = 1  # fitz field_display 0=visible, 1=hidden, 2=no-print, 3=no-view


# Acrobat AF* JavaScript helpers (AFNumber_Format / AFDate_FormatEx /
# AFSpecial_Format) live in Adobe Reader's built-in JS environment. They are
# the de facto standard for PDF field formatting; non-Acrobat readers may not
# implement them, in which case the field still saves/displays raw text.
_FORMAT_SCRIPTS = {
    "Number": (
        'AFNumber_Format(2, 0, 0, 0, "$", true);',
        'AFNumber_Keystroke(2, 0, 0, 0, "$", true);',
    ),
    "Date": (
        'AFDate_FormatEx("mm/dd/yyyy");',
        'AFDate_KeystrokeEx("mm/dd/yyyy");',
    ),
    "Zip":   ("AFSpecial_Format(0);", "AFSpecial_Keystroke(0);"),
    "Phone": ("AFSpecial_Format(2);", "AFSpecial_Keystroke(2);"),
    "SSN":   ("AFSpecial_Format(1);", "AFSpecial_Keystroke(1);"),
}

_CALC_OPS = ["None", "Sum", "Product", "Average", "Minimum", "Maximum"]


def _build_calc_script(op: str, sources: list[str]) -> str:
    """Generate a JS calculation script Adobe Reader executes on field change.

    Result is assigned to widget.script_calc. Empty string for op=None or
    no sources — the caller writes that back to clear any prior calc.
    """
    if op == "None" or not sources:
        return ""
    lines = ["var v = 0;"]
    if op == "Sum":
        for s in sources:
            lines.append(f'v += Number(this.getField("{s}").value || 0);')
        lines.append("event.value = v;")
    elif op == "Product":
        lines = ["var v = 1;"]
        for s in sources:
            lines.append(f'v *= Number(this.getField("{s}").value || 0);')
        lines.append("event.value = v;")
    elif op == "Average":
        for s in sources:
            lines.append(f'v += Number(this.getField("{s}").value || 0);')
        lines.append(f"event.value = v / {len(sources)};")
    elif op == "Minimum":
        lines = [f'var v = Number(this.getField("{sources[0]}").value || 0);']
        for s in sources[1:]:
            lines.append(
                f'v = Math.min(v, Number(this.getField("{s}").value || 0));'
            )
        lines.append("event.value = v;")
    elif op == "Maximum":
        lines = [f'var v = Number(this.getField("{sources[0]}").value || 0);']
        for s in sources[1:]:
            lines.append(
                f'v = Math.max(v, Number(this.getField("{s}").value || 0));'
            )
        lines.append("event.value = v;")
    return "\n".join(lines)


def _parse_calc_script(script: str) -> tuple[str, list[str]]:
    """Reverse of _build_calc_script — returns (op, sources). ('None', []) if
    nothing recognizable. Used to repopulate the dialog when reopening a
    field that already has a calc."""
    if not script:
        return "None", []
    refs = re.findall(r'getField\("([^"]+)"\)', script)
    sources = list(dict.fromkeys(refs))  # preserve order, dedupe
    if not sources:
        return "None", []
    if "Math.min" in script:
        return "Minimum", sources
    if "Math.max" in script:
        return "Maximum", sources
    if "*=" in script:
        return "Product", sources
    if f"/ {len(sources)}" in script:
        return "Average", sources
    return "Sum", sources


def _collect_text_field_names(doc: "fitz.Document") -> list[str]:
    """Return all unique TEXT field names across all pages, sorted."""
    if doc is None:
        return []
    seen: list[str] = []
    s: set[str] = set()
    for i in range(len(doc)):
        try:
            page = doc[i]
        except Exception:
            continue
        for w in page.widgets():
            if w.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
                continue
            name = w.field_name
            if name and name not in s:
                s.add(name)
                seen.append(name)
    return sorted(seen)


def _all_field_names(doc: "fitz.Document") -> set[str]:
    out: set[str] = set()
    if doc is None:
        return out
    for i in range(len(doc)):
        try:
            page = doc[i]
        except Exception:
            continue
        for w in page.widgets():
            if w.field_name:
                out.add(w.field_name)
    return out


DEFAULT_FIELD_NAME_PATTERN = "{type}_{n}"
DEFAULT_FIELD_NAME_PATTERN_KEY = "defaultFieldNamePattern"
AUTO_OPEN_FIELD_PROPERTIES_KEY = "autoOpenFieldProperties"
FORM_BUILDER_PANEL_DEFAULT_VISIBLE_KEY = "formBuilderPanelDefaultVisible"


def _unique_field_name(doc: "fitz.Document", base: str) -> str:
    """Return a unique field name based on the user's pattern setting.

    Pattern placeholders: ``{type}`` (the `base` arg, e.g. "Text") and
    ``{n}`` (the smallest positive integer that yields a name not already
    in use). Defaults to ``{type}_{n}``.
    """
    try:
        pat = QSettings().value(DEFAULT_FIELD_NAME_PATTERN_KEY, DEFAULT_FIELD_NAME_PATTERN)
        if not isinstance(pat, str) or not pat.strip():
            pat = DEFAULT_FIELD_NAME_PATTERN
    except Exception:
        pat = DEFAULT_FIELD_NAME_PATTERN
    if "{n}" not in pat:
        pat = pat + "_{n}"
    used = _all_field_names(doc)
    n = 1
    while True:
        try:
            name = pat.format(type=base, n=n)
        except Exception:
            name = f"{base}_{n}"
        if name not in used:
            return name
        n += 1


@contextlib.contextmanager
def _bound_widget(doc, page_idx, xref):
    """Yield (page, widget) with the page guaranteed live for the block.

    fitz.Widget instances obtained from page.widgets() raise
    'annotation not bound to any page' once the originating page reference
    is GC'd. Callers that mutate a widget across function boundaries must
    use this helper so the page binding survives the mutation.
    """
    page = doc[page_idx]
    for w in page.widgets():
        if w.xref == xref:
            yield page, w
            return
    raise KeyError(f"widget xref {xref} not on page {page_idx}")


def _set_radio_on_state(doc: "fitz.Document", widget_xref: int, on_state: str) -> None:
    """Rename the /AP/N on-state key for a radio. PyMuPDF ignores
    button_caption when adding radio widgets — both kids get /Yes by default,
    which breaks Adobe's mutual-exclusivity (kids in a group must have
    distinct on-state names per PDF 1.7 §12.7.4.2.3). We rewrite the /AP/N
    dict in-place by string-substituting the literal '/Yes ' marker.
    """
    if not on_state or on_state == "Off":
        return
    try:
        kind, ap = doc.xref_get_key(widget_xref, "AP")
    except Exception:
        return
    if not ap or "/Yes " not in ap:
        return
    new_ap = ap.replace("/Yes ", f"/{on_state} ")
    doc.xref_set_key(widget_xref, "AP", new_ap)
    doc.xref_set_key(widget_xref, "AS", "/Off")


def _link_radio_group(doc: "fitz.Document", group_name: str) -> int | None:
    """Manually link all radio kids sharing `group_name` under one parent
    field via /Parent/Kids xref edits. PDF 1.7 §12.7.4.2.3.

    PyMuPDF does NOT auto-link radios sharing field_name — each becomes its
    own top-level field. To make them mutually exclusive we have to:
      1. allocate a parent field xref with /T=group_name, /FT=/Btn,
         /Ff=(1<<15) (Radio bit), /Kids=[...]
      2. set /Parent on each kid, drop the kid's own /T (inherited)
      3. point /AcroForm/Fields at the parent (replace the kid entries)

    Returns the new parent xref, or None if fewer than 2 kids match
    (nothing to group) or doc is missing.
    """
    if doc is None or not group_name:
        return None
    kid_xrefs: list[int] = []
    for i in range(len(doc)):
        try:
            page = doc[i]
        except Exception:
            continue
        for w in page.widgets():
            if w.field_type != fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                continue
            if w.field_name == group_name:
                kid_xrefs.append(w.xref)
    if len(kid_xrefs) < 2:
        return None
    # Was the previous AcroForm/Fields list seeded with these xrefs? Strip.
    catalog = doc.pdf_catalog()
    try:
        kind, af = doc.xref_get_key(catalog, "AcroForm")
    except Exception:
        kind, af = ("null", "null")
    parent_xref = doc.get_new_xref()
    kid_refs = " ".join(f"{x} 0 R" for x in kid_xrefs)
    radio_bit = 1 << 15  # PDF spec: /Ff bit 16 = Radio
    doc.update_object(
        parent_xref,
        f"<< /T ({group_name}) /FT /Btn /Ff {radio_bit} "
        f"/Kids [ {kid_refs} ] /V /Off >>",
    )
    for x in kid_xrefs:
        doc.xref_set_key(x, "Parent", f"{parent_xref} 0 R")
        doc.xref_set_key(x, "T", "null")
        doc.xref_set_key(x, "AS", "/Off")
    # Rewrite /AcroForm/Fields: drop the kid entries, add the parent.
    # Simplest correct rewrite is to re-derive the full top-level list by
    # walking pages. Fields with /Parent are NOT top-level.
    top_level: list[int] = [parent_xref]
    seen = {parent_xref, *kid_xrefs}
    for i in range(len(doc)):
        try:
            page = doc[i]
        except Exception:
            continue
        for w in page.widgets():
            if w.xref in seen:
                continue
            # Only top-level (no parent) fields go in /AcroForm/Fields.
            try:
                ptype, _ = doc.xref_get_key(w.xref, "Parent")
            except Exception:
                ptype = "null"
            if ptype == "null":
                top_level.append(w.xref)
                seen.add(w.xref)
    refs = " ".join(f"{x} 0 R" for x in top_level)
    doc.xref_set_key(catalog, "AcroForm", f"<< /Fields [ {refs} ] >>")
    return parent_xref


def _radio_parent_xref(doc: "fitz.Document", widget_xref: int) -> int | None:
    """Return the /Parent xref of a radio kid, or None if the kid has no /Parent."""
    try:
        kind, val = doc.xref_get_key(widget_xref, "Parent")
    except Exception:
        return None
    if kind != "xref":
        return None
    m = re.match(r"\s*(\d+)\s+0\s+R", val or "")
    if not m:
        return None
    return int(m.group(1))


def _rename_radio_group(doc: "fitz.Document", widget_xref: int, new_name: str) -> bool:
    """Rename a grouped radio's group by writing /T on the parent xref.

    Kids in a `_link_radio_group`-built group share a parent that owns /T;
    each kid's /T is null (inherited per PDF 1.7 §12.7.3). Writing the
    kid's /T directly splits the group. Returns True if the parent was
    rewritten, False if the widget has no /Parent (caller should fall
    back to plain `widget.field_name = new_name`).
    """
    parent_xref = _radio_parent_xref(doc, widget_xref)
    if parent_xref is None:
        return False
    doc.xref_set_key(parent_xref, "T", f"({new_name})")
    return True


def _cleanup_radio_parent_after_delete(
    doc: "fitz.Document", deleted_xref: int, parent_xref: int
) -> None:
    """Drop `deleted_xref` from a radio parent's /Kids array. If the parent
    is now empty, remove it from /AcroForm/Fields and zero out the object.

    PyMuPDF's `page.delete_widget()` removes the kid annot but never touches
    the parent's /Kids array, so without this we leave a dangling
    `<X 0 R>` ref pointing at a freed xref slot — pikepdf flags those.
    """
    try:
        _, kids_raw = doc.xref_get_key(parent_xref, "Kids")
    except Exception:
        return
    kid_xrefs = [int(m) for m in re.findall(r"(\d+)\s+0\s+R", kids_raw or "")]
    remaining = [x for x in kid_xrefs if x != deleted_xref]
    if remaining:
        refs = " ".join(f"{x} 0 R" for x in remaining)
        doc.xref_set_key(parent_xref, "Kids", f"[ {refs} ]")
        return
    # Parent is now empty — strip from /AcroForm/Fields and free the object.
    catalog = doc.pdf_catalog()
    try:
        _, af = doc.xref_get_key(catalog, "AcroForm")
    except Exception:
        af = ""
    fields_match = re.search(r"/Fields\s*\[(.*?)\]", af or "", re.DOTALL)
    if fields_match:
        existing = [int(m) for m in re.findall(r"(\d+)\s+0\s+R", fields_match.group(1))]
        kept = [x for x in existing if x != parent_xref]
        refs = " ".join(f"{x} 0 R" for x in kept)
        doc.xref_set_key(catalog, "AcroForm", f"<< /Fields [ {refs} ] >>")
    try:
        doc.update_object(parent_xref, "<<>>")
    except Exception:
        pass


def _all_radio_group_names(doc: "fitz.Document") -> list[str]:
    """Return unique radio field names in document order."""
    if doc is None:
        return []
    names: list[str] = []
    s: set[str] = set()
    for i in range(len(doc)):
        try:
            page = doc[i]
        except Exception:
            continue
        for w in page.widgets():
            if w.field_type != fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                continue
            if w.field_name and w.field_name not in s:
                s.add(w.field_name)
                names.append(w.field_name)
    return names


def _radio_export_values(doc: "fitz.Document", group_name: str) -> list[str]:
    """Existing on-state names for radios in `group_name` — used to enforce
    uniqueness when adding a new sibling."""
    if doc is None or not group_name:
        return []
    out: list[str] = []
    for i in range(len(doc)):
        try:
            page = doc[i]
        except Exception:
            continue
        for w in page.widgets():
            if (
                w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
                and w.field_name == group_name
            ):
                cap = w.button_caption or ""
                # button_caption may be None on reopen; sniff /AP/N keys.
                if not cap:
                    try:
                        ap = doc.xref_get_key(w.xref, "AP")[1]
                    except Exception:
                        ap = ""
                    # /N<</Off ... /Yes ...>> — extract names except Off
                    for m in re.findall(r"/([A-Za-z0-9_]+)\s", ap):
                        if m not in ("N", "D", "R", "Off"):
                            cap = m
                            break
                if cap and cap != "Off":
                    out.append(cap)
    return out


def _color_to_rgb_floats(qc: QColor | None) -> tuple[float, float, float] | None:
    if qc is None or not qc.isValid():
        return None
    return (qc.redF(), qc.greenF(), qc.blueF())


def _rgb_floats_to_color(rgb) -> QColor:
    if not rgb:
        return QColor(0, 0, 0)
    try:
        r, g, b = rgb[0], rgb[1], rgb[2]
        return QColor.fromRgbF(float(r), float(g), float(b))
    except Exception:
        return QColor(0, 0, 0)


class _ColorButton(QPushButton):
    """A small button that shows a color swatch and opens QColorDialog when clicked."""

    def __init__(self, initial: QColor | None, parent=None, allow_none: bool = True):
        super().__init__(parent)
        self._color: QColor | None = initial if (initial and initial.isValid()) else None
        self._allow_none = allow_none
        self.setMinimumWidth(120)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> QColor | None:
        return self._color

    def set_color(self, c: QColor | None):
        self._color = c if (c is not None and c.isValid()) else None
        self._refresh()

    def _refresh(self):
        if self._color is None:
            self.setText("(none)")
            self.setStyleSheet("")
        else:
            name = self._color.name()
            self.setText(name)
            # Pick readable contrast for the label.
            fg = "#000" if self._color.lightnessF() > 0.5 else "#fff"
            self.setStyleSheet(
                f"background:{name}; color:{fg}; border:1px solid #888; padding:4px;"
            )

    def _pick(self):
        start = self._color if self._color else QColor(255, 255, 255)
        c = QColorDialog.getColor(start, self, "Pick color")
        if c.isValid():
            self.set_color(c)


class FieldPropertiesDialog(QDialog):
    """Adobe-Acrobat-style Field Properties dialog with General/Appearance/Options/Actions tabs."""

    _BORDER_STYLES = [
        ("Solid", "S"),
        ("Dashed", "D"),
        ("Beveled", "B"),
        ("Inset", "I"),
        ("Underline", "U"),
    ]
    _ALIGN_LABELS = ["Left", "Center", "Right"]
    _FORMAT_LABELS = ["None", "Number", "Date", "Zip", "Phone", "SSN"]

    def __init__(self, widget: "fitz.Widget", parent=None, doc: "fitz.Document | None" = None):
        super().__init__(parent)
        self.widget = widget
        self.doc = doc  # for populating Calculate source-field picker
        self.setWindowTitle("Field Properties")
        self.setMinimumWidth(440)

        self.tabs = QTabWidget()
        self._build_general_tab()
        self._build_appearance_tab()
        self._build_options_tab()
        self._build_actions_tab()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(bb)

    # --- General tab ---
    def _build_general_tab(self):
        page = QWidget()
        form = QFormLayout(page)

        self.name_edit = QLineEdit(self.widget.field_name or "")
        form.addRow("Name:", self.name_edit)

        self.tooltip_edit = QLineEdit(self.widget.field_label or "")
        form.addRow("Tooltip:", self.tooltip_edit)

        flags = int(self.widget.field_flags or 0)
        self.required_cb = QCheckBox("Required")
        self.required_cb.setChecked(bool(flags & 2))  # PDF_FIELD_IS_REQUIRED = 1<<1
        self.readonly_cb = QCheckBox("Read-only")
        self.readonly_cb.setChecked(bool(flags & 1))  # PDF_FIELD_IS_READ_ONLY = 1<<0
        self.noexport_cb = QCheckBox("No-export")
        self.noexport_cb.setChecked(bool(flags & 4))  # PDF_FIELD_IS_NO_EXPORT = 1<<2
        # /F (field_display) maps 0/1/2/3 to four user-visible states. Adobe
        # exposes these by name; we follow the same labels so a round-tripped
        # field doesn't silently lose its NoView/NoPrint setting.
        self.display_combo = QComboBox()
        self.display_combo.addItem("Visible", 0)
        self.display_combo.addItem("Hidden", 1)
        self.display_combo.addItem("Visible but doesn't print", 2)
        self.display_combo.addItem("Hidden but printable", 3)
        cur_display = int(self.widget.field_display or 0)
        idx_for_display = {0: 0, 1: 1, 2: 2, 3: 3}.get(cur_display, 0)
        self.display_combo.setCurrentIndex(idx_for_display)

        form.addRow("Flags:", self.required_cb)
        form.addRow("", self.readonly_cb)
        form.addRow("", self.noexport_cb)
        form.addRow("Form Field:", self.display_combo)

        self.tabs.addTab(page, "General")

    # --- Appearance tab ---
    def _build_appearance_tab(self):
        page = QWidget()
        form = QFormLayout(page)

        self.border_color_btn = _ColorButton(_rgb_floats_to_color(self.widget.border_color))
        self.fill_color_btn = _ColorButton(_rgb_floats_to_color(self.widget.fill_color))
        self.text_color_btn = _ColorButton(_rgb_floats_to_color(self.widget.text_color))
        form.addRow("Border color:", self.border_color_btn)
        form.addRow("Fill color:", self.fill_color_btn)
        form.addRow("Text color:", self.text_color_btn)

        self.border_width_spin = QSpinBox()
        self.border_width_spin.setRange(0, 10)
        self.border_width_spin.setValue(int(self.widget.border_width or 0))
        form.addRow("Border width:", self.border_width_spin)

        self.border_style_combo = QComboBox()
        for label, _code in self._BORDER_STYLES:
            self.border_style_combo.addItem(label)
        cur_style = (self.widget.border_style or "S")
        idx = 0
        for i, (label, code) in enumerate(self._BORDER_STYLES):
            # fitz returns the *expanded* name for some styles ("Dashed", "Beveled"...).
            if cur_style in (code, label, label[0]):
                idx = i
                break
        self.border_style_combo.setCurrentIndex(idx)
        form.addRow("Border style:", self.border_style_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 72)
        self.font_size_spin.setValue(int(self.widget.text_fontsize or 10))
        form.addRow("Font size:", self.font_size_spin)

        self.align_combo = QComboBox()
        self.align_combo.addItems(self._ALIGN_LABELS)
        # /Q (0=Left, 1=Center, 2=Right) is persisted on the widget xref —
        # `_pe_text_align` is a transient runtime attr that's missing on reopen.
        align_idx = int(getattr(self.widget, "_pe_text_align", 0))
        if self.doc is not None:
            try:
                qkind, qval = self.doc.xref_get_key(self.widget.xref, "Q")
                if qkind == "int":
                    persisted = int(str(qval).strip())
                    if persisted in (0, 1, 2):
                        align_idx = persisted
            except Exception:
                pass
        self.align_combo.setCurrentIndex(align_idx)
        form.addRow("Text alignment:", self.align_combo)

        self.tabs.addTab(page, "Appearance")

    # --- Options tab ---
    def _build_options_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        layout.addLayout(form)

        ft = self.widget.field_type
        flags = int(self.widget.field_flags or 0)

        # Containers for type-specific widgets so _apply_to_widget can probe them.
        self.default_value_edit = None
        self.maxlen_spin = None
        self.format_combo = None
        self.check_default_combo = None
        self.export_value_edit = None
        self.group_name_edit = None
        self.choices_list = None
        self.choices_default_combo = None
        self.allow_custom_cb = None
        self.calc_op_combo = None
        self.calc_sources_list = None
        self.multiline_cb = None

        if ft == fitz.PDF_WIDGET_TYPE_TEXT:
            self.default_value_edit = QLineEdit(self.widget.field_value or "")
            form.addRow("Default value:", self.default_value_edit)

            self.maxlen_spin = QSpinBox()
            self.maxlen_spin.setRange(0, 9999)
            self.maxlen_spin.setValue(int(self.widget.text_maxlen or 0))
            self.maxlen_spin.setSpecialValueText("Unlimited")
            form.addRow("Max length:", self.maxlen_spin)

            self.multiline_cb = QCheckBox("Multi-line")
            self.multiline_cb.setChecked(
                bool(int(self.widget.field_flags or 0) & fitz.PDF_TX_FIELD_IS_MULTILINE)
            )
            form.addRow("", self.multiline_cb)

            self.format_combo = QComboBox()
            self.format_combo.addItems(self._FORMAT_LABELS)
            # Prefer the round-tripped script as source-of-truth; fall back
            # to the in-memory _pe_format hint for fields created this session.
            cur = self._infer_format_from_script() or getattr(
                self.widget, "_pe_format", "None"
            )
            if cur in self._FORMAT_LABELS:
                self.format_combo.setCurrentIndex(self._FORMAT_LABELS.index(cur))
            form.addRow("Format:", self.format_combo)

            # Calculate (Adobe folds this into a separate tab; we keep it here.)
            self.calc_op_combo = QComboBox()
            self.calc_op_combo.addItems(_CALC_OPS)
            cur_op, cur_srcs = _parse_calc_script(self.widget.script_calc or "")
            if cur_op in _CALC_OPS:
                self.calc_op_combo.setCurrentIndex(_CALC_OPS.index(cur_op))
            form.addRow("Calculate field as:", self.calc_op_combo)

            layout.addWidget(QLabel("Calculate sources (pick 2+ named text fields):"))
            self.calc_sources_list = QListWidget()
            self.calc_sources_list.setSelectionMode(
                QListWidget.SelectionMode.MultiSelection
            )
            self_name = self.widget.field_name or ""
            for n in _collect_text_field_names(self.doc):
                if n == self_name:
                    continue  # never include self
                item = QListWidgetItem(n)
                self.calc_sources_list.addItem(item)
                if n in cur_srcs:
                    item.setSelected(True)
            layout.addWidget(self.calc_sources_list)

        elif ft == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            self.check_default_combo = QComboBox()
            self.check_default_combo.addItems(["Unchecked", "Checked"])
            cur = self.widget.field_value
            checked = bool(cur) and cur not in (False, "Off", "off", 0, "0")
            self.check_default_combo.setCurrentIndex(1 if checked else 0)
            form.addRow("Default state:", self.check_default_combo)

            self.export_value_edit = QLineEdit(self.widget.button_caption or "Yes")
            form.addRow("Export value:", self.export_value_edit)

        elif ft == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            self.group_name_edit = QLineEdit(self.widget.field_name or "")
            form.addRow("Group name:", self.group_name_edit)
            self.export_value_edit = QLineEdit(self.widget.button_caption or "")
            form.addRow("Export value:", self.export_value_edit)

        elif ft in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
            self.choices_list = QListWidget()
            for v in (self.widget.choice_values or []):
                # choice_values can be either ["a","b"] or [["export","display"],...]
                disp = v if isinstance(v, str) else (v[1] if len(v) > 1 else v[0])
                self.choices_list.addItem(QListWidgetItem(str(disp)))
            layout.addWidget(QLabel("Choices:"))
            layout.addWidget(self.choices_list)

            row = QHBoxLayout()
            add_btn = QPushButton("Add")
            rm_btn = QPushButton("Remove")
            up_btn = QPushButton("Up")
            dn_btn = QPushButton("Down")
            add_btn.clicked.connect(self._add_choice)
            rm_btn.clicked.connect(self._remove_choice)
            up_btn.clicked.connect(lambda: self._move_choice(-1))
            dn_btn.clicked.connect(lambda: self._move_choice(1))
            for b in (add_btn, rm_btn, up_btn, dn_btn):
                row.addWidget(b)
            row.addStretch()
            layout.addLayout(row)

            self.choices_default_combo = QComboBox()
            self._refresh_choices_default()
            # _refresh_choices_default only restores `cur_text` from the combo;
            # on first build the combo is empty, so the persisted field_value
            # never seeds. Set it explicitly here.
            persisted_default = self.widget.field_value or ""
            if isinstance(persisted_default, str) and persisted_default:
                idx = self.choices_default_combo.findText(persisted_default)
                if idx >= 0:
                    self.choices_default_combo.setCurrentIndex(idx)
            self.choices_list.model().rowsInserted.connect(
                lambda *a: self._refresh_choices_default()
            )
            self.choices_list.model().rowsRemoved.connect(
                lambda *a: self._refresh_choices_default()
            )
            form2 = QFormLayout()
            form2.addRow("Default:", self.choices_default_combo)
            layout.addLayout(form2)

            if ft == fitz.PDF_WIDGET_TYPE_COMBOBOX:
                self.allow_custom_cb = QCheckBox("Allow custom text entry")
                self.allow_custom_cb.setChecked(bool(flags & fitz.PDF_CH_FIELD_IS_EDIT))
                layout.addWidget(self.allow_custom_cb)

        else:
            # Signature / Button — no per-type options in Phase 2.
            layout.addWidget(QLabel("This field type has no editable options."))
            layout.addStretch()

        self.tabs.addTab(page, "Options")

    def _infer_format_from_script(self) -> str | None:
        """Reverse-map widget.script_format back to a Format dropdown label."""
        s = self.widget.script_format or ""
        if not s:
            return None
        if "AFNumber_Format" in s:
            return "Number"
        if "AFDate_Format" in s:
            return "Date"
        if "AFSpecial_Format(0)" in s:
            return "Zip"
        if "AFSpecial_Format(2)" in s:
            return "Phone"
        if "AFSpecial_Format(1)" in s:
            return "SSN"
        return None

    # --- Actions tab ---
    def _build_actions_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        layout.addLayout(form)

        # Track per-editor user-edit dirty flags so Options-tab Format
        # writes don't clobber JS the user typed manually in Actions.
        # Programmatic setPlainText below fires textChanged, so we only
        # mark the editor dirty when the signal fires AFTER setup.
        self._action_dirty: dict[str, bool] = {}

        def _editor(initial: str, key: str) -> QPlainTextEdit:
            ed = QPlainTextEdit()
            ed.setPlainText(initial or "")
            ed.setMinimumHeight(60)
            self._action_dirty[key] = False
            ed.textChanged.connect(lambda k=key: self._action_dirty.__setitem__(k, True))
            return ed

        self.action_focus_edit = _editor(self.widget.script_focus or "", "focus")
        self.action_blur_edit = _editor(self.widget.script_blur or "", "blur")
        self.action_mouseup_edit = _editor(self.widget.script or "", "mouseup")
        self.action_calc_edit = _editor(self.widget.script_calc or "", "calc")
        self.action_format_edit = _editor(self.widget.script_format or "", "format")
        self.action_keystroke_edit = _editor(self.widget.script_change or "", "keystroke")

        form.addRow("On Focus:", self.action_focus_edit)
        form.addRow("On Blur:", self.action_blur_edit)
        form.addRow("On Mouse Up:", self.action_mouseup_edit)
        form.addRow("On Calculate:", self.action_calc_edit)
        form.addRow("On Format:", self.action_format_edit)
        form.addRow("On Keystroke:", self.action_keystroke_edit)

        layout.addWidget(QLabel(
            "Tip: scripts set via the Options tab (Calculate / Format) overwrite "
            "the matching field above when you press OK."
        ))
        layout.addStretch()
        self.tabs.addTab(page, "Actions")

    # --- choices helpers ---
    def _add_choice(self):
        text, ok = QInputDialog.getText(self, "Add choice", "Choice value:")
        if ok and text.strip():
            self.choices_list.addItem(QListWidgetItem(text.strip()))

    def _remove_choice(self):
        row = self.choices_list.currentRow()
        if row >= 0:
            self.choices_list.takeItem(row)

    def _move_choice(self, delta: int):
        row = self.choices_list.currentRow()
        new = row + delta
        if row < 0 or new < 0 or new >= self.choices_list.count():
            return
        item = self.choices_list.takeItem(row)
        self.choices_list.insertItem(new, item)
        self.choices_list.setCurrentRow(new)

    def _refresh_choices_default(self):
        if self.choices_default_combo is None or self.choices_list is None:
            return
        cur_text = self.choices_default_combo.currentText()
        self.choices_default_combo.clear()
        for i in range(self.choices_list.count()):
            self.choices_default_combo.addItem(self.choices_list.item(i).text())
        if cur_text:
            idx = self.choices_default_combo.findText(cur_text)
            if idx >= 0:
                self.choices_default_combo.setCurrentIndex(idx)

    def choice_values(self) -> list[str]:
        if self.choices_list is None:
            return []
        return [self.choices_list.item(i).text() for i in range(self.choices_list.count())]

    def add_choice_value(self, value: str):
        """Test hook: append a choice without going through QInputDialog."""
        if self.choices_list is not None:
            self.choices_list.addItem(QListWidgetItem(str(value)))

    # --- apply ---
    def _apply_to_widget(self) -> int:
        """Push form values back onto self.widget. Returns the new /Q (0/1/2) value
        so the caller can persist it via xref_set_key — fitz.Widget has no
        text_align attribute, so /Q lives outside the in-place mutation."""
        w = self.widget

        new_name = self.name_edit.text().strip()
        if new_name:
            w.field_name = new_name
        w.field_label = self.tooltip_edit.text().strip() or None

        flags = int(w.field_flags or 0)
        flags = (flags | 2) if self.required_cb.isChecked() else (flags & ~2)
        flags = (flags | 1) if self.readonly_cb.isChecked() else (flags & ~1)
        flags = (flags | 4) if self.noexport_cb.isChecked() else (flags & ~4)

        ft = w.field_type
        if ft == fitz.PDF_WIDGET_TYPE_COMBOBOX and self.allow_custom_cb is not None:
            edit_bit = fitz.PDF_CH_FIELD_IS_EDIT
            flags = (flags | edit_bit) if self.allow_custom_cb.isChecked() else (flags & ~edit_bit)
        if ft == fitz.PDF_WIDGET_TYPE_TEXT and self.multiline_cb is not None:
            ml = fitz.PDF_TX_FIELD_IS_MULTILINE
            flags = (flags | ml) if self.multiline_cb.isChecked() else (flags & ~ml)
        w.field_flags = flags

        w.field_display = int(self.display_combo.currentData() or 0)

        # Actions tab — written first so Options-tab Format/Calc can overwrite
        # the matching slots when the user picks a non-"None" option there.
        if hasattr(self, "action_focus_edit"):
            w.script_focus = self.action_focus_edit.toPlainText() or ""
            w.script_blur = self.action_blur_edit.toPlainText() or ""
            w.script = self.action_mouseup_edit.toPlainText() or ""
            w.script_calc = self.action_calc_edit.toPlainText() or ""
            w.script_format = self.action_format_edit.toPlainText() or ""
            w.script_change = self.action_keystroke_edit.toPlainText() or ""

        # Appearance
        bc = self.border_color_btn.color()
        w.border_color = _color_to_rgb_floats(bc)
        fc = self.fill_color_btn.color()
        w.fill_color = _color_to_rgb_floats(fc)
        tc = self.text_color_btn.color()
        if tc is not None:
            w.text_color = _color_to_rgb_floats(tc)
        w.border_width = self.border_width_spin.value()
        w.border_style = self._BORDER_STYLES[self.border_style_combo.currentIndex()][1]
        w.text_fontsize = self.font_size_spin.value()
        align_idx = self.align_combo.currentIndex()
        # Stash for round-trip in dialog state; persisted via /Q by the caller.
        w._pe_text_align = align_idx

        # Options (per type)
        if ft == fitz.PDF_WIDGET_TYPE_TEXT:
            if self.default_value_edit is not None:
                w.field_value = self.default_value_edit.text()
            if self.maxlen_spin is not None:
                w.text_maxlen = self.maxlen_spin.value()
            if self.format_combo is not None:
                fmt = self.format_combo.currentText()
                w._pe_format = fmt
                # If the user typed JS into the Actions-tab On Format /
                # On Keystroke editors, preserve their text — don't let
                # the Options-tab Format dropdown silently overwrite it.
                fmt_dirty = self._action_dirty.get("format", False)
                ks_dirty = self._action_dirty.get("keystroke", False)
                if fmt in _FORMAT_SCRIPTS:
                    fs, ks = _FORMAT_SCRIPTS[fmt]
                    if not fmt_dirty:
                        w.script_format = fs
                    if not ks_dirty:
                        w.script_change = ks
                else:
                    # User picked "None" → strip any prior format script
                    # (still respect explicit user edits in Actions).
                    if not fmt_dirty:
                        w.script_format = ""
                    if not ks_dirty:
                        w.script_change = ""
            if self.calc_op_combo is not None and self.calc_sources_list is not None:
                op = self.calc_op_combo.currentText()
                srcs = [
                    self.calc_sources_list.item(i).text()
                    for i in range(self.calc_sources_list.count())
                    if self.calc_sources_list.item(i).isSelected()
                ]
                w.script_calc = _build_calc_script(op, srcs)
        elif ft == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            if self.export_value_edit is not None:
                w.button_caption = self.export_value_edit.text().strip() or "Yes"
            if self.check_default_combo is not None:
                w.field_value = self.check_default_combo.currentIndex() == 1
        elif ft == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            if self.group_name_edit is not None:
                grp = self.group_name_edit.text().strip()
                if grp:
                    w.field_name = grp
            if self.export_value_edit is not None:
                w.button_caption = self.export_value_edit.text().strip() or "On"
        elif ft in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
            choices = self.choice_values()
            w.choice_values = choices
            if self.choices_default_combo is not None:
                d = self.choices_default_combo.currentText()
                if d:
                    w.field_value = d
                elif choices:
                    w.field_value = choices[0]

        return align_idx


class _FormFieldTree(QTreeWidget):
    """QTreeWidget subclass that signals its parent panel after an internal-move drop."""

    def __init__(self, panel: "FormBuilderPanel"):
        super().__init__()
        self._panel = panel

    def dropEvent(self, ev):
        super().dropEvent(ev)
        QTimer.singleShot(0, self._panel.commit_drop)


_FIELD_TYPE_LABELS: dict[int, tuple[str, str]] = {
    fitz.PDF_WIDGET_TYPE_TEXT: ("T", "Text"),
    fitz.PDF_WIDGET_TYPE_CHECKBOX: ("\u2611", "Checkbox"),
    fitz.PDF_WIDGET_TYPE_RADIOBUTTON: ("\u25cb", "Radio"),
    fitz.PDF_WIDGET_TYPE_COMBOBOX: ("\u25bc", "Dropdown"),
    fitz.PDF_WIDGET_TYPE_LISTBOX: ("\u2630", "List"),
    fitz.PDF_WIDGET_TYPE_SIGNATURE: ("\u270d", "Signature"),
    fitz.PDF_WIDGET_TYPE_BUTTON: ("\u25a3", "Button"),
}


def _field_type_display(widget: "fitz.Widget") -> tuple[str, str]:
    """Return (icon-prefix, type-label) for a widget. Multi-line text gets its
    own label so the panel surfaces the difference at a glance."""
    ft = widget.field_type
    if ft == fitz.PDF_WIDGET_TYPE_TEXT and int(widget.field_flags or 0) & fitz.PDF_TX_FIELD_IS_MULTILINE:
        return ("\u00b6", "Multi-line")
    return _FIELD_TYPE_LABELS.get(ft, ("?", "Unknown"))


class FormBuilderPanel(QDockWidget):
    """Adobe-Acrobat-style "Prepare Form" side panel.

    Reads the document via MainWindow.collect_all_widgets() and groups by
    page in a QTreeWidget. Supports inline rename, drag-reorder (drives the
    Phase-3 tab-order writeback), Delete/Enter shortcuts, and a context
    menu mirroring those actions.
    """

    PAGE_ROLE = Qt.ItemDataRole.UserRole + 1
    XREF_ROLE = Qt.ItemDataRole.UserRole + 2
    KIND_ROLE = Qt.ItemDataRole.UserRole + 3  # "page" or "field"

    def __init__(self, window: "MainWindow"):
        super().__init__("Form Fields", window)
        self.window_ = window
        self.setObjectName("FormBuilderPanel")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Form Fields")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        header.addWidget(title)
        header.addStretch()
        self.refresh_btn = QToolButton(body)
        self.refresh_btn.setText("Refresh")
        self.refresh_btn.setAutoRaise(True)
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.stack = QStackedWidget(body)
        layout.addWidget(self.stack, 1)

        self.tree = _FormFieldTree(self)
        self.tree.setParent(body)
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Field Name", "Type"])
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        try:
            self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        except Exception:
            pass
        self.stack.addWidget(self.tree)

        self.empty_label = QLabel(
            "No form fields yet — pick a tool from the Forms menu and drag on the page to create one."
        )
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty_label.setContentsMargins(8, 8, 8, 8)
        self.stack.addWidget(self.empty_label)

        self.status_label = QLabel("0 fields")
        sf = self.status_label.font()
        sf.setPointSize(max(8, sf.pointSize() - 1))
        self.status_label.setFont(sf)
        layout.addWidget(self.status_label)

        self.setWidget(body)

        self._suspend_changes = False
        self.tree.installEventFilter(self)
        self.refresh()

    # --- public API used by tests ---
    def refresh(self) -> None:
        self._suspend_changes = True
        try:
            self.tree.clear()
            pairs = self.window_.collect_all_widgets() if self.window_.view.doc else []
            by_page: dict[int, list["fitz.Widget"]] = {}
            for pi, w in pairs:
                by_page.setdefault(pi, []).append(w)

            total = len(pairs)
            if total == 0:
                self.stack.setCurrentWidget(self.empty_label)
                self.status_label.setText("0 fields")
                return
            self.stack.setCurrentWidget(self.tree)

            for pi in sorted(by_page.keys()):
                page_item = QTreeWidgetItem([f"Page {pi + 1}", ""])
                page_item.setData(0, self.KIND_ROLE, "page")
                page_item.setData(0, self.PAGE_ROLE, pi)
                page_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled
                )
                page_font = page_item.font(0)
                page_font.setBold(True)
                page_item.setFont(0, page_font)
                self.tree.addTopLevelItem(page_item)
                page_item.setFirstColumnSpanned(True)
                page_item.setExpanded(True)
                for w in by_page[pi]:
                    self._append_field_item(page_item, pi, w)

            self.status_label.setText(f"{total} field{'s' if total != 1 else ''}")
        finally:
            self._suspend_changes = False

    def _append_field_item(self, parent: QTreeWidgetItem, pi: int, w: "fitz.Widget") -> None:
        icon, type_label = _field_type_display(w)
        name = w.field_name or "(unnamed)"
        item = QTreeWidgetItem([f"{icon}  {name}", type_label])
        item.setData(0, self.KIND_ROLE, "field")
        item.setData(0, self.PAGE_ROLE, pi)
        item.setData(0, self.XREF_ROLE, w.xref)
        item.setData(0, Qt.ItemDataRole.UserRole, name)
        flags = (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsDragEnabled
        )
        item.setFlags(flags)
        if int(w.field_flags or 0) & 2:
            f = item.font(0)
            f.setBold(True)
            item.setFont(0, f)
            item.setFont(1, f)
        parent.addChild(item)

    def selected_widget(self) -> "tuple[int, fitz.Widget] | None":
        items = self.tree.selectedItems()
        if not items:
            return None
        item = items[0]
        if item.data(0, self.KIND_ROLE) != "field":
            return None
        return self._resolve_widget(item)

    def _resolve_widget(self, item: QTreeWidgetItem) -> "tuple[int, fitz.Widget] | None":
        """Return (page_idx, widget) for a tree item, or None if not found.

        Callers that mutate the widget across function boundaries should
        re-resolve via _bound_widget — the widget yielded here is only safe
        to read inside the immediate caller frame.
        """
        pi = item.data(0, self.PAGE_ROLE)
        xr = item.data(0, self.XREF_ROLE)
        if pi is None or xr is None or not self.window_.view.doc:
            return None
        if pi < 0 or pi >= len(self.window_.view.doc):
            return None
        try:
            page = self.window_.view.doc[pi]
        except Exception:
            return None
        for w in page.widgets():
            if w.xref == xr:
                return (pi, w)
        return None

    def select_widget_by_xref(self, page_idx: int, xref: int) -> None:
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if (
                    child.data(0, self.PAGE_ROLE) == page_idx
                    and child.data(0, self.XREF_ROLE) == xref
                ):
                    self.tree.setCurrentItem(child)
                    return

    def delete_selected(self) -> bool:
        sel = self.selected_widget()
        if sel is None:
            return False
        pi, w = sel
        # delete_widget re-resolves under _bound_widget, so the page binding
        # is held inside that call — no _page_pin workaround needed.
        self.window_.delete_widget(pi, w)
        return True

    def open_properties_for_selected(self) -> bool:
        sel = self.selected_widget()
        if sel is None:
            return False
        pi, w = sel
        self.window_.edit_widget_properties(pi, w)
        return True

    def rename_item(self, item: QTreeWidgetItem, new_name: str) -> bool:
        """Programmatic rename hook for tests. Mirrors the inline-edit path."""
        if item is None or item.data(0, self.KIND_ROLE) != "field":
            return False
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        pi = item.data(0, self.PAGE_ROLE)
        xr = item.data(0, self.XREF_ROLE)
        doc = self.window_.view.doc
        if pi is None or xr is None or not doc:
            return False
        self.window_._snapshot()
        relink_group: str | None = None
        try:
            with _bound_widget(doc, pi, xr) as (_page, w):
                if w.field_name == new_name:
                    if self.window_._undo:
                        self.window_._undo.pop()
                    return True
                # Grouped radios: /T lives on the parent, kids inherit.
                # Writing w.field_name directly splits the group.
                is_radio = w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
                if is_radio and _rename_radio_group(doc, xr, new_name):
                    relink_group = new_name
                else:
                    w.field_name = new_name
                    w.update()
        except Exception:
            if self.window_._undo:
                self.window_._undo.pop()
            return False
        if relink_group:
            try:
                _link_radio_group(doc, relink_group)
            except Exception as exc:
                print(f"[radio] relink after rename failed: {exc}", file=sys.stderr)
        self.window_.view.render_all(preserve_scroll=True)
        self.window_._mark_dirty()
        self.window_._refresh_form_panel()
        return True

    def apply_reorder(self, ordered_xrefs: list[tuple[int, int]]) -> None:
        """Persist a new tab order, mirroring TabOrderDialog.apply_to_doc().

        Cross-page moves: a widget's xref dict is shared, but each page's
        /Annots holds a reference. To move xref X from page A to page B we
        must rewrite BOTH pages — A drops X, B gains X. We compute the
        before-snapshot of (page → set-of-widget-xrefs), then rewrite every
        page that either previously held a moved xref or now holds one.
        Without this, dragging the only widget on page A to page B leaves
        a stale /Annots entry on A AND adds it to B → duplicated widget.
        """
        doc = self.window_.view.doc
        if doc is None:
            return
        before_by_page: dict[int, set[int]] = {}
        for pi, w in self.window_.collect_all_widgets():
            before_by_page.setdefault(pi, set()).add(w.xref)
        per_page: dict[int, list[int]] = {}
        for pi, xr in ordered_xrefs:
            per_page.setdefault(pi, []).append(xr)
        affected_pages: set[int] = set(before_by_page.keys()) | set(per_page.keys())
        self.window_._snapshot()
        try:
            for pi in affected_pages:
                if pi < 0 or pi >= len(doc):
                    continue
                page = doc[pi]
                widget_xrefs = per_page.get(pi, [])
                page_widget_xrefs_now = {w.xref for w in page.widgets()}
                stale_widget_xrefs = before_by_page.get(pi, set()) | page_widget_xrefs_now
                try:
                    _, raw = doc.xref_get_key(page.xref, "Annots")
                except Exception:
                    raw = ""
                existing_order: list[int] = []
                for m in re.findall(r"(\d+)\s+0\s+R", raw or ""):
                    existing_order.append(int(m))
                non_widget_tail = [
                    x for x in existing_order if x not in stale_widget_xrefs
                ]
                new_order = list(widget_xrefs) + non_widget_tail
                arr = "[ " + " ".join(f"{x} 0 R" for x in new_order) + " ]"
                doc.xref_set_key(page.xref, "Annots", arr)
                doc.xref_set_key(page.xref, "Tabs", "/R")
        except Exception as exc:
            QMessageBox.warning(self, "Reorder", f"Could not reorder: {exc}")
            if self.window_._undo:
                self.window_._undo.pop()
            return
        self.window_.view.render_all(preserve_scroll=True)
        self.window_._mark_dirty()
        self.window_._refresh_form_panel()

    def current_order(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            pi = top.data(0, self.PAGE_ROLE)
            for j in range(top.childCount()):
                child = top.child(j)
                xr = child.data(0, self.XREF_ROLE)
                if xr is not None:
                    out.append((int(pi if pi is not None else child.data(0, self.PAGE_ROLE)), int(xr)))
        return out

    # --- event handlers ---
    def _on_selection_changed(self) -> None:
        sel = self.selected_widget()
        if sel is None:
            return
        pi, w = sel
        self.window_.focus_widget_in_view(pi, w)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suspend_changes or column != 0:
            return
        if item.data(0, self.KIND_ROLE) != "field":
            return
        new_text = item.text(0).strip()
        # Strip ONLY this widget's own icon-prefix (e.g. "T  Foo" \u2192 "Foo").
        # Stripping any icon char from the global set ate the leading "T"
        # of legitimate names like "Total" or "Title".
        cleaned = new_text
        sel = self._resolve_widget(item)
        if sel is not None:
            _, w_for_icon = sel
            own_icon, _ = _field_type_display(w_for_icon)
            prefix = f"{own_icon} "
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].lstrip()
        prev = item.data(0, Qt.ItemDataRole.UserRole) or ""
        if not cleaned or cleaned == prev:
            self._suspend_changes = True
            try:
                icon, _ = "", ""
                sel = self._resolve_widget(item)
                if sel is not None:
                    _, w = sel
                    icon, _ = _field_type_display(w)
                item.setText(0, f"{icon}  {prev}")
            finally:
                self._suspend_changes = False
            return
        ok = self.rename_item(item, cleaned)
        if not ok:
            self._suspend_changes = True
            try:
                sel = self._resolve_widget(item)
                if sel is not None:
                    _, w = sel
                    icon, _ = _field_type_display(w)
                else:
                    icon = ""
                item.setText(0, f"{icon}  {prev}")
            finally:
                self._suspend_changes = False

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        # Double-click on the type column or a page row: open properties.
        if item.data(0, self.KIND_ROLE) != "field":
            return
        if column == 1:
            self.open_properties_for_selected()

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        if item.data(0, self.KIND_ROLE) != "field":
            return
        menu = QMenu(self.tree)
        act_props = menu.addAction("Properties…")
        act_rename = menu.addAction("Rename")
        menu.addSeparator()
        act_del = menu.addAction("Delete")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_props:
            self.open_properties_for_selected()
        elif chosen is act_rename:
            self.tree.editItem(item, 0)
        elif chosen is act_del:
            self.delete_selected()

    def eventFilter(self, obj, ev):
        if obj is self.tree and ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self.delete_selected():
                    return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.open_properties_for_selected():
                    return True
        return super().eventFilter(obj, ev)

    def dropEvent(self, ev):  # pragma: no cover - dock itself isn't drop target
        super().dropEvent(ev)

    # Hook drop on the inner tree to commit a reorder.
    # We override via a wrapper installed on the tree.
    def commit_drop(self) -> None:
        ordered = self.current_order()
        self.apply_reorder(ordered)


class TabOrderDialog(QDialog):
    """Reorder form-field tab order document-wide.

    Tab order is rewritten by replacing each page's /Annots array with the
    same widget xrefs in a new order, plus setting /Tabs=/R (row order). PDF
    1.7 §12.5.3 says viewers may use either /Tabs or /Annots ordering; setting
    both maximizes the chance Reader/Acrobat respects the new sequence. We
    verified empirically on PyMuPDF 1.x that /Annots reordering survives
    save+reopen in `page.widgets()`.
    """

    def __init__(self, doc: "fitz.Document", parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Tab Order")
        self.setMinimumSize(420, 360)

        self.list_widget = QListWidget()
        # Each item.data(Qt.UserRole) = (page_idx, xref) so reorder maps cleanly back.
        self._populate()

        up_btn = QPushButton("Move Up")
        dn_btn = QPushButton("Move Down")
        up_btn.clicked.connect(lambda: self._move(-1))
        dn_btn.clicked.connect(lambda: self._move(1))

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drag or use the buttons to reorder field tab focus:"))
        layout.addWidget(self.list_widget)
        row = QHBoxLayout()
        row.addWidget(up_btn)
        row.addWidget(dn_btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(bb)

        # Enable internal drag-reorder
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)

    def _populate(self):
        self.list_widget.clear()
        if self.doc is None:
            return
        for pi in range(len(self.doc)):
            try:
                page = self.doc[pi]
            except Exception:
                continue
            for w in page.widgets():
                label = f"Page {pi + 1}: {w.field_name or '(unnamed)'}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, (pi, w.xref))
                self.list_widget.addItem(item)

    def _move(self, delta: int):
        row = self.list_widget.currentRow()
        new = row + delta
        if row < 0 or new < 0 or new >= self.list_widget.count():
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(new, item)
        self.list_widget.setCurrentRow(new)

    def ordered_entries(self) -> list[tuple[int, int]]:
        """Test hook: current (page_idx, xref) order in the list."""
        out: list[tuple[int, int]] = []
        for i in range(self.list_widget.count()):
            data = self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            if data is not None:
                out.append(data)
        return out

    def reorder_to(self, entries: list[tuple[int, int]]) -> None:
        """Test hook: rewrite the list to a specific order, by xref."""
        # Snapshot label text BEFORE clear() — clear() deletes the QListWidgetItem
        # C++ objects out from under any Python refs we still hold.
        labels: dict[int, str] = {}
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            data = it.data(Qt.ItemDataRole.UserRole)
            if data is not None:
                labels[data[1]] = it.text()
        self.list_widget.clear()
        for pi, xr in entries:
            label = labels.get(xr, f"Page {pi + 1}: xref {xr}")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (pi, xr))
            self.list_widget.addItem(item)

    def apply_to_doc(self) -> None:
        """Rewrite each page's /Annots in the new order. Non-widget annotations
        on the page (e.g. highlights) are preserved at the END of /Annots —
        only widget xrefs are reordered."""
        if self.doc is None:
            return
        # Collect the desired widget order per page from the list.
        per_page: dict[int, list[int]] = {}
        for i in range(self.list_widget.count()):
            data = self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            if data is None:
                continue
            pi, xr = data
            per_page.setdefault(pi, []).append(xr)
        for pi, widget_xrefs in per_page.items():
            try:
                page = self.doc[pi]
            except Exception:
                continue
            current_widget_xrefs = {w.xref for w in page.widgets()}
            # Read raw /Annots so we can preserve non-widget annot order.
            try:
                _, raw = self.doc.xref_get_key(page.xref, "Annots")
            except Exception:
                raw = ""
            existing_order: list[int] = []
            for m in re.findall(r"(\d+)\s+0\s+R", raw or ""):
                existing_order.append(int(m))
            non_widget_tail = [
                x for x in existing_order if x not in current_widget_xrefs
            ]
            new_order = list(widget_xrefs) + non_widget_tail
            arr = "[ " + " ".join(f"{x} 0 R" for x in new_order) + " ]"
            self.doc.xref_set_key(page.xref, "Annots", arr)
            self.doc.xref_set_key(page.xref, "Tabs", "/R")


MAX_UNDO = 30


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Basic PDF Editor")
        self.resize(1100, 850)
        self.setAcceptDrops(True)

        self.path: str | None = None
        self.dirty = False
        self._undo: list[bytes] = []
        self._redo: list[bytes] = []
        # Search state
        self._search_results: list[tuple[int, fitz.Rect]] = []
        self._search_idx = -1
        self._search_query = ""

        self.view = PDFView(self)
        self.setCentralWidget(self.view)
        self.setStatusBar(QStatusBar())
        self._build_toolbar()
        self._build_form_panel()
        self.statusBar().showMessage("Open a PDF to begin (⌘O) — or drop one onto the window")

    # --- Title / dirty tracking ---
    def _refresh_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        mark = " •" if self.dirty else ""
        self.setWindowTitle(f"Basic PDF Editor — {name}{mark}")

    def _mark_dirty(self):
        self.dirty = True
        self._refresh_title()

    def _mark_clean(self):
        self.dirty = False
        self._refresh_title()

    # --- Undo/redo ---
    # Each entry is (doc_bytes, [serialized overlay states]).
    def _capture_state(self) -> tuple:
        return (
            self.view.doc.tobytes(),
            [ov.serialize() for ov in self.view.overlays],
        )

    def _snapshot(self):
        if not self.view.doc:
            return
        try:
            self._undo.append(self._capture_state())
            if len(self._undo) > MAX_UNDO:
                self._undo.pop(0)
            self._redo.clear()
        except Exception as exc:
            print(f"[snapshot] {exc}", file=sys.stderr)

    def _restore_state(self, state: tuple):
        data, overlay_states = state
        if self.view.doc:
            self.view.doc.close()
        self.view.doc = fitz.open(stream=data, filetype="pdf")
        # Drop current overlays from the scene (rendered_all will rebuild pages anyway,
        # but we want the new overlays on top after).
        for ov in list(self.view.overlays):
            if ov.scene() is self.view.scene_:
                self.view.scene_.removeItem(ov)
        self.view.overlays = []
        for d in overlay_states:
            kind = d.get("kind", "text")
            if kind == "text":
                ov = TextBoxItem.deserialize(self.view, d)
            elif kind == "signature":
                ov = SignatureItem.deserialize(self.view, d)
            elif kind == "image":
                ov = ImageOverlayItem.deserialize(self.view, d)
            else:
                continue
            self.view.overlays.append(ov)
        self.view.render_all(preserve_scroll=True)
        self._refresh_page_label()
        self.refresh_format_toolbar()
        self._refresh_form_panel()

    def undo(self):
        if not self._undo or not self.view.doc:
            return
        self._redo.append(self._capture_state())
        self._restore_state(self._undo.pop())
        self._mark_dirty()
        self.statusBar().showMessage("Undo")

    def redo(self):
        if not self._redo or not self.view.doc:
            return
        self._undo.append(self._capture_state())
        self._restore_state(self._redo.pop())
        self._mark_dirty()
        self.statusBar().showMessage("Redo")

    # --- Menu bar + slim toolbar ---
    def _build_toolbar(self):
        # Build all actions once; reuse them in menu bar AND toolbar where needed.
        def make(label, slot, shortcut=None, checkable=False):
            a = QAction(label, self)
            if shortcut:
                a.setShortcut(shortcut)
                a.setShortcutVisibleInContextMenu(True)
            if checkable:
                a.setCheckable(True)
            else:
                a.triggered.connect(slot)
            return a

        # File
        self.act_new = make("New…", self.new_pdf, "Ctrl+N")
        self.act_open = make("Open…", self.open_pdf, "Ctrl+O")
        self.act_save = make("Save", self.save_pdf, "Ctrl+S")
        self.act_save_as = make("Save As…", self.save_pdf_as, "Ctrl+Shift+S")
        self.act_merge = make("Merge PDF…", self.merge_pdfs)
        self.act_extract = make("Extract Pages…", self.extract_pages_dialog)
        self.act_preferences = make("Preferences…", self.open_settings_dialog, "Ctrl+,")
        self.act_preferences.setMenuRole(QAction.MenuRole.PreferencesRole)

        # Tools (one-shot)
        self.act_watermark = make("Watermark…", self.do_watermark)

        # Forms (one-shot)
        self.act_tab_order = make("Tab Order…", self.open_tab_order_dialog)
        self.act_reset_form = make("Reset Form", self.reset_form)
        self.act_flatten_form = make("Flatten Form", self.flatten_form)

        # Open Recent submenu — populated dynamically from QSettings on aboutToShow.
        self.recent_menu = QMenu("Open Recent", self)
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)

        # Edit
        self.act_undo = make("Undo", self.undo, "Ctrl+Z")
        self.act_redo = make("Redo", self.redo, "Ctrl+Shift+Z")
        self.act_find = QAction("Find…", self)
        self.act_find.setShortcut("Ctrl+F")
        self.act_find.setShortcutVisibleInContextMenu(True)
        self.act_find_next = make("Find Next", self.find_next, "Ctrl+G")

        # View
        self.act_prev = make("Previous Page", lambda: self.change_page(-1), "Ctrl+Left")
        self.act_next = make("Next Page", lambda: self.change_page(1), "Ctrl+Right")
        self.act_zoom_in = make("Zoom In", lambda: self.zoom_by(1.15), "Ctrl+=")
        self.act_zoom_out = make("Zoom Out", lambda: self.zoom_by(1 / 1.15), "Ctrl+-")
        self.act_zoom_reset = make("Actual Size", lambda: self.set_zoom(1.0), "Ctrl+0")

        # Insert (one-shot commands)
        self.act_page_numbers = make("Page Numbers", self.add_page_numbers)

        # Pages
        self.act_rotate = make("Rotate Page", self.rotate_current_page)
        self.act_insert_blank = make("Insert Blank Page", self.insert_blank_page)
        self.act_delete_page = make("Delete Page", self.delete_current_page)

        # Tools (mutually exclusive, checkable — used in both menu and toolbar)
        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_actions: list[QAction] = []
        tool_tooltips = {
            "select": "Click to select, drag to move textboxes. Hold Space to pan. (V)",
            "add-text": "Drag a rectangle to create an editable textbox. (T)",
            "signature": "Drag a rectangle, then type or draw your signature. (S)",
            "highlight": "Drag across text to highlight in yellow. (H)",
            "underline": "Drag across text to underline. (U)",
            "strikeout": "Drag across text to strike out. (K)",
            "sticky": "Click to add a sticky note. (N)",
            "erase": "Drag a rectangle to white out (redact) content. (E)",
            "image": "Click on the page to insert an image file. (I)",
            "form-text": "Drag to add a single-line fillable text field. Properties dialog opens for tooltip, default value, format.",
            "form-multiline": "Drag to add a multi-line text area for paragraphs of input.",
            "form-check": "Drag to add a checkbox the user can toggle on/off.",
            "form-radio": "Drag to add a radio button — siblings sharing a group name are mutually exclusive. (R)",
            "form-combo": "Drag to add a dropdown menu (one selection from a list of choices). (D)",
            "form-list": "Drag to add a scrollable list box (one or more selections).",
            "form-signature": "Drag to add a signature field where the recipient will sign.",
            "form-date": "Drag to add a date field with a YYYY-MM-DD format hint.",
            "form-button": "Drag to add a push button (link to an action or script). (B)",
        }
        # Single-key shortcuts. We gate them at the QShortcut level (see
        # _install_tool_shortcuts) so they don't fire while typing into a
        # TextBoxItem.
        tool_keys = {
            "select": "V",
            "add-text": "T",
            "signature": "S",
            "highlight": "H",
            "underline": "U",
            "strikeout": "K",
            "sticky": "N",
            "erase": "E",
            "image": "I",
            "form-radio": "R",
            "form-combo": "D",
            "form-button": "B",
        }
        self._form_actions: list[QAction] = []
        form_modes = {
            "form-text", "form-multiline", "form-check", "form-radio",
            "form-combo", "form-list", "form-signature", "form-date",
            "form-button",
        }
        for label, mode in (
            ("Select", "select"),
            ("Add Text", "add-text"),
            ("Signature", "signature"),
            ("Highlight", "highlight"),
            ("Underline", "underline"),
            ("Strikeout", "strikeout"),
            ("Sticky Note", "sticky"),
            ("Erase", "erase"),
            ("Image", "image"),
            ("Text Field", "form-text"),
            ("Multi-line Text", "form-multiline"),
            ("Checkbox", "form-check"),
            ("Radio Button", "form-radio"),
            ("Dropdown", "form-combo"),
            ("List Box", "form-list"),
            ("Signature Field", "form-signature"),
            ("Date Field", "form-date"),
            ("Push Button", "form-button"),
        ):
            act = make(label, None, checkable=True)
            act.setToolTip(tool_tooltips.get(mode, label))
            act.triggered.connect(lambda _=False, m=mode: self._set_mode(m))
            self._tool_group.addAction(act)
            act.setData(mode)
            self._tool_actions.append(act)
            if mode in form_modes:
                self._form_actions.append(act)
            if mode == "select":
                act.setChecked(True)
        self._tool_keys = tool_keys

        # ---- Menu structure (label, list of actions; None = separator;
        # QMenu = submenu) ----
        _form_action_set = set(self._form_actions)
        _insert_actions = [a for a in self._tool_actions if a not in _form_action_set]
        menu_spec: list[tuple[str, list]] = [
            ("&File", [self.act_new, self.act_open, self.recent_menu, None,
                       self.act_save, self.act_save_as, None,
                       self.act_merge, self.act_extract, None,
                       self.act_preferences]),
            ("&Edit", [self.act_undo, self.act_redo, None,
                       self.act_find, self.act_find_next]),
            ("&View", [self.act_prev, self.act_next, None,
                       self.act_zoom_in, self.act_zoom_out, self.act_zoom_reset]),
            ("&Insert", [*_insert_actions, None, self.act_page_numbers]),
            ("&Pages", [self.act_insert_blank, self.act_rotate, self.act_delete_page]),
            ("&Forms", [*self._form_actions, None, self.act_tab_order,
                        None, self.act_reset_form, self.act_flatten_form]),
            ("&Tools", [self.act_watermark]),
        ]

        def _add_to_menu(menu, items):
            for it in items:
                if it is None:
                    menu.addSeparator()
                elif isinstance(it, QMenu):
                    menu.addMenu(it)
                else:
                    menu.addAction(it)

        # Native top-of-screen bar (default on macOS)
        native_mb = self.menuBar()
        native_mb.setNativeMenuBar(True)
        for label, items in menu_spec:
            menu = native_mb.addMenu(label)
            _add_to_menu(menu, items)

        # In-window duplicate: a QToolBar of QToolButtons with popup menus.
        # We use this rather than a second QMenuBar because Qt on macOS won't
        # render a non-native QMenuBar widget reliably alongside the native one.
        self.in_app_menubar = QToolBar("Menus")
        self.in_app_menubar.setObjectName("InAppMenuBar")
        self.in_app_menubar.setMovable(False)
        for label, items in menu_spec:
            btn = QToolButton(self.in_app_menubar)
            btn.setText(label.replace("&", ""))
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(btn)
            _add_to_menu(menu, items)
            btn.setMenu(menu)
            self.in_app_menubar.addWidget(btn)

        # ---- Slim toolbar: page nav, tool modes, find ----
        tb = QToolBar("Main")
        tb.setObjectName("MainToolBar")
        tb.setMovable(False)
        # Stack: menu strip on top, tool toolbar below.
        self.addToolBar(self.in_app_menubar)
        self.addToolBarBreak()
        self.addToolBar(tb)

        # Page nav
        prev_btn = QAction("◀", self)
        prev_btn.triggered.connect(lambda: self.change_page(-1))
        tb.addAction(prev_btn)
        self.page_label = QLabel("  —  ")
        tb.addWidget(self.page_label)
        next_btn = QAction("▶", self)
        next_btn.triggered.connect(lambda: self.change_page(1))
        tb.addAction(next_btn)
        tb.addSeparator()

        # Tool modes
        for act in self._tool_actions:
            tb.addAction(act)

        # Find box on the right
        spacer = QLabel()
        spacer.setSizePolicy(
            spacer.sizePolicy().Policy.Expanding, spacer.sizePolicy().Policy.Preferred
        )
        tb.addWidget(spacer)
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find…")
        self.find_box.setFixedWidth(180)
        self.find_box.returnPressed.connect(self.find_next)
        tb.addWidget(self.find_box)
        self.find_status = QLabel("")
        tb.addWidget(self.find_status)

        # Wire ⌘F to focus the find box
        self.act_find.triggered.connect(self.find_box.setFocus)

        # ---- Format toolbar (third row) ----
        fmt = QToolBar("Format")
        fmt.setObjectName("FormatToolBar")
        fmt.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(fmt)
        self.fmt_toolbar = fmt

        # Font family combo
        self.fmt_family = QComboBox()
        self.fmt_family.setEditable(True)
        self.fmt_family.addItems(BUILTIN_FONTS)
        self.fmt_family.insertSeparator(self.fmt_family.count())
        sys_fonts = installed_system_fonts()
        if sys_fonts:
            self.fmt_family.addItems(sys_fonts)
            self.fmt_family.insertSeparator(self.fmt_family.count())
        self.fmt_family.addItems(POPULAR_FONTS)
        self.fmt_family.setCurrentText("Helvetica")
        self.fmt_family.setMinimumContentsLength(14)
        self.fmt_family.activated.connect(self._fmt_change_family)
        fmt.addWidget(self.fmt_family)

        # Font size spinbox — fire on editingFinished so typing "144" doesn't
        # commit at "1" then "14" then "144" (three resnapshots, three repaints).
        self.fmt_size = QSpinBox()
        self.fmt_size.setRange(4, 288)
        self.fmt_size.setValue(14)
        self.fmt_size.editingFinished.connect(
            lambda: self._fmt_change_size_value(self.fmt_size.value())
        )
        fmt.addWidget(self.fmt_size)

        self.act_size_down = QAction("A−", self)
        self.act_size_down.setShortcut("Ctrl+[")
        self.act_size_down.setShortcutVisibleInContextMenu(True)
        self.act_size_down.triggered.connect(lambda: self._fmt_bump_size(-1))
        fmt.addAction(self.act_size_down)

        self.act_size_up = QAction("A+", self)
        self.act_size_up.setShortcut("Ctrl+]")
        self.act_size_up.setShortcutVisibleInContextMenu(True)
        self.act_size_up.triggered.connect(lambda: self._fmt_bump_size(1))
        fmt.addAction(self.act_size_up)

        fmt.addSeparator()

        # Bold / Italic / Underline / Strike — checkable
        def style_action(label, shortcut, attr, *, bold=False, italic=False,
                          underline=False, strike=False):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setShortcut(shortcut)
            a.setShortcutVisibleInContextMenu(True)
            f = a.font()
            f.setBold(bold)
            f.setItalic(italic)
            f.setUnderline(underline)
            f.setStrikeOut(strike)
            a.setFont(f)
            a.triggered.connect(lambda checked: self._fmt_toggle(attr, checked))
            return a

        self.act_bold = style_action("B", "Ctrl+B", "bold", bold=True)
        self.act_italic = style_action("I", "Ctrl+I", "italic", italic=True)
        self.act_underline = style_action("U", "Ctrl+U", "underline", underline=True)
        self.act_strike = style_action("S", "Ctrl+Shift+X", "strike", strike=True)
        for a in (self.act_bold, self.act_italic, self.act_underline, self.act_strike):
            fmt.addAction(a)

        fmt.addSeparator()

        self.act_text_color = QAction("Color", self)
        self.act_text_color.triggered.connect(self._fmt_change_color)
        fmt.addAction(self.act_text_color)

        self.refresh_format_toolbar()
        self._install_tool_shortcuts()

    def _install_tool_shortcuts(self):
        """Bind single-key tool shortcuts (V/T/S/H/U/K/N/E/I).

        QShortcut with WidgetWithChildrenShortcut + an activatedAmbiguously
        guard would be one option, but the simplest reliable way to keep
        these from firing while the user is typing into a TextBoxItem is to
        check the scene focus item at activation time and bail out.
        """
        for mode, key in self._tool_keys.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(lambda m=mode: self._handle_tool_shortcut(m))

    def _handle_tool_shortcut(self, mode: str):
        # Don't hijack the keystroke if the user is editing a text overlay.
        focus = QApplication.focusWidget()
        scene_focus = self.view.scene_.focusItem() if self.view.scene_ else None
        if isinstance(scene_focus, QGraphicsTextItem) and bool(
            scene_focus.textInteractionFlags()
            & Qt.TextInteractionFlag.TextEditorInteraction
        ):
            return
        # Also skip if focus is in any text-input widget (find box, font-size
        # spinbox, format-toolbar editable combo, etc.). focusWidget() returns
        # the wrapper, not the inner QLineEdit, so check for the wrappers too.
        if isinstance(focus, (QLineEdit, QAbstractSpinBox)):
            return
        if isinstance(focus, QComboBox) and focus.isEditable():
            return
        self._activate_tool(mode)

    # --- format toolbar helpers ---
    def _selected_overlay(self):
        # Kept for back-compat; refresh_format_toolbar uses it for the display
        # values. Mutations now use _selected_overlays() so multi-select works.
        for it in self.view.overlays:
            if it.isSelected():
                return it
        return None

    def _selected_overlays(self):
        return [it for it in self.view.overlays if it.isSelected()]

    def _selected_textboxes(self):
        return [it for it in self.view.overlays
                if it.isSelected() and isinstance(it, TextBoxItem)]

    def refresh_format_toolbar(self):
        it = self._selected_overlay()
        text_selected = isinstance(it, TextBoxItem)
        # Enable controls only when a textbox is selected (signatures get color only)
        for w in (self.fmt_family, self.fmt_size, self.act_size_down, self.act_size_up,
                  self.act_bold, self.act_italic, self.act_underline, self.act_strike):
            w.setEnabled(text_selected)
        self.act_text_color.setEnabled(it is not None)

        if not text_selected:
            return
        # Reflect the (first) selected box's state. With mixed-state multi-
        # selection the toolbar shows the first item's values — applying a
        # change still propagates to all selected boxes.
        self.fmt_family.blockSignals(True)
        self.fmt_size.blockSignals(True)
        self.fmt_family.setCurrentText(it.family)
        self.fmt_size.setValue(int(it.size_pt))
        self.fmt_family.blockSignals(False)
        self.fmt_size.blockSignals(False)
        self.act_bold.setChecked(it.bold)
        self.act_italic.setChecked(it.italic)
        self.act_underline.setChecked(it.underline)
        self.act_strike.setChecked(it.strike)

    def _fmt_toggle(self, attr: str, checked: bool):
        boxes = self._selected_textboxes()
        if not boxes:
            return
        if all(getattr(it, attr) == checked for it in boxes):
            return
        self._snapshot()
        for it in boxes:
            setattr(it, attr, checked)
            it.refresh()
        self._mark_dirty()

    def _fmt_change_family(self):
        boxes = self._selected_textboxes()
        if not boxes:
            return
        new_family = self.fmt_family.currentText().strip() or "Helvetica"
        if all(it.family == new_family for it in boxes):
            return
        self._snapshot()
        for it in boxes:
            it.family = new_family
            it.refresh()
        self._mark_dirty()

    def _fmt_change_size_value(self, v: int):
        boxes = self._selected_textboxes()
        if not boxes:
            return
        new = max(4, min(288, int(v)))
        if all(int(it.size_pt) == new for it in boxes):
            return
        self._snapshot()
        for it in boxes:
            it.size_pt = new
            it.refresh()
        self._mark_dirty()

    def _fmt_bump_size(self, delta: int):
        boxes = self._selected_textboxes()
        if not boxes:
            return
        # Skip the snapshot if every box would be clamped to its current size.
        if all(
            max(4, min(288, int(it.size_pt) + delta)) == int(it.size_pt)
            for it in boxes
        ):
            return
        self._snapshot()
        for it in boxes:
            it.size_pt = max(4, min(288, int(it.size_pt) + delta))
            it.refresh()
        # Reflect the first box's new value in the spinner.
        self.fmt_size.blockSignals(True)
        self.fmt_size.setValue(int(boxes[0].size_pt))
        self.fmt_size.blockSignals(False)
        self._mark_dirty()

    def _fmt_change_color(self):
        items = self._selected_overlays()
        if not items:
            return
        colored = [it for it in items if hasattr(it, "color")]
        if not colored:
            return
        # Open the picker seeded from the first item; apply chosen color to all.
        seed = colored[0].color
        c = QColorDialog.getColor(seed, self, "Text Color")
        if not c.isValid():
            return
        if all(it.color == c for it in colored):
            return
        self._snapshot()
        for it in colored:
            it.color = c
            it.refresh()
        self._mark_dirty()

    # --- View / zoom ---
    def zoom_by(self, factor: float):
        if not self.view.doc:
            return
        self.view.zoom = max(0.3, min(self.view.zoom * factor, 6.0))
        self.view.render_all(preserve_scroll=True)

    def set_zoom(self, z: float):
        if not self.view.doc:
            return
        self.view.zoom = max(0.3, min(z, 6.0))
        self.view.render_all(preserve_scroll=True)

    def _set_mode(self, mode: str):
        self.view.set_mode(mode)
        # Reuse the per-tool tooltip text as the status-bar hint so users
        # discover features like Space-to-pan without hunting for them.
        for act in self._tool_actions:
            if act.data() == mode:
                hint = act.toolTip() or f"Mode: {mode}"
                self.statusBar().showMessage(hint)
                return
        self.statusBar().showMessage(f"Mode: {mode}")

    def _refresh_page_label(self):
        if self.view.doc:
            self.page_label.setText(
                f"  Page {self.view.page_idx + 1} / {self.view.page_count()}  "
            )
        else:
            self.page_label.setText("  —  ")

    # --- Drag and drop ---
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            for u in ev.mimeData().urls():
                if u.toLocalFile().lower().endswith(".pdf"):
                    ev.acceptProposedAction()
                    return
        ev.ignore()

    def dropEvent(self, ev):
        pdf_paths = [
            u.toLocalFile() for u in ev.mimeData().urls()
            if u.toLocalFile().lower().endswith(".pdf")
        ]
        if not pdf_paths:
            return
        if len(pdf_paths) == 1:
            if not self._confirm_discard_changes():
                return
            self.open_path(pdf_paths[0])
            ev.acceptProposedAction()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Multiple PDFs dropped")
        box.setText(f"You dropped {len(pdf_paths)} PDFs. What would you like to do?")
        first_btn = box.addButton(
            "Open first file only", QMessageBox.ButtonRole.AcceptRole
        )
        merge_btn = box.addButton(
            "Merge all", QMessageBox.ButtonRole.AcceptRole
        )
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(first_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_btn or clicked is None:
            return
        if clicked is first_btn:
            if not self._confirm_discard_changes():
                return
            self.open_path(pdf_paths[0])
            ev.acceptProposedAction()
            return
        if clicked is merge_btn:
            if not self._confirm_discard_changes():
                return
            self.open_path(pdf_paths[0])
            if self.view.doc is not None:
                self.merge_pdfs(paths=pdf_paths[1:])
            ev.acceptProposedAction()
            return

    # --- Close warning ---
    def closeEvent(self, ev):
        if not self.dirty:
            return super().closeEvent(ev)
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            "You have unsaved changes. Save before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            self.save_pdf()
            if self.dirty:
                ev.ignore()
                return
            ev.accept()
        elif choice == QMessageBox.StandardButton.Discard:
            ev.accept()
        else:
            ev.ignore()

    # --- file ops ---
    def _confirm_discard_changes(self) -> bool:
        """If dirty, ask the user. Returns True if it's OK to proceed."""
        if not self.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            "You have unsaved changes. Save first?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            self.save_pdf()
            return not self.dirty  # True if save succeeded
        if choice == QMessageBox.StandardButton.Discard:
            return True
        return False

    def new_pdf(self):
        if not self._confirm_discard_changes():
            return
        dlg = NewPDFDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        width_pt, height_pt, count = dlg.values()
        try:
            doc = fitz.open()
            for _ in range(count):
                doc.new_page(width=width_pt, height=height_pt)
        except Exception as exc:
            QMessageBox.critical(self, "New PDF failed", str(exc))
            return
        self.view.clear_overlays()
        if self.view.doc:
            self.view.doc.close()
        self.view.doc = doc
        self.view.page_idx = 0
        self.view.render_all()
        self.path = None
        self._undo.clear()
        self._redo.clear()
        self._search_results.clear()
        self._search_idx = -1
        self.find_status.setText("")
        # Untitled new doc starts dirty so close prompts to save.
        self.dirty = True
        self._refresh_title()
        self._refresh_page_label()
        self._refresh_form_panel()
        self.statusBar().showMessage(
            f"Created new {count}-page PDF ({width_pt/72:.2f} × {height_pt/72:.2f} in)"
        )

    def open_pdf(self):
        if not self._confirm_discard_changes():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.open_path(path)

    def open_path(self, path: str):
        try:
            ok = self.view.load(path)
        except Exception as exc:
            msg = str(exc)
            is_corrupt = isinstance(exc, getattr(fitz, "FileDataError", ())) or \
                "cannot open broken document" in msg.lower() or \
                "no objects found" in msg.lower()
            if is_corrupt:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Critical)
                box.setWindowTitle("Cannot open PDF")
                box.setText("This PDF appears to be corrupted or unreadable.")
                box.setDetailedText(msg)
                box.exec()
            else:
                QMessageBox.critical(self, "Error", f"Could not open PDF:\n{msg}")
            return
        if not ok:
            return
        self.path = path
        self._undo.clear()
        self._redo.clear()
        self._search_results.clear()
        self._search_idx = -1
        self.find_status.setText("")
        self._mark_clean()
        self._refresh_page_label()
        self._add_recent(path)
        self._refresh_form_panel()
        self.statusBar().showMessage(f"Opened {path}")

    # --- Recent files ---
    _RECENT_MAX = 10

    def _recent_settings(self) -> QSettings:
        # No-arg QSettings uses the org/app set on QApplication in main().
        return QSettings()

    @staticmethod
    def _recent_key(path: str) -> str:
        """Normalize a path for case-insensitive / symlink-resolved comparison.

        normcase is a no-op on POSIX, but macOS's HFS+/APFS-default volumes
        are case-insensitive — so we lower-case on darwin to dedup
        /Users/x/A.pdf and /Users/x/a.pdf to one entry.
        """
        try:
            resolved = os.path.realpath(path)
        except Exception:
            resolved = path
        normalized = os.path.normcase(resolved)
        if sys.platform == "darwin":
            normalized = normalized.lower()
        return normalized

    def _add_recent(self, path: str) -> None:
        if not path:
            return
        try:
            abspath = os.path.abspath(path)
        except Exception:
            abspath = path
        s = self._recent_settings()
        existing = s.value("recent_files", []) or []
        if isinstance(existing, str):
            existing = [existing]
        new_key = self._recent_key(abspath)
        # Remove any existing entry whose normalized form matches.
        filtered = [p for p in existing if p and self._recent_key(p) != new_key]
        filtered.insert(0, abspath)
        filtered = filtered[: self._RECENT_MAX]
        s.setValue("recent_files", filtered)

    def _populate_recent_menu(self) -> None:
        self.recent_menu.clear()
        s = self._recent_settings()
        existing = s.value("recent_files", []) or []
        if isinstance(existing, str):
            existing = [existing]
        # Prune missing files; keep the rest.
        kept: list[str] = []
        for p in existing:
            if p and os.path.isfile(p):
                kept.append(p)
        if kept != list(existing):
            s.setValue("recent_files", kept)
        if not kept:
            empty = self.recent_menu.addAction("(No recent files)")
            empty.setEnabled(False)
            return
        for p in kept:
            label = os.path.basename(p) or p
            act = self.recent_menu.addAction(label)
            act.setToolTip(p)
            act.triggered.connect(lambda _=False, path=p: self._open_recent(path))
        self.recent_menu.addSeparator()
        clear_act = self.recent_menu.addAction("Clear Menu")
        clear_act.triggered.connect(self._clear_recent)

    def _open_recent(self, path: str) -> None:
        if not self._confirm_discard_changes():
            return
        if not os.path.isfile(path):
            QMessageBox.warning(
                self, "File missing", f"File no longer exists:\n{path}"
            )
            # Force a prune on next open.
            s = self._recent_settings()
            existing = s.value("recent_files", []) or []
            if isinstance(existing, str):
                existing = [existing]
            s.setValue("recent_files", [p for p in existing if p != path])
            return
        self.open_path(path)

    def _clear_recent(self) -> None:
        s = self._recent_settings()
        s.setValue("recent_files", [])
        # Refresh the visible submenu immediately so the user sees feedback
        # instead of waiting for the next aboutToShow.
        self.recent_menu.clear()
        empty = self.recent_menu.addAction("(No recent files)")
        empty.setEnabled(False)

    # --- Preferences ---
    def open_settings_dialog(self) -> "SettingsDialog":
        dlg = SettingsDialog(self)
        self._last_settings_dialog = dlg
        dlg.exec()
        return dlg

    def _bake_to_clone(self) -> tuple["fitz.Document", list[str]]:
        """Clone the in-memory doc and bake all overlays into the clone.

        Returns (clone, failed) where `failed` is a human-readable list of
        per-overlay failures the caller should surface to the user. Caller
        owns clone and must close it.
        """
        clone = fitz.open(stream=self.view.doc.tobytes(), filetype="pdf")
        failed: list[str] = []
        for ov in self.view.overlays:
            if not (0 <= ov.page_idx < len(clone)):
                failed.append(
                    f"{type(ov).__name__} on page {ov.page_idx + 1} "
                    "(page no longer exists)"
                )
                continue
            try:
                ov.to_pdf(clone[ov.page_idx])
            except Exception as exc:
                kind = getattr(ov, "DISPLAY_NAME", type(ov).__name__)
                failed.append(f"{kind} on page {ov.page_idx + 1}: {exc}")
                print(f"[bake] overlay failed: {exc}", file=sys.stderr)
        return clone, failed

    def _report_bake_failures(self, failed: list[str], *, critical: bool = False) -> None:
        if not failed:
            return
        body = "\n".join(f"  • {f}" for f in failed)
        if critical:
            QMessageBox.critical(
                self,
                "Save aborted",
                f"All {len(failed)} overlay(s) failed to embed; save was "
                "aborted to avoid losing your edits:\n\n"
                f"{body}",
            )
            return
        QMessageBox.warning(
            self,
            "Some overlays could not be embedded",
            f"{len(failed)} overlay(s) could not be embedded into the saved "
            "PDF and were dropped:\n\n"
            f"{body}\n\n"
            "The file was still written, but is missing this content.",
        )

    def _save_clone_atomic(self, path: str) -> bool:
        """Bake overlays into a clone and write to `path` atomically.

        Returns True on success. Surfaces any error or partial-failure via
        QMessageBox. Always cleans up the .tmp file on failure.
        """
        tmp = path + ".tmp"
        clone = None
        failed: list[str] = []
        overlay_count = len(self.view.overlays)
        try:
            clone, failed = self._bake_to_clone()
            if overlay_count > 0 and len(failed) == overlay_count:
                # Every overlay failed to bake — refuse the save so the
                # caller doesn't mark a stale file clean.
                clone.close()
                clone = None
                self._report_bake_failures(failed, critical=True)
                return False
            clone.save(tmp, garbage=4, deflate=True)
            clone.close()
            clone = None
            os.replace(tmp, path)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        finally:
            if clone is not None:
                try:
                    clone.close()
                except Exception:
                    pass
            # Best-effort tmp cleanup; on success os.replace already moved it.
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
        if failed:
            # The file IS on disk — partial bake failures are warnings, not
            # save failures. Surface them, but still return True so callers
            # mark the document clean and update self.path.
            self._report_bake_failures(failed)
        return True

    def save_pdf(self):
        if not self.view.doc:
            return
        if not self.path:
            return self.save_pdf_as()
        if self._save_clone_atomic(self.path):
            self._mark_clean()
            self.statusBar().showMessage(f"Saved {self.path}")

    def save_pdf_as(self):
        if not self.view.doc:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF As", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if self._save_clone_atomic(path):
            self.path = path
            self._mark_clean()
            self._add_recent(path)
            self.statusBar().showMessage(f"Saved {path}")

    def change_page(self, delta: int):
        if not self.view.doc:
            return
        new = max(0, min(self.view.page_idx + delta, self.view.page_count() - 1))
        if new != self.view.page_idx:
            self.view.scroll_to_page(new)
            self._refresh_page_label()

    # --- edit ops ---
    def do_add_text(self, page_idx: int, x0: float, y0: float, x1: float, y1: float):
        """Drop a floating, editable textbox at the dragged rect. Single-click → 240pt wide.

        Opens AddTextDialog first so the user can pick text/font/size/color
        before the overlay materializes. Cancelling drops nothing.
        """
        dlg = AddTextDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text, family, size_pt, color = dlg.values()
        w = x1 - x0
        if w < 30:
            w = 240
        self._snapshot()
        item = TextBoxItem(
            self.view, page_idx, x0, y0, w,
            text=text, family=family or "Helvetica",
            size_pt=size_pt, color=color,
        )
        self.view.overlays.append(item)
        self.view.scene_.addItem(item)
        # Switch back to Select so the user can move/edit further
        self._activate_tool("select")
        item.setSelected(True)
        # Defer entering edit mode until the in-flight mouseReleaseEvent
        # (we're still inside PDFView.mouseReleaseEvent here) has fully
        # unwound. Otherwise Qt clears scene focus as the release completes,
        # and the next click on the new box can't get a text cursor.
        QTimer.singleShot(0, lambda it=item: it.enter_edit_mode(
            Qt.FocusReason.OtherFocusReason
        ))
        self._mark_dirty()
        self.refresh_format_toolbar()

    def _activate_tool(self, mode: str):
        for act in self._tool_actions:
            if act.data() == mode:
                act.setChecked(True)
                break
        self._set_mode(mode)

    def do_signature(self, page_idx: int, x0: float, y0: float, x1: float, y1: float):
        """Open Signature dialog; drop result at the given page rect."""
        w = x1 - x0
        h = y1 - y0
        if w < 60 or h < 20:
            w = max(w, 240)
            h = max(h, 60)
        dlg = SignatureDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.result_data
        if not result:
            return
        self._snapshot()
        if result["kind"] == "typed":
            color = QColor(result.get("color", "#000000"))
            strokes, sig_w, sig_h = _typed_signature_strokes(
                result["text"], result["family"], w, h
            )
            if not strokes:
                # Text→path conversion produced nothing usable (e.g. a font
                # with no glyphs for the input). Fall back to a TextBoxItem.
                item = TextBoxItem(
                    self.view, page_idx, x0, y0, w,
                    text=result["text"],
                    family=result["family"],
                    size_pt=max(18, h * 0.6),
                    color=color,
                )
                self.view.overlays.append(item)
                self.view.scene_.addItem(item)
            else:
                # Center inside the requested rect using the actual bbox of
                # the rendered text path.
                sig_x = x0 + (w - sig_w) / 2 if sig_w < w else x0
                sig_y = y0 + (h - sig_h) / 2 if sig_h < h else y0
                sig = SignatureItem(
                    self.view, page_idx, sig_x, sig_y, sig_w, sig_h, strokes,
                    color=color,
                )
                self.view.overlays.append(sig)
                self.view.scene_.addItem(sig)
        else:  # drawn
            strokes = result["strokes"]  # already normalized 0..1
            # Preserve aspect ratio of the drawn strokes so the signature
            # doesn't get squashed into the user's drag rect.
            xs = [x for s in strokes for (x, _) in s]
            ys = [y for s in strokes for (_, y) in s]
            EPS = 1e-2  # 1% of canvas → too thin to be meaningfully 2D
            sig_x, sig_y, sig_w, sig_h = x0, y0, w, h
            if xs and ys:
                sx0, sx1 = min(xs), max(xs)
                sy0, sy1 = min(ys), max(ys)
                sw = sx1 - sx0
                sh = sy1 - sy0
                # If strokes are nearly collinear (purely horizontal or
                # vertical), re-normalizing against the bbox would explode
                # one axis. Skip the bbox fit and use raw 0..1 strokes.
                if sw > EPS and sh > EPS:
                    stroke_aspect = sw / sh
                    rect_aspect = (w / h) if h > 0 else stroke_aspect
                    if rect_aspect > stroke_aspect:
                        new_w = h * stroke_aspect
                        sig_x = x0 + (w - new_w) / 2
                        sig_w = new_w
                    else:
                        new_h = w / stroke_aspect
                        sig_y = y0 + (h - new_h) / 2
                        sig_h = new_h
                    strokes = [
                        [((x - sx0) / sw, (y - sy0) / sh) for (x, y) in s]
                        for s in strokes
                    ]
            sig = SignatureItem(
                self.view, page_idx, sig_x, sig_y, sig_w, sig_h, strokes,
                color=QColor(result.get("color", "#000000")),
            )
            self.view.overlays.append(sig)
            self.view.scene_.addItem(sig)
        self._activate_tool("select")
        self._mark_dirty()
        self.refresh_format_toolbar()

    def add_page_numbers(self):
        if not self.view.doc:
            return
        self._snapshot()
        total = len(self.view.doc)
        applied = 0
        failures: list[str] = []
        for i in range(total):
            page = self.view.doc[i]
            text = f"Page {i + 1} of {total}"
            try:
                tw = fitz.get_text_length(text, fontname="tiro", fontsize=12)
            except Exception:
                tw = len(text) * 6.0
            x = (page.rect.width - tw) / 2
            y = page.rect.height - 24
            try:
                page.insert_text(
                    (x, y), text, fontname="tiro", fontsize=12, color=(0, 0, 0)
                )
                applied += 1
            except Exception as exc:
                failures.append(f"page {i + 1}: {exc}")
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        if failures:
            body = "\n".join(f"  • {f}" for f in failures)
            QMessageBox.warning(
                self, "Page numbers",
                f"Page numbers added to {applied} of {total} pages.\n\n"
                f"{len(failures)} page(s) failed:\n\n{body}",
            )
        self.statusBar().showMessage(
            f"Added page numbers to {applied} of {total} pages"
        )

    def _resolve_pdf_font(self, family: str, page) -> str:
        """Pick a PyMuPDF fontname for `family`, registering fonts as needed.

        Resolution order: base14 alias → system-font-to-base14 alias →
        local system font file (embedded) → Google Fonts (embedded) → helv.
        Note: embedded system fonts use the regular weight only — bold/italic
        styling on Calibri/Verdana/etc. renders as regular in the saved PDF.
        """
        if family in BASE14_VARIANTS:
            return BASE14_VARIANTS[family][0]
        if family in SYSTEM_FONT_BASE14_ALIAS:
            return SYSTEM_FONT_BASE14_ALIAS[family][0]
        sys_path = find_system_font(family) if family else None
        if sys_path:
            fontname = "sys_" + re.sub(r"[^A-Za-z0-9]", "", family)
            try:
                page.insert_font(fontname=fontname, fontfile=str(sys_path))
                return fontname
            except Exception:
                pass
        ttf = fetch_google_font(family) if family else None
        if ttf:
            fontname = "gf_" + re.sub(r"[^A-Za-z0-9]", "", family)
            try:
                page.insert_font(fontname=fontname, fontfile=str(ttf))
                return fontname
            except Exception:
                pass
        return "helv"

    def do_watermark(self):
        if not self.view.doc:
            return
        page_count = len(self.view.doc)
        dlg = WatermarkDialog(self, page_count=page_count)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        text = (v.get("text") or "").strip()
        if not text:
            QMessageBox.warning(self, "Watermark", "Watermark text is empty.")
            return
        if v.get("all_pages", True):
            indices = list(range(page_count))
        else:
            try:
                indices = parse_page_range(v.get("range", ""), page_count)
            except ValueError as exc:
                QMessageBox.warning(self, "Watermark", f"Bad page range: {exc}")
                return
            if not indices:
                QMessageBox.warning(self, "Watermark", "No pages selected.")
                return
        color = v["color"]
        rgb = (color.redF(), color.greenF(), color.blueF())
        opacity = float(v["opacity"])
        size = int(v["size"])
        rotation = int(v["rotation"])
        family = v["family"]

        self._snapshot()
        applied = 0
        failures: list[str] = []
        for i in indices:
            page = self.view.doc[i]
            fontname = self._resolve_pdf_font(family, page)
            try:
                tw = fitz.get_text_length(text, fontname=fontname, fontsize=size)
            except Exception:
                tw = len(text) * size * 0.55
            cx = page.rect.width / 2
            cy = page.rect.height / 2
            # Place text so its midpoint sits at (cx, cy) before rotation.
            origin = fitz.Point(cx - tw / 2, cy + size * 0.35)
            morph = (fitz.Point(cx, cy), fitz.Matrix(1, 1).prerotate(rotation))
            try:
                page.insert_text(
                    origin, text, fontname=fontname, fontsize=size,
                    color=rgb, fill_opacity=opacity, stroke_opacity=opacity,
                    morph=morph,
                )
                applied += 1
            except Exception as exc:
                failures.append(f"page {i + 1}: {exc}")
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        if failures:
            body = "\n".join(f"  • {f}" for f in failures)
            QMessageBox.warning(
                self, "Watermark",
                f"Watermark applied to {applied} page(s).\n\n"
                f"{len(failures)} page(s) failed:\n\n{body}",
            )
        self.statusBar().showMessage(
            f"Applied watermark to {applied} page(s)"
        )

    def merge_pdfs(self, _checked=False, *, paths: list[str] | None = None):
        if not self.view.doc:
            QMessageBox.information(
                self, "Merge PDF", "Open or create a PDF first to merge into."
            )
            return
        if paths is None:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Merge PDFs (append to current)", "", "PDF Files (*.pdf)"
            )
        if not paths:
            return
        self._snapshot()
        appended = 0
        errors: list[str] = []
        for p in paths:
            try:
                src = fitz.open(p)
            except Exception as exc:
                errors.append(f"{os.path.basename(p)}: {exc}")
                continue
            try:
                if src.needs_pass:
                    pwd, ok = QInputDialog.getText(
                        self, "Password required",
                        f"Enter password for {os.path.basename(p)}:",
                        QLineEdit.EchoMode.Password,
                    )
                    if not ok or not src.authenticate(pwd):
                        errors.append(
                            f"{os.path.basename(p)}: wrong password — skipped"
                        )
                        continue
                self.view.doc.insert_pdf(src)
                appended += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(p)}: {exc}")
            finally:
                try:
                    src.close()
                except Exception:
                    pass
        self.view.render_all(preserve_scroll=True)
        self._refresh_page_label()
        self._mark_dirty()
        if errors:
            QMessageBox.warning(
                self, "Merge", "Some files could not be merged:\n\n"
                + "\n".join(f"  • {e}" for e in errors),
            )
        self.statusBar().showMessage(
            f"Merged {appended} file(s); now {len(self.view.doc)} page(s)"
        )

    def extract_pages_dialog(self):
        if not self.view.doc:
            return
        page_count = len(self.view.doc)
        spec, ok = QInputDialog.getText(
            self, "Extract Pages",
            f"Pages to extract (1–{page_count}), e.g. 1,3-5,8:",
        )
        if not ok or not spec.strip():
            return
        try:
            indices = parse_page_range(spec, page_count)
        except ValueError as exc:
            QMessageBox.warning(self, "Extract Pages", f"Bad page range: {exc}")
            return
        if not indices:
            QMessageBox.warning(self, "Extract Pages", "No pages selected.")
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "Save Extracted Pages", "", "PDF Files (*.pdf)"
        )
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        tmp = out + ".tmp"
        baked, failed = self._bake_to_clone()
        new_doc = fitz.open()
        try:
            for i in indices:
                new_doc.insert_pdf(baked, from_page=i, to_page=i)
            new_doc.save(tmp, garbage=4, deflate=True)
            new_doc.close()
            os.replace(tmp, out)
        except Exception as exc:
            try:
                new_doc.close()
            except Exception:
                pass
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
            QMessageBox.critical(self, "Extract Pages failed", str(exc))
            return
        finally:
            try:
                baked.close()
            except Exception:
                pass
        if failed:
            self._report_bake_failures(failed)
        self.statusBar().showMessage(
            f"Extracted {len(indices)} page(s) to {out}"
        )

    def do_erase(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        page.add_redact_annot(fitz.Rect(x0, y0, x1, y1), fill=(1, 1, 1))
        page.apply_redactions()
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_highlight(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        annot = page.add_highlight_annot(fitz.Rect(x0, y0, x1, y1))
        annot.set_colors(stroke=ANNOTATION_COLORS["highlight"])
        annot.update()
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_underline(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        annot = page.add_underline_annot(fitz.Rect(x0, y0, x1, y1))
        annot.set_colors(stroke=ANNOTATION_COLORS["underline"])
        annot.update()
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_strikeout(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        annot = page.add_strikeout_annot(fitz.Rect(x0, y0, x1, y1))
        annot.set_colors(stroke=ANNOTATION_COLORS["strikeout"])
        annot.update()
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_sticky(self, page_idx: int, x: float, y: float):
        # Empty body → user cancelled by typing nothing; surface a status
        # message and skip creation rather than creating an empty note.
        body, ok = QInputDialog.getMultiLineText(
            self, "Sticky Note", "Note text:"
        )
        if not ok:
            return
        if not body:
            self.statusBar().showMessage("Sticky note cancelled — empty body")
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        page.add_text_annot(fitz.Point(x, y), body)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_insert_image(self, page_idx: int, x0: float, y0: float,
                         x1: float, y1: float):
        """Drop an image as a draggable, resizable overlay.

        Bakes only at save time via ImageOverlayItem.to_pdf. If the drag rect
        is too small (click-only or tiny drag), falls back to a 200pt-wide
        default sized to the image's aspect ratio.
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Insert Image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.tiff *.webp)",
        )
        if not path:
            return
        try:
            img = QImage(path)
            if img.isNull():
                raise ValueError("Could not read image")
            iw, ih = img.width(), img.height()
        except Exception as exc:
            QMessageBox.warning(self, "Insert image failed", str(exc))
            return

        drag_w = x1 - x0
        drag_h = y1 - y0
        if drag_w < 30 or drag_h < 30:
            target_w = 200.0
            target_h = target_w * (ih / iw) if iw else target_w
            px, py = x0, y0
        else:
            target_w = drag_w
            target_h = drag_h
            px, py = x0, y0

        self._snapshot()
        try:
            item = ImageOverlayItem(
                self.view, page_idx, path, px, py, target_w, target_h,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Insert image failed", str(exc))
            try:
                if self._undo:
                    self._undo.pop()
            except Exception:
                pass
            return
        self.view.overlays.append(item)
        self.view.scene_.addItem(item)
        self._activate_tool("select")
        item.setSelected(True)
        self._mark_dirty()
        self.statusBar().showMessage(f"Inserted {os.path.basename(path)}")

    def _post_create_field(self, page_idx: int, w: "fitz.Widget") -> None:
        """Common tail for do_form_*: render, mark dirty, refresh panel,
        then auto-open the Properties dialog. Cancel keeps the field as-is —
        the user dragged it intentionally.

        The original `w` returned from page.add_widget() has xref=0 (the
        widget object isn't rebound to the new annot xref). Refetch from
        page.widgets() so edit_widget_properties can locate it.
        """
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self._refresh_form_panel()
        if not _read_auto_open_field_properties():
            return
        try:
            page = self.view.doc[page_idx]
            real = list(page.widgets())[-1]
        except Exception:
            return
        self.edit_widget_properties(page_idx, real)

    def do_form_text(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "Text")
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = ""
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    def do_form_check(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        side = min(x1 - x0, y1 - y0)
        x1, y1 = x0 + side, y0 + side
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "Checkbox")
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = False
        w.border_color = (0.4, 0.4, 0.4)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    def do_form_multiline(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "Multiline")
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = ""
        w.text_fontsize = 10
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    def do_form_radio(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        side = min(x1 - x0, y1 - y0)
        x1, y1 = x0 + side, y0 + side
        # Group name is the one prompt we keep — radios MUST share a group
        # name to be mutually exclusive, and there's no good default.
        group, ok = QInputDialog.getText(
            self, "Radio Button", "Group name (siblings sharing this name are mutually exclusive):"
        )
        if not ok or not group.strip():
            return
        group = group.strip()
        # Auto-pick a unique export value within this group.
        existing_caps = set(_radio_export_values(self.view.doc, group))
        n = 1
        while f"Option_{n}" in existing_caps:
            n += 1
        export = f"Option_{n}"
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = group
        w.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.button_caption = export
        w.field_value = "Off"
        w.border_color = (0.4, 0.4, 0.4)
        page.add_widget(w)
        new_w = list(page.widgets())[-1]
        try:
            _set_radio_on_state(self.view.doc, new_w.xref, export)
        except Exception as exc:
            print(f"[radio] on-state rename failed: {exc}", file=sys.stderr)
        try:
            _link_radio_group(self.view.doc, group)
        except Exception as exc:
            print(f"[radio] group link failed: {exc}", file=sys.stderr)
        self._post_create_field(page_idx, new_w)

    def do_form_combo(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        # Default to a single placeholder choice; user edits via Options tab.
        choices = ["Option 1"]
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "Dropdown")
        w.field_type = fitz.PDF_WIDGET_TYPE_COMBOBOX
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.choice_values = choices
        w.field_value = choices[0]
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    def do_form_list(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        choices = ["Option 1"]
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "ListBox")
        w.field_type = fitz.PDF_WIDGET_TYPE_LISTBOX
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.choice_values = choices
        w.field_value = choices[0]
        w.text_fontsize = 10
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    def do_form_signature(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "Signature")
        w.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (0.96, 0.96, 0.96)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    def do_form_date(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "Date")
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = ""
        w.field_label = "Expected format: YYYY-MM-DD"
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    def do_form_button(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = _unique_field_name(self.view.doc, "Button")
        w.field_type = fitz.PDF_WIDGET_TYPE_BUTTON
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.button_caption = "Button"
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (0.9, 0.9, 0.9)
        page.add_widget(w)
        self._post_create_field(page_idx, w)

    # --- Form field editing (Phase 2) ---
    def _widget_at(self, page_idx: int, pdf_x: float, pdf_y: float):
        """Return the topmost form widget under (pdf_x, pdf_y) on page_idx, or None."""
        if not self.view.doc or page_idx < 0 or page_idx >= len(self.view.doc):
            return None
        try:
            page = self.view.doc[page_idx]
        except Exception:
            return None
        hit = None
        pt = fitz.Point(pdf_x, pdf_y)
        for w in page.widgets():
            try:
                if w.rect.contains(pt):
                    hit = w  # last match wins (topmost in z-order)
            except Exception:
                continue
        return hit

    def edit_widget_properties(self, page_idx: int, widget):
        """Open the FieldPropertiesDialog for `widget` and persist changes on OK.

        We re-resolve the widget under _bound_widget at apply time so the
        annot binding is guaranteed live, even if `widget` was passed in
        from a panel where the originating page reference has since been GC'd.
        """
        if widget is None or not self.view.doc:
            return
        try:
            xref = widget.xref
        except Exception:
            return
        # Build the dialog against a freshly-resolved widget so its
        # constructor reads from a live annot.
        try:
            with _bound_widget(self.view.doc, page_idx, xref) as (_page, w_init):
                dlg = FieldPropertiesDialog(w_init, parent=self, doc=self.view.doc)
        except Exception as exc:
            QMessageBox.warning(self, "Field properties", f"Could not open: {exc}")
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._snapshot()
        try:
            with _bound_widget(self.view.doc, page_idx, xref) as (_page, w):
                dlg.widget = w  # rebind so _apply_to_widget mutates the live annot
                align_idx = dlg._apply_to_widget()
                w.update()
                try:
                    self.view.doc.xref_set_key(w.xref, "Q", str(int(align_idx)))
                except Exception:
                    pass
                if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                    cap = w.button_caption or ""
                    if cap and cap != "Off":
                        try:
                            _set_radio_on_state(self.view.doc, w.xref, cap)
                        except Exception as exc:
                            print(f"[radio] on-state set failed: {exc}", file=sys.stderr)
                    try:
                        _link_radio_group(self.view.doc, w.field_name or "")
                    except Exception as exc:
                        print(f"[radio] relink failed: {exc}", file=sys.stderr)
        except Exception as exc:
            QMessageBox.warning(self, "Field properties", f"Could not apply: {exc}")
            if self._undo:
                self._undo.pop()
            return
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self._refresh_form_panel()

    def collect_all_widgets(self):
        """Document-wide tab-order source: list of (page_idx, widget) tuples
        in current /Annots order. Phase 4 (Form Builder side panel) reads
        from here so the panel stays in sync with TabOrderDialog."""
        out: list[tuple[int, "fitz.Widget"]] = []
        if not self.view.doc:
            return out
        for pi in range(len(self.view.doc)):
            try:
                page = self.view.doc[pi]
            except Exception:
                continue
            for w in page.widgets():
                out.append((pi, w))
        return out

    def open_tab_order_dialog(self):
        if not self.view.doc:
            return
        dlg = TabOrderDialog(self.view.doc, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._snapshot()
        try:
            dlg.apply_to_doc()
        except Exception as exc:
            QMessageBox.warning(self, "Tab order", f"Could not reorder: {exc}")
            if self._undo:
                self._undo.pop()
            return
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self._refresh_form_panel()

    def delete_widget(self, page_idx: int, widget):
        """Remove `widget` from page `page_idx` (with snapshot + render).

        The caller may pass a stale widget whose page binding has been GC'd.
        We re-resolve via xref under _bound_widget so the delete always runs
        against a live annot bound to a held page.
        """
        if widget is None or not self.view.doc:
            return
        if page_idx < 0 or page_idx >= len(self.view.doc):
            return
        try:
            xref = widget.xref
        except Exception:
            return
        self._snapshot()
        parent_xref: int | None = None
        try:
            with _bound_widget(self.view.doc, page_idx, xref) as (page, w):
                if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                    parent_xref = _radio_parent_xref(self.view.doc, xref)
                page.delete_widget(w)
        except Exception as exc:
            QMessageBox.warning(self, "Delete field", f"Could not delete: {exc}")
            if self._undo:
                self._undo.pop()
            return
        if parent_xref is not None:
            try:
                _cleanup_radio_parent_after_delete(self.view.doc, xref, parent_xref)
            except Exception as exc:
                print(f"[radio] parent cleanup after delete failed: {exc}", file=sys.stderr)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self._refresh_form_panel()

    def reset_form(self) -> None:
        """Clear every form field's value to its default state.

        Text → "", checkbox → False, radio → "Off", combo/list → first
        choice, signature/button → unchanged. Fields are not removed.

        PyMuPDF silently ignores `widget.field_value = ""` for text fields
        (you can replace one non-empty value with another, but can't clear
        back to empty), so we fall back to writing /V=null on the xref
        directly for those.
        """
        if not self.view.doc:
            return
        targets: list[tuple[int, int]] = []
        for pi, w in self.collect_all_widgets():
            try:
                targets.append((pi, w.xref))
            except Exception:
                continue
        if not targets:
            return
        self._snapshot()
        try:
            for pi, xref in targets:
                with _bound_widget(self.view.doc, pi, xref) as (_page, ww):
                    ft = ww.field_type
                    if ft == fitz.PDF_WIDGET_TYPE_TEXT:
                        try:
                            self.view.doc.xref_set_key(xref, "V", "null")
                        except Exception:
                            pass
                    elif ft == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                        ww.field_value = False
                        ww.update()
                    elif ft == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                        try:
                            self.view.doc.xref_set_key(xref, "V", "/Off")
                            self.view.doc.xref_set_key(xref, "AS", "/Off")
                        except Exception:
                            pass
                    elif ft in (
                        fitz.PDF_WIDGET_TYPE_COMBOBOX,
                        fitz.PDF_WIDGET_TYPE_LISTBOX,
                    ):
                        choices = ww.choice_values or []
                        if choices:
                            first = choices[0]
                            ww.field_value = (
                                first if isinstance(first, str)
                                else (first[-1] if first else "")
                            )
                            ww.update()
        except Exception as exc:
            QMessageBox.warning(self, "Reset Form", f"Could not reset: {exc}")
            if self._undo:
                self._undo.pop()
            return
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self._refresh_form_panel()

    def flatten_form(self) -> None:
        """Bake all form widgets into static page content (no longer editable).

        Uses PyMuPDF's doc.bake(annots=False, widgets=True) to convert
        every widget's appearance stream into the page's content stream.
        After this the document is no longer a Form PDF. Operation is
        snapshot-undoable.
        """
        if not self.view.doc:
            return
        if not self.collect_all_widgets():
            QMessageBox.information(self, "Flatten Form", "No form fields to flatten.")
            return
        confirm = QMessageBox.question(
            self,
            "Flatten Form",
            "Flatten all form fields into static page content? "
            "This cannot be edited as form fields afterward (undo will restore).",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Ok:
            return
        self._snapshot()
        try:
            self.view.doc.bake(annots=False, widgets=True)
        except Exception as exc:
            QMessageBox.warning(self, "Flatten Form", f"Could not flatten: {exc}")
            if self._undo:
                self._undo.pop()
            return
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self._refresh_form_panel()

    # --- Form Builder side panel (Phase 4) ---
    def _build_form_panel(self):
        self.form_panel = FormBuilderPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.form_panel)

        self.act_show_form_panel = self.form_panel.toggleViewAction()
        self.act_show_form_panel.setText("Show Form Fields Panel")

        # Insert into the existing &Forms native menu and the in-app duplicate.
        for mb in (self.menuBar(), self.in_app_menubar):
            menus = []
            if isinstance(mb, QToolBar):
                for act in mb.actions():
                    btn = mb.widgetForAction(act)
                    if isinstance(btn, QToolButton) and btn.menu() is not None and btn.text() == "Forms":
                        menus.append(btn.menu())
            else:
                for act in mb.actions():
                    if act.menu() is not None and (act.text() == "&Forms" or act.text() == "Forms"):
                        menus.append(act.menu())
            for menu in menus:
                menu.addSeparator()
                menu.addAction(self.act_show_form_panel)

        # Restore visibility from QSettings (default: hidden until a doc with widgets loads).
        self._form_panel_user_choice: bool | None = self._read_form_panel_visibility()
        if self._form_panel_user_choice is None:
            self.form_panel.setVisible(_read_form_panel_default_visible())
        else:
            self.form_panel.setVisible(self._form_panel_user_choice)
        self.form_panel.visibilityChanged.connect(self._on_form_panel_visibility_changed)

    def _read_form_panel_visibility(self) -> bool | None:
        try:
            s = QSettings()
            v = s.value("formBuilderPanelVisible")
        except Exception:
            return None
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        try:
            return bool(int(v))
        except Exception:
            return bool(v)

    def _on_form_panel_visibility_changed(self, visible: bool) -> None:
        self._form_panel_user_choice = visible
        try:
            QSettings().setValue("formBuilderPanelVisible", visible)
        except Exception:
            pass

    def _refresh_form_panel(self) -> None:
        panel = getattr(self, "form_panel", None)
        if panel is None:
            return
        QTimer.singleShot(0, panel.refresh)
        # Auto-show on first widget if user hasn't explicitly hidden it.
        if self._form_panel_user_choice is None and self.view.doc:
            has_any = any(True for _ in self.collect_all_widgets())
            if has_any and not panel.isVisible():
                panel.blockSignals(True)
                panel.setVisible(True)
                panel.blockSignals(False)

    def focus_widget_in_view(self, page_idx: int, widget) -> None:
        """Scroll the view to a widget and draw a transient highlight ring."""
        if not self.view.doc or widget is None:
            return
        if page_idx < 0 or page_idx >= len(self.view.doc):
            return
        if self.view.mode != "select":
            self._activate_tool("select")
        try:
            scene_rect = self.view._pdf_rect_to_scene(page_idx, widget.rect)
        except Exception:
            return
        self.view.centerOn(scene_rect.center())
        self.view.page_idx = page_idx
        self._refresh_page_label()
        self._show_widget_highlight(scene_rect)

    def _show_widget_highlight(self, scene_rect) -> None:
        item = getattr(self, "_widget_highlight_item", None)
        if item is None or item.scene() is not self.view.scene_:
            item = QGraphicsRectItem()
            accent = current_accent_color()
            pen_color = QColor(accent)
            pen_color.setAlpha(220)
            brush_color = QColor(accent)
            brush_color.setAlpha(40)
            pen = QPen(pen_color)
            pen.setWidth(3)
            item.setPen(pen)
            item.setBrush(QBrush(brush_color))
            item.setZValue(10000)
            self.view.scene_.addItem(item)
            self._widget_highlight_item = item
        item.setRect(scene_rect)
        item.setVisible(True)
        timer = getattr(self, "_widget_highlight_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._hide_widget_highlight)
            self._widget_highlight_timer = timer
        timer.start(1500)

    def _hide_widget_highlight(self) -> None:
        item = getattr(self, "_widget_highlight_item", None)
        if item is not None:
            item.setVisible(False)

    # --- Page management ---
    def rotate_current_page(self):
        if not self.view.doc:
            return
        self._snapshot()
        idx = self.view.page_idx
        page = self.view.doc[idx]
        # Floating overlays are stored in pre-rotation page coordinates. Once
        # the page rotates, those coords land in the wrong scene/PDF spot. Flatten
        # any overlays on this page into the page first so saved output matches
        # what the user sees. Snapshot above lets Cmd+Z undo the whole operation.
        on_page = [ov for ov in self.view.overlays if ov.page_idx == idx]
        baked = 0
        for ov in on_page:
            try:
                ov.to_pdf(page)
                baked += 1
            except Exception as exc:
                print(f"[rotate] bake failed: {exc}", file=sys.stderr)
        if baked:
            self.view.overlays = [ov for ov in self.view.overlays if ov.page_idx != idx]
            for ov in on_page:
                if ov.scene() is self.view.scene_:
                    self.view.scene_.removeItem(ov)
        page.set_rotation((page.rotation + 90) % 360)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self._refresh_form_panel()
        msg = f"Rotated page {idx + 1}"
        if baked:
            msg += f" (flattened {baked} item{'s' if baked != 1 else ''})"
        self.statusBar().showMessage(msg)

    def insert_blank_page(self):
        if not self.view.doc:
            return
        self._snapshot()
        idx = self.view.page_idx
        ref = self.view.doc[idx]
        # Bump page_idx for overlays that sit on pages after the new one.
        for ov in self.view.overlays:
            if ov.page_idx > idx:
                ov.page_idx += 1
        self.view.doc.new_page(pno=idx + 1, width=ref.rect.width, height=ref.rect.height)
        self.view.render_all()
        self.view.scroll_to_page(idx + 1)
        self._refresh_page_label()
        self._mark_dirty()
        self._refresh_form_panel()
        self.statusBar().showMessage(f"Inserted blank page after page {idx + 1}")

    def delete_current_page(self):
        if not self.view.doc:
            return
        if len(self.view.doc) <= 1:
            QMessageBox.information(self, "Cannot delete", "A PDF must have at least one page.")
            return
        idx = self.view.page_idx
        confirm = QMessageBox.question(
            self,
            "Delete page",
            f"Delete page {idx + 1}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._snapshot()
        # Drop overlays on the deleted page and shift indices for pages after.
        kept = []
        for ov in self.view.overlays:
            if ov.page_idx == idx:
                if ov.scene() is self.view.scene_:
                    self.view.scene_.removeItem(ov)
                continue
            if ov.page_idx > idx:
                ov.page_idx -= 1
            kept.append(ov)
        self.view.overlays = kept
        self.view.doc.delete_page(idx)
        new_idx = min(idx, len(self.view.doc) - 1)
        self.view.render_all()
        self.view.scroll_to_page(new_idx)
        self._refresh_page_label()
        self._mark_dirty()
        self._refresh_form_panel()
        self.statusBar().showMessage(f"Deleted page {idx + 1}")

    # --- Find ---
    def find_next(self):
        if not self.view.doc:
            return
        query = self.find_box.text().strip()
        if not query:
            return
        if query != self._search_query:
            self._search_query = query
            self._search_results = []
            for i in range(len(self.view.doc)):
                for r in self.view.doc[i].search_for(query):
                    self._search_results.append((i, r))
            self._search_idx = -1
        if not self._search_results:
            self.find_status.setText("0 matches")
            self.view.show_search_overlays([])
            return
        self._search_idx = (self._search_idx + 1) % len(self._search_results)
        self.find_status.setText(
            f"{self._search_idx + 1} / {len(self._search_results)}"
        )
        page_idx, rect = self._search_results[self._search_idx]
        self.view.scroll_to_pdf_rect(page_idx, rect)
        self.view.show_search_overlays(self._search_results, self._search_idx)


class _PDFApp(QApplication):
    """QApplication subclass that routes macOS 'Open With…' file events to the window."""

    def __init__(self, argv):
        super().__init__(argv)
        self._win: MainWindow | None = None
        self._pending: list[str] = []

    def set_window(self, win: "MainWindow"):
        self._win = win
        # Drain anything queued before window was ready
        for p in self._pending:
            win.open_path(p)
        self._pending.clear()

    def event(self, ev):
        if ev.type() == QEvent.Type.FileOpen:
            path = ev.file()
            if path:
                if self._win:
                    self._win.open_path(path)
                else:
                    self._pending.append(path)
                return True
        return super().event(ev)


def main():
    # Show shortcut text in popup menus on macOS (defaults to hidden there).
    QApplication.setAttribute(
        Qt.ApplicationAttribute.AA_DontShowShortcutsInContextMenus, False
    )
    # Identify the app for QSettings (used by recent-files) before any
    # QSettings() call. Static setters apply globally, so doing this before
    # the QApplication is constructed is fine.
    QApplication.setOrganizationName(APP_ORG)
    QApplication.setOrganizationDomain(APP_ORG_DOMAIN)
    QApplication.setApplicationName(APP_NAME)
    app = _PDFApp(sys.argv)
    app.setApplicationDisplayName("Basic PDF Editor")
    _load_persisted_appearance()
    apply_theme(app, current_theme_name())
    start_font_prefetch(app)
    win = MainWindow()
    win.show()
    app.set_window(win)

    # Open file passed on command line (e.g. via "Open With…" on Linux/Windows)
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".pdf") and os.path.exists(arg):
            win.open_path(arg)
            break

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
