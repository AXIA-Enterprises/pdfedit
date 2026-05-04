"""Tests for Feature E: OCR (Recognize Text).

These tests skip cleanly on machines without Tesseract — both the pytesseract
Python wrapper AND the `tesseract` CLI binary must be present. CI without
tesseract installed will report this whole file as skipped, which is fine.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

# Skip the entire module if either dependency is missing. importorskip handles
# the wrapper; we then probe the CLI binary directly.
pytesseract = pytest.importorskip("pytesseract")

if shutil.which("tesseract") is None:
    pytest.skip(
        "tesseract binary not on PATH — skipping OCR tests",
        allow_module_level=True,
    )

try:
    subprocess.run(
        ["tesseract", "--version"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except Exception:
    pytest.skip(
        "tesseract --version failed — skipping OCR tests",
        allow_module_level=True,
    )

import io  # noqa: E402

import fitz  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import pdfedit  # noqa: E402

from conftest import install_doc, make_blank_doc  # noqa: E402


def _make_text_image_pdf(text: str = "Hello OCR World") -> fitz.Document:
    """Build a 1-page PDF whose only content is a rendered raster image of
    `text`. The PDF has no native text — the only way to read `text` out is OCR.
    """
    img = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            font = ImageFont.truetype(path, 96)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((40, 120), text, fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(20, 200, 592, 392), stream=buf.getvalue())
    return doc


def test_pipeline_makes_image_pdf_searchable():
    """Render an image of 'Hello OCR World', run OCR, then page.get_text()
    should contain that string (Tesseract may produce minor variations)."""
    doc = _make_text_image_pdf("Hello OCR World")
    try:
        # Sanity check: no text before OCR.
        assert doc[0].get_text().strip() == ""
        summary = pdfedit.run_ocr_on_doc(doc, [0], "eng", skip_existing=True)
        assert summary["processed"] == 1
        assert summary["words"] >= 1
        text = doc[0].get_text()
        # Tesseract output may differ by a character; require the meaningful
        # tokens to be present rather than exact-match the whole phrase.
        assert "Hello" in text
        assert "OCR" in text or "OCR" in text.replace("0", "O")
        assert "World" in text
    finally:
        doc.close()


def test_skip_existing_text_does_not_modify_doc(main_window, qtbot, monkeypatch):
    """A page that already has selectable text should be skipped, and the
    main-window dirty flag should remain False."""
    win = main_window
    doc = make_blank_doc()
    page = doc[0]
    page.insert_text((72, 120), "I am already searchable.",
                     fontsize=14, fontname="helv")
    install_doc(win, doc)
    win.dirty = False  # install_doc sets dirty=True; reset for this assertion

    apply_mode = pdfedit.OCRDialog.OUTPUT_APPLY  # capture before patch

    class FakeDlg:
        def __init__(self, *a, **kw):
            pass

        def exec(self):
            return pdfedit.QDialog.DialogCode.Accepted

        def values(self):
            return {
                "range": "all",
                "lang_label": "English",
                "lang": "eng",
                "skip_existing": True,
                "output_mode": apply_mode,
            }

    # Auto-accept the "modify current doc?" confirm.
    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **kw: pdfedit.QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(pdfedit, "OCRDialog", FakeDlg)

    win.run_ocr()

    assert win.dirty is False, "skipped-only run must not dirty the doc"


def test_language_label_to_code_mapping():
    mapping = dict(pdfedit.OCR_LANGUAGES)
    assert mapping["English"] == "eng"
    assert mapping["Spanish"] == "spa"
    assert mapping["French"] == "fra"
    assert mapping["German"] == "deu"
    assert mapping["Chinese (Simplified)"] == "chi_sim"
    assert mapping["Japanese"] == "jpn"
    assert mapping["Auto-detect"] == "osd"


def test_missing_tesseract_shows_install_message(main_window, qtbot, monkeypatch):
    """If `which('tesseract')` returns None at click time, run_ocr() must show
    a QMessageBox.warning with install instructions and not crash, even when no
    document is loaded."""
    monkeypatch.setattr(pdfedit.shutil, "which", lambda name: None, raising=False)

    captured = {}

    def fake_warning(parent, title, body, *a, **kw):
        captured["title"] = title
        captured["body"] = body
        return pdfedit.QMessageBox.StandardButton.Ok

    monkeypatch.setattr(pdfedit.QMessageBox, "warning",
                        staticmethod(fake_warning))

    main_window.run_ocr()

    assert "Recognize Text" in captured.get("title", "")
    body = captured.get("body", "")
    assert "brew install tesseract" in body
    assert "apt install tesseract-ocr" in body
    assert "UB-Mannheim" in body


def test_check_tesseract_available_when_missing(monkeypatch):
    monkeypatch.setattr(pdfedit.shutil, "which", lambda name: None, raising=False)
    ok, reason = pdfedit._check_tesseract_available()
    assert ok is False
    assert "tesseract" in reason.lower()


def test_ocr_dialog_default_values(main_window):
    dlg = pdfedit.OCRDialog(main_window, page_count=5)
    v = dlg.values()
    assert v["range"] == "all"
    assert v["lang"] == "eng"
    assert v["lang_label"] == "English"
    assert v["skip_existing"] is True
    assert v["output_mode"] == pdfedit.OCRDialog.OUTPUT_APPLY


def test_apply_run_marks_doc_dirty(main_window, qtbot, monkeypatch):
    """A real OCR pass on an image-only page should set the dirty flag."""
    win = main_window
    doc = _make_text_image_pdf("Searchable Now")
    install_doc(win, doc)
    win.dirty = False

    apply_mode = pdfedit.OCRDialog.OUTPUT_APPLY  # capture before patch

    class FakeDlg:
        def __init__(self, *a, **kw):
            pass

        def exec(self):
            return pdfedit.QDialog.DialogCode.Accepted

        def values(self):
            return {
                "range": "all",
                "lang_label": "English",
                "lang": "eng",
                "skip_existing": True,
                "output_mode": apply_mode,
            }

    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **kw: pdfedit.QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(pdfedit, "OCRDialog", FakeDlg)

    win.run_ocr()

    assert win.dirty is True
    assert "Searchable" in win.view.doc[0].get_text() or \
           "Now" in win.view.doc[0].get_text()
