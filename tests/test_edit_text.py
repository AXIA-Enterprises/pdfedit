"""Tests for Feature D: Edit Existing Text (redact-and-replace).

The inline-edit popup (EditTextPopup) is UI-heavy, so the apply path is
exercised directly through MainWindow.apply_edit_text — the popup is
covered by a focused construction test plus an Esc/cancel test that
verifies the handler routing without going through the popup widget.
"""

from __future__ import annotations

import io

import fitz
import pytest

from conftest import install_doc, make_blank_doc

import pdfedit


def _doc_with_text(lines, *, fontname="helv", fontsize=12,
                    start_xy=(50, 100), line_gap=20):
    """Build a single-page PDF with the given lines drawn at known baselines."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    x, y = start_xy
    for i, line in enumerate(lines):
        page.insert_text(
            (x, y + i * line_gap), line, fontname=fontname, fontsize=fontsize,
            color=(0, 0, 0),
        )
    return doc


def _roundtrip(doc):
    """Save to bytes and reopen to flush insert_text + apply_redactions to disk."""
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return fitz.open(stream=buf.read(), filetype="pdf")


def _get_line_info(page, needle):
    """Return the line dict that contains `needle`, or None."""
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            text = "".join(s.get("text", "") for s in line["spans"])
            if needle in text:
                return {
                    "bbox": fitz.Rect(*line["bbox"]),
                    "spans": line["spans"],
                    "text": text,
                }
    return None


def test_apply_edit_text_replaces_line(main_window):
    win = main_window
    install_doc(win, _doc_with_text(["Hello world"]))

    page = win.view.doc[0]
    info = _get_line_info(page, "Hello world")
    assert info is not None, "fixture line not found"

    result = win.apply_edit_text(0, info["bbox"], info["spans"], "Goodbye world")
    assert result["applied"] is True

    # Round-trip through save+reopen so the redaction + insert_text are flushed.
    doc2 = _roundtrip(win.view.doc)
    text = doc2.load_page(0).get_text()
    assert "Goodbye world" in text
    assert "Hello world" not in text


def test_apply_edit_text_marks_dirty_and_snapshots(main_window):
    win = main_window
    install_doc(win, _doc_with_text(["Original"]))
    win.dirty = False
    pre_undo_len = len(win._undo)

    page = win.view.doc[0]
    info = _get_line_info(page, "Original")
    win.apply_edit_text(0, info["bbox"], info["spans"], "Replaced")

    assert win.dirty is True
    assert len(win._undo) == pre_undo_len + 1


def test_undo_restores_original(main_window):
    win = main_window
    install_doc(win, _doc_with_text(["Hello world"]))

    page = win.view.doc[0]
    info = _get_line_info(page, "Hello world")
    win.apply_edit_text(0, info["bbox"], info["spans"], "Goodbye world")

    # Confirm change took effect on the in-memory doc.
    page_after = win.view.doc[0]
    assert "Goodbye world" in page_after.get_text()

    win.undo()

    page_undone = win.view.doc[0]
    text = page_undone.get_text()
    assert "Hello world" in text
    assert "Goodbye world" not in text


def test_multiline_only_affects_targeted_line(main_window):
    win = main_window
    install_doc(win, _doc_with_text(["Line one", "Line two", "Line three"]))

    page = win.view.doc[0]
    info = _get_line_info(page, "Line two")
    assert info is not None

    win.apply_edit_text(0, info["bbox"], info["spans"], "Middle line")

    doc2 = _roundtrip(win.view.doc)
    text = doc2.load_page(0).get_text()
    assert "Line one" in text
    assert "Line three" in text
    assert "Middle line" in text
    assert "Line two" not in text


def test_unknown_font_collects_warning(main_window):
    win = main_window
    install_doc(win, _doc_with_text(["Hello world"]))

    page = win.view.doc[0]
    info = _get_line_info(page, "Hello world")
    spans = list(info["spans"])
    # Force an unrecognized font name on the first span so the matcher
    # has to fall back. flags=0 keeps it regular weight.
    spans[0] = dict(spans[0])
    spans[0]["font"] = "WeirdMadeUpFontXYZ"

    result = win.apply_edit_text(0, info["bbox"], spans, "Replaced text")
    assert result["applied"] is True
    assert any("WeirdMadeUpFontXYZ" in w for w in result["warnings"]), (
        f"expected font-substitution warning, got {result['warnings']}"
    )


def test_no_text_under_click_returns_status(main_window):
    win = main_window
    install_doc(win, _doc_with_text(["Hello world"]))

    # Click in empty whitespace far from the text line.
    win._open_edit_text_at(0, 500.0, 700.0, None)

    msg = win.statusBar().currentMessage()
    assert "No editable text" in msg


def test_find_text_line_at_helper_returns_line():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 100), "Click here", fontname="helv", fontsize=12)

    line_dict = page.get_text("dict")["blocks"][0]["lines"][0]
    bx0, by0, bx1, by1 = line_dict["bbox"]
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2

    info = pdfedit._find_text_line_at(page, cx, cy)
    assert info is not None
    assert info["text"] == "Click here"
    assert info["bbox"].x0 == pytest.approx(bx0)


def test_find_text_line_at_returns_none_in_blank_area():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 100), "Some text", fontname="helv", fontsize=12)

    # Far below the text — no line covers this point.
    info = pdfedit._find_text_line_at(page, 500.0, 700.0)
    assert info is None


def test_match_pdf_font_helvetica_family():
    doc = fitz.open()
    page = doc.new_page()
    fn, sub = pdfedit._match_pdf_font_for_edit("Helvetica", 12, 0, page)
    assert fn == "helv"
    assert sub is None


def test_match_pdf_font_arial_aliases_to_helv():
    doc = fitz.open()
    page = doc.new_page()
    fn, sub = pdfedit._match_pdf_font_for_edit("Arial,Bold", 12, 16, page)
    assert fn == "hebo"
    assert sub is None


def test_match_pdf_font_times_family():
    doc = fitz.open()
    page = doc.new_page()
    fn, sub = pdfedit._match_pdf_font_for_edit("Times-Italic", 12, 2, page)
    assert fn == "tiit"
    assert sub is None


def test_match_pdf_font_unknown_falls_back_to_helv_with_warning():
    doc = fitz.open()
    page = doc.new_page()
    fn, sub = pdfedit._match_pdf_font_for_edit("ZapfChancery", 12, 0, page)
    assert fn == "helv"
    assert sub is not None
    assert "ZapfChancery" in sub


def test_pdf_color_int_decoder():
    # 0xFF8040 → (1.0, 0.5019..., 0.2509...)
    r, g, b = pdfedit._pdf_span_color_to_rgb(0xFF8040)
    assert r == pytest.approx(1.0)
    assert g == pytest.approx(0x80 / 255.0)
    assert b == pytest.approx(0x40 / 255.0)
    # Fall through path: tuple input passes through.
    rt = pdfedit._pdf_span_color_to_rgb((0.1, 0.2, 0.3))
    assert rt == (0.1, 0.2, 0.3)


def test_edit_text_tool_registered(main_window):
    win = main_window
    modes = [a.data() for a in win._tool_actions]
    assert "edit-text" in modes


def test_edit_text_in_edit_menu(main_window):
    win = main_window
    found = False
    for label, items in win._menu_spec:
        if label == "&Edit":
            for it in items:
                if it is not None and hasattr(it, "data") and it.data() == "edit-text":
                    found = True
                    break
    assert found, "Edit Text action should appear in the &Edit menu"


def test_edit_text_not_in_insert_menu(main_window):
    win = main_window
    for label, items in win._menu_spec:
        if label == "&Insert":
            for it in items:
                if it is not None and hasattr(it, "data") and it.data() == "edit-text":
                    pytest.fail("Edit Text should not appear in the &Insert menu")


def test_edit_text_mode_sets_ibeam_cursor(main_window):
    win = main_window
    install_doc(win, _doc_with_text(["Hello"]))
    win._activate_tool("edit-text")
    assert win.view.mode == "edit-text"
    # Cursor reflects the text-targeted mode.
    from PyQt6.QtCore import Qt as _Qt
    assert win.view.viewport().cursor().shape() == _Qt.CursorShape.IBeamCursor


def test_edit_text_popup_cancel_does_nothing(main_window, qtbot):
    """Construct a popup, simulate Esc, assert no edit was applied."""
    win = main_window
    install_doc(win, _doc_with_text(["Hello world"]))
    win.dirty = False

    captured = {"committed": False, "cancelled": False}

    def on_commit(t):
        captured["committed"] = True

    def on_cancel():
        captured["cancelled"] = True

    popup = pdfedit.EditTextPopup(
        win, original_text="Hello world",
        on_commit=on_commit, on_cancel=on_cancel,
    )
    qtbot.addWidget(popup)
    popup.show()
    qtbot.waitExposed(popup)

    from PyQt6.QtCore import Qt as _Qt
    qtbot.keyClick(popup, _Qt.Key.Key_Escape)

    assert captured["cancelled"] is True
    assert captured["committed"] is False
    # Document untouched.
    page = win.view.doc[0]
    assert "Hello world" in page.get_text()


def test_edit_text_popup_enter_commits(main_window, qtbot):
    win = main_window
    install_doc(win, _doc_with_text(["Hello"]))

    captured = {"text": None}

    def on_commit(t):
        captured["text"] = t

    popup = pdfedit.EditTextPopup(
        win, original_text="Hello",
        on_commit=on_commit, on_cancel=lambda: None,
    )
    qtbot.addWidget(popup)
    popup.show()
    qtbot.waitExposed(popup)
    popup.setText("Bye")

    from PyQt6.QtCore import Qt as _Qt
    qtbot.keyClick(popup, _Qt.Key.Key_Return)

    assert captured["text"] == "Bye"


def test_apply_edit_text_overflow_flagged(main_window):
    """A much longer replacement should report overflow=True in the result."""
    win = main_window
    install_doc(win, _doc_with_text(["Hi"]))

    page = win.view.doc[0]
    info = _get_line_info(page, "Hi")
    long_text = "This is a much longer replacement string that overflows the original line"
    result = win.apply_edit_text(0, info["bbox"], info["spans"], long_text)
    assert result["applied"] is True
    assert result["overflow"] is True
