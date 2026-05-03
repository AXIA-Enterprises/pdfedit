"""End-to-end tests for the form-field tools.

These bypass the drag UI by calling do_form_<x> directly with a known
rectangle, monkeypatching QInputDialog where input is required, then save
and reopen the PDF to confirm the widget was baked in with the right
field_type / field_name.
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
    assert out.exists(), f"save failed, no {name}"
    if win.view.doc is not None:
        win.view.doc.close()
        win.view.doc = None
    return fitz.open(str(out))


def _widgets(page):
    return list(page.widgets())


def _patch_text(monkeypatch, *values):
    """Patch QInputDialog.getText to return a sequence of canned values."""
    it = iter(values)

    def fake(*a, **kw):
        try:
            return (next(it), True)
        except StopIteration:
            return ("", False)

    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText", staticmethod(fake)
    )


def test_form_text_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "my_text")
    win.do_form_text(0, 50, 50, 250, 80)

    with _save_and_reopen(win, tmp_path, "ftext.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets, "no widget written"
    assert widgets[0].field_type == fitz.PDF_WIDGET_TYPE_TEXT
    assert widgets[0].field_name == "my_text"


def test_form_multiline_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "notes")
    win.do_form_multiline(0, 50, 50, 250, 150)

    with _save_and_reopen(win, tmp_path, "fmulti.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets and widgets[0].field_type == fitz.PDF_WIDGET_TYPE_TEXT
    assert widgets[0].field_name == "notes"
    assert widgets[0].field_flags & fitz.PDF_TX_FIELD_IS_MULTILINE


def test_form_check_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "agree")
    win.do_form_check(0, 50, 50, 80, 80)

    with _save_and_reopen(win, tmp_path, "fcheck.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets and widgets[0].field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX
    assert widgets[0].field_name == "agree"


def test_form_radio_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "color", "red")
    win.do_form_radio(0, 50, 50, 80, 80)

    with _save_and_reopen(win, tmp_path, "fradio.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets, "no widget written"
    assert widgets[0].field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    assert widgets[0].field_name == "color"


def test_form_combo_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "country", "USA, Canada, Mexico")
    win.do_form_combo(0, 50, 50, 250, 80)

    with _save_and_reopen(win, tmp_path, "fcombo.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets and widgets[0].field_type == fitz.PDF_WIDGET_TYPE_COMBOBOX
    assert widgets[0].field_name == "country"
    assert "USA" in (widgets[0].choice_values or [])


def test_form_list_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "fruits", "apple, banana, cherry")
    win.do_form_list(0, 50, 50, 250, 150)

    with _save_and_reopen(win, tmp_path, "flist.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets and widgets[0].field_type == fitz.PDF_WIDGET_TYPE_LISTBOX
    assert widgets[0].field_name == "fruits"
    assert "banana" in (widgets[0].choice_values or [])


def test_form_signature_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "signer1")
    win.do_form_signature(0, 50, 50, 250, 100)

    with _save_and_reopen(win, tmp_path, "fsig.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets and widgets[0].field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE
    assert widgets[0].field_name == "signer1"


def test_form_date_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "birthdate")
    win.do_form_date(0, 50, 50, 250, 80)

    with _save_and_reopen(win, tmp_path, "fdate.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets and widgets[0].field_type == fitz.PDF_WIDGET_TYPE_TEXT
    assert widgets[0].field_name == "birthdate"


def test_form_button_baked(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "Click me", "submit_btn")
    win.do_form_button(0, 50, 50, 200, 90)

    with _save_and_reopen(win, tmp_path, "fbtn.pdf") as doc:
        widgets = _widgets(doc[0])
    assert widgets and widgets[0].field_type == fitz.PDF_WIDGET_TYPE_BUTTON
    assert widgets[0].field_name == "submit_btn"
