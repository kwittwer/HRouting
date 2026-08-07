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
        top_labels = {fp_item.child(i).text(0) for i in range(fp_item.childCount())}
        assert top_labels == {"Heizung"}

        heating_group = fp_item.child(0)
        sub_labels = {heating_group.child(i).text(0) for i in range(heating_group.childCount())}
        assert sub_labels == {"Heizkreise"}
    finally:
        dock.deleteLater()


def _find_tree_child_by_text(parent, text: str):
    for index in range(parent.childCount()):
        child = parent.child(index)
        if child.text(0) == text:
            return child
    return None


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
        elec_group = _find_tree_child_by_text(fp_item, "Elektro")
        assert elec_group is not None
        cat_item = _find_tree_child_by_text(elec_group, "Anschlusspunkte")
        assert cat_item is not None
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


def test_route_changed_updates_lengths(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1"}],
                    "manual_routes": {
                        "HK-1": [[0.0, 0.0], [300.0, 0.0]]
                    },
                },
                "params": {
                    "floorplans": {"grundriss-1": {"name": "EG"}},
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Wohnzimmer",
                        }
                    },
                },
            }
        )
        window._set_document(document)

        refreshed: list[str] = []

        def record_refresh(element_id: str):
            refreshed.append(element_id)

        monkeypatch.setattr(window.properties, "refresh_element", record_refresh)

        window.canvas.route_changed.emit("HK-1")

        assert refreshed == ["HK-1"]
    finally:
        window.deleteLater()


def test_floorplan_property_actions_are_wired(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    img_path = tmp_path / "floor.png"
    img_path.write_bytes(b"not-a-real-image")

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(img_path), "")),
    )

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                        }
                    }
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("grundriss-1")

        calls = {"move": [], "rotate": [], "image": []}

        monkeypatch.setattr(
            window.canvas,
            "start_move_floor_plan",
            lambda fp_id: calls["move"].append(fp_id),
        )
        monkeypatch.setattr(
            window.canvas,
            "start_rotate_floor_plan",
            lambda fp_id: calls["rotate"].append(fp_id),
        )
        monkeypatch.setattr(
            window.canvas,
            "load_floor_plan_image",
            lambda fp_id, path: calls["image"].append((fp_id, path)),
        )

        window._on_property_action("grundriss-1", "move")
        window._on_property_action("grundriss-1", "rotate")
        window._on_property_action("grundriss-1", "choose_image")

        assert calls["move"] == ["grundriss-1"]
        assert calls["rotate"] == ["grundriss-1"]
        assert calls["image"] == [("grundriss-1", str(img_path))]
        assert window._document.floorplans["grundriss-1"].file_path == str(img_path)
    finally:
        window.deleteLater()


def test_floorplan_tools_use_active_floorplan(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                        }
                    }
                },
            }
        )
        document.active_floorplan_id = "grundriss-1"
        window._set_document(document)

        calls = {"move": [], "rotate": [], "ref": [], "polygon": []}
        monkeypatch.setattr(
            window.canvas,
            "start_move_floor_plan",
            lambda fp_id: calls["move"].append(fp_id),
        )
        monkeypatch.setattr(
            window.canvas,
            "start_rotate_floor_plan",
            lambda fp_id: calls["rotate"].append(fp_id),
        )
        monkeypatch.setattr(
            window.canvas,
            "start_ref_line_for_floor",
            lambda fp_id: calls["ref"].append(fp_id),
        )
        monkeypatch.setattr(
            window.canvas,
            "start_draw_floor_plan_polygon",
            lambda fp_id: calls["polygon"].append(fp_id),
        )

        window._on_tool_activated("fp.move")
        window._on_tool_activated("fp.rotate")
        window._on_tool_activated("fp.ref_line")
        window._on_tool_activated("fp.polygon")

        assert calls["move"] == ["grundriss-1"]
        assert calls["rotate"] == ["grundriss-1"]
        assert calls["ref"] == ["grundriss-1"]
        assert calls["polygon"] == ["grundriss-1"]
    finally:
        window.deleteLater()


def test_floorplan_draw_polygon_property_action_uses_floorplan_polygon_mode(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                        }
                    }
                },
            }
        )
        window._set_document(document)

        calls = {"polygon": [], "circuit": []}
        monkeypatch.setattr(
            window.canvas,
            "start_draw_floor_plan_polygon",
            lambda fp_id: calls["polygon"].append(fp_id),
        )
        monkeypatch.setattr(
            window.canvas,
            "start_drawing",
            lambda element_id: calls["circuit"].append(element_id),
        )

        window._on_property_action("grundriss-1", "draw_polygon")

        assert calls["polygon"] == ["grundriss-1"]
        assert calls["circuit"] == []
    finally:
        window.deleteLater()


def test_selecting_floorplan_updates_properties_dock(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [
                        {"fp_id": "grundriss-1", "visible": True},
                        {"fp_id": "grundriss-2", "visible": True},
                    ],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                        "grundriss-2": {"name": "OG", "visible": True, "file_path": ""},
                    }
                },
            }
        )
        window._set_document(document)

        item = window.navigator._find_item_by_id("grundriss-2")
        assert item is not None
        window.navigator._tree.setCurrentItem(item)
        window.navigator._on_selection_changed()

        assert window.properties._current_id == "grundriss-2"
    finally:
        window.deleteLater()


def test_selecting_element_syncs_active_floorplan(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [
                        {"fp_id": "grundriss-1", "visible": True},
                        {"fp_id": "grundriss-2", "visible": True},
                    ],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                        "grundriss-2": {"name": "OG", "visible": True, "file_path": ""},
                    },
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-2",
                            "name": "Wohnzimmer",
                        }
                    },
                },
            }
        )
        document.active_floorplan_id = "grundriss-1"
        window._set_document(document)

        window._on_element_selected("HK-1")

        assert window._document.active_floorplan_id == "grundriss-2"
        assert window.properties._current_id == "HK-1"
    finally:
        window.deleteLater()


def test_navigator_allows_selecting_non_workspace_elements_for_properties(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [100.0, 100.0]},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                        }
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Steckdose",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window._apply_workspace("floorplan")

        item = window.navigator._find_item_by_id("AP-1")
        assert item is not None
        assert not item.isDisabled()

        window.navigator._tree.setCurrentItem(item)
        window.navigator._on_selection_changed()

        assert window.properties._current_id == "AP-1"
    finally:
        window.deleteLater()


def test_navigator_selection_keeps_canvas_highlight_for_non_workspace_element(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [100.0, 100.0]},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                        }
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Steckdose",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window._apply_workspace("floorplan")

        window._on_element_selected("AP-1")

        assert window.canvas._selected_item_id == "AP-1"
        assert window.canvas._selected_item_type == "elec_point"
    finally:
        window.deleteLater()


def test_property_color_change_updates_canvas_color(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "manual_routes": {"HK-1": [[0.0, 0.0], [100.0, 0.0]]},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                        }
                    },
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Wohnzimmer",
                            "color": "#2a9d8f",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)

        window._document.elements["circuits"]["HK-1"].data["color"] = "#ff0000"
        window._on_property_changed("HK-1", "color", "#ff0000")

        assert "HK-1" in window.canvas._color_map
        assert window.canvas._color_map["HK-1"].name().lower() == "#ff0000"
    finally:
        window.deleteLater()


def test_ref_line_and_ref_length_recompute_floorplan_scale(app, monkeypatch):
    from PySide6.QtCore import QPointF, QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "mm_per_px": 1.0,
                    "floor_plans": [
                        {
                            "fp_id": "grundriss-1",
                            "visible": True,
                            "mm_per_px": 1.0,
                            "ref_length_mm": 5000.0,
                        }
                    ],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                            "ref_length_mm": 5000.0,
                            "mm_per_px": 1.0,
                        }
                    }
                },
            }
        )
        document.active_floorplan_id = "grundriss-1"
        window._set_document(document)

        layer = window.canvas._floor_plans["grundriss-1"]
        layer.ref_p1 = QPointF(0.0, 0.0)
        layer.ref_p2 = QPointF(100.0, 0.0)
        layer.ref_length_mm = 5000.0
        window.canvas._ref_floor_id = "grundriss-1"

        # Nach dem Zeichnen der Referenzlinie: NOCH KEINE automatische Skalierung
        assert abs(layer.mm_per_px - 1.0) < 1e-9
        assert abs(window._document.floorplans["grundriss-1"].layer["mm_per_px"] - 1.0) < 1e-9
        assert abs(window.canvas.get_mm_per_px() - 1.0) < 1e-9

        # Referenzlänge ändern (Input im UI)
        window._document.floorplans["grundriss-1"].data["ref_length_mm"] = 5000.0
        window._on_property_changed("grundriss-1", "ref_length_mm", 5000.0)
        # Immer noch keine Skalierung
        assert abs(layer.mm_per_px - 1.0) < 1e-9

        # Nur wenn der User "Aktualisieren" Button klickt, wird neu berechnet
        window._on_property_action("grundriss-1", "recompute_scale")

        assert abs(window.canvas._floor_plans["grundriss-1"].mm_per_px - 50.0) < 1e-9
        assert abs(window._document.floorplans["grundriss-1"].layer["mm_per_px"] - 50.0) < 1e-9
        assert abs(window.canvas.get_mm_per_px() - 1.0) < 1e-9
    finally:
        window.deleteLater()


def test_floorplan_editor_has_recompute_button(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QComboBox, QPushButton  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                            "ref_length_mm": 5000.0,
                        }
                    }
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("grundriss-1")

        editor = window.properties._editors.get("grundriss-1")
        assert editor is not None
        buttons = [
            btn for btn in editor.findChildren(QPushButton)
            if btn.text() == "Aktualisieren"
        ]
        assert buttons, "Aktualisieren-Button neben Referenzlänge fehlt"

        draw_buttons = [
            btn for btn in editor.findChildren(QPushButton)
            if btn.text() == "Referenzlinie zeichnen"
        ]
        assert draw_buttons, "Referenzlinie-Button unter Aktualisieren fehlt"

        unit_combos = [
            combo for combo in editor.findChildren(QComboBox)
            if {combo.itemText(i) for i in range(combo.count())} >= {"mm", "cm", "m"}
        ]
        assert unit_combos, "Einheitenwahl mm/cm/m für Referenzmaß fehlt"
    finally:
        window.deleteLater()


def test_recompute_scale_on_active_floorplan_keeps_global_mpp(app, monkeypatch):
    """Beim Recompute: Layer skaliert, Ref-Linie mit dem Bild mit, Global bleibt konstant."""
    from PySide6.QtCore import QPointF, QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "mm_per_px": 50.0,
                    "floor_plans": [
                        {"fp_id": "grundriss-1", "visible": True, "mm_per_px": 50.0, "ref_length_mm": 5000.0},
                        {"fp_id": "grundriss-2", "visible": True, "mm_per_px": 50.0, "ref_length_mm": 3000.0},
                    ],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": "", "mm_per_px": 50.0, "ref_length_mm": 5000.0},
                        "grundriss-2": {"name": "OG", "visible": True, "file_path": "", "mm_per_px": 50.0, "ref_length_mm": 3000.0},
                    }
                },
            }
        )
        document.active_floorplan_id = "grundriss-1"
        window._set_document(document)

        # Referenzlinie mit 100 px = 5000 mm → 50 mm/px
        layer1 = window.canvas._floor_plans["grundriss-1"]
        layer1.ref_p1 = QPointF(0.0, 0.0)
        layer1.ref_p2 = QPointF(100.0, 0.0)  # 100 px
        layer1.ref_length_mm = 5000.0        # 5000 mm / 100 px = 50 mm/px

        # Änderung: neue Referenzlinie mit kürzerer Länge
        layer1.ref_length_mm = 10000.0       # 10000 mm → sollte zu 100 mm/px werden

        # Nur bei Button-Click skaliert
        window._on_property_action("grundriss-1", "recompute_scale")

        # Globaler Canvas-Maßstab bleibt unverändert
        assert abs(window.canvas.get_mm_per_px() - 50.0) < 1e-9, "Global mm_per_px darf sich nicht ändern"
        # Aktiver Grundriss wurde neu skaliert (100 px * 50 (global) = 5000 px Screen; 10000 mm / 5000 px = 2 mm/px... nein warte)
        # Die Formel ist: new_mpp = ref_length_mm / px_len
        # Ref_length_mm = 10000, px_len = 100 px → new_mpp = 100.0
        assert abs(window.canvas._floor_plans["grundriss-1"].mm_per_px - 100.0) < 1e-9
        # Anderer Grundriss bleibt unverändert
        assert abs(window.canvas._floor_plans["grundriss-2"].mm_per_px - 50.0) < 1e-9
    finally:
        window.deleteLater()


def test_delete_elec_point_clears_cable_endpoints(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "elec_points": {
                    "AP-1": [10.0, 10.0],
                    "AP-2": [20.0, 20.0],
                },
                "elec_cables": {"EK-1": [[10.0, 10.0], [20.0, 20.0]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1"},
                    "AP-2": {"point_id": "AP-2", "floor_plan_id": "grundriss-1"},
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "start_ap": "AP-1",
                        "end_ap": "AP-2",
                    }
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        assert document.elements["elec_cables"]["EK-1"].start_ap == "AP-1"

        window._delete_element("AP-1")

        cable = document.elements["elec_cables"]["EK-1"]
        assert cable.start_ap == ""
        assert cable.geom.get("cable_start_ap", "") == ""
        assert cable.end_ap == "AP-2"
    finally:
        window.deleteLater()


def test_duplicate_cable_resets_endpoint_references(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "elec_cables": {"EK-1": [[10.0, 10.0], [20.0, 20.0]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "start_ap": "AP-1",
                        "end_ap": "AP-2",
                    }
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._copy_buffer = {"id": "EK-1", "type": "ElecCable"}
        window._paste_copied()

        ids = sorted(document.elements["elec_cables"].keys())
        assert len(ids) == 2
        new_id = next(eid for eid in ids if eid != "EK-1")
        duplicate = document.elements["elec_cables"][new_id]
        assert duplicate.start_ap == ""
        assert duplicate.end_ap == ""
        assert duplicate.geom.get("cable_start_ap", "") == ""
        assert duplicate.geom.get("cable_end_ap", "") == ""
    finally:
        window.deleteLater()


def test_renumber_elec_points(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "elec_points": {
                    "AP-1": [10.0, 10.0],
                    "AP-2": [20.0, 20.0],
                },
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Steckdose",
                    },
                    "AP-2": {
                        "point_id": "AP-2",
                        "floor_plan_id": "grundriss-1",
                        "name": "Steckdose",
                    },
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._renumber_elec_points()

        names = {
            document.elements["elec_points"]["AP-1"].name,
            document.elements["elec_points"]["AP-2"].name,
        }
        assert names == {"Steckdose1", "Steckdose2"}
    finally:
        window.deleteLater()


def test_undo_redo_add_circuit(app, monkeypatch):
    """Undo nach add_circuit stellt den Zustand ohne den Heizkreis wieder her."""
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
        staticmethod(lambda *a, **k: ("EG", True)),
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        window._add_floorplan()
        assert len(window._document.floorplans) == 1

        # Stack leer nach Grundriss hinzufügen? Nein – floorplan selber hat push.
        # Stacks zurücksetzen um einen sauberen Ausgangspunkt zu haben.
        window._undo_stack.clear()
        window._redo_stack.clear()

        window._add_circuit()
        assert len(window._document.elements["circuits"]) == 1
        assert len(window._undo_stack) == 1  # Snapshot vor add_circuit

        # Undo – Heizkreis muss wieder weg sein.
        window._undo()
        assert len(window._document.elements["circuits"]) == 0
        assert len(window._undo_stack) == 0
        assert len(window._redo_stack) == 1

        # Redo – Heizkreis erscheint wieder.
        window._redo()
        assert len(window._document.elements["circuits"]) == 1
        assert len(window._redo_stack) == 0
    finally:
        window.deleteLater()


def test_undo_redo_delete(app, monkeypatch):
    """Undo nach delete_element stellt das Element wieder her."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
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

    window = AppWindow()
    try:
        window._set_document(document)
        assert "HK-1" in window._document.elements["circuits"]

        window._delete_element("HK-1")
        assert "HK-1" not in window._document.elements["circuits"]
        assert len(window._undo_stack) == 1

        window._undo()
        assert "HK-1" in window._document.elements["circuits"]
    finally:
        window.deleteLater()


def test_undo_stack_clears_on_new_project(app, monkeypatch):
    """Beim Öffnen eines neuen Projekts werden Undo/Redo-Stacks geleert."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        # Manuell etwas auf den Stack legen.
        window._undo_stack.append(Document().snapshot())
        window._redo_stack.append(Document().snapshot())
        assert window._undo_stack

        # Neues Dokument setzen → Stacks müssen leer sein.
        window._set_document(Document())
        assert len(window._undo_stack) == 0
        assert len(window._redo_stack) == 0
    finally:
        window.deleteLater()


def test_recent_projects_menu_updates(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    store = {"recent_projects": []}

    def _value(_self, key, default=None, **_kw):
        return store.get(key, default)

    def _set_value(_self, key, value):
        store[key] = value

    monkeypatch.setattr(QSettings, "value", _value)
    monkeypatch.setattr(QSettings, "setValue", _set_value)

    from gui.app_window import AppWindow  # noqa: PLC0415

    project = tmp_path / "demo.hrp"
    project.write_text("{}", encoding="utf-8")

    window = AppWindow()
    try:
        window._add_to_recent(project)
        assert store["recent_projects"][0] == str(project)
        assert window._recent_menu is not None
        assert not window._recent_menu.isEmpty()
    finally:
        window.deleteLater()


def test_schema_windows_open_and_refresh(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "mm_per_px": 10.0}],
                "elec_points": {"AP-1": [10.0, 20.0], "AP-2": [100.0, 20.0]},
                "elec_point_size_px": {"AP-1": [30.0, 30.0], "AP-2": [30.0, 30.0]},
                "elec_cables": {"EK-1": [[10.0, 20.0], [100.0, 20.0]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1", "name": "Dose 1"},
                    "AP-2": {"point_id": "AP-2", "floor_plan_id": "grundriss-1", "name": "Dose 2"},
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Kabel 1",
                        "start_ap": "AP-1",
                        "end_ap": "AP-2",
                    }
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._open_elec_schema_window()
        window._open_schaltplan_window()
        assert window._elec_schema_window is not None
        assert window._schaltplan_window is not None
        window._refresh_schema_windows()
    finally:
        window.deleteLater()


def test_schema_add_ap_writes_document(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
            "params": {"floorplans": {"grundriss-1": {"name": "EG"}}},
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._on_schema_add_ap(
            {
                "name": "Schema AP",
                "symbol": "Steckdose",
                "color": "#4fc3f7",
                "ap_type": "uv",
                "room_id": "",
            }
        )
        assert len(document.elements["elec_points"]) == 1
        point = next(iter(document.elements["elec_points"].values()))
        assert point.name == "Schema AP"
        assert point.ap_type == "uv"
    finally:
        window.deleteLater()


def test_schema_edit_cable_writes_document(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "elec_points": {"AP-1": [10.0, 20.0], "AP-2": [100.0, 20.0]},
                "elec_cables": {"EK-1": [[10.0, 20.0], [100.0, 20.0]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1", "name": "Dose 1"},
                    "AP-2": {"point_id": "AP-2", "floor_plan_id": "grundriss-1", "name": "Dose 2"},
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Kabel 1",
                        "type": "5x1,5",
                        "start_ap": "AP-1",
                        "end_ap": "AP-2",
                    }
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._on_schema_edit_cable(
            "EK-1",
            {
                "name": "Kabel Neu",
                "type": "3x1,5",
                "color": "#e53935",
                "visible": True,
                "label_visible": True,
                "type_label_visible": True,
                "label_size": 14.0,
                "stroke_width": 3.0,
                "start_ap_id": "AP-2",
                "end_ap_id": "AP-1",
                "comment": "gedreht",
            },
        )
        cable = document.elements["elec_cables"]["EK-1"]
        assert cable.name == "Kabel Neu"
        assert cable.cable_type == "3x1,5"
        assert cable.start_ap == "AP-2"
        assert cable.end_ap == "AP-1"
        assert cable.geom.get("elec_cable_stroke_width") == 3.0
    finally:
        window.deleteLater()


def test_grid_settings_survive_workspace_switch(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.workspaces import WORKSPACES  # noqa: PLC0415

    window = AppWindow()
    try:
        window._grid_cb.setChecked(True)
        window._grid_spin.setValue(0.25)
        idx = window._snap_combo.findData(45)
        window._snap_combo.setCurrentIndex(idx)

        first_workspace = WORKSPACES[0].id
        second_workspace = WORKSPACES[-1].id
        window._apply_workspace(first_workspace)
        window._apply_workspace(second_workspace)

        assert window.canvas.grid_visible() is True
        assert window.canvas.grid_spacing_mm() == 250.0
        assert window.canvas.snap_angle() == 45.0
        assert window._grid_cb.isChecked() is True
    finally:
        window.deleteLater()


# ---------------------------------------------------------------------------
# Phase F – Export
# ---------------------------------------------------------------------------

EXAMPLE = ROOT / "examples" / "minimal.hrp"


def _settings_noop(monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)


def test_grab_source_rect_returns_pixmap(app):
    """Regression: grab_source_rect() hatte einen leeren Rumpf (toter Code
    lag unerreichbar hinter render_for_export). Muss ein echtes Pixmap liefern."""
    from PySide6.QtCore import QRectF  # noqa: PLC0415

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        pixmap = window.canvas.grab_source_rect(QRectF(0, 0, 800, 400))
        assert not pixmap.isNull()
        assert pixmap.width() > 0
        assert pixmap.height() > 0
    finally:
        window.deleteLater()


def test_render_for_export_returns_image(app):
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        img = window.canvas.render_for_export(output_w=400, output_h=300)
        assert img.width() == 400
        assert img.height() == 300
    finally:
        window.deleteLater()


def test_export_svg_creates_file(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "plan.svg"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window._export_svg()
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "<svg" in content
    finally:
        window.deleteLater()


def test_export_pdf_creates_file(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "bericht.pdf"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window._export_pdf()
        assert target.exists()
        assert target.stat().st_size > 0
    finally:
        window.deleteLater()


def test_export_kicad_creates_file(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "schaltplan.kicad_sch"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window._project_path = tmp_path / "demo.hrp"
        window._export_kicad()
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "kicad_sch" in content
    finally:
        window.deleteLater()


def test_export_kicad_without_saved_project_warns(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    _settings_noop(monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a) or QMessageBox.Ok),
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window._project_path = None
        window._export_kicad()
        assert warnings, "Erwartete Warnung bei ungespeichertem Projekt"
    finally:
        window.deleteLater()


def test_export_qet_creates_file(app, tmp_path, monkeypatch):
    import zipfile  # noqa: PLC0415

    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "schaltplan.qet"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window._project_path = tmp_path / "demo.hrp"
        window._export_qet()
        assert target.exists()
        assert zipfile.is_zipfile(target)
    finally:
        window.deleteLater()


def test_export_lengths_computes_totals(app, monkeypatch):
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        # Darf bei vorhandenen Heizkreisen ohne Fehler durchlaufen (auch ohne
        # gezeichneten manual_route: Rohrlänge fällt dann auf 0 zurück).
        window._export_lengths()
    finally:
        window.deleteLater()


def test_export_lengths_without_circuits_shows_info(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    _settings_noop(monkeypatch)
    infos: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: infos.append(a) or QMessageBox.Ok),
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        window._export_lengths()
        assert infos, "Erwartete Hinweis-Dialog ohne Heizkreise"
    finally:
        window.deleteLater()


def test_collect_project_dict_matches_document_shape(app):
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        project_dict = window._collect_project_dict()
        assert set(project_dict.keys()) >= {"svg_path", "canvas", "params", "pdf_export_pages"}
        assert "HK-1" in project_dict["canvas"]["polygons"]
        assert "AP-1" in project_dict["params"]["elec_points"]
    finally:
        window.deleteLater()


def test_undo_redo_after_canvas_mutation(app, monkeypatch):
    """Canvas-Änderungen müssen einen Undo-/Redo-Schritt erzeugen."""
    from PySide6.QtCore import QPointF  # noqa: PLC0415
    from PySide6.QtGui import QColor  # noqa: PLC0415

    _settings_noop(monkeypatch)
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)

        # Ausgangsroute setzen und diesen Setup-Schritt aus der Historie
        # entfernen, damit der Test genau die folgende Änderung prüft.
        window.canvas._manual_routes["HK-1"] = [
            QPointF(100, 100), QPointF(200, 100), QPointF(200, 200)
        ]
        window._undo_group_timer.stop()
        window._finish_undo_group()
        window._undo_stack.clear()
        window._redo_stack.clear()
        window.canvas._scale = 2.75
        window.canvas._offset = QPointF(-123.0, 87.0)
        window.canvas._bg_color = QColor("#123456")
        window.canvas.set_grid_visible(True)
        window.canvas.set_grid_spacing_mm(333.0)
        window.canvas.set_grid_color(QColor(11, 22, 33, 144))
        window.canvas.set_snap_angle(17.0)

        window.canvas._manual_routes["HK-1"][1] = QPointF(260, 140)
        assert len(window._undo_stack) == 1
        assert window._document.to_dict()["canvas"]["manual_routes"]["HK-1"][1] == [
            260.0, 140.0
        ]

        window._undo()
        assert window._document.to_dict()["canvas"]["manual_routes"]["HK-1"][1] == [
            200.0, 100.0
        ]
        assert window.canvas._scale == 2.75
        assert window.canvas._offset == QPointF(-123.0, 87.0)
        assert window.canvas.grid_visible() is True
        assert window.canvas.grid_spacing_mm() == 333.0
        assert window.canvas.grid_color() == QColor(11, 22, 33, 144)
        assert window.canvas.snap_angle() == 17.0
        assert window.canvas._bg_color.name() == QColor("#123456").name()

        window._redo()
        assert window._document.to_dict()["canvas"]["manual_routes"]["HK-1"][1] == [
            260.0, 140.0
        ]
        assert window.canvas._scale == 2.75
        assert window.canvas._offset == QPointF(-123.0, 87.0)
        assert window.canvas.grid_visible() is True
        assert window.canvas.grid_spacing_mm() == 333.0
        assert window.canvas.grid_color() == QColor(11, 22, 33, 144)
        assert window.canvas.snap_angle() == 17.0
        assert window.canvas._bg_color.name() == QColor("#123456").name()
    finally:
        window.deleteLater()

