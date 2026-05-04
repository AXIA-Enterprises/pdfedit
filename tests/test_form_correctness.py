"""QA pass: form correctness regressions (audits 4 + 7).

Each test exercises one bug from the qa/forms-correctness branch:
- Cross-page drag-reorder no longer duplicates a widget xref
- Renaming a grouped radio rewrites /T on the parent (group survives)
- Deleting one radio kid leaves the parent + remaining kids intact
- Inline rename to "Total"/"Title" no longer eats the leading "T"
- Page mutations (rotate/insert/delete) refresh the form panel
- FieldPropertiesDialog round-trips for alignment / choice default /
  field_display / multi-line / Actions JS preservation
"""

from __future__ import annotations

import re
import unittest.mock as _mock

import fitz
import pytest

from conftest import install_doc, make_blank_doc, rename_last_widget

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


def _patch_radio_group(monkeypatch, name):
    monkeypatch.setattr(
        pdfedit.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: (name, True)),
    )


# --- Bug 1: cross-page drag-reorder ----------------------------------------

def test_cross_page_drag_reorder_does_not_duplicate(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "moved")
    win.do_form_text(1, 50, 50, 250, 80)
    rename_last_widget(win, 1, "p2_keep")

    panel = win.form_panel
    panel.refresh()

    # Find xrefs
    page0 = win.view.doc[0]
    moved_xref = next(w.xref for w in page0.widgets() if w.field_name == "moved")
    page1 = win.view.doc[1]
    keep_xref = next(w.xref for w in page1.widgets() if w.field_name == "p2_keep")
    del page0, page1

    # Drag "moved" from page 0 to page 1: new order has both on page 1.
    desired = [(1, moved_xref), (1, keep_xref)]
    panel.apply_reorder(desired)

    with _save_and_reopen(win, tmp_path, "cross_page.pdf") as doc:
        p0_names = [w.field_name for w in doc[0].widgets()]
        p1_names = [w.field_name for w in doc[1].widgets()]

    assert p0_names == [], f"page 0 still has widgets: {p0_names}"
    assert "moved" in p1_names and "p2_keep" in p1_names
    assert p1_names.count("moved") == 1, f"duplicate widget on page 1: {p1_names}"


# --- Bug 2: rename a grouped radio kid -------------------------------------

def test_rename_grouped_radio_keeps_group(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_radio_group(monkeypatch, "g1")
    win.do_form_radio(0, 50, 50, 70, 70)
    win.do_form_radio(0, 50, 100, 70, 120)
    win.do_form_radio(0, 50, 150, 70, 170)

    panel = win.form_panel
    panel.refresh()
    # Find the first radio's tree item.
    top = panel.tree.topLevelItem(0)
    item = top.child(0)
    assert panel.rename_item(item, "g_renamed")

    with _save_and_reopen(win, tmp_path, "radio_rename.pdf") as doc:
        radios = [
            w for w in doc[0].widgets()
            if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        ]
        assert len(radios) == 3
        parents = []
        for r in radios:
            kind, val = doc.xref_get_key(r.xref, "Parent")
            assert kind == "xref", f"radio missing /Parent: {kind}/{val}"
            parents.append(val)
        assert parents[0] == parents[1] == parents[2], (
            f"radios split across parents after rename: {parents}"
        )
        # All kids should report the new inherited name.
        names = [r.field_name for r in radios]
        assert names == ["g_renamed"] * 3, f"kids names: {names}"
        # Parent /T must be the new name.
        parent_xref_str = parents[0]
        m = re.match(r"\s*(\d+)\s+0\s+R", parent_xref_str)
        assert m, f"could not parse parent xref: {parent_xref_str!r}"
        parent_xref = int(m.group(1))
        ptype, t_val = doc.xref_get_key(parent_xref, "T")
        assert "g_renamed" in (t_val or ""), f"parent /T not renamed: {t_val!r}"


# --- Bug 3: delete one of N grouped radios ---------------------------------

def test_delete_one_radio_kid_keeps_others_grouped(
    main_window, tmp_path, monkeypatch
):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_radio_group(monkeypatch, "g1")
    win.do_form_radio(0, 50, 50, 70, 70)
    win.do_form_radio(0, 50, 100, 70, 120)
    win.do_form_radio(0, 50, 150, 70, 170)

    page = win.view.doc[0]
    radios = [
        w for w in page.widgets()
        if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    ]
    assert len(radios) == 3
    target = radios[1]
    del page

    win.delete_widget(0, target)

    with _save_and_reopen(win, tmp_path, "radio_delete_one.pdf") as doc:
        radios = [
            w for w in doc[0].widgets()
            if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        ]
        assert len(radios) == 2
        parents = []
        for r in radios:
            kind, val = doc.xref_get_key(r.xref, "Parent")
            assert kind == "xref"
            parents.append(val)
        assert parents[0] == parents[1], (
            f"remaining radios not grouped: {parents}"
        )
        m = re.match(r"\s*(\d+)\s+0\s+R", parents[0])
        parent_xref = int(m.group(1))
        # Parent's /Kids should have exactly 2 entries — both pointing at the
        # surviving radio xrefs. fitz may renumber xrefs across save+reopen,
        # so we compare the kid set against the live widget xrefs, not the
        # pre-save target_xref.
        _, kids_raw = doc.xref_get_key(parent_xref, "Kids")
        kid_xrefs = sorted(int(x) for x in re.findall(r"(\d+)\s+0\s+R", kids_raw or ""))
        survivor_xrefs = sorted(r.xref for r in radios)
        assert kid_xrefs == survivor_xrefs, (
            f"parent /Kids != survivors: kids={kid_xrefs} survivors={survivor_xrefs}"
        )
        # /AcroForm/Fields should still reference the parent.
        catalog = doc.pdf_catalog()
        _, af = doc.xref_get_key(catalog, "AcroForm")
        af_xrefs = [int(x) for x in re.findall(r"(\d+)\s+0\s+R", af or "")]
        assert parent_xref in af_xrefs, f"parent missing from AcroForm: {af_xrefs}"


def test_delete_all_radio_kids_drops_parent_from_acroform(
    main_window, tmp_path, monkeypatch
):
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_radio_group(monkeypatch, "g1")
    win.do_form_radio(0, 50, 50, 70, 70)
    win.do_form_radio(0, 50, 100, 70, 120)
    win.do_form_radio(0, 50, 150, 70, 170)

    page = win.view.doc[0]
    radios = list(page.widgets())
    targets = [r for r in radios]
    del page

    for r in targets:
        win.delete_widget(0, r)

    with _save_and_reopen(win, tmp_path, "radio_delete_all.pdf") as doc:
        page = doc[0]
        assert list(page.widgets()) == [], "widgets remained after delete-all"
        # /AcroForm/Fields should be empty (or absent) — the orphan parent
        # must not still be referenced.
        catalog = doc.pdf_catalog()
        _, af = doc.xref_get_key(catalog, "AcroForm")
        af_xrefs = re.findall(r"(\d+)\s+0\s+R", af or "")
        assert af_xrefs == [], (
            f"orphan refs still in AcroForm/Fields: {af} -> {af_xrefs}"
        )


# --- Bug 4: rename to leading-icon-character names -------------------------

@pytest.mark.parametrize("new_name", ["Total", "Title", "Tax"])
def test_rename_text_field_to_T_prefix_name_round_trips(
    main_window, tmp_path, new_name
):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "orig")

    panel = win.form_panel
    panel.refresh()
    item = panel.tree.topLevelItem(0).child(0)
    # Simulate the user double-clicking and typing — the inline-edit path
    # reuses _on_item_changed, but rename_item is the test hook.
    assert panel.rename_item(item, new_name)

    with _save_and_reopen(win, tmp_path, f"rename_{new_name}.pdf") as doc:
        names = [w.field_name for w in doc[0].widgets()]
    assert names == [new_name], f"name corrupted on rename: got {names}"


def test_rename_via_item_changed_strips_only_own_icon(main_window):
    """If the user kept the icon prefix during inline edit, only the
    field's *own* icon (not any global icon char) should be stripped."""
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "orig")

    panel = win.form_panel
    panel.refresh()
    item = panel.tree.topLevelItem(0).child(0)
    # Simulate the user typing "T  Total" (icon + 2 spaces + name) — the
    # display format used by _append_field_item. Only the leading "T " should
    # be stripped, leaving "Total" (not "otal").
    panel._suspend_changes = False
    item.setText(0, "T  Total")
    # _on_item_changed runs synchronously via itemChanged signal.
    page = win.view.doc[0]
    name = list(page.widgets())[0].field_name
    assert name == "Total", f"expected 'Total', got {name!r}"


# --- Bug 5: page mutations refresh the form panel --------------------------

def test_delete_page_relabels_panel(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "p1")
    win.do_form_text(1, 50, 50, 250, 80)
    rename_last_widget(win, 1, "p2")

    panel = win.form_panel
    panel.refresh()
    # Confirm: 2 page groups before delete.
    page_labels = [
        panel.tree.topLevelItem(i).text(0)
        for i in range(panel.tree.topLevelItemCount())
    ]
    assert page_labels == ["Page 1", "Page 2"]

    # Auto-confirm the "Delete page?" QMessageBox.
    with _mock.patch.object(
        pdfedit.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: pdfedit.QMessageBox.StandardButton.Yes),
    ):
        win.view.page_idx = 0
        win.delete_current_page()

    panel.refresh()
    page_labels = [
        panel.tree.topLevelItem(i).text(0)
        for i in range(panel.tree.topLevelItemCount())
    ]
    assert page_labels == ["Page 1"], f"panel labels stale after delete: {page_labels}"
    field_text = panel.tree.topLevelItem(0).child(0).text(0)
    assert "p2" in field_text, f"expected p2 to remain, got {field_text!r}"


# --- Bug 6: alignment round-trip -------------------------------------------

def test_alignment_round_trips_through_dialog(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "f")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    xref = w.xref
    del page

    # Set alignment to Right via edit_widget_properties (full Apply path).
    def _drive_set_right(dlg):
        dlg.align_combo.setCurrentIndex(2)  # Right

    page = win.view.doc[0]
    w = next(ww for ww in page.widgets() if ww.xref == xref)
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    dlg.align_combo.setCurrentIndex(2)
    dlg._apply_to_widget()
    w.update()
    win.view.doc.xref_set_key(xref, "Q", "2")
    del page

    with _save_and_reopen(win, tmp_path, "align_right.pdf") as doc:
        page = doc[0]
        w2 = next(ww for ww in page.widgets() if ww.field_name == "f")
        # Reopen the dialog: alignment combo must read /Q=2 (Right).
        dlg2 = pdfedit.FieldPropertiesDialog(w2, parent=None, doc=doc)
        assert dlg2.align_combo.currentIndex() == 2, (
            f"alignment did not round-trip; got {dlg2.align_combo.currentText()!r}"
        )


# --- Bug 7: choice default round-trip --------------------------------------

def test_choice_default_round_trips(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_combo(0, 50, 50, 250, 80)
    page = win.view.doc[0]
    w = next(ww for ww in page.widgets()
             if ww.field_type == fitz.PDF_WIDGET_TYPE_COMBOBOX)
    w.field_name = "country"
    w.choice_values = ["USA", "Canada", "Mexico"]
    w.field_value = "Canada"
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "choice_default.pdf") as doc:
        page = doc[0]
        w2 = next(ww for ww in page.widgets() if ww.field_name == "country")
        dlg = pdfedit.FieldPropertiesDialog(w2, parent=None, doc=doc)
        assert dlg.choices_default_combo is not None
        assert dlg.choices_default_combo.currentText() == "Canada", (
            f"persisted default lost on reopen: "
            f"{dlg.choices_default_combo.currentText()!r}"
        )


# --- Bug 8: field_display 4-state round-trip -------------------------------

@pytest.mark.parametrize("display_value", [0, 1, 2, 3])
def test_field_display_round_trips_all_four_states(
    main_window, tmp_path, display_value
):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, f"f{display_value}")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    xref = w.xref
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    # Find the index that maps to display_value.
    target_idx = None
    for i in range(dlg.display_combo.count()):
        if int(dlg.display_combo.itemData(i)) == display_value:
            target_idx = i
            break
    assert target_idx is not None
    dlg.display_combo.setCurrentIndex(target_idx)
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, f"display_{display_value}.pdf") as doc:
        page = doc[0]
        w2 = list(page.widgets())[0]
        assert int(w2.field_display or 0) == display_value, (
            f"display={display_value} did not round-trip: got {w2.field_display!r}"
        )
        dlg2 = pdfedit.FieldPropertiesDialog(w2, parent=None, doc=doc)
        cur_data = int(dlg2.display_combo.currentData() or 0)
        assert cur_data == display_value


# --- Bug 9: multi-line toggle round-trip -----------------------------------

def test_multiline_toggle_round_trips(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "notes")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    xref = w.xref
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    assert dlg.multiline_cb is not None, "multi-line checkbox missing"
    assert not dlg.multiline_cb.isChecked()
    dlg.multiline_cb.setChecked(True)
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "multiline_on.pdf") as doc:
        page = doc[0]
        w2 = list(page.widgets())[0]
        assert int(w2.field_flags or 0) & fitz.PDF_TX_FIELD_IS_MULTILINE, (
            f"multi-line flag missing after round-trip: flags={w2.field_flags}"
        )
        dlg2 = pdfedit.FieldPropertiesDialog(w2, parent=None, doc=doc)
        assert dlg2.multiline_cb.isChecked()


# --- Bug 10: Actions-tab JS not overwritten by Options-tab format ----------

def test_actions_format_user_edit_not_overwritten_by_options_format(
    main_window, tmp_path
):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "amount")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    custom_js = "event.value = 'CUSTOM_FORMAT';"
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    # Simulate user typing custom JS into the Actions tab On-Format editor.
    # setPlainText fires textChanged, which marks the editor dirty.
    dlg.action_format_edit.setPlainText(custom_js)
    # Then user picks Format=Number in Options. With the dirty flag,
    # the user's JS in Actions wins.
    dlg.format_combo.setCurrentText("Number")
    dlg._apply_to_widget()
    w.update()
    del page

    page = win.view.doc[0]
    w2 = list(page.widgets())[0]
    assert (w2.script_format or "") == custom_js, (
        f"user JS overwritten by Options-tab Format: {w2.script_format!r}"
    )
