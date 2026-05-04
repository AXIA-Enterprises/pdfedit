"""Shared pytest fixtures for the pdfedit smoke test suite.

The `qapp_cls` fixture tells pytest-qt to use `_PDFApp` (pdfedit's
QApplication subclass) so that macOS FileOpen events get routed correctly,
matching how the app actually runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Run Qt headlessly when DISPLAY is missing (CI). On macOS the cocoa
# platform works fine without a display in pytest-qt's offscreen mode too.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Make the project importable regardless of where pytest is invoked from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pdfedit  # noqa: E402

# Phase 5: do_form_* now auto-opens FieldPropertiesDialog after creation,
# which calls .exec() and blocks under the offscreen test platform. Patch
# at module load time (before any test class is collected) so even tests
# that don't use the autouse fixture get a non-blocking dialog.
from PyQt6.QtWidgets import QDialog as _QDialog  # noqa: E402

_original_field_props_exec = pdfedit.FieldPropertiesDialog.exec
pdfedit.FieldPropertiesDialog.exec = lambda self: _QDialog.DialogCode.Rejected

# do_add_text now opens AddTextDialog before creating a TextBoxItem. Under the
# offscreen platform .exec() blocks forever; auto-accept so existing tests
# continue to drive the textbox flow without prompting.
_original_add_text_exec = pdfedit.AddTextDialog.exec
pdfedit.AddTextDialog.exec = lambda self: _QDialog.DialogCode.Accepted


@pytest.fixture(scope="session", autouse=True)
def _disable_unsaved_changes_dialog():
    """Disable the modal "Unsaved changes" QMessageBox in tests.

    pytest-qt's teardown calls close() on every registered widget, which
    triggers MainWindow.closeEvent → QMessageBox.question(...). Under the
    offscreen platform the modal blocks forever (no event loop is running
    at teardown time and there is no human to click the button), so the
    pytest process hangs after the last test passes.

    We override the class method for the whole session.
    """
    original = pdfedit.MainWindow.closeEvent
    pdfedit.MainWindow.closeEvent = lambda self, ev: ev.accept()
    yield
    pdfedit.MainWindow.closeEvent = original




@pytest.fixture(scope="session")
def qapp_cls():
    """Tell pytest-qt to instantiate _PDFApp instead of plain QApplication."""
    return pdfedit._PDFApp


def pytest_sessionfinish(session, exitstatus):
    """Force-exit at session end.

    pytest-qt + PyQt6 + pymupdf hold cross-references that occasionally keep
    the interpreter alive past the test summary on macOS (the QApplication
    sticks around with no event loop running but with windows that the
    cocoa layer still owns). The tests have already run by this point;
    just bail out so the shell can move on.
    """
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            for w in list(app.topLevelWidgets()):
                try:
                    w.close()
                    w.deleteLater()
                except Exception:
                    pass
            app.processEvents()
            app.quit()
    except Exception:
        pass


def pytest_unconfigure(config):
    """Hard-exit AFTER pytest has written the summary footer.

    This runs after pytest_sessionfinish, after the terminal summary, after
    all reporting plugins. Without this, PyQt6 + pymupdf hold the
    interpreter alive past `sys.exit` on macOS and the shell never sees the
    return code.
    """
    # Flush stdout/stderr so the summary line (e.g. "1 passed in 0.5s") makes
    # it to the terminal before we hard-exit.
    import sys as _sys
    try:
        _sys.stdout.flush()
        _sys.stderr.flush()
    except Exception:
        pass
    # exitstatus is stashed by pytest on the config object via session.
    # We use the testsfailed count from the session if present.
    code = 1 if getattr(config, "_test_failed", False) else 0
    # Fallback: look at terminal reporter stats.
    try:
        tr = config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            stats = getattr(tr, "stats", {})
            if stats.get("failed") or stats.get("error"):
                code = 1
    except Exception:
        pass
    os._exit(code)


@pytest.fixture
def main_window(qtbot):
    """Build a fresh MainWindow for each test and register it with qtbot.

    Yields the window then clears the dirty flag and detaches close-event
    confirmation. Otherwise pytest-qt's teardown calls close() and our
    closeEvent pops a modal QMessageBox asking to save unsaved changes,
    which blocks forever in the offscreen test harness.
    """
    win = pdfedit.MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    # Make sure teardown cannot trigger the unsaved-changes modal.
    win.dirty = False
    # Stub out closeEvent so even an accidental dirty=True path can't pop a
    # modal during pytest-qt teardown.
    win.closeEvent = lambda ev: ev.accept()
    # Detach overlays + close any loaded fitz document so GC can run cleanly.
    try:
        win.view.overlays.clear()
        if win.view.doc is not None:
            win.view.doc.close()
            win.view.doc = None
        win.view.scene_.clear()
    except Exception:
        pass


def make_blank_doc(width_pt: float = 612.0, height_pt: float = 792.0, pages: int = 1):
    """Build an in-memory PDF without invoking the NewPDFDialog.

    Uses the same logic that MainWindow.new_pdf would have run after the
    dialog was accepted, so we exercise the same fitz code paths.
    """
    import fitz  # imported lazily so the conftest can be collected w/o fitz

    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width_pt, height=height_pt)
    return doc


def rename_last_widget(win, page_idx: int, new_name: str) -> None:
    """Rename the most recently added widget on `page_idx` to `new_name`.

    Many phase 1-4 tests created a field then asserted on a specific
    field_name. After phase 5's auto-naming refactor, do_form_* picks
    a unique default like "Text_1"; tests that need a specific name
    call this helper after each do_form_* to set it.
    """
    page = win.view.doc[page_idx]
    widgets = list(page.widgets())
    if not widgets:
        raise AssertionError(f"no widgets on page {page_idx} to rename")
    w = widgets[-1]
    w.field_name = new_name
    w.update()


def install_doc(win, doc):
    """Attach a fitz.Document to a MainWindow as if the user had run New PDF."""
    if win.view.doc:
        win.view.doc.close()
    win.view.doc = doc
    win.view.page_idx = 0
    win.view.render_all()
    win.path = None
    win.dirty = True
    win._refresh_title()
    win._refresh_page_label()


def scene_to_viewport(view, scene_pt):
    """Map a QPointF in scene coords to a QPoint in viewport coords."""
    return view.mapFromScene(scene_pt)
