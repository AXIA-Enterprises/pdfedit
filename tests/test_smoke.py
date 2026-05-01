"""Pytest-qt smoke tests for pdfedit.

These tests exercise the core user-visible flows so a regression in any
of them fails loudly in CI rather than waiting to be hit by a user.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QKeyEvent
from PyQt6.QtWidgets import QApplication, QGraphicsTextItem, QToolBar

from conftest import install_doc, make_blank_doc, scene_to_viewport

import pdfedit


# ---------------------------------------------------------------------------
# 1. App boots
# ---------------------------------------------------------------------------
def test_app_boots_with_toolbars(main_window, qtbot):
    """MainWindow constructs and the expected toolbars/widgets are present."""
    win = main_window
    assert win.windowTitle().startswith("Basic PDF Editor")
    assert win.view is not None
    assert win.view.scene_ is not None

    # The format toolbar is held as an attribute; the menu bar toolbar too.
    assert isinstance(win.fmt_toolbar, QToolBar)
    assert isinstance(win.in_app_menubar, QToolBar)

    # There should be at least three QToolBars (menu strip, main, format).
    toolbars = win.findChildren(QToolBar)
    assert len(toolbars) >= 3, f"expected >=3 toolbars, got {len(toolbars)}"

    # Tool actions are wired up.
    tool_modes = {a.data() for a in win._tool_actions}
    assert {"select", "add-text", "signature"}.issubset(tool_modes)


# ---------------------------------------------------------------------------
# 2. New PDF
# ---------------------------------------------------------------------------
def test_new_pdf_creates_one_letter_page(main_window, qtbot):
    """Skip the modal dialog and confirm view.doc has 1 Letter-size page."""
    win = main_window
    doc = make_blank_doc(width_pt=612.0, height_pt=792.0, pages=1)
    install_doc(win, doc)

    assert win.view.doc is not None
    assert win.view.doc.page_count == 1
    page = win.view.doc[0]
    assert round(page.rect.width, 1) == 612.0
    assert round(page.rect.height, 1) == 792.0
    # Page geometry should have been computed by render_all().
    assert len(win.view._page_geom) == 1


# ---------------------------------------------------------------------------
# 3. Add Text tool flow (REGRESSION TEST for the click-to-type bug)
# ---------------------------------------------------------------------------
# REGRESSION TEST: the user reported that after dragging out a textbox via the
# Add Text tool, clicking back into the box did not put it in edit mode and
# typed keystrokes were dropped. do_add_text() is supposed to leave the new
# box focused with TextEditorInteraction enabled. If this test fails, the
# regression has come back.
def test_add_text_drag_creates_editable_box(main_window, qtbot):
    """Drag out a textbox, type into it, verify it captured the keystrokes."""
    win = main_window
    install_doc(win, make_blank_doc())

    view = win.view

    # Switch to Add Text mode via the action so the toolbar state matches a
    # real user click rather than just calling _set_mode under the hood.
    for act in win._tool_actions:
        if act.data() == "add-text":
            act.setChecked(True)
            act.trigger()
            break
    assert view.mode == "add-text"

    # Pick two scene points well inside the first page. The page top-left in
    # scene coords is (PAGE_MARGIN, top_y) and the page is rendered at
    # view.zoom, so 100,100 PDF-points → these scene coords:
    z = view.zoom
    top = view._page_geom[0][0]
    p1_scene = QPointF(pdfedit.PAGE_MARGIN + 100 * z, top + 100 * z)
    p2_scene = QPointF(pdfedit.PAGE_MARGIN + 300 * z, top + 140 * z)

    # Make sure those points are actually inside the rendered page rect.
    view.centerOn(p1_scene)
    qtbot.wait(50)

    p1 = scene_to_viewport(view, p1_scene)
    p2 = scene_to_viewport(view, p2_scene)

    viewport = view.viewport()
    # Press → at least one move → release. The intermediate move events are
    # what actually drive the rubber-band tracking in PDFView.mouseMoveEvent.
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=p1)
    mid = QPoint((p1.x() + p2.x()) // 2, (p1.y() + p2.y()) // 2)
    qtbot.mouseMove(viewport, pos=mid)
    qtbot.mouseMove(viewport, pos=p2)
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=p2)

    # do_add_text uses QTimer.singleShot(0, enter_edit_mode) — wait for the
    # event loop to drain that deferred call before asserting edit-mode flags.
    qtbot.wait(150)

    # do_add_text should have appended exactly one TextBoxItem.
    text_boxes = [ov for ov in view.overlays if isinstance(ov, pdfedit.TextBoxItem)]
    assert len(text_boxes) == 1, f"expected 1 textbox, got {len(text_boxes)}"

    box = text_boxes[0]
    # The freshly created box must be selected and put into edit mode so the
    # user can immediately type — this is the precise contract the regression
    # broke.
    assert box.isSelected(), "new textbox should be selected"
    assert bool(
        box.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
    ), "new textbox should have TextEditorInteraction set"
    assert box.hasFocus() or view.scene_.focusItem() is box, (
        "new textbox should hold scene focus so keystrokes route to it"
    )

    # And the tool should auto-revert to Select.
    assert view.mode == "select"

    # Now type — this is what was failing for the user. Key events for a
    # QGraphicsTextItem must be delivered to the scene so the scene routes
    # them to its focus item; sending to the viewport doesn't reliably reach
    # the scene under the offscreen platform.
    scene = view.scene_
    for ch in "hello":
        for ev_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            ev = QKeyEvent(
                ev_type, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, ch
            )
            QApplication.sendEvent(scene, ev)
    qtbot.wait(50)
    assert "hello" in box.toPlainText(), (
        f"expected 'hello' in textbox, got {box.toPlainText()!r}"
    )


# ---------------------------------------------------------------------------
# 4. Save bake
# ---------------------------------------------------------------------------
def test_save_bakes_textbox_into_pdf(main_window, qtbot, tmp_path):
    """Add a textbox programmatically, save, then re-open with fitz."""
    win = main_window
    install_doc(win, make_blank_doc())

    # Build the textbox directly. We're not testing the drag here (that's the
    # job of the previous test); we're testing the bake path.
    box = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=72, pdf_w=400,
        text="hello", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()

    out = tmp_path / "out.pdf"
    win.path = str(out)
    win.save_pdf()
    assert out.exists()

    with fitz.open(str(out)) as doc:
        assert doc.page_count == 1
        text = doc[0].get_text()
    assert "hello" in text, f"expected 'hello' baked into PDF, got {text!r}"


# ---------------------------------------------------------------------------
# 5. Signature dialog
# ---------------------------------------------------------------------------
def test_signature_dialog_typed(qtbot, monkeypatch):
    """Typed-name signature returns result_data with kind=='typed'."""
    # Avoid hitting the network — Google Font fetching is irrelevant here.
    monkeypatch.setattr(pdfedit, "fetch_google_font", lambda family: None)

    dlg = pdfedit.SignatureDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    qtbot.keyClicks(dlg.type_input, "Jane Doe")
    assert dlg.type_input.text() == "Jane Doe"

    dlg._accept()  # bypass the QDialogButtonBox plumbing
    assert dlg.result() == dlg.DialogCode.Accepted
    assert dlg.result_data is not None
    assert dlg.result_data["kind"] == "typed"
    assert dlg.result_data["text"] == "Jane Doe"


# ---------------------------------------------------------------------------
# 6. Atomic save_pdf_as
# ---------------------------------------------------------------------------
def test_save_pdf_as_is_atomic(main_window, qtbot, tmp_path, monkeypatch):
    """save_pdf_as must write through a .tmp + os.replace and clean up on
    failure. Verify both the success path (.tmp gone, target PDF exists) and
    that a failure leaves no .tmp littering the directory."""
    win = main_window
    install_doc(win, make_blank_doc())

    out = tmp_path / "saved.pdf"

    # Skip the file dialog by stubbing it.
    monkeypatch.setattr(
        pdfedit.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **kw: (str(out), "PDF Files (*.pdf)")),
    )
    win.save_pdf_as()
    assert out.exists(), "saved file should exist on success"
    assert not (tmp_path / "saved.pdf.tmp").exists(), \
        ".tmp file must not be left behind on success"
    assert win.path == str(out)

    # Now simulate a failure mid-save: monkeypatch fitz.Document.save on the
    # clone path by making _bake_to_clone return a doc whose save raises.
    out2 = tmp_path / "fails.pdf"
    monkeypatch.setattr(
        pdfedit.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **kw: (str(out2), "PDF Files (*.pdf)")),
    )
    # Stub QMessageBox.critical so the test isn't blocked by a modal.
    seen = {}
    monkeypatch.setattr(
        pdfedit.QMessageBox,
        "critical",
        staticmethod(lambda *a, **kw: seen.setdefault("called", True)),
    )

    real_bake = win._bake_to_clone

    def boom():
        clone, failed = real_bake()
        # Replace clone.save with a method that raises after the .tmp is
        # created (or before — either way, cleanup must happen).
        original_save = clone.save

        def bad_save(*a, **kw):
            # Touch the tmp path first so we can verify cleanup. Then raise.
            tmp = (a[0] if a else kw.get("filename"))
            if tmp:
                with open(tmp, "wb") as f:
                    f.write(b"%PDF-1.4\n")
            raise RuntimeError("synthetic save failure")

        clone.save = bad_save
        return clone, failed

    monkeypatch.setattr(win, "_bake_to_clone", boom)
    win.save_pdf_as()
    assert seen.get("called"), "expected QMessageBox.critical on save error"
    assert not out2.exists(), "failed save must not leave a partial output"
    assert not (tmp_path / "fails.pdf.tmp").exists(), \
        ".tmp must be cleaned up on save failure"


# ---------------------------------------------------------------------------
# 7. Watermark applied to pages
# ---------------------------------------------------------------------------
def test_watermark_applied_to_pages(main_window, qtbot, tmp_path, monkeypatch):
    """Run do_watermark via a stubbed dialog, save, and confirm the text is in
    the saved PDF on the targeted pages."""
    win = main_window
    install_doc(win, make_blank_doc(pages=3))

    # Build a fake dialog whose exec() returns Accepted and whose values()
    # returns a deterministic config. We skip the dialog UI entirely.
    class FakeDlg:
        def __init__(self, *a, **kw):
            pass

        def exec(self):
            return pdfedit.QDialog.DialogCode.Accepted

        def values(self):
            return {
                "text": "CONFIDENTIAL",
                "family": "Helvetica",
                "size": 48,
                "opacity": 0.4,
                "rotation": 0,  # 0 keeps the layout simple for assertions
                "color": pdfedit.QColor(0, 0, 0),
                "all_pages": True,
                "range": "",
            }

    monkeypatch.setattr(pdfedit, "WatermarkDialog", FakeDlg)
    win.do_watermark()

    # Save and re-open to verify the text is actually drawn on every page.
    out = tmp_path / "wm.pdf"
    win.path = str(out)
    win.save_pdf()
    assert out.exists()
    with fitz.open(str(out)) as doc:
        assert doc.page_count == 3
        for i in range(3):
            assert "CONFIDENTIAL" in doc[i].get_text(), \
                f"watermark missing on page {i}"


# ---------------------------------------------------------------------------
# 8. Extract pages range parser
# ---------------------------------------------------------------------------
def test_parse_page_range():
    """parse_page_range handles single pages, dashes, commas, dedup, clamping."""
    # Basic cases.
    assert pdfedit.parse_page_range("1", 10) == [0]
    assert pdfedit.parse_page_range("1,3", 10) == [0, 2]
    assert pdfedit.parse_page_range("1-3", 10) == [0, 1, 2]
    assert pdfedit.parse_page_range("1,3-5,8", 10) == [0, 2, 3, 4, 7]
    # De-dup overlapping ranges.
    assert pdfedit.parse_page_range("1-3,2-4", 10) == [0, 1, 2, 3]
    # Reversed dash order is normalized.
    assert pdfedit.parse_page_range("5-3", 10) == [2, 3, 4]
    # Out-of-range entries are dropped (not raised).
    assert pdfedit.parse_page_range("8-15", 10) == [7, 8, 9]
    assert pdfedit.parse_page_range("0,11", 10) == []
    # Whitespace tolerance.
    assert pdfedit.parse_page_range(" 1 , 3 - 4 ", 10) == [0, 2, 3]
    # Empty input → empty result, not an error.
    assert pdfedit.parse_page_range("", 10) == []
    # Malformed input raises ValueError.
    import pytest as _pytest
    with _pytest.raises(ValueError):
        pdfedit.parse_page_range("abc", 10)


# ---------------------------------------------------------------------------
# 9. Regression: save_pdf_as must not raise AttributeError on the success path
# ---------------------------------------------------------------------------
# save_pdf_as calls self._add_recent(path) after writing the file. If that
# method (or any other in the post-write success block) is ever removed or
# renamed, save_pdf_as will raise AttributeError after the file is already on
# disk and the user sees a traceback even though the save succeeded. This
# test pins the contract: a successful save_pdf_as must complete without any
# exception and must update self.path.
def test_save_pdf_as_no_attribute_error(main_window, qtbot, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    out = tmp_path / "asaved.pdf"
    monkeypatch.setattr(
        pdfedit.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **kw: (str(out), "PDF Files (*.pdf)")),
    )
    # Must not raise. If it does, _add_recent (or another missing method) is
    # being invoked from the success path.
    win.save_pdf_as()
    assert out.exists()
    assert win.path == str(out)


# ---------------------------------------------------------------------------
# 10. Regression: Underline/Strikeout/Sticky tools must have callable handlers
# ---------------------------------------------------------------------------
# PDFView.mouseReleaseEvent dispatches to self.window_.do_underline,
# do_strikeout and do_sticky for these three modes. If any of those methods
# is ever removed or renamed, a drag in those modes (or a click in sticky)
# would crash with AttributeError. This test pins the contract: each method
# must exist on MainWindow and be callable.
def test_annotation_tools_have_handlers(main_window):
    win = main_window
    for name in ("do_underline", "do_strikeout", "do_sticky"):
        attr = getattr(win, name, None)
        assert attr is not None, f"MainWindow.{name} is missing — {name} tool will crash"
        assert callable(attr), f"MainWindow.{name} is not callable — {name} tool will crash"


# ---------------------------------------------------------------------------
# 11. Regression: tool shortcuts must NOT fire while typing into the
#     format-toolbar spin box (or any QAbstractSpinBox / editable QComboBox).
#     QApplication.focusWidget() returns the spinbox wrapper, not its inner
#     QLineEdit — so a naive isinstance(focus, QLineEdit) check is wrong.
# ---------------------------------------------------------------------------
def test_tool_shortcut_ignored_while_typing_in_spinbox(main_window):
    win = main_window
    install_doc(win, make_blank_doc())
    # The format-toolbar size spinbox is normally disabled when no textbox
    # is selected; enable it so we can grab focus the way an editing user
    # would have.
    win.fmt_size.setEnabled(True)
    win.fmt_size.setFocus(Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()
    fw = QApplication.focusWidget()
    assert fw is not None, "expected a focused widget"
    # focusWidget() may return the spinbox itself or its inner line-edit;
    # both paths must be gated.
    win.view.set_mode("select")
    win._handle_tool_shortcut("add-text")
    assert win.view.mode == "select", \
        f"tool shortcut leaked while focus is in {type(fw).__name__}"

    # Same gate must apply to the editable font-family combobox.
    win.fmt_family.setEnabled(True)
    win.fmt_family.setFocus(Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()
    win.view.set_mode("select")
    win._handle_tool_shortcut("add-text")
    assert win.view.mode == "select", \
        "tool shortcut leaked while focus is in the editable combobox"
