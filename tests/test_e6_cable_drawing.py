"""Regression tests for E-6: Cable drawing workflow."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_e6_add_elec_cable_workflow(app):
    """Test that adding an electrical cable works and sets correct mode."""
    from gui.canvas_widget import ToolMode  # noqa: PLC0415
    from model.elements import ElecCable  # noqa: PLC0415
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        ok = window.open_project_file(Path("examples/Planung_Linda.hrp"))
        assert ok, "Failed to open Planung_Linda.hrp"

        doc_before_cables = len(window._document.elements["elec_cables"])
        window._add_elec_cable()
        doc_after_cables = len(window._document.elements["elec_cables"])
        assert doc_after_cables == doc_before_cables + 1, "Cable not added to document"

        assert window.canvas._mode == ToolMode.DRAW_ELEC_CABLE, "Mode not switched to DRAW_ELEC_CABLE"
        assert window.canvas._current_elec_cable_id, "Cable ID not set"

        cable_id = window.canvas._current_elec_cable_id
        cable = window._document.get(cable_id)
        assert isinstance(cable, ElecCable), "Cable not found in document"
    finally:
        window.deleteLater()


def test_e6_canvas_menu_action_exists(app):
    """Test that the 'Elektro-Kabel hinzufügen' menu action exists."""
    from gui.app_window import AppWindow  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    window = AppWindow()
    try:
        ok = window.open_project_file(Path("examples/Planung_Linda.hrp"))
        assert ok

        # Check that the action exists in the Einfügen menu
        found = False
        for action in window.menuBar().actions():
            if "einfügen" in action.text().lower():
                for sub_action in action.menu().actions():
                    if "kabel" in sub_action.text().lower():
                        found = True
                        break
        assert found, "Cable action not found in menu"
    finally:
        window.deleteLater()



