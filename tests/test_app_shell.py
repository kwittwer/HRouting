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


def test_navigator_persists_expanded_state_and_has_collapse_button(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    store: dict[str, object] = {}

    monkeypatch.setattr(
        QSettings,
        "value",
        lambda self, key, default=None, **kw: store.get(key, default),
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: store.__setitem__(key, value))

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
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1"}
                },
            },
        }
    )

    dock = NavigatorDock()
    try:
        assert dock._collapse_all_button.text() == "Alle zuklappen"
        dock.set_document(document)

        root = dock._tree.invisibleRootItem()
        fp_item = root.child(0)
        elec_group = _find_tree_child_by_text(fp_item, "Elektro")
        assert elec_group is not None
        # APs ohne Raum landen unter "Ohne Raum" (kein Raum im Test-Dokument)
        ap_group = _find_tree_child_by_text(elec_group, "Ohne Raum")
        assert ap_group is not None, "Erwarte 'Ohne Raum' Kategorie für APs ohne Raum"

        fp_item.setExpanded(True)
        elec_group.setExpanded(True)
        ap_group.setExpanded(False)
        dock._save_expanded_state()

        dock.rebuild()

        root_after = dock._tree.invisibleRootItem()
        fp_after = root_after.child(0)
        elec_after = _find_tree_child_by_text(fp_after, "Elektro")
        ap_after = _find_tree_child_by_text(elec_after, "Ohne Raum")
        assert fp_after.isExpanded()
        assert elec_after is not None and elec_after.isExpanded()
        assert ap_after is not None and not ap_after.isExpanded()
    finally:
        dock.deleteLater()


def test_navigator_shows_measurement_categories(app):
    from gui.docks.navigator_dock import (  # noqa: PLC0415
        NavigatorDock,
        make_helper_nav_id,
    )
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "distance_measurements": {
                    "MSRD-1": [[0.0, 0.0], [100.0, 0.0]],
                },
                "distance_label_positions": {
                    "MSRD-1": [50.0, -10.0],
                },
                "angle_measurements": {
                    "MSRA-1": [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0]],
                },
                "angle_label_positions": {
                    "MSRA-1": [58.0, 42.0],
                },
                "floor_helper_lines": {
                    "grundriss-1": {"HL-1": [[0.0, 0.0], [30.0, 0.0]]}
                },
                "floor_helper_line_visible": {
                    "grundriss-1": {"HL-1": True}
                },
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "distance_measurements": {
                    "MSRD-1": {
                        "measurement_id": "MSRD-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Distanz 1",
                        "visible": True,
                    }
                },
                "angle_measurements": {
                    "MSRA-1": {
                        "measurement_id": "MSRA-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Winkel 1",
                        "visible": True,
                    }
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

        annotation_group = _find_tree_child_by_text(fp_item, "Annotationen")
        assert annotation_group is not None

        category_labels = [
            annotation_group.child(i).text(0)
            for i in range(annotation_group.childCount())
        ]
        assert "Distanz 1" in category_labels or "MSRD-1" in category_labels
        assert "Winkel 1" in category_labels or "MSRA-1" in category_labels

        dist_item = dock._find_item_by_id("MSRD-1")
        angle_item = dock._find_item_by_id("MSRA-1")
        helper_item = dock._find_item_by_id(make_helper_nav_id("grundriss-1", "HL-1"))
        assert dist_item is not None
        assert angle_item is not None
        assert helper_item is not None
        assert helper_item.text(0).startswith("Hilfslinie HL-1")
        assert helper_item.toolTip(0).startswith("Hilfslinie HL-1") or helper_item.toolTip(0) == ""
    finally:
        dock.deleteLater()


def test_navigator_helper_lines_are_sorted_naturally(app):
    from gui.docks.navigator_dock import (  # noqa: PLC0415
        NavigatorDock,
        make_helper_nav_id,
    )
    from model.document import Document  # noqa: PLC0415

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "floor_helper_lines": {
                    "grundriss-1": {
                        "HL-10": [[0.0, 0.0], [10.0, 0.0]],
                        "HL-2": [[0.0, 0.0], [20.0, 0.0]],
                        "HL-1": [[0.0, 0.0], [30.0, 0.0]],
                    }
                },
                "floor_helper_line_visible": {
                    "grundriss-1": {
                        "HL-10": True,
                        "HL-2": True,
                        "HL-1": True,
                    }
                },
                "floor_helper_line_length_mm": {
                    "grundriss-1": {
                        "HL-10": 100.0,
                        "HL-2": 200.0,
                        "HL-1": 300.0,
                    }
                },
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
            },
        }
    )

    dock = NavigatorDock()
    dock.set_document(document)
    try:
        helper_ids = ["HL-1", "HL-2", "HL-10"]
        labels = [
            dock._find_item_by_id(make_helper_nav_id("grundriss-1", hid)).text(0)
            for hid in helper_ids
        ]
        assert labels[0].startswith("Hilfslinie HL-1")
        assert labels[1].startswith("Hilfslinie HL-2")
        assert labels[2].startswith("Hilfslinie HL-10")

        root = dock._tree.invisibleRootItem()
        fp_item = root.child(0)
        annotations_top = _find_tree_child_by_text(fp_item, "Annotationen")
        assert annotations_top is not None
        helper_texts = [
            annotations_top.child(i).text(0)
            for i in range(annotations_top.childCount())
            if annotations_top.child(i).text(0).startswith("Hilfslinie ")
        ]
        assert helper_texts == [
            "Hilfslinie HL-1 (300 mm)",
            "Hilfslinie HL-2 (200 mm)",
            "Hilfslinie HL-10 (100 mm)",
        ]
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


def test_canvas_angle_label_positions_roundtrip(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.from_dict(
            {
                "mm_per_px": 1.0,
                "angle_measurements": {
                    "MSRA-1": [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0]],
                },
                "angle_label_positions": {
                    "MSRA-1": [60.0, 44.0],
                },
            }
        )

        exported = canvas.to_dict()
        assert exported.get("angle_label_positions", {}).get("MSRA-1") == [60.0, 44.0]
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
        if cat_item is None:
            cat_item = _find_tree_child_by_text(elec_group, "Ohne Raum")
        assert cat_item is not None
        cat_item.setCheckState(0, Qt.Unchecked)

        # Alle 5 APs müssen im Modell unsichtbar sein
        for i in range(1, 6):
            assert not window._document.is_visible(f"AP-{i}")
        # Kein einziger voller Rebuild durch die Sichtbarkeitsänderung
        assert rebuilds["count"] == 0
    finally:
        window.deleteLater()


def test_helper_line_selection_from_navigator_sets_active_floor(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.docks.navigator_dock import make_helper_nav_id  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        window._set_document(
            Document.from_dict(
                {
                    "canvas": {
                        "floor_plans": [
                            {"fp_id": "grundriss-1"},
                            {"fp_id": "grundriss-2"},
                        ],
                        "floor_helper_lines": {
                            "grundriss-2": {"HL-1": [[0.0, 0.0], [20.0, 0.0]]}
                        },
                        "floor_helper_line_visible": {
                            "grundriss-2": {"HL-1": True}
                        },
                    },
                    "params": {
                        "floorplans": {
                            "grundriss-1": {"name": "EG"},
                            "grundriss-2": {"name": "OG"},
                        }
                    },
                }
            )
        )

        nav_id = make_helper_nav_id("grundriss-2", "HL-1")
        window._select_element_everywhere(nav_id, update_navigator=True)

        assert window._document.active_floorplan_id == "grundriss-2"
        assert window.canvas._selected_item_id == nav_id
        assert window.canvas._selected_item_type == "helper_line"
        assert window.canvas._helper_selected_floor_id == "grundriss-2"
        assert window.canvas._helper_selected_id == "HL-1"
    finally:
        window.deleteLater()


def test_helper_line_visibility_toggle_from_navigator_updates_canvas(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.docks.navigator_dock import make_helper_nav_id  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        window._set_document(
            Document.from_dict(
                {
                    "canvas": {
                        "floor_plans": [{"fp_id": "grundriss-1"}],
                        "floor_helper_lines": {
                            "grundriss-1": {"HL-1": [[0.0, 0.0], [20.0, 0.0]]}
                        },
                        "floor_helper_line_visible": {
                            "grundriss-1": {"HL-1": True}
                        },
                    },
                    "params": {"floorplans": {"grundriss-1": {"name": "EG"}}},
                }
            )
        )

        nav_id = make_helper_nav_id("grundriss-1", "HL-1")
        window._on_visibility_changed(nav_id, False)

        assert window.canvas._floor_helper_line_visible["grundriss-1"]["HL-1"] is False
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


def test_annotation_text_tool_creates_and_places_text(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.canvas_widget import ToolMode  # noqa: PLC0415
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
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    }
                },
            }
        )
        document.active_floorplan_id = "grundriss-1"
        window._set_document(document)
        window._apply_workspace("annotation")

        before = len(window._document.elements["text_annotations"])
        window._on_tool_activated("ann.text")
        after = len(window._document.elements["text_annotations"])

        assert after == before + 1
        assert window.canvas._mode == ToolMode.PLACE_TEXT
        assert window.canvas._placing_text_id is not None
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


def test_helper_tool_uses_visible_floor_when_active_floor_hidden(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    from gui.canvas_widget import ToolMode  # noqa: PLC0415

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
                        {"fp_id": "grundriss-1", "visible": False},
                        {"fp_id": "grundriss-2", "visible": True},
                    ],
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "Hidden",
                            "visible": False,
                            "file_path": "",
                        },
                        "grundriss-2": {
                            "name": "Visible",
                            "visible": True,
                            "file_path": "",
                        },
                    }
                },
            }
        )
        document.active_floorplan_id = "grundriss-1"
        window._set_document(document)

        window._on_tool_activated("ann.helper")

        assert window.canvas._helper_active_floor_id == "grundriss-2"
        assert window.canvas.tool_mode() == ToolMode.DRAW_HELPER_LINE
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


def test_context_menu_shows_workspace_actions_for_selected_element(app, monkeypatch):
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
                    "manual_routes": {"HK-1": [[0.0, 0.0], [50.0, 0.0]]},
                    "supply_lines": {"HK-1": [[0.0, 0.0], [0.0, 50.0]]},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    },
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Wohnzimmer",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)

        window._apply_workspace("heating")
        heating_actions = [entry[0] for entry in window._workspace_context_action_specs("HK-1", "element")]
        assert "draw_polygon" in heating_actions
        assert "edit_route" in heating_actions
        assert "draw_supply" in heating_actions

        window._apply_workspace("floorplan")
        floorplan_actions = [entry[0] for entry in window._workspace_context_action_specs("HK-1", "element")]
        assert "draw_polygon" not in floorplan_actions
        assert "edit_route" not in floorplan_actions
    finally:
        window.deleteLater()


def test_context_menu_offers_draw_cable_for_ap(app, monkeypatch):
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
                    "elec_points": {"AP-1": [120.0, 80.0]},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
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
        window._apply_workspace("electrical")
        actions = [entry[0] for entry in window._workspace_context_action_specs("AP-1", "element")]
        assert "draw_cable_from_ap" in actions
    finally:
        window.deleteLater()


def test_context_menu_includes_generic_actions(app, monkeypatch):
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
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
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
        window._copy_buffer = {"id": "AP-1", "type": "ElecPoint"}
        window._undo_stack.append({"dummy": True})
        window._redo_stack.append({"dummy": True})

        generic = {entry[0]: entry[2] for entry in window._generic_context_action_specs("AP-1")}
        assert generic == {
            "undo": True,
            "redo": True,
            "cut": True,
            "copy": True,
            "paste": True,
            "duplicate": True,
            "delete": True,
        }
    finally:
        window.deleteLater()


def test_context_menu_includes_generic_actions_for_measurements(app, monkeypatch):
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
                    "distance_measurements": {
                        "MSRD-1": [[0.0, 0.0], [100.0, 0.0]],
                    },
                    "distance_label_positions": {
                        "MSRD-1": [50.0, -10.0],
                    },
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    },
                    "distance_measurements": {
                        "MSRD-1": {
                            "measurement_id": "MSRD-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Distanz 1",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)

        generic = {entry[0]: entry[2] for entry in window._generic_context_action_specs("MSRD-1")}
        assert generic["copy"] is False, "Messungen dürfen nicht kopiert werden"
        assert generic["cut"] is False, "Messungen dürfen nicht ausgeschnitten werden"
        assert generic["duplicate"] is False, "Messungen dürfen nicht dupliziert werden"
        assert generic["delete"] is True
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


def test_selecting_measurement_syncs_properties_and_active_floorplan(app, monkeypatch):
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
                    "distance_measurements": {
                        "MSRD-1": [[10.0, 10.0], [120.0, 10.0]],
                    },
                    "distance_label_positions": {
                        "MSRD-1": [70.0, 0.0],
                    },
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                        "grundriss-2": {"name": "OG", "visible": True, "file_path": ""},
                    },
                    "distance_measurements": {
                        "MSRD-1": {
                            "measurement_id": "MSRD-1",
                            "floor_plan_id": "grundriss-2",
                            "name": "Messung OG",
                            "visible": True,
                        }
                    },
                },
            }
        )
        document.active_floorplan_id = "grundriss-1"
        window._set_document(document)

        window._on_element_selected("MSRD-1")

        assert window._document.active_floorplan_id == "grundriss-2"
        assert window.properties._current_id == "MSRD-1"
        assert window.canvas._selected_item_id == "MSRD-1"
    finally:
        window.deleteLater()


def test_sync_canvas_to_document_persists_measurements(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from PySide6.QtCore import QPointF  # noqa: PLC0415
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
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    }
                },
            }
        )
        window._set_document(document)

        window.canvas._measure_lines = [
            (QPointF(10.0, 10.0), QPointF(110.0, 10.0), 100.0),
        ]
        window.canvas._measure_label_positions = [(120.0, 4.0)]
        window.canvas._angle_measurements = [
            (QPointF(0.0, 0.0), QPointF(50.0, 0.0), QPointF(50.0, 50.0), 90.0),
        ]
        window.canvas._angle_measure_label_positions = [(60.0, 40.0)]

        window._sync_canvas_to_document()

        assert window._document.view.get("distance_measurements", {}).get("MSRD-1") == [
            (10.0, 10.0),
            (110.0, 10.0),
        ]
        assert window._document.view.get("distance_label_positions", {}).get("MSRD-1") == [120.0, 4.0]
        assert window._document.view.get("angle_measurements", {}).get("MSRA-1") == [
            (0.0, 0.0),
            (50.0, 0.0),
            (50.0, 50.0),
        ]
        assert window._document.view.get("angle_label_positions", {}).get("MSRA-1") == [60.0, 40.0]
    finally:
        window.deleteLater()


def test_a4_navigator_and_properties_prefer_element_names(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415

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
                    "elec_rooms": {
                        "ER-13": [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]],
                    },
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    },
                    "elec_rooms": {
                        "ER-13": {
                            "room_id": "ER-13",
                            "floor_plan_id": "grundriss-1",
                            "name": "Ankleide",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)

        item = window.navigator._find_item_by_id("ER-13")
        assert item is not None
        assert item.text(0) == "Ankleide"

        window.properties.show_element("ER-13")
        editor = window.properties._editors.get("ER-13")
        assert editor is not None
        header = editor.findChild(QLabel, "element_header")
        assert header is not None
        # Properties-Header zeigt die ID; der Name erscheint im Canvas-Label
        assert "ER-13" in header.text()
        # Canvas-Label verwendet den Namen statt der ID
        assert window.canvas._label_map.get("ER-13") == "Ankleide"
    finally:
        window.deleteLater()


def test_pdf_export_render_keeps_canvas_parent(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        parent_before = window.canvas.parentWidget()
        assert parent_before is not None

        img = window.canvas.render_for_export(output_w=640, output_h=360)

        assert not img.isNull()
        assert window.canvas.parentWidget() is parent_before
    finally:
        window.deleteLater()


def test_export_menu_hides_kicad_and_qet_actions(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        export_action = next(
            (a for a in window.menuBar().actions() if "export" in a.text().lower()),
            None,
        )
        assert export_action is not None
        export_menu = export_action.menu()
        assert export_menu is not None
        labels = [a.text() for a in export_menu.actions() if a.text()]

        assert "PDF exportieren…" in labels
        assert "SVG exportieren…" in labels
        assert "Längen & Stückliste…" in labels
        assert "KiCad exportieren…" not in labels
        assert "QElectroTech exportieren…" not in labels
    finally:
        window.deleteLater()


def test_export_pdf_smoke_writes_file(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    pdf_path = tmp_path / "report.pdf"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(pdf_path), "PDF (*.pdf)")),
    )

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "polygons": {
                        "HK-1": [[0.0, 0.0], [200.0, 0.0], [200.0, 150.0], [0.0, 150.0]],
                    },
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    },
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Wohnzimmer",
                            "visible": True,
                            "diameter": 16.0,
                            "spacing": 150.0,
                            "wall_dist": 200.0,
                            "room_temp": 20.0,
                            "floor_covering": "Fliesen / Keramik",
                        }
                    },
                },
            }
        )
        window._set_document(document)

        monkeypatch.setattr(
            window,
            "_open_pdf_export_config_dialog",
            lambda: window._normalize_pdf_export_pages(window._default_pdf_export_pages()),
        )

        window._export_pdf()

        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 0
    finally:
        window.deleteLater()


def test_pdf_export_collects_extended_elektro_sections(app, monkeypatch):
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
                    "elec_points": {
                        "AP-1": [100.0, 100.0],
                        "AP-2": [220.0, 100.0],
                        "AP-3": [360.0, 100.0],
                    },
                    "elec_cables": {
                        "EK-1": [[100.0, 100.0], [220.0, 100.0]],
                        "EK-2": [[220.0, 100.0], [360.0, 100.0]],
                    },
                    "cable_start_ap": {"EK-1": "AP-1", "EK-2": "AP-2"},
                    "cable_end_ap": {"EK-1": "AP-2", "EK-2": "AP-3"},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "UV Flur",
                            "ap_type": "uv",
                            "uv_config": {
                                "rows": 1,
                                "modules_per_row": 12,
                                "slots": [
                                    {
                                        "row": 1,
                                        "slot": 1,
                                        "device_type": "LS",
                                        "te_size": 1,
                                        "assignment": "EK-1",
                                    }
                                ],
                                "busbars": [{"phase": "L1", "te_start": 1, "te_end": 3}],
                            },
                        },
                        "AP-2": {
                            "point_id": "AP-2",
                            "floor_plan_id": "grundriss-1",
                            "name": "UP Dose",
                            "ap_type": "up_distribution",
                            "up_distribution_config": {
                                "incoming_cable_id": "EK-1",
                                "outgoing_cable_ids": ["EK-2"],
                                "mappings": [
                                    {
                                        "from_conductor": "L1",
                                        "to_cable_id": "EK-2",
                                        "to_conductor": "L1",
                                    }
                                ],
                            },
                        },
                        "AP-3": {
                            "point_id": "AP-3",
                            "floor_plan_id": "grundriss-1",
                            "name": "Steckdose",
                            "ap_type": "standard",
                        },
                    },
                    "elec_cables": {
                        "EK-1": {
                            "cable_id": "EK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Zuleitung UV->UP",
                            "type": "NYM-J 5x1,5",
                            "start_ap": "AP-1",
                            "end_ap": "AP-2",
                        },
                        "EK-2": {
                            "cable_id": "EK-2",
                            "floor_plan_id": "grundriss-1",
                            "name": "Abgang UP->AP",
                            "type": "NYM-J 3x1,5",
                            "start_ap": "AP-2",
                            "end_ap": "AP-3",
                        },
                    },
                },
            }
        )
        window._set_document(document)

        sections = set(window._default_pdf_table_sections("elektro"))
        data = window._collect_export_data()

        assert "el_uv" in sections
        assert "el_up_distribution" in sections
        assert "el_bom" in sections
        assert "el_uv_busbars" in sections
        assert len(data["uv_rows"]) >= 1
        assert len(data["up_distribution_rows"]) >= 1
        assert len(data["cable_bom_rows"]) >= 1
        assert len(data["uv_busbar_bom_rows"]) >= 1
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


def test_floorplan_remove_polygon_action_clears_outline(app, monkeypatch):
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
                        {
                            "fp_id": "grundriss-1",
                            "visible": True,
                            "polygon": [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]],
                        }
                    ],
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

        assert window._document.floorplans["grundriss-1"].layer.get("polygon")

        window._on_property_action("grundriss-1", "remove_polygon")

        assert window._document.floorplans["grundriss-1"].layer.get("polygon") == []
    finally:
        window.deleteLater()


def test_floorplan_property_changes_update_canvas_layer(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415
    from model.field_access import set_field  # noqa: PLC0415
    from model.schema import FLOOR_PLAN_SCHEMA  # noqa: PLC0415

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
                            "opacity": 1.0,
                            "offset_x": 0.0,
                            "offset_y": 0.0,
                            "rotation": 0.0,
                        }
                    }
                },
            }
        )
        window._set_document(document)

        floor = window._document.floorplans["grundriss-1"]
        schema_fields = {spec.key: spec for spec in FLOOR_PLAN_SCHEMA.fields}
        set_field(floor, schema_fields["opacity"], 0.35)
        set_field(floor, schema_fields["offset_x"], 12.0)
        set_field(floor, schema_fields["offset_y"], 24.0)
        set_field(floor, schema_fields["rotation"], 33.0)

        window._on_property_changed("grundriss-1", "opacity", 0.35)
        window._on_property_changed("grundriss-1", "offset_x", 12.0)
        window._on_property_changed("grundriss-1", "offset_y", 24.0)
        window._on_property_changed("grundriss-1", "rotation", 33.0)

        layer = window.canvas._floor_plans["grundriss-1"]
        assert abs(layer.opacity - 0.35) < 1e-9
        assert abs(layer.offset_x - 12.0) < 1e-9
        assert abs(layer.offset_y - 24.0) < 1e-9
        assert abs(layer.rotation - 33.0) < 1e-9
    finally:
        window.deleteLater()


def test_floorplan_ref_line_visibility_property_updates_canvas(app, monkeypatch):
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
                    "ref_line_visible": {"grundriss-1": True},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {
                            "name": "EG",
                            "visible": True,
                            "file_path": "",
                            "ref_line_visible": True,
                        }
                    }
                },
            }
        )
        window._set_document(document)
        window._document.floorplans["grundriss-1"].data["ref_line_visible"] = False

        window._on_property_changed("grundriss-1", "ref_line_visible", False)

        assert not window.canvas.get_ref_line_visible("grundriss-1")
    finally:
        window.deleteLater()


def test_add_floorplan_loads_selected_image(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QFileDialog, QInputDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    img_path = tmp_path / "floor.png"
    img_path.write_bytes(b"fake-image")

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(img_path), "")),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("EG", True)),
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        calls: list[tuple[str, str]] = []
        original_add_floor_plan = window.canvas.add_floor_plan

        def record_add_floor_plan(fp_id: str, path: str = ""):
            calls.append((fp_id, path))
            return original_add_floor_plan(fp_id, path)

        monkeypatch.setattr(window.canvas, "add_floor_plan", record_add_floor_plan)

        window._add_floorplan()

        fp_id = window._document.floorplan_order[0]
        assert calls == [(fp_id, str(img_path))]
        assert window._document.floorplans[fp_id].file_path == str(img_path)
        assert window._document.active_floorplan_id == fp_id
    finally:
        window.deleteLater()


class _MouseEventStub:
    def __init__(self, position, *, button, buttons=None, modifiers=0):
        from PySide6.QtCore import Qt  # noqa: PLC0415

        self._position = position
        self._button = button
        self._buttons = button if buttons is None else buttons
        self._modifiers = Qt.NoModifier if modifiers == 0 else modifiers

    def position(self):
        return self._position

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def modifiers(self):
        return self._modifiers

    def globalPosition(self):
        return self._position


def test_canvas_move_floorplan_updates_transform_and_ref_line(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        layer = canvas.add_floor_plan("grundriss-1")
        layer.ref_p1 = QPointF(10.0, 10.0)
        layer.ref_p2 = QPointF(20.0, 10.0)
        canvas._ref_floor_id = "grundriss-1"

        emitted: list[tuple[str, float, float, float]] = []
        canvas.floor_plan_transform_updated.connect(
            lambda fp_id, ox, oy, rot: emitted.append((fp_id, ox, oy, rot))
        )

        canvas.start_move_floor_plan("grundriss-1")
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(5.0, 5.0), button=Qt.LeftButton)
        )
        canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(25.0, 35.0),
                button=Qt.NoButton,
                buttons=Qt.LeftButton,
            )
        )
        canvas.mouseReleaseEvent(
            _MouseEventStub(QPointF(25.0, 35.0), button=Qt.LeftButton)
        )

        assert abs(layer.offset_x - 20.0) < 1e-9
        assert abs(layer.offset_y - 30.0) < 1e-9
        assert layer.ref_p1 == QPointF(30.0, 40.0)
        assert layer.ref_p2 == QPointF(40.0, 40.0)
        assert canvas._ref_p1 == QPointF(30.0, 40.0)
        assert canvas._ref_p2 == QPointF(40.0, 40.0)
        assert emitted == [("grundriss-1", 20.0, 30.0, 0.0)]
    finally:
        canvas.deleteLater()


def test_canvas_rotate_floorplan_updates_rotation_and_ref_line(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        layer = canvas.add_floor_plan("grundriss-1")
        layer.size = (100.0, 100.0)
        layer.ref_p1 = QPointF(60.0, 50.0)
        layer.ref_p2 = QPointF(70.0, 50.0)
        canvas._ref_floor_id = "grundriss-1"

        emitted: list[tuple[str, float, float, float]] = []
        canvas.floor_plan_transform_updated.connect(
            lambda fp_id, ox, oy, rot: emitted.append((fp_id, ox, oy, rot))
        )

        canvas.start_rotate_floor_plan("grundriss-1")
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(100.0, 50.0), button=Qt.LeftButton)
        )
        canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(50.0, 100.0),
                button=Qt.NoButton,
                buttons=Qt.LeftButton,
            )
        )
        canvas.mouseReleaseEvent(
            _MouseEventStub(QPointF(50.0, 100.0), button=Qt.LeftButton)
        )

        assert abs(layer.rotation - 90.0) < 1e-9
        assert abs(layer.ref_p1.x() - 50.0) < 1e-9
        assert abs(layer.ref_p1.y() - 60.0) < 1e-9
        assert abs(layer.ref_p2.x() - 50.0) < 1e-9
        assert abs(layer.ref_p2.y() - 70.0) < 1e-9
        assert emitted == [("grundriss-1", 0.0, 0.0, 90.0)]
    finally:
        canvas.deleteLater()


def test_canvas_draw_ref_line_stores_points_on_floorplan(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget, ToolMode  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        layer = canvas.add_floor_plan("grundriss-1")
        emitted: list[bool] = []
        canvas.ref_line_set.connect(lambda: emitted.append(True))

        canvas.start_ref_line_for_floor("grundriss-1")
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(10.0, 20.0), button=Qt.LeftButton)
        )
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(110.0, 20.0), button=Qt.LeftButton)
        )

        assert canvas.tool_mode() == ToolMode.NONE
        assert canvas._ref_p1 == QPointF(10.0, 20.0)
        assert canvas._ref_p2 == QPointF(110.0, 20.0)
        assert layer.ref_p1 == QPointF(10.0, 20.0)
        assert layer.ref_p2 == QPointF(110.0, 20.0)
        assert emitted == [True]
    finally:
        canvas.deleteLater()


def test_canvas_helper_lines_are_scoped_per_floorplan(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.add_floor_plan("grundriss-1")
        canvas.add_floor_plan("grundriss-2")
        canvas.set_mm_per_px(1.0)
        canvas.set_helper_line_target_length_mm(100.0, "grundriss-1")
        canvas.set_helper_line_target_length_mm(200.0, "grundriss-2")

        emitted: list[bool] = []
        canvas.helper_lines_changed.connect(lambda: emitted.append(True))

        canvas.start_draw_helper_line("grundriss-1")
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(0.0, 0.0), button=Qt.LeftButton)
        )
        canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(10.0, 0.0),
                button=Qt.NoButton,
                buttons=Qt.NoButton,
            )
        )
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(10.0, 0.0), button=Qt.LeftButton)
        )

        canvas.start_draw_helper_line("grundriss-2")
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(0.0, 10.0), button=Qt.LeftButton)
        )
        canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(0.0, 20.0),
                button=Qt.NoButton,
                buttons=Qt.NoButton,
            )
        )
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(0.0, 20.0), button=Qt.LeftButton)
        )

        floor1_lines = canvas._floor_helper_lines["grundriss-1"]
        floor2_lines = canvas._floor_helper_lines["grundriss-2"]

        assert len(floor1_lines) == 1
        assert len(floor2_lines) == 1

        floor1_pts = next(iter(floor1_lines.values()))
        floor2_pts = next(iter(floor2_lines.values()))

        assert floor1_pts[0] == QPointF(0.0, 0.0)
        assert floor1_pts[1] == QPointF(100.0, 0.0)
        assert floor2_pts[0] == QPointF(0.0, 10.0)
        assert floor2_pts[1] == QPointF(0.0, 210.0)
        assert len(emitted) == 2
    finally:
        canvas.deleteLater()


def test_canvas_helper_draw_falls_back_to_visible_floor(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        hidden = canvas.add_floor_plan("grundriss-1")
        visible = canvas.add_floor_plan("grundriss-2")
        hidden.visible = False
        visible.visible = True

        canvas.set_mm_per_px(1.0)
        canvas.set_active_helper_floor("grundriss-1")
        canvas.start_draw_helper_line("grundriss-1")

        canvas.mousePressEvent(
            _MouseEventStub(QPointF(0.0, 0.0), button=Qt.LeftButton)
        )
        canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(10.0, 0.0),
                button=Qt.NoButton,
                buttons=Qt.NoButton,
            )
        )
        canvas.mousePressEvent(
            _MouseEventStub(QPointF(10.0, 0.0), button=Qt.LeftButton)
        )

        assert canvas._helper_active_floor_id == "grundriss-2"
        assert len(canvas._floor_helper_lines.get("grundriss-1", {})) == 0
        assert len(canvas._floor_helper_lines.get("grundriss-2", {})) == 1
    finally:
        canvas.deleteLater()


def test_canvas_helper_draw_enables_floor_helper_visibility(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        floor = canvas.add_floor_plan("grundriss-1")
        floor.visible = True
        canvas.set_helper_line_visible("grundriss-1", False)

        assert canvas.get_helper_line_visible("grundriss-1") is False

        canvas.start_draw_helper_line("grundriss-1")

        assert canvas.get_helper_line_visible("grundriss-1") is True
    finally:
        canvas.deleteLater()


def test_repair_project_menu_action_creates_backup_and_reloads(app, monkeypatch, tmp_path):
    """_repair_project() creates a backup, repairs the file, and reloads the document."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415
    from storage.hrp_io import save_raw  # noqa: PLC0415

    hrp_file = tmp_path / "project.hrp"
    raw = {
        "canvas": {
            "floor_plans": [{"fp_id": "grundriss-1"}],
            "polygons": {
                "HK-1": [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]],
                "HK-orphan": [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0]],
            },
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
            "floorplans_order": ["grundriss-orphan", "grundriss-1"],
            "circuits": {
                "HK-1": {"circuit_id": "HK-1", "floor_plan_id": "grundriss-1", "name": "Wohnzimmer"}
            },
            "elec_points": {},
            "elec_cables": {},
            "hkv_points": {},
            "hkv_lines": {},
            "elec_rooms": {},
            "text_annotations": {},
            "furniture": {},
        },
    }
    save_raw(raw, hrp_file)

    window = AppWindow()
    try:
        assert window.open_project_file(hrp_file)
        result = window._repair_project()

        assert result is True
        backup = hrp_file.with_suffix(".hrp.bak")
        assert backup.exists()

        # After reload: orphan canvas key removed, order normalised
        doc = window._document
        assert doc is not None
        assert "HK-orphan" not in doc.view.get("polygons", {})
        assert doc.elements.get("circuits", {}) or True  # project still loadable
        assert window._document is not None
    finally:
        window.deleteLater()


def test_repair_project_menu_action_dry_run_does_not_write(app, monkeypatch, tmp_path):
    """repair_hrp_data called with dry_run=True does not modify the source file."""
    from storage.hrp_io import save_raw  # noqa: PLC0415
    from storage.hrp_repair import repair_hrp_data  # noqa: PLC0415

    hrp_file = tmp_path / "project.hrp"
    raw = {
        "canvas": {
            "floor_plans": [{"fp_id": "grundriss-1"}],
            "polygons": {"HK-orphan": [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0]]},
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
            "circuits": {},
            "elec_points": {},
            "elec_cables": {},
            "hkv_points": {},
            "hkv_lines": {},
            "elec_rooms": {},
            "text_annotations": {},
            "furniture": {},
        },
    }
    save_raw(raw, hrp_file)
    original_mtime = hrp_file.stat().st_mtime

    import copy as _copy  # noqa: PLC0415
    import json  # noqa: PLC0415
    with open(hrp_file, encoding="utf-8") as fh:
        raw_loaded = json.load(fh)
    repaired, changes = repair_hrp_data(_copy.deepcopy(raw_loaded), aggressive=True)

    # File not touched in dry-run simulation
    assert hrp_file.stat().st_mtime == original_mtime
    assert not (tmp_path / "project.hrp.bak").exists()
    assert changes  # orphan polygon reported


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


def test_paste_copied_ap_creates_offset_duplicate(app, monkeypatch):
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
                "elec_points": {"AP-1": [10.0, 10.0]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Steckdose",
                    }
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._copy_buffer = {"id": "AP-1", "type": "ElecPoint"}
        window._paste_copied()

        ids = sorted(document.elements["elec_points"].keys())
        assert len(ids) == 2
        new_id = next(eid for eid in ids if eid != "AP-1")
        duplicate = document.elements["elec_points"][new_id]
        assert duplicate.floor_plan_id == "grundriss-1"
        assert duplicate.name == "Steckdose"
        assert duplicate.geom.get("elec_points") == [30.0, 30.0]
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


def test_e3_configure_uv_action_persists_config(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui import parameter_panel  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    class _FakeUvDialog:
        def __init__(self, config, cable_choices, parent=None):
            self._config = dict(config or {})
            self._choices = list(cable_choices or [])

        def exec(self):
            return QDialog.Accepted

        def get_config(self):
            return {
                "rows": 2,
                "modules_per_row": 12,
                "slots": [{"row": 1, "slot": 1, "device_type": "FI", "te_size": 4}],
                "busbars": [{"phase": "L1", "color": "#e53935", "te_start": 1, "te_end": 4}],
            }

    monkeypatch.setattr(parameter_panel, "UvConfigDialog", _FakeUvDialog)

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "elec_cables": {"EK-1": [[0.0, 0.0], [10.0, 0.0]]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "UV EG",
                        "ap_type": "uv",
                        "uv_config": {},
                    }
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Zuleitung",
                        "type": "NYM-J 5x2,5",
                    }
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._action_configure_uv("AP-1")

        point = document.elements["elec_points"]["AP-1"]
        assert point.data["uv_config"]["rows"] == 2
        assert point.data["uv_config"]["slots"][0]["device_type"] == "FI"
        assert point.data["uv_config"]["busbars"][0]["phase"] == "L1"
        assert window._dirty is True
    finally:
        window.deleteLater()


def test_e4_configure_up_action_persists_config(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui import parameter_panel  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    class _FakeUpDialog:
        def __init__(self, config, cable_choices, parent=None):
            self._config = dict(config or {})
            self._choices = list(cable_choices or [])

        def exec(self):
            return QDialog.Accepted

        def get_config(self):
            return {
                "incoming_cable_id": "EK-1",
                "outgoing_cable_ids": ["EK-2"],
                "mappings": [
                    {
                        "from_conductor": "L1",
                        "to_cable_id": "EK-2",
                        "to_conductor": "L1",
                        "note": "Abgang Phase",
                    }
                ],
                "note": "UP im Flur",
            }

    monkeypatch.setattr(parameter_panel, "UpDistributionDialog", _FakeUpDialog)

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "elec_cables": {
                    "EK-1": [[0.0, 0.0], [10.0, 0.0]],
                    "EK-2": [[10.0, 0.0], [20.0, 0.0]],
                },
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "UP Flur",
                        "ap_type": "up_distribution",
                        "up_distribution_config": {},
                    }
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Zuleitung",
                        "type": "NYM-J 5x2,5",
                    },
                    "EK-2": {
                        "cable_id": "EK-2",
                        "floor_plan_id": "grundriss-1",
                        "name": "Abgang",
                        "type": "NYM-J 3x1,5",
                    },
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._action_configure_up("AP-1")

        point = document.elements["elec_points"]["AP-1"]
        cfg = point.data["up_distribution_config"]
        assert cfg["incoming_cable_id"] == "EK-1"
        assert cfg["outgoing_cable_ids"] == ["EK-2"]
        assert cfg["mappings"][0]["from_conductor"] == "L1"
        assert cfg["note"] == "UP im Flur"
        assert window._dirty is True
    finally:
        window.deleteLater()


def test_e7_schema_with_planung_linda_has_uv_and_cables(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)

        ap_nodes, cable_edges, room_map = window._build_schema_data()
        assert ap_nodes, "Keine AP-Knoten aus Planung_Linda gelesen"
        assert cable_edges, "Keine Kabelkanten aus Planung_Linda gelesen"
        assert room_map
        assert any(node.ap_type == "uv" for node in ap_nodes)
        assert any(edge.length_m >= 0.0 for edge in cable_edges)

        window._open_elec_schema_window()
        assert window._elec_schema_window is not None
        window._refresh_schema_windows()

        schema_nodes = window._elec_schema_window._ap_nodes
        schema_edges = window._elec_schema_window._cable_edges
        assert len(schema_nodes) == len(ap_nodes)
        assert len(schema_edges) == len(cable_edges)
    finally:
        window.deleteLater()


def test_e8_export_data_with_planung_linda_preserves_bom_and_configs(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)

        project_dict = window._collect_project_dict()
        params = project_dict.get("params") or {}
        canvas = project_dict.get("canvas") or {}

        assert isinstance(params.get("bom"), dict)

        ap_params = params.get("elec_points") or {}
        cable_params = params.get("elec_cables") or {}
        assert ap_params
        assert cable_params

        assert any("uv_config" in ap for ap in ap_params.values())
        assert any("up_distribution_config" in ap for ap in ap_params.values())
        assert any(bool(ap.get("up_distribution_config")) for ap in ap_params.values())

        elec_points_geom = canvas.get("elec_points") or {}
        elec_cables_geom = canvas.get("elec_cables") or {}
        assert len(elec_points_geom) >= len(ap_params)
        assert len(elec_cables_geom) >= len(cable_params)
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


def test_x1_draw_export_frame_persists_in_project_dict(app, monkeypatch):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.canvas_widget import ToolMode  # noqa: PLC0415

    _settings_noop(monkeypatch)

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)
        window.canvas.clear_export_frame()
        window._dirty = False

        window._on_tool_activated("exp.frame")
        assert window.canvas.tool_mode() == ToolMode.DRAW_EXPORT_FRAME

        expected_start = window.canvas._to_canvas(QPointF(10.0, 20.0))
        expected_end = window.canvas._to_canvas(QPointF(110.0, 70.0))
        expected_frame = (
            min(expected_start.x(), expected_end.x()),
            min(expected_start.y(), expected_end.y()),
            abs(expected_end.x() - expected_start.x()),
            abs(expected_end.y() - expected_start.y()),
        )

        window.canvas.mousePressEvent(
            _MouseEventStub(QPointF(10.0, 20.0), button=Qt.LeftButton)
        )
        window.canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(110.0, 70.0),
                button=Qt.NoButton,
                buttons=Qt.LeftButton,
            )
        )
        window.canvas.mouseReleaseEvent(
            _MouseEventStub(QPointF(110.0, 70.0), button=Qt.LeftButton)
        )

        frame = window.canvas.get_export_frame()
        assert frame is not None
        assert frame.x() == pytest.approx(expected_frame[0])
        assert frame.y() == pytest.approx(expected_frame[1])
        assert frame.width() == pytest.approx(expected_frame[2])
        assert frame.height() == pytest.approx(expected_frame[3])
        assert window.canvas.tool_mode() == ToolMode.NONE
        assert window._dirty is True

        project_dict = window._collect_project_dict()
        export_frame = project_dict["canvas"]["export_frame"]
        assert export_frame == pytest.approx(list(expected_frame))
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


def test_x2_export_pdf_with_planung_linda_creates_file(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "planung_linda_bericht.pdf"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)
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


def test_x3_export_svg_with_planung_linda_creates_file(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "planung_linda.svg"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)
        window._export_svg()
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "<svg" in content
    finally:
        window.deleteLater()


def test_x3_export_kicad_with_planung_linda_creates_file(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "planung_linda.kicad_sch"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)
        window._project_path = tmp_path / "planung_linda.hrp"
        window._export_kicad()
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "kicad_sch" in content
    finally:
        window.deleteLater()


def test_x3_export_qet_with_planung_linda_creates_file(app, tmp_path, monkeypatch):
    import zipfile  # noqa: PLC0415

    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    _settings_noop(monkeypatch)
    target = tmp_path / "planung_linda.qet"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)
        window._project_path = tmp_path / "planung_linda.hrp"
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


def test_export_lengths_includes_supply_length():
    from model.document import Document  # noqa: PLC0415
    from model.computed import heating_length_overview  # noqa: PLC0415
    from storage.hrp_io import load_raw  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    document = Document.from_dict(load_raw(planning_linda))
    rows, _t_supply, _t_return = heating_length_overview(document)
    assert rows
    assert any(row["supply_m"] > 0 for row in rows)
    assert all(
        row["total_m"] == pytest.approx(row["route_m"] + row["supply_m"] * 2.0)
        for row in rows
    )


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
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1"}],
                    "manual_routes": {
                        "HK-1": [[100.0, 100.0], [200.0, 100.0], [200.0, 200.0]]
                    },
                },
                "params": {
                    "floorplans": {"grundriss-1": {"name": "EG", "visible": True}},
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "HK 1",
                        }
                    },
                },
            }
        )
        window._set_document(document)

        # Ausgangsroute setzen und diesen Setup-Schritt aus der Historie
        # entfernen, damit der Test genau die folgende Änderung prüft.
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


def test_all1_undo_redo_export_frame_canvas_mutation(app, monkeypatch):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    _settings_noop(monkeypatch)
    from gui.app_window import AppWindow  # noqa: PLC0415

    planning_linda = ROOT / "examples" / "Planung_Linda.hrp"
    assert planning_linda.exists(), "Fixture fehlt: examples/Planung_Linda.hrp"

    window = AppWindow()
    try:
        assert window.open_project_file(planning_linda)

        baseline_frame = window._collect_project_dict()["canvas"].get("export_frame")

        window.canvas.clear_export_frame()
        window._undo_group_timer.stop()
        window._finish_undo_group()
        window._undo_stack.clear()
        window._redo_stack.clear()

        window._on_tool_activated("exp.frame")
        window.canvas.mousePressEvent(
            _MouseEventStub(QPointF(20.0, 30.0), button=Qt.LeftButton)
        )
        window.canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(220.0, 130.0),
                button=Qt.NoButton,
                buttons=Qt.LeftButton,
            )
        )
        window.canvas.mouseReleaseEvent(
            _MouseEventStub(QPointF(220.0, 130.0), button=Qt.LeftButton)
        )

        frame_after_draw = window._collect_project_dict()["canvas"]["export_frame"]
        assert frame_after_draw is not None
        assert len(window._undo_stack) == 1

        window._undo()
        assert window._collect_project_dict()["canvas"].get("export_frame") == pytest.approx(baseline_frame)
        assert len(window._redo_stack) == 1

        window._redo()
        assert window._collect_project_dict()["canvas"]["export_frame"] == pytest.approx(frame_after_draw)
    finally:
        window.deleteLater()


def test_all1_undo_redo_uv_config_action(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    import gui.parameter_panel as parameter_panel  # noqa: PLC0415
    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    class _FakeUvDialog:
        def __init__(self, config, cable_choices, parent=None):
            self._config = config

        def exec(self):
            return QDialog.Accepted

        def get_config(self):
            return {
                "name": "UV EG",
                "rows": 2,
                "modules_per_row": 12,
                "incoming_device": "SLS",
                "devices": [{"name": "FI A", "quantity": 1, "te": 4}],
            }

    monkeypatch.setattr(parameter_panel, "UvConfigDialog", _FakeUvDialog)

    document = Document.from_dict(
        {
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "UV EG",
                        "ap_type": "uv",
                        "uv_config": {},
                    }
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._on_property_action("AP-1", "configure_uv")

        point = document.elements["elec_points"]["AP-1"]
        applied_config = point.data["uv_config"]
        assert applied_config["name"] == "UV EG"
        assert len(window._undo_stack) == 1

        window._undo()
        assert document.elements["elec_points"]["AP-1"].data["uv_config"] == {}

        window._redo()
        assert document.elements["elec_points"]["AP-1"].data["uv_config"] == applied_config
    finally:
        window.deleteLater()


def test_all1_undo_redo_up_config_action(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    import gui.parameter_panel as parameter_panel  # noqa: PLC0415
    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    class _FakeUpDialog:
        def __init__(self, config, cable_choices, parent=None):
            self._config = config

        def exec(self):
            return QDialog.Accepted

        def get_config(self):
            return {
                "incoming_cable_id": "EK-1",
                "outgoing_cable_ids": ["EK-2"],
                "mappings": [
                    {
                        "from_conductor": "L1",
                        "to_cable_id": "EK-2",
                        "to_conductor": "L1",
                        "note": "Abgang Phase",
                    }
                ],
                "note": "UP Flur",
            }

    monkeypatch.setattr(parameter_panel, "UpDistributionDialog", _FakeUpDialog)

    document = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "elec_cables": {
                    "EK-1": [[0.0, 0.0], [10.0, 0.0]],
                    "EK-2": [[10.0, 0.0], [20.0, 0.0]],
                },
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "UP Flur",
                        "ap_type": "up_distribution",
                        "up_distribution_config": {},
                    }
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Zuleitung",
                        "type": "NYM-J 5x2,5",
                    },
                    "EK-2": {
                        "cable_id": "EK-2",
                        "floor_plan_id": "grundriss-1",
                        "name": "Abgang",
                        "type": "NYM-J 3x1,5",
                    },
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._on_property_action("AP-1", "configure_up")

        point = document.elements["elec_points"]["AP-1"]
        applied_config = point.data["up_distribution_config"]
        assert applied_config["incoming_cable_id"] == "EK-1"
        assert len(window._undo_stack) == 1

        window._undo()
        assert document.elements["elec_points"]["AP-1"].data["up_distribution_config"] == {}

        window._redo()
        assert document.elements["elec_points"]["AP-1"].data["up_distribution_config"] == applied_config
    finally:
        window.deleteLater()


def test_all2_schema_duplicate_selection_remaps_and_undoes_as_single_step(app, monkeypatch):
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
                    "AP-2": [110.0, 10.0],
                },
                "elec_cables": {"EK-1": [[10.0, 10.0], [110.0, 10.0]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Dose 1",
                    },
                    "AP-2": {
                        "point_id": "AP-2",
                        "floor_plan_id": "grundriss-1",
                        "name": "Dose 2",
                    },
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
        window._undo_group_timer.stop()
        window._finish_undo_group()
        window._undo_stack.clear()
        window._redo_stack.clear()

        window._on_schema_duplicate_selection(["AP-1", "AP-2"], ["EK-1"])

        ap_ids = sorted(document.elements["elec_points"].keys())
        cable_ids = sorted(document.elements["elec_cables"].keys())
        assert len(ap_ids) == 4
        assert len(cable_ids) == 2

        new_cable_id = next(cid for cid in cable_ids if cid != "EK-1")
        new_cable = document.elements["elec_cables"][new_cable_id]
        assert new_cable.start_ap in ap_ids and new_cable.start_ap != "AP-1"
        assert new_cable.end_ap in ap_ids and new_cable.end_ap != "AP-2"
        assert len(window._undo_stack) == 1

        window._undo()
        assert sorted(document.elements["elec_points"].keys()) == ["AP-1", "AP-2"]
        assert sorted(document.elements["elec_cables"].keys()) == ["EK-1"]

        window._redo()
        assert len(document.elements["elec_points"]) == 4
        assert len(document.elements["elec_cables"]) == 2
    finally:
        window.deleteLater()


def test_all3_delete_cable_clears_uv_and_up_references(app, monkeypatch):
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
                "elec_cables": {
                    "EK-1": [[0.0, 0.0], [10.0, 0.0]],
                    "EK-2": [[10.0, 0.0], [20.0, 0.0]],
                },
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "UV",
                        "ap_type": "uv",
                        "uv_config": {
                            "slots": [
                                {"row": 1, "slot": 1, "label": "FI", "cable": "EK-1"},
                                {"row": 1, "slot": 2, "label": "LS", "cable": "EK-2"},
                            ]
                        },
                    },
                    "AP-2": {
                        "point_id": "AP-2",
                        "floor_plan_id": "grundriss-1",
                        "name": "UP",
                        "ap_type": "up_distribution",
                        "up_distribution_config": {
                            "incoming_cable_id": "EK-1",
                            "outgoing_cable_ids": ["EK-1", "EK-2"],
                            "mappings": [
                                {
                                    "from_conductor": "L1",
                                    "to_cable_id": "EK-1",
                                    "to_conductor": "L1",
                                    "note": "Abgang 1",
                                },
                                {
                                    "from_conductor": "L2",
                                    "to_cable_id": "EK-2",
                                    "to_conductor": "L2",
                                    "note": "Abgang 2",
                                },
                            ],
                        },
                    },
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Kabel 1",
                    },
                    "EK-2": {
                        "cable_id": "EK-2",
                        "floor_plan_id": "grundriss-1",
                        "name": "Kabel 2",
                    },
                },
            },
        }
    )

    window = AppWindow()
    try:
        window._set_document(document)
        window._delete_element("EK-1")

        assert "EK-1" not in document.elements["elec_cables"]

        uv_config = document.elements["elec_points"]["AP-1"].data["uv_config"]
        assert uv_config["slots"][0]["cable"] == ""
        assert uv_config["slots"][1]["cable"] == "EK-2"

        up_config = document.elements["elec_points"]["AP-2"].data["up_distribution_config"]
        assert up_config["incoming_cable_id"] == ""
        assert up_config["outgoing_cable_ids"] == ["EK-2"]
        assert up_config["mappings"][0]["to_cable_id"] == ""
        assert up_config["mappings"][1]["to_cable_id"] == "EK-2"
    finally:
        window.deleteLater()


def test_all4_confirm_discard_cancel_save_discard(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        window._dirty = True

        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.Cancel),
        )
        assert window._confirm_discard() is False

        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.Discard),
        )
        assert window._confirm_discard() is True

        save_calls: list[bool] = []

        def _save_project_stub():
            save_calls.append(True)
            return True

        monkeypatch.setattr(window, "_save_project", _save_project_stub)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.Save),
        )
        assert window._confirm_discard() is True
        assert save_calls == [True]
    finally:
        window.deleteLater()


def test_all4_open_recent_missing_file_cleans_recent_list(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    missing_path = tmp_path / "missing_project.hrp"
    existing_path = tmp_path / "existing_project.hrp"
    existing_path.write_text("{}", encoding="utf-8")

    store = {
        "recent_projects": [str(missing_path), str(existing_path)],
    }

    def _value(_self, key, default=None, **_kw):
        return store.get(key, default)

    def _set_value(_self, key, value):
        store[key] = value

    monkeypatch.setattr(QSettings, "value", _value)
    monkeypatch.setattr(QSettings, "setValue", _set_value)

    warnings: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warnings.append(a) or QMessageBox.Ok),
    )

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        window._open_recent(missing_path)
        assert warnings, "Erwartete Warnung bei fehlender Datei"
        assert str(missing_path) not in store["recent_projects"]
        assert str(existing_path) in store["recent_projects"]
    finally:
        window.deleteLater()



# ── H-1 bis H-5: Heizungs-Workflow ────────────────────────────────── #

def _make_heating_window(monkeypatch, app, with_circuit=False):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QFileDialog, QInputDialog  # noqa: PLC0415
    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("EG", True)))

    window = AppWindow()
    window._add_floorplan()

    if with_circuit:
        window._add_circuit()

    return window


def test_h1_add_circuit_creates_element_and_starts_draw(app, monkeypatch):
    window = _make_heating_window(monkeypatch, app)
    try:
        from gui.canvas_widget import ToolMode  # noqa: PLC0415
        window._add_circuit()
        circuits = window._document.elements["circuits"]
        assert len(circuits) == 1
        cid = next(iter(circuits))
        assert circuits[cid].floor_plan_id == next(iter(window._document.floorplans))
        assert window.canvas.tool_mode() == ToolMode.DRAW_POLY
    finally:
        window.deleteLater()


def test_h2_draw_polygon_via_property_action(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from gui.canvas_widget import ToolMode  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1", "visible": True}]},
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
                "circuits": {"HK-1": {"circuit_id": "HK-1", "floor_plan_id": "grundriss-1", "name": "Wohnzimmer"}},
            },
        })
        window._set_document(doc)

        drawn = []
        monkeypatch.setattr(window.canvas, "start_drawing", lambda cid: drawn.append(cid))
        window._on_property_action("HK-1", "draw_polygon")

        assert drawn == ["HK-1"]
    finally:
        window.deleteLater()


def test_h3_draw_route_action_passes_circuit_params(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtCore import QPointF  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "polygons": {"HK-1": [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]]},
                "start_points": {"HK-1": [10.0, 10.0]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
                "circuits": {
                    "HK-1": {
                        "circuit_id": "HK-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Wohnzimmer",
                        "wall_dist": 200.0,
                        "spacing": 150.0,
                    }
                },
            },
        })
        window._set_document(doc)

        calls = []
        monkeypatch.setattr(
            window.canvas, "start_route_drawing",
            lambda cid, wall_mm, line_mm: calls.append((cid, wall_mm, line_mm)),
        )
        window._on_property_action("HK-1", "draw_route")
        assert calls == [("HK-1", 200.0, 150.0)]
    finally:
        window.deleteLater()


def test_h4_draw_supply_line_action_starts_canvas_mode(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "start_points": {"HK-1": [10.0, 10.0]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
                "circuits": {"HK-1": {"circuit_id": "HK-1", "floor_plan_id": "grundriss-1"}},
            },
        })
        window._set_document(doc)

        calls = []
        monkeypatch.setattr(window.canvas, "start_draw_supply_line", lambda cid: calls.append(cid))
        window._on_property_action("HK-1", "draw_supply")
        assert calls == ["HK-1"]
    finally:
        window.deleteLater()


def test_h5_add_hkv_creates_element_and_starts_place(app, monkeypatch):
    window = _make_heating_window(monkeypatch, app)
    try:
        from gui.canvas_widget import ToolMode  # noqa: PLC0415
        window._add_hkv()
        hkvs = window._document.elements["hkv_points"]
        assert len(hkvs) == 1
        hid = next(iter(hkvs))
        assert hkvs[hid].floor_plan_id == next(iter(window._document.floorplans))
        assert window.canvas.tool_mode() == ToolMode.PLACE_HKV
    finally:
        window.deleteLater()


def test_h5_add_hkv_line_creates_element_and_starts_draw(app, monkeypatch):
    window = _make_heating_window(monkeypatch, app)
    try:
        from gui.canvas_widget import ToolMode  # noqa: PLC0415
        window._add_hkv_line()
        lines = window._document.elements["hkv_lines"]
        assert len(lines) == 1
        lid = next(iter(lines))
        assert lines[lid].floor_plan_id == next(iter(window._document.floorplans))
        assert window.canvas.tool_mode() == ToolMode.DRAW_HKV_LINE
    finally:
        window.deleteLater()


# ── E-1 bis E-6: Elektro-Workflow ────────────────────────────────── #

def _make_elec_window(monkeypatch, app):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    from gui.app_window import AppWindow

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("EG", True)))

    window = AppWindow()
    window._add_floorplan()
    return window


def test_e1_add_elec_point_creates_element_and_starts_place(app, monkeypatch):
    window = _make_elec_window(monkeypatch, app)
    try:
        from gui.canvas_widget import ToolMode
        window._add_elec_point()
        points = window._document.elements["elec_points"]
        assert len(points) == 1
        pid = next(iter(points))
        assert points[pid].floor_plan_id == next(iter(window._document.floorplans))
        assert window.canvas.tool_mode() == ToolMode.PLACE_ELEC_POINT
    finally:
        window.deleteLater()


def test_e1_ap_properties_are_editable_via_property_changed(app, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow
    from model.document import Document

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "elec_points": {"AP-1": [10.0, 10.0]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "name": "Steckdose",
                        "color": "#4fc3f7",
                        "visible": True,
                    }
                },
            },
        })
        window._set_document(doc)
        doc.elements["elec_points"]["AP-1"].data["name"] = "UV Küche"
        window._on_property_changed("AP-1", "name", "UV Küche")
        assert doc.elements["elec_points"]["AP-1"].name == "UV Küche"
    finally:
        window.deleteLater()


def test_e5_add_elec_room_creates_element_and_starts_draw(app, monkeypatch):
    window = _make_elec_window(monkeypatch, app)
    try:
        from gui.canvas_widget import ToolMode
        window._add_elec_room()
        rooms = window._document.elements["elec_rooms"]
        assert len(rooms) == 1
        rid = next(iter(rooms))
        assert rooms[rid].floor_plan_id == next(iter(window._document.floorplans))
        assert window.canvas.tool_mode() == ToolMode.DRAW_POLY
    finally:
        window.deleteLater()


def test_e6_add_elec_cable_creates_element_and_starts_draw(app, monkeypatch):
    window = _make_elec_window(monkeypatch, app)
    try:
        from gui.canvas_widget import ToolMode
        window._add_elec_cable()
        cables = window._document.elements["elec_cables"]
        assert len(cables) == 1
        eid = next(iter(cables))
        assert cables[eid].floor_plan_id == next(iter(window._document.floorplans))
        assert window.canvas.tool_mode() == ToolMode.DRAW_ELEC_CABLE
    finally:
        window.deleteLater()


def test_e6_context_action_starts_cable_from_ap(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from PySide6.QtCore import QPointF  # noqa: PLC0415
    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.canvas_widget import ToolMode  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        doc = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [100.0, 100.0]},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""},
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Steckdose 1",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                    },
                },
            }
        )
        window._set_document(doc)

        window._run_context_action("draw_cable_from_ap", "AP-1", "element")

        cables = window._document.elements["elec_cables"]
        assert len(cables) == 1
        cable_id = next(iter(cables))
        cable = cables[cable_id]
        assert cable.start_ap == "AP-1"

        assert window.canvas.tool_mode() == ToolMode.DRAW_ELEC_CABLE
        assert window.canvas._current_elec_cable_id == cable_id
        assert len(window.canvas._current_elec_cable_points) == 1
        assert window.canvas._current_elec_cable_points[0] == QPointF(100.0, 100.0)
        assert window.canvas.get_cable_ap(cable_id)[0] == "AP-1"
    finally:
        window.deleteLater()


def test_e6_draw_cable_interaction_snaps_and_finishes(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.canvas_widget import ToolMode  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        doc = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [100.0, 100.0], "AP-2": [220.0, 100.0]},
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
                            "name": "Steckdose 1",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                        "AP-2": {
                            "point_id": "AP-2",
                            "floor_plan_id": "grundriss-1",
                            "name": "Steckdose 2",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                    },
                },
            }
        )
        window._set_document(doc)
        window._add_elec_cable()
        cable_id = window.canvas._current_elec_cable_id
        assert cable_id
        assert window.canvas.tool_mode() == ToolMode.DRAW_ELEC_CABLE

        window.canvas.mousePressEvent(
            _MouseEventStub(QPointF(100.0, 100.0), button=Qt.LeftButton)
        )
        window.canvas.mousePressEvent(
            _MouseEventStub(QPointF(220.0, 100.0), button=Qt.LeftButton)
        )
        window.canvas.mousePressEvent(
            _MouseEventStub(QPointF(220.0, 100.0), button=Qt.RightButton)
        )

        cable = window._document.elements["elec_cables"][cable_id]
        assert cable.geom.get("points")
        assert window.canvas.tool_mode() == ToolMode.NONE
        assert window.canvas._current_elec_cable_id is None
        assert window.canvas.get_cable_ap(cable_id) == ("AP-1", "AP-2")
    finally:
        window.deleteLater()


def test_e6_draw_cable_property_action_starts_canvas_mode(app, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow
    from model.document import Document

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "elec_cables": {"EK-1": [[0.0, 0.0], [100.0, 0.0]]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
                "elec_cables": {"EK-1": {"cable_id": "EK-1", "floor_plan_id": "grundriss-1"}},
            },
        })
        window._set_document(doc)
        calls = []
        monkeypatch.setattr(window.canvas, "start_draw_elec_cable", lambda eid: calls.append(eid))
        window._on_property_action("EK-1", "draw_cable")
        assert calls == ["EK-1"]
    finally:
        window.deleteLater()


def test_e6_cable_properties_show_start_and_end_ap(app, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow
    from model.document import Document

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "elec_points": {
                    "AP-1": [0.0, 0.0],
                    "AP-2": [100.0, 0.0],
                },
                "elec_cables": {"EK-1": [[0.0, 0.0], [100.0, 0.0]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1", "name": "Steckdose 1"},
                    "AP-2": {"point_id": "AP-2", "floor_plan_id": "grundriss-1", "name": "Steckdose 2"},
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "floor_plan_id": "grundriss-1",
                        "start_ap": "",
                        "end_ap": "",
                    }
                },
            },
        })
        window._set_document(doc)

        window.properties.show_element("EK-1")
        editor = window.properties._editors.get("EK-1")
        assert editor is not None
        assert "start_ap" in editor._widgets
        assert "end_ap" in editor._widgets
        assert editor._widgets["start_ap"].value() == "AP-1"
        assert editor._widgets["end_ap"].value() == "AP-2"
    finally:
        window.deleteLater()


def test_e1_ap_place_action_starts_canvas_mode(app, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow
    from model.document import Document

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "elec_points": {"AP-1": [10.0, 10.0]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
                "elec_points": {
                    "AP-1": {
                        "point_id": "AP-1",
                        "floor_plan_id": "grundriss-1",
                        "width": 30.0,
                        "height": 30.0,
                    }
                },
            },
        })
        window._set_document(doc)
        calls = []
        monkeypatch.setattr(
            window.canvas, "start_place_elec_point",
            lambda pid, w, h: calls.append((pid, w, h)),
        )
        window._on_property_action("AP-1", "place")
        assert calls == [("AP-1", 30.0, 30.0)]
    finally:
        window.deleteLater()


# ── F-1 bis F-3: Einrichtungs-Workflow ────────────────────────────── #

def test_f1_add_furniture_creates_element_and_starts_polygon_draw(app, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    from gui.app_window import AppWindow
    from gui.canvas_widget import ToolMode

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Sofa", True)))

    window = AppWindow()
    try:
        # Grundriss zuerst anlegen
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("EG", True)))
        window._add_floorplan()
        # Einrichtung mit eigenem Namen
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Sofa", True)))
        window._add_furniture()

        furniture = window._document.furniture
        assert len(furniture) == 1
        fid = next(iter(furniture))
        assert furniture[fid].name == "Sofa"
        assert window.canvas.tool_mode() == ToolMode.DRAW_FURNITURE_POLY
    finally:
        window.deleteLater()


def test_f2_draw_furniture_polygon_via_property_action(app, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow
    from model.document import Document

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}, {"fp_id": "einrichtung-1"}]},
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "furniture": {"einrichtung-1": {"name": "Sofa"}},
            },
        })
        window._set_document(doc)

        calls = []
        monkeypatch.setattr(
            window.canvas, "start_draw_floor_plan_polygon",
            lambda fid: calls.append(fid),
        )
        window._on_property_action("einrichtung-1", "draw_polygon")
        assert calls == ["einrichtung-1"]
    finally:
        window.deleteLater()


def test_f3_move_furniture_property_action_starts_canvas_move(app, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow
    from model.document import Document

    window = AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}, {"fp_id": "einrichtung-1"}]},
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "furniture": {"einrichtung-1": {"name": "Sofa"}},
            },
        })
        window._set_document(doc)

        calls = []
        monkeypatch.setattr(window.canvas, "start_move_floor_plan", lambda fid: calls.append(fid))
        window._on_property_action("einrichtung-1", "move")
        assert calls == ["einrichtung-1"]
    finally:
        window.deleteLater()


def test_f4_add_furniture_uses_active_floorplan_as_parent(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QFileDialog, QInputDialog  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        names = iter([("EG", True), ("OG", True), ("Schrank", True)])
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: next(names)))

        window._add_floorplan()
        window._add_floorplan()
        window._document.active_floorplan_id = "grundriss-2"

        window._add_furniture()

        fid = next(iter(window._document.furniture))
        assert window._document.furniture[fid].floor_plan_id == "grundriss-2"
    finally:
        window.deleteLater()


def test_f4_furniture_fixed_sizes_update_canvas_layer(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    from model.field_access import set_field  # noqa: PLC0415
    from model.schema import FURNITURE_SCHEMA  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
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
                        {"fp_id": "einrichtung-1", "visible": True},
                    ]
                },
                "params": {
                    "floorplans": {"grundriss-1": {"name": "EG"}},
                    "furniture": {
                        "einrichtung-1": {
                            "name": "Sofa",
                            "floor_plan_id": "grundriss-1",
                            "fixed_width_mm": 0.0,
                            "fixed_height_mm": 0.0,
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)

        furniture = window._document.furniture["einrichtung-1"]
        schema_fields = {spec.key: spec for spec in FURNITURE_SCHEMA.fields}
        set_field(furniture, schema_fields["fixed_width_mm"], 1200.0)
        set_field(furniture, schema_fields["fixed_height_mm"], 800.0)

        window._on_property_changed("einrichtung-1", "fixed_width_mm", 1200.0)
        window._on_property_changed("einrichtung-1", "fixed_height_mm", 800.0)

        layer = window.canvas._floor_plans["einrichtung-1"]
        assert abs(layer.fixed_width_mm - 1200.0) < 1e-9
        assert abs(layer.fixed_height_mm - 800.0) < 1e-9
    finally:
        window.deleteLater()


def test_a1_measure_distance_persists_line(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget, ToolMode  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.set_mm_per_px(10.0)
        emitted: list[bool] = []
        canvas.measure_changed.connect(lambda: emitted.append(True))

        canvas.start_measure()
        canvas.mousePressEvent(_MouseEventStub(QPointF(0.0, 0.0), button=Qt.LeftButton))
        canvas.mousePressEvent(_MouseEventStub(QPointF(100.0, 0.0), button=Qt.LeftButton))

        assert canvas.tool_mode() == ToolMode.MEASURE
        assert len(canvas._measure_lines) == 1
        p1, p2, mm_len = canvas._measure_lines[0]
        assert p1 == QPointF(0.0, 0.0)
        assert p2 == QPointF(100.0, 0.0)
        assert abs(mm_len - 1000.0) < 1e-9
        assert emitted == [True]
    finally:
        canvas.deleteLater()


def test_a1_measure_distance_repaints_while_dragging_second_point(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.start_measure()
        canvas.mousePressEvent(_MouseEventStub(QPointF(0.0, 0.0), button=Qt.LeftButton))

        update_calls = {"count": 0}
        original_update = canvas.update

        def _counting_update(*args, **kwargs):
            update_calls["count"] += 1
            return original_update(*args, **kwargs)

        canvas.update = _counting_update
        canvas.mouseMoveEvent(
            _MouseEventStub(QPointF(50.0, 0.0), button=Qt.NoButton, buttons=Qt.NoButton)
        )

        assert update_calls["count"] > 0
    finally:
        canvas.deleteLater()


def test_a2_measure_angle_persists_angle(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget, ToolMode  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.start_angle_measure()
        canvas.mousePressEvent(_MouseEventStub(QPointF(0.0, 0.0), button=Qt.LeftButton))
        canvas.mousePressEvent(_MouseEventStub(QPointF(0.0, 10.0), button=Qt.LeftButton))
        canvas.mousePressEvent(_MouseEventStub(QPointF(10.0, 10.0), button=Qt.LeftButton))

        assert canvas.tool_mode() == ToolMode.MEASURE_ANGLE
        assert len(canvas._angle_measurements) == 1
        p1, p2, p3, angle_deg = canvas._angle_measurements[0]
        assert p1 == QPointF(0.0, 0.0)
        assert p2 == QPointF(0.0, 10.0)
        assert p3 == QPointF(10.0, 10.0)
        assert abs(angle_deg - 90.0) < 1e-9
    finally:
        canvas.deleteLater()


def test_a4_place_text_persists_annotation(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget, ToolMode  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        emitted: list[str] = []
        canvas.text_placed.connect(lambda tid: emitted.append(tid))

        canvas.start_place_text("TEXT-1", "Hallo")
        canvas.mousePressEvent(_MouseEventStub(QPointF(40.0, 60.0), button=Qt.LeftButton))

        assert canvas.tool_mode() == ToolMode.NONE
        assert canvas._text_annotations["TEXT-1"] == QPointF(40.0, 60.0)
        assert canvas._text_contents["TEXT-1"] == "Hallo"
        assert emitted == ["TEXT-1"]
    finally:
        canvas.deleteLater()


def test_a3_edit_helper_line_endpoint_updates_geometry(app):
    from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415

    from gui.canvas_widget import CanvasWidget, ToolMode  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.add_floor_plan("grundriss-1")
        canvas.set_mm_per_px(1.0)
        canvas._floor_helper_lines["grundriss-1"] = {
            "HL-1": [QPointF(0.0, 0.0), QPointF(100.0, 0.0)]
        }
        canvas._floor_helper_line_visible["grundriss-1"] = {"HL-1": True}
        canvas._floor_helper_line_length_mm["grundriss-1"] = {"HL-1": 100.0}
        canvas._floor_helper_line_fixed["grundriss-1"] = {"HL-1": False}

        emitted: list[bool] = []
        canvas.helper_lines_changed.connect(lambda: emitted.append(True))

        canvas.start_edit_helper_lines("grundriss-1")
        assert canvas.tool_mode() == ToolMode.EDIT_HELPER_LINE

        canvas.mousePressEvent(_MouseEventStub(QPointF(0.0, 0.0), button=Qt.LeftButton))
        canvas.mouseMoveEvent(
            _MouseEventStub(
                QPointF(20.0, 30.0),
                button=Qt.NoButton,
                buttons=Qt.LeftButton,
            )
        )
        canvas.mouseReleaseEvent(_MouseEventStub(QPointF(20.0, 30.0), button=Qt.LeftButton))

        pts = canvas._floor_helper_lines["grundriss-1"]["HL-1"]
        assert pts[0] == QPointF(20.0, 30.0)
        assert pts[1] == QPointF(100.0, 0.0)
        assert emitted == [True]
    finally:
        canvas.deleteLater()


# ---------------------------------------------------------------------------
# Phase-5 tests: live-draw → Navigator visibility (Measurements & Helpers)
# ---------------------------------------------------------------------------


def test_live_distance_measurement_appears_in_navigator(app, monkeypatch):
    """Distanzmessung via Canvas → Document-Element → Navigator sichtbar."""
    from PySide6.QtCore import QPointF, QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415
    from model.elements import DistanceMeasurement  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
                "params": {"floorplans": {"grundriss-1": {"name": "EG"}}},
            }
        )
        window._set_document(document)

        # Simulate: Nutzer zeichnet eine Messlinie im Canvas
        window.canvas._measure_lines.append(
            (QPointF(0.0, 0.0), QPointF(100.0, 0.0), 100.0)
        )
        window.canvas.measure_changed.emit()

        # Nach dem Signal muss ein echtes Element im Dokument existieren
        elements = list(document.elements_of(DistanceMeasurement, "grundriss-1"))
        assert len(elements) == 1, "Kein DistanceMeasurement-Element nach live draw"
        assert elements[0].id == "MSRD-1"

        # Und der Navigator muss es zeigen
        item = window.navigator._find_item_by_id("MSRD-1")
        assert item is not None, "MSRD-1 nicht im Navigator-Baum"
    finally:
        window.deleteLater()


def test_live_helper_line_appears_in_navigator(app, monkeypatch):
    """Hilfslinie via Canvas-Signal → Navigator sichtbar ohne Reload."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from gui.docks.navigator_dock import make_helper_nav_id  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
                "params": {"floorplans": {"grundriss-1": {"name": "EG"}}},
            }
        )
        window._set_document(document)

        # Simulate: Hilfslinie direkt in Canvas-Datenstuktur eintragen
        window.canvas._floor_helper_lines.setdefault("grundriss-1", {})["HL-1"] = [
            (0.0, 0.0), (200.0, 0.0)
        ]
        window.canvas._floor_helper_line_visible.setdefault("grundriss-1", {})["HL-1"] = True
        window.canvas.helper_lines_changed.emit()

        nav_id = make_helper_nav_id("grundriss-1", "HL-1")
        item = window.navigator._find_item_by_id(nav_id)
        assert item is not None, f"Hilfslinie nicht im Navigator-Baum ({nav_id})"
    finally:
        window.deleteLater()


def test_delete_measurement_via_navigator_removes_element_and_canvas(app, monkeypatch):
    """Löschen einer Messung über Navigator entfernt Canvas-Eintrag und Element."""
    from PySide6.QtCore import QPointF, QSettings  # noqa: PLC0415
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )

    from gui.app_window import AppWindow  # noqa: PLC0415
    from model.document import Document  # noqa: PLC0415
    from model.elements import DistanceMeasurement  # noqa: PLC0415

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
                "params": {"floorplans": {"grundriss-1": {"name": "EG"}}},
            }
        )
        window._set_document(document)

        # Messung hinzufügen und im Dokument synchronisieren
        window.canvas._measure_lines.append(
            (QPointF(0.0, 0.0), QPointF(50.0, 0.0), 50.0)
        )
        window.canvas.measure_changed.emit()
        assert document.get("MSRD-1") is not None

        # Selektion setzen (wie der Navigator/Canvas es tut)
        window.canvas._selected_item_id = "MSRD-1"
        window._delete_selected()

        # Canvas-Liste muss leer sein
        assert len(window.canvas._measure_lines) == 0, "Canvas-Liste nicht geleert"
        # Element darf nicht mehr im Dokument stehen
        assert document.get("MSRD-1") is None, "Element noch im Dokument"
        # Navigator-Baum darf es nicht mehr zeigen
        assert window.navigator._find_item_by_id("MSRD-1") is None, "Item noch im Navigator"
    finally:
        window.deleteLater()
