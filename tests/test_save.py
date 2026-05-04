"""Tests for the save_pdf paths and atomic-write/bake-failure semantics."""

from __future__ import annotations

import fitz
import pytest

from conftest import install_doc, make_blank_doc

import pdfedit


def test_save_pdf_atomic_temp_cleanup(main_window, tmp_path):
    """save_pdf (non-As) writes through a .tmp + os.replace, leaves no .tmp."""
    win = main_window
    install_doc(win, make_blank_doc())
    out = tmp_path / "x.pdf"
    win.path = str(out)

    win.save_pdf()
    assert out.exists()
    assert not (tmp_path / "x.pdf.tmp").exists()
    assert win.dirty is False


def test_save_pdf_partial_bake_warning(main_window, tmp_path, monkeypatch):
    """If SOME overlays fail to bake (but not all), _report_bake_failures must warn."""
    win = main_window
    install_doc(win, make_blank_doc())

    # Two overlays: only the first will fail. The second succeeds → "partial".
    box_fail = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=72, pdf_w=400,
        text="will-fail", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box_fail)
    win.view.scene_.addItem(box_fail)
    box_fail.refresh()
    box_ok = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=200, pdf_w=400,
        text="will-succeed", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box_ok)
    win.view.scene_.addItem(box_ok)
    box_ok.refresh()

    real_to_pdf = pdfedit.TextBoxItem.to_pdf

    def maybe_boom(self, page):
        if "will-fail" in self.toPlainText():
            raise RuntimeError("synthetic bake failure")
        return real_to_pdf(self, page)

    monkeypatch.setattr(pdfedit.TextBoxItem, "to_pdf", maybe_boom)

    seen = {}

    def fake_warning(parent, title, body, *a, **kw):
        seen["title"] = title
        seen["body"] = body

    monkeypatch.setattr(pdfedit.QMessageBox, "warning", staticmethod(fake_warning))

    out = tmp_path / "warn.pdf"
    win.path = str(out)
    win.save_pdf()

    assert out.exists(), "file should still be written even with bake failures"
    assert "could not be embedded" in seen.get("body", "").lower()
    # Dirty should be cleared because the file IS on disk.
    assert win.dirty is False


def test_save_pdf_round_trips_text_through_close_and_reopen(
    main_window, tmp_path
):
    """A baked textbox containing 'round trip' must be present in the saved PDF."""
    win = main_window
    install_doc(win, make_blank_doc())
    box = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=120, pdf_w=400,
        text="round trip", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()

    out = tmp_path / "rt.pdf"
    win.path = str(out)
    win.save_pdf()
    assert out.exists()

    # Close the in-memory doc to make the test reflect a true reopen.
    win.view.doc.close()
    win.view.doc = None

    with fitz.open(str(out)) as doc:
        assert doc.page_count == 1
        text = doc[0].get_text()
    assert "round trip" in text, f"expected 'round trip' baked, got {text!r}"
