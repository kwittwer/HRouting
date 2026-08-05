"""Smoke-Tests der neuen Dock-/Workspace-Oberfläche (offscreen)."""

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

from gui.workspaces import WORKSPACES, workspace  # noqa: E402
from gui.tool_registry import TOOLS, TOOLS_BY_ID  # noqa: E402
from model.layers import LayerId  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_every_workspace_has_tools():
    for definition in WORKSPACES:
        assert definition.tools, f"Workspace {definition.id} hat keine Werkzeuge"


def test_tool_modes_exist_on_canvas(app):
    from gui.canvas_widget import ToolMode  # noqa: PLC0415

    for tool in TOOLS:
        assert hasattr(ToolMode, tool.tool_mode), f"{tool.id}: {tool.tool_mode} fehlt"


def test_tool_ids_unique():
    assert len(TOOLS_BY_ID) == len(TOOLS)


def test_workspace_selectable_layers():
    assert workspace("heating").selectable_layers == {LayerId.HEATING}
    assert LayerId.ELECTRICAL not in workspace("heating").selectable_layers


def test_app_window_builds_and_switches_workspaces(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        for definition in WORKSPACES:
            window._apply_workspace(definition.id)
            assert window._workspace.id == definition.id
            assert window.canvas._selectable_layers == {
                layer.value for layer in definition.selectable_layers
            }
    finally:
        window.deleteLater()


def test_navigator_hides_empty_categories(app):
    from gui.docks.navigator_dock import NavigatorDock  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "circuits": {
                    "HK-1": {"circuit_id": "HK-1", "floor_plan_id": "grundriss-1"}
                },
            },
        }
    )
    dock = NavigatorDock()
    dock.set_document(document)
    try:
        root = dock._tree.invisibleRootItem()
        assert root.childCount() == 1
        fp_item = root.child(0)
        labels = {fp_item.child(i).text(0) for i in range(fp_item.childCount())}
        assert labels == {"Heizkreise"}
    finally:
        dock.deleteLater()


def test_canvas_selection_filter(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.set_selectable_layers({LayerId.HEATING})
        assert canvas._is_selectable("polygon", "HK-1")
        assert not canvas._is_selectable("elec_cable", "EK-1")
        canvas.set_selectable_layers(None)
        assert canvas._is_selectable("elec_cable", "EK-1")
    finally:
        canvas.deleteLater()
