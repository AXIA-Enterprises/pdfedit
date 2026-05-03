#!/usr/bin/env python3
"""Basic Mac PDF editor — open, add/erase text (Google Fonts), and add form fields."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import fitz  # PyMuPDF
from PyQt6.QtCore import QEvent, QPointF, QRectF, QSettings, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
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
        self.color_btn = QPushButton("Black")
        self.color_btn.clicked.connect(self._pick_color)
        self._update_color_btn()

        form = QFormLayout()
        form.addRow("Text:", self.text_edit)
        form.addRow("Font:", self.font_box)
        form.addRow("Size:", self.size_box)
        form.addRow("Color:", self.color_btn)

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

    def _update_color_btn(self):
        self.color_btn.setText(self.color.name())
        self.color_btn.setStyleSheet(
            f"background:{self.color.name()};"
            f"color:{'white' if self.color.lightness() < 128 else 'black'};"
        )

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
        pen = QPen(QColor(0, 0, 0), 2)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Signature")
        self.setMinimumWidth(480)
        self.result_data: dict | None = None

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
        self.type_input.textChanged.connect(self._update_preview)
        self.type_font.currentTextChanged.connect(self._update_preview)
        ty.addWidget(QLabel("Type your name:"))
        ty.addWidget(self.type_input)
        ty.addWidget(QLabel("Style:"))
        ty.addWidget(self.type_font)
        ty.addWidget(QLabel("Preview:"))
        ty.addWidget(self.type_preview)
        tabs.addTab(type_widget, "Type")

        # --- Draw tab ---
        draw_widget = QWidget()
        dw = QVBoxLayout(draw_widget)
        dw.addWidget(QLabel("Sign with your mouse or trackpad:"))
        self.draw_canvas = _DrawCanvas()
        dw.addWidget(self.draw_canvas)
        undo_stroke_btn = QPushButton("Undo last stroke")
        undo_stroke_btn.clicked.connect(self.draw_canvas.undo_stroke)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.draw_canvas.clear)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(undo_stroke_btn)
        btn_row.addWidget(clear_btn)
        dw.addLayout(btn_row)
        tabs.addTab(draw_widget, "Draw")

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(bb)
        self._tabs = tabs
        self._update_preview()

    def _update_preview(self):
        text = self.type_input.text() or "Your Name"
        family = self.type_font.currentText()
        # Trigger Google Font fetch so the preview matches what gets baked.
        ttf = fetch_google_font(family)
        if ttf:
            from PyQt6.QtGui import QFontDatabase
            QFontDatabase.addApplicationFont(str(ttf))
        f = QFont(family)
        f.setPointSize(34)
        self.type_preview.setFont(f)
        self.type_preview.setText(text)

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
            }
        else:
            strokes = self.draw_canvas.normalized_strokes()
            strokes = [s for s in strokes if len(s) >= 2]
            if not strokes:
                QMessageBox.information(self, "No drawing", "Draw your signature first.")
                return
            self.result_data = {"kind": "drawn", "strokes": strokes}
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


class SignatureItem(QGraphicsPathItem):
    """A drawn (mouse/trackpad) signature overlay. Strokes stored in PDF-space coords."""

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

    def load(self, path: str):
        self.clear_overlays()
        if self.doc:
            self.doc.close()
        self.doc = fitz.open(path)
        self.page_idx = 0
        self.render_all()

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
    def mousePressEvent(self, ev):
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
                          "add-text", "signature"):
            if self.mode == "highlight":
                line_color = QColor(245, 220, 20, 220)
                fill = QColor(245, 220, 20, 90)
            elif self.mode in ("underline", "strikeout"):
                line_color = QColor(60, 130, 220, 220)
                fill = QColor(60, 130, 220, 30)
            elif self.mode in ("add-text", "signature"):
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
            self.window_.do_insert_image(page, sx, sy)
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

    def wheelEvent(self, ev):
        mods = ev.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            factor = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.zoom = max(0.3, min(self.zoom * factor, 6.0))
            self.render_all(preserve_scroll=True)
        else:
            super().wheelEvent(ev)


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
            else:
                continue
            self.view.overlays.append(ov)
        self.view.render_all(preserve_scroll=True)
        self._refresh_page_label()
        self.refresh_format_toolbar()

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

        # Tools (one-shot)
        self.act_watermark = make("Watermark…", self.do_watermark)

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
            "form-text": "Drag to add a fillable text field.",
            "form-multiline": "Drag to add a multi-line text field.",
            "form-check": "Drag to add a checkbox.",
            "form-radio": "Drag to add a radio button (group siblings by name). (R)",
            "form-combo": "Drag to add a dropdown. (D)",
            "form-list": "Drag to add a list box.",
            "form-signature": "Drag to add a signature field for the recipient.",
            "form-date": "Drag to add a date field.",
            "form-button": "Drag to add a push button. (B)",
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
                       self.act_merge, self.act_extract]),
            ("&Edit", [self.act_undo, self.act_redo, None,
                       self.act_find, self.act_find_next]),
            ("&View", [self.act_prev, self.act_next, None,
                       self.act_zoom_in, self.act_zoom_out, self.act_zoom_reset]),
            ("&Insert", [*_insert_actions, None, self.act_page_numbers]),
            ("&Pages", [self.act_insert_blank, self.act_rotate, self.act_delete_page]),
            ("&Forms", [*self._form_actions]),
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
        self.in_app_menubar.setMovable(False)
        mb_font = self.in_app_menubar.font()
        mb_font.setPointSize(mb_font.pointSize() + 2)
        self.in_app_menubar.setFont(mb_font)
        self.in_app_menubar.setStyleSheet(
            "QToolButton { padding: 6px 10px; }"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
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
        tb.setMovable(False)
        tb_font = tb.font()
        tb_font.setPointSize(tb_font.pointSize() + 2)
        tb.setFont(tb_font)
        tb.setStyleSheet(
            "QToolButton { padding: 6px 10px; }"
            "QToolBar QLabel { padding: 0 6px; }"
            "QLineEdit { padding: 4px 6px; }"
        )
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
        fmt.setMovable(False)
        fmt.setFont(tb_font)
        fmt.setStyleSheet(
            "QToolButton { padding: 6px 10px; }"
            "QToolBar QLabel { padding: 0 6px; }"
            "QComboBox, QSpinBox { padding: 2px 6px; }"
        )
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
        for u in ev.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                if self._confirm_discard_changes():
                    self.open_path(p)
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
            self.view.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not open PDF:\n{exc}")
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
        self.statusBar().showMessage(f"Opened {path}")

    # --- Recent files ---
    _RECENT_MAX = 10

    def _recent_settings(self) -> QSettings:
        # No-arg QSettings uses the org/app set on QApplication in main().
        return QSettings()

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
        # Remove any existing entry for this path (case-insensitive on macOS).
        filtered = [p for p in existing if p and p != abspath]
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
                kind = "Text box" if isinstance(ov, TextBoxItem) else "Signature"
                failed.append(f"{kind} on page {ov.page_idx + 1}: {exc}")
                print(f"[bake] overlay failed: {exc}", file=sys.stderr)
        return clone, failed

    def _report_bake_failures(self, failed: list[str]) -> None:
        if not failed:
            return
        body = "\n".join(f"  • {f}" for f in failed)
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
        try:
            clone, failed = self._bake_to_clone()
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
            # The file IS on disk — bake failures are warnings, not save
            # failures. Surface them, but still return True so callers mark
            # the document clean and update self.path.
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
        """Drop a floating, editable textbox at the dragged rect. Single-click → 240pt wide."""
        w = x1 - x0
        if w < 30:
            w = 240
        self._snapshot()
        item = TextBoxItem(self.view, page_idx, x0, y0, w)
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
            item = TextBoxItem(
                self.view, page_idx, x0, y0, w,
                text=result["text"],
                family=result["family"],
                size_pt=max(18, h * 0.6),
                color=QColor(0, 0, 0),
            )
            self.view.overlays.append(item)
            self.view.scene_.addItem(item)
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
            sig = SignatureItem(self.view, page_idx, sig_x, sig_y, sig_w, sig_h, strokes)
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
        for i in range(total):
            page = self.view.doc[i]
            text = f"Page {i + 1} of {total}"
            try:
                tw = fitz.get_text_length(text, fontname="tiro", fontsize=12)
            except Exception:
                tw = len(text) * 6.0
            x = (page.rect.width - tw) / 2
            y = page.rect.height - 24
            page.insert_text(
                (x, y), text, fontname="tiro", fontsize=12, color=(0, 0, 0)
            )
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self.statusBar().showMessage(f"Added page numbers to {total} pages")

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
                print(f"[watermark] page {i + 1}: {exc}", file=sys.stderr)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self.statusBar().showMessage(
            f"Applied watermark to {applied} page(s)"
        )

    def merge_pdfs(self):
        if not self.view.doc:
            QMessageBox.information(
                self, "Merge PDF", "Open or create a PDF first to merge into."
            )
            return
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
                with fitz.open(p) as src:
                    self.view.doc.insert_pdf(src)
                appended += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(p)}: {exc}")
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
        new_doc = fitz.open()
        try:
            for i in indices:
                new_doc.insert_pdf(self.view.doc, from_page=i, to_page=i)
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
        annot.set_colors(stroke=(1, 0.95, 0))
        annot.update()
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_underline(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        annot = page.add_underline_annot(fitz.Rect(x0, y0, x1, y1))
        annot.update()
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_strikeout(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        annot = page.add_strikeout_annot(fitz.Rect(x0, y0, x1, y1))
        annot.update()
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_sticky(self, page_idx: int, x: float, y: float):
        body, ok = QInputDialog.getMultiLineText(
            self, "Sticky Note", "Note text:"
        )
        if not ok or not body:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        page.add_text_annot(fitz.Point(x, y), body)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_insert_image(self, page_idx: int, x: float, y: float):
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
            target_w = 200.0
            target_h = target_w * (ih / iw) if iw else target_w
        except Exception as exc:
            QMessageBox.warning(self, "Insert image failed", str(exc))
            return

        self._snapshot()
        page = self.view.doc[page_idx]
        rect = fitz.Rect(x, y, x + target_w, y + target_h)
        try:
            page.insert_image(rect, filename=path, keep_proportion=True)
        except Exception as exc:
            QMessageBox.warning(self, "Insert image failed", str(exc))
            self._undo.pop()  # rollback snapshot
            return
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()
        self.statusBar().showMessage(f"Inserted {os.path.basename(path)}")

    def do_form_text(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        name, ok = QInputDialog.getText(self, "Text Field", "Field name:")
        if not ok or not name.strip():
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name.strip()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = ""
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_form_check(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        side = min(x1 - x0, y1 - y0)
        x1, y1 = x0 + side, y0 + side
        name, ok = QInputDialog.getText(self, "Checkbox", "Field name:")
        if not ok or not name.strip():
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name.strip()
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = False
        w.border_color = (0.4, 0.4, 0.4)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_form_multiline(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        name, ok = QInputDialog.getText(self, "Multi-line Text", "Field name:")
        if not ok or not name.strip():
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name.strip()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = ""
        w.text_fontsize = 10
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_form_radio(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        side = min(x1 - x0, y1 - y0)
        x1, y1 = x0 + side, y0 + side
        group, ok = QInputDialog.getText(
            self, "Radio Button", "Group name (siblings sharing this name are mutually exclusive):"
        )
        if not ok or not group.strip():
            return
        export, ok = QInputDialog.getText(
            self, "Radio Button", "Export value (the on-state for this button):"
        )
        if not ok or not export.strip():
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = group.strip()
        w.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.button_caption = export.strip()
        w.field_value = "Off"
        w.border_color = (0.4, 0.4, 0.4)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def _prompt_choices(self, title: str):
        name, ok = QInputDialog.getText(self, title, "Field name:")
        if not ok or not name.strip():
            return None, None
        raw, ok = QInputDialog.getText(self, title, "Choices (comma-separated):")
        if not ok:
            return None, None
        choices = [c.strip() for c in raw.split(",") if c.strip()]
        if not choices:
            return None, None
        return name.strip(), choices

    def do_form_combo(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        name, choices = self._prompt_choices("Dropdown")
        if name is None:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_COMBOBOX
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.choice_values = choices
        w.field_value = choices[0]
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_form_list(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        name, choices = self._prompt_choices("List Box")
        if name is None:
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_LISTBOX
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.choice_values = choices
        w.field_value = choices[0]
        w.text_fontsize = 10
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_form_signature(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        name, ok = QInputDialog.getText(self, "Signature Field", "Field name:")
        if not ok or not name.strip():
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name.strip()
        w.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (0.96, 0.96, 0.96)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_form_date(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        name, ok = QInputDialog.getText(self, "Date Field", "Field name:")
        if not ok or not name.strip():
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name.strip()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.field_value = ""
        w.field_label = "Expected format: YYYY-MM-DD"
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

    def do_form_button(self, page_idx: int, x0, y0, x1, y1):
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        caption, ok = QInputDialog.getText(self, "Push Button", "Button caption:")
        if not ok or not caption.strip():
            return
        name, ok = QInputDialog.getText(self, "Push Button", "Field name:")
        if not ok or not name.strip():
            return
        self._snapshot()
        page = self.view.doc[page_idx]
        w = fitz.Widget()
        w.field_name = name.strip()
        w.field_type = fitz.PDF_WIDGET_TYPE_BUTTON
        w.rect = fitz.Rect(x0, y0, x1, y1)
        w.button_caption = caption.strip()
        w.text_fontsize = max(8, int((y1 - y0) * 0.55))
        w.border_color = (0.4, 0.4, 0.4)
        w.fill_color = (0.9, 0.9, 0.9)
        page.add_widget(w)
        self.view.render_all(preserve_scroll=True)
        self._mark_dirty()

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
