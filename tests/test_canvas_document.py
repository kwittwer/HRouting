"""Tests für Phase A: Document als einzige Datenquelle des Canvas."""

from __future__ import annotations

import json
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
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas_widget import CanvasWidget  # noqa: E402
from storage.hrp_io import load_raw  # noqa: E402
from model.document import Document  # noqa: E402
from model.elements import TextAnnotation  # noqa: E402

EXAMPLE = ROOT / "examples" / "minimal.hrp"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def bound(app):
    document = Document.from_dict(load_raw(EXAMPLE))
    canvas = CanvasWidget()
    canvas.set_document(document)
    yield canvas, document
    canvas.deleteLater()


def test_canvas_reads_geometry_from_document(bound):
    canvas, _document = bound
    polygon = canvas._polygons["HK-1"]
    assert [(p.x(), p.y()) for p in polygon] == [
        (100.0, 100.0),
        (500.0, 100.0),
        (500.0, 400.0),
        (100.0, 400.0),
    ]
    assert isinstance(canvas._elec_points["AP-1"], QPointF)


def test_canvas_write_lands_in_document(bound):
    canvas, document = bound
    canvas._elec_points["AP-1"] = QPointF(11.0, 22.0)
    canvas._polygons["HK-1"] = [QPointF(0, 0), QPointF(5, 0), QPointF(5, 5)]

    saved = document.to_dict()["canvas"]
    assert saved["elec_points"]["AP-1"] == [11.0, 22.0]
    assert saved["polygons"]["HK-1"] == [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0]]


def test_nested_text_fields_are_independent(bound):
    canvas, document = bound
    text_ids = document.elements.get("text_annotations", {})
    text_id = next(iter(text_ids.keys()), "")
    if not text_id:
        floor_plan_id = next(iter(document.floorplans.keys()), "")
        text_id = document.new_id(TextAnnotation)
        text = TextAnnotation.create(
            text_id,
            floor_plan_id=floor_plan_id,
            name="Test Text",
            visible=True,
        )
        text.geom["text_annotations"] = {"pos": [300, 50], "content": "", "font_size": 14.0}
        document.add(text)

    canvas._text_contents[text_id] = "Neuer Text"
    entry = document.to_dict()["canvas"]["text_annotations"][text_id]
    assert entry["content"] == "Neuer Text"
    assert entry["pos"] == [300, 50]  # Position unverändert


def test_visibility_maps_write_to_params(bound):
    canvas, document = bound
    canvas._circuit_visible["HK-1"] = False
    assert document.to_dict()["params"]["circuits"]["HK-1"]["visible"] is False


def test_inplace_point_assignment_lands_in_document(bound):
    """In-Place-Mutation einer Punktliste muss ins Document zurückschreiben.

    Regression: `DocumentMapView.__getitem__` lieferte eine frisch erzeugte
    Wegwerf-Liste; `canvas._manual_routes[cid][idx] = pt` (so arbeitet der
    Drag-Code in mouseMoveEvent) verpuffte damit wirkungslos.
    """
    canvas, document = bound
    canvas._manual_routes["HK-1"] = [QPointF(0, 0), QPointF(10, 10), QPointF(20, 20)]

    canvas._manual_routes["HK-1"][1] = QPointF(50, 60)

    saved = document.to_dict()["canvas"]["manual_routes"]["HK-1"]
    assert saved == [[0.0, 0.0], [50.0, 60.0], [20.0, 20.0]]


def test_inplace_mutation_via_local_variable(bound):
    """Auch über eine lokale Variable gehaltene Liste muss zurückschreiben."""
    canvas, document = bound
    canvas._polygons["HK-1"] = [QPointF(0, 0), QPointF(5, 0), QPointF(5, 5)]

    pts = canvas._polygons["HK-1"]
    pts[0] = QPointF(1, 2)
    pts.insert(1, QPointF(3, 4))
    pts.append(QPointF(9, 9))
    del pts[-2]

    saved = document.to_dict()["canvas"]["polygons"]["HK-1"]
    assert saved == [[1.0, 2.0], [3.0, 4.0], [5.0, 0.0], [9.0, 9.0]]


def test_inplace_mutation_fires_change_signal(bound):
    canvas, _document = bound
    canvas._manual_routes["HK-1"] = [QPointF(0, 0), QPointF(1, 1)]
    seen: list[str] = []
    canvas.document_data_changed.connect(seen.append)

    canvas._manual_routes["HK-1"][0] = QPointF(7, 7)

    assert seen == ["HK-1"]


def test_inplace_mutation_works_for_all_point_maps(bound):
    """Alle sechs POINT_LIST-gebundenen Maps müssen In-Place schreiben."""
    canvas, document = bound
    cases = [
        (canvas._manual_routes, "HK-1", "manual_routes"),
        (canvas._polygons, "HK-1", "polygons"),
        (canvas._supply_lines, "HK-1", "supply_lines"),
        (canvas._elec_cables, "EK-1", "elec_cables"),
        (canvas._elec_room_polygons, "ER-1", "elec_rooms"),
    ]
    for view, key, _canvas_key in cases:
        view[key] = [QPointF(0, 0), QPointF(10, 10)]

    for view, key, _canvas_key in cases:
        view[key][1] = QPointF(99, 88)

    saved = document.to_dict()["canvas"]
    for _view, key, canvas_key in cases:
        assert saved[canvas_key][key][1] == [99.0, 88.0], canvas_key


def _mouse(canvas, kind, canvas_pt, ctrl=False):
    """Sendet ein echtes QMouseEvent an den Canvas (Canvas- statt Screen-Koordinaten)."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    btn = Qt.MouseButton.LeftButton
    screen = QPointF(
        canvas_pt.x() * canvas._scale + canvas._offset.x(),
        canvas_pt.y() * canvas._scale + canvas._offset.y(),
    )
    types = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
    }
    mods = (
        Qt.KeyboardModifier.ControlModifier if ctrl
        else Qt.KeyboardModifier.NoModifier
    )
    buttons = btn if kind == "move" else Qt.MouseButton.NoButton
    event = QMouseEvent(types[kind], screen, screen, btn, buttons, mods)
    getattr(canvas, {"press": "mousePressEvent",
                     "move": "mouseMoveEvent",
                     "release": "mouseReleaseEvent"}[kind])(event)


def test_dragging_route_point_persists_to_document(bound):
    """Laufzeitnachweis Bug B: ein echter Drag muss im Document ankommen.

    Ctrl wird gedrückt gehalten, damit Grid-Snapping und die
    Wandabstands-Constraints den Zielpunkt nicht verschieben.
    """
    canvas, document = bound
    canvas._manual_routes["HK-1"] = [
        QPointF(100, 100), QPointF(200, 100), QPointF(200, 200)
    ]

    _mouse(canvas, "press", QPointF(200, 100))
    assert canvas._dragging_route_point == ("HK-1", 1), "Drag wurde nicht gestartet"

    _mouse(canvas, "move", QPointF(260, 140), ctrl=True)
    _mouse(canvas, "release", QPointF(260, 140), ctrl=True)

    saved = document.to_dict()["canvas"]["manual_routes"]["HK-1"]
    assert saved[1] == [260.0, 140.0], f"Punkt nicht verschoben: {saved}"


def test_dragging_circuit_polygon_vertex_snaps_without_ctrl(bound):
    canvas, document = bound
    canvas.start_edit_polygon("HK-1")

    _mouse(canvas, "press", QPointF(100, 100))
    assert canvas._dragging_route_point == ("HK-1", 0)

    _mouse(canvas, "move", QPointF(112, 137))
    _mouse(canvas, "release", QPointF(112, 137))

    saved = document.to_dict()["canvas"]["polygons"]["HK-1"]
    assert saved[0] == [112.0, 137.0]


def test_dragging_circuit_polygon_vertex_uses_free_drag_with_ctrl(bound):
    canvas, document = bound
    canvas.start_edit_polygon("HK-1")

    _mouse(canvas, "press", QPointF(100, 100))
    _mouse(canvas, "move", QPointF(113, 137), ctrl=True)
    _mouse(canvas, "release", QPointF(113, 137), ctrl=True)

    saved = document.to_dict()["canvas"]["polygons"]["HK-1"]
    assert saved[0] == [113.0, 137.0]


def test_dragging_elec_room_polygon_vertex_persists(bound):
    canvas, document = bound
    canvas.start_edit_elec_room_polygon("ER-1")

    _mouse(canvas, "press", QPointF(50, 50))
    assert canvas._dragging_route_point == ("ER-1", 0)

    _mouse(canvas, "move", QPointF(75, 75), ctrl=True)
    _mouse(canvas, "release", QPointF(75, 75), ctrl=True)

    saved = document.to_dict()["canvas"]["elec_rooms"]["ER-1"]
    assert saved[0] == [75.0, 75.0]


def test_dragging_floorplan_polygon_vertex_persists(bound):
    canvas, document = bound
    floor = document.floorplans["grundriss-1"]
    floor.layer["polygon"] = [[0.0, 0.0], [40.0, 0.0], [40.0, 30.0]]
    canvas.set_document(document)
    canvas.start_edit_floor_plan_polygon("grundriss-1")

    _mouse(canvas, "press", QPointF(0, 0))
    assert canvas._dragging_route_point == ("grundriss-1", 0)

    _mouse(canvas, "move", QPointF(13, 37), ctrl=True)
    _mouse(canvas, "release", QPointF(13, 37), ctrl=True)

    saved_layers = document.to_dict()["canvas"]["floor_plans"]
    saved = next(entry for entry in saved_layers if entry["fp_id"] == "grundriss-1")
    assert saved["polygon"][0] == [13.0, 37.0]


def test_dragging_furniture_polygon_vertex_persists(bound):
    canvas, document = bound
    from model.elements import Furniture

    furniture = document.add(
        Furniture(
            "einrichtung-1",
            data={"name": "Sofa", "visible": True},
            geom={},
            layer={"fp_id": "einrichtung-1", "visible": True},
        )
    )
    furniture.layer.update({
        "polygon": [[0.0, 0.0], [40.0, 0.0], [40.0, 30.0]],
        "offset_x": 100.0,
        "offset_y": 200.0,
        "rotation": 0.0,
        "visible": True,
    })
    canvas.set_document(document)
    canvas.start_edit_floor_plan_polygon("einrichtung-1")

    _mouse(canvas, "press", QPointF(100, 200))
    assert canvas._dragging_route_point == ("einrichtung-1", 0)

    _mouse(canvas, "move", QPointF(126, 236), ctrl=True)
    _mouse(canvas, "release", QPointF(126, 236), ctrl=True)

    saved_layers = document.to_dict()["canvas"]["floor_plans"]
    saved = next(entry for entry in saved_layers if entry["fp_id"] == "einrichtung-1")
    assert saved["polygon"][0] == [26.0, 36.0]


# ---------------------------------------------------------------------------
# Bug A: Workspace-Filter muss auch für den Direkt-Drag gelten
# ---------------------------------------------------------------------------


def test_elec_point_not_draggable_in_heating_workspace(bound):
    """Ein AP darf im Heizung-Workspace nicht per Direktklick gezogen werden."""
    from model.layers import LayerId
    from gui.canvas_widget import ToolMode

    canvas, _document = bound
    canvas.set_selectable_layers({LayerId.HEATING})

    _mouse(canvas, "press", QPointF(200, 250))  # Position von AP-1

    assert canvas._dragging_elec_point is None
    assert canvas._mode is not ToolMode.MOVE_ELEC_POINT


def test_elec_point_draggable_in_electrical_workspace(bound):
    from model.layers import LayerId

    canvas, _document = bound
    canvas.set_selectable_layers({LayerId.ELECTRICAL})

    _mouse(canvas, "press", QPointF(200, 250))

    assert canvas._dragging_elec_point == "AP-1"


def test_route_point_not_draggable_in_electrical_workspace(bound):
    from model.layers import LayerId

    canvas, _document = bound
    canvas._manual_routes["HK-1"] = [QPointF(100, 100), QPointF(200, 100)]
    canvas.set_selectable_layers({LayerId.ELECTRICAL})

    _mouse(canvas, "press", QPointF(200, 100))

    assert canvas._dragging_route_point is None


def test_route_point_draggable_in_heating_workspace(bound):
    from model.layers import LayerId

    canvas, _document = bound
    canvas._manual_routes["HK-1"] = [QPointF(100, 100), QPointF(200, 100)]
    canvas.set_selectable_layers({LayerId.HEATING})

    _mouse(canvas, "press", QPointF(200, 100))

    assert canvas._dragging_route_point == ("HK-1", 1)


def test_start_point_not_draggable_in_electrical_workspace(bound):
    from model.layers import LayerId

    canvas, _document = bound
    canvas._start_points["HK-1"] = QPointF(300, 300)
    canvas.set_selectable_layers({LayerId.ELECTRICAL})

    _mouse(canvas, "press", QPointF(300, 300))

    assert canvas._dragging_start is None


def test_locked_hit_falls_through_to_allowed_object(bound):
    """Ein gesperrter Treffer darf einen dahinterliegenden erlaubten nicht blockieren."""
    from model.layers import LayerId

    canvas, _document = bound
    # AP und Routenpunkt exakt übereinander legen.
    canvas._elec_points["AP-1"] = QPointF(400, 400)
    canvas._manual_routes["HK-1"] = [QPointF(100, 100), QPointF(400, 400)]
    canvas.set_selectable_layers({LayerId.HEATING})

    _mouse(canvas, "press", QPointF(400, 400))

    assert canvas._dragging_elec_point is None
    assert canvas._dragging_route_point == ("HK-1", 1)


def test_workspace_switch_cancels_locked_route_drag(bound):
    """Ein laufender Drag wird beim Workspace-Wechsel sauber abgebrochen."""
    from model.layers import LayerId
    from gui.canvas_widget import ToolMode

    canvas, _document = bound
    canvas._manual_routes["HK-1"] = [QPointF(100, 100), QPointF(200, 100)]
    canvas.set_selectable_layers({LayerId.HEATING})
    _mouse(canvas, "press", QPointF(200, 100))
    assert canvas._dragging_route_point == ("HK-1", 1)

    canvas.set_selectable_layers({LayerId.ELECTRICAL})

    assert canvas._dragging_route_point is None
    assert canvas._mode is ToolMode.NONE


def test_read_only_access_does_not_modify_document(app):
    document = Document.from_dict(load_raw(EXAMPLE))
    before = json.dumps(document.to_dict(), sort_keys=True)

    canvas = CanvasWidget()
    canvas.set_document(document)
    try:
        for cid in list(canvas._polygons.keys()):
            _ = canvas._polygons[cid]
        for pid in list(canvas._elec_points.keys()):
            _ = canvas._elec_points[pid]
        for kid in list(canvas._elec_cables.keys()):
            _ = canvas._elec_cables[kid]
        _ = dict(canvas._circuit_visible)
        _ = dict(canvas._cable_start_ap)
    finally:
        canvas.deleteLater()

    assert json.dumps(document.to_dict(), sort_keys=True) == before


def test_document_data_changed_signal_fires(bound):
    canvas, _document = bound
    seen: list[str] = []
    canvas.document_data_changed.connect(seen.append)
    canvas._elec_points["AP-1"] = QPointF(1.0, 1.0)
    assert seen == ["AP-1"]


def test_app_window_persists_canvas_edits(app, tmp_path, monkeypatch):
    """Kernfall: Eine Canvas-Änderung muss Speichern und Laden überleben."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from storage.hrp_io import load_document  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)

        window.canvas._elec_points["AP-1"] = QPointF(777.0, 888.0)
        assert window._dirty is True

        target = tmp_path / "roundtrip.hrp"
        window._project_path = target
        assert window._save_project()

        reloaded = load_document(target)
        assert reloaded.to_dict()["canvas"]["elec_points"]["AP-1"] == [777.0, 888.0]
    finally:
        window.deleteLater()


def test_app_window_preserves_view_settings(app, tmp_path, monkeypatch):
    """Globale Ansichtsdaten (Raster, Zoom) dürfen nicht verloren gehen."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from storage.hrp_io import load_document  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window.canvas._grid_spacing_mm = 250.0

        target = tmp_path / "view.hrp"
        window._project_path = target
        assert window._save_project()

        reloaded = load_document(target)
        assert reloaded.to_dict()["canvas"]["grid_spacing_mm"] == 250.0
        # Grundrisse müssen erhalten bleiben
        assert reloaded.to_dict()["params"]["floorplans"]
    finally:
        window.deleteLater()


# ---------------------------------------------------------------------------
# A4.6 – Grundriss-Layer
# ---------------------------------------------------------------------------


def test_floor_plan_layer_is_document_backed(bound):
    canvas, document = bound
    fp_id = document.floorplan_order[0]
    layer = canvas._floor_plans[fp_id]

    assert type(layer).__name__ == "FloorPlanLayerView"
    assert layer.fp_id == fp_id
    saved = document.to_dict()
    floor_entry = next(e for e in saved["canvas"]["floor_plans"] if e["fp_id"] == fp_id)
    assert layer.mm_per_px == floor_entry["mm_per_px"]


def test_floor_plan_transform_writes_to_document(bound):
    canvas, document = bound
    fp_id = document.floorplan_order[0]
    layer = canvas._floor_plans[fp_id]

    layer.offset_x = 123.0
    layer.offset_y = -45.0
    layer.rotation = 30.0

    entry = next(
        e for e in document.to_dict()["canvas"]["floor_plans"] if e["fp_id"] == fp_id
    )
    assert entry["offset_x"] == 123.0
    assert entry["offset_y"] == -45.0
    assert entry["rotation"] == 30.0


def test_floor_plan_mirrors_fields_into_params(bound):
    """Redundante Felder müssen in canvas und params konsistent bleiben."""
    canvas, document = bound
    fp_id = document.floorplan_order[0]
    canvas._floor_plans[fp_id].rotation = 15.0

    saved = document.to_dict()
    entry = next(e for e in saved["canvas"]["floor_plans"] if e["fp_id"] == fp_id)
    assert entry["rotation"] == 15.0
    assert saved["params"]["floorplans"][fp_id]["rotation"] == 15.0


def test_floor_plan_ref_line_roundtrip(bound):
    canvas, document = bound
    fp_id = document.floorplan_order[0]
    layer = canvas._floor_plans[fp_id]

    layer.ref_p1 = QPointF(10.0, 20.0)
    layer.ref_p2 = QPointF(30.0, 40.0)

    entry = next(
        e for e in document.to_dict()["canvas"]["floor_plans"] if e["fp_id"] == fp_id
    )
    assert entry["ref_line"] == [[10.0, 20.0], [30.0, 40.0]]
    assert layer.ref_p1 == QPointF(10.0, 20.0)


def test_floor_plan_image_data_stays_local(bound):
    """Renderer und Pixmap gehören nicht ins Projektformat."""
    canvas, document = bound
    fp_id = document.floorplan_order[0]
    layer = canvas._floor_plans[fp_id]

    layer.size = (640.0, 480.0)
    assert layer.size == (640.0, 480.0)

    entry = next(
        e for e in document.to_dict()["canvas"]["floor_plans"] if e["fp_id"] == fp_id
    )
    assert "size" not in entry
    assert "renderer" not in entry


def test_floor_plan_can_load_embedded_svg_data_uri(bound):
    canvas, document = bound
    fp_id = document.floorplan_order[0]
    data_uri = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScxNicgaGVpZ2h0PSc4Jz48cmVjdCB3aWR0aD0nMTYnIGhlaWdodD0nOCcgZmlsbD0nI2NjYycvPjwvc3ZnPg=="

    canvas.load_floor_plan_image(fp_id, data_uri)

    layer = canvas._floor_plans[fp_id]
    assert layer.renderer is not None


def test_elec_point_icon_accepts_embedded_svg_data_uri(bound):
    canvas, _document = bound
    data_uri = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScxNicgaGVpZ2h0PScxNic+PHJlY3Qgd2lkdGg9JzE2JyBoZWlnaHQ9JzE2JyBmaWxsPSdibGFjaycvPjwvc3ZnPg=="

    canvas.set_elec_point_icon("AP-1", data_uri)

    assert canvas._elec_point_svgs.get("AP-1") is not None


# ---------------------------------------------------------------------------
# A4.7 – Hilfslinien
# ---------------------------------------------------------------------------


def _helper_document() -> Document:
    return Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "mm_per_px": 25.0}],
                "floor_helper_lines": {"grundriss-1": {"HL-1": [[0, 0], [100, 100]]}},
                "floor_helper_line_visible": {"grundriss-1": {"HL-1": True}},
            },
            "params": {"floorplans": {"grundriss-1": {"name": "EG"}}},
        }
    )


def test_helper_lines_read_as_points(app):
    canvas = CanvasWidget()
    try:
        canvas.set_document(_helper_document())
        points = canvas._floor_helper_lines["grundriss-1"]["HL-1"]
        assert [(p.x(), p.y()) for p in points] == [(0.0, 0.0), (100.0, 100.0)]
        assert canvas._floor_helper_line_visible["grundriss-1"]["HL-1"] is True
    finally:
        canvas.deleteLater()


def test_helper_lines_write_to_document(app):
    document = _helper_document()
    canvas = CanvasWidget()
    try:
        canvas.set_document(document)
        canvas._floor_helper_lines["grundriss-1"]["HL-1"] = [
            QPointF(7, 8),
            QPointF(9, 10),
        ]
        canvas._floor_helper_line_visible["grundriss-1"]["HL-1"] = False

        saved = document.to_dict()["canvas"]
        assert saved["floor_helper_lines"]["grundriss-1"]["HL-1"] == [
            [7.0, 8.0],
            [9.0, 10.0],
        ]
        assert saved["floor_helper_line_visible"]["grundriss-1"]["HL-1"] is False
    finally:
        canvas.deleteLater()


def test_helper_lines_setdefault_creates_entry(app):
    """Der Canvas nutzt setdefault beim Anlegen neuer Hilfslinien."""
    document = _helper_document()
    canvas = CanvasWidget()
    try:
        canvas.set_document(document)
        canvas._floor_helper_lines.setdefault("grundriss-1", {})["HL-2"] = [
            QPointF(1, 1),
            QPointF(2, 2),
        ]
        stored = document.to_dict()["canvas"]["floor_helper_lines"]["grundriss-1"]
        assert stored["HL-2"] == [[1.0, 1.0], [2.0, 2.0]]
        assert "HL-1" in stored  # bestehender Eintrag bleibt
    finally:
        canvas.deleteLater()


# ---------------------------------------------------------------------------
# A5 – Canvas-API statt direkter Dict-Zugriffe
# ---------------------------------------------------------------------------


def test_set_element_visible_covers_all_types(bound):
    canvas, document = bound

    canvas.set_element_visible("HK-1", False)
    canvas.set_element_visible("AP-1", False)

    saved = document.to_dict()["params"]
    assert saved["circuits"]["HK-1"]["visible"] is False
    assert saved["elec_points"]["AP-1"]["visible"] is False

    assert canvas.get_element_visible("HK-1") is False
    assert canvas.get_element_visible("AP-1") is False


def test_set_element_visible_handles_floor_plans(bound):
    canvas, document = bound
    fp_id = document.floorplan_order[0]

    canvas.set_element_visible(fp_id, False)

    assert canvas.get_element_visible(fp_id) is False
    entry = next(
        e for e in document.to_dict()["canvas"]["floor_plans"] if e["fp_id"] == fp_id
    )
    assert entry["visible"] is False


def test_get_element_visible_defaults_to_true(bound):
    canvas, _document = bound
    assert canvas.get_element_visible("gibt-es-nicht") is True
