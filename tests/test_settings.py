"""Tests for the Phase 7 SettingsDialog (Preferences panel).

The dialog mutates module-level theme/font constants and writes through
QSettings. We redirect QSettings to a per-test on-disk INI so tests don't
pollute the user's real preference store, and we restore the stock palette
constants in an autouse fixture so tests don't bleed into one another.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

import pdfedit
from conftest import install_doc, make_blank_doc


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Redirect QSettings (as accessed through pdfedit) to a tmpdir INI file."""
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
    """Snapshot/restore the mutable theme constants around each test."""
    light = dict(pdfedit.LIGHT_PALETTE)
    dark = dict(pdfedit.DARK_PALETTE)
    font_pt = pdfedit.UI_FONT_PT
    yield
    pdfedit.LIGHT_PALETTE.clear()
    pdfedit.LIGHT_PALETTE.update(light)
    pdfedit.DARK_PALETTE.clear()
    pdfedit.DARK_PALETTE.update(dark)
    pdfedit.UI_FONT_PT = font_pt


def _open_dialog(parent):
    dlg = pdfedit.SettingsDialog(parent)
    return dlg


def test_theme_change_applies_live(qapp, main_window, isolated_settings):
    dlg = _open_dialog(main_window)
    dlg._on_theme_changed("dark")
    assert pdfedit.current_theme_name() == "dark"
    qss = qapp.styleSheet()
    assert qss
    assert pdfedit.DARK_PALETTE["bg"] in qss


def test_font_size_slider_rebuilds_qss(qapp, main_window, isolated_settings):
    dlg = _open_dialog(main_window)
    dlg.font_slider.setValue(16)
    assert pdfedit.UI_FONT_PT == 16
    qss = qapp.styleSheet()
    assert qss
    assert "16pt" in qss


def test_accent_chosen_mutates_palette(qapp, main_window, isolated_settings):
    dlg = _open_dialog(main_window)
    dlg._on_accent_chosen(QColor("#FF00FF"))
    assert pdfedit.LIGHT_PALETTE["accent"].lower() == "#ff00ff"
    assert pdfedit.DARK_PALETTE["accent"].lower() == "#ff00ff"
    # accent-hover should be a different shade (darker for light, lighter for dark)
    assert pdfedit.LIGHT_PALETTE["accent-hover"].lower() != "#ff00ff"
    assert pdfedit.DARK_PALETTE["accent-hover"].lower() != "#ff00ff"
    qss = qapp.styleSheet()
    assert "#FF00FF" in qss or "#ff00ff" in qss.lower()


def test_auto_open_off_suppresses_properties_dialog(
    qapp, main_window, isolated_settings, monkeypatch
):
    dlg = _open_dialog(main_window)
    dlg.auto_open_chk.setChecked(False)

    calls = {"n": 0}
    original = main_window.edit_widget_properties

    def _spy(page_idx, widget):
        calls["n"] += 1
        return original(page_idx, widget)

    monkeypatch.setattr(main_window, "edit_widget_properties", _spy)

    install_doc(main_window, make_blank_doc())
    main_window.do_form_text(0, 50, 50, 200, 80)
    assert calls["n"] == 0, (
        f"edit_widget_properties was called {calls['n']} times "
        "with auto-open disabled"
    )

    # And re-enabling it should let the call through.
    dlg.auto_open_chk.setChecked(True)
    main_window.do_form_text(0, 50, 100, 200, 130)
    assert calls["n"] == 1


def test_field_pattern_persists_and_is_used(
    qapp, main_window, isolated_settings
):
    dlg = _open_dialog(main_window)
    dlg.field_pattern_edit.setText("F_{type}_{n}")
    dlg._on_field_pattern_changed()
    install_doc(main_window, make_blank_doc())
    main_window.do_form_text(0, 50, 50, 200, 80)
    page = main_window.view.doc[0]
    widgets = list(page.widgets())
    assert widgets, "expected one widget after do_form_text"
    assert widgets[0].field_name == "F_Text_1", (
        f"expected pattern-derived name, got {widgets[0].field_name!r}"
    )


def test_reset_all_clears_settings_store(
    qapp, main_window, isolated_settings, monkeypatch
):
    isolated_settings.setValue("uiFontPt", 17)
    isolated_settings.setValue("autoOpenFieldProperties", False)
    isolated_settings.setValue(pdfedit.THEME_SETTINGS_KEY, "dark")
    assert isolated_settings.value("uiFontPt") is not None

    dlg = _open_dialog(main_window)
    monkeypatch.setattr(
        pdfedit.QMessageBox,
        "question",
        staticmethod(lambda *a, **kw: pdfedit.QMessageBox.StandardButton.Yes),
    )
    dlg._on_reset_all_clicked()

    # After clear(), the keys should be gone.
    assert isolated_settings.value("uiFontPt") is None
    assert isolated_settings.value("autoOpenFieldProperties") is None


def test_reset_all_direct_clears_without_confirm(
    qapp, main_window, isolated_settings
):
    isolated_settings.setValue("uiFontPt", 17)
    dlg = _open_dialog(main_window)
    dlg.reset_all()
    assert isolated_settings.value("uiFontPt") is None


def test_settings_persist_across_main_window_instances(
    qapp, qtbot, isolated_settings
):
    """Open a window, change a setting, build a fresh window, and verify
    the second window picks the persisted value up."""
    win1 = pdfedit.MainWindow()
    qtbot.addWidget(win1)
    win1.show()
    qtbot.waitExposed(win1)
    # Clear any prior recorded user-choice override so the next window's
    # _read_form_panel_visibility() returns None and falls back to the
    # default-visible setting we're about to flip on.
    isolated_settings.remove("formBuilderPanelVisible")
    dlg1 = pdfedit.SettingsDialog(win1)
    dlg1.show_panel_chk.setChecked(True)
    dlg1.close()
    win1.dirty = False
    win1.closeEvent = lambda ev: ev.accept()
    win1.close()

    # Persisted?
    assert isolated_settings.value(
        pdfedit.FORM_BUILDER_PANEL_DEFAULT_VISIBLE_KEY
    )

    # New MainWindow should respect the saved default for visibility.
    win2 = pdfedit.MainWindow()
    qtbot.addWidget(win2)
    win2.show()
    qtbot.waitExposed(win2)
    assert win2.form_panel.isVisible() is True
    win2.dirty = False
    win2.closeEvent = lambda ev: ev.accept()
    win2.close()


def test_reset_appearance_restores_stock_constants(
    qapp, main_window, isolated_settings
):
    dlg = _open_dialog(main_window)
    dlg._on_accent_chosen(QColor("#123456"))
    dlg.font_slider.setValue(17)
    assert pdfedit.LIGHT_PALETTE["accent"].lower() == "#123456"
    assert pdfedit.UI_FONT_PT == 17
    dlg.reset_appearance()
    assert pdfedit.LIGHT_PALETTE["accent"] == pdfedit._DEFAULT_LIGHT_PALETTE["accent"]
    assert pdfedit.UI_FONT_PT == pdfedit._DEFAULT_UI_FONT_PT


def test_open_settings_dialog_returns_dialog(qapp, main_window, monkeypatch):
    monkeypatch.setattr(pdfedit.SettingsDialog, "exec", lambda self: 0)
    dlg = main_window.open_settings_dialog()
    assert isinstance(dlg, pdfedit.SettingsDialog)
