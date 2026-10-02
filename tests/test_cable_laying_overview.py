"""Cable locations in actual overview tables, including document refresh."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.docks.overview_dock import ProjectOverviewDock
from model.computed import project_overview_data
from model.document import Document


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _document():
    return Document.from_dict({
        "canvas": {
            "mm_per_px": 10.0,
            "floor_plans": [{"fp_id": "grundriss-1", "mm_per_px": 10.0}],
            "elec_points": {"AP-1": [0, 0], "AP-2": [100, 0]},
            "elec_cables": {"EK-1": [[0, 0], [100, 0]], "EK-2": [[0, 0], [200, 0]]},
            "cable_start_ap": {"EK-1": "AP-1", "EK-2": "AP-1"},
            "cable_end_ap": {"EK-1": "AP-2", "EK-2": "AP-2"},
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG"}},
            "elec_points": {"AP-1": {"point_id": "AP-1", "name": "UV"},
                            "AP-2": {"point_id": "AP-2", "name": "Dose"}},
            "elec_cables": {
                "EK-1": {"cable_id": "EK-1", "floor_plan_id": "grundriss-1", "name": "Same", "type": "5x1,5",
                         "laying_location": {"locations": ["floor"], "custom_enabled": False,
                                             "custom_text": "HIDDEN"}},
                "EK-2": {"cable_id": "EK-2", "floor_plan_id": "grundriss-1", "name": "Same", "type": "5x1,5",
                         "laying_location": {"locations": ["wall", "ceiling"], "custom_enabled": True,
                                             "custom_text": 'Kanal; "Süd"'}},
            },
        },
    })


def test_overview_model_union_does_not_duplicate_length():
    doc = _document()
    data = project_overview_data(doc)["electro"]
    assert data["materials"]["cable_length_by_type_m"] == {"5x1,5": 3.0}
    union = 'Auf dem Boden, In der Wand, In der Decke, Kanal; "Süd"'
    assert data["materials"]["cable_laying_location_by_type"] == {"5x1,5": union}
    assert data["cables"][0]["laying_location_text"] == "Auf dem Boden"
    for ap in data["rooms"][0]["aps"]:
        assert ap["laying_location_text"] == union
        assert [row["id"] for row in ap["cable_details"]] == ["EK-1", "EK-2"]
    assert all("HIDDEN" not in row["laying_location_text"] for row in data["cables"])


def test_overview_tables_locations_and_refresh(app):
    doc = _document()
    dock = ProjectOverviewDock()
    try:
        dock.set_document(doc)
        table = dock._elec_cable_table
        assert table.horizontalHeaderItem(5).text() == "Verlegeort"
        assert sorted(table.item(r, 5).text() for r in range(table.rowCount())) == [
            "Auf dem Boden", 'In der Wand, In der Decke, Kanal; "Süd"',
        ]
        assert all(not table.item(r, 5).flags() & Qt.ItemIsEditable for r in range(table.rowCount()))
        # Existing type-combos remain in their original column.
        assert all(table.cellWidget(r, 1) is not None for r in range(table.rowCount()))
        materials = dock._elec_cable_mat_table
        row = next(r for r in range(materials.rowCount()) if materials.item(r, 0).text() == "5x1,5")
        assert materials.item(row, 1).text() == "3.00 m"
        assert materials.item(row, 2).text() == 'Auf dem Boden, In der Wand, In der Decke, Kanal; "Süd"'
        rooms = dock._elec_room_table
        for r in range(rooms.rowCount()):
            if dock._elec_room_row_ap_ids.get(r):
                assert "EK-1" in rooms.item(r, 5).toolTip()
                assert "EK-2" in rooms.item(r, 5).toolTip()
                assert "Verlegeort" == rooms.horizontalHeaderItem(5).text()
        doc.get("EK-1").laying_location = {}
        doc.element_changed.emit("EK-1")
        dock.refresh_now()
        assert "–" in [table.item(r, 5).text() for r in range(table.rowCount())]
        assert "Auf dem Boden" not in materials.item(row, 2).text()
    finally:
        dock.deleteLater()
        app.processEvents()


def test_overview_old_cable_defaults_without_rewriting(app):
    doc = _document()
    for cable in doc.elements["elec_cables"].values():
        cable.data.pop("laying_location")
    before = doc.to_dict()
    dock = ProjectOverviewDock()
    try:
        dock.set_document(doc)
        assert all(dock._elec_cable_table.item(r, 5).text() == "–" for r in range(2))
        assert doc.to_dict() == before
    finally:
        dock.deleteLater()
        app.processEvents()