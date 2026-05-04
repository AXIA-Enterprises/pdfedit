"""Tests for the theme/settings/shortcut polish pass.

Covers nine bugs surfaced by audits 5/7/8:

1. accent swatch reads the active palette, not LIGHT_PALETTE
2. system theme follows OS color-scheme toggles at runtime
3. widget-highlight ring picks up a fresh accent after theme changes
4. SettingsDialog.reset_appearance snaps the theme combo back to "System"
5. font slider applies _apply_font_size only on release, not on every tick
6. tool-mode single-key shortcuts skip QPlainTextEdit / QTextEdit focus
7. an &Edit -> Format submenu (or top-level) exposes Bold/Italic/etc.
8. the "Select" tool no longer appears in the &Insert menu
9. Ctrl+Shift+= is a registered alternate Zoom-In shortcut
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
)

import pdfedit


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    ini_path = tmp_path / "polish.ini"

    class _FakeQSettings:
        _store = QSettings(str(ini_path), QSettings.Format.IniFormat)

        def __init__(self, *a, **kw):
            self._s = _FakeQSettings._store

        def value(self, key, default=None):
            return self._s.value(key, default)

        def setValue(self, key, val):
            self._s.setValue(key, val)
            self._s.sync()

        def remove(self, key):
            self._s.remove(key)
            self._s.sync()

        def clear(self):
            self._s.clear()
            self._s.sync()

        def sync(self):
            self._s.sync()

    monkeypatch.setattr(pdfedit, "QSettings", _FakeQSettings)
    yield _FakeQSettings._store


@pytest.fixture(autouse=True)
def _restore_appearance():
    light = dict(pdfedit.LIGHT_PALETTE)
    dark = dict(pdfedit.DARK_PALETTE)
    font_pt = pdfedit.UI_FONT_PT
    yield
    pdfedit.LIGHT_PALETTE.clear()
    pdfedit.LIGHT_PALETTE.update(light)
    pdfedit.DARK_PALETTE.clear()
    pdfedit.DARK_PALETTE.update(dark)
    pdfedit.UI_FONT_PT = font_pt
    # Reapply default theme so other tests start clean.
    app = QApplication.instance()
    if app is not None:
        pdfedit.apply_theme(app, "light")


# ---------------------------------------------------------------------------
# Bug 1 — accent swatch reads the active palette
# ---------------------------------------------------------------------------

def test_accent_swatch_reads_active_palette_in_dark_mode(
    qapp, main_window, isolated_settings
):
    pdfedit.apply_theme(qapp, "dark")
    pdfedit.LIGHT_PALETTE["accent"] = "#111111"
    pdfedit.DARK_PALETTE["accent"] = "#FF0000"
    pdfedit._active_palette = pdfedit.DARK_PALETTE

    dlg = pdfedit.SettingsDialog(main_window)
    dlg._refresh_accent_swatch()
    text = dlg.accent_btn.text()
    assert text.upper() == "#FF0000", (
        f"swatch should read DARK accent in dark mode, got {text!r}"
    )
    style = dlg.accent_btn.styleSheet().lower()
    assert "#ff0000" in style


# ---------------------------------------------------------------------------
# Bug 2 — system theme follows OS color-scheme toggles
# ---------------------------------------------------------------------------

def test_pdfapp_responds_to_color_scheme_signal(
    qapp, monkeypatch, isolated_settings
):
    # Persist 'system' as the user's choice.
    pdfedit.set_theme(qapp, "system")
    assert pdfedit.current_theme_name() == "system"

    calls = {"n": 0}
    real_apply = pdfedit.apply_theme

    def _spy(app, name):
        calls["n"] += 1
        return real_apply(app, name)

    monkeypatch.setattr(pdfedit, "apply_theme", _spy)
    qapp._on_os_color_scheme_changed()
    assert calls["n"] >= 1


def test_pdfapp_ignores_color_scheme_when_user_picked_explicit(
    qapp, monkeypatch, isolated_settings
):
    pdfedit.set_theme(qapp, "light")
    assert pdfedit.current_theme_name() == "light"
    calls = {"n": 0}
    real_apply = pdfedit.apply_theme

    def _spy(app, name):
        calls["n"] += 1
        return real_apply(app, name)

    monkeypatch.setattr(pdfedit, "apply_theme", _spy)
    qapp._on_os_color_scheme_changed()
    assert calls["n"] == 0, (
        "explicit light/dark choice should not be overridden by an OS toggle"
    )


# ---------------------------------------------------------------------------
# Bug 3 — highlight ring refreshes pen/brush each call
# ---------------------------------------------------------------------------

def test_widget_highlight_pen_reflects_new_accent(
    qapp, main_window, isolated_settings
):
    from conftest import install_doc, make_blank_doc
    install_doc(main_window, make_blank_doc())

    pdfedit.LIGHT_PALETTE["accent"] = "#0000FF"
    pdfedit.DARK_PALETTE["accent"] = "#0000FF"
    pdfedit._active_palette = pdfedit.LIGHT_PALETTE
    main_window._show_widget_highlight(main_window.view.scene_.sceneRect())
    item = main_window._widget_highlight_item
    pen_color_before = item.pen().color().name().lower()
    assert pen_color_before == "#0000ff"

    pdfedit.LIGHT_PALETTE["accent"] = "#FF8800"
    pdfedit.DARK_PALETTE["accent"] = "#FF8800"
    pdfedit._active_palette = pdfedit.LIGHT_PALETTE
    main_window._show_widget_highlight(main_window.view.scene_.sceneRect())
    pen_color_after = main_window._widget_highlight_item.pen().color().name().lower()
    assert pen_color_after == "#ff8800", (
        f"expected refreshed accent #ff8800, got {pen_color_after!r}"
    )


# ---------------------------------------------------------------------------
# Bug 4 — reset_appearance snaps theme combo to "System"
# ---------------------------------------------------------------------------

def test_reset_appearance_snaps_theme_combo(
    qapp, main_window, isolated_settings
):
    dlg = pdfedit.SettingsDialog(main_window)
    # Force the combo to a non-system value first.
    for i in range(dlg.theme_combo.count()):
        if dlg.theme_combo.itemData(i) == "dark":
            dlg.theme_combo.setCurrentIndex(i)
            break
    assert dlg.theme_combo.currentData() == "dark"
    dlg.reset_appearance()
    assert dlg.theme_combo.currentData() == "system"


# ---------------------------------------------------------------------------
# Bug 7 — font slider applies only on release for mid-drag ticks
# ---------------------------------------------------------------------------

def test_font_slider_drag_does_not_apply_until_release(
    qapp, main_window, isolated_settings, monkeypatch
):
    dlg = pdfedit.SettingsDialog(main_window)
    calls = {"n": 0, "last": None}

    def _spy(value):
        calls["n"] += 1
        calls["last"] = value

    monkeypatch.setattr(dlg, "_apply_font_size", _spy)

    # Simulate mid-drag by holding the slider down. setSliderDown(False)
    # auto-emits sliderReleased, so the order of operations matters: first
    # press, then change values mid-drag, then release once.
    dlg.font_slider.setSliderDown(True)
    dlg.font_slider.setValue(15)
    dlg.font_slider.setValue(16)
    assert calls["n"] == 0, (
        "mid-drag valueChanged should NOT call _apply_font_size, "
        f"got {calls['n']} call(s)"
    )
    dlg.font_slider.setSliderDown(False)  # emits sliderReleased once
    assert calls["n"] == 1
    assert calls["last"] == 16


def test_font_slider_setvalue_outside_drag_still_applies(
    qapp, main_window, isolated_settings, monkeypatch
):
    """Programmatic setValue / arrow keys should apply immediately so
    test_font_size_slider_rebuilds_qss and keyboard navigation still work."""
    dlg = pdfedit.SettingsDialog(main_window)
    # Force slider to a known starting value.
    dlg.font_slider.setValue(11)
    calls = {"n": 0}

    def _spy(value):
        calls["n"] += 1

    monkeypatch.setattr(dlg, "_apply_font_size", _spy)
    dlg.font_slider.setValue(15)
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Bug 8 — single-key tool shortcuts skip QPlainTextEdit / QTextEdit
# ---------------------------------------------------------------------------

def test_tool_shortcut_skipped_when_qplaintextedit_focused(
    qapp, main_window
):
    # Set a known current mode and confirm 'T' (add-text) doesn't change it.
    main_window._activate_tool("select")
    assert main_window.view.mode == "select"

    edit = QPlainTextEdit(main_window)
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    assert qapp.focusWidget() is edit

    main_window._handle_tool_shortcut("add-text")
    assert main_window.view.mode == "select", (
        "tool shortcut should not fire while a QPlainTextEdit has focus"
    )


def test_tool_shortcut_skipped_when_qtextedit_focused(qapp, main_window):
    from PyQt6.QtWidgets import QTextEdit
    main_window._activate_tool("select")

    edit = QTextEdit(main_window)
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    assert qapp.focusWidget() is edit

    main_window._handle_tool_shortcut("add-text")
    assert main_window.view.mode == "select"


# ---------------------------------------------------------------------------
# Bug 10 — Select tool not in &Insert menu
# ---------------------------------------------------------------------------

def test_select_not_in_insert_menu(qapp, main_window):
    spec = main_window._menu_spec
    insert_items = next(items for label, items in spec if label == "&Insert")
    for it in insert_items:
        if it is None or isinstance(it, QMenu):
            continue
        # tool actions store their mode in .data()
        assert it.data() != "select", (
            "the Select tool shouldn't appear under the Insert menu"
        )


# ---------------------------------------------------------------------------
# Bug 11 — Ctrl+Shift+= alternate zoom-in shortcut
# ---------------------------------------------------------------------------

def test_zoom_in_has_shift_equals_alternate(qapp, main_window):
    seqs = main_window.act_zoom_in.shortcuts()
    seq_strs = {s.toString() for s in seqs}
    assert any("Ctrl+=" == s for s in seq_strs), seq_strs
    assert any(
        "Ctrl+Shift+=" == s or "Shift+=" in s for s in seq_strs
    ), seq_strs


# ---------------------------------------------------------------------------
# Bug 12 — Format menu present with Bold / Italic
# ---------------------------------------------------------------------------

def test_format_menu_under_edit_has_bold_italic(qapp, main_window):
    spec = main_window._menu_spec
    edit_items = next(items for label, items in spec if label == "&Edit")
    submenus = [it for it in edit_items if isinstance(it, QMenu)]
    titles = [m.title() for m in submenus]
    assert any("Format" in t for t in titles), (
        f"expected a Format submenu under &Edit, got submenu titles {titles!r}"
    )
    fmt_menu = next(m for m in submenus if "Format" in m.title())
    action_texts = [a.text() for a in fmt_menu.actions()]
    assert any(t == "B" or "Bold" in t for t in action_texts), action_texts
    assert any(t == "I" or "Italic" in t for t in action_texts), action_texts


# ---------------------------------------------------------------------------
# Bug 9 (format-toolbar focus guard) — Ctrl+B in find box doesn't toggle bold
# ---------------------------------------------------------------------------

def test_fmt_toggle_skipped_when_lineedit_focused(
    qapp, main_window, monkeypatch
):
    # Under the offscreen Qt platform setFocus() is unreliable; force
    # focusWidget() to return the find box so the guard path is exercised.
    monkeypatch.setattr(
        pdfedit.QApplication, "focusWidget",
        staticmethod(lambda: main_window.find_box),
    )
    snapshots_before = len(main_window._undo) if hasattr(main_window, "_undo") else 0
    main_window._fmt_toggle("bold", True)
    snapshots_after = len(main_window._undo) if hasattr(main_window, "_undo") else 0
    assert snapshots_after == snapshots_before, (
        "_fmt_toggle should bail when a QLineEdit has focus"
    )
