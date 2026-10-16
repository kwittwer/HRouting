"""AP detector settings and floor-plan coverage rendering."""

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

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas_widget import CanvasWidget  # noqa: E402
from gui.sensor_coverage import visible_room_path  # noqa: E402
from model.document import Document  # noqa: E402
from model.field_access import get_field, set_field  # noqa: E402
from model.schema import ELEC_POINT_SCHEMA  # noqa: E402
from model.sensor_coverage import (  # noqa: E402
    ceiling_coverage_polygon_mm, coverage_radius_mm, sensor_data_for_point, sensor_type,
)
from validate_hrp import _load_schema, validate_schema, validate_semantic  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _document(*, position="Wand", symbol="Bewegungsmelder", height=2500,
              direction=90, angle=90, range_mm=2000, room=False):
    canvas = {
        "mm_per_px": 20.0,
        "floor_plans": [{"fp_id": "grundriss-1"}],
        "elec_points": {"AP-1": [160, 160]},
    }
    params = {
        "floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}},
        "elec_points": {
            "AP-1": {
                "point_id": "AP-1", "floor_plan_id": "grundriss-1",
                "builtin_symbol": symbol, "position": position,
                "height_from_floor": height, "sensor_direction_deg": direction,
                "sensor_angle_deg": angle, "sensor_range_mm": range_mm,
                "sensor_cone_angle_deg": 90,
            }
        },
    }
    if room:
        canvas["elec_rooms"] = {
            "ER-1": [[100, 100], [220, 100], [220, 220], [100, 220]],
        }
        params["elec_rooms"] = {
            "ER-1": {"room_id": "ER-1", "floor_plan_id": "grundriss-1"},
        }
    return Document.from_dict({"canvas": canvas, "params": params})


def _images(document, app, *, export=False):
    canvas = CanvasWidget()
    try:
        canvas.from_dict(document.to_dict()["canvas"])
        canvas.set_document(document)
        canvas.setMinimumSize(0, 0)
        canvas.resize(320, 320)
        def capture():
            return (canvas.render_for_export(QRectF(0, 0, 320, 320), 320, 320)
                    if export else canvas.grab().toImage())
        shown = capture()
        canvas.set_sensor_coverage_global_visible(False)
        hidden = capture()
        return shown, hidden
    finally:
        canvas.deleteLater()


def _different(shown, hidden, x, y):
    return shown.pixelColor(x, y) != hidden.pixelColor(x, y)


def test_ceiling_radius_depends_on_angle_height_and_slant_range():
    point = {"builtin_symbol": "Praesenzmelder", "position": "Decke",
             "height_from_floor": 2500, "sensor_cone_angle_deg": 90}
    assert coverage_radius_mm(point) == pytest.approx(2500)
    assert coverage_radius_mm({**point, "sensor_range_mm": 3000}) == pytest.approx(
        (3000**2 - 2500**2)**0.5
    )
    assert coverage_radius_mm({**point, "height_from_floor": 300}) is None
    assert coverage_radius_mm({**point, "sensor_range_mm": 2500}) is None
    assert coverage_radius_mm({**point, "position": "Boden"}) is None


def test_tilted_ceiling_projection_matches_cone_intercepts():
    from math import radians, tan

    data = {"sensor_type": "pir", "position": "Decke", "height_from_floor": 2500,
            "sensor_cone_angle_deg": 90, "sensor_tilt_deg": 25,
            "sensor_range_mm": 20000, "sensor_direction_deg": 90}
    polygon = ceiling_coverage_polygon_mm(data)
    assert min(point[0] for point in polygon) == pytest.approx(2500 * tan(radians(-20)), abs=3)
    assert max(point[0] for point in polygon) == pytest.approx(2500 * tan(radians(70)), abs=3)
    width = max(point[0] for point in polygon) - min(point[0] for point in polygon)
    depth = max(point[1] for point in polygon) - min(point[1] for point in polygon)
    assert width > depth
    assert min(point[0] for point in polygon) + max(point[0] for point in polygon) > 0


@pytest.mark.parametrize("tilt", [0, 25, 70, 90])
def test_ceiling_projection_stays_inside_slant_range(tilt):
    data = {"sensor_type": "radar", "position": "Decke", "height_from_floor": 2500,
            "sensor_cone_angle_deg": 90, "sensor_tilt_deg": tilt, "sensor_range_mm": 6000}
    polygon = ceiling_coverage_polygon_mm(data)
    assert len(polygon) >= 3
    assert all(point[0]**2 + point[1]**2 + 2500**2 <= 6000**2 + 1e-6 for point in polygon)


def test_tilted_ceiling_pixels_follow_direction_and_invalidate_cache(app):
    document = _document(position="Decke", range_mm=12000)
    point = document.elements["elec_points"]["AP-1"]
    canvas = CanvasWidget()
    try:
        canvas.from_dict(document.to_dict()["canvas"])
        canvas.set_document(document)
        canvas.setMinimumSize(0, 0)
        canvas.resize(600, 600)
        canvas._offset = QPointF(140, 140)
        upright = canvas.grab().toImage()
        upright_path = canvas._sensor_coverage_path_cache["AP-1"][1]
        assert upright_path.contains(QPointF(60, 160))
        point.sensor_tilt_deg = 25
        tilted = canvas.grab().toImage()
        tilted_path = canvas._sensor_coverage_path_cache["AP-1"][1]
        assert tilted_path.boundingRect().width() > tilted_path.boundingRect().height()
        assert not tilted_path.contains(QPointF(60, 160))
        assert tilted_path.contains(QPointF(360, 160))
        assert _different(upright, tilted, 200, 300)
        assert _different(upright, tilted, 500, 300)
        canvas.grab()
        assert canvas._sensor_coverage_path_cache["AP-1"][1] is tilted_path
        point.sensor_direction_deg = 0
        canvas.grab()
        rotated_path = canvas._sensor_coverage_path_cache["AP-1"][1]
        assert not rotated_path.contains(QPointF(360, 160))
        assert rotated_path.contains(QPointF(160, -40))
        point.sensor_cone_angle_deg = 60
        canvas.grab()
        assert canvas._sensor_coverage_path_cache["AP-1"][1] is not rotated_path
    finally:
        canvas.deleteLater()


def test_tilt_editor_and_roundtrip(app):
    from gui.properties.generic_editor import GenericElementEditor
    from model.field_access import display_value

    document = _document(position="Decke", range_mm=12000)
    point = document.elements["elec_points"]["AP-1"]
    editor = GenericElementEditor(document, point, ELEC_POINT_SCHEMA)
    try:
        assert editor._widgets["sensor_tilt_deg"]._spin.value() == 0
        assert "sensor_tilt_deg" not in point.data
        editor._on_field_changed("sensor_tilt_deg", 25.0)
        saved = document.to_dict()
        assert not validate_schema(saved, _load_schema(ROOT / "hrp_schema.json"))
        reloaded = Document.from_dict(saved).elements["elec_points"]["AP-1"]
        assert reloaded.sensor_tilt_deg == 25.0
        spec = next(field for field in ELEC_POINT_SCHEMA.fields if field.key == "sensor_tilt_deg")
        assert display_value(reloaded, spec) == 25.0
        saved["params"]["elec_points"]["AP-1"]["sensor_tilt_deg"] = 91
        assert validate_schema(saved, _load_schema(ROOT / "hrp_schema.json"))
    finally:
        editor.deleteLater()


def test_tilt_editor_changes_support_undo_redo(app):
    from gui.app_window import AppWindow

    window = AppWindow()
    try:
        window._set_document(_document(position="Decke", range_mm=12000))
        window.properties.show_element("AP-1")
        editor = window.properties._editors["AP-1"]
        editor._on_field_changed("sensor_tilt_deg", 25.0)
        assert window._document.elements["elec_points"]["AP-1"].sensor_tilt_deg == 25
        window._undo()
        assert window._document.elements["elec_points"]["AP-1"].sensor_tilt_deg == 0
        window._redo()
        assert window._document.elements["elec_points"]["AP-1"].sensor_tilt_deg == 25
    finally:
        window.deleteLater()


def test_wall_height_does_not_change_radius_and_symbol_is_overridable():
    point = {"builtin_symbol": "Bewegungsmelder", "position": "Wand"}
    assert sensor_type(point) == "pir"
    assert sensor_type({"icon_path": "icons/Praesenzmelder.png"}) == "radar"
    assert sensor_type({**point, "sensor_type": "radar"}) == "radar"
    assert sensor_type({**point, "sensor_type": "none"}) == "none"
    assert coverage_radius_mm(point) == 6000
    assert coverage_radius_mm({**point, "height_from_floor": 3000}) == 6000


def test_legacy_sensor_defaults_are_virtual_and_new_fields_validate():
    document = _document(symbol="Praesenzmelder")
    point = document.elements["elec_points"]["AP-1"]
    point.data.pop("sensor_angle_deg")
    point.data.pop("sensor_range_mm")
    specs = {spec.key: spec for spec in ELEC_POINT_SCHEMA.fields}
    assert get_field(point, specs["sensor_angle_deg"]) == 360
    assert point.sensor_angle_deg is None
    before = document.to_dict()
    assert "sensor_angle_deg" not in before["params"]["elec_points"]["AP-1"]
    set_field(point, specs["sensor_range_mm"], 3000)
    saved = document.to_dict()
    assert saved["params"]["elec_points"]["AP-1"]["sensor_range_mm"] == 3000
    assert not validate_schema(saved, _load_schema(ROOT / "hrp_schema.json"))
    errors, _warnings = validate_semantic(saved)
    assert not errors
    assert Document.from_dict(saved).elements["elec_points"]["AP-1"].sensor_range_mm == 3000


def test_legacy_canvas_mounting_metadata_is_used_without_rewriting():
    raw = {
        "canvas": {
            "elec_points": {"AP-1": [150, 150]},
            "elec_point_position": {"AP-1": "Decke"},
            "elec_point_height": {"AP-1": 2500},
        },
        "params": {"elec_points": {
            "AP-1": {"point_id": "AP-1", "builtin_symbol": "Bewegungsmelder",
                     "sensor_cone_angle_deg": 90},
        }},
    }
    document = Document.from_dict(raw)
    point = document.elements["elec_points"]["AP-1"]
    assert coverage_radius_mm(sensor_data_for_point(point)) == pytest.approx(2500)
    saved = document.to_dict()
    assert saved["canvas"]["elec_point_height"] == raw["canvas"]["elec_point_height"]
    assert saved["params"]["elec_points"]["AP-1"] == raw["params"]["elec_points"]["AP-1"]


def test_sensor_editor_range_is_displayed_in_meters(app):
    from gui.properties.generic_editor import GenericElementEditor  # noqa: PLC0415

    document = _document()
    point = document.elements["elec_points"]["AP-1"]
    editor = GenericElementEditor(document, point, ELEC_POINT_SCHEMA)
    try:
        spinner = editor._widgets["sensor_range_mm"]._spin
        assert spinner.minimum() == pytest.approx(0.1)
        assert spinner.maximum() == pytest.approx(100.0)
        assert spinner.value() == pytest.approx(2.0)
        height_spinner = editor._widgets["height_from_floor"]._spin
        assert height_spinner.suffix() == " m"
        assert height_spinner.value() == pytest.approx(2.5)
        editor._on_field_changed("height_from_floor", 2.7)
        assert point.height_from_floor == pytest.approx(2700)
        editor._on_field_changed("sensor_range_mm", 3.0)
        assert point.sensor_range_mm == pytest.approx(3000)
    finally:
        editor.deleteLater()


def test_wall_sector_points_right_and_not_left(app):
    shown, hidden = _images(_document(), app)
    assert _different(shown, hidden, 230, 160)
    assert not _different(shown, hidden, 90, 160)
    assert not _different(shown, hidden, 200, 235)


def test_ceiling_circle_is_clipped_to_its_room_and_exports(app):
    document = _document(position="Decke", symbol="Praesenzmelder", room=True,
                         range_mm=6000)
    shown, hidden = _images(document, app, export=True)
    assert _different(shown, hidden, 190, 160)
    assert not _different(shown, hidden, 245, 160)


def test_ceiling_without_room_shows_full_circle(app):
    shown, hidden = _images(_document(position="Decke", symbol="Praesenzmelder",
                                      range_mm=6000), app)
    assert _different(shown, hidden, 245, 160)


def test_room_from_another_floor_does_not_clip_sensor(app):
    document = _document(position="Decke", symbol="Praesenzmelder",
                         range_mm=6000, room=True)
    document.elements["elec_rooms"]["ER-1"].floor_plan_id = "grundriss-2"
    shown, hidden = _images(document, app)
    assert _different(shown, hidden, 245, 160)


def test_hidden_ap_and_per_sensor_setting_suppress_coverage(app):
    document = _document()
    point = document.elements["elec_points"]["AP-1"]
    point.sensor_coverage_visible = False
    shown, hidden = _images(document, app)
    assert not _different(shown, hidden, 230, 160)
    point.sensor_coverage_visible = True
    point.visible = False
    shown, hidden = _images(document, app)
    assert not _different(shown, hidden, 230, 160)


def test_canvas_display_setting_survives_reload(app):
    document = _document()
    canvas = CanvasWidget()
    try:
        canvas.set_document(document)
        canvas.set_sensor_coverage_global_visible(False)
        saved = canvas.to_dict()
        canvas.from_dict(saved)
        assert not canvas._sensor_coverage_global_visible
        canvas.from_dict({})
        assert canvas._sensor_coverage_global_visible
    finally:
        canvas.deleteLater()


def test_view_action_tracks_project_and_undo(app):
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        window._set_document(_document())
        window._sensor_coverage_action.trigger()
        assert not window._sensor_coverage_action.isChecked()
        assert window._document.view["sensor_coverage_global_visible"] is False
        window._undo()
        assert window._sensor_coverage_action.isChecked()
        window._redo()
        assert not window._sensor_coverage_action.isChecked()
        window._sync_canvas_to_document()
        saved = window._document.to_dict()
        assert saved["canvas"]["sensor_coverage_global_visible"] is False
        window._set_document(Document.from_dict(saved))
        assert not window._sensor_coverage_action.isChecked()
        assert not window.canvas._sensor_coverage_global_visible
    finally:
        window.deleteLater()


def test_pdf_page_ap_visibility_suppresses_sensor(app):
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        window._set_document(_document())
        assert window.canvas._elec_visible.get("AP-1", True)
        window._apply_page_visibility({"element_visibility": {"ap": False}})
        assert window.canvas._elec_visible["AP-1"] is False
        assert window._document.elements["elec_points"]["AP-1"].visible is False
    finally:
        window.deleteLater()


def test_sensor_edit_does_not_rebuild_electrical_views(app, monkeypatch):
    from gui.app_window import AppWindow

    window = AppWindow()
    try:
        window._set_document(_document())
        changes = []
        window._document.element_changed.connect(changes.append)
        monkeypatch.setattr(window, "_refresh_schema_windows",
                            lambda: pytest.fail("Sensor-only edit rebuilt topology"))
        window._document.elements["elec_points"]["AP-1"].sensor_angle_deg = 60
        window._on_property_changed("AP-1", "sensor_angle_deg", 60)
        assert not changes
        assert window._dirty
    finally:
        window.deleteLater()


def test_room_visibility_depends_on_sensor_position():
    polygon = [QPointF(0, 0), QPointF(300, 0), QPointF(300, 100),
               QPointF(100, 100), QPointF(100, 300), QPointF(0, 300)]
    hidden = visible_room_path(QPointF(50, 250), polygon)
    visible = visible_room_path(QPointF(50, 50), polygon)
    target = QPointF(250, 50)
    assert not hidden.contains(target)
    assert visible.contains(target)


def test_sensor_path_follows_ap_and_reuses_geometry_when_unchanged(app, monkeypatch):
    import gui.canvas_widget as canvas_module

    document = _document(room=True, range_mm=800)
    canvas = CanvasWidget()
    calls = []
    original = canvas_module.visible_room_path

    def tracked(center, polygon):
        calls.append((center.x(), center.y()))
        return original(center, polygon)

    monkeypatch.setattr(canvas_module, "visible_room_path", tracked)
    try:
        canvas.from_dict(document.to_dict()["canvas"])
        canvas.set_document(document)
        canvas.setMinimumSize(0, 0)
        canvas.resize(320, 320)
        before = canvas.grab().toImage()
        assert calls == [(160, 160)]
        assert not canvas._sensor_coverage_path_cache["AP-1"][1].contains(QPointF(210, 160))
        canvas.grab()
        assert calls == [(160, 160)]
        canvas._scale = 1.1
        canvas._offset = QPointF(5, 5)
        canvas.grab()
        assert calls == [(160, 160)]
        canvas._scale = 1.0
        canvas._offset = QPointF(0, 0)
        canvas._elec_points["AP-1"] = QPointF(120, 160)
        after = canvas.grab().toImage()
        assert calls == [(160, 160), (120, 160)]
        assert _different(before, after, 190, 160)
        assert _different(before, after, 145, 160)
        polygon = canvas._elec_room_polygons["ER-1"]
        polygon[1] = QPointF(210, 100)
        canvas._elec_room_polygons["ER-1"] = polygon
        canvas.grab()
        assert len(calls) == 3
    finally:
        canvas.deleteLater()


def test_sensor_on_room_wall_is_clipped(app):
    document = _document(room=True, angle=360, range_mm=2000)
    document.elements["elec_points"]["AP-1"].pos = [100, 160]
    shown, hidden = _images(document, app)
    assert _different(shown, hidden, 150, 160)
    assert not _different(shown, hidden, 60, 160)


def test_pdf_height_uses_meters(app):
    from gui.app_window import AppWindow

    window = AppWindow()
    try:
        window._set_document(_document(height=2500))
        rows, _cables = window._collect_pdf_electro_rows()
        assert rows[0][3] == "2.50 m"
    finally:
        window.deleteLater()


def test_legacy_height_control_displays_meters_and_preserves_api(app):
    from gui.parameter_panel import ElektroPointPanel

    panel = ElektroPointPanel("AP-1")
    try:
        panel.from_dict({"height_from_floor": 250.0})
        assert panel.sb_height_from_floor.suffix() == " m"
        assert panel.sb_height_from_floor.value() == pytest.approx(2.5)
        values = []
        panel.height_changed.connect(lambda _point_id, value: values.append(value))
        panel.sb_height_from_floor.setValue(2.7)
        assert values[-1] == pytest.approx(270.0)
        assert panel.get_parameters()["height_from_floor"] == pytest.approx(270.0)
    finally:
        panel.deleteLater()