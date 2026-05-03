"""Smoke tests for the Phase 6 theme system.

These are 'doesn't crash and writes the right strings' tests, not pixel
comparisons. They verify the public API surface that Phase 7's settings
panel will call into: apply_theme / set_theme / current_theme_name /
current_accent_color.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

import pdfedit


@pytest.fixture
def _isolated_theme_settings(tmp_path, monkeypatch):
    """Redirect QSettings used by the theme module to a tmpdir INI file.

    The theme code calls `QSettings()` (no-arg) which uses the org/app
    name. We monkeypatch the QSettings class as accessed through
    pdfedit so set_theme/current_theme_name write into a per-test file.
    """
    ini_path = tmp_path / "theme.ini"

    class _FakeQSettings:
        def __init__(self, *args, **kwargs):
            self._s = QSettings(str(ini_path), QSettings.Format.IniFormat)

        def value(self, key, default=None):
            v = self._s.value(key, default)
            return v

        def setValue(self, key, val):
            self._s.setValue(key, val)
            self._s.sync()

    monkeypatch.setattr(pdfedit, "QSettings", _FakeQSettings)
    yield ini_path


def test_apply_light_writes_light_colors(qapp):
    pdfedit.apply_theme(qapp, "light")
    qss = qapp.styleSheet()
    assert qss, "apply_theme('light') should set a non-empty stylesheet"
    # bg color or primary blue from LIGHT_PALETTE must show up in the QSS.
    assert pdfedit.LIGHT_PALETTE["bg"] in qss
    assert pdfedit.LIGHT_PALETTE["accent"] in qss
    # Dark accent should NOT appear when light theme is active.
    assert pdfedit.DARK_PALETTE["accent"] not in qss


def test_apply_dark_writes_dark_colors(qapp):
    pdfedit.apply_theme(qapp, "dark")
    qss = qapp.styleSheet()
    assert qss, "apply_theme('dark') should set a non-empty stylesheet"
    assert pdfedit.DARK_PALETTE["bg"] in qss
    assert pdfedit.DARK_PALETTE["accent"] in qss
    # Light-only accent (a value that is NOT also used inside the dark QSS
    # for some other role) should be absent.
    assert pdfedit.LIGHT_PALETTE["accent"] not in qss


def test_apply_system_does_not_raise(qapp):
    # Whatever the host preference is, system should resolve and apply.
    pdfedit.apply_theme(qapp, "system")
    qss = qapp.styleSheet()
    assert qss, "apply_theme('system') should set a non-empty stylesheet"


def test_apply_unknown_falls_back_to_system(qapp):
    pdfedit.apply_theme(qapp, "totally-not-a-theme")
    assert qapp.styleSheet(), "unknown theme name should still apply something"


def test_set_theme_persists_to_qsettings(qapp, _isolated_theme_settings):
    pdfedit.set_theme(qapp, "dark")
    # Read it back via the same fake-QSettings indirection.
    s = pdfedit.QSettings()
    assert s.value(pdfedit.THEME_SETTINGS_KEY) == "dark"

    pdfedit.set_theme(qapp, "light")
    s2 = pdfedit.QSettings()
    assert s2.value(pdfedit.THEME_SETTINGS_KEY) == "light"


def test_current_theme_name_reads_persisted_value(
    qapp, _isolated_theme_settings
):
    pdfedit.set_theme(qapp, "dark")
    assert pdfedit.current_theme_name() == "dark"
    pdfedit.set_theme(qapp, "light")
    assert pdfedit.current_theme_name() == "light"


def test_current_theme_name_defaults_to_system(qapp, _isolated_theme_settings):
    # Fresh INI, nothing persisted.
    assert pdfedit.current_theme_name() == "system"


def test_current_accent_color_matches_active_theme(qapp):
    pdfedit.apply_theme(qapp, "light")
    c = pdfedit.current_accent_color()
    assert isinstance(c, QColor)
    assert c.name().lower() == pdfedit.LIGHT_PALETTE["accent"].lower()

    pdfedit.apply_theme(qapp, "dark")
    c2 = pdfedit.current_accent_color()
    assert c2.name().lower() == pdfedit.DARK_PALETTE["accent"].lower()


def test_palette_dicts_have_matching_keys():
    """LIGHT and DARK palettes must define the same role set so QSS templating
    never KeyErrors when switching themes."""
    assert set(pdfedit.LIGHT_PALETTE.keys()) == set(pdfedit.DARK_PALETTE.keys())


def test_apply_then_main_window_does_not_crash(qapp, qtbot):
    """End-to-end: apply each theme then build a MainWindow under that theme."""
    for name in ("light", "dark", "system"):
        pdfedit.apply_theme(qapp, name)
        win = pdfedit.MainWindow()
        qtbot.addWidget(win)
        # styleSheet on the QApplication should be populated.
        assert qapp.styleSheet(), f"styleSheet empty under theme {name!r}"
        win.dirty = False
        win.closeEvent = lambda ev: ev.accept()
        win.close()
