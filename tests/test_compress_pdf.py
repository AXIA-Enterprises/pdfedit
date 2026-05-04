"""Tests for CompressDialog and the compress-PDF flow."""

from __future__ import annotations

import io
import os
import random
from pathlib import Path

import fitz
import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox

from conftest import install_doc

import pdfedit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_noisy_png(w: int = 256, h: int = 256, seed: int = 0) -> bytes:
    """Return a high-entropy PNG that won't trivially deflate.

    Used so that JPEG re-encoding produces a measurable size delta.
    """
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    random.seed(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (
                random.randint(0, 255),
                random.randint(0, 255),
                random.randint(0, 255),
            )
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _make_pdf_with_image(path: Path, *, image_bytes: bytes | None = None,
                         pages: int = 1) -> None:
    if image_bytes is None:
        image_bytes = _make_noisy_png()
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=612.0, height=792.0)
        if i == 0:
            page.insert_image(fitz.Rect(50, 50, 562, 562), stream=image_bytes)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()


def _make_blank_pdf(path: Path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=612.0, height=792.0)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()


def _open_in_window(win, source_path: Path) -> None:
    doc = fitz.open(str(source_path))
    install_doc(win, doc)
    win.path = str(source_path)


def _stub_msgbox(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes))


# ---------------------------------------------------------------------------
# 1. Compress doc with embedded image → output smaller than input
# ---------------------------------------------------------------------------
def test_compress_image_doc_shrinks(main_window, tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    win = main_window
    src = tmp_path / "src.pdf"
    _make_pdf_with_image(src)
    _open_in_window(win, src)
    src_size = os.path.getsize(src)

    out_path = tmp_path / "out.pdf"
    dlg = pdfedit.CompressDialog(win, source_path=str(src), doc=win.view.doc)
    dlg.set_preset("low")
    dlg.set_output_path(str(out_path))

    monkeypatch.setattr(pdfedit.CompressDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "CompressDialog",
                        lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    win.open_compress_dialog()
    assert out_path.exists(), "compressed file not written"
    out_size = os.path.getsize(out_path)
    assert out_size < src_size, f"expected smaller output ({out_size} >= {src_size})"


# ---------------------------------------------------------------------------
# 2. Three quality presets each produce different file sizes
# ---------------------------------------------------------------------------
def test_three_presets_produce_different_files(main_window, tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    win = main_window
    src = tmp_path / "src.pdf"
    _make_pdf_with_image(src)

    # Hold a reference to the real class before any monkeypatching.
    real_dlg_cls = pdfedit.CompressDialog
    monkeypatch.setattr(real_dlg_cls, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    _stub_msgbox(monkeypatch)

    # The factory the MainWindow will call. We rebuild a fresh dialog for
    # each iteration via a mutable holder.
    holder: dict = {}

    def _factory(*a, **kw):
        return holder["dlg"]

    monkeypatch.setattr(pdfedit, "CompressDialog", _factory)

    sizes: dict[str, int] = {}
    for preset in ("low", "medium", "high"):
        _open_in_window(win, src)
        out_path = tmp_path / f"out_{preset}.pdf"
        dlg = real_dlg_cls(win, source_path=str(src), doc=win.view.doc)
        dlg.set_preset(preset)
        dlg.set_output_path(str(out_path))
        holder["dlg"] = dlg
        win.open_compress_dialog()
        assert out_path.exists()
        sizes[preset] = os.path.getsize(out_path)

    # All three succeeded
    assert all(s > 0 for s in sizes.values())
    # All three differ from each other
    assert len(set(sizes.values())) == 3, f"expected distinct sizes, got {sizes}"
    # Low quality should be smallest, high should be largest (sanity)
    assert sizes["low"] < sizes["high"]


# ---------------------------------------------------------------------------
# 3. Replace original overwrites + reloads in editor
# ---------------------------------------------------------------------------
def test_replace_original_overwrites_and_reloads(main_window, tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    win = main_window
    src = tmp_path / "src.pdf"
    _make_pdf_with_image(src)
    _open_in_window(win, src)
    orig_size = os.path.getsize(src)

    dlg = pdfedit.CompressDialog(win, source_path=str(src), doc=win.view.doc)
    dlg.set_preset("low")
    dlg.set_output_mode(pdfedit.CompressDialog.OUTPUT_REPLACE)

    monkeypatch.setattr(pdfedit.CompressDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "CompressDialog", lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    win.open_compress_dialog()
    new_size = os.path.getsize(src)
    assert new_size < orig_size
    # Editor reloaded — path matches the source we just overwrote
    assert win.path == str(src)
    assert win.view.doc is not None
    assert len(win.view.doc) == 1


# ---------------------------------------------------------------------------
# 4. Save-as-new defaults to <stem>_compressed.pdf
# ---------------------------------------------------------------------------
def test_save_as_new_default_path(tmp_path):
    src = tmp_path / "myfile.pdf"
    _make_blank_pdf(src)
    doc = fitz.open(str(src))
    try:
        dlg = pdfedit.CompressDialog(None, source_path=str(src), doc=doc)
        expected = str(tmp_path / "myfile_compressed.pdf")
        assert dlg.output_path() == expected
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# 5. Cancel mid-compression discards partial output
# ---------------------------------------------------------------------------
def test_cancel_discards_partial_output(main_window, tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    win = main_window
    src = tmp_path / "src.pdf"
    _make_pdf_with_image(src, pages=3)
    _open_in_window(win, src)
    orig_bytes = src.read_bytes()

    out_path = tmp_path / "out.pdf"
    dlg = pdfedit.CompressDialog(win, source_path=str(src), doc=win.view.doc)
    dlg.set_preset("low")
    dlg.set_output_path(str(out_path))

    monkeypatch.setattr(pdfedit.CompressDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "CompressDialog", lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    # Force every cancel-check to return True so the apply loop bails out.
    from PyQt6.QtWidgets import QProgressDialog
    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: True)

    win.open_compress_dialog()

    assert not out_path.exists(), "cancel should not produce an output file"
    # Original untouched
    assert src.read_bytes() == orig_bytes


# ---------------------------------------------------------------------------
# 6. Estimate label is reasonable (positive savings on a doc with images)
# ---------------------------------------------------------------------------
def test_estimate_label_positive_for_image_doc(tmp_path):
    pytest.importorskip("PIL")
    src = tmp_path / "src.pdf"
    _make_pdf_with_image(src)
    doc = fitz.open(str(src))
    try:
        dlg = pdfedit.CompressDialog(None, source_path=str(src), doc=doc)
        dlg.set_preset("low")
        text = dlg.estimate_text()
        assert "Estimated size:" in text
        # Parse out percent
        import re
        m = re.search(r"\((-?\d+)% smaller\)", text)
        assert m is not None, f"expected '(...% smaller)' in {text!r}"
        pct = int(m.group(1))
        assert pct > 0, f"expected positive savings, got {pct}% in {text!r}"

        # Estimator function directly
        cur, proj = pdfedit._compress_estimate_image_bytes(doc, 40, 72)
        assert cur > 0
        assert proj < cur
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# 7. Doc with no images — compression still produces valid PDF
# ---------------------------------------------------------------------------
def test_compress_doc_with_no_images(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "blank.pdf"
    _make_blank_pdf(src, pages=2)
    _open_in_window(win, src)

    out_path = tmp_path / "out.pdf"
    dlg = pdfedit.CompressDialog(win, source_path=str(src), doc=win.view.doc)
    dlg.set_preset("medium")
    dlg.set_output_path(str(out_path))

    monkeypatch.setattr(pdfedit.CompressDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "CompressDialog", lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    win.open_compress_dialog()
    assert out_path.exists()
    d = fitz.open(str(out_path))
    try:
        assert len(d) == 2
    finally:
        d.close()


# ---------------------------------------------------------------------------
# 8. Output is a valid PDF that can be reopened with images intact
# ---------------------------------------------------------------------------
def test_output_pdf_valid_with_images(main_window, tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    win = main_window
    src = tmp_path / "src.pdf"
    _make_pdf_with_image(src, pages=2)
    _open_in_window(win, src)

    out_path = tmp_path / "out.pdf"
    dlg = pdfedit.CompressDialog(win, source_path=str(src), doc=win.view.doc)
    dlg.set_preset("medium")
    dlg.set_output_path(str(out_path))

    monkeypatch.setattr(pdfedit.CompressDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "CompressDialog", lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    win.open_compress_dialog()

    assert out_path.exists()
    d = fitz.open(str(out_path))
    try:
        assert len(d) == 2
        # First page should still have at least one image
        imgs = d[0].get_images(full=True)
        assert len(imgs) >= 1
        # Render first page — proves the stream is decodable
        pix = d[0].get_pixmap()
        assert pix.width > 0 and pix.height > 0
    finally:
        d.close()


# ---------------------------------------------------------------------------
# 9. Preset settings sanity
# ---------------------------------------------------------------------------
def test_preset_settings():
    assert pdfedit.COMPRESS_PRESETS["low"] == (40, 72)
    assert pdfedit.COMPRESS_PRESETS["medium"] == (65, 150)
    assert pdfedit.COMPRESS_PRESETS["high"] == (85, None)


# ---------------------------------------------------------------------------
# 10. CompressDialog without a source_path (unsaved doc) still constructs
# ---------------------------------------------------------------------------
def test_dialog_without_source_path():
    doc = fitz.open()
    doc.new_page(width=400, height=400)
    try:
        dlg = pdfedit.CompressDialog(None, source_path=None, doc=doc)
        # No source path → no default new-file path
        assert dlg.output_path() == ""
        assert dlg.preset_key() == "medium"
    finally:
        doc.close()
