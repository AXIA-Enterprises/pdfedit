"""Tests for the doc-bytes + serialized-overlays undo/redo stack."""

from __future__ import annotations

import pytest

from conftest import install_doc, make_blank_doc

import pdfedit


def _add_textbox(win, text="hello", page_idx=0):
    box = pdfedit.TextBoxItem(
        win.view, page_idx=page_idx, pdf_x=72, pdf_y=72, pdf_w=400,
        text=text, family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()
    return box


def test_undo_after_add_textbox(main_window):
    win = main_window
    install_doc(win, make_blank_doc())

    win._snapshot()  # baseline = empty overlays
    _add_textbox(win, "to-be-undone")
    assert len(win.view.overlays) == 1

    win.undo()
    # Overlay should be gone after restore_state.
    assert len(win.view.overlays) == 0


def test_redo_restores_undone_textbox(main_window):
    win = main_window
    install_doc(win, make_blank_doc())

    win._snapshot()  # baseline = empty overlays
    _add_textbox(win, "redo-me")
    assert len(win.view.overlays) == 1

    win.undo()
    assert len(win.view.overlays) == 0
    win.redo()
    assert len(win.view.overlays) == 1
    assert win.view.overlays[0].toPlainText() == "redo-me"


def test_undo_stack_capped_at_max_undo(main_window):
    """Snapshot more than MAX_UNDO times; assert stack stays bounded."""
    win = main_window
    install_doc(win, make_blank_doc())
    cap = pdfedit.MAX_UNDO
    # Push cap + 5 snapshots so the oldest entries get evicted.
    for _ in range(cap + 5):
        win._snapshot()
    assert len(win._undo) == cap, (
        f"undo stack must cap at MAX_UNDO={cap}, got {len(win._undo)}"
    )


def test_undo_after_save_does_not_corrupt_doc(main_window, tmp_path):
    """Save + undo: in-memory doc must remain consistent (page_count, overlays)."""
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    expected_pages = win.view.doc.page_count

    win._snapshot()  # baseline
    _add_textbox(win, "savetest")

    out = tmp_path / "u.pdf"
    win.path = str(out)
    win.save_pdf()
    assert out.exists()

    # Now undo. Doc must still report 2 pages and overlays must reflect the
    # restored state (empty, since we snapshotted before adding the textbox).
    win.undo()
    assert win.view.doc is not None
    assert win.view.doc.page_count == expected_pages
    assert len(win.view.overlays) == 0
