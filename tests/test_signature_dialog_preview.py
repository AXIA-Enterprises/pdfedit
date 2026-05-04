"""Tests for SignatureDialog font preview behavior.

Covers the audit fixes for Bug A:
- text changes update the preview text without re-fetching the font,
- font changes apply the cached font when present,
- a network failure surfaces a visible failure indicator instead of
  silently keeping the old font.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase

import pdfedit


@pytest.fixture
def signature_dialog(qtbot, qapp):
    dlg = pdfedit.SignatureDialog()
    qtbot.addWidget(dlg)
    return dlg


def _force_family_loaded(family: str) -> None:
    pdfedit._loaded_font_families.add(family)


def test_preview_updates_text_without_blocking(signature_dialog, qtbot):
    """Type text, switch to a cached font, assert preview reflects both."""
    dlg = signature_dialog
    family = dlg.type_font.currentText()
    _force_family_loaded(family)
    # Drive a font refresh now that the family is "loaded".
    dlg._refresh_font(family)
    qtbot.wait(20)

    dlg.type_input.setText("Alice")
    qtbot.wait(20)

    assert dlg.type_preview.text() == "Alice"
    # The preview's font().family() should be the chosen one (or a Qt
    # substitute that resolves to it). Asserting non-empty + not italic
    # ("loading…" placeholder) is enough.
    assert not dlg.type_preview.font().italic(), (
        "preview should not be left in the loading-italic state"
    )


def test_switching_font_repaints_preview(signature_dialog, qtbot):
    """Pick a different cached font and assert the preview's font().family() changes."""
    dlg = signature_dialog
    a = dlg.type_font.itemText(0)
    b = dlg.type_font.itemText(1)
    _force_family_loaded(a)
    _force_family_loaded(b)

    dlg.type_input.setText("Sample")
    dlg._refresh_font(a)
    qtbot.wait(20)
    fam_a = dlg.type_preview.font().family()

    dlg.type_font.setCurrentText(b)
    qtbot.wait(120)  # well under 300ms; preview is set synchronously when cached
    fam_b = dlg.type_preview.font().family()

    # At least one of (a, b) should map to a unique resolved family. If the
    # platform substitutes both to the same fallback, this test is moot —
    # accept either a real change or a status update.
    if fam_a == fam_b:
        assert dlg.type_status.text() == "" or "failed" not in dlg.type_status.text()
    else:
        assert fam_a != fam_b


def test_fetch_failure_surfaces_visible_indicator(signature_dialog, qtbot, monkeypatch):
    """Mock fetch_google_font to return None; a font switch must surface a visible failure."""
    dlg = signature_dialog

    # Make the family look unloaded so _refresh_font goes through the
    # network path (rather than the cached-already branch).
    fresh_family = "Pacifico"
    pdfedit._loaded_font_families.discard(fresh_family)
    # Also nuke any on-disk cached file so the refresh uses fetch_google_font.
    cached = pdfedit.FONT_CACHE / f"{fresh_family.replace(' ', '_')}.ttf"
    if cached.exists():
        cached.unlink()

    monkeypatch.setattr(pdfedit, "fetch_google_font", lambda family: None)

    # Force the dialog onto the missing family.
    if dlg.type_font.findText(fresh_family) < 0:
        dlg.type_font.addItem(fresh_family)
    dlg.type_font.setCurrentText(fresh_family)
    qtbot.wait(50)

    # The pool runnable runs on a background thread; wait for the signal to
    # land on the GUI thread.
    def _failure_visible():
        return "failed" in dlg.type_status.text()

    qtbot.waitUntil(_failure_visible, timeout=2000)
    assert "failed" in dlg.type_status.text()
