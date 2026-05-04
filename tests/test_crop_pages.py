"""Feature G: Crop Pages.

Drives `MainWindow.do_crop` / `apply_crop_to_pages` / `reset_crop_on_pages`
without going through the modal CropConfirmDialog (the dialog is exec()'d
synchronously and would block under the offscreen platform). The dialog
itself is also smoke-tested via direct construction.
"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox

from conftest import install_doc, make_blank_doc

import pdfedit


def _save_and_reopen(win, tmp_path, name: str):
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists()
    return fitz.open(str(out))


def _accept_dialog(monkeypatch, *, scope="current", range_text=""):
    """Patch CropConfirmDialog.exec so do_crop runs without a real dialog."""
    def fake_exec(self):
        if scope == "current":
            self.rb_current.setChecked(True)
        elif scope == "all":
            self.rb_all.setChecked(True)
        elif scope == "range":
            self.rb_range.setChecked(True)
            self.range_edit.setText(range_text)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(pdfedit.CropConfirmDialog, "exec", fake_exec)


# ---------------------------------------------------------------- 1. round-trip
def test_crop_round_trips_to_disk(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=1))
    _accept_dialog(monkeypatch, scope="current")

    win.do_crop(0, 50.0, 100.0, 500.0, 700.0)

    cb = win.view.doc[0].cropbox
    assert (cb.x0, cb.y0, cb.x1, cb.y1) == (50.0, 100.0, 500.0, 700.0)

    reopened = _save_and_reopen(win, tmp_path, "cropped.pdf")
    cb2 = reopened[0].cropbox
    assert (cb2.x0, cb2.y0, cb2.x1, cb2.y1) == (50.0, 100.0, 500.0, 700.0)
    reopened.close()


# ---------------------------------------------------------------- 2. apply to all
def test_apply_to_all_pages_gets_same_cropbox(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=3))
    _accept_dialog(monkeypatch, scope="all")

    win.do_crop(0, 40.0, 60.0, 560.0, 720.0)

    for i in range(3):
        cb = win.view.doc[i].cropbox
        assert (cb.x0, cb.y0, cb.x1, cb.y1) == (40.0, 60.0, 560.0, 720.0)


# ---------------------------------------------------------------- 3. apply to range
def test_apply_to_page_range(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=5))
    _accept_dialog(monkeypatch, scope="range", range_text="2-4")
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Ok),
    )

    win.do_crop(0, 40.0, 60.0, 560.0, 720.0)

    cropped = (40.0, 60.0, 560.0, 720.0)
    full = (0.0, 0.0, 612.0, 792.0)
    # Pages 1 and 5 (indices 0, 4) untouched.
    for i in (0, 4):
        cb = win.view.doc[i].cropbox
        assert (cb.x0, cb.y0, cb.x1, cb.y1) == full
    # Pages 2-4 (indices 1, 2, 3) cropped.
    for i in (1, 2, 3):
        cb = win.view.doc[i].cropbox
        assert (cb.x0, cb.y0, cb.x1, cb.y1) == cropped


# ---------------------------------------------------------------- 4. tiny rect
def test_tiny_rect_rejected(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=1))
    # Patch exec so a *real* dialog never blocks even if the guard breaks.
    _accept_dialog(monkeypatch, scope="current")
    original = (
        win.view.doc[0].cropbox.x0,
        win.view.doc[0].cropbox.y0,
        win.view.doc[0].cropbox.x1,
        win.view.doc[0].cropbox.y1,
    )

    win.do_crop(0, 100.0, 100.0, 110.0, 110.0)

    cb = win.view.doc[0].cropbox
    assert (cb.x0, cb.y0, cb.x1, cb.y1) == original
    msg = win.statusBar().currentMessage().lower()
    assert "too small" in msg


# ---------------------------------------------------------------- 5. clamp out-of-bounds
def test_out_of_bounds_rect_clamped(main_window):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=1))

    n = win.apply_crop_to_pages((-100.0, -50.0, 1000.0, 900.0), [0])
    assert n == 1
    cb = win.view.doc[0].cropbox
    assert cb.x0 == 0.0 and cb.y0 == 0.0
    assert cb.x1 == 612.0 and cb.y1 == 792.0


def test_clamp_per_page_when_pages_have_different_sizes(main_window):
    """A 3-page doc where page 1 is letter (612×792) and pages 2-3 are A5
    (about 419×595). Apply rect (50, 50, 600, 700) to all — page 1 takes it
    verbatim; smaller pages clamp to their own mediabox."""
    win = main_window
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=419, height=595)
    doc.new_page(width=419, height=595)
    install_doc(win, doc)

    n = win.apply_crop_to_pages((50.0, 50.0, 600.0, 700.0), [0, 1, 2])
    assert n == 3
    p0 = win.view.doc[0].cropbox
    assert (p0.x0, p0.y0, p0.x1, p0.y1) == (50.0, 50.0, 600.0, 700.0)
    p1 = win.view.doc[1].cropbox
    assert (p1.x0, p1.y0) == (50.0, 50.0)
    assert p1.x1 == 419.0 and p1.y1 == 595.0
    p2 = win.view.doc[2].cropbox
    assert p2.x1 == 419.0 and p2.y1 == 595.0


# ---------------------------------------------------------------- 6. reset
def test_reset_crop_restores_mediabox(main_window):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=1))

    win.apply_crop_to_pages((100.0, 100.0, 500.0, 700.0), [0])
    cb = win.view.doc[0].cropbox
    assert (cb.x0, cb.y0, cb.x1, cb.y1) == (100.0, 100.0, 500.0, 700.0)

    n = win.reset_crop_on_pages([0])
    assert n == 1
    cb2 = win.view.doc[0].cropbox
    mb = win.view.doc[0].mediabox
    assert (cb2.x0, cb2.y0, cb2.x1, cb2.y1) == (mb.x0, mb.y0, mb.x1, mb.y1)


def test_reset_crop_dialog_all_pages(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=3))
    win.apply_crop_to_pages((50.0, 50.0, 500.0, 700.0), [0, 1, 2])

    monkeypatch.setattr(
        QInputDialog, "getItem",
        staticmethod(lambda *a, **kw: ("All pages (3)", True)),
    )
    win.reset_crop_dialog()

    for i in range(3):
        cb = win.view.doc[i].cropbox
        mb = win.view.doc[i].mediabox
        assert (cb.x0, cb.y0, cb.x1, cb.y1) == (mb.x0, mb.y0, mb.x1, mb.y1)


# ---------------------------------------------------------------- 7. undo
def test_undo_restores_pre_crop_cropbox(main_window):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=1))
    pre = win.view.doc[0].cropbox
    pre_t = (pre.x0, pre.y0, pre.x1, pre.y1)

    win.apply_crop_to_pages((100.0, 100.0, 500.0, 700.0), [0])
    after = win.view.doc[0].cropbox
    assert (after.x0, after.y0, after.x1, after.y1) == (100.0, 100.0, 500.0, 700.0)

    win.undo()
    restored = win.view.doc[0].cropbox
    assert (restored.x0, restored.y0, restored.x1, restored.y1) == pre_t


# ---------------------------------------------------------------- 8. thumbnails
def test_thumbnails_refresh_after_crop(main_window):
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=2))
    panel = getattr(win, "thumbs_panel", None)
    if panel is None:
        pytest.skip("no thumbnails panel on this build")

    panel.setVisible(True)
    panel.refresh()
    refreshed_before = getattr(panel, "_render_count", None)

    win.apply_crop_to_pages((50.0, 50.0, 500.0, 700.0), [0, 1])

    if refreshed_before is None:
        # No counter — at minimum, make sure refresh runs without error and
        # the panel is still attached.
        assert panel is win.thumbs_panel
    else:
        assert panel._render_count > refreshed_before


# ---------------------------------------------------------------- tool wiring
def test_crop_tool_mode_registered(main_window):
    win = main_window
    modes = [a.data() for a in win._tool_actions]
    assert "crop" in modes
    assert win._tool_keys.get("crop") == "C"


def test_crop_tool_action_in_pages_menu(main_window):
    """The Pages menu should include the Crop tool action and Reset Crop."""
    win = main_window
    pages_items = None
    for label, items in win._menu_spec:
        if label == "&Pages":
            pages_items = items
            break
    assert pages_items is not None
    datas = [getattr(it, "data", lambda: None)() for it in pages_items if it is not None]
    assert "crop" in datas
    titles = [it.text() for it in pages_items if it is not None]
    assert any("Reset Crop" in t for t in titles)


def test_crop_dialog_constructs(qtbot):
    """Smoke-test CropConfirmDialog so the preview painter path runs."""
    dlg = pdfedit.CropConfirmDialog(
        None,
        page_idx=0,
        page_count=3,
        rect=(50.0, 50.0, 500.0, 700.0),
        page_w=612.0,
        page_h=792.0,
    )
    qtbot.addWidget(dlg)
    assert dlg.scope() == "current"
    dlg.rb_all.setChecked(True)
    assert dlg.scope() == "all"
    dlg.rb_range.setChecked(True)
    dlg.range_edit.setText("1-3")
    assert dlg.scope() == "range"
    assert dlg.range_text() == "1-3"


def test_crop_translates_relative_drag_to_mediabox(main_window, monkeypatch):
    """When a page already has a non-default cropbox, dragging a rect inside
    the rendered page should produce a mediabox-space cropbox offset by the
    current cropbox's top-left."""
    win = main_window
    install_doc(win, make_blank_doc(width_pt=612, height_pt=792, pages=1))
    # First crop.
    win.apply_crop_to_pages((100.0, 100.0, 500.0, 700.0), [0])
    # Now the page renders as 400×600 starting at user-space (0,0). A drag at
    # (50, 60)→(300, 500) should translate to mediabox (150, 160)→(400, 600).
    _accept_dialog(monkeypatch, scope="current")
    win.do_crop(0, 50.0, 60.0, 300.0, 500.0)
    cb = win.view.doc[0].cropbox
    assert (cb.x0, cb.y0, cb.x1, cb.y1) == (150.0, 160.0, 400.0, 600.0)
