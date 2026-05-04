"""Tests for Feature K: Bates Numbering."""

from __future__ import annotations

import fitz
import pytest

import pdfedit
from conftest import install_doc, make_blank_doc


def _stamp(win, **overrides):
    """Apply Bates with a sane default config; overrides are merged in."""
    options = {
        "prefix": "ACME",
        "suffix": "",
        "start": 1,
        "padding": 6,
        "position": "bottom-right",
        "size": 10,
        "color": pdfedit.QColor(0, 0, 0),
        "all_pages": True,
        "range": "",
    }
    options.update(overrides)
    win.open_bates_dialog(options=options)


def test_bates_basic_prefix_padded(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    _stamp(win, prefix="ACME", padding=6, start=1)
    assert "ACME000001" in win.view.doc[0].get_text("text")
    assert "ACME000002" in win.view.doc[1].get_text("text")


def test_bates_suffix(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=1))
    _stamp(win, prefix="ACME", suffix="-EXHIBIT-A", padding=6, start=1)
    assert "ACME000001-EXHIBIT-A" in win.view.doc[0].get_text("text")


@pytest.mark.parametrize(
    "position,expect_x,expect_y",
    [
        ("bottom-right", "right", "bottom"),
        ("bottom-left", "left", "bottom"),
        ("bottom-center", "center", "bottom"),
        ("top-right", "right", "top"),
        ("top-left", "left", "top"),
        ("top-center", "center", "top"),
    ],
)
def test_bates_position_quadrant(main_window, position, expect_x, expect_y):
    win = main_window
    install_doc(win, make_blank_doc(pages=1))
    _stamp(win, prefix="POS", padding=4, start=1, position=position)
    page = win.view.doc[0]
    rects = page.search_for("POS0001")
    assert rects, f"text not found for {position}"
    r = rects[0]
    cx = (r.x0 + r.x1) / 2
    cy = (r.y0 + r.y1) / 2
    pw = page.rect.width
    ph = page.rect.height
    if expect_x == "left":
        assert cx < pw / 2, f"{position} should be on left; got cx={cx}, pw={pw}"
    elif expect_x == "right":
        assert cx > pw / 2, f"{position} should be on right; got cx={cx}, pw={pw}"
    else:
        assert abs(cx - pw / 2) < pw / 4, f"{position} should be near center; cx={cx}"
    if expect_y == "top":
        assert cy < ph / 2, f"{position} should be on top; got cy={cy}, ph={ph}"
    else:
        assert cy > ph / 2, f"{position} should be on bottom; got cy={cy}, ph={ph}"


def test_bates_font_size_changes_height(main_window, tmp_path):
    """Larger font size produces taller rendered text in the saved PDF."""
    def stamp_and_height(size):
        win = main_window
        install_doc(win, make_blank_doc(pages=1))
        _stamp(win, prefix="SZ", padding=4, start=1, size=size)
        out = tmp_path / f"sz_{size}.pdf"
        win.path = str(out)
        win.save_pdf()
        with fitz.open(str(out)) as doc:
            rects = doc[0].search_for("SZ0001")
            assert rects, f"text not found at size {size}"
            return rects[0].height

    h_small = stamp_and_height(8)
    h_large = stamp_and_height(20)
    assert h_large > h_small, (
        f"font size 20 ({h_large}) should render taller than size 8 ({h_small})"
    )


def test_bates_color_changes_in_saved_pdf(main_window, tmp_path):
    """Stamping with red produces red colored spans in the saved PDF."""
    def stamp_color(color):
        win = main_window
        install_doc(win, make_blank_doc(pages=1))
        _stamp(win, prefix="CLR", padding=4, start=1, color=color)
        out = tmp_path / f"clr_{color.name()}.pdf"
        win.path = str(out)
        win.save_pdf()
        with fitz.open(str(out)) as doc:
            d = doc[0].get_text("dict")
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if "CLR0001" in span.get("text", ""):
                            return span.get("color")
        return None

    black_int = stamp_color(pdfedit.QColor(0, 0, 0))
    red_int = stamp_color(pdfedit.QColor(255, 0, 0))
    assert black_int is not None and red_int is not None
    assert black_int != red_int, (
        f"black ({black_int}) and red ({red_int}) should differ in saved PDF"
    )


def test_bates_page_range(main_window):
    """Range '2-4' on a 5-page doc stamps only pages 2..4."""
    win = main_window
    install_doc(win, make_blank_doc(pages=5))
    _stamp(win, prefix="RNG", padding=4, start=1, all_pages=False, range="2-4")
    assert "RNG" not in win.view.doc[0].get_text("text")
    assert "RNG0001" in win.view.doc[1].get_text("text")
    assert "RNG0002" in win.view.doc[2].get_text("text")
    assert "RNG0003" in win.view.doc[3].get_text("text")
    assert "RNG" not in win.view.doc[4].get_text("text")


def test_bates_dialog_live_preview_updates(qtbot):
    dlg = pdfedit.BatesNumberingDialog(page_count=3)
    qtbot.addWidget(dlg)

    base = dlg.preview_update_count
    assert "Sample:" in dlg.preview.text()

    dlg.prefix_edit.setText("ACME")
    assert dlg.preview_update_count > base
    assert "ACME000001" in dlg.preview.text()

    base2 = dlg.preview_update_count
    dlg.suffix_edit.setText("-DOC")
    assert dlg.preview_update_count > base2
    assert "ACME000001-DOC" in dlg.preview.text()

    base3 = dlg.preview_update_count
    dlg.start_box.setValue(42)
    assert dlg.preview_update_count > base3
    assert "ACME000042-DOC" in dlg.preview.text()

    base4 = dlg.preview_update_count
    dlg.padding_box.setValue(4)
    assert dlg.preview_update_count > base4
    assert "ACME0042-DOC" in dlg.preview.text()

    base5 = dlg.preview_update_count
    dlg.size_box.setValue(14)
    assert dlg.preview_update_count > base5

    base6 = dlg.preview_update_count
    dlg.position_box.setCurrentIndex(1)
    assert dlg.preview_update_count > base6


def test_bates_undo_restores_pre_state(main_window):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))
    pre_text_p0 = win.view.doc[0].get_text("text")
    pre_text_p1 = win.view.doc[1].get_text("text")
    assert "UND" not in pre_text_p0
    _stamp(win, prefix="UND", padding=4, start=1)
    assert "UND0001" in win.view.doc[0].get_text("text")
    assert "UND0002" in win.view.doc[1].get_text("text")
    win.undo()
    assert "UND" not in win.view.doc[0].get_text("text")
    assert "UND" not in win.view.doc[1].get_text("text")
    assert win.view.doc[0].get_text("text") == pre_text_p0
    assert win.view.doc[1].get_text("text") == pre_text_p1


def test_bates_dialog_default_values(qtbot):
    dlg = pdfedit.BatesNumberingDialog(page_count=10)
    qtbot.addWidget(dlg)
    v = dlg.values()
    assert v["prefix"] == ""
    assert v["suffix"] == ""
    assert v["start"] == 1
    assert v["padding"] == 6
    assert v["position"] == "bottom-right"
    assert v["size"] == 10
    assert v["color"].name() == "#000000"
    assert v["all_pages"] is True


def test_bates_menu_action_wired(main_window):
    win = main_window
    assert hasattr(win, "act_bates")
    assert win.act_bates.text() == "Bates Numbering…"


def test_bates_format_helper():
    fmt = pdfedit.BatesNumberingDialog.format_bates
    assert fmt("ACME", 1, 6, "") == "ACME000001"
    assert fmt("ACME", 42, 6, "-DOC") == "ACME000042-DOC"
    assert fmt("", 7, 4, "") == "0007"
    assert fmt("X", 5, 0, "Y") == "X5Y"
