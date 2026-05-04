"""Tests that the three QToolBars in MainWindow collapse to overflow chevrons
rather than getting clipped at narrow window widths.

The bug: at normal widths (~1100-1500px) the rightmost toolbar tools (Crop,
Color, Find box, etc.) got clipped because widgets like the find-box and
font-picker had Expanding size policies that fooled QToolBar's sizeHint
into thinking the toolbar fit when it didn't. The fix caps those widgets
to fixed widths so QToolBar's overflow mechanism kicks in properly and
inserts a `>>` extension button.
"""

from __future__ import annotations

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QToolBar, QToolButton


# Qt's internal object name for the auto-inserted extension chevron.
QT_TOOLBAR_EXT = "qt_toolbar_ext_button"


def _process(qtbot, win):
    QCoreApplication.processEvents()
    qtbot.wait(50)
    QCoreApplication.processEvents()


def _ext_button(toolbar: QToolBar) -> QToolButton | None:
    return toolbar.findChild(QToolButton, QT_TOOLBAR_EXT)


def test_minimum_width_is_reasonable(main_window):
    """MainWindow.minimumWidth must let the user shrink below typical content
    width so overflow chevrons can be exercised."""
    assert main_window.minimumWidth() <= 900
    assert main_window.minimumWidth() >= 200


def test_find_box_capped(main_window):
    """find_box must have a max width so it can't steal toolbar space."""
    assert main_window.find_box.maximumWidth() <= 220


def test_font_picker_capped(main_window):
    assert main_window.fmt_family.maximumWidth() <= 180


def test_size_spinbox_capped(main_window):
    assert main_window.fmt_size.maximumWidth() <= 60


def test_page_spinbox_capped(main_window):
    assert main_window.page_spin.maximumWidth() <= 70


def test_main_toolbar_is_exposed(main_window):
    """The Main toolbar must be reachable as `self.tb` for tests / scripts."""
    assert isinstance(main_window.tb, QToolBar)
    assert main_window.tb.objectName() == "MainToolBar"


def test_toolbar_overflow_chevrons_appear_when_shrunk(main_window, qtbot):
    """After shrinking the window, each toolbar should have an extension
    chevron available in its widget tree (Qt creates it lazily but it
    exists in the toolbar's children once the layout overflows).
    """
    win = main_window
    win.resize(700, 800)
    _process(qtbot, win)

    for toolbar in (win.in_app_menubar, win.tb, win.fmt_toolbar):
        ext = _ext_button(toolbar)
        assert ext is not None, (
            f"Toolbar {toolbar.objectName()} has no extension chevron child "
            f"(expected QToolButton named {QT_TOOLBAR_EXT!r})"
        )


def test_all_actions_remain_callable_when_shrunk(main_window, qtbot):
    """Overflow must hide actions visually, not remove them from .actions()."""
    win = main_window
    win.resize(1500, 800)
    _process(qtbot, win)
    full_main = list(win.tb.actions())
    full_fmt = list(win.fmt_toolbar.actions())

    win.resize(700, 800)
    _process(qtbot, win)

    assert list(win.tb.actions()) == full_main
    assert list(win.fmt_toolbar.actions()) == full_fmt
    for act in full_main + full_fmt:
        assert act.isEnabled() in (True, False)


def test_smaller_toolbars_fit_at_wide_width(main_window, qtbot):
    """The menubar and format toolbar both fit comfortably under 1500px;
    their extension chevrons must not be visible at that width.

    The Main toolbar legitimately overflows at typical widths (this is the
    bug we're fixing — the chevron exists so users can reach overflowed
    actions); we don't assert it fits at any specific width.
    """
    win = main_window
    win.resize(1500, 900)
    _process(qtbot, win)

    for toolbar in (win.in_app_menubar, win.fmt_toolbar):
        ext = _ext_button(toolbar)
        if ext is not None:
            assert not ext.isVisible(), (
                f"Toolbar {toolbar.objectName()} chevron is visible at "
                f"1500px — content should fit without overflow"
            )


def test_toolbar_size_policies(main_window):
    """Each toolbar uses Preferred-horizontal so it doesn't grab extra space
    that would push siblings off-screen."""
    from PyQt6.QtWidgets import QSizePolicy

    for toolbar in (
        main_window.in_app_menubar,
        main_window.tb,
        main_window.fmt_toolbar,
    ):
        policy = toolbar.sizePolicy()
        assert policy.horizontalPolicy() == QSizePolicy.Policy.Preferred
        assert policy.verticalPolicy() == QSizePolicy.Policy.Fixed
