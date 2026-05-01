"""Tests for the recent-files QSettings-backed list on MainWindow."""

from __future__ import annotations

import os

import pytest
from PyQt6.QtCore import QSettings

import pdfedit


@pytest.fixture(autouse=True)
def _isolated_recent_settings(main_window, tmp_path, monkeypatch):
    """Redirect QSettings to a per-test on-disk INI under tmp_path.

    Otherwise these tests would pollute the user's real
    AXIA Enterprises/PDFEdit registry/plist entries.
    """
    ini_path = tmp_path / "pdfedit.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)
    monkeypatch.setattr(
        pdfedit.MainWindow, "_recent_settings", lambda self: settings
    )
    settings.setValue("recent_files", [])
    yield settings
    settings.setValue("recent_files", [])


def test_add_recent_inserts_path(main_window, tmp_path, _isolated_recent_settings):
    p = tmp_path / "foo.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    main_window._add_recent(str(p))
    stored = _isolated_recent_settings.value("recent_files", []) or []
    if isinstance(stored, str):
        stored = [stored]
    assert os.path.abspath(str(p)) in stored


def test_add_recent_dedups(main_window, tmp_path, _isolated_recent_settings):
    p = tmp_path / "dup.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    main_window._add_recent(str(p))
    main_window._add_recent(str(p))
    stored = _isolated_recent_settings.value("recent_files", []) or []
    if isinstance(stored, str):
        stored = [stored]
    abspath = os.path.abspath(str(p))
    assert stored.count(abspath) == 1, (
        f"path appears {stored.count(abspath)} times in {stored!r}"
    )


def test_add_recent_caps_at_max(main_window, tmp_path, _isolated_recent_settings):
    cap = pdfedit.MainWindow._RECENT_MAX
    # Add cap+5 distinct paths.
    paths = []
    for i in range(cap + 5):
        p = tmp_path / f"f{i}.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        paths.append(str(p))
        main_window._add_recent(str(p))
    stored = _isolated_recent_settings.value("recent_files", []) or []
    if isinstance(stored, str):
        stored = [stored]
    assert len(stored) == cap, f"expected list capped at {cap}, got {len(stored)}"
    # Most-recent at front.
    assert stored[0] == os.path.abspath(paths[-1])


def test_clear_recent_empties_list(
    main_window, tmp_path, _isolated_recent_settings
):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    main_window._add_recent(str(p))
    assert _isolated_recent_settings.value("recent_files", []), \
        "precondition: list should be non-empty"
    main_window._clear_recent()
    stored = _isolated_recent_settings.value("recent_files", []) or []
    if isinstance(stored, str):
        stored = [stored]
    assert stored == []
