"""Tests for the Phase 4 Form Builder side panel.

The panel reads from MainWindow.collect_all_widgets() and exposes
programmatic hooks (`refresh`, `delete_selected`, `rename_item`,
`apply_reorder`, `selected_widget`) so we don't need to drive the
QTreeWidget through real clicks.

Phase 5: do_form_* no longer prompts for a name. Tests that need
specific names call rename_last_widget() after creating the field.
"""

from __future__ import annotations

import re

import fitz
import pytest

from conftest import install_doc, make_blank_doc, rename_last_widget

import pdfedit
from PyQt6.QtCore import Qt


def _save_and_reopen(win, tmp_path, name):
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists(), f"save failed, no {name}"
    if win.view.doc is not None:
        win.view.doc.close()
        win.view.doc = None
    return fitz.open(str(out))


def _field_items(panel):
    """Flatten field rows from the panel's tree as (name, type_label) pairs."""
    out = []
    tree = panel.tree
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for j in range(top.childCount()):
            child = top.child(j)
            # Strip the icon prefix added in _append_field_item.
            text = child.text(0).strip()
            # Format is "<icon>  <name>" — split on 2-space sep.
            parts = text.split("  ", 1)
            name = parts[1] if len(parts) > 1 else parts[0]
            out.append((top.text(0), name, child.text(1)))
    return out


def test_panel_groups_fields_by_page(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "p1a")
    win.do_form_check(0, 50, 100, 80, 130)
    rename_last_widget(win, 0, "p1b")
    win.do_form_text(1, 50, 50, 250, 80)
    rename_last_widget(win, 1, "p2a")

    panel = win.form_panel
    panel.refresh()

    rows = _field_items(panel)
    assert len(rows) == 3
    pages = [r[0] for r in rows]
    names = [r[1] for r in rows]
    types = [r[2] for r in rows]
    assert pages == ["Page 1", "Page 1", "Page 2"]
    assert names == ["p1a", "p1b", "p2a"]
    assert types == ["Text", "Checkbox", "Text"]


def test_panel_auto_refreshes_on_add(main_window):
    win = main_window
    install_doc(win, make_blank_doc())
    panel = win.form_panel
    panel.refresh()
    assert _field_items(panel) == []

    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "first")

    panel.refresh()
    rows = _field_items(panel)
    assert len(rows) == 1
    assert rows[0][1] == "first"


def test_panel_delete_selected_removes_field(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "to_keep")
    win.do_form_text(0, 50, 100, 250, 130)
    rename_last_widget(win, 0, "to_drop")

    panel = win.form_panel
    panel.refresh()
    # Select the second (to_drop) row.
    top = panel.tree.topLevelItem(0)
    target = None
    for j in range(top.childCount()):
        child = top.child(j)
        if "to_drop" in child.text(0):
            target = child
            break
    assert target is not None
    panel.tree.setCurrentItem(target)

    assert panel.delete_selected()
    rows = _field_items(panel)
    assert [r[1] for r in rows] == ["to_keep"]

    with _save_and_reopen(win, tmp_path, "deleted.pdf") as doc:
        names = [w.field_name for w in doc[0].widgets()]
    assert names == ["to_keep"]


def test_panel_inline_rename_round_trip(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "old_name")

    panel = win.form_panel
    panel.refresh()
    top = panel.tree.topLevelItem(0)
    item = top.child(0)
    assert panel.rename_item(item, "renamed")

    with _save_and_reopen(win, tmp_path, "renamed.pdf") as doc:
        names = [w.field_name for w in doc[0].widgets()]
    assert names == ["renamed"]


def test_panel_apply_reorder_round_trip(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "A")
    win.do_form_text(0, 50, 100, 250, 130)
    rename_last_widget(win, 0, "B")
    win.do_form_text(0, 50, 150, 250, 180)
    rename_last_widget(win, 0, "C")

    panel = win.form_panel
    panel.refresh()

    # Build (page_idx, xref) lookup keyed by name from the panel's current order.
    by_name: dict[str, tuple[int, int]] = {}
    for pi, xr in panel.current_order():
        for w in win.view.doc[pi].widgets():
            if w.xref == xr:
                by_name[w.field_name] = (pi, xr)
                break
    desired = [by_name["C"], by_name["A"], by_name["B"]]
    panel.apply_reorder(desired)

    with _save_and_reopen(win, tmp_path, "reordered.pdf") as doc:
        names = [w.field_name for w in doc[0].widgets()]
    assert names == ["C", "A", "B"]


def test_panel_visibility_persists_via_qsettings(main_window, monkeypatch):
    win = main_window

    # Fake QSettings backed by a dict so the test doesn't touch real prefs.
    store: dict[str, object] = {}

    class FakeQSettings:
        def __init__(self, *a, **kw):
            pass

        def value(self, key, default=None):
            return store.get(key, default)

        def setValue(self, key, value):
            store[key] = value

    monkeypatch.setattr(pdfedit, "QSettings", FakeQSettings)

    # Hide the panel — should write False to QSettings via the visibilityChanged hook.
    win.form_panel.setVisible(True)
    win.form_panel.setVisible(False)
    assert store.get("formBuilderPanelVisible") is False

    # New MainWindow should restore the hidden state.
    win2 = pdfedit.MainWindow()
    try:
        assert win2.form_panel.isVisible() is False
    finally:
        win2.close()
        win2.deleteLater()


def test_panel_empty_state_message(main_window):
    win = main_window
    install_doc(win, make_blank_doc())
    panel = win.form_panel
    panel.refresh()
    # With no fields, the empty-state label should be the active stack widget.
    assert panel.stack.currentWidget() is panel.empty_label
    assert "No form fields" in panel.empty_label.text()


def test_panel_selected_widget_returns_pair(main_window):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_form_text(0, 50, 50, 250, 80)
    rename_last_widget(win, 0, "only")

    panel = win.form_panel
    panel.refresh()
    item = panel.tree.topLevelItem(0).child(0)
    panel.tree.setCurrentItem(item)

    sel = panel.selected_widget()
    assert sel is not None
    pi, w = sel
    assert pi == 0
    assert w.field_name == "only"


def test_panel_focus_widget_centers_view(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    win.do_form_text(1, 50, 400, 250, 430)
    rename_last_widget(win, 1, "p2_field")

    panel = win.form_panel
    panel.refresh()
    # Find the page-2 child item and select it.
    top2 = panel.tree.topLevelItem(0)
    # Two top-level items (one per page that has fields). Page 2 only.
    assert top2.text(0) == "Page 2"
    item = top2.child(0)
    panel.tree.setCurrentItem(item)

    # The window should have switched to page 2.
    assert win.view.page_idx == 1
    # And the highlight item should exist + be visible.
    hl = getattr(win, "_widget_highlight_item", None)
    assert hl is not None
    assert hl.isVisible()
