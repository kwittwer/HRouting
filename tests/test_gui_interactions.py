"""GUI interaction tests for dialogs and auxiliary windows (offscreen)."""

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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.app_window import AppWindow  # noqa: E402
from gui.elec_schema_window import ApNode, CableEdge, ElecSchemaWindow  # noqa: E402
from gui.pdf_export_dialog import PdfExportConfigDialog  # noqa: E402
from gui.schaltplan_window import SchaltplanWindow  # noqa: E402
from model.document import Document  # noqa: E402
from model.elements import Hkv  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _sample_page() -> dict:
    return {
        "id": "plan-1",
        "type": "plan",
        "title": "Planseite EG",
        "enabled": True,
        "show_background": True,
        "show_heating": True,
        "show_elektro": True,
        "element_visibility": {
            "background": True,
            "furniture": True,
            "hk": True,
            "hkv": True,
            "hkv_line": True,
            "ap": True,
            "room": True,
            "kv": True,
            "text": True,
        },
        "floor_plan_id": "grundriss-1",
        "source_rect": None,
    }


def _sample_uv_node() -> ApNode:
    return ApNode(
        point_id="AP-UV",
        name="UV EG",
        room="Technik",
        ap_type="uv",
        has_distributor_function=True,
        is_connected=True,
        color="#4fc3f7",
        icon_path="",
        builtin_symbol="Steckdose",
        width_px=30.0,
        height_px=30.0,
    )


def _sample_consumer_node() -> ApNode:
    return ApNode(
        point_id="AP-1",
        name="Steckdose Küche",
        room="Küche",
        ap_type="standard",
        has_distributor_function=False,
        is_connected=True,
        color="#ff9800",
        icon_path="",
        builtin_symbol="Steckdose",
        width_px=30.0,
        height_px=30.0,
    )


def _sample_cable() -> CableEdge:
    return CableEdge(
        cable_id="EK-1",
        name="Küche Zuleitung",
        cable_type="NYM 3x1,5",
        length_m=8.5,
        color="#ff9800",
        stroke_width_px=2.0,
        start_ap_id="AP-UV",
        end_ap_id="AP-1",
    )


@pytest.mark.gui
def test_pdf_export_dialog_add_remove_and_edit_title(app):
    dialog = PdfExportConfigDialog(
        pages=[_sample_page()],
        floor_plans=[("grundriss-1", "EG")],
        svg_size=(1000.0, 700.0),
    )
    try:
        assert dialog.tree.topLevelItemCount() == 1

        QTest.mouseClick(dialog.btn_add_plan, Qt.MouseButton.LeftButton)
        assert dialog.tree.topLevelItemCount() == 2

        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
        dialog.le_title.setText("Planseite Erdgeschoss")

        pages = dialog.get_pages()
        assert pages[0]["title"] == "Planseite Erdgeschoss"

        QTest.mouseClick(dialog.btn_remove, Qt.MouseButton.LeftButton)
        assert dialog.tree.topLevelItemCount() == 1
    finally:
        dialog.deleteLater()


@pytest.mark.gui
def test_pdf_export_dialog_add_heating_circuit_page(app):
    dialog = PdfExportConfigDialog(
        pages=[_sample_page()],
        floor_plans=[("grundriss-1", "EG")],
        svg_size=(1000.0, 700.0),
        heating_circuits=[("HK-1", "Wohnzimmer"), ("HK-2", "Kueche")],
    )
    try:
        initial_count = dialog.tree.topLevelItemCount()
        QTest.mouseClick(dialog.btn_add_heating_circuit, Qt.MouseButton.LeftButton)

        assert dialog.tree.topLevelItemCount() == initial_count + 1
        current = dialog.tree.currentItem()
        assert current is not None
        page = current.data(0, Qt.UserRole)
        assert isinstance(page, dict)
        assert page.get("type") == "heating_circuit"

        cb = dialog._circuit_checks["HK-1"]
        cb.setChecked(True)
        updated = dialog._current_page()
        assert updated is not None
        assert updated.get("circuit_ids") == ["HK-1"]
    finally:
        dialog.deleteLater()


@pytest.mark.gui
def test_schaltplan_window_updates_uv_selection_and_tabs(app):
    window = SchaltplanWindow()
    try:
        ap_nodes = {
            "AP-UV": _sample_uv_node(),
            "AP-1": _sample_consumer_node(),
        }
        cable_edges = {"EK-1": _sample_cable()}

        window.set_data(ap_nodes, cable_edges, {"AP-1": "Küche"})

        assert window._cmb_uv.count() >= 1
        assert "UV EG" in window._cmb_uv.itemText(0)

        window._tabs.setCurrentIndex(1)
        window._tabs.setCurrentIndex(2)
        assert window._scene_uv is not None
        assert window._scene_circ is not None
        assert window._scene_hier is not None

        before = window._lbl_zoom.text()
        QTest.mouseClick(window._btn_zoom_in, Qt.MouseButton.LeftButton)
        after = window._lbl_zoom.text()
        assert before != after
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_elec_schema_window_renders_and_delete_signals(app):
    window = ElecSchemaWindow()
    try:
        ap_nodes = [_sample_uv_node(), _sample_consumer_node()]
        cable_edges = [_sample_cable()]

        deleted_aps: list[str] = []
        deleted_cables: list[str] = []
        window.delete_ap_requested.connect(deleted_aps.append)
        window.delete_cable_requested.connect(deleted_cables.append)

        window.set_data(
            ap_nodes=ap_nodes,
            cable_edges=cable_edges,
            manual_positions={"AP-UV": (100.0, 120.0), "AP-1": (240.0, 120.0)},
            room_choices=[("ER-1", "Küche")],
        )

        assert len(window.scene.items()) > 0

        zoom_before = window.lbl_zoom.text()
        QTest.mouseClick(window.btn_zoom_in, Qt.MouseButton.LeftButton)
        zoom_after = window.lbl_zoom.text()
        assert zoom_before != zoom_after

        window._delete_ids(["AP-1"], ["EK-1"])
        assert deleted_aps == ["AP-1"]
        assert deleted_cables == ["EK-1"]
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_properties_edit_name_updates_document_and_undo_redo(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {"floor_plans": [{"fp_id": "grundriss-1", "visible": True}]},
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Wohnzimmer",
                            "diameter": 16.0,
                            "spacing": 150.0,
                            "wall_dist": 200.0,
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("HK-1")

        editor = window.properties._editors["HK-1"]
        name_widget = editor._widgets["name"]
        name_widget._edit.setText("Kueche")
        name_widget._edit.editingFinished.emit()

        assert document.elements["circuits"]["HK-1"].data["name"] == "Kueche"

        window._undo()
        assert document.elements["circuits"]["HK-1"].data["name"] == "Wohnzimmer"

        window._redo()
        assert document.elements["circuits"]["HK-1"].data["name"] == "Kueche"
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_global_settings_edit_updates_and_undo_redo(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {"floor_plans": [{"fp_id": "grundriss-1", "visible": True}]},
                "params": {
                    "t_supply": 35.0,
                    "t_return": 30.0,
                    "t_norm_outdoor": -12.0,
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_global_settings()

        global_editor = window.properties._global_editor
        assert global_editor is not None
        t_supply_widget = global_editor._widgets["t_supply"]
        t_supply_widget._spin.setValue(40.0)

        assert float(document.settings["t_supply"]) == 40.0

        window._undo()
        assert float(document.settings["t_supply"]) == 35.0

        window._redo()
        assert float(document.settings["t_supply"]) == 40.0
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_distributor_dropdown_updates_when_hkv_added(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {"floor_plans": [{"fp_id": "grundriss-1", "visible": True}]},
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Wohnzimmer",
                            "distributor": "",
                            "diameter": 16.0,
                            "spacing": 150.0,
                            "wall_dist": 200.0,
                            "visible": True,
                        }
                    },
                    "hkv_points": {
                        "HKV-1": {
                            "hkv_id": "HKV-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Verteiler EG",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("HK-1")

        editor = window.properties._editors["HK-1"]
        combo = editor._widgets["distributor"]._combo
        options_before = {combo.itemText(i) for i in range(combo.count())}
        assert "Verteiler EG" in options_before
        assert "Verteiler OG" not in options_before

        document.add(
            Hkv.create(
                "HKV-2",
                floor_plan_id="grundriss-1",
                name="Verteiler OG",
                visible=True,
            )
        )
        window._emit_structure_changed()
        window.properties.refresh_current()

        options_after = {combo.itemText(i) for i in range(combo.count())}
        assert "Verteiler OG" in options_after
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_distributor_dropdown_and_value_cleanup_when_hkv_deleted(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {"floor_plans": [{"fp_id": "grundriss-1", "visible": True}]},
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "circuits": {
                        "HK-1": {
                            "circuit_id": "HK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Wohnzimmer",
                            "distributor": "Verteiler EG",
                            "diameter": 16.0,
                            "spacing": 150.0,
                            "wall_dist": 200.0,
                            "visible": True,
                        }
                    },
                    "hkv_points": {
                        "HKV-1": {
                            "hkv_id": "HKV-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Verteiler EG",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("HK-1")

        editor = window.properties._editors["HK-1"]
        combo = editor._widgets["distributor"]._combo
        options_before = {combo.itemText(i) for i in range(combo.count())}
        assert "Verteiler EG" in options_before

        window._cleanup_references_before_delete("HKV-1")
        document.remove("HKV-1")
        window._emit_structure_changed()
        window.properties.refresh_current()

        circuit = document.elements["circuits"]["HK-1"]
        assert (circuit.distributor or "") == ""

        options_after = {combo.itemText(i) for i in range(combo.count())}
        assert "Verteiler EG" not in options_after
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_mixed_selection_shows_only_shared_fields(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [100.0, 100.0]},
                    "elec_cables": {"EK-1": [[100.0, 100.0], [300.0, 100.0]]},
                    "cable_start_ap": {"EK-1": "AP-1"},
                    "cable_end_ap": {"EK-1": ""},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "AP 1",
                            "color": "#4fc3f7",
                            "visible": True,
                            "label_visible": True,
                            "label_size": 12.0,
                            "builtin_symbol": "Steckdose",
                        }
                    },
                    "elec_cables": {
                        "EK-1": {
                            "cable_id": "EK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Kabel 1",
                            "color": "#ff9800",
                            "visible": True,
                            "label_visible": True,
                            "label_size": 12.0,
                            "type": "3x1,5",
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_elements(["AP-1", "EK-1"])

        editor = window.properties._multi_editor
        assert editor is not None
        keys = set(editor._widgets.keys())
        assert {"color", "visible", "label_visible", "label_size"}.issubset(keys)
        assert "builtin_symbol" not in keys
        assert "ap_type" not in keys
        assert "type" not in keys
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_mixed_batch_edit_undo_redo_is_single_step(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [100.0, 100.0]},
                    "elec_cables": {"EK-1": [[100.0, 100.0], [300.0, 100.0]]},
                    "cable_start_ap": {"EK-1": "AP-1"},
                    "cable_end_ap": {"EK-1": ""},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "AP 1",
                            "color": "#4fc3f7",
                            "visible": True,
                            "label_visible": True,
                            "label_size": 12.0,
                            "builtin_symbol": "Steckdose",
                        }
                    },
                    "elec_cables": {
                        "EK-1": {
                            "cable_id": "EK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Kabel 1",
                            "color": "#ff9800",
                            "visible": True,
                            "label_visible": True,
                            "label_size": 12.0,
                            "type": "3x1,5",
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_elements(["AP-1", "EK-1"])

        editor = window.properties._multi_editor
        assert editor is not None

        color_before_ap = str(document.elements["elec_points"]["AP-1"].data.get("color") or "")
        color_before_ek = str(document.elements["elec_cables"]["EK-1"].data.get("color") or "")
        undo_len_before = len(window._undo_stack)

        editor._on_field_changed("color", "#123456")

        assert str(document.elements["elec_points"]["AP-1"].data.get("color") or "") == "#123456"
        assert str(document.elements["elec_cables"]["EK-1"].data.get("color") or "") == "#123456"
        assert len(window._undo_stack) == undo_len_before + 1

        window._undo()
        assert str(document.elements["elec_points"]["AP-1"].data.get("color") or "") == color_before_ap
        assert str(document.elements["elec_cables"]["EK-1"].data.get("color") or "") == color_before_ek

        window._redo()
        assert str(document.elements["elec_points"]["AP-1"].data.get("color") or "") == "#123456"
        assert str(document.elements["elec_cables"]["EK-1"].data.get("color") or "") == "#123456"
    finally:
        window.deleteLater()
