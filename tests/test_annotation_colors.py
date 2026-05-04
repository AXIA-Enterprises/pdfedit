"""Tests that underline/strikeout/highlight annotations save with the
expected stroke colors matching the rubber-band preview.
"""

from __future__ import annotations

import fitz
import pytest
from conftest import install_doc, make_blank_doc

import pdfedit


def _save_and_reopen(win, tmp_path, name):
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists()
    return fitz.open(str(out))


def _approx_eq(actual, expected, abs_tol=0.02):
    return all(
        a == pytest.approx(e, abs=abs_tol) for a, e in zip(actual, expected)
    )


def _stroke_for_type(doc, annot_type):
    """Walk page 0 annots and return the colors['stroke'] for the first match."""
    page = doc[0]
    for a in page.annots():
        if a.type[0] == annot_type:
            return a.colors.get("stroke")
    return None


def test_underline_stroke_color_matches_rubber_band(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_underline(0, 60, 60, 240, 80)

    expected = pdfedit.ANNOTATION_COLORS["underline"]
    doc = _save_and_reopen(win, tmp_path, "ul_color.pdf")
    try:
        stroke = _stroke_for_type(doc, fitz.PDF_ANNOT_UNDERLINE)
    finally:
        doc.close()
    assert stroke is not None
    assert _approx_eq(stroke, expected), f"expected {expected}, got {stroke}"


def test_strikeout_stroke_color_matches_rubber_band(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_strikeout(0, 60, 60, 240, 80)

    expected = pdfedit.ANNOTATION_COLORS["strikeout"]
    doc = _save_and_reopen(win, tmp_path, "so_color.pdf")
    try:
        stroke = _stroke_for_type(doc, fitz.PDF_ANNOT_STRIKE_OUT)
    finally:
        doc.close()
    assert stroke is not None
    assert _approx_eq(stroke, expected), f"expected {expected}, got {stroke}"


def test_highlight_stroke_color_unchanged(main_window, tmp_path):
    """Highlight color (yellow) is unchanged by this branch."""
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_highlight(0, 60, 60, 240, 80)

    expected = pdfedit.ANNOTATION_COLORS["highlight"]
    doc = _save_and_reopen(win, tmp_path, "hl_color.pdf")
    try:
        stroke = _stroke_for_type(doc, fitz.PDF_ANNOT_HIGHLIGHT)
    finally:
        doc.close()
    assert stroke is not None
    assert _approx_eq(stroke, expected), f"expected {expected}, got {stroke}"


def test_sticky_empty_body_skips_creation(main_window, monkeypatch, tmp_path):
    """Empty body → status message + no annot. Cancel is the same."""
    win = main_window
    install_doc(win, make_blank_doc())

    monkeypatch.setattr(
        pdfedit.QInputDialog, "getMultiLineText",
        staticmethod(lambda *a, **kw: ("", True)),
    )
    win.do_sticky(0, 100.0, 100.0)

    out = tmp_path / "empty_sticky.pdf"
    win.path = str(out)
    win.save_pdf()

    doc = fitz.open(str(out))
    try:
        annots = list(doc[0].annots())
    finally:
        doc.close()
    sticky_annots = [a for a in annots if a.type[0] == fitz.PDF_ANNOT_TEXT]
    assert sticky_annots == [], "empty body should not create a sticky annot"
