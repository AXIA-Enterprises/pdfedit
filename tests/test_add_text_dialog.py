"""Tests for AddTextDialog wiring and the do_add_text → AddTextDialog hop.

AddTextDialog used to be dead code — do_add_text just hardcoded Helvetica/14.
It now opens the dialog, reads font/size/color/text, and passes them to the
TextBoxItem constructor.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QDialog
from conftest import install_doc, make_blank_doc

import pdfedit


def test_add_text_dialog_returns_values(qtbot):
    """Programmatic API: set fields, accept, read .values()."""
    dlg = pdfedit.AddTextDialog()
    qtbot.addWidget(dlg)

    dlg.text_edit.setText("Hello world")
    dlg.font_box.setCurrentText("Times")
    dlg.size_box.setValue(24)
    dlg.color = QColor(255, 0, 0)
    dlg._update_color_btn()
    dlg._update_preview()

    text, family, size, color = dlg.values()
    assert text == "Hello world"
    assert family == "Times"
    assert size == 24
    assert color.red() == 255 and color.green() == 0 and color.blue() == 0


def test_add_text_dialog_preview_updates(qtbot):
    """Live preview QLabel reflects the chosen font/size/color/text."""
    dlg = pdfedit.AddTextDialog()
    qtbot.addWidget(dlg)

    dlg.text_edit.setText("Preview!")
    dlg.font_box.setCurrentText("Helvetica")
    dlg.size_box.setValue(18)
    dlg.color = QColor(0, 100, 200)
    dlg._update_preview()

    assert dlg.preview.text() == "Preview!"
    f = dlg.preview.font()
    assert f.pointSize() == 18
    # Stylesheet carries the color
    assert "#" in dlg.preview.styleSheet()


def test_do_add_text_uses_dialog_values(main_window, monkeypatch):
    """do_add_text instantiates AddTextDialog and passes its values into TextBoxItem."""
    win = main_window
    install_doc(win, make_blank_doc())

    captured: dict = {}

    real_init = pdfedit.AddTextDialog.__init__

    def patched_init(self, parent=None):
        real_init(self, parent)
        self.text_edit.setText("Custom")
        self.font_box.setCurrentText("Times")
        self.size_box.setValue(22)
        self.color = QColor(10, 20, 30)
        captured["set"] = True

    monkeypatch.setattr(pdfedit.AddTextDialog, "__init__", patched_init)
    # exec is already auto-Accepted via conftest.

    win.do_add_text(0, 50.0, 50.0, 250.0, 80.0)
    boxes = [ov for ov in win.view.overlays if isinstance(ov, pdfedit.TextBoxItem)]
    assert len(boxes) == 1
    box = boxes[0]
    assert box.toPlainText() == "Custom"
    assert box.family == "Times"
    assert box.size_pt == 22
    assert box.color.red() == 10
    assert box.color.green() == 20
    assert box.color.blue() == 30


def test_do_add_text_cancel_creates_no_overlay(main_window, monkeypatch):
    """If the dialog is rejected, no TextBoxItem is appended and no snapshot leaks."""
    win = main_window
    install_doc(win, make_blank_doc())
    undo_before = len(win._undo)

    monkeypatch.setattr(
        pdfedit.AddTextDialog, "exec",
        lambda self: QDialog.DialogCode.Rejected,
    )

    win.do_add_text(0, 50.0, 50.0, 250.0, 80.0)
    boxes = [ov for ov in win.view.overlays if isinstance(ov, pdfedit.TextBoxItem)]
    assert boxes == []
    # No snapshot should have been pushed since the dialog cancelled.
    assert len(win._undo) == undo_before


def test_add_text_dialog_color_button_has_no_black_placeholder(qtbot):
    """The 'Black' literal placeholder is gone — button text matches the color name."""
    dlg = pdfedit.AddTextDialog()
    qtbot.addWidget(dlg)
    # Default color is black (#000000); the button should display the color name,
    # not the literal string "Black".
    assert dlg.color_btn.text() == "#000000"
