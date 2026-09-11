"""Regression tests for E-6: Cable drawing workflow."""

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
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_e6_add_elec_cable_workflow(app):
    """Test that adding an electrical cable works and sets correct mode."""
    from gui.canvas_widget import ToolMode  # noqa: PLC0415
    from model.elements import ElecCable  # noqa: PLC0415
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        ok = window.open_project_file(Path("examples/Planung_Linda.hrp"))
        assert ok, "Failed to open Planung_Linda.hrp"

        doc_before_cables = len(window._document.elements["elec_cables"])
        window._add_elec_cable()
        doc_after_cables = len(window._document.elements["elec_cables"])
        assert doc_after_cables == doc_before_cables + 1, "Cable not added to document"

        assert window.canvas._mode == ToolMode.DRAW_ELEC_CABLE, "Mode not switched to DRAW_ELEC_CABLE"
        assert window.canvas._current_elec_cable_id, "Cable ID not set"

        cable_id = window.canvas._current_elec_cable_id
        cable = window._document.get(cable_id)
        assert isinstance(cable, ElecCable), "Cable not found in document"
    finally:
        window.deleteLater()


def test_e6_canvas_menu_action_exists(app):
    """Test that the 'Elektro-Kabel hinzufügen' menu action exists."""
    from gui.app_window import AppWindow  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    window = AppWindow()
    try:
        ok = window.open_project_file(Path("examples/Planung_Linda.hrp"))
        assert ok

        # Check that the action exists in the Einfügen menu
        found = False
        for action in window.menuBar().actions():
            if "einfügen" in action.text().lower():
                for sub_action in action.menu().actions():
                    if "kabel" in sub_action.text().lower():
                        found = True
                        break
        assert found, "Cable action not found in menu"
    finally:
        window.deleteLater()


def test_e6_identical_cables_get_opposite_offsets(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        p0 = QPointF(10.0, 10.0)
        p1 = QPointF(210.0, 10.0)
        canvas._elec_cables = {
            "EK-1": [QPointF(p0), QPointF(p1)],
            "EK-2": [QPointF(p0), QPointF(p1)],
        }
        canvas._elec_visible = {"EK-1": True, "EK-2": True}
        canvas._elec_cable_stroke_width = {"EK-1": 2.0, "EK-2": 2.0}

        off1 = canvas._get_elec_cable_segment_offsets("EK-1", canvas._elec_cables["EK-1"])
        off2 = canvas._get_elec_cable_segment_offsets("EK-2", canvas._elec_cables["EK-2"])

        assert len(off1) == 1
        assert len(off2) == 1
        assert abs(off1[0]) > 0.0
        assert abs(off2[0]) > 0.0
        assert off1[0] == pytest.approx(-off2[0], rel=1e-6, abs=1e-6)
    finally:
        canvas.deleteLater()


def test_e6_partial_overlap_offsets_only_overlapped_segment(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas._elec_cables = {
            "EK-1": [QPointF(0.0, 0.0), QPointF(100.0, 0.0), QPointF(200.0, 0.0)],
            "EK-2": [QPointF(0.0, 0.0), QPointF(100.0, 0.0)],
        }
        canvas._elec_visible = {"EK-1": True, "EK-2": True}
        canvas._elec_cable_stroke_width = {"EK-1": 2.0, "EK-2": 2.0}

        off1 = canvas._get_elec_cable_segment_offsets("EK-1", canvas._elec_cables["EK-1"])
        off2 = canvas._get_elec_cable_segment_offsets("EK-2", canvas._elec_cables["EK-2"])

        assert len(off1) == 2
        assert len(off2) == 1
        assert abs(off1[0]) > 0.0
        assert off1[1] == pytest.approx(0.0, abs=1e-6)
        assert abs(off2[0]) > 0.0
    finally:
        canvas.deleteLater()


def test_e6_hit_testing_uses_shifted_render_path(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas._elec_cables = {
            "EK-1": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
            "EK-2": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
        }
        canvas._elec_visible = {"EK-1": True, "EK-2": True}
        canvas._elec_cable_stroke_width = {"EK-1": 2.0, "EK-2": 2.0}

        pts = canvas._elec_cables["EK-1"]
        seg_offsets = canvas._get_elec_cable_segment_offsets("EK-1", pts)
        shifted = canvas._build_elec_cable_offset_polyline(pts, seg_offsets)
        mid = QPointF(
            (shifted[0].x() + shifted[1].x()) * 0.5,
            (shifted[0].y() + shifted[1].y()) * 0.5,
        )

        hit = canvas._hit_elec_cable_edge(mid, "EK-1")
        assert hit == (0, 1)
    finally:
        canvas.deleteLater()


def test_e6_overlap_gap_zero_disables_offsetting(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas._elec_cables = {
            "EK-1": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
            "EK-2": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
        }
        canvas._elec_visible = {"EK-1": True, "EK-2": True}
        canvas._elec_cable_stroke_width = {"EK-1": 2.0, "EK-2": 2.0}
        canvas.set_elec_cable_overlap_gap_px(0.0)

        off1 = canvas._get_elec_cable_segment_offsets("EK-1", canvas._elec_cables["EK-1"])
        off2 = canvas._get_elec_cable_segment_offsets("EK-2", canvas._elec_cables["EK-2"])

        assert off1 == [0.0]
        assert off2 == [0.0]
    finally:
        canvas.deleteLater()


def test_e6_elec_point_fill_alpha_clamps(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.set_elec_point_fill_alpha(-20)
        assert canvas.elec_point_fill_alpha() == 0

        canvas.set_elec_point_fill_alpha(80)
        assert canvas.elec_point_fill_alpha() == 80

        canvas.set_elec_point_fill_alpha(999)
        assert canvas.elec_point_fill_alpha() == 255
    finally:
        canvas.deleteLater()


def test_e6_elec_cable_ap_approach_length_clamps(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.set_elec_cable_ap_approach_length_px(-5.0)
        assert canvas.elec_cable_ap_approach_length_px() == 0.0

        canvas.set_elec_cable_ap_approach_length_px(18.5)
        assert canvas.elec_cable_ap_approach_length_px() == pytest.approx(18.5)
    finally:
        canvas.deleteLater()


def test_e6_connected_ap_render_path_keeps_endpoint_center(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas._elec_cables = {
            "EK-1": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
            "EK-2": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
        }
        canvas._elec_visible = {"EK-1": True, "EK-2": True}
        canvas._elec_cable_stroke_width = {"EK-1": 2.0, "EK-2": 2.0}
        canvas._cable_start_ap = {"EK-1": "AP-1", "EK-2": "AP-2"}
        canvas._cable_end_ap = {"EK-1": "AP-3", "EK-2": "AP-4"}
        canvas._elec_point_size_px = {
            "AP-1": (30.0, 30.0),
            "AP-2": (30.0, 30.0),
            "AP-3": (30.0, 30.0),
            "AP-4": (30.0, 30.0),
        }

        pts = canvas._elec_cables["EK-1"]
        seg_offsets = canvas._get_elec_cable_segment_offsets("EK-1", pts)
        render_pts = canvas._build_elec_cable_offset_polyline(pts, seg_offsets, "EK-1")

        assert render_pts[0] == pts[0]
        assert render_pts[-1] == pts[-1]
        assert len(render_pts) == 4
        assert render_pts[1].y() == pytest.approx(seg_offsets[0], abs=1e-6)
        assert render_pts[2].y() == pytest.approx(seg_offsets[0], abs=1e-6)
    finally:
        canvas.deleteLater()


def test_e6_hit_testing_with_ap_bend_maps_to_original_segment(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas._elec_cables = {
            "EK-1": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
            "EK-2": [QPointF(0.0, 0.0), QPointF(200.0, 0.0)],
        }
        canvas._elec_visible = {"EK-1": True, "EK-2": True}
        canvas._elec_cable_stroke_width = {"EK-1": 2.0, "EK-2": 2.0}
        canvas._cable_end_ap = {"EK-1": "AP-1", "EK-2": "AP-2"}
        canvas._elec_point_size_px = {"AP-1": (30.0, 30.0), "AP-2": (30.0, 30.0)}

        pts = canvas._elec_cables["EK-1"]
        seg_offsets = canvas._get_elec_cable_segment_offsets("EK-1", pts)
        render_pts = canvas._build_elec_cable_offset_polyline(pts, seg_offsets, "EK-1")
        bend_mid = QPointF(
            (render_pts[-2].x() + render_pts[-1].x()) * 0.5,
            (render_pts[-2].y() + render_pts[-1].y()) * 0.5,
        )

        assert canvas._hit_elec_cable_edge(bend_mid, "EK-1") == (0, 1)
    finally:
        canvas.deleteLater()


def test_e6_ap_bend_omitted_for_straight_center_approach(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        pts = [QPointF(0.0, 0.0), QPointF(200.0, 0.0)]
        seg_offsets = [0.0]
        canvas._cable_end_ap = {"EK-1": "AP-1"}
        canvas._elec_point_size_px = {"AP-1": (30.0, 30.0)}

        render_pts = canvas._build_elec_cable_offset_polyline(pts, seg_offsets, "EK-1")

        assert render_pts == pts
    finally:
        canvas.deleteLater()



