"""Tests for Feature H: multi-document tabs."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QTabWidget

from conftest import install_doc, make_blank_doc

import pdfedit


def _write_pdf(tmp_path: Path, name: str, text: str = "hello") -> Path:
    """Write a simple single-page PDF and return its path."""
    p = tmp_path / name
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), text)
    doc.save(str(p))
    doc.close()
    return p


# ---------------------------------------------------------------------------
# 1. Two PDFs in two tabs have separate doc instances
# ---------------------------------------------------------------------------
def test_two_pdfs_in_two_tabs_separate_docs(main_window, qtbot, tmp_path):
    win = main_window
    a = _write_pdf(tmp_path, "a.pdf", "alpha")
    b = _write_pdf(tmp_path, "b.pdf", "beta")

    win.open_path(str(a))
    win.open_path(str(b))

    tabs = win.all_tabs()
    assert len(tabs) == 2
    assert tabs[0].view.doc is not None
    assert tabs[1].view.doc is not None
    assert tabs[0].view.doc is not tabs[1].view.doc
    assert tabs[0].path == str(a)
    assert tabs[1].path == str(b)


# ---------------------------------------------------------------------------
# 2. Cmd+T (new_tab) creates a new blank tab
# ---------------------------------------------------------------------------
def test_new_tab_increments_count(main_window, qtbot):
    win = main_window
    before = win.tabs.count()
    new_tab = win.new_tab()
    assert win.tabs.count() == before + 1
    assert new_tab is win.current_tab
    assert new_tab.view.doc is None
    assert new_tab.path is None
    assert not new_tab.dirty


# ---------------------------------------------------------------------------
# 3. close_current_tab decrements count; dirty triggers prompt
# ---------------------------------------------------------------------------
def test_close_clean_tab_decrements_count(main_window, qtbot):
    win = main_window
    win.new_tab()
    win.new_tab()
    assert win.tabs.count() == 3

    win.close_current_tab()
    assert win.tabs.count() == 2


def test_close_dirty_tab_prompts(main_window, qtbot, monkeypatch):
    win = main_window
    tab = win.new_tab()
    install_doc(win, make_blank_doc())
    tab.dirty = True

    asked = {"count": 0}

    def fake_question(*a, **kw):
        asked["count"] += 1
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(pdfedit.QMessageBox, "question", staticmethod(fake_question))
    win.close_current_tab()
    assert asked["count"] == 1


def test_close_dirty_tab_cancel_keeps_tab(main_window, qtbot, monkeypatch):
    win = main_window
    tab = win.new_tab()
    install_doc(win, make_blank_doc())
    tab.dirty = True
    before = win.tabs.count()

    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Cancel),
    )
    win.close_current_tab()
    assert win.tabs.count() == before


# ---------------------------------------------------------------------------
# 4. Switch tabs: form panel + thumbs panel re-point at active tab
# ---------------------------------------------------------------------------
def test_panels_repoint_on_tab_switch(main_window, qtbot, tmp_path):
    win = main_window
    a = _write_pdf(tmp_path, "a.pdf", "alpha")
    b = _write_pdf(tmp_path, "b.pdf", "beta")
    win.open_path(str(a))
    win.open_path(str(b))

    tabs = win.all_tabs()
    win.tabs.setCurrentWidget(tabs[0])
    qtbot.wait(20)
    assert win.view.doc is tabs[0].view.doc
    assert win.form_panel.window_.view.doc is tabs[0].view.doc
    assert win.thumbs_panel.window_.view.doc is tabs[0].view.doc

    win.tabs.setCurrentWidget(tabs[1])
    qtbot.wait(20)
    assert win.view.doc is tabs[1].view.doc
    assert win.form_panel.window_.view.doc is tabs[1].view.doc
    assert win.thumbs_panel.window_.view.doc is tabs[1].view.doc


# ---------------------------------------------------------------------------
# 5. Per-tab undo: undo on B doesn't affect A
# ---------------------------------------------------------------------------
def test_per_tab_undo_isolated(main_window, qtbot):
    win = main_window
    # Tab A
    install_doc(win, make_blank_doc())
    tab_a = win.current_tab
    win._snapshot()
    # Mutate A: insert a textbox-equivalent — just push something distinct
    # onto A's undo stack so the stacks have different lengths.
    win._snapshot()
    a_undo_len = len(tab_a._undo)

    # New tab B
    tab_b = win.new_tab()
    install_doc(win, make_blank_doc())
    win._snapshot()
    b_undo_len_before = len(tab_b._undo)
    assert b_undo_len_before == 1

    # Undo on B
    win.undo()
    assert len(tab_b._undo) == 0
    # A's undo stack untouched
    assert len(tab_a._undo) == a_undo_len


# ---------------------------------------------------------------------------
# 6. Per-tab dirty: editing B then saving B doesn't clean A
# ---------------------------------------------------------------------------
def test_per_tab_dirty_isolated(main_window, qtbot, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    tab_a = win.current_tab
    tab_a.dirty = True

    tab_b = win.new_tab()
    install_doc(win, make_blank_doc())
    tab_b.dirty = True

    save_path = tmp_path / "b.pdf"
    monkeypatch.setattr(
        pdfedit.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **kw: (str(save_path), "")),
    )
    win.save_pdf_as()

    assert tab_b.dirty is False
    assert tab_a.dirty is True


# ---------------------------------------------------------------------------
# 7. Tab bar shows filename + dirty marker
# ---------------------------------------------------------------------------
def test_tab_bar_shows_filename_and_dirty_marker(main_window, qtbot, tmp_path):
    win = main_window
    a = _write_pdf(tmp_path, "doc1.pdf")
    win.open_path(str(a))
    idx = win.tabs.indexOf(win.current_tab)
    assert win.tabs.tabText(idx) == "doc1.pdf"

    win.current_tab.dirty = True
    win._refresh_title()
    assert "•" in win.tabs.tabText(idx)


# ---------------------------------------------------------------------------
# 8. App close: dirty tabs prompted in order; cancel aborts
# ---------------------------------------------------------------------------
class _FakeEvent:
    def __init__(self):
        self.accepted = None

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_close_event_cancel_aborts(main_window, qtbot, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    win.current_tab.dirty = True
    win.new_tab()
    install_doc(win, make_blank_doc())
    win.current_tab.dirty = True

    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Cancel),
    )

    ev = _FakeEvent()
    # Invoke the production helper directly — conftest stubs closeEvent.
    win._prompt_close_dirty_tabs(ev)
    assert ev.accepted is False


def test_close_event_discards_all_when_chosen(main_window, qtbot, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    win.current_tab.dirty = True
    win.new_tab()
    install_doc(win, make_blank_doc())
    win.current_tab.dirty = True

    asked = {"count": 0}

    def fake_q(*a, **kw):
        asked["count"] += 1
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(pdfedit.QMessageBox, "question", staticmethod(fake_q))

    ev = _FakeEvent()
    win._prompt_close_dirty_tabs(ev)
    assert ev.accepted is True
    assert asked["count"] == 2


# ---------------------------------------------------------------------------
# 9. Cmd+O opens into a new tab (doesn't replace current)
# ---------------------------------------------------------------------------
def test_open_path_opens_in_new_tab_when_current_has_doc(main_window, qtbot, tmp_path):
    win = main_window
    a = _write_pdf(tmp_path, "a.pdf")
    b = _write_pdf(tmp_path, "b.pdf")

    win.open_path(str(a))
    assert win.tabs.count() == 1
    assert win.current_tab.path == str(a)

    win.open_path(str(b))
    assert win.tabs.count() == 2
    assert win.current_tab.path == str(b)
    # Original tab still has tab a.
    tabs = win.all_tabs()
    paths = sorted(t.path for t in tabs if t.path)
    assert paths == sorted([str(a), str(b)])


# ---------------------------------------------------------------------------
# 10. Drag-to-reorder: tabs can be reordered programmatically
# ---------------------------------------------------------------------------
def test_tab_bar_movable(main_window, qtbot):
    win = main_window
    assert win.tabs.isMovable() is True


def test_tabs_reorder_swaps(main_window, qtbot, tmp_path):
    win = main_window
    a = _write_pdf(tmp_path, "a.pdf")
    b = _write_pdf(tmp_path, "b.pdf")
    win.open_path(str(a))
    win.open_path(str(b))

    tabs_before = win.all_tabs()
    win.tabs.tabBar().moveTab(0, 1)
    tabs_after = win.all_tabs()
    assert tabs_after[0] is tabs_before[1]
    assert tabs_after[1] is tabs_before[0]


# ---------------------------------------------------------------------------
# 11. Per-tab search state isolated
# ---------------------------------------------------------------------------
def test_per_tab_search_state_isolated(main_window, qtbot):
    win = main_window
    install_doc(win, make_blank_doc())
    tab_a = win.current_tab
    tab_a._search_results = [(0, fitz.Rect(0, 0, 10, 10))]
    tab_a._search_idx = 0

    tab_b = win.new_tab()
    install_doc(win, make_blank_doc())
    assert tab_b._search_results == []
    assert tab_b._search_idx == -1

    # win delegates to active tab.
    assert win._search_idx == -1
    win.tabs.setCurrentWidget(tab_a)
    assert win._search_idx == 0
    assert len(win._search_results) == 1


# ---------------------------------------------------------------------------
# 12. Empty state: closing the only tab spawns a fresh blank tab
# ---------------------------------------------------------------------------
def test_closing_last_tab_creates_fresh_blank(main_window, qtbot):
    win = main_window
    assert win.tabs.count() == 1
    # Force-close the only tab (clean, no prompt).
    win.close_current_tab()
    assert win.tabs.count() == 1
    assert win.current_tab.view.doc is None
    assert win.current_tab.path is None


# ---------------------------------------------------------------------------
# 13. Tab close shortcut Cmd+W is wired
# ---------------------------------------------------------------------------
def test_close_tab_shortcut_registered(main_window):
    win = main_window
    assert win.act_close_tab.shortcut().toString() in ("Ctrl+W", "Meta+W")
    assert win.act_new_tab.shortcut().toString() in ("Ctrl+T", "Meta+T")


# ---------------------------------------------------------------------------
# 14. window title reflects active tab
# ---------------------------------------------------------------------------
def test_window_title_reflects_active_tab(main_window, qtbot, tmp_path):
    win = main_window
    a = _write_pdf(tmp_path, "alpha.pdf")
    b = _write_pdf(tmp_path, "beta.pdf")
    win.open_path(str(a))
    win.open_path(str(b))
    win._refresh_title()
    assert "beta.pdf" in win.windowTitle()

    tabs = win.all_tabs()
    win.tabs.setCurrentWidget(tabs[0])
    qtbot.wait(20)
    assert "alpha.pdf" in win.windowTitle()
