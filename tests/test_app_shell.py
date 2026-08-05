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


def test_branch_visibility_toggle_no_rebuild(app, monkeypatch):
    """Ast-Toggle darf keinen vollen Navigator-Rebuild pro Element auslösen."""
    from PySide6.QtCore import QSettings, Qt  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        window._set_document(
            __import__("model.document", fromlist=["Document"]).Document.from_dict(
                {
                    "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
                    "params": {
                        "floorplans": {"grundriss-1": {"name": "EG"}},
                        "elec_points": {
                            f"AP-{i}": {
                                "point_id": f"AP-{i}",
                                "floor_plan_id": "grundriss-1",
                                "visible": True,
                            }
                            for i in range(1, 6)
                        },
                    },
                }
            )
        )
        rebuilds = {"count": 0}
        original = window.navigator.rebuild

        def _counting_rebuild(*args, **kwargs):
            rebuilds["count"] += 1
            return original(*args, **kwargs)

        window.navigator.rebuild = _counting_rebuild

        root = window.navigator._tree.invisibleRootItem()
        fp_item = root.child(0)
        cat_item = fp_item.child(0)  # "Anschlusspunkte"
        cat_item.setCheckState(0, Qt.Unchecked)

        # Alle 5 APs müssen im Modell unsichtbar sein
        for i in range(1, 6):
            assert not window._document.is_visible(f"AP-{i}")
        # Kein einziger voller Rebuild durch die Sichtbarkeitsänderung
        assert rebuilds["count"] == 0
    finally:
        window.deleteLater()



def test_app_window_add_elements(app, tmp_path, monkeypatch):
    """Test: Alle Add-Funktionen erzeugen gültige Elemente."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QFileDialog, QInputDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("Test Element", True)),
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        # Grundriss hinzufügen, damit andere Elemente einen FP haben
        window._add_floorplan()
        fp_id = list(window._document.floorplans.keys())[0]
        assert fp_id, "Kein Grundriss erzeugt"

        # Heizkreis
        before_circuits = len(window._document.elements["circuits"])
        window._add_circuit()
        after_circuits = len(window._document.elements["circuits"])
        assert after_circuits == before_circuits + 1

        # Elektro-Punkt
        before_elec_points = len(window._document.elements["elec_points"])
        window._add_elec_point()
        after_elec_points = len(window._document.elements["elec_points"])
        assert after_elec_points == before_elec_points + 1

        # Elektro-Raum
        before_elec_rooms = len(window._document.elements["elec_rooms"])
        window._add_elec_room()
        after_elec_rooms = len(window._document.elements["elec_rooms"])
        assert after_elec_rooms == before_elec_rooms + 1

        # Elektro-Kabel
        before_elec_cables = len(window._document.elements["elec_cables"])
        window._add_elec_cable()
        after_elec_cables = len(window._document.elements["elec_cables"])
        assert after_elec_cables == before_elec_cables + 1

        # HKV
        before_hkvs = len(window._document.elements["hkv_points"])
        window._add_hkv()
        after_hkvs = len(window._document.elements["hkv_points"])
        assert after_hkvs == before_hkvs + 1

        # HKV-Leitung
        before_hkv_lines = len(window._document.elements["hkv_lines"])
        window._add_hkv_line()
        after_hkv_lines = len(window._document.elements["hkv_lines"])
        assert after_hkv_lines == before_hkv_lines + 1

        # Text
        before_texts = len(window._document.elements["text_annotations"])
        window._add_text()
        after_texts = len(window._document.elements["text_annotations"])
        assert after_texts == before_texts + 1
    finally:
        window.deleteLater()

