"""Tests for SplitPdfDialog and the split-PDF flow."""

from __future__ import annotations

import os
from pathlib import Path

import fitz
import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import install_doc, make_blank_doc

import pdfedit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _save_doc(doc, path: str) -> None:
    doc.save(path, garbage=4, deflate=True)


def _make_n_page_pdf(path: Path, pages: int) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=612.0, height=792.0)
    _save_doc(doc, str(path))
    doc.close()


def _make_pdf_with_bookmarks(path: Path, pages: int, toc: list) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=612.0, height=792.0)
    doc.set_toc(toc)
    _save_doc(doc, str(path))
    doc.close()


def _open_in_window(win, source_path: Path) -> None:
    doc = fitz.open(str(source_path))
    install_doc(win, doc)
    win.path = str(source_path)


# ---------------------------------------------------------------------------
# 1. By page ranges
# ---------------------------------------------------------------------------
def test_by_page_ranges_produces_two_files(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_n_page_pdf(src, 5)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=5, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("1-2, 3-5")
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("part_{n}.pdf")
    dlg.open_when_done.setChecked(False)

    monkeypatch.setattr(pdfedit.SplitPdfDialog, "exec",
                        lambda self: pdfedit.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(pdfedit, "SplitPdfDialog",
                        lambda *a, **kw: dlg)
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win.open_split_dialog()

    files = sorted(out_dir.glob("*.pdf"))
    assert len(files) == 2
    d1 = fitz.open(str(files[0]))
    d2 = fitz.open(str(files[1]))
    try:
        assert len(d1) == 2
        assert len(d2) == 3
    finally:
        d1.close()
        d2.close()


# ---------------------------------------------------------------------------
# 2. Every N pages
# ---------------------------------------------------------------------------
def test_every_n_chunks_correctly():
    dlg = pdfedit.SplitPdfDialog(
        None, page_count=17, toc=[], source_path="/tmp/doc.pdf"
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_EVERY_N)
    dlg.set_every_n(5)
    chunks, warnings = dlg._collect_chunks()
    assert len(chunks) == 4
    sizes = [end - start + 1 for start, end, _ in chunks]
    assert sizes == [5, 5, 5, 2]
    assert warnings == []


def test_every_n_writes_four_files(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "doc17.pdf"
    _make_n_page_pdf(src, 17)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=17, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_EVERY_N)
    dlg.set_every_n(5)
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("{stem}_part_{n}.pdf")
    dlg.open_when_done.setChecked(False)

    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)

    files = sorted(out_dir.glob("*.pdf"))
    assert len(files) == 4
    counts = []
    for f in files:
        d = fitz.open(str(f))
        counts.append(len(d))
        d.close()
    assert sorted(counts) == [2, 5, 5, 5]


# ---------------------------------------------------------------------------
# 3. By bookmarks
# ---------------------------------------------------------------------------
def test_by_bookmarks_three_chunks(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "bm.pdf"
    toc = [
        [1, "Chapter A", 1],
        [1, "Chapter B", 5],
        [1, "Chapter C", 9],
    ]
    _make_pdf_with_bookmarks(src, 15, toc)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    real_toc = win.view.doc.get_toc(simple=True)
    dlg = pdfedit.SplitPdfDialog(
        win, page_count=15, toc=real_toc, source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_BOOKMARKS)
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("ch_{n}.pdf")
    dlg.open_when_done.setChecked(False)

    chunks, warnings = dlg._collect_chunks()
    sizes = [end - start + 1 for start, end, _ in chunks]
    assert sizes == [4, 4, 7]

    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)
    files = sorted(out_dir.glob("*.pdf"))
    assert len(files) == 3


# ---------------------------------------------------------------------------
# 4. Empty bookmark list disables the option
# ---------------------------------------------------------------------------
def test_empty_bookmarks_disables_option(qtbot):
    dlg = pdfedit.SplitPdfDialog(
        None, page_count=10, toc=[], source_path="/tmp/x.pdf"
    )
    qtbot.addWidget(dlg)
    assert not dlg.rb_bookmarks.isEnabled()
    assert "no bookmarks" in dlg.bookmark_hint.text().lower()
    with pytest.raises(ValueError):
        dlg.set_mode(pdfedit.SplitPdfDialog.MODE_BOOKMARKS)


def test_nonempty_bookmarks_enables_option(qtbot):
    toc = [[1, "A", 1], [1, "B", 4]]
    dlg = pdfedit.SplitPdfDialog(
        None, page_count=10, toc=toc, source_path="/tmp/x.pdf"
    )
    qtbot.addWidget(dlg)
    assert dlg.rb_bookmarks.isEnabled()


# ---------------------------------------------------------------------------
# 5. Filename template tokens
# ---------------------------------------------------------------------------
def test_filename_template_tokens(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "report.pdf"
    _make_n_page_pdf(src, 6)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=6, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("1-2, 3-4, 5-6")
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("{stem}_n{n}_p{first}-{last}.pdf")
    dlg.open_when_done.setChecked(False)

    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)

    names = sorted(p.name for p in out_dir.glob("*.pdf"))
    assert names == [
        "report_n1_p1-2.pdf",
        "report_n2_p3-4.pdf",
        "report_n3_p5-6.pdf",
    ]


# ---------------------------------------------------------------------------
# 6. Title token sanitization in bookmark mode
# ---------------------------------------------------------------------------
def test_filename_template_title_sanitized(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "bm2.pdf"
    toc = [
        [1, "Intro/Setup", 1],
        [1, 'Q&A: "Why?" <2024>', 4],
    ]
    _make_pdf_with_bookmarks(src, 6, toc)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    real_toc = win.view.doc.get_toc(simple=True)
    dlg = pdfedit.SplitPdfDialog(
        win, page_count=6, toc=real_toc, source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_BOOKMARKS)
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("{n}_{title}.pdf")
    dlg.open_when_done.setChecked(False)

    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)
    names = sorted(p.name for p in out_dir.glob("*.pdf"))
    for n in names:
        for bad in '\\/:*?"<>|':
            assert bad not in n
    assert any("IntroSetup" in n for n in names)
    assert any("Q&A" in n for n in names)


# ---------------------------------------------------------------------------
# 7. Overlapping ranges produce a warning
# ---------------------------------------------------------------------------
def test_overlapping_ranges_warning():
    chunks, warnings = pdfedit.parse_split_ranges("1-3, 2-5", 10)
    assert "overlapping ranges" in warnings


def test_overlapping_ranges_via_dialog():
    dlg = pdfedit.SplitPdfDialog(
        None, page_count=10, toc=[], source_path="/tmp/d.pdf"
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("1-3, 2-5")
    chunks, warnings = dlg._collect_chunks()
    assert "overlapping ranges" in warnings


def test_overlapping_ranges_run_split_rejected(
    main_window, tmp_path, monkeypatch
):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_n_page_pdf(src, 10)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=10, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("1-3, 2-5")
    dlg.set_output_folder(str(out_dir))
    dlg.open_when_done.setChecked(False)

    captured = {}

    def fake_warn(parent, title, text, *a, **kw):
        captured["text"] = text
        return 0

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warn))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)

    assert "Overlapping" in captured.get("text", "") or "overlapping" in captured.get("text", "").lower()
    assert list(out_dir.glob("*.pdf")) == []


# ---------------------------------------------------------------------------
# 8. Cancel mid-split via QProgressDialog
# ---------------------------------------------------------------------------
def test_cancel_mid_split(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "long.pdf"
    _make_n_page_pdf(src, 20)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=20, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_EVERY_N)
    dlg.set_every_n(2)
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("part_{n}.pdf")
    dlg.open_when_done.setChecked(False)

    info_calls = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: info_calls.append(a) or 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    real_was_canceled = pdfedit.QProgressDialog.wasCanceled
    state = {"calls": 0}

    def fake_was_canceled(self):
        state["calls"] += 1
        return state["calls"] > 3

    monkeypatch.setattr(
        pdfedit.QProgressDialog, "wasCanceled", fake_was_canceled
    )

    win._run_split(dlg)

    files = list(out_dir.glob("*.pdf"))
    assert 0 < len(files) < 10
    assert any("Cancelled" in str(call) for call in info_calls)


# ---------------------------------------------------------------------------
# 9. Output filename collision -> overwrite prompt
# ---------------------------------------------------------------------------
def test_collision_triggers_overwrite_prompt(
    main_window, tmp_path, monkeypatch
):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_n_page_pdf(src, 4)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "part_1.pdf"
    existing.write_bytes(b"%PDF-1.4\n%stub\n")
    pre_size = existing.stat().st_size

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=4, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("1-2, 3-4")
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("part_{n}.pdf")
    dlg.open_when_done.setChecked(False)

    asked = {"count": 0}

    def fake_question(parent, title, text, btns, *a, **kw):
        asked["count"] += 1
        return QMessageBox.StandardButton.YesToAll

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)

    assert asked["count"] == 1
    assert existing.exists()
    assert existing.stat().st_size != pre_size  # overwritten with real PDF


def test_collision_no_skips_file(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_n_page_pdf(src, 4)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "part_1.pdf"
    existing.write_bytes(b"original")
    pre_bytes = existing.read_bytes()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=4, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("1-2, 3-4")
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("part_{n}.pdf")
    dlg.open_when_done.setChecked(False)

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(
            lambda *a, **kw: QMessageBox.StandardButton.No
        ),
    )
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)

    assert existing.read_bytes() == pre_bytes
    assert (out_dir / "part_2.pdf").exists()


# ---------------------------------------------------------------------------
# 10. Outputs are valid PDFs
# ---------------------------------------------------------------------------
def test_outputs_are_valid_pdfs(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_n_page_pdf(src, 8)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=8, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_EVERY_N)
    dlg.set_every_n(3)
    dlg.set_output_folder(str(out_dir))
    dlg.set_filename_template("seg_{n}.pdf")
    dlg.open_when_done.setChecked(False)

    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)

    files = sorted(out_dir.glob("*.pdf"))
    assert len(files) == 3
    total = 0
    for f in files:
        d = fitz.open(str(f))
        try:
            assert len(d) > 0
            total += len(d)
        finally:
            d.close()
    assert total == 8


# ---------------------------------------------------------------------------
# Validation: empty input rejected
# ---------------------------------------------------------------------------
def test_empty_ranges_rejected(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_n_page_pdf(src, 5)
    _open_in_window(win, src)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=5, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("")
    dlg.set_output_folder(str(out_dir))
    dlg.open_when_done.setChecked(False)

    captured = {}
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **kw: captured.setdefault("warned", a)),
    )
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)
    assert "warned" in captured
    assert list(out_dir.glob("*.pdf")) == []


# ---------------------------------------------------------------------------
# Validation: bad output folder rejected
# ---------------------------------------------------------------------------
def test_bad_output_folder_rejected(main_window, tmp_path, monkeypatch):
    win = main_window
    src = tmp_path / "src.pdf"
    _make_n_page_pdf(src, 4)
    _open_in_window(win, src)

    dlg = pdfedit.SplitPdfDialog(
        win, page_count=4, toc=[], source_path=str(src)
    )
    dlg.set_mode(pdfedit.SplitPdfDialog.MODE_RANGES)
    dlg.set_range_text("1-2, 3-4")
    dlg.set_output_folder(str(tmp_path / "does_not_exist"))
    dlg.open_when_done.setChecked(False)

    captured = {}
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **kw: captured.setdefault("warned", a) or 0),
    )
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: 0))

    win._run_split(dlg)
    assert "warned" in captured


# ---------------------------------------------------------------------------
# Action wiring
# ---------------------------------------------------------------------------
def test_split_action_present_in_file_menu(main_window):
    win = main_window
    assert hasattr(win, "act_split")
    assert win.act_split.text() == "Split…"

    file_menu = None
    for spec in win._menu_spec:
        if spec[0] == "&File":
            file_menu = spec[1]
            break
    assert file_menu is not None
    assert win.act_split in file_menu


# ---------------------------------------------------------------------------
# sanitize_filename helper
# ---------------------------------------------------------------------------
def test_sanitize_filename_strips_illegal_chars():
    assert pdfedit.sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"
    assert pdfedit.sanitize_filename("  spaced   out  ") == "spaced out"
    assert pdfedit.sanitize_filename("") == "untitled"
    assert pdfedit.sanitize_filename("///") == "untitled"


# ---------------------------------------------------------------------------
# parse_split_ranges helper
# ---------------------------------------------------------------------------
def test_parse_split_ranges_basic():
    chunks, warnings = pdfedit.parse_split_ranges("1-3, 5, 7-9", 10)
    assert chunks == [(0, 2), (4, 4), (6, 8)]
    assert "overlapping ranges" not in warnings


def test_parse_split_ranges_open_ended():
    chunks, _ = pdfedit.parse_split_ranges("1-", 5)
    assert chunks == [(0, 4)]
    chunks, _ = pdfedit.parse_split_ranges("-3", 5)
    assert chunks == [(0, 2)]


def test_parse_split_ranges_empty():
    chunks, warnings = pdfedit.parse_split_ranges("", 10)
    assert chunks == []
    assert warnings == []


def test_parse_split_ranges_invalid():
    with pytest.raises(ValueError):
        pdfedit.parse_split_ranges("abc", 10)
