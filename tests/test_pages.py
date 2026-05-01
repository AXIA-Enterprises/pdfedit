"""Tests for page-management ops: rotate, insert blank, delete, extract."""

from __future__ import annotations

import fitz
import pytest

from conftest import install_doc, make_blank_doc

import pdfedit


def _add_textbox(win, page_idx, text="t"):
    box = pdfedit.TextBoxItem(
        win.view, page_idx=page_idx, pdf_x=72, pdf_y=72, pdf_w=400,
        text=text, family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()
    return box


# ---------------------------------------------------------------------------
# rotate_current_page
# ---------------------------------------------------------------------------
def test_rotate_current_page_90_degrees(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    win.view.scroll_to_page(1)
    assert win.view.page_idx == 1

    win.rotate_current_page()

    out = tmp_path / "rot.pdf"
    win.path = str(out)
    win.save_pdf()

    with fitz.open(str(out)) as doc:
        assert doc[0].rotation == 0
        assert doc[1].rotation == 90
        assert doc[2].rotation == 0


# ---------------------------------------------------------------------------
# insert_blank_page
# ---------------------------------------------------------------------------
def test_insert_blank_page_appends(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=1))
    assert win.view.doc.page_count == 1

    win.insert_blank_page()
    assert win.view.doc.page_count == 2
    assert len(win.view._page_geom) == 2


# ---------------------------------------------------------------------------
# delete_current_page
# ---------------------------------------------------------------------------
def test_delete_current_page(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    win.view.scroll_to_page(1)
    assert win.view.page_idx == 1

    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **kw: pdfedit.QMessageBox.StandardButton.Yes),
    )

    win.delete_current_page()
    assert win.view.doc.page_count == 2
    # page_idx should be clamped sensibly: still 1 (now pointing at what was page 2)
    # or 0 — the code does min(idx, len-1) so 1 is fine.
    assert 0 <= win.view.page_idx <= 1


def test_delete_refuses_when_only_one_page(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(pages=1))

    seen = {}

    def fake_info(parent, title, body, *a, **kw):
        seen["title"] = title

    monkeypatch.setattr(pdfedit.QMessageBox, "information", staticmethod(fake_info))
    win.delete_current_page()
    assert win.view.doc.page_count == 1
    assert "delete" in seen.get("title", "").lower()


# ---------------------------------------------------------------------------
# Overlay index shifting on page delete
# ---------------------------------------------------------------------------
def test_overlay_indices_shift_on_page_delete(main_window, monkeypatch):
    """Overlay on page 2 must now report page_idx=1 after deleting page 0."""
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    box = _add_textbox(win, page_idx=2, text="floats-up")

    win.view.scroll_to_page(0)
    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **kw: pdfedit.QMessageBox.StandardButton.Yes),
    )
    win.delete_current_page()

    # The overlay was on page 2 of 3. After deleting page 0, it should now be
    # on page 1 of 2. Find it (it might have been reconstructed via undo
    # snapshot, but the contract is: the same logical text is on page_idx=1).
    surviving = [
        ov for ov in win.view.overlays
        if isinstance(ov, pdfedit.TextBoxItem) and ov.toPlainText() == "floats-up"
    ]
    assert len(surviving) == 1
    assert surviving[0].page_idx == 1, (
        f"expected overlay shifted from page 2 → page 1, got page_idx={surviving[0].page_idx}"
    )


# ---------------------------------------------------------------------------
# extract_pages_dialog
# ---------------------------------------------------------------------------
def test_extract_pages_writes_subset(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(pages=5))

    out = tmp_path / "extract.pdf"
    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText",
        staticmethod(lambda *a, **kw: ("1,3-4", True)),
    )
    monkeypatch.setattr(
        pdfedit.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **kw: (str(out), "PDF Files (*.pdf)")),
    )

    win.extract_pages_dialog()

    assert out.exists()
    with fitz.open(str(out)) as doc:
        # Pages 1, 3, 4 → 3 pages.
        assert doc.page_count == 3
    # No leftover .tmp.
    assert not (tmp_path / "extract.pdf.tmp").exists()
