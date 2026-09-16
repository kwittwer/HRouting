"""GUI interaction tests for dialogs and auxiliary windows (offscreen)."""

from __future__ import annotations

import itertools
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
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem  # noqa: E402

from gui.app_window import AppWindow  # noqa: E402
from gui.elec_schema_window import (  # noqa: E402
    ApNode,
    CableEdge,
    ElecSchemaWindow,
    _AddCableDialog,
    _EditCableDialog,
)
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
        name="Küche Zuleitung:UV_HWR",
        cable_type="NYM 3x1,5",
        length_m=8.5,
        color="#ff9800",
        stroke_width_px=2.0,
        start_ap_id="AP-UV",
        end_ap_id="AP-1",
    )


def _scene_texts(window: ElecSchemaWindow) -> list[str]:
    return [item.text() for item in window.scene.items() if isinstance(item, QGraphicsSimpleTextItem)]


def _count_scene_tag(window: ElecSchemaWindow, tag: str) -> int:
    return sum(1 for item in window.scene.items() if item.data(0) == tag)


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
            room_choices=[("ER-1", "Küche")],
        )

        assert len(window.scene.items()) > 0
        assert window._layout_mode == "radial"
        assert window.cmb_root_ap.count() >= 3
        assert window.cmb_root_ap.findData("AP-UV") >= 0
        assert hasattr(window, "chk_rooms")
        assert not hasattr(window, "cmb_layout_mode")
        assert not hasattr(window, "chk_compact")
        assert not hasattr(window, "chk_room_colors")
        assert not hasattr(window, "btn_pack_tight")
        assert not hasattr(window, "chk_cable_labels")
        assert not hasattr(window, "btn_refresh")

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
def test_elec_schema_renders_state_cards_with_attributes(app):
    window = ElecSchemaWindow()
    try:
        ap_nodes = [
            ApNode(
                point_id="AP-UV",
                name="UV",
                room="Technik",
                ap_type="uv",
                has_distributor_function=True,
                is_connected=True,
                color="#4fc3f7",
                icon_path="",
                builtin_symbol="Steckdose",
                width_px=64.0,
                height_px=64.0,
            )
        ]
        cable_edges: list[CableEdge] = []
        for idx in range(1, 7):
            point_id = f"AP-{idx}"
            ap_nodes.append(
                ApNode(
                    point_id=point_id,
                    name=f"Verbraucher {idx}",
                    room="EG",
                    ap_type="standard",
                    has_distributor_function=False,
                    is_connected=True,
                    color="#ff9800",
                    icon_path="",
                    builtin_symbol="Steckdose",
                    width_px=64.0,
                    height_px=64.0,
                )
            )
            cable_edges.append(
                CableEdge(
                    cable_id=f"EK-{idx}",
                    name=f"Kabel {idx}",
                    cable_type="NYM 3x1,5",
                    length_m=10.0,
                    color="#ff9800",
                    stroke_width_px=2.0,
                    start_ap_id="AP-UV",
                    end_ap_id=point_id,
                )
            )

        window.set_data(ap_nodes=ap_nodes, cable_edges=cable_edges, room_choices=[])

        texts = _scene_texts(window)
        assert any("UV (AP-UV)" in text for text in texts)
        assert any("Raum: EG" in text for text in texts)
        assert any("Typ: Standard" in text for text in texts)
        assert any("Status: angeschlossen" in text for text in texts)
        xs = [point.x() for point in window._ap_scene_positions.values()]
        ys = [point.y() for point in window._ap_scene_positions.values()]
        assert abs(window._ap_scene_positions["AP-UV"].x()) < 1e-6
        assert abs(window._ap_scene_positions["AP-UV"].y()) < 1e-6
        assert max(xs) - min(xs) < 700.0
        assert max(ys) - min(ys) < 900.0
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_elec_schema_places_disconnected_components_in_grid(app):
    window = ElecSchemaWindow()
    try:
        ap_nodes = [
            ApNode(
                point_id="AP-UV",
                name="UV Hauptverteiler",
                room="Technik",
                ap_type="uv",
                has_distributor_function=True,
                is_connected=True,
                color="#4fc3f7",
                icon_path="",
                builtin_symbol="Steckdose",
                width_px=72.0,
                height_px=72.0,
            )
        ]
        cable_edges: list[CableEdge] = [
            CableEdge(
                cable_id="EK-1",
                name="Kabel 1",
                cable_type="NYM 3x1,5",
                length_m=10.0,
                color="#ff9800",
                stroke_width_px=2.0,
                start_ap_id="AP-UV",
                end_ap_id="AP-1",
            )
        ]
        ap_nodes.append(
            ApNode(
                point_id="AP-1",
                name="Verbraucher 1",
                room="EG",
                ap_type="standard",
                has_distributor_function=False,
                is_connected=True,
                color="#ff9800",
                icon_path="",
                builtin_symbol="Steckdose",
                width_px=72.0,
                height_px=72.0,
            )
        )
        ap_nodes.extend(
            [
                ApNode(
                    point_id="AP-2",
                    name="Neben 2",
                    room="Garage",
                    ap_type="standard",
                    has_distributor_function=False,
                    is_connected=True,
                    color="#43aa8b",
                    icon_path="",
                    builtin_symbol="Steckdose",
                    width_px=72.0,
                    height_px=72.0,
                ),
                ApNode(
                    point_id="AP-3",
                    name="Neben 3",
                    room="Garage",
                    ap_type="standard",
                    has_distributor_function=False,
                    is_connected=True,
                    color="#43aa8b",
                    icon_path="",
                    builtin_symbol="Steckdose",
                    width_px=72.0,
                    height_px=72.0,
                ),
            ]
        )
        window.set_data(ap_nodes=ap_nodes, cable_edges=cable_edges, room_choices=[])

        root_point = window._ap_scene_positions["AP-UV"]
        side_left = window._ap_scene_positions["AP-2"]
        side_right = window._ap_scene_positions["AP-3"]
        assert window._layout_mode == "radial"
        assert abs(root_point.x()) < 1e-6
        assert abs(root_point.y()) < 1e-6
        assert side_left.y() > 200.0
        assert side_right.y() > 200.0
        assert abs(side_left.y() - side_right.y()) < 80.0
        assert abs(side_left.x() - side_right.x()) > 120.0
        assert len(window._ap_items) == 4
        assert len(window._cable_items) == 1
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_elec_schema_cable_label_with_colon_renders(app):
    window = ElecSchemaWindow()
    try:
        ap_nodes = [_sample_uv_node(), _sample_consumer_node()]
        cable_edges = [
            CableEdge(
                cable_id="EK-1",
                name="KBL_UV_Zählerschrank:UV_HWR",
                cable_type="NYM 5x10",
                length_m=3.0,
                color="#ff9800",
                stroke_width_px=2.0,
                start_ap_id="AP-UV",
                end_ap_id="AP-1",
            )
        ]

        window.set_data(ap_nodes=ap_nodes, cable_edges=cable_edges, room_choices=[])
        window._set_selection(set(), {"EK-1"}, "EK-1")

        texts = _scene_texts(window)
        assert any("KBL_UV_Zählerschrank:UV_HWR" in text for text in texts)
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_elec_schema_room_controls_and_zones(app):
    window = ElecSchemaWindow()
    try:
        ap_nodes = [
            ApNode(
                point_id="AP-1",
                name="Steckdose Wohnen",
                room="Wohnzimmer",
                room_id="room-wohnen",
                ap_type="standard",
                has_distributor_function=False,
                is_connected=True,
                color="#4fc3f7",
                icon_path="",
                builtin_symbol="Steckdose",
                width_px=64.0,
                height_px=64.0,
            ),
            ApNode(
                point_id="AP-2",
                name="Lampe Wohnen",
                room="Wohnzimmer",
                room_id="room-wohnen",
                ap_type="standard",
                has_distributor_function=False,
                is_connected=True,
                color="#ff9800",
                icon_path="",
                builtin_symbol="Licht",
                width_px=64.0,
                height_px=64.0,
            ),
            ApNode(
                point_id="AP-3",
                name="Steckdose Kueche",
                room="Kueche",
                room_id="room-kueche",
                ap_type="standard",
                has_distributor_function=False,
                is_connected=True,
                color="#43aa8b",
                icon_path="",
                builtin_symbol="Steckdose",
                width_px=64.0,
                height_px=64.0,
            ),
        ]
        cable_edges = [
            CableEdge(
                cable_id="EK-1",
                name="Wohnen",
                cable_type="NYM 3x1,5",
                length_m=5.0,
                color="#ff9800",
                stroke_width_px=2.0,
                start_ap_id="AP-1",
                end_ap_id="AP-2",
            )
        ]

        window.set_data(
            ap_nodes=ap_nodes,
            cable_edges=cable_edges,
            room_choices=[],
            room_styles={
                "room-wohnen": {"name": "Wohnzimmer", "color": "#3366cc"},
                "room-kueche": {"name": "Kueche", "color": "#00aa88"},
            },
        )

        zones_before = _count_scene_tag(window, "room-zone")
        assert zones_before >= 2
        assert _count_scene_tag(window, "room-zone-label") == 0
        window.chk_rooms.setChecked(False)
        assert _count_scene_tag(window, "room-zone") == 0

        window.chk_rooms.setChecked(True)
        assert _count_scene_tag(window, "room-zone") >= 2
        assert _count_scene_tag(window, "room-zone-label") == 0

        texts = _scene_texts(window)
        assert any("Steckdose Wohnen (AP-1)" in text for text in texts)
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_elec_schema_cable_dialogs_show_ap_name_with_id(app):
    ap_nodes = {
        "AP-UV": _sample_uv_node(),
        "AP-1": _sample_consumer_node(),
    }
    edge = _sample_cable()

    add_dialog = _AddCableDialog(ap_nodes)
    edit_dialog = _EditCableDialog(edge, ap_nodes)
    try:
        add_options = {add_dialog.cmb_start_ap.itemText(i) for i in range(add_dialog.cmb_start_ap.count())}
        edit_options = {edit_dialog.cmb_end_ap.itemText(i) for i in range(edit_dialog.cmb_end_ap.count())}

        assert add_dialog.le_name.isReadOnly() is True
        assert edit_dialog.le_name.isReadOnly() is True
        assert "UV EG (AP-UV)" in add_options
        assert "Steckdose Küche (AP-1)" in add_options
        assert "UV EG (AP-UV)" in edit_options
        assert "Steckdose Küche (AP-1)" in edit_options
        assert add_dialog.le_name.text() == "KBL_?:?"
        assert edit_dialog.cmb_start_ap.currentText() == "UV EG (AP-UV)"
        assert edit_dialog.cmb_end_ap.currentText() == "Steckdose Küche (AP-1)"
        assert edit_dialog.le_name.text() == "KBL_UV EG:Steckdose Küche"

        add_dialog.cmb_start_ap.setCurrentIndex(add_dialog.cmb_start_ap.findData("AP-UV"))
        add_dialog.cmb_end_ap.setCurrentIndex(add_dialog.cmb_end_ap.findData("AP-1"))
        assert add_dialog.le_name.text() == "KBL_UV EG:Steckdose Küche"
    finally:
        add_dialog.deleteLater()
        edit_dialog.deleteLater()


@pytest.mark.gui
def test_kicad_ap_choices_keep_room_prefix_and_name_id_suffix(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [10, 10], "AP-2": [20, 20]},
                    "elec_rooms": {
                        "ER-1": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    },
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Steckdose Küche",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                        "AP-2": {
                            "point_id": "AP-2",
                            "floor_plan_id": "grundriss-1",
                            "name": "",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                    },
                    "elec_rooms": {
                        "ER-1": {
                            "room_id": "ER-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Küche",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        choices = window._kicad_elec_point_choices_with_rooms()

        assert ("AP-1", "Küche / Steckdose Küche (AP-1)") in choices
        assert ("AP-2", "Küche / AP-2") in choices
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_git_commit_dialog_uses_large_multiline_editor(app):
    from gui.app_window import _GitCommitDialog  # noqa: PLC0415

    dialog = _GitCommitDialog("Update Planung", None)
    try:
        assert dialog.minimumWidth() >= 640
        assert dialog.minimumHeight() >= 360
        assert dialog._message_edit.toPlainText() == "Update Planung"
        assert dialog._push_checkbox.isChecked() is True

        dialog._message_edit.setPlainText("  Erste Zeile\nZweite Zeile  ")
        assert dialog.commit_message() == "Erste Zeile\nZweite Zeile"
    finally:
        dialog.deleteLater()


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
def test_properties_ap_rename_updates_connected_cable_name(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [10.0, 10.0], "AP-2": [110.0, 10.0]},
                    "elec_cables": {"EK-1": [[10.0, 10.0], [110.0, 10.0]]},
                    "cable_start_ap": {"EK-1": "AP-1"},
                    "cable_end_ap": {"EK-1": "AP-2"},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Dose 1",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                        "AP-2": {
                            "point_id": "AP-2",
                            "floor_plan_id": "grundriss-1",
                            "name": "Dose 2",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                    },
                    "elec_cables": {
                        "EK-1": {
                            "cable_id": "EK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "KBL_Dose 1:Dose 2",
                            "type": "3x1,5",
                            "start_ap": "AP-1",
                            "end_ap": "AP-2",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("AP-1")

        ap_editor = window.properties._editors["AP-1"]
        name_widget = ap_editor._widgets["name"]
        name_widget._edit.setText("UV Küche")
        name_widget._edit.editingFinished.emit()
        app.processEvents()

        assert document.elements["elec_points"]["AP-1"].name == "UV Küche"
        assert document.elements["elec_cables"]["EK-1"].name == "KBL_UV Küche:Dose 2"

        window.properties.show_element("EK-1")
        cable_editor = window.properties._editors["EK-1"]
        assert cable_editor._widgets["name"].value() == "KBL_UV Küche:Dose 2"

        overview_name = window.overview_electro_cables._elec_cable_table.item(0, 0)
        assert overview_name is not None
        assert overview_name.text() == "KBL_UV Küche:Dose 2"
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_navigator_keeps_renamed_ap_selected(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [10.0, 10.0]},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Dose 1",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.navigator.select("AP-1")
        window.properties.show_element("AP-1")

        ap_editor = window.properties._editors["AP-1"]
        name_widget = ap_editor._widgets["name"]
        name_widget._edit.setText("UV Küche")
        name_widget._edit.editingFinished.emit()
        app.processEvents()

        assert window.navigator.selected_ids() == ["AP-1"]
        item = window.navigator._find_item_by_id("AP-1")
        assert item is not None
        assert item.text(0) == "UV Küche"
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_open_cable_properties_refreshes_after_ap_rename(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {"AP-1": [10.0, 10.0], "AP-2": [110.0, 10.0]},
                    "elec_cables": {"EK-1": [[10.0, 10.0], [110.0, 10.0]]},
                    "cable_start_ap": {"EK-1": "AP-1"},
                    "cable_end_ap": {"EK-1": "AP-2"},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "elec_points": {
                        "AP-1": {
                            "point_id": "AP-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "Dose 1",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                        "AP-2": {
                            "point_id": "AP-2",
                            "floor_plan_id": "grundriss-1",
                            "name": "Dose 2",
                            "builtin_symbol": "Steckdose",
                            "visible": True,
                        },
                    },
                    "elec_cables": {
                        "EK-1": {
                            "cable_id": "EK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "KBL_Dose 1:Dose 2",
                            "type": "3x1,5",
                            "start_ap": "AP-1",
                            "end_ap": "AP-2",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("EK-1")

        cable_editor = window.properties._editors["EK-1"]
        assert cable_editor._widgets["name"].value() == "KBL_Dose 1:Dose 2"

        document.elements["elec_points"]["AP-1"].data["name"] = "UV Küche"
        window._on_property_changed("AP-1", "name", "UV Küche")
        app.processEvents()

        assert cable_editor._widgets["name"].value() == "KBL_UV Küche:Dose 2"
    finally:
        window.deleteLater()


@pytest.mark.gui
def test_open_cable_properties_refreshes_name_after_endpoint_change(app, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    window = AppWindow()
    try:
        document = Document.from_dict(
            {
                "canvas": {
                    "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                    "elec_points": {
                        "AP-1": [10.0, 10.0],
                        "AP-2": [110.0, 10.0],
                        "AP-3": [210.0, 10.0],
                    },
                    "elec_cables": {"EK-1": [[10.0, 10.0], [110.0, 10.0]]},
                    "cable_start_ap": {"EK-1": "AP-1"},
                    "cable_end_ap": {"EK-1": "AP-2"},
                },
                "params": {
                    "floorplans": {
                        "grundriss-1": {"name": "EG", "visible": True, "file_path": ""}
                    },
                    "elec_points": {
                        "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1", "name": "Dose 1", "builtin_symbol": "Steckdose", "visible": True},
                        "AP-2": {"point_id": "AP-2", "floor_plan_id": "grundriss-1", "name": "Dose 2", "builtin_symbol": "Steckdose", "visible": True},
                        "AP-3": {"point_id": "AP-3", "floor_plan_id": "grundriss-1", "name": "Dose 3", "builtin_symbol": "Steckdose", "visible": True},
                    },
                    "elec_cables": {
                        "EK-1": {
                            "cable_id": "EK-1",
                            "floor_plan_id": "grundriss-1",
                            "name": "KBL_Dose 1:Dose 2",
                            "type": "3x1,5",
                            "start_ap": "AP-1",
                            "end_ap": "AP-2",
                            "visible": True,
                        }
                    },
                },
            }
        )
        window._set_document(document)
        window.properties.show_element("EK-1")

        cable_editor = window.properties._editors["EK-1"]
        assert cable_editor._widgets["name"].value() == "KBL_Dose 1:Dose 2"

        end_widget = cable_editor._widgets["end_ap"]
        end_widget._combo.setCurrentIndex(end_widget._combo.findData("AP-3"))
        app.processEvents()

        assert document.elements["elec_cables"]["EK-1"].end_ap == "AP-3"
        assert cable_editor._widgets["name"].value() == "KBL_Dose 1:Dose 3"
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
