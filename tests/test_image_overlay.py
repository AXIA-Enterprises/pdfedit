"""Tests for ImageOverlayItem placement, move, resize, undo, and round-trip.

Image insertion now drops a movable, resizable overlay (ImageOverlayItem)
rather than baking page.insert_image immediately. The overlay bakes only at
save time via _bake_to_clone.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import fitz
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QFileDialog
from conftest import install_doc, make_blank_doc

import pdfedit


def _make_png(path: Path, w: int = 100, h: int = 100, color=(255, 0, 0)) -> Path:
    """Write a tiny solid-color PNG to `path` without external deps."""
    r, g, b = color
    raw = b"".join(b"\x00" + bytes([r, g, b]) * w for _ in range(h))
    compressed = zlib.compress(raw)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


@pytest.fixture
def png_path(tmp_path) -> Path:
    return _make_png(tmp_path / "tiny.png", 100, 100, (200, 50, 50))


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


def _patch_file_dialog(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(path), "")),
    )


def _save_and_reopen(win, tmp_path, name):
    out = tmp_path / name
    win.path = str(out)
    win.save_pdf()
    assert out.exists()
    return fitz.open(str(out))


def test_image_drop_creates_overlay_not_baked(main_window, monkeypatch, png_path):
    """Inserted image is a Qt overlay, not yet in the saved PDF bytes."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_file_dialog(monkeypatch, png_path)

    win.do_insert_image(0, 50.0, 50.0, 250.0, 250.0)

    overlays = [ov for ov in win.view.overlays if isinstance(ov, pdfedit.ImageOverlayItem)]
    assert len(overlays) == 1
    ov = overlays[0]
    assert ov.path == str(png_path)
    assert ov.pdf_w > 0 and ov.pdf_h > 0
    # The fitz doc itself must not yet contain the image — overlay bakes on save.
    assert not win.view.doc[0].get_images()


def test_image_round_trips_through_save(main_window, monkeypatch, png_path, tmp_path):
    """Save the doc; the saved PDF must contain the embedded image."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_file_dialog(monkeypatch, png_path)

    win.do_insert_image(0, 50.0, 50.0, 250.0, 250.0)

    with _save_and_reopen(win, tmp_path, "img.pdf") as doc:
        imgs = doc[0].get_images()
    assert imgs, "saved PDF should contain the inserted image"


def test_image_overlay_resize_persists(main_window, monkeypatch, png_path, tmp_path):
    """Drag the resize handle, save+reopen — bbox should reflect new size."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_file_dialog(monkeypatch, png_path)

    win.do_insert_image(0, 50.0, 50.0, 200.0, 200.0)
    ov = [o for o in win.view.overlays if isinstance(o, pdfedit.ImageOverlayItem)][0]
    ov.setSelected(True)

    w0, h0 = ov.pdf_w, ov.pdf_h
    handle = ov._handle
    press_scene = handle.mapToScene(handle.boundingRect().center())
    _drive_resize(handle, press_scene, press_scene + QPointF(120.0, 80.0))
    assert ov.pdf_w > w0
    assert ov.pdf_h > h0
    expected_w, expected_h = ov.pdf_w, ov.pdf_h

    with _save_and_reopen(win, tmp_path, "img2.pdf") as doc:
        imgs = doc[0].get_image_info()
    assert imgs
    info = imgs[0]
    bb = info["bbox"]
    saved_w = bb[2] - bb[0]
    saved_h = bb[3] - bb[1]
    # PyMuPDF's keep_proportion fits the source aspect inside the rect, so the
    # actual extents may be smaller than (pdf_w, pdf_h) on one axis. Check the
    # bbox is no larger than the rect on either axis and definitely not still
    # at the original 200×200.
    assert saved_w <= expected_w + 1.0
    assert saved_h <= expected_h + 1.0
    assert saved_w > w0 or saved_h > h0


def test_image_overlay_move_persists(main_window, monkeypatch, png_path, tmp_path):
    """Move the overlay; saved PDF reflects the new position."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_file_dialog(monkeypatch, png_path)

    win.do_insert_image(0, 50.0, 50.0, 200.0, 200.0)
    ov = [o for o in win.view.overlays if isinstance(o, pdfedit.ImageOverlayItem)][0]

    new_pdf_x, new_pdf_y = 250.0, 300.0
    z = win.view.zoom
    top = win.view._page_geom[0][0]
    ov.setPos(pdfedit.PAGE_MARGIN + new_pdf_x * z, top + new_pdf_y * z)
    assert abs(ov.pdf_x - new_pdf_x) < 0.5
    assert abs(ov.pdf_y - new_pdf_y) < 0.5

    with _save_and_reopen(win, tmp_path, "img3.pdf") as doc:
        imgs = doc[0].get_image_info()
    assert imgs
    bb = imgs[0]["bbox"]
    assert abs(bb[0] - new_pdf_x) < 4.0
    assert abs(bb[1] - new_pdf_y) < 4.0


def test_image_overlay_resize_undo_restores(main_window, monkeypatch, png_path):
    """Undo after resize restores prior pdf_w/pdf_h."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_file_dialog(monkeypatch, png_path)

    win.do_insert_image(0, 50.0, 50.0, 200.0, 200.0)
    ov = [o for o in win.view.overlays if isinstance(o, pdfedit.ImageOverlayItem)][0]
    ov.setSelected(True)

    w0, h0 = ov.pdf_w, ov.pdf_h
    handle = ov._handle
    press_scene = handle.mapToScene(handle.boundingRect().center())
    _drive_resize(handle, press_scene, press_scene + QPointF(150.0, 90.0))
    assert (ov.pdf_w, ov.pdf_h) != (w0, h0)

    win.undo()
    overlays = [o for o in win.view.overlays if isinstance(o, pdfedit.ImageOverlayItem)]
    assert overlays, "undo should not delete the image overlay"
    restored = overlays[0]
    assert abs(restored.pdf_w - w0) < 1e-3
    assert abs(restored.pdf_h - h0) < 1e-3


def test_image_overlay_display_name_for_bake_failure(
    main_window, monkeypatch, png_path, tmp_path
):
    """Bake-failure label uses the ImageOverlayItem DISPLAY_NAME ('Image')."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_file_dialog(monkeypatch, png_path)
    win.do_insert_image(0, 50.0, 50.0, 200.0, 200.0)

    def boom(self, page):
        raise RuntimeError("synthetic image bake failure")

    monkeypatch.setattr(pdfedit.ImageOverlayItem, "to_pdf", boom)

    seen: dict[str, str] = {}

    def fake_warning(parent, title, body, *a, **kw):
        seen["body"] = body

    monkeypatch.setattr(pdfedit.QMessageBox, "warning", staticmethod(fake_warning))

    out = tmp_path / "imgfail.pdf"
    win.path = str(out)
    win.save_pdf()
    assert "Image" in seen.get("body", "")


def test_image_click_only_falls_back_to_default_size(
    main_window, monkeypatch, png_path
):
    """Tiny drag rect (< 30) → default 200pt-wide overlay sized to image aspect."""
    win = main_window
    install_doc(win, make_blank_doc())
    _patch_file_dialog(monkeypatch, png_path)

    win.do_insert_image(0, 100.0, 100.0, 105.0, 105.0)
    ov = [o for o in win.view.overlays if isinstance(o, pdfedit.ImageOverlayItem)][0]
    assert abs(ov.pdf_w - 200.0) < 1e-3
    assert abs(ov.pdf_h - 200.0) < 1e-3  # 100x100 source → square
