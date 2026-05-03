"""Tests for the Phase 2 Field Properties dialog and field-edit helpers.

These exercise FieldPropertiesDialog without going through .exec() — we
instantiate it, drive its sub-widgets directly, then call
`_apply_to_widget()` (which is split out from accept() exactly so tests
can poke it). Then we save+reopen the PDF and confirm the change baked.
"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtGui import QColor

from conftest import install_doc, make_blank_doc

import pdfedit


def _save_and_reopen(win, tmp_path, name):
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists(), f"save failed, no {name}"
    if win.view.doc is not None:
        win.view.doc.close()
        win.view.doc = None
    return fitz.open(str(out))


def _patch_text(monkeypatch, *values):
    it = iter(values)

    def fake(*a, **kw):
        try:
            return (next(it), True)
        except StopIteration:
            return ("", False)

    monkeypatch.setattr(pdfedit.QInputDialog, "getText", staticmethod(fake))


def _first_widget(win):
    """Return (page, widget) — caller MUST keep `page` alive while mutating
    the widget, otherwise the underlying annot binding is GC'd and
    widget.update() raises 'Annot is not bound to a page'."""
    page = win.view.doc[0]
    return page, list(page.widgets())[0]


def test_text_field_name_and_tooltip_round_trip(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "orig_name")
    win.do_form_text(0, 50, 50, 250, 80)

    page, w = _first_widget(win)
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None)
    dlg.name_edit.setText("renamed_field")
    dlg.tooltip_edit.setText("Enter your full name")
    dlg._apply_to_widget()
    w.update()
    del page  # noqa: F841 — kept alive across update(), free after

    with _save_and_reopen(win, tmp_path, "rename.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    assert ww.field_name == "renamed_field"
    assert ww.field_label == "Enter your full name"


def test_checkbox_default_state_round_trip(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "agree")
    win.do_form_check(0, 50, 50, 80, 80)

    page, w = _first_widget(win)
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None)
    dlg.check_default_combo.setCurrentIndex(1)  # Checked
    dlg.export_value_edit.setText("Yes")
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "checked.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    # A "checked" checkbox has its on-state value stored in field_value
    # (typically "Yes" or the export value, never literal False/"Off").
    assert ww.field_value not in (False, "Off", "off", None, "", 0)


def test_combobox_choices_round_trip(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "country", "X")  # X is a placeholder; we'll overwrite
    win.do_form_combo(0, 50, 50, 250, 80)

    page, w = _first_widget(win)
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None)
    # Replace existing choices with a fresh list of three.
    dlg.choices_list.clear()
    for v in ("a", "b", "c"):
        dlg.add_choice_value(v)
    dlg._refresh_choices_default()
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "choices.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    cv = ww.choice_values or []
    # PyMuPDF normalizes choices to either ["a","b"] or [["export","display"], ...].
    flat = [c if isinstance(c, str) else c[-1] for c in cv]
    assert flat == ["a", "b", "c"]


def test_required_flag_round_trip(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "required_field")
    win.do_form_text(0, 50, 50, 250, 80)

    page, w = _first_widget(win)
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None)
    dlg.required_cb.setChecked(True)
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "req.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    assert ww.field_flags & 2, f"expected REQUIRED bit set, got {ww.field_flags}"


def test_border_color_round_trip(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "colored")
    win.do_form_text(0, 50, 50, 250, 80)

    page, w = _first_widget(win)
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None)
    dlg.border_color_btn.set_color(QColor(255, 0, 0))
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "color.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    bc = ww.border_color or []
    assert len(bc) >= 3
    # Allow tiny float drift from the PDF round-trip.
    assert bc[0] > 0.95 and bc[1] < 0.05 and bc[2] < 0.05, f"got {bc}"


def test_delete_widget_removes_one_of_two(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "field_a", "field_b")
    win.do_form_text(0, 50, 50, 250, 80)
    win.do_form_text(0, 50, 100, 250, 130)

    page = win.view.doc[0]
    widgets = list(page.widgets())
    assert len(widgets) == 2
    target = next(w for w in widgets if w.field_name == "field_a")
    win.delete_widget(0, target)

    with _save_and_reopen(win, tmp_path, "deleted.pdf") as doc:
        names = [w.field_name for w in doc[0].widgets()]
    assert names == ["field_b"], f"expected only field_b, got {names}"


def test_widget_at_hit_test(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "hit_me")
    win.do_form_text(0, 100, 100, 300, 140)

    # Inside the rect → finds the widget.
    found = win._widget_at(0, 200, 120)
    assert found is not None
    assert found.field_name == "hit_me"

    # Outside the rect → None.
    miss = win._widget_at(0, 10, 10)
    assert miss is None
