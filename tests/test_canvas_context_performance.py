"""Work-count regressions for the input sequence preceding a context menu."""
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
from PySide6.QtWidgets import QApplication, QMenu

from gui.canvas_widget import CanvasWidget, ToolMode
from model.document import Document
from model.elements import ElecCable, ElecPoint


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def scene(app):
    doc = Document()
    for i in range(1, 41):
        cable = ElecCable.create(f"EK-{i}")
        cable.geom["elec_cables"] = [[20, i * 40], [220, i * 40], [420, i * 40]]
        doc.add(cable)
    canvas = CanvasWidget()
    canvas.set_document(doc)
    canvas._scale = 1.0
    canvas._offset = QPointF()
    canvas._grid_visible = False
    try:
        yield canvas, doc
    finally:
        canvas.close()
        canvas.deleteLater()
        QApplication.sendPostedEvents(canvas, QEvent.Type.DeferredDelete)


def mouse(canvas, kind, point, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
    pos = point * canvas._scale + canvas._offset
    event = QMouseEvent(kind, pos, pos, button, buttons, Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseMove: canvas.mouseMoveEvent,
     QEvent.Type.MouseButtonPress: canvas.mousePressEvent,
     QEvent.Type.MouseButtonRelease: canvas.mouseReleaseEvent}[kind](event)


def press(canvas, point):
    mouse(canvas, QEvent.Type.MouseButtonPress, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)


def release(canvas, point):
    mouse(canvas, QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.LeftButton)


@pytest.mark.parametrize("interaction", ["hover", "left", "miss"])
def test_input_scan_builds_global_signature_at_most_once(scene, monkeypatch, interaction):
    canvas, doc = scene
    build = Mock(wraps=canvas._build_elec_cable_overlap_signature)
    monkeypatch.setattr(canvas, "_build_elec_cable_overlap_signature", build)
    before = deepcopy(doc.to_dict())
    point = QPointF(110, 40 * 40 if interaction != "miss" else 2000)
    if interaction == "left":
        press(canvas, point)
        assert canvas._dragging_elec_cable_id == "EK-40"
        release(canvas, point)
    else:
        mouse(canvas, QEvent.Type.MouseMove, point)
    assert build.call_count == 1
    assert doc.to_dict() == before


@pytest.mark.parametrize("mode,gap", [(ToolMode.NONE, 3.0), (ToolMode.NONE, 0.0),
                                      (ToolMode.EDIT_ELEC_CABLE, 3.0)])
@pytest.mark.parametrize("scale", [0.5, 1.0, 2.0])
@pytest.mark.parametrize("include_points", [False, True])
def test_scoped_hit_matches_individual_lane_and_point_tests(scene, mode, gap, scale, include_points):
    canvas, doc = scene
    canvas._mode, canvas._scale = mode, scale
    canvas.set_elec_cable_overlap_gap_px(gap)
    # Overlapping lanes with bound endpoints: do not substitute raw-line bounds.
    for cid in ("EK-1", "EK-2"):
        canvas._elec_cables[cid] = [QPointF(20, 40), QPointF(220, 40), QPointF(420, 60)]
    doc.add(ElecPoint.create("AP-1"))
    canvas._elec_points["AP-1"] = QPointF(20, 40)
    canvas._cable_start_ap["EK-1"] = "AP-1"
    canvas._elec_visible["EK-3"] = False
    for point in (QPointF(20, 40), QPointF(110, 35), QPointF(110, 47),
                  QPointF(110, 120), QPointF(420, 60), QPointF(110, 1600), QPointF(900, 2000)):
        expected = None
        for cid, pts in canvas._elec_cables.items():
            if not canvas._elec_visible.get(cid, True) or len(pts) < 2:
                continue
            if ((include_points and canvas._hit_elec_cable_point(point, cid) is not None)
                    or canvas._hit_elec_cable_edge(point, cid) is not None):
                expected = cid
                break
        assert canvas._find_elec_cable_at(point, include_points=include_points) == expected


def test_next_scan_revalidates_in_place_edits_visibility_and_selection(scene, monkeypatch):
    canvas, doc = scene
    point = QPointF(110, 1600)
    build = Mock(wraps=canvas._build_elec_cable_overlap_signature)
    monkeypatch.setattr(canvas, "_build_elec_cable_overlap_signature", build)
    assert canvas._find_elec_cable_at(point) == "EK-40"
    # Direct model writes deliberately bypass revision and canvas callbacks.
    raw = doc.elements["elec_cables"]["EK-40"].geom["elec_cables"]
    for p in raw:
        p[1] = 1800
    assert canvas._find_elec_cable_at(point) is None
    canvas._elec_visible["EK-40"] = False
    assert canvas._find_elec_cable_at(QPointF(110, 1800)) is None
    assert build.call_count == 3
    monkeypatch.setattr(canvas, "_is_selectable", lambda kind, cid: False)
    assert canvas._find_elec_cable_at(QPointF(110, 40), selectable_only=True) is None
    assert canvas._find_elec_cable_at(QPointF(110, 40)) == "EK-1"


def test_failed_scan_does_not_leave_prepared_state(scene, monkeypatch):
    canvas, _ = scene
    original = canvas._hit_elec_cable_render_edge
    monkeypatch.setattr(canvas, "_hit_elec_cable_render_edge", Mock(side_effect=RuntimeError("hit failed")))
    with pytest.raises(RuntimeError, match="hit failed"):
        canvas._find_elec_cable_at(QPointF(110, 1600))
    monkeypatch.setattr(canvas, "_hit_elec_cable_render_edge", original)
    build = Mock(wraps=canvas._build_elec_cable_overlap_signature)
    monkeypatch.setattr(canvas, "_build_elec_cable_overlap_signature", build)
    assert canvas._find_elec_cable_at(QPointF(110, 1600)) == "EK-40"
    assert build.call_count == 1


@pytest.mark.parametrize("point", [(110, 1600), (20, 1600), (220, 1600), (420, 1600)])
def test_selection_without_drag_emits_no_mutation(scene, point):
    canvas, doc = scene
    before, revision = deepcopy(doc.to_dict()), doc.revision
    data_changed, cable_changed = [], []
    canvas.document_data_changed.connect(data_changed.append)
    canvas.elec_cable_changed.connect(cable_changed.append)
    press(canvas, QPointF(*point))
    release(canvas, QPointF(*point))
    assert cable_changed == data_changed == []
    assert doc.to_dict() == before and doc.revision == revision
    assert canvas._dragging_elec_cable_id is None and canvas._dragging_route_point is None


@pytest.mark.parametrize("point", [(110, 1600), (220, 1600), (420, 1600)])
def test_actual_drag_still_emits_change(scene, point):
    canvas, doc = scene
    before = deepcopy(doc.to_dict())
    cable_changed = []
    canvas.elec_cable_changed.connect(cable_changed.append)
    start = QPointF(*point)
    end = start + QPointF(15, 15)
    press(canvas, start)
    mouse(canvas, QEvent.Type.MouseMove, end, buttons=Qt.MouseButton.LeftButton)
    release(canvas, end)
    assert cable_changed == ["EK-40"]
    assert doc.to_dict() != before


def test_dragged_endpoint_still_binds_to_ap(scene):
    canvas, doc = scene
    doc.add(ElecPoint.create("AP-1"))
    canvas._elec_points["AP-1"] = QPointF(460, 1620)
    press(canvas, QPointF(420, 1600))
    mouse(canvas, QEvent.Type.MouseMove, QPointF(460, 1620), buttons=Qt.MouseButton.LeftButton)
    release(canvas, QPointF(460, 1620))
    assert canvas._cable_end_ap["EK-40"] == "AP-1"
    assert canvas._elec_cables["EK-40"][-1] == canvas._elec_points["AP-1"]


def test_selected_cable_context_keeps_model_undo_and_menu_actions(app, tmp_path, monkeypatch):
    from gui import app_window, layout_store

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setFallbacksEnabled(False)
    monkeypatch.setattr(layout_store, "settings", lambda: store)
    monkeypatch.setattr(app_window.AppWindow, "_auto_load_last_project", lambda self: None)
    reached_menu = []

    class MenuProbe(QMenu):
        def exec(self, pos):
            reached_menu.append([action.text() for action in self.actions()])
            return None

    monkeypatch.setattr(app_window, "QMenu", MenuProbe)
    window = app_window.AppWindow()
    try:
        doc = Document.from_dict({
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1", "visible": True}]},
            "params": {"floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}}},
        })
        cable = ElecCable.create("EK-1", floor_plan_id="grundriss-1")
        cable.geom["elec_cables"] = [[20, 40], [220, 40], [420, 40]]
        doc.add(cable)
        window._set_document(doc)
        window._apply_workspace("electrical")
        # Initialize the active floor's existing helper-map defaults before
        # checking that ordinary selection/context clicks cause no mutations.
        window.canvas.set_active_helper_floor("grundriss-1")
        window.canvas._scale = 1.0
        window.canvas._offset = QPointF()
        window.canvas._grid_visible = False
        window._dirty = False
        app.processEvents()
        before, revision = deepcopy(doc.to_dict()), doc.revision
        schema_refresh = Mock(wraps=window._refresh_schema_windows)
        monkeypatch.setattr(window, "_refresh_schema_windows", schema_refresh)
        point = QPointF(110, 40)
        press(window.canvas, point)
        release(window.canvas, point)
        editor = window.properties._editors["EK-1"]
        resets = []
        for key in ("start_ap", "end_ap"):
            editor._widgets[key]._combo.model().rowsInserted.connect(lambda *_: resets.append(True))
            editor._widgets[key]._combo.model().rowsRemoved.connect(lambda *_: resets.append(True))
        mouse(window.canvas, QEvent.Type.MouseButtonPress, point, Qt.MouseButton.RightButton,
              Qt.MouseButton.RightButton)
        assert reached_menu and "Punkt hinzufügen" in reached_menu[-1]
        assert "Löschen" in reached_menu[-1]
        assert window.navigator.selected_ids() == ["EK-1"]
        assert window.canvas._selected_item_id == "EK-1"
        assert window._context_menu_canvas_pt is None
        assert not resets
        schema_refresh.assert_not_called()
        assert not window._dirty and not window._undo_stack
        assert doc.revision == revision and doc.to_dict() == before
    finally:
        window._dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()