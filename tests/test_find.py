"""Tests for the find/search subsystem (`MainWindow.find_next`)."""

from __future__ import annotations

import pytest

from conftest import install_doc, make_blank_doc

import pdfedit


def _doc_with_text(pages: int = 3, text: str = "hello"):
    """Build a multi-page doc with the same searchable text on every page."""
    doc = make_blank_doc(pages=pages)
    for i in range(pages):
        page = doc[i]
        page.insert_text((72, 72), text, fontname="helv", fontsize=18)
    return doc


def test_find_next_locates_text(main_window):
    win = main_window
    install_doc(win, _doc_with_text(pages=2, text="hello"))
    win.find_box.setText("hello")
    win.find_next()
    # _search_idx advances from -1 to 0 on the first hit.
    assert win._search_idx == 0
    # Status reflects "1 / N" format.
    status = win.find_status.text()
    assert status.startswith("1 /"), f"unexpected status {status!r}"
    assert "hello" in [r[1] for r in []] or len(win._search_results) >= 1


def test_find_next_cycles_past_last_match(main_window):
    win = main_window
    # 2 pages, one hit each = 2 results total.
    install_doc(win, _doc_with_text(pages=2, text="hello"))
    win.find_box.setText("hello")
    win.find_next()  # → idx 0
    win.find_next()  # → idx 1
    win.find_next()  # → wraps to 0
    assert win._search_idx == 0, (
        f"expected wrap-around to first hit, got idx={win._search_idx}"
    )


def test_find_no_match_does_not_crash(main_window):
    win = main_window
    install_doc(win, _doc_with_text(pages=1, text="hello"))
    win.find_box.setText("xyzzy_no_match")
    # Must not raise.
    win.find_next()
    assert win._search_results == []
    assert win.find_status.text() == "0 matches"
