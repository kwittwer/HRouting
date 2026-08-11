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

from gui.elec_schema_window import ApNode, CableEdge, ElecSchemaWindow  # noqa: E402
from gui.pdf_export_dialog import PdfExportConfigDialog  # noqa: E402
from gui.schaltplan_window import SchaltplanWindow  # noqa: E402


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
