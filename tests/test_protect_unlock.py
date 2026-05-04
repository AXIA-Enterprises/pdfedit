"""Tests for the Protect PDF / Unlock PDF flows."""

from __future__ import annotations

import os
from pathlib import Path

import fitz
import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox

from conftest import install_doc

import pdfedit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_blank_pdf(path: Path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=612.0, height=792.0)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()


def _make_encrypted_pdf(path: Path, *, user_pw: str = "userpass",
                        owner_pw: str = "ownerpass") -> None:
    doc = fitz.open()
    doc.new_page(width=612.0, height=792.0)
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw=owner_pw,
        user_pw=user_pw,
    )
    doc.close()


def _open_in_window(win, source_path: Path) -> None:
    doc = fitz.open(str(source_path))
    install_doc(win, doc)
    win.path = str(source_path)


def _open_encrypted_in_window(win, source_path: Path, password: str,
                               monkeypatch) -> None:
    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText",
        staticmethod(lambda *a, **kw: (password, True)),
    )
    win.open_path(str(source_path))


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
# 1. Protect: encrypt with owner+user passwords, reopen needs_pass
# ---------------------------------------------------------------------------
def test_protect_writes_encrypted_pdf(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_blank_pdf(src)
    _open_in_window(win, src)

    out_path = tmp_path / "out_protected.pdf"
    dlg = pdfedit.ProtectPdfDialog(win, source_path=str(src))
    dlg.owner_edit.setText("ownerpw")
    dlg.owner_confirm.setText("ownerpw")
    dlg.user_edit.setText("userpw")
    dlg.user_confirm.setText("userpw")
    dlg.set_output_path(str(out_path))

    monkeypatch.setattr(pdfedit.ProtectPdfDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "ProtectPdfDialog",
                        lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    win.open_protect_dialog()

    assert out_path.exists()
    reopened = fitz.open(str(out_path))
    try:
        assert reopened.needs_pass
        assert reopened.authenticate("userpw")
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# 2. Permissions: revoking copy strips PDF_PERM_COPY in saved file
# ---------------------------------------------------------------------------
def test_protect_permissions_no_copy(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_blank_pdf(src)
    _open_in_window(win, src)

    out_path = tmp_path / "out_nocopy.pdf"
    dlg = pdfedit.ProtectPdfDialog(win, source_path=str(src))
    dlg.owner_edit.setText("ownerpw")
    dlg.owner_confirm.setText("ownerpw")
    dlg.user_edit.setText("userpw")
    dlg.user_confirm.setText("userpw")
    dlg.set_permission("copy", False)
    dlg.set_output_path(str(out_path))

    monkeypatch.setattr(pdfedit.ProtectPdfDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "ProtectPdfDialog",
                        lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    win.open_protect_dialog()

    reopened = fitz.open(str(out_path))
    try:
        assert reopened.needs_pass
        ok = reopened.authenticate("userpw")
        assert ok
        assert (reopened.permissions & pdfedit.PDF_PERM_COPY) == 0
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# 3. Mismatched passwords block accept
# ---------------------------------------------------------------------------
def test_protect_dialog_mismatched_passwords_block_accept(qtbot):
    dlg = pdfedit.ProtectPdfDialog(None, source_path="/tmp/whatever.pdf")
    qtbot.addWidget(dlg)

    dlg.owner_edit.setText("ownerpw")
    dlg.owner_confirm.setText("WRONG")
    dlg.user_edit.setText("")
    dlg.user_confirm.setText("")

    err = dlg.validation_error()
    assert err  # non-empty
    assert "owner" in err.lower()

    # _on_accept should not call accept() — the dialog should still be visible
    # (we use isAccepted check via result()).
    dlg._on_accept()
    assert dlg.result() != QDialog.DialogCode.Accepted

    # Fix owner, mismatch user
    dlg.owner_confirm.setText("ownerpw")
    dlg.user_edit.setText("userpw")
    dlg.user_confirm.setText("nope")
    err2 = dlg.validation_error()
    assert err2
    assert "user" in err2.lower()
    dlg._on_accept()
    assert dlg.result() != QDialog.DialogCode.Accepted

    # Empty owner is also invalid
    dlg.owner_edit.setText("")
    dlg.owner_confirm.setText("")
    err3 = dlg.validation_error()
    assert err3
    assert "owner" in err3.lower()


# ---------------------------------------------------------------------------
# 4. Default output path is <stem>_protected.pdf
# ---------------------------------------------------------------------------
def test_protect_default_output_naming(qtbot, tmp_path):
    src = tmp_path / "report.pdf"
    src.write_bytes(b"")  # actual content irrelevant; only path stem matters
    dlg = pdfedit.ProtectPdfDialog(None, source_path=str(src))
    qtbot.addWidget(dlg)
    out = dlg.output_path()
    assert out.endswith("report_protected.pdf")


# ---------------------------------------------------------------------------
# 5. Replace original: writes back to original path; reopen prompts for pw
# ---------------------------------------------------------------------------
def test_protect_replace_original_and_reload(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_blank_pdf(src)
    _open_in_window(win, src)

    dlg = pdfedit.ProtectPdfDialog(win, source_path=str(src))
    dlg.owner_edit.setText("ownerpw")
    dlg.owner_confirm.setText("ownerpw")
    # Set a user pw so the reopen actually prompts (owner-only encryption
    # is auto-authenticated by readers).
    dlg.user_edit.setText("userpw")
    dlg.user_confirm.setText("userpw")
    dlg.set_output_mode(pdfedit.ProtectPdfDialog.OUTPUT_REPLACE)

    monkeypatch.setattr(pdfedit.ProtectPdfDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "ProtectPdfDialog",
                        lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    # The replace flow calls open_path which (because the file is now
    # encrypted) prompts for a password. Provide the owner pw.
    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText",
        staticmethod(lambda *a, **kw: ("ownerpw", True)),
    )

    win.open_protect_dialog()

    # Original file overwritten with encrypted variant
    reopened = fitz.open(str(src))
    try:
        assert reopened.needs_pass
        assert reopened.authenticate("ownerpw")
    finally:
        reopened.close()

    # Editor reloaded the protected file
    assert win.path == str(src)
    assert win.view.doc is not None
    # was_encrypted now True so Unlock should be enabled
    assert win.view.was_encrypted
    assert win.act_unlock.isEnabled()


# ---------------------------------------------------------------------------
# 6. Unlock: encrypt → open → unlock → reopen unencrypted
# ---------------------------------------------------------------------------
def test_unlock_writes_unencrypted_copy(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "locked.pdf"
    _make_encrypted_pdf(src, user_pw="userpw", owner_pw="ownerpw")

    _open_encrypted_in_window(win, src, "userpw", monkeypatch)
    assert win.view.doc is not None
    assert win.view.was_encrypted
    assert win.act_unlock.isEnabled()

    out_path = tmp_path / "unlocked.pdf"
    dlg = pdfedit.UnlockPdfDialog(win, source_path=str(src))
    dlg.set_output_path(str(out_path))

    monkeypatch.setattr(pdfedit.UnlockPdfDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "UnlockPdfDialog",
                        lambda *a, **kw: dlg)
    _stub_msgbox(monkeypatch)

    win.open_unlock_dialog()

    assert out_path.exists()
    reopened = fitz.open(str(out_path))
    try:
        assert not reopened.needs_pass
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# 7. Unlock disabled when doc is not protected
# ---------------------------------------------------------------------------
def test_unlock_disabled_on_unprotected_doc(main_window, tmp_path):
    win = main_window
    src = tmp_path / "plain.pdf"
    _make_blank_pdf(src)
    _open_in_window(win, src)
    # _open_in_window uses install_doc, which doesn't set was_encrypted.
    win.view.was_encrypted = False
    win._refresh_protect_actions()

    assert not win.act_unlock.isEnabled()
    assert "not protected" in win.act_unlock.toolTip().lower()


# ---------------------------------------------------------------------------
# 8. Unlock with wrong password is handled gracefully (no output written).
# ---------------------------------------------------------------------------
def test_unlock_with_wrong_password_fails_gracefully(
    main_window, tmp_path, monkeypatch
):
    win = main_window
    src = tmp_path / "locked.pdf"
    _make_encrypted_pdf(src, user_pw="userpw", owner_pw="ownerpw")

    # Wrong password: open_path should refuse and the doc should not be
    # installed. Capture any QMessageBox.warning so the test doesn't block.
    monkeypatch.setattr(
        pdfedit.QInputDialog, "getText",
        staticmethod(lambda *a, **kw: ("WRONG", True)),
    )
    _stub_msgbox(monkeypatch)

    out_path = tmp_path / "unlocked.pdf"
    # No file should be created since open_path will fail.
    win.open_path(str(src))

    # Either the doc didn't load, or it did and was_encrypted is False (it
    # never reached the success branch). Verify Unlock cannot run because
    # the doc isn't authenticated/installed as encrypted.
    if win.view.doc is None:
        # Open was rejected — Unlock would also be a no-op. Confirm act
        # is still in disabled state.
        assert not win.act_unlock.isEnabled()
    else:
        # Unexpected: doc loaded with wrong password — fail loudly.
        pytest.fail("PDFView.load accepted the wrong password")

    assert not out_path.exists()
