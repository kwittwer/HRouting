"""Insert control points at double-click positions without losing AP bindings."""
from copy import deepcopy
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from gui.canvas_widget import CanvasWidget, ToolMode
from model.document import Document
from model.elements import ElecCable


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def cable_document():
    return Document.from_dict({
        "canvas": {
            # Undo deliberately retains live view settings. Include their
            # defaults so restoring does not merely add missing view keys.
            "view_scale": 1.0, "view_offset": [0, 0], "bg_color": "#2b2b2b",
            "grid_visible": False, "grid_spacing_mm": 100.0,
            "grid_color": [255, 255, 255, 60], "snap_angle": 90.0,
            "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
            "elec_points": {"AP-1": [20, 40], "AP-2": [420, 40]},
            "elec_cables": {"EK-1": [[20, 40], [420, 40]]},
            "cable_start_ap": {"EK-1": "AP-1"}, "cable_end_ap": {"EK-1": "AP-2"},
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}},
            "elec_points": {pid: {"floor_plan_id": "grundriss-1", "name": pid}
                            for pid in ("AP-1", "AP-2")},
            "elec_cables": {"EK-1": {"floor_plan_id": "grundriss-1", "start_ap": "AP-1", "end_ap": "AP-2"}},
        },
    })


@pytest.fixture
def bound(app):
    doc = cable_document()
    canvas = CanvasWidget()
    canvas.set_document(doc)
    canvas._scale = 1.0
    canvas._offset = QPointF()
    canvas._grid_visible = False
    canvas.set_selected_item("EK-1")
    try:
        yield canvas, doc
    finally:
        canvas.close()
        canvas.deleteLater()
        QApplication.sendPostedEvents(canvas, QEvent.Type.DeferredDelete)


def send(canvas, kind, point, button=Qt.MouseButton.LeftButton, buttons=None):
    pos = point * canvas._scale + canvas._offset
    if buttons is None:
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    event = QMouseEvent(kind, pos, pos, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas, event)


def double_click(canvas, point):
    send(canvas, QEvent.Type.MouseButtonDblClick, point)
    send(canvas, QEvent.Type.MouseButtonRelease, point)


@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize("scale,offset", [(1.0, (0, 0)), (2.25, (-51.5, 63.25)), (0.5, (117.5, -20.0))])
def test_selected_cable_inserts_exact_click_not_midpoint_or_grid(bound, edit, scale, offset):
    canvas, doc = bound
    canvas._scale, canvas._offset = scale, QPointF(*offset)
    canvas._grid_visible = True
    canvas._grid_spacing_mm = 100
    if edit:
        canvas.start_edit_elec_cable("EK-1")
    changes = []
    canvas.elec_cable_changed.connect(changes.append)
    point = QPointF(137.25, 40)
    double_click(canvas, point)
    assert canvas._elec_cables["EK-1"] == [QPointF(20, 40), point, QPointF(420, 40)]
    assert doc.elements["elec_cables"]["EK-1"].geom["elec_cables"] == [[20, 40], [137.25, 40], [420, 40]]
    assert canvas._cable_start_ap["EK-1"] == "AP-1"
    assert canvas._cable_end_ap["EK-1"] == "AP-2"
    assert changes == ["EK-1"]
    assert canvas.tool_mode() == (ToolMode.EDIT_ELEC_CABLE if edit else ToolMode.NONE)


def test_full_click_sequence_then_drag_new_point(bound):
    canvas, _doc = bound
    point = QPointF(137.25, 40)
    # Qt delivers press/release/double-click/release, not two ordinary presses.
    send(canvas, QEvent.Type.MouseButtonPress, point)
    send(canvas, QEvent.Type.MouseButtonRelease, point)
    double_click(canvas, point)
    assert len(canvas._elec_cables["EK-1"]) == 3
    assert canvas._dragging_elec_cable_id is None
    assert canvas._dragging_route_point is None
    send(canvas, QEvent.Type.MouseButtonPress, point)
    assert canvas._dragging_route_point == ("EK-1", 1)
    end = QPointF(150, 110)
    send(canvas, QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    send(canvas, QEvent.Type.MouseButtonRelease, end)
    assert canvas._elec_cables["EK-1"] == [QPointF(20, 40), end, QPointF(420, 40)]
    assert canvas._cable_start_ap["EK-1"] == "AP-1"
    assert canvas._cable_end_ap["EK-1"] == "AP-2"


def test_selected_render_lane_inserts_into_original_segment(bound):
    canvas, doc = bound
    for cid in ("EK-2", "EK-3"):
        cable = ElecCable.create(cid)
        cable.geom["elec_cables"] = [[20, 40], [420, 40]]
        doc.add(cable)
    canvas.set_selected_item("EK-3")
    canvas.set_elec_cable_overlap_gap_px(20)
    pts = canvas._elec_cables["EK-3"]
    offsets = canvas._get_elec_cable_segment_offsets("EK-3", pts)
    assert offsets[0] > 8  # Deliberately outside the old raw-line hit tolerance.
    point = QPointF(137, 40 + offsets[0])
    before_other = deepcopy(doc.elements["elec_cables"]["EK-1"].geom)
    double_click(canvas, point)
    assert canvas._elec_cables["EK-3"] == [pts[0], point, pts[1]]
    assert doc.elements["elec_cables"]["EK-1"].geom == before_other
    assert len(canvas._elec_cables["EK-2"]) == 2


def test_ap_approach_bend_maps_insertion_to_last_model_segment(bound):
    canvas, doc = bound
    route = [[20, 40], [220, 40], [420, 40]]
    canvas._elec_cables["EK-1"] = [QPointF(*p) for p in route]
    other = ElecCable.create("EK-2")
    other.geom["elec_cables"] = deepcopy(route)
    doc.add(other)
    canvas.set_elec_cable_overlap_gap_px(40)
    canvas.set_elec_cable_ap_approach_length_px(100)
    pts = canvas._elec_cables["EK-1"]
    offsets = canvas._get_elec_cable_segment_offsets("EK-1", pts)
    render, owners = canvas._build_elec_cable_render_geometry("EK-1", pts, offsets)
    point = (render[-2] + render[-1]) * 0.5
    assert canvas._hit_elec_point(point) is None
    assert owners[-1] == 1
    assert canvas._hit_elec_cable_edge(point, "EK-1") == (1, 2)
    double_click(canvas, point)
    assert list(canvas._elec_cables["EK-1"]) == [pts[0], pts[1], point, pts[2]]
    assert canvas._cable_start_ap["EK-1"] == "AP-1"
    assert canvas._cable_end_ap["EK-1"] == "AP-2"


def test_selected_handles_are_drawn_at_draggable_model_points(bound):
    canvas, doc = bound
    other = ElecCable.create("EK-2")
    other.geom["elec_cables"] = [[20, 40], [420, 40]]
    doc.add(other)
    canvas.set_elec_cable_overlap_gap_px(30)
    painter = Mock()
    pts = canvas._elec_cables["EK-1"]
    canvas._draw_elec_cable(painter, "EK-1", pts)
    assert [call.args[0] for call in painter.drawEllipse.call_args_list] == [pts[0], pts[0], pts[1], pts[1]]


@pytest.mark.parametrize("index", [0, -1])
def test_free_endpoint_still_resumes_drawing_without_inserting(bound, index):
    canvas, doc = bound
    canvas._elec_points.clear()
    canvas._cable_start_ap.clear()
    canvas._cable_end_ap.clear()
    before = deepcopy(doc.to_dict())
    double_click(canvas, canvas._elec_cables["EK-1"][index])
    assert doc.to_dict() == before
    assert canvas.tool_mode() == ToolMode.DRAW_ELEC_CABLE
    assert canvas._drawing_cable_from_start == (index == 0)


def test_edit_mode_keeps_existing_point_deletion(bound):
    canvas, _doc = bound
    point = QPointF(137, 40)
    canvas._elec_cables["EK-1"] = [QPointF(20, 40), point, QPointF(420, 40)]
    canvas.start_edit_elec_cable("EK-1")
    double_click(canvas, point)
    assert canvas._elec_cables["EK-1"] == [QPointF(20, 40), QPointF(420, 40)]


def test_context_action_still_inserts_segment_midpoint(bound):
    canvas, _doc = bound
    assert canvas.context_insert_point("elec_cable", "EK-1", QPointF(137, 40))
    assert canvas._elec_cables["EK-1"] == [QPointF(20, 40), QPointF(220, 40), QPointF(420, 40)]


@pytest.mark.parametrize("case", ["miss", "hidden", "filtered", "unselected", "existing_point", "right_button", "other_mode"])
def test_no_insertion_outside_selected_editable_edge(bound, case):
    canvas, doc = bound
    point = QPointF(137, 40)
    button = Qt.MouseButton.LeftButton
    if case == "miss":
        point.setY(80)
    elif case == "hidden":
        canvas._elec_visible["EK-1"] = False
    elif case == "filtered":
        canvas._selectable_layers = {"heating"}
    elif case == "unselected":
        canvas.set_selected_item("")
    elif case == "existing_point":
        canvas._elec_cables["EK-1"] = [QPointF(20, 40), point, QPointF(420, 40)]
    elif case == "right_button":
        button = Qt.MouseButton.RightButton
    elif case == "other_mode":
        canvas._mode = ToolMode.MEASURE
    before = deepcopy(doc.to_dict())
    changes = []
    canvas.elec_cable_changed.connect(changes.append)
    send(canvas, QEvent.Type.MouseButtonDblClick, point, button)
    assert doc.to_dict() == before
    assert not changes


def test_ap_double_click_keeps_priority_and_bindings(bound):
    canvas, doc = bound
    events = []
    canvas.object_double_clicked.connect(lambda kind, eid: events.append((kind, eid)))
    before = deepcopy(doc.to_dict())
    double_click(canvas, QPointF(20, 40))
    assert events == [("elec_point", "AP-1")]
    assert doc.to_dict() == before


def test_double_click_insertion_has_one_undo_step_and_redo(app, tmp_path, monkeypatch):
    from gui import app_window, layout_store

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    monkeypatch.setattr(layout_store, "settings", lambda: settings)
    monkeypatch.setattr(app_window.AppWindow, "_auto_load_last_project", lambda self: None)
    window = app_window.AppWindow()
    try:
        window._set_document(cable_document())
        window._apply_workspace("electrical")
        window._select_element_everywhere("EK-1", update_navigator=True)
        canvas = window.canvas
        canvas._scale, canvas._offset = 1.0, QPointF()
        undo_depth = len(window._undo_stack)
        before = deepcopy(window._document.to_dict())
        # The new discrete edit must form its own undo step even if an earlier
        # edit is still grouped (no idle wait between consecutive double clicks).
        point = QPointF(137, 40)
        send(canvas, QEvent.Type.MouseButtonPress, point)
        send(canvas, QEvent.Type.MouseButtonRelease, point)
        assert len(window._undo_stack) == undo_depth
        double_click(canvas, point)
        inserted = deepcopy(window._document.to_dict())
        assert len(canvas._elec_cables["EK-1"]) == 3
        assert len(window._undo_stack) == undo_depth + 1
        double_click(canvas, QPointF(300, 40))
        assert len(canvas._elec_cables["EK-1"]) == 4
        assert len(window._undo_stack) == undo_depth + 2
        window._undo()
        assert window._document.to_dict() == inserted
        window._undo()
        assert window._document.to_dict() == before
        window._redo()
        assert window._document.to_dict() == inserted
        assert window._dirty
    finally:
        window._dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()