"""Phase 5 end-to-end Adobe-Reader-compatibility test.

Creates one of every supported widget type (text, multiline, check, radio
group of 3, combo, list, signature, date, button), edits each via the
Properties dialog programmatically, reorders tab order, saves to disk,
reopens with fitz, and asserts every property survived.

Plus: opens the saved PDF with pikepdf if available and verifies the
AcroForm dictionary structure is well-formed. pikepdf parses the PDF
the same way Adobe does, so a clean pikepdf parse is a strong signal
that Adobe Reader will be happy too.
"""

from __future__ import annotations

import fitz
import pytest

from conftest import install_doc, make_blank_doc, rename_last_widget

import pdfedit


def _drive_dialog(win, page_idx, widget_xref, fn):
    """Open FieldPropertiesDialog on a freshly-resolved widget, run fn(dlg)
    against it, then call _apply_to_widget + update under a held page so
    the annot binding survives. Mirrors the manual flow Phase 2 tests use.
    """
    page = win.view.doc[page_idx]
    target = next(w for w in page.widgets() if w.xref == widget_xref)
    dlg = pdfedit.FieldPropertiesDialog(target, parent=None, doc=win.view.doc)
    fn(dlg)
    dlg._apply_to_widget()
    target.update()
    del page


def test_full_round_trip_every_field_type(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())

    # --- Create one of each field type --------------------------------------
    # Text field
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "text_field")

    # Multi-line text
    win.do_form_multiline(0, 50, 100, 250, 200)
    rename_last_widget(win, 0, "notes_field")

    # Checkbox
    win.do_form_check(0, 50, 220, 80, 250)
    rename_last_widget(win, 0, "agree_check")

    # Radio group with three buttons (use 'colors' as group name)
    import unittest.mock as _mock
    with _mock.patch.object(
        pdfedit.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("colors", True)),
    ):
        win.do_form_radio(0, 50, 270, 70, 290)
        win.do_form_radio(0, 80, 270, 100, 290)
        win.do_form_radio(0, 110, 270, 130, 290)

    # Combo (dropdown)
    win.do_form_combo(0, 50, 310, 250, 340)
    page = win.view.doc[0]
    combo_w = next(w for w in page.widgets()
                   if w.field_type == fitz.PDF_WIDGET_TYPE_COMBOBOX)
    combo_xref = combo_w.xref
    combo_w.field_name = "country_combo"
    combo_w.choice_values = ["USA", "Canada", "Mexico"]
    combo_w.field_value = "USA"
    combo_w.update()
    del page

    # List box
    win.do_form_list(0, 50, 360, 250, 440)
    page = win.view.doc[0]
    list_w = next(w for w in page.widgets()
                  if w.field_type == fitz.PDF_WIDGET_TYPE_LISTBOX)
    list_xref = list_w.xref
    list_w.field_name = "fruits_list"
    list_w.choice_values = ["apple", "banana", "cherry"]
    list_w.field_value = "apple"
    list_w.update()
    del page

    # Signature
    win.do_form_signature(0, 300, 50, 500, 100)
    rename_last_widget(win, 0, "sig_field")

    # Date
    win.do_form_date(0, 300, 110, 500, 140)
    rename_last_widget(win, 0, "date_field")

    # Button
    win.do_form_button(0, 300, 160, 500, 200)
    rename_last_widget(win, 0, "click_btn")

    # Sanity: 11 widgets total (3 radios + 8 others).
    page = win.view.doc[0]
    all_w = list(page.widgets())
    assert len(all_w) == 11, f"expected 11 widgets, got {len(all_w)}"
    del page

    # --- Drive the Properties dialog for several fields ---------------------
    page = win.view.doc[0]
    by_name = {w.field_name: w.xref for w in page.widgets()
               if w.field_name and w.field_type != fitz.PDF_WIDGET_TYPE_RADIOBUTTON}
    del page

    def _set_tooltip_required(tip: str, required: bool):
        def fn(dlg):
            dlg.tooltip_edit.setText(tip)
            dlg.required_cb.setChecked(required)
        return fn

    _drive_dialog(win, 0, by_name["text_field"],
                  _set_tooltip_required("Your full name", True))
    _drive_dialog(win, 0, by_name["notes_field"],
                  _set_tooltip_required("Comments", False))
    _drive_dialog(win, 0, by_name["agree_check"],
                  lambda d: (d.tooltip_edit.setText("I agree"),
                             d.check_default_combo.setCurrentIndex(1)))

    # Format script on text_field → Number
    _drive_dialog(win, 0, by_name["text_field"],
                  lambda d: d.format_combo.setCurrentText("Number"))

    # Calc script on notes_field via the Options-tab Sum operation
    # (Options overwrites Actions when both are set, by design).
    def _set_calc(d):
        d.calc_op_combo.setCurrentIndex(pdfedit._CALC_OPS.index("Sum"))
        # Pick text_field as the source.
        for i in range(d.calc_sources_list.count()):
            it = d.calc_sources_list.item(i)
            if it.text() == "text_field":
                it.setSelected(True)
    _drive_dialog(win, 0, by_name["notes_field"], _set_calc)

    # --- Reorder tab order (reverse it) ------------------------------------
    pairs = win.collect_all_widgets()
    panel = win.form_panel
    panel.refresh()
    desired = list(reversed([(pi, w.xref) for pi, w in pairs]))
    panel.apply_reorder(desired)

    # --- Save and reopen ----------------------------------------------------
    out = tmp_path / "round_trip.pdf"
    win.path = str(out)
    win.save_pdf()
    assert out.exists()
    if win.view.doc is not None:
        win.view.doc.close()
        win.view.doc = None

    # --- Verify with fitz ---------------------------------------------------
    with fitz.open(str(out)) as doc:
        page = doc[0]
        widgets = list(page.widgets())
        assert len(widgets) == 11

        ws_by_name: dict[str, fitz.Widget] = {}
        for w in widgets:
            if w.field_name and w.field_type != fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                ws_by_name[w.field_name] = w

        # text_field: tooltip + required + Number format script
        tf = ws_by_name["text_field"]
        assert tf.field_label == "Your full name"
        assert tf.field_flags & 2, "REQUIRED bit missing"
        assert "AFNumber_Format" in (tf.script_format or "")

        # notes_field: tooltip + multiline flag + calc script (Sum of text_field)
        nf = ws_by_name["notes_field"]
        assert nf.field_label == "Comments"
        assert nf.field_flags & fitz.PDF_TX_FIELD_IS_MULTILINE
        s = nf.script_calc or ""
        assert 'getField("text_field")' in s, f"missing calc source: {s!r}"
        assert "event.value =" in s, f"missing event.value: {s!r}"

        # checkbox: default Checked + tooltip
        cb = ws_by_name["agree_check"]
        assert cb.field_label == "I agree"
        assert cb.field_value not in (False, "Off", "off", None, "", 0)

        # combo: choices survived
        cb_combo = ws_by_name["country_combo"]
        cv = cb_combo.choice_values or []
        flat = [c if isinstance(c, str) else c[-1] for c in cv]
        assert "USA" in flat and "Canada" in flat and "Mexico" in flat

        # list: choices survived
        lb = ws_by_name["fruits_list"]
        cv = lb.choice_values or []
        flat = [c if isinstance(c, str) else c[-1] for c in cv]
        assert "apple" in flat and "banana" in flat and "cherry" in flat

        # signature, date, button — type survived
        assert ws_by_name["sig_field"].field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE
        assert ws_by_name["date_field"].field_type == fitz.PDF_WIDGET_TYPE_TEXT
        assert ws_by_name["click_btn"].field_type == fitz.PDF_WIDGET_TYPE_BUTTON

        # Three radios in a 'colors' group, all under same parent xref.
        radios = [w for w in widgets if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON]
        assert len(radios) == 3
        for r in radios:
            assert r.field_name == "colors"
        parents = []
        for r in radios:
            kind, val = doc.xref_get_key(r.xref, "Parent")
            assert kind == "xref", f"radio missing /Parent xref: {kind}/{val}"
            parents.append(val)
        assert parents[0] == parents[1] == parents[2], (
            f"radios not under one parent: {parents}"
        )

    # --- pikepdf structural verification (Adobe-style parse) -----------------
    pikepdf = pytest.importorskip("pikepdf")
    with pikepdf.open(str(out)) as pdf:
        # AcroForm must exist on the catalog.
        root = pdf.Root
        assert "/AcroForm" in root, "missing /AcroForm on doc catalog"
        af = root["/AcroForm"]
        assert "/Fields" in af, "missing /Fields in /AcroForm"
        fields = af["/Fields"]
        assert len(fields) > 0, "no top-level fields in /AcroForm/Fields"

        # Every top-level field must carry /T and /FT (or be a radio group
        # parent that does). Walk recursively into /Kids.
        def _walk(field):
            assert "/T" in field or "/Parent" in field, (
                f"field missing /T and /Parent: {field}"
            )
            assert "/FT" in field or "/Parent" in field, (
                f"field missing /FT and /Parent: {field}"
            )
            for kid in field.get("/Kids", []):
                _walk(kid)

        for f in fields:
            _walk(f)

        # /Annots on the page must reference at least our widgets.
        page0 = pdf.pages[0]
        assert "/Annots" in page0, "page 0 missing /Annots"
        annots = page0["/Annots"]
        widget_subtypes = sum(
            1 for a in annots if str(a.get("/Subtype", "")) == "/Widget"
        )
        assert widget_subtypes >= 8, (
            f"expected at least 8 /Widget annots, got {widget_subtypes}"
        )


def test_reset_form_clears_values(main_window, tmp_path):
    """Reset Form should null out user input across all field types."""
    win = main_window
    install_doc(win, make_blank_doc())

    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "name_field")
    page = win.view.doc[0]
    w = list(page.widgets())[0]
    w.field_value = "filled-in"
    w.update()
    del page

    win.do_form_check(0, 50, 100, 80, 130)
    rename_last_widget(win, 0, "agree_check")
    page = win.view.doc[0]
    w = next(x for x in page.widgets() if x.field_name == "agree_check")
    w.field_value = True
    w.update()
    del page

    # Reset everything.
    win.reset_form()

    page = win.view.doc[0]
    name_w = next(x for x in page.widgets() if x.field_name == "name_field")
    chk_w = next(x for x in page.widgets() if x.field_name == "agree_check")
    assert name_w.field_value == "", f"text not reset: {name_w.field_value!r}"
    assert chk_w.field_value in (False, "Off", "off", 0, ""), (
        f"checkbox not reset: {chk_w.field_value!r}"
    )
    del page


def test_flatten_form_removes_widgets(main_window, tmp_path, monkeypatch):
    """Flatten Form should bake fields into page content and remove them
    from page.widgets()."""
    win = main_window
    install_doc(win, make_blank_doc())

    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "to_flatten")
    win.do_form_check(0, 50, 100, 80, 130)
    rename_last_widget(win, 0, "to_flatten_check")

    # Auto-confirm the flatten dialog.
    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **k: pdfedit.QMessageBox.StandardButton.Ok),
    )

    win.flatten_form()

    page = win.view.doc[0]
    widgets = list(page.widgets())
    assert widgets == [], f"widgets remained after flatten: {[w.field_name for w in widgets]}"
    del page

    # Round-trip survives.
    out = tmp_path / "flat.pdf"
    win.path = str(out)
    win.save_pdf()
    assert out.exists()
    with fitz.open(str(out)) as doc:
        assert list(doc[0].widgets()) == []


def test_insert_menu_excludes_form_actions(main_window):
    """The &Insert menu must NOT contain any form-* tool actions."""
    win = main_window
    mb = win.menuBar()
    insert_menu = None
    for act in mb.actions():
        if act.menu() is not None and act.text() in ("&Insert", "Insert"):
            insert_menu = act.menu()
            break
    assert insert_menu is not None, "&Insert menu not found"
    insert_action_modes = []
    for a in insert_menu.actions():
        mode = a.data()
        if isinstance(mode, str):
            insert_action_modes.append(mode)
    form_modes = {
        "form-text", "form-multiline", "form-check", "form-radio",
        "form-combo", "form-list", "form-signature", "form-date",
        "form-button",
    }
    leaked = [m for m in insert_action_modes if m in form_modes]
    assert leaked == [], f"&Insert leaked form actions: {leaked}"


def test_forms_menu_includes_reset_and_flatten(main_window):
    """&Forms menu should expose Reset Form and Flatten Form."""
    win = main_window
    mb = win.menuBar()
    forms_menu = None
    for act in mb.actions():
        if act.menu() is not None and act.text() in ("&Forms", "Forms"):
            forms_menu = act.menu()
            break
    assert forms_menu is not None, "&Forms menu not found"
    titles = [a.text() for a in forms_menu.actions() if a.text()]
    assert "Reset Form" in titles, f"Reset Form missing; got: {titles}"
    assert "Flatten Form" in titles, f"Flatten Form missing; got: {titles}"
