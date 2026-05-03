"""Phase 3 tests: radio grouping, calc/format JS, tab order, actions tab.

Phase 5: do_form_* (except radio) no longer prompts for a name. Tests
that need specific names call rename_last_widget() after each create.
do_form_radio still prompts for the group name.
"""

from __future__ import annotations

import re

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


def _patch_text(monkeypatch, *values):
    it = iter(values)

    def fake(*a, **kw):
        try:
            return (next(it), True)
        except StopIteration:
            return ("", False)

    monkeypatch.setattr(pdfedit.QInputDialog, "getText", staticmethod(fake))


# ---- Radio grouping ---------------------------------------------------------

def test_radio_group_links_under_parent(main_window, tmp_path, monkeypatch):
    """Two radios sharing field_name=g1 with distinct on-state captions
    should end up under one parent field after Phase 3 linkage."""
    win = main_window
    install_doc(win, make_blank_doc())
    # Two radios in the same group. Phase 5 auto-picks unique export values
    # (Option_1, Option_2, ...) so we only need to supply group names.
    _patch_text(monkeypatch, "g1", "g1")
    win.do_form_radio(0, 50, 50, 70, 70)
    win.do_form_radio(0, 50, 100, 70, 120)

    with _save_and_reopen(win, tmp_path, "radios.pdf") as doc:
        page = doc[0]
        ws = list(page.widgets())
        assert len(ws) == 2
        # Both radios share the inherited group name.
        names = [w.field_name for w in ws]
        assert names == ["g1", "g1"]
        # Both kids must point at the same parent xref.
        parents = []
        for w in ws:
            kind, val = doc.xref_get_key(w.xref, "Parent")
            assert kind == "xref", f"expected /Parent xref ref, got {kind}: {val}"
            parents.append(val)
        assert parents[0] == parents[1], (
            f"radios not linked under same parent: {parents}"
        )


def test_radio_group_auto_unique_export(main_window, tmp_path, monkeypatch):
    """Phase 5 auto-generates unique export values within a group."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_text(monkeypatch, "g2", "g2")
    win.do_form_radio(0, 50, 50, 70, 70)
    win.do_form_radio(0, 50, 100, 70, 120)

    with _save_and_reopen(win, tmp_path, "radio_auto.pdf") as doc:
        ws = list(doc[0].widgets())
        on_states = []
        for w in ws:
            ap = doc.xref_get_key(w.xref, "AP")[1]
            states = [m for m in re.findall(r"/([A-Za-z0-9_]+)\s", ap) if m not in ("N", "D", "R", "Off")]
            on_states.extend(states)
        # Both radios should have distinct on-state names.
        assert len(set(on_states)) == 2, f"expected distinct on-states, got {on_states}"


# ---- Calculation script -----------------------------------------------------

def test_sum_calculation_script(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "a")
    win.do_form_text(0, 50, 100, 250, 130)
    rename_last_widget(win, 0, "b")
    win.do_form_text(0, 50, 150, 250, 180)
    rename_last_widget(win, 0, "total")

    page = win.view.doc[0]
    total = next(w for w in page.widgets() if w.field_name == "total")
    dlg = pdfedit.FieldPropertiesDialog(total, parent=None, doc=win.view.doc)
    # Set Calc=Sum, select sources [a, b].
    dlg.calc_op_combo.setCurrentIndex(pdfedit._CALC_OPS.index("Sum"))
    for i in range(dlg.calc_sources_list.count()):
        item = dlg.calc_sources_list.item(i)
        if item.text() in ("a", "b"):
            item.setSelected(True)
    dlg._apply_to_widget()
    total.update()
    del page

    with _save_and_reopen(win, tmp_path, "calc.pdf") as doc:
        ww = next(w for w in doc[0].widgets() if w.field_name == "total")
        s = ww.script_calc or ""
    assert 'getField("a")' in s, f"missing a-ref: {s!r}"
    assert 'getField("b")' in s, f"missing b-ref: {s!r}"
    assert "event.value =" in s, f"missing event.value=: {s!r}"


def test_calc_script_round_trip_repopulates_dialog(main_window):
    """Reopening properties on a field with an existing calc should
    pre-select the sources and the operation."""
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "x")
    win.do_form_text(0, 50, 100, 250, 130)
    rename_last_widget(win, 0, "y")
    win.do_form_text(0, 50, 150, 250, 180)
    rename_last_widget(win, 0, "result")

    page = win.view.doc[0]
    result = next(w for w in page.widgets() if w.field_name == "result")
    # Pre-seed the script as if Phase 3 had already been applied.
    result.script_calc = pdfedit._build_calc_script("Sum", ["x", "y"])
    result.update()

    dlg = pdfedit.FieldPropertiesDialog(result, parent=None, doc=win.view.doc)
    assert dlg.calc_op_combo.currentText() == "Sum"
    selected = [
        dlg.calc_sources_list.item(i).text()
        for i in range(dlg.calc_sources_list.count())
        if dlg.calc_sources_list.item(i).isSelected()
    ]
    assert set(selected) == {"x", "y"}, f"got {selected}"


# ---- Format scripts ---------------------------------------------------------

def test_number_format_script(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "amount")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    dlg.format_combo.setCurrentText("Number")
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "fmt_num.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    assert "AFNumber_Format" in (ww.script_format or "")
    assert "AFNumber_Keystroke" in (ww.script_change or "")


def test_date_format_script(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "dob")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    dlg.format_combo.setCurrentText("Date")
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "fmt_date.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    assert "AFDate_FormatEx" in (ww.script_format or "")
    assert "AFDate_KeystrokeEx" in (ww.script_change or "")


def test_format_none_clears_script(main_window, tmp_path):
    """Switching back to Format=None should clear any prior format script."""
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "fld")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    # First apply Number format.
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    dlg.format_combo.setCurrentText("Number")
    dlg._apply_to_widget()
    w.update()
    # Then re-open and switch to None.
    dlg2 = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    dlg2.format_combo.setCurrentText("None")
    dlg2._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "fmt_none.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    assert (ww.script_format or "") == ""
    assert (ww.script_change or "") == ""


# ---- Actions tab editors ---------------------------------------------------

def test_actions_tab_round_trip(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "act")

    page = win.view.doc[0]
    w = list(page.widgets())[0]
    dlg = pdfedit.FieldPropertiesDialog(w, parent=None, doc=win.view.doc)
    dlg.action_focus_edit.setPlainText("console.println('focus');")
    dlg.action_blur_edit.setPlainText("console.println('blur');")
    dlg.action_mouseup_edit.setPlainText("app.alert('hi');")
    dlg._apply_to_widget()
    w.update()
    del page

    with _save_and_reopen(win, tmp_path, "actions.pdf") as doc:
        ww = list(doc[0].widgets())[0]
    assert "focus" in (ww.script_focus or "")
    assert "blur" in (ww.script_blur or "")
    assert "app.alert" in (ww.script or "")


# ---- Tab order --------------------------------------------------------------

def test_tab_order_reorder_round_trip(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "A")
    win.do_form_text(0, 50, 100, 250, 130)
    rename_last_widget(win, 0, "B")
    win.do_form_text(0, 50, 150, 250, 180)
    rename_last_widget(win, 0, "C")

    dlg = pdfedit.TabOrderDialog(win.view.doc, parent=None)
    # Build the desired order by xref, picking each by current name.
    by_name: dict[str, tuple[int, int]] = {}
    for i in range(dlg.list_widget.count()):
        item = dlg.list_widget.item(i)
        pi, xr = item.data(pdfedit.Qt.ItemDataRole.UserRole)
        # Look up name from doc.
        for w in win.view.doc[pi].widgets():
            if w.xref == xr:
                by_name[w.field_name] = (pi, xr)
                break
    desired = [by_name["C"], by_name["A"], by_name["B"]]
    dlg.reorder_to(desired)
    dlg.apply_to_doc()

    with _save_and_reopen(win, tmp_path, "tabs.pdf") as doc:
        names = [w.field_name for w in doc[0].widgets()]
    assert names == ["C", "A", "B"], f"tab order didn't survive: {names}"


def test_collect_all_widgets(main_window):
    """Phase 4 read-API smoke: collect_all_widgets returns (page_idx, widget)."""
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "p1a")
    # Second field on page 2.
    win.do_form_text(1, 50, 50, 250, 80)
    rename_last_widget(win, 1, "p2a")

    pairs = win.collect_all_widgets()
    assert len(pairs) == 2
    pages = [p for (p, _) in pairs]
    names = [w.field_name for (_, w) in pairs]
    assert pages == [0, 1]
    assert names == ["p1a", "p2a"]
