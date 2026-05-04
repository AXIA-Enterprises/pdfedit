"""Verify the side-panel chrome (Phase 7 / qa/toolbar-overflow):

- The Page Thumbnails dock body no longer carries its own bold "Page
  Thumbnails" QLabel — that text now appears only in the QDockWidget
  title bar (and not duplicated below it).
- The Form Builder dock body likewise drops its body "Form Fields"
  QLabel.
- A toolbar toggle button on the Main toolbar (`self.tb`) shows / hides
  the thumbnails dock and stays in sync with the View-menu QAction.
- A second toolbar toggle does the same for the Form Fields dock.
"""

from __future__ import annotations

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QLabel, QToolBar, QToolButton


def _process(qtbot):
    QCoreApplication.processEvents()
    qtbot.wait(20)
    QCoreApplication.processEvents()


def _body_labels(panel):
    body = panel.widget()
    return [lbl for lbl in body.findChildren(QLabel)]


def test_thumbs_panel_body_has_no_duplicate_title(main_window):
    panel = main_window.thumbs_panel
    texts = [lbl.text() for lbl in _body_labels(panel)]
    assert "Page Thumbnails" not in texts, (
        f"PageThumbnailsPanel body still contains a 'Page Thumbnails' "
        f"QLabel — the dock title bar already shows it. Found labels: {texts}"
    )
    # The dock itself still exposes the title to Qt's title bar.
    assert panel.windowTitle() == "Page Thumbnails"


def test_form_panel_body_has_no_duplicate_title(main_window):
    panel = main_window.form_panel
    texts = [lbl.text() for lbl in _body_labels(panel)]
    assert "Form Fields" not in texts, (
        f"FormBuilderPanel body still contains a 'Form Fields' QLabel — "
        f"the dock title bar already shows it. Found labels: {texts}"
    )
    assert panel.windowTitle() == "Form Fields"


def test_thumbs_panel_refresh_button_still_present(main_window):
    """The Refresh button stays — only the redundant title label was removed."""
    panel = main_window.thumbs_panel
    assert hasattr(panel, "refresh_btn")
    assert panel.refresh_btn.text() == "Refresh"


def test_form_panel_refresh_button_still_present(main_window):
    panel = main_window.form_panel
    assert hasattr(panel, "refresh_btn")
    assert panel.refresh_btn.text() == "Refresh"


def _find_btn(tb: QToolBar, object_name: str) -> QToolButton | None:
    return tb.findChild(QToolButton, object_name)


def test_main_toolbar_has_thumbs_toggle_button(main_window):
    btn = _find_btn(main_window.tb, "ToggleThumbsPanelButton")
    assert btn is not None, (
        "Expected a QToolButton with objectName 'ToggleThumbsPanelButton' "
        "on the main toolbar (self.tb)."
    )
    assert btn.toolTip()


def test_main_toolbar_has_form_toggle_button(main_window):
    btn = _find_btn(main_window.tb, "ToggleFormPanelButton")
    assert btn is not None, (
        "Expected a QToolButton with objectName 'ToggleFormPanelButton' "
        "on the main toolbar (self.tb)."
    )
    assert btn.toolTip()


def test_thumbs_toggle_button_hides_and_shows_dock(main_window, qtbot):
    win = main_window
    btn = _find_btn(win.tb, "ToggleThumbsPanelButton")
    assert btn is not None

    # Force a known visible starting state.
    win.thumbs_panel.setVisible(True)
    _process(qtbot)
    assert win.thumbs_panel.isVisible() is True

    btn.click()
    _process(qtbot)
    assert win.thumbs_panel.isVisible() is False
    assert win.act_show_thumbs_panel.isChecked() is False

    btn.click()
    _process(qtbot)
    assert win.thumbs_panel.isVisible() is True
    assert win.act_show_thumbs_panel.isChecked() is True


def test_thumbs_toggle_button_mirrors_menu_action(main_window, qtbot):
    """Toggling via the View-menu QAction must update the toolbar button's
    checked state, and vice versa."""
    win = main_window
    btn = _find_btn(win.tb, "ToggleThumbsPanelButton")
    assert btn is not None

    win.thumbs_panel.setVisible(True)
    _process(qtbot)
    assert btn.isChecked() is True

    win.act_show_thumbs_panel.trigger()  # menu-side toggle
    _process(qtbot)
    assert win.thumbs_panel.isVisible() is False
    assert btn.isChecked() is False

    win.act_show_thumbs_panel.trigger()
    _process(qtbot)
    assert win.thumbs_panel.isVisible() is True
    assert btn.isChecked() is True


def test_form_toggle_button_hides_and_shows_dock(main_window, qtbot):
    win = main_window
    btn = _find_btn(win.tb, "ToggleFormPanelButton")
    assert btn is not None

    win.form_panel.setVisible(True)
    _process(qtbot)
    assert win.form_panel.isVisible() is True

    btn.click()
    _process(qtbot)
    assert win.form_panel.isVisible() is False
    assert win.act_show_form_panel.isChecked() is False

    btn.click()
    _process(qtbot)
    assert win.form_panel.isVisible() is True
    assert win.act_show_form_panel.isChecked() is True
