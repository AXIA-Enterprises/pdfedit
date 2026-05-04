"""Tests for file-operation correctness and save-path safety.

Covers password-protected PDFs, multi-file drop UX, recent-file dedup,
extract-with-overlays, full-bake-failure refusal, and the visible
"clear recent" affordance.
"""

from __future__ import annotations

import os
import sys

import fitz
import pytest
from PyQt6.QtCore import QMimeData, QPoint, QPointF, QSettings, QUrl, Qt
from PyQt6.QtGui import QDropEvent

from conftest import install_doc, make_blank_doc

import pdfedit


# ---------------------------------------------------------------------------
# Shared isolation
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_settings(main_window, tmp_path, monkeypatch):
    ini_path = tmp_path / "pdfedit.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)
    monkeypatch.setattr(
        pdfedit.MainWindow, "_recent_settings", lambda self: settings
    )
    settings.setValue("recent_files", [])
    yield settings
    settings.setValue("recent_files", [])


def _make_encrypted_pdf(path, user_pw="secret", owner_pw="ownersecret"):
    doc = fitz.open()
    doc.new_page(width=612.0, height=792.0)
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw=owner_pw,
        user_pw=user_pw,
    )
    doc.close()


# ---------------------------------------------------------------------------
# 1. Password-protected PDFs
# ---------------------------------------------------------------------------
def test_open_password_protected_pdf_with_correct_password(
    main_window, tmp_path, monkeypatch
):
    win = main_window
    p = tmp_path / "locked.pdf"
    _make_encrypted_pdf(p, user_pw="letmein")

    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText",
        staticmethod(lambda *a, **kw: ("letmein", True)),
    )
    win.open_path(str(p))

    assert win.path == str(p)
    assert win.view.doc is not None
    assert len(win.view.doc) == 1


def test_open_password_protected_pdf_with_wrong_password_refuses(
    main_window, tmp_path, monkeypatch
):
    win = main_window
    p = tmp_path / "locked.pdf"
    _make_encrypted_pdf(p, user_pw="letmein")

    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText",
        staticmethod(lambda *a, **kw: ("nope", True)),
    )

    seen = {}

    def fake_warning(parent, title, body, *a, **kw):
        seen["title"] = title
        seen["body"] = body

    monkeypatch.setattr(
        pdfedit.QMessageBox, "warning", staticmethod(fake_warning)
    )

    win.open_path(str(p))

    assert win.path != str(p), "wrong password should not set the path"
    assert "wrong password" in seen.get("body", "").lower()


# ---------------------------------------------------------------------------
# 2. Recent-file dedup is case-insensitive (darwin/Windows only — case-
#    sensitive filesystems on Linux legitimately keep both entries)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    sys.platform not in ("darwin", "win32"),
    reason="recent-file dedup is only case-insensitive on case-insensitive filesystems",
)
def test_add_recent_dedups_case_insensitive(
    main_window, tmp_path, isolated_settings
):
    win = main_window
    p = tmp_path / "MixedCase.pdf"
    p.write_bytes(b"%PDF-1.4\n")

    upper = str(p)
    lower = str(p).replace("MixedCase", "mixedcase")

    win._add_recent(upper)
    win._add_recent(lower)

    stored = isolated_settings.value("recent_files", []) or []
    if isinstance(stored, str):
        stored = [stored]
    keys = {pdfedit.MainWindow._recent_key(s) for s in stored}
    assert len(stored) == 1, (
        f"expected 1 entry after case-insensitive dedup, got {stored!r}"
    )
    assert len(keys) == 1


# ---------------------------------------------------------------------------
# 3. _clear_recent immediately empties the visible menu
# ---------------------------------------------------------------------------
def test_clear_recent_refreshes_visible_menu(
    main_window, tmp_path, isolated_settings
):
    win = main_window
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    win._add_recent(str(p))
    # Populate the menu as the user would by opening it.
    win._populate_recent_menu()
    # Sanity: at least one real entry plus separator + Clear Menu.
    assert len(win.recent_menu.actions()) >= 1

    win._clear_recent()

    actions = win.recent_menu.actions()
    assert len(actions) == 1
    assert actions[0].text() == "(No recent files)"
    assert not actions[0].isEnabled()


# ---------------------------------------------------------------------------
# 4. extract_pages bakes overlays
# ---------------------------------------------------------------------------
def test_extract_pages_bakes_overlay_text(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc(pages=2))

    box = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=120, pdf_w=400,
        text="extract-me", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()

    out = tmp_path / "subset.pdf"
    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText",
        staticmethod(lambda *a, **kw: ("1", True)),
    )
    monkeypatch.setattr(
        pdfedit.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **kw: (str(out), "PDF Files (*.pdf)")),
    )

    win.extract_pages_dialog()

    assert out.exists()
    with fitz.open(str(out)) as doc:
        assert doc.page_count == 1
        text = doc[0].get_text()
    assert "extract-me" in text, (
        f"expected baked overlay text in extracted PDF, got {text!r}"
    )


# ---------------------------------------------------------------------------
# 5. Multi-file drop → "Merge all" sums page counts
# ---------------------------------------------------------------------------
def test_multi_file_drop_merge_all(main_window, tmp_path, monkeypatch):
    win = main_window
    # Need two distinct PDFs on disk.
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    da = fitz.open()
    da.new_page(width=612, height=792)
    da.new_page(width=612, height=792)
    da.save(str(a))
    da.close()
    db = fitz.open()
    db.new_page(width=612, height=792)
    db.new_page(width=612, height=792)
    db.new_page(width=612, height=792)
    db.save(str(b))
    db.close()

    # Patch QMessageBox.exec on the multi-file dialog so the user "clicks"
    # the Merge all button — record that button on the box via a small
    # wrapper around addButton.
    captured = {}
    real_add_button = pdfedit.QMessageBox.addButton

    def fake_add_button(self, *args, **kwargs):
        result = real_add_button(self, *args, **kwargs)
        if args and isinstance(args[0], str) and args[0].startswith("Merge"):
            captured["merge_btn"] = result
        return result

    monkeypatch.setattr(pdfedit.QMessageBox, "addButton", fake_add_button)
    monkeypatch.setattr(pdfedit.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(
        pdfedit.QMessageBox, "clickedButton",
        lambda self: captured.get("merge_btn"),
    )
    # win.dirty must be False so _confirm_discard_changes returns True.
    win.dirty = False

    # Build a fake QDropEvent with two URLs.
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(a)), QUrl.fromLocalFile(str(b))])
    ev = QDropEvent(
        QPointF(0, 0),
        Qt.DropAction.CopyAction,
        md,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    win.dropEvent(ev)

    assert win.view.doc is not None
    assert len(win.view.doc) == 5, (
        f"expected 2+3=5 pages after merge-all, got {len(win.view.doc)}"
    )


# ---------------------------------------------------------------------------
# 6. _save_clone_atomic full-fail returns False
# ---------------------------------------------------------------------------
def test_save_clone_atomic_full_fail_refuses(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    box = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=72, pdf_w=400,
        text="will-all-fail", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()

    def boom(self, page):
        raise RuntimeError("synthetic full bake failure")

    monkeypatch.setattr(pdfedit.TextBoxItem, "to_pdf", boom)

    seen = {}

    def fake_critical(parent, title, body, *a, **kw):
        seen["title"] = title
        seen["body"] = body

    monkeypatch.setattr(
        pdfedit.QMessageBox, "critical", staticmethod(fake_critical)
    )

    out = tmp_path / "fail.pdf"
    win.dirty = True
    ok = win._save_clone_atomic(str(out))

    assert ok is False, "full-fail must refuse to claim success"
    assert not out.exists(), "no file should be written when every overlay fails"
    assert win.dirty is True, "doc should remain dirty when save is refused"
    assert "Save aborted" == seen.get("title")


# Drive save_pdf_as through the full-fail path: it should not mark clean.
def test_save_pdf_as_full_fail_keeps_dirty(main_window, tmp_path, monkeypatch):
    win = main_window
    install_doc(win, make_blank_doc())
    box = pdfedit.TextBoxItem(
        win.view, page_idx=0, pdf_x=72, pdf_y=72, pdf_w=400,
        text="will-fail", family="Helvetica", size_pt=18,
    )
    win.view.overlays.append(box)
    win.view.scene_.addItem(box)
    box.refresh()

    def boom(self, page):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(pdfedit.TextBoxItem, "to_pdf", boom)
    monkeypatch.setattr(
        pdfedit.QMessageBox, "critical", staticmethod(lambda *a, **kw: None)
    )

    out = tmp_path / "fail2.pdf"
    monkeypatch.setattr(
        pdfedit.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **kw: (str(out), "PDF Files (*.pdf)")),
    )

    win.dirty = True
    win.save_pdf_as()

    assert win.dirty is True, "save must not mark clean when every overlay fails"
    assert win.path is None or win.path != str(out)
