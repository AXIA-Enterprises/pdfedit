"""End-to-end tests for the annotation tools (highlight/underline/strike/sticky/erase).

These tests bypass the drag UI and call the do_* methods directly with a
known rectangle, then save and re-open the PDF to confirm the annotation
landed in the file with the correct PyMuPDF type code.
"""

from __future__ import annotations

import fitz
import pytest

from conftest import install_doc, make_blank_doc

import pdfedit


def _save_and_reopen(win, tmp_path, name):
    """Save the current main_window doc and return a fresh fitz.Document."""
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists(), f"save failed, no {name}"
    # Detach the in-memory doc so the test reads the on-disk bytes only.
    if win.view.doc is not None:
        win.view.doc.close()
        win.view.doc = None
    return fitz.open(str(out))


def _annot_types(page):
    return [a.type[0] for a in page.annots()] if list(page.annots()) else []


# ---------------------------------------------------------------------------
# Highlight
# ---------------------------------------------------------------------------
def test_highlight_baked_into_pdf(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_highlight(0, 50, 50, 200, 70)

    with _save_and_reopen(win, tmp_path, "hl.pdf") as doc:
        types = _annot_types(doc[0])
    assert fitz.PDF_ANNOT_HIGHLIGHT in types, \
        f"expected PDF_ANNOT_HIGHLIGHT in {types!r}"


# ---------------------------------------------------------------------------
# Underline
# ---------------------------------------------------------------------------
def test_underline_baked_into_pdf(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_underline(0, 50, 50, 200, 70)

    with _save_and_reopen(win, tmp_path, "ul.pdf") as doc:
        types = _annot_types(doc[0])
    assert fitz.PDF_ANNOT_UNDERLINE in types, \
        f"expected PDF_ANNOT_UNDERLINE in {types!r}"


# ---------------------------------------------------------------------------
# Strikeout
# ---------------------------------------------------------------------------
def test_strikeout_baked_into_pdf(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_strikeout(0, 50, 50, 200, 70)

    with _save_and_reopen(win, tmp_path, "so.pdf") as doc:
        types = _annot_types(doc[0])
    assert fitz.PDF_ANNOT_STRIKE_OUT in types, \
        f"expected PDF_ANNOT_STRIKE_OUT in {types!r}"


# ---------------------------------------------------------------------------
# Sticky note
# ---------------------------------------------------------------------------
def test_sticky_baked_into_pdf(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())

    monkeypatch.setattr(
        pdfedit.QInputDialog, "getMultiLineText",
        staticmethod(lambda *a, **kw: ("hello sticky", True)),
    )
    win.do_sticky(0, 100.0, 100.0)

    with _save_and_reopen(win, tmp_path, "sticky.pdf") as doc:
        types = _annot_types(doc[0])
    assert fitz.PDF_ANNOT_TEXT in types, \
        f"expected PDF_ANNOT_TEXT (sticky) in {types!r}"


# ---------------------------------------------------------------------------
# Erase
# ---------------------------------------------------------------------------
def test_erase_covers_text(main_window, tmp_path):
    """Bake some text, then erase a rectangle over it; assert text is gone."""
    win = main_window
    install_doc(win, make_blank_doc())
    # Bake text directly via fitz so it survives clone/reopen.
    page = win.view.doc[0]
    page.insert_text((72, 72), "ERASE-ME", fontname="helv", fontsize=18)

    # Erase covers the text rect.
    win.do_erase(0, 60, 60, 250, 90)

    with _save_and_reopen(win, tmp_path, "erase.pdf") as doc:
        text = doc[0].get_text()
    assert "ERASE-ME" not in text, (
        f"erased region still extracts text: {text!r}"
    )
