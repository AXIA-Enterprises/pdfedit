"""Tests for SignatureItem placement, resize, undo, and round-trip.

Covers the signature flow bugs fixed on qa/signature:
1. typed signatures now create SignatureItem (not TextBoxItem),
2. SignatureItem has a 2D resize handle that mutates pdf_w/pdf_h,
3. resize is captured in the undo stack,
4. resized signatures round-trip through save/reopen,
5. bake-failure messages use DISPLAY_NAME, not a hardcoded "Signature".
"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor
from conftest import install_doc, make_blank_doc

import pdfedit


class _FakeSceneEvent:
    """Minimal stand-in for QGraphicsSceneMouseEvent.

    QGraphicsSceneMouseEvent can't be constructed from Python (PyQt6 raises
    TypeError). The handle methods only call .scenePos() and .accept(), so
    a duck-typed object is enough.
    """

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


def _accept_signature_dialog(monkeypatch, kind, **extra):
    """Patch SignatureDialog.exec to accept a synthetic result_data payload."""
    real_exec = pdfedit.SignatureDialog.exec

    def fake_exec(self):
        if kind == "typed":
            self.result_data = {
                "kind": "typed",
                "text": extra.get("text", "Sample"),
                "family": extra.get("family", "Dancing Script"),
                "color": extra.get("color", "#000000"),
                "size_pt": extra.get("size_pt", 24),
            }
        else:
            self.result_data = {
                "kind": "drawn",
                "strokes": extra["strokes"],
                "color": extra.get("color", "#000000"),
            }
        from PyQt6.QtWidgets import QDialog as _D
        return _D.DialogCode.Accepted

    monkeypatch.setattr(pdfedit.SignatureDialog, "exec", fake_exec)
    return real_exec


def _place_drawn_signature(win, monkeypatch):
    strokes = [
        [(0.05, 0.5), (0.4, 0.2), (0.6, 0.8), (0.95, 0.5)],
    ]
    _accept_signature_dialog(monkeypatch, "drawn", strokes=strokes)
    win.do_signature(0, 100.0, 100.0, 340.0, 200.0)
    sigs = [ov for ov in win.view.overlays if isinstance(ov, pdfedit.SignatureItem)]
    assert sigs, "drawn path should have produced a SignatureItem"
    return sigs[-1]


def test_typed_signature_produces_signature_item(main_window, monkeypatch):
    """do_signature with kind='typed' must create a SignatureItem (not a TextBoxItem)."""
    win = main_window
    install_doc(win, make_blank_doc())

    _accept_signature_dialog(monkeypatch, "typed", text="Alice", family="Helvetica")
    win.do_signature(0, 100.0, 100.0, 340.0, 180.0)

    assert len(win.view.overlays) == 1
    ov = win.view.overlays[0]
    assert isinstance(ov, pdfedit.SignatureItem), (
        f"typed signature should be a SignatureItem, got {type(ov).__name__}"
    )
    # Make sure dimensions were actually populated.
    assert ov.pdf_w > 0 and ov.pdf_h > 0


def test_signature_item_has_resize_handle(main_window, monkeypatch):
    """SignatureItem.__init__ wires up a _SignatureResizeHandle."""
    win = main_window
    install_doc(win, make_blank_doc())

    sig = _place_drawn_signature(win, monkeypatch)
    assert hasattr(sig, "_handle"), "SignatureItem should expose a resize handle"
    assert isinstance(sig._handle, pdfedit._SignatureResizeHandle)


def test_signature_resize_drag_mutates_pdf_w_and_pdf_h(main_window, monkeypatch):
    """Programmatically drag the resize handle and assert pdf_w/pdf_h changed."""
    win = main_window
    install_doc(win, make_blank_doc())
    sig = _place_drawn_signature(win, monkeypatch)
    sig.setSelected(True)

    handle = sig._handle
    w0, h0 = sig.pdf_w, sig.pdf_h
    press_scene = handle.mapToScene(handle.boundingRect().center())
    _drive_resize(handle, press_scene, press_scene + QPointF(120.0, 80.0))

    assert sig.pdf_w > w0, f"pdf_w should grow (was {w0}, now {sig.pdf_w})"
    assert sig.pdf_h > h0, f"pdf_h should grow (was {h0}, now {sig.pdf_h})"


def test_signature_resize_is_captured_for_undo(main_window, monkeypatch):
    """Resize triggers a snapshot; undo restores prior dimensions."""
    win = main_window
    install_doc(win, make_blank_doc())
    sig = _place_drawn_signature(win, monkeypatch)
    sig.setSelected(True)

    w0, h0 = sig.pdf_w, sig.pdf_h
    handle = sig._handle

    press_scene = handle.mapToScene(handle.boundingRect().center())
    _drive_resize(handle, press_scene, press_scene + QPointF(150.0, 90.0))

    new_w, new_h = sig.pdf_w, sig.pdf_h
    assert (new_w, new_h) != (w0, h0)

    win.undo()
    sigs = [ov for ov in win.view.overlays if isinstance(ov, pdfedit.SignatureItem)]
    assert sigs, "undo should not delete the SignatureItem (snapshot was taken AT resize start)"
    restored = sigs[0]
    assert abs(restored.pdf_w - w0) < 1e-3
    assert abs(restored.pdf_h - h0) < 1e-3


def test_signature_resize_round_trips_through_save_and_reopen(
    main_window, monkeypatch, tmp_path
):
    """Resize a placed signature, save, reopen — saved page must reflect new dims."""
    win = main_window
    install_doc(win, make_blank_doc())
    sig = _place_drawn_signature(win, monkeypatch)
    sig.setSelected(True)

    handle = sig._handle
    press_scene = handle.mapToScene(handle.boundingRect().center())
    _drive_resize(handle, press_scene, press_scene + QPointF(200.0, 120.0))

    expected_w = sig.pdf_w
    expected_h = sig.pdf_h
    assert expected_w > 0 and expected_h > 0

    out = tmp_path / "sig.pdf"
    win.path = str(out)
    win.save_pdf()
    assert out.exists()

    # Reopen and look at the page's drawing extents. fitz's get_drawings
    # exposes path bboxes which should fall inside the signature rect.
    doc = fitz.open(str(out))
    try:
        drawings = doc[0].get_drawings()
        assert drawings, "saved PDF should contain the signature drawing"
        # Find the bbox spanning all drawings on the page (signature is the
        # only thing on a blank page).
        xs0 = [d["rect"].x0 for d in drawings]
        ys0 = [d["rect"].y0 for d in drawings]
        xs1 = [d["rect"].x1 for d in drawings]
        ys1 = [d["rect"].y1 for d in drawings]
        bbox_w = max(xs1) - min(xs0)
        bbox_h = max(ys1) - min(ys0)
        # The strokes are normalized 0..1 across (pdf_w, pdf_h), so the
        # extracted bbox should be close to the resized dims (within rounding
        # and the stroke width).
        assert bbox_w == pytest.approx(expected_w, abs=4.0), (
            f"saved bbox width {bbox_w} should match resized pdf_w {expected_w}"
        )
        assert bbox_h == pytest.approx(expected_h, abs=4.0), (
            f"saved bbox height {bbox_h} should match resized pdf_h {expected_h}"
        )
    finally:
        doc.close()


def test_bake_failure_label_uses_display_name_for_text_box(
    main_window, monkeypatch, tmp_path
):
    """A TextBoxItem bake failure must report 'Text box', not 'Signature'."""
    win = main_window
    install_doc(win, make_blank_doc())

    box = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=72, pdf_w=400,
        text="boom", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()

    def boom(self, page):
        raise RuntimeError("synthetic bake failure")

    monkeypatch.setattr(pdfedit.TextBoxItem, "to_pdf", boom)

    seen: dict[str, str] = {}

    def fake_critical(parent, title, body, *a, **kw):
        seen["body"] = body

    monkeypatch.setattr(pdfedit.QMessageBox, "critical", staticmethod(fake_critical))

    out = tmp_path / "label.pdf"
    win.path = str(out)
    win.save_pdf()
    assert "Text box" in seen.get("body", ""), (
        f"bake-failure body should include the TextBoxItem display name; got: "
        f"{seen.get('body')!r}"
    )


def test_bake_failure_label_uses_display_name_for_signature(
    main_window, monkeypatch, tmp_path
):
    """A SignatureItem bake failure must report 'Signature', not the type name."""
    win = main_window
    install_doc(win, make_blank_doc())

    sig = _place_drawn_signature(win, monkeypatch)

    def boom(self, page):
        raise RuntimeError("synthetic bake failure")

    monkeypatch.setattr(pdfedit.SignatureItem, "to_pdf", boom)

    seen: dict[str, str] = {}

    def fake_critical(parent, title, body, *a, **kw):
        seen["body"] = body

    monkeypatch.setattr(pdfedit.QMessageBox, "critical", staticmethod(fake_critical))

    out = tmp_path / "label2.pdf"
    win.path = str(out)
    win.save_pdf()

    body = seen.get("body", "")
    assert "Signature" in body
    assert "Text box" not in body
