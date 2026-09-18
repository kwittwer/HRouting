"""Isolated helper-intersection geometry/cache regressions (no paint loop)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import gui.canvas_widget as canvas_module  # noqa: E402
from gui.canvas_widget import CanvasWidget, FloorPlanLayer, ToolMode  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def canvas(app):
    widget = CanvasWidget()
    widget._floor_plans = {"floor-1": FloorPlanLayer("floor-1")}
    widget._floor_plan_order = ["floor-1"]
    widget._floor_helper_lines = {"floor-1": crossing_lines()}
    widget._scale = 1.0
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()


@pytest.fixture
def intersection_spy():
    with patch.object(
        canvas_module, "_line_line_intersection",
        wraps=canvas_module._line_line_intersection,
    ) as spy:
        yield spy


def segment(x1, y1, x2, y2):
    return [QPointF(x1, y1), QPointF(x2, y2)]


def crossing_lines(x=5.0):
    return {"HL-1": segment(0, 0, 10, 0), "HL-2": segment(x, -5, x, 5)}


def results(canvas):
    canvas._calculate_helper_line_intersections()
    return [
        (point.x(), point.y(), angle, hid1, hid2, fid)
        for point, angle, hid1, hid2, fid in canvas._helper_line_intersections
    ]


def test_stable_pairs_reused_on_pan_and_zoom(canvas, intersection_spy):
    canvas._floor_helper_lines["floor-1"]["HL-3"] = segment(0, -5, 10, 5)
    expected = results(canvas)
    assert len(expected) == 3
    assert [item[2] for item in expected] == pytest.approx([90, 45, 45])
    assert intersection_spy.call_count == 3
    raw = canvas._helper_intersection_raw
    for scale in (0.1, 0.5, 1.0, 2.0, 50.0):
        canvas._scale = scale
        canvas._offset = QPointF(100 * scale, -200 * scale)
        assert results(canvas) == expected
    assert canvas._helper_intersection_raw is raw
    assert intersection_spy.call_count == 3


@pytest.mark.parametrize("reverse_pair", [False, True])
def test_zoom_rechecks_endpoint_tolerance_including_boundary(canvas, intersection_spy, reverse_pair):
    # Infinite lines meet 0.25 beyond the horizontal segment. Starting at
    # high zoom must cache this rejected intersection for later zoom-out.
    lines = crossing_lines(10.25)
    if reverse_pair:
        lines = dict(reversed(list(lines.items())))
    canvas._floor_helper_lines["floor-1"] = lines
    for scale, accepted in ((4, False), (2, True), (1, True), (4, False), (0, True), (-1, True)):
        canvas._scale = scale
        actual = results(canvas)
        assert bool(actual) is accepted
        if accepted:
            assert actual[0][:3] == (10.25, 0, 90)
    assert intersection_spy.call_count == 1


def test_in_place_geometry_changes_use_exact_coordinates(canvas, intersection_spy):
    assert results(canvas)[0][0] == 5
    lines = canvas._floor_helper_lines["floor-1"]
    for point in lines["HL-2"]:
        point.setX(5 + 1e-8)
    assert results(canvas)[0][0] == pytest.approx(5 + 1e-8, abs=1e-12)
    assert intersection_spy.call_count == 2
    assert results(canvas)[0][0] == pytest.approx(5 + 1e-8, abs=1e-12)
    assert intersection_spy.call_count == 2


def test_add_remove_and_incomplete_helpers_invalidate(canvas, intersection_spy):
    assert len(results(canvas)) == 1
    lines = canvas._floor_helper_lines["floor-1"]
    lines["HL-3"] = [QPointF(0, -5)]
    assert len(results(canvas)) == 1
    assert intersection_spy.call_count == 2
    lines["HL-3"].append(QPointF(10, 5))
    assert len(results(canvas)) == 3
    assert intersection_spy.call_count == 5
    del lines["HL-2"]
    assert len(results(canvas)) == 1
    assert intersection_spy.call_count == 6
    lines.clear()
    assert results(canvas) == []
    assert canvas._helper_intersection_raw == {"floor-1": []}
    assert results(canvas) == []
    assert intersection_spy.call_count == 6


def test_restore_same_ids_and_only_last_signature_is_retained(canvas, intersection_spy):
    assert results(canvas)[0][0] == 5
    original_raw = canvas._helper_intersection_raw
    canvas._floor_helper_lines = {"floor-1": crossing_lines(8)}
    assert results(canvas)[0][0] == 8
    assert canvas._helper_intersection_raw is not original_raw
    assert len(canvas._helper_intersection_raw["floor-1"]) == 1
    canvas._floor_helper_lines = {"floor-1": crossing_lines()}
    assert results(canvas)[0][0] == 5
    assert intersection_spy.call_count == 3  # Returning to old geometry rebuilds.
    canvas._floor_helper_lines = {"floor-1": crossing_lines()}
    assert results(canvas)[0][0] == 5
    assert intersection_spy.call_count == 3  # Equal values, different objects.


def test_hidden_floor_and_visible_floor_order_invalidate(canvas, intersection_spy):
    canvas._floor_plans["floor-2"] = FloorPlanLayer("floor-2", visible=False)
    canvas._floor_plan_order.append("floor-2")
    canvas._floor_helper_lines["floor-2"] = crossing_lines(8)
    assert [item[-1] for item in results(canvas)] == ["floor-1"]
    assert intersection_spy.call_count == 1
    canvas._floor_plans["floor-2"].visible = True
    assert [item[-1] for item in results(canvas)] == ["floor-1", "floor-2"]
    assert intersection_spy.call_count == 3
    canvas._floor_plan_order.reverse()
    assert [item[-1] for item in results(canvas)] == ["floor-2", "floor-1"]
    assert intersection_spy.call_count == 5
    canvas._floor_plans["floor-1"].visible = False
    assert [item[-1] for item in results(canvas)] == ["floor-2"]
    assert intersection_spy.call_count == 6
    assert set(canvas._helper_intersection_raw) == {"floor-2"}
    del canvas._floor_plans["floor-2"]
    assert results(canvas) == []
    assert canvas._helper_intersection_raw == {}


def test_parallel_and_degenerate_pairs_are_not_recomputed(canvas, intersection_spy):
    canvas._floor_helper_lines["floor-1"] = {
        "HL-1": segment(0, 0, 10, 0),
        "HL-2": segment(0, 1, 10, 1),
        "HL-3": segment(5, 0, 5, 0),
    }
    assert results(canvas) == []
    assert intersection_spy.call_count == 3
    canvas._scale = 0.1
    assert results(canvas) == []
    assert intersection_spy.call_count == 3


def enable_preview(canvas):
    canvas._mode = ToolMode.DRAW_HELPER_LINE
    canvas._helper_active_floor_id = "floor-1"
    canvas._helper_draw_start = QPointF(0, -4)
    canvas._helper_draw_current = QPointF(10, -4)
    canvas._mm_per_px = 2
    canvas._floor_helper_settings["floor-1"] = {"target_length_mm": 20}


def test_live_preview_recomputed_without_rebuilding_static_pairs(canvas, intersection_spy):
    canvas._floor_plans["floor-2"] = FloorPlanLayer("floor-2")
    canvas._floor_plan_order.append("floor-2")
    canvas._floor_helper_lines["floor-2"] = crossing_lines(8)
    enable_preview(canvas)
    first = results(canvas)
    assert [(item[3], item[-1]) for item in first] == [
        ("HL-1", "floor-1"), ("__preview__", "floor-1"), ("HL-1", "floor-2"),
    ]
    assert first[1][:3] == (5, -4, 90)
    assert intersection_spy.call_count == 4  # Two static pairs, two preview checks.
    canvas._helper_draw_start.setY(-3)
    canvas._helper_draw_current.setY(-3)
    assert results(canvas)[1][:3] == (5, -3, 90)
    assert intersection_spy.call_count == 6
    # Target length and mm_per_px affect the preview, not the static cache.
    canvas._floor_helper_settings["floor-1"]["target_length_mm"] = 8
    assert len(results(canvas)) == 2
    assert intersection_spy.call_count == 8
    canvas._mm_per_px = 1
    assert results(canvas)[1][3] == "__preview__"
    assert intersection_spy.call_count == 10
    canvas._helper_draw_current = None
    assert len(results(canvas)) == 2
    assert intersection_spy.call_count == 10


def test_hidden_helper_semantics_static_included_preview_excluded(canvas, intersection_spy):
    enable_preview(canvas)
    canvas._floor_helper_line_visible["floor-1"] = {"HL-2": False}
    # The floor-wide helper setting also historically does not filter pairs.
    canvas._floor_helper_settings["floor-1"]["visible"] = False
    assert len(results(canvas)) == 1
    assert intersection_spy.call_count == 2  # Static pair + visible HL-1 preview.
    raw = canvas._helper_intersection_raw
    canvas._floor_helper_line_visible["floor-1"]["HL-2"] = True
    assert len(results(canvas)) == 2
    assert intersection_spy.call_count == 4
    assert canvas._helper_intersection_raw is raw
    canvas._floor_plans["floor-1"].visible = False
    assert results(canvas) == []
    assert intersection_spy.call_count == 4


def test_preview_endpoint_tolerance_tracks_zoom(canvas, intersection_spy):
    enable_preview(canvas)
    canvas._helper_draw_start = QPointF(0, -5.25)
    canvas._helper_draw_current = QPointF(10, -5.25)
    for scale, count in ((4, 1), (2, 2), (4, 1)):
        canvas._scale = scale
        assert len(results(canvas)) == count
    assert intersection_spy.call_count == 7  # Static once, two preview checks/call.


def test_result_point_mutation_does_not_corrupt_raw_cache(canvas, intersection_spy):
    expected = results(canvas)
    canvas._helper_line_intersections[0][0].setX(999)
    assert results(canvas) == expected
    assert intersection_spy.call_count == 1