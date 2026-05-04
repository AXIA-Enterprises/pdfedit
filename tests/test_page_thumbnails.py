"""Tests for the Feature A Page Thumbnails side panel.

The panel renders one item per page in the active doc. Tests drive it
through its programmatic API (refresh, select_page, commit_reorder,
rotate_page, delete_page, extract_page) rather than real mouse drags.
"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QPixmap

from conftest import install_doc, make_blank_doc

import pdfedit


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    ini_path = tmp_path / "settings.ini"

    class _FakeQSettings:
        _store = QSettings(str(ini_path), QSettings.Format.IniFormat)

        def __init__(self, *args, **kwargs):
            self._s = _FakeQSettings._store

        def value(self, key, default=None):
            return self._s.value(key, default)

        def setValue(self, key, val):
            self._s.setValue(key, val)
            self._s.sync()

        def sync(self):
            self._s.sync()

        def clear(self):
            self._s.clear()
            self._s.sync()

        def remove(self, key):
            self._s.remove(key)
            self._s.sync()

    monkeypatch.setattr(pdfedit, "QSettings", _FakeQSettings)
    yield _FakeQSettings._store


def _save_and_reopen(win, tmp_path, name):
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists(), f"save failed, no {name}"
    if win.view.doc is not None:
        win.view.doc.close()
        win.view.doc = None
    return fitz.open(str(out))


def _label_each_page(doc):
    """Insert a unique text label on each page so we can identify them post-reorder."""
    for i in range(len(doc)):
        page = doc[i]
        page.insert_text((72, 72), f"PAGE-{i}", fontsize=18)


def test_panel_renders_three_items_for_three_page_doc(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    assert panel.list_widget.count() == 3
    for i in range(3):
        item = panel.list_widget.item(i)
        assert item is not None
        assert f"Page {i + 1}" in item.text()
        assert item.data(panel.PAGE_ROLE) == i
        assert not item.icon().isNull()
    assert "3" in panel.status_label.text()


def test_click_thumbnail_scrolls_main_view(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=4))
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    panel._on_item_clicked(panel.list_widget.item(2))
    assert win.view.page_idx == 2

    panel._on_item_clicked(panel.list_widget.item(0))
    assert win.view.page_idx == 0


def test_drag_drop_reorder_persists_through_save(main_window, tmp_path):
    win = main_window
    doc = make_blank_doc(pages=3)
    _label_each_page(doc)
    install_doc(win, doc)
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    panel.commit_reorder([2, 0, 1])
    assert panel.list_widget.count() == 3
    assert win.view.doc.page_count == 3
    with _save_and_reopen(win, tmp_path, "reordered.pdf") as reopened:
        assert reopened.page_count == 3
        texts = [reopened[i].get_text("text").strip() for i in range(3)]
    assert texts == ["PAGE-2", "PAGE-0", "PAGE-1"]


def test_insert_blank_page_rebuilds_panel_with_n_plus_one_items(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()
    assert panel.list_widget.count() == 2

    panel.insert_blank_page(0, after=True)
    assert panel.list_widget.count() == 3
    assert win.view.doc.page_count == 3


def test_delete_page_rebuilds_panel_with_n_minus_one_items(main_window, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    panel.delete_page(1)
    assert panel.list_widget.count() == 2
    assert win.view.doc.page_count == 2


def test_rotate_page_rerenders_thumbnail(main_window):
    win = main_window
    doc = make_blank_doc(pages=2)
    _label_each_page(doc)
    install_doc(win, doc)
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    item_before = panel.list_widget.item(0)
    pm_before = item_before.icon().pixmap(150, 200)
    hash_before = pm_before.toImage().bits().asstring(pm_before.width() * pm_before.height() * 4)

    panel.rotate_page(0, 90)

    item_after = panel.list_widget.item(0)
    pm_after = item_after.icon().pixmap(150, 200)
    hash_after = pm_after.toImage().bits().asstring(pm_after.width() * pm_after.height() * 4)
    assert hash_before != hash_after
    assert win.view.doc[0].rotation == 90


def test_visibility_toggle_persists_via_qsettings(qapp, qtbot, isolated_settings):
    win = pdfedit.MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    panel = win.thumbs_panel
    panel.setVisible(False)
    val_off = isolated_settings.value(pdfedit.PAGE_THUMBNAILS_PANEL_VISIBLE_KEY)
    assert val_off in (False, "false", 0, "0")
    panel.setVisible(True)
    val_on = isolated_settings.value(pdfedit.PAGE_THUMBNAILS_PANEL_VISIBLE_KEY)
    assert val_on in (True, "true", 1, "1")
    win.dirty = False
    win.closeEvent = lambda ev: ev.accept()
    win.close()


def test_empty_state_when_no_doc(main_window):
    win = main_window
    panel = win.thumbs_panel
    panel.refresh()
    assert panel.list_widget.count() == 0
    assert "0" in panel.status_label.text()
    assert panel.stack.currentWidget() is panel.empty_label


def test_theme_change_re_renders_with_new_accent(main_window, isolated_settings):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    saved_light = dict(pdfedit.LIGHT_PALETTE)
    saved_dark = dict(pdfedit.DARK_PALETTE)
    saved_active = pdfedit._active_palette
    try:
        pdfedit.LIGHT_PALETTE["accent"] = "#FF0000"
        pdfedit.DARK_PALETTE["accent"] = "#FF0000"
        pdfedit._active_palette = pdfedit.LIGHT_PALETTE

        panel.select_page(0)
        panel._update_current_highlight()
        item = panel.list_widget.item(0)
        bg = item.background().color()
        assert bg.red() == 255 and bg.green() == 0 and bg.blue() == 0
    finally:
        pdfedit.LIGHT_PALETTE.clear()
        pdfedit.LIGHT_PALETTE.update(saved_light)
        pdfedit.DARK_PALETTE.clear()
        pdfedit.DARK_PALETTE.update(saved_dark)
        pdfedit._active_palette = saved_active


def test_context_menu_rotate_right_rotates_page(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=1))
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()
    assert win.view.doc[0].rotation == 0

    panel.rotate_page(0, 90)
    assert win.view.doc[0].rotation == 90


def test_select_page_and_current_page_index(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=4))
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    panel.select_page(2)
    assert panel.current_page_index() == 2


def test_extract_page_writes_single_page_pdf(main_window, tmp_path, monkeypatch):
    win = main_window
    doc = make_blank_doc(pages=3)
    _label_each_page(doc)
    install_doc(win, doc)
    panel = win.thumbs_panel
    panel.setVisible(True)
    panel.refresh()

    out = tmp_path / "extracted.pdf"
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "PDF Files (*.pdf)")))

    panel.extract_page(1)
    assert out.exists()
    with fitz.open(str(out)) as ext:
        assert ext.page_count == 1
        assert "PAGE-1" in ext[0].get_text("text")
