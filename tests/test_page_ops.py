"""Tests for page-ops & navigation UX fixes (audit 10):

- Page-jump spinner
- Prev/Next disabled at boundaries
- insert_blank_page → US Letter
- Search results cleared on rotate/insert/delete page
- Cmd+wheel zoom anchored to cursor
- WatermarkDialog preview updates on every control change
- PageNumbersDialog format/position options
- Find Previous (Ctrl+Shift+G)
- parse_page_range edge cases + warnings
- Case-insensitive find
"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

from conftest import install_doc, make_blank_doc

import pdfedit


# ---------------------------------------------------------------------------
# Bug 1: Page-jump spinner
# ---------------------------------------------------------------------------
def test_page_spinner_jumps_to_typed_page(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=5))
    assert win.view.page_idx == 0
    assert win.page_spin.value() == 1
    assert win.page_spin.maximum() == 5

    # Simulate user typing "4" + Enter: setValue then editingFinished.
    win.page_spin.setValue(4)
    win._on_page_spin_changed()
    assert win.view.page_idx == 3


def test_page_spinner_max_updates_on_doc_change(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    assert win.page_spin.maximum() == 2
    install_doc(win, make_blank_doc(pages=7))
    assert win.page_spin.maximum() == 7
    assert win.page_spin.value() == 1


# ---------------------------------------------------------------------------
# Bug 2: Prev/Next disabled at boundaries
# ---------------------------------------------------------------------------
def test_prev_disabled_on_first_page(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    assert win.view.page_idx == 0
    assert not win.act_prev.isEnabled()
    assert win.act_next.isEnabled()


def test_next_disabled_on_last_page(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    win.view.scroll_to_page(2)
    win._refresh_page_label()
    assert win.view.page_idx == 2
    assert win.act_prev.isEnabled()
    assert not win.act_next.isEnabled()


def test_both_enabled_in_middle(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    win.view.scroll_to_page(1)
    win._refresh_page_label()
    assert win.act_prev.isEnabled()
    assert win.act_next.isEnabled()


# ---------------------------------------------------------------------------
# Bug 3: insert_blank_page is US Letter
# ---------------------------------------------------------------------------
def test_insert_blank_page_is_us_letter(main_window):
    win = main_window
    # Source doc is a non-Letter A4 page (595×842) so we can detect that
    # the inserted page does NOT inherit the source dimensions.
    install_doc(win, make_blank_doc(width_pt=595.0, height_pt=842.0, pages=1))
    win.insert_blank_page()
    assert win.view.doc.page_count == 2
    new_page = win.view.doc[1]
    assert int(new_page.rect.width) == 612
    assert int(new_page.rect.height) == 792


# ---------------------------------------------------------------------------
# Bug 4: search results cleared on page mutation
# ---------------------------------------------------------------------------
def _doc_with_text(pages: int = 3, text: str = "hello"):
    doc = make_blank_doc(pages=pages)
    for i in range(pages):
        doc[i].insert_text((72, 72), text, fontname="helv", fontsize=18)
    return doc


def test_search_cleared_on_rotate(main_window):
    win = main_window
    install_doc(win, _doc_with_text(pages=2, text="hello"))
    win.find_box.setText("hello")
    win.find_next()
    assert len(win._search_results) >= 1
    win.rotate_current_page()
    assert win._search_results == []
    assert win._search_idx == -1


def test_search_cleared_on_insert(main_window):
    win = main_window
    install_doc(win, _doc_with_text(pages=2, text="hello"))
    win.find_box.setText("hello")
    win.find_next()
    assert len(win._search_results) >= 1
    win.insert_blank_page()
    assert win._search_results == []
    assert win._search_idx == -1


def test_search_cleared_on_delete(main_window, monkeypatch):
    win = main_window
    install_doc(win, _doc_with_text(pages=3, text="hello"))
    win.find_box.setText("hello")
    win.find_next()
    assert len(win._search_results) >= 1
    monkeypatch.setattr(
        pdfedit.QMessageBox, "question",
        staticmethod(lambda *a, **kw: pdfedit.QMessageBox.StandardButton.Yes),
    )
    win.delete_current_page()
    assert win._search_results == []
    assert win._search_idx == -1


# ---------------------------------------------------------------------------
# Bug 5: Cmd+wheel zoom anchors to cursor
# ---------------------------------------------------------------------------
def test_cmd_wheel_anchors_to_cursor(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=1))
    view = win.view
    view.resize(800, 600)
    view.render_all()

    cursor_view = QPoint(200, 150)
    pre_scene = view.mapToScene(cursor_view)

    ev = QWheelEvent(
        QPointF(cursor_view),                # position (viewport)
        view.viewport().mapToGlobal(cursor_view).toPointF(),  # globalPosition
        QPoint(0, 0),                        # pixelDelta
        QPoint(0, 120),                      # angleDelta (positive = zoom in)
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    view.wheelEvent(ev)

    # After the zoom + scroll-correction, the same viewport point should map
    # back to (approximately) the same scene point. Allow a few px of slack
    # for scrollbar quantization.
    post_scene = view.mapToScene(cursor_view)
    assert abs(post_scene.x() - pre_scene.x()) < 5
    assert abs(post_scene.y() - pre_scene.y()) < 5


# ---------------------------------------------------------------------------
# Bug 6: WatermarkDialog preview updates on each control change
# ---------------------------------------------------------------------------
def test_watermark_preview_updates_on_changes(qtbot):
    dlg = pdfedit.WatermarkDialog(page_count=3)
    qtbot.addWidget(dlg)
    base = dlg.preview_update_count
    dlg.text_edit.setText("CONFIDENTIAL")
    assert dlg.preview_update_count > base
    base2 = dlg.preview_update_count
    dlg.size_box.setValue(40)
    assert dlg.preview_update_count > base2
    base3 = dlg.preview_update_count
    dlg.opacity_box.setValue(0.5)
    assert dlg.preview_update_count > base3
    base4 = dlg.preview_update_count
    dlg.rotation_box.setValue(30)
    assert dlg.preview_update_count > base4


# ---------------------------------------------------------------------------
# Bug 7: PageNumbersDialog format/position options
# ---------------------------------------------------------------------------
def test_page_numbers_dialog_default_values(qtbot):
    dlg = pdfedit.PageNumbersDialog(page_count=10)
    qtbot.addWidget(dlg)
    v = dlg.values()
    # Default = "Page 1 of N" at bottom center.
    assert v["position"] == "bottom-center"
    assert v["format"] == "Page {n} of {N}"
    assert v["size"] == 12
    assert v["start"] == 1
    assert v["skip_first"] is False


def test_page_numbers_apply_each_format(main_window):
    """Each format produces the expected text on a page."""
    win = main_window
    install_doc(win, make_blank_doc(pages=2))

    cases = [
        ("{n}", "1"),
        ("{n} / {N}", "1 / 2"),
        ("Page {n}", "Page 1"),
        ("Page {n} of {N}", "Page 1 of 2"),
        ("- {n} -", "- 1 -"),
    ]
    for fmt, expected in cases:
        # Fresh doc each iteration so previously-applied numbers don't pile up.
        install_doc(win, make_blank_doc(pages=2))
        win.add_page_numbers(options={
            "position": "bottom-center",
            "format": fmt,
            "size": 12,
            "start": 1,
            "skip_first": False,
        })
        page_text = win.view.doc[0].get_text("text")
        assert expected in page_text, (
            f"format {fmt!r} expected {expected!r} on page; got {page_text!r}"
        )


def test_page_numbers_skip_first(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=3))
    win.add_page_numbers(options={
        "position": "bottom-center",
        "format": "{n}",
        "size": 12,
        "start": 1,
        "skip_first": True,
    })
    # Page 0 should be untouched; pages 1+2 should have "1" and "2".
    assert "1" not in win.view.doc[0].get_text("text").strip()
    assert "1" in win.view.doc[1].get_text("text")
    assert "2" in win.view.doc[2].get_text("text")


def test_page_numbers_position_left_vs_right(main_window):
    """bottom-left and bottom-right place text at different x coordinates."""
    win = main_window
    for position, expect_left in [("bottom-left", True), ("bottom-right", False)]:
        install_doc(win, make_blank_doc(pages=1))
        win.add_page_numbers(options={
            "position": position,
            "format": "Page {n}",
            "size": 12,
            "start": 1,
            "skip_first": False,
        })
        # find the inserted text bbox
        rects = win.view.doc[0].search_for("Page 1")
        assert rects, f"text not found for {position}"
        x_center = (rects[0].x0 + rects[0].x1) / 2
        page_w = win.view.doc[0].rect.width
        if expect_left:
            assert x_center < page_w / 2, f"{position} should be on left; got x={x_center}"
        else:
            assert x_center > page_w / 2, f"{position} should be on right; got x={x_center}"


# ---------------------------------------------------------------------------
# Bug 8: Find Previous
# ---------------------------------------------------------------------------
def test_find_prev_walks_backward(main_window):
    win = main_window
    # 3 pages, 1 hit each = 3 results.
    install_doc(win, _doc_with_text(pages=3, text="hello"))
    win.find_box.setText("hello")
    win.find_next()  # idx → 0
    win.find_next()  # idx → 1
    assert win._search_idx == 1
    win.find_prev()  # idx → 0
    assert win._search_idx == 0


def test_find_prev_wraps_at_zero(main_window):
    win = main_window
    install_doc(win, _doc_with_text(pages=3, text="hello"))
    win.find_box.setText("hello")
    win.find_next()  # idx → 0
    win.find_prev()  # wrap → 2 (last)
    assert win._search_idx == len(win._search_results) - 1


def test_find_prev_action_has_shortcut(main_window):
    win = main_window
    assert win.act_find_prev is not None
    seq = win.act_find_prev.shortcut().toString()
    assert "Ctrl+Shift+G" in seq or "Shift+Ctrl+G" in seq, (
        f"expected Ctrl+Shift+G, got {seq!r}"
    )


# ---------------------------------------------------------------------------
# Bug 9: parse_page_range — friendlier edge cases + warnings
# ---------------------------------------------------------------------------
def test_parse_open_ended_high():
    pages, warnings = pdfedit.parse_page_range("1-", 10)
    assert pages == list(range(0, 10))
    assert warnings == []


def test_parse_open_ended_low():
    pages, warnings = pdfedit.parse_page_range("-3", 10)
    assert pages == [0, 1, 2]
    assert warnings == []


def test_parse_zero_warns():
    pages, warnings = pdfedit.parse_page_range("0", 10)
    assert pages == []
    assert any("0" in w for w in warnings)


def test_parse_out_of_range_warns():
    pages, warnings = pdfedit.parse_page_range("99999", 10)
    assert pages == []
    assert warnings, "expected a warning for out-of-range page"
    assert any("99999" in w or "exceeds" in w for w in warnings)


def test_parse_mixed_valid_and_invalid():
    pages, warnings = pdfedit.parse_page_range("1,2,99", 10)
    assert pages == [0, 1]
    assert warnings, "expected warning about page 99"


# ---------------------------------------------------------------------------
# Bug 10: Case-insensitive find
# ---------------------------------------------------------------------------
def test_find_matches_case_insensitively_by_default(main_window):
    win = main_window
    install_doc(win, _doc_with_text(pages=1, text="hello"))
    win.find_box.setText("HELLO")
    win.find_case_chk.setChecked(False)  # default
    win.find_next()
    assert len(win._search_results) >= 1


def test_find_with_match_case_filters(main_window):
    """When 'Match case' is on, "HELLO" should NOT match "hello"."""
    win = main_window
    install_doc(win, _doc_with_text(pages=1, text="hello"))
    win.find_case_chk.setChecked(True)
    win.find_box.setText("HELLO")
    win.find_next()
    assert win._search_results == []
    # And case-matching query DOES find it.
    win.find_box.setText("hello")
    win.find_next()
    assert len(win._search_results) >= 1
