"""Tests for Feature F drawing tools: pen, rectangle, ellipse, line, arrow.

Each test exercises do_draw_* on a blank doc, saves+reopens, and asserts the
shape ends up in the saved PDF via page.get_drawings().
"""

from __future__ import annotations

import math

import fitz
import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor

from conftest import install_doc, make_blank_doc

import pdfedit


class _FakeSceneEvent:
    def __init__(self, scene_pos: QPointF):
        self._scene_pos = scene_pos

    def scenePos(self) -> QPointF:
        return self._scene_pos

    def accept(self) -> None:
        pass


def _drive_resize(handle, start: QPointF, end: QPointF) -> None:
    handle.mousePressEvent(_FakeSceneEvent(start))
    handle.mouseMoveEvent(_FakeSceneEvent(end))
    handle.mouseReleaseEvent(_FakeSceneEvent(end))


def _save_and_reopen(win, tmp_path, name):
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists()
    return fitz.open(str(out))


def _all_drawings(page):
    """Return PyMuPDF drawings (vector ops) on the page."""
    return page.get_drawings()


def _pdf_has_polyline_close_to(page, expected_pts, tol=2.0) -> bool:
    """Return True if the page contains a polyline whose vertices match
    expected_pts (within tol). Allows reordered or nested drawings."""
    target = [(round(x, 1), round(y, 1)) for x, y in expected_pts]
    for d in _all_drawings(page):
        items = d.get("items", [])
        pts = []
        for it in items:
            # PyMuPDF item tuples: ('l', p1, p2) for line segments
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if not pts:
                    pts.append((round(p1.x, 1), round(p1.y, 1)))
                pts.append((round(p2.x, 1), round(p2.y, 1)))
        if not pts:
            continue
        if len(pts) < len(target):
            continue
        # Allow slack: just check the first len(target) points match.
        ok = True
        for tgt, got in zip(target, pts):
            if abs(tgt[0] - got[0]) > tol or abs(tgt[1] - got[1]) > tol:
                ok = False
                break
        if ok:
            return True
    return False


def _find_rect_drawing(page, expected_rect, tol=2.0):
    """Return the first drawing whose rect bbox matches `expected_rect`."""
    for d in _all_drawings(page):
        bb = d.get("rect")
        if bb is None:
            continue
        if (
            abs(bb.x0 - expected_rect[0]) <= tol
            and abs(bb.y0 - expected_rect[1]) <= tol
            and abs(bb.x1 - expected_rect[2]) <= tol
            and abs(bb.y1 - expected_rect[3]) <= tol
        ):
            return d
    return None


# ---------------------------------------------------------------- pen

def test_pen_round_trips(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    pts = [(50.0, 50.0), (60.0, 70.0), (80.0, 90.0), (100.0, 100.0)]
    win.do_draw_pen(0, pts)
    overlays = [o for o in win.view.overlays if isinstance(o, pdfedit.PenStrokeOverlay)]
    assert len(overlays) == 1
    with _save_and_reopen(win, tmp_path, "pen.pdf") as doc:
        assert _pdf_has_polyline_close_to(doc[0], pts, tol=2.0)


def test_pen_50_point_polyline_round_trip(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    pts = [(50.0 + i * 4.0, 100.0 + math.sin(i / 3.0) * 30.0) for i in range(50)]
    win.do_draw_pen(0, pts)
    with _save_and_reopen(win, tmp_path, "pen50.pdf") as doc:
        assert _pdf_has_polyline_close_to(doc[0], pts, tol=2.0)


# ---------------------------------------------------------------- rectangle

def test_rect_round_trips_with_stroke_color(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win._session_draw_stroke = QColor(200, 30, 30)
    win.do_draw_rect(0, 60.0, 60.0, 240.0, 180.0)
    overlays = [o for o in win.view.overlays
                if isinstance(o, pdfedit.ShapeOverlay) and o.shape == "rect"]
    assert len(overlays) == 1
    with _save_and_reopen(win, tmp_path, "rect.pdf") as doc:
        d = _find_rect_drawing(doc[0], (60.0, 60.0, 240.0, 180.0))
        assert d is not None, "rectangle bbox not found in saved PDF"
        # Color comes back as a 3-tuple of floats 0..1.
        col = d.get("color") or d.get("stroke")
        assert col is not None
        r, g, b = col
        assert r > 0.6 and g < 0.3 and b < 0.3


# ---------------------------------------------------------------- ellipse

def test_ellipse_round_trips(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_draw_ellipse(0, 100.0, 100.0, 300.0, 220.0)
    overlays = [o for o in win.view.overlays
                if isinstance(o, pdfedit.ShapeOverlay) and o.shape == "ellipse"]
    assert len(overlays) == 1
    with _save_and_reopen(win, tmp_path, "ellipse.pdf") as doc:
        # PyMuPDF renders draw_oval as a curved path; its bounding rect should
        # match the requested rect.
        found = False
        for d in _all_drawings(doc[0]):
            bb = d.get("rect")
            if bb is None:
                continue
            if (
                abs(bb.x0 - 100.0) < 3.0
                and abs(bb.y0 - 100.0) < 3.0
                and abs(bb.x1 - 300.0) < 3.0
                and abs(bb.y1 - 220.0) < 3.0
            ):
                found = True
                break
        assert found, "ellipse bbox not found in saved PDF"


# ---------------------------------------------------------------- line

def test_line_round_trips(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_draw_line(0, 50.0, 50.0, 250.0, 150.0)
    with _save_and_reopen(win, tmp_path, "line.pdf") as doc:
        # A single line: one drawing with one 'l' item.
        ok = False
        for d in _all_drawings(doc[0]):
            items = d.get("items", [])
            if len(items) == 1 and items[0][0] == "l":
                p1, p2 = items[0][1], items[0][2]
                if (
                    abs(p1.x - 50.0) < 2.0 and abs(p1.y - 50.0) < 2.0
                    and abs(p2.x - 250.0) < 2.0 and abs(p2.y - 150.0) < 2.0
                ):
                    ok = True
                    break
        assert ok, "line endpoints not found in saved PDF"


# ---------------------------------------------------------------- arrow

def test_arrow_round_trips_with_arrowhead(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_draw_arrow(0, 60.0, 60.0, 260.0, 160.0)
    with _save_and_reopen(win, tmp_path, "arrow.pdf") as doc:
        # An arrow bakes as 3 line segments: shaft + 2 head edges.
        line_count = 0
        for d in _all_drawings(doc[0]):
            for it in d.get("items", []):
                if it[0] == "l":
                    line_count += 1
        assert line_count >= 3, f"expected >=3 line segments for arrow, got {line_count}"


# ---------------------------------------------------------------- resize

def test_resize_drawn_rectangle_persists(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_draw_rect(0, 50.0, 50.0, 150.0, 120.0)
    ov = [o for o in win.view.overlays
          if isinstance(o, pdfedit.ShapeOverlay) and o.shape == "rect"][0]
    ov.setSelected(True)
    handle = ov._handle
    press_scene = handle.mapToScene(handle.boundingRect().center())
    _drive_resize(handle, press_scene, press_scene + QPointF(160.0, 110.0))
    assert ov.pdf_w > 100.0
    assert ov.pdf_h > 70.0
    with _save_and_reopen(win, tmp_path, "rect_resize.pdf") as doc:
        d = _find_rect_drawing(
            doc[0],
            (ov.pdf_x, ov.pdf_y, ov.pdf_x + ov.pdf_w, ov.pdf_y + ov.pdf_h),
            tol=3.0,
        )
        assert d is not None


# ---------------------------------------------------------------- properties

def test_properties_dialog_changes_stroke_color(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_draw_rect(0, 80.0, 80.0, 220.0, 180.0)
    ov = [o for o in win.view.overlays if isinstance(o, pdfedit.ShapeOverlay)][0]

    # Bypass the dialog UI and apply values directly via edit_drawing_properties.
    new_color = QColor(20, 180, 40)

    class _StubDlg:
        def __init__(self, *a, **kw):
            pass
        def exec(self):
            from PyQt6.QtWidgets import QDialog
            return QDialog.DialogCode.Accepted
        def result_values(self):
            return {
                "stroke_color": new_color,
                "stroke_width": 4.0,
                "fill_color": None,
            }

    real = pdfedit.DrawingPropertiesDialog
    pdfedit.DrawingPropertiesDialog = _StubDlg
    try:
        win.edit_drawing_properties(ov)
    finally:
        pdfedit.DrawingPropertiesDialog = real

    assert ov.stroke_color.green() > 100
    assert ov.stroke_width == 4.0
    with _save_and_reopen(win, tmp_path, "rect_props.pdf") as doc:
        for d in _all_drawings(doc[0]):
            col = d.get("color") or d.get("stroke")
            if col is None:
                continue
            r, g, b = col
            if g > 0.5 and r < 0.3 and b < 0.3:
                return
        pytest.fail("recolored rectangle stroke not found in saved PDF")


# ---------------------------------------------------------------- move

def test_move_drawn_ellipse_persists(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_draw_ellipse(0, 50.0, 50.0, 200.0, 150.0)
    ov = [o for o in win.view.overlays
          if isinstance(o, pdfedit.ShapeOverlay) and o.shape == "ellipse"][0]
    new_x, new_y = 220.0, 320.0
    z = win.view.zoom
    top = win.view._page_geom[0][0]
    ov.setPos(pdfedit.PAGE_MARGIN + new_x * z, top + new_y * z)
    assert abs(ov.pdf_x - new_x) < 0.5
    assert abs(ov.pdf_y - new_y) < 0.5
    with _save_and_reopen(win, tmp_path, "ellipse_move.pdf") as doc:
        found = False
        for d in _all_drawings(doc[0]):
            bb = d.get("rect")
            if bb is None:
                continue
            if abs(bb.x0 - new_x) < 4.0 and abs(bb.y0 - new_y) < 4.0:
                found = True
                break
        assert found, "moved ellipse not found at new position"


# ---------------------------------------------------------------- multi

def test_multiple_overlays_round_trip(main_window, tmp_path):
    win = main_window
    install_doc(win, make_blank_doc())
    win.do_draw_rect(0, 60.0, 60.0, 160.0, 140.0)
    win.do_draw_ellipse(0, 200.0, 60.0, 320.0, 140.0)
    win.do_draw_line(0, 60.0, 200.0, 320.0, 200.0)
    overlays = [o for o in win.view.overlays if isinstance(o, pdfedit.ShapeOverlay)]
    assert len(overlays) == 3
    with _save_and_reopen(win, tmp_path, "multi.pdf") as doc:
        # Rectangle bbox.
        assert _find_rect_drawing(doc[0], (60.0, 60.0, 160.0, 140.0)) is not None
        # Ellipse bbox.
        ellipse_ok = False
        for d in _all_drawings(doc[0]):
            bb = d.get("rect")
            if bb is None:
                continue
            if (
                abs(bb.x0 - 200.0) < 3.0
                and abs(bb.y0 - 60.0) < 3.0
                and abs(bb.x1 - 320.0) < 3.0
                and abs(bb.y1 - 140.0) < 3.0
            ):
                ellipse_ok = True
                break
        assert ellipse_ok, "ellipse bbox not found"
        # Line endpoints.
        line_ok = False
        for d in _all_drawings(doc[0]):
            items = d.get("items", [])
            if len(items) == 1 and items[0][0] == "l":
                p1, p2 = items[0][1], items[0][2]
                if (
                    abs(p1.x - 60.0) < 2.0 and abs(p1.y - 200.0) < 2.0
                    and abs(p2.x - 320.0) < 2.0 and abs(p2.y - 200.0) < 2.0
                ):
                    line_ok = True
                    break
        assert line_ok, "line not found"
