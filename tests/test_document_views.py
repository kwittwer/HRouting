"""Tests für die dict-kompatiblen Document-Proxy-Views."""

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

from PySide6.QtCore import QPointF  # noqa: E402

from model.document import Document  # noqa: E402
from model.elements import Circuit, ElecPoint, TextAnnotation  # noqa: E402
from model.views import (  # noqa: E402
    POINT,
    POINT_LIST,
    RAW,
    SIZE,
    DocumentMapView,
    NestedEntryView,
    ParamsMapView,
)


def _document() -> Document:
    return Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1"}],
                "polygons": {"HK-1": [[0, 0], [10, 0], [10, 10]]},
                "elec_points": {"AP-1": [5, 5]},
                "elec_point_size_px": {"AP-1": [30, 30]},
                "text_annotations": {
                    "TEXT-1": {"pos": [1, 2], "content": "Hallo", "font_size": 14.0}
                },
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "circuits": {
                    "HK-1": {
                        "circuit_id": "HK-1",
                        "floor_plan_id": "grundriss-1",
                        "visible": True,
                    },
                    "HK-2": {
                        "circuit_id": "HK-2",
                        "floor_plan_id": "grundriss-1",
                        "visible": False,
                    },
                },
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1"}
                },
                "text_annotations": {"TEXT-1": {"text_id": "TEXT-1"}},
            },
        }
    )


# ---------------------------------------------------------------------------
# DocumentMapView
# ---------------------------------------------------------------------------


def test_map_view_reads_and_converts_points():
    doc = _document()
    view = DocumentMapView(doc, "elec_points", ElecPoint, POINT)
    point = view["AP-1"]
    assert isinstance(point, QPointF)
    assert (point.x(), point.y()) == (5.0, 5.0)


def test_map_view_write_lands_in_document():
    doc = _document()
    view = DocumentMapView(doc, "elec_points", ElecPoint, POINT)
    view["AP-1"] = QPointF(42.0, 7.0)
    assert doc.to_dict()["canvas"]["elec_points"]["AP-1"] == [42.0, 7.0]


def test_map_view_point_list_roundtrip():
    doc = _document()
    view = DocumentMapView(doc, "polygons", Circuit, POINT_LIST)
    polygon = view["HK-1"]
    assert [(p.x(), p.y()) for p in polygon] == [(0, 0), (10, 0), (10, 10)]

    view["HK-1"] = [QPointF(1, 1), QPointF(2, 2)]
    assert doc.to_dict()["canvas"]["polygons"]["HK-1"] == [[1.0, 1.0], [2.0, 2.0]]


def test_map_view_size_conversion():
    doc = _document()
    view = DocumentMapView(doc, "elec_point_size_px", ElecPoint, SIZE)
    assert view["AP-1"] == (30.0, 30.0)
    view["AP-1"] = (40.0, 20.0)
    assert doc.to_dict()["canvas"]["elec_point_size_px"]["AP-1"] == [40.0, 20.0]


def test_map_view_iteration_only_yields_existing_entries():
    doc = _document()
    view = DocumentMapView(doc, "polygons", Circuit, POINT_LIST)
    assert list(view) == ["HK-1"]  # HK-2 hat kein Polygon
    assert "HK-2" not in view
    assert len(view) == 1


def test_map_view_with_default_yields_all_elements():
    doc = _document()
    view = DocumentMapView(
        doc, "route_wall_dist_px", Circuit, RAW, default=0.0, has_default=True
    )
    assert set(view) == {"HK-1", "HK-2"}
    assert view["HK-1"] == 0.0
    assert view.get("HK-2") == 0.0


def test_map_view_delete_and_pop():
    doc = _document()
    view = DocumentMapView(doc, "polygons", Circuit, POINT_LIST)
    popped = view.pop("HK-1")
    assert len(popped) == 3
    assert "HK-1" not in view
    assert view.pop("HK-1", "fallback") == "fallback"


def test_map_view_setdefault_does_not_overwrite():
    doc = _document()
    view = DocumentMapView(doc, "polygons", Circuit, POINT_LIST)
    existing = view.setdefault("HK-1", [])
    assert len(existing) == 3
    created = view.setdefault("HK-2", [QPointF(0, 0)])
    assert len(created) == 1
    assert "HK-2" in doc.to_dict()["canvas"]["polygons"]


def test_map_view_clear_removes_all_entries():
    doc = _document()
    view = DocumentMapView(doc, "polygons", Circuit, POINT_LIST)
    view.clear()
    assert len(view) == 0
    assert doc.to_dict()["canvas"]["polygons"] == {}


def test_map_view_unknown_key_is_preserved_as_orphan():
    doc = _document()
    view = DocumentMapView(doc, "polygons", Circuit, POINT_LIST)
    view["HK-99"] = [QPointF(3, 3)]
    # Kein Element vorhanden -> darf nicht verloren gehen
    assert doc.canvas_orphans["polygons"]["HK-99"] == [[3.0, 3.0]]


def test_map_view_notifies_on_change():
    doc = _document()
    seen: list[str] = []
    view = DocumentMapView(
        doc, "elec_points", ElecPoint, POINT, on_change=seen.append
    )
    view["AP-1"] = QPointF(1, 1)
    assert seen == ["AP-1"]


# ---------------------------------------------------------------------------
# NestedEntryView
# ---------------------------------------------------------------------------


def test_nested_view_reads_field():
    doc = _document()
    view = NestedEntryView(doc, "text_annotations", "content", TextAnnotation, RAW, "")
    assert view["TEXT-1"] == "Hallo"


def test_nested_view_writes_field_without_touching_siblings():
    doc = _document()
    view = NestedEntryView(doc, "text_annotations", "content", TextAnnotation, RAW, "")
    view["TEXT-1"] = "Welt"
    entry = doc.to_dict()["canvas"]["text_annotations"]["TEXT-1"]
    assert entry["content"] == "Welt"
    assert entry["pos"] == [1, 2]  # unverändert
    assert entry["font_size"] == 14.0


def test_nested_view_point_conversion():
    doc = _document()
    view = NestedEntryView(doc, "text_annotations", "pos", TextAnnotation, POINT)
    assert view["TEXT-1"] == QPointF(1.0, 2.0)
    view["TEXT-1"] = QPointF(9.0, 8.0)
    assert doc.to_dict()["canvas"]["text_annotations"]["TEXT-1"]["pos"] == [9.0, 8.0]


# ---------------------------------------------------------------------------
# ParamsMapView
# ---------------------------------------------------------------------------


def test_params_view_reads_visibility():
    doc = _document()
    view = ParamsMapView(doc, "visible", (Circuit,), RAW, True)
    assert view["HK-1"] is True
    assert view["HK-2"] is False


def test_params_view_write_lands_in_params():
    doc = _document()
    view = ParamsMapView(doc, "visible", (Circuit,), RAW, True)
    view["HK-1"] = False
    assert doc.to_dict()["params"]["circuits"]["HK-1"]["visible"] is False


def test_params_view_spans_multiple_element_types():
    doc = _document()
    view = ParamsMapView(doc, "visible", (Circuit, ElecPoint), RAW, True)
    assert set(view) == {"HK-1", "HK-2", "AP-1"}
    view["AP-1"] = False
    assert doc.to_dict()["params"]["elec_points"]["AP-1"]["visible"] is False


def test_params_view_get_returns_default_for_unknown():
    doc = _document()
    view = ParamsMapView(doc, "visible", (Circuit,), RAW, True)
    assert view.get("HK-99", True) is True
