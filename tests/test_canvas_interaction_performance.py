"""Deterministic interaction/write-count regressions on real Document bindings.

No AppWindow, persisted settings, example projects, or elapsed-time benchmarks.
Only the interaction clock is controlled; trailing repaint uses a real QTimer.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QEventLoop, QPointF, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import gui.canvas_widget as canvas_module  # noqa: E402
from gui.canvas_widget import CanvasWidget, ToolMode  # noqa: E402
from model.document import Document  # noqa: E402
from model.elements import AnnotationPolyline, ElecCable, ElecPoint  # noqa: E402
from model.views import DocumentMapView  # noqa: E402


class RecordingCanvas(CanvasWidget):
    """Observe update requests, including the slot connected during __init__."""

    def __init__(self):
        self.update_offsets = []
        super().__init__()

    def update(self, *args):
        if hasattr(self, "_offset"):
            self.update_offsets.append(QPointF(self._offset))
        super().update(*args)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def bound(app):
    document = Document()
    for point_id in ("AP-1", "AP-2"):
        document.add(ElecPoint.create(point_id))
    document.add(ElecCable.create("EK-1"))
    document.add(AnnotationPolyline.create("ANP-1"))
    widget = RecordingCanvas()
    widget.set_document(document)
    widget._grid_visible = False
    widget._scale = 1.0
    widget._offset = QPointF()
    widget._drag_render_interval_ms = 20.0
    widget._last_drag_render_time = 0.0
    widget.update_offsets.clear()
    assert widget.document() is document
    assert isinstance(widget._elec_cables, DocumentMapView)
    try:
        yield widget, document
    finally:
        widget._interaction_update_timer.stop()
        widget.close()
        widget.deleteLater()
        QApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)


@pytest.fixture
def clock(monkeypatch):
    now = [10.0]
    # Replace only this module's clock, not the global time module used by Qt/tests.
    monkeypatch.setattr(canvas_module, "time", SimpleNamespace(
        monotonic=lambda: now[0], time=lambda: now[0],
    ))
    return now


def mouse_move(canvas, x, y, buttons=Qt.MouseButton.LeftButton):
    position = QPointF(x, y)
    event = QMouseEvent(
        QEvent.Type.MouseMove, position, position,
        Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier,
    )
    canvas.mouseMoveEvent(event)


def start_pan(canvas, anchor):
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, anchor, anchor,
        Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mousePressEvent(event)
    assert canvas._panning
    assert canvas._pan_start == anchor


def observe_route_writes(monkeypatch, canvas):
    """Count real view commits, including WritebackList's per-item flushes."""
    writes = []
    original = DocumentMapView.__setitem__

    def record(view, key, value):
        if view is canvas._elec_cables:
            writes.append((key, [[p.x(), p.y()] for p in value]))
        return original(view, key, value)

    monkeypatch.setattr(DocumentMapView, "__setitem__", record)
    return writes


@pytest.mark.parametrize("mode", [
    ToolMode.NONE, ToolMode.MEASURE, ToolMode.MEASURE_ANGLE,
    ToolMode.DRAW_ANNOTATION_POLYLINE,
], ids=["idle", "distance", "angle", "drawing-polyline"])
def test_pan_bypasses_hit_testing_snapping_and_annotation_preview(
    bound, monkeypatch, clock, mode,
):
    canvas, document = bound
    canvas._mode = mode
    canvas._scale = 2.0
    canvas._offset = QPointF(10, -20)
    canvas._placing_annotation_id = "ANP-1"
    canvas._placing_annotation_kind = "annotation_polyline"
    canvas._annotation_polylines["ANP-1"] = {
        "points": [[30.0, 40.0], [70.0, 80.0], [90.0, 100.0]],
    }
    canvas._annotation_preview = QPointF(90, 100)
    before = deepcopy(document.to_dict())
    preview = QPointF(canvas._annotation_preview)
    changed = []
    canvas.document_data_changed.connect(changed.append)
    hit = Mock(return_value=None)
    snap = Mock(return_value=QPointF(-999, -999))
    monkeypatch.setattr(canvas, "_hit_any_object", hit)
    monkeypatch.setattr(canvas, "_snap_measure_point", snap)
    monkeypatch.setattr(canvas, "_should_update_hover_hit_test", Mock(return_value=True))

    # QPointF(0, 0) is a valid anchor, not an absent pan start.
    start_pan(canvas, QPointF(0, 0))
    for x, y in ((12, 18), (33, 45), (25, 31)):
        mouse_move(canvas, x, y, Qt.MouseButton.MiddleButton)
        assert canvas._offset == QPointF(10 + x, -20 + y)
        assert canvas._pan_start == QPointF(x, y)
        assert canvas._mouse_pos == QPointF(-5, 10)
        assert canvas._annotation_preview == preview
        assert document.to_dict() == before

    hit.assert_not_called()
    snap.assert_not_called()
    assert changed == []
    assert canvas._mode == mode


@pytest.mark.parametrize("anchor", [(0, 0), (25, 40)], ids=["origin", "nonzero"])
def test_pan_burst_keeps_latest_offset_and_real_trailing_repaint(bound, clock, anchor):
    canvas, _document = bound
    canvas._offset = QPointF(100, -50)
    canvas._scale = 2.0
    start_pan(canvas, QPointF(*anchor))
    x, y = anchor
    timer = canvas._interaction_update_timer
    assert timer.isSingleShot()

    mouse_move(canvas, x + 10, y + 15, Qt.MouseButton.MiddleButton)
    assert canvas._offset == QPointF(110, -35)
    assert canvas.update_offsets == [QPointF(110, -35)]
    assert not timer.isActive()

    clock[0] += 0.002
    mouse_move(canvas, x + 30, y + 40, Qt.MouseButton.MiddleButton)
    assert canvas._offset == QPointF(130, -10)
    assert timer.isActive()
    scheduled_interval = timer.interval()
    assert 1 <= scheduled_interval <= 20
    clock[0] += 0.003
    mouse_move(canvas, x + 45, y + 70, Qt.MouseButton.MiddleButton)
    assert canvas._offset == QPointF(145, 20)
    assert canvas._pan_start == QPointF(x + 45, y + 70)
    assert canvas.update_offsets == [QPointF(110, -35)]
    assert timer.isActive()
    assert timer.interval() == scheduled_interval  # Burst must not restart the timer.

    # Let Qt deliver the real trailing timeout, without a sleep/timing benchmark.
    loop = QEventLoop()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    fired = []

    def trailing_frame():
        fired.append(QPointF(canvas._offset))
        loop.quit()

    timer.timeout.connect(trailing_frame)
    watchdog.timeout.connect(loop.quit)
    try:
        watchdog.start(1000)
        loop.exec()
    finally:
        watchdog.stop()
        timer.timeout.disconnect(trailing_frame)
    assert fired == [QPointF(145, 20)], "Trailing repaint timer did not fire"
    assert not timer.isActive()
    assert canvas.update_offsets == [QPointF(110, -35), QPointF(145, 20)]


def test_whole_cable_drag_skips_hover_and_commits_one_complete_route_per_move(
    bound, monkeypatch, clock,
):
    canvas, document = bound
    origin = [[10, 20], [40, 50], [70, 80], [100, 110], [130, 140]]
    canvas._elec_points["AP-1"] = QPointF(*origin[0])
    canvas._elec_points["AP-2"] = QPointF(*origin[-1])
    canvas._elec_cables["EK-1"] = [QPointF(*p) for p in origin]
    canvas._cable_start_ap["EK-1"] = "AP-1"
    canvas._cable_end_ap["EK-1"] = "AP-2"
    canvas._mode = ToolMode.NONE
    canvas._dragging_elec_cable_id = "EK-1"
    canvas._dragging_elec_cable_start = QPointF(50, 60)
    canvas._dragging_elec_cable_origin = [QPointF(*p) for p in origin]
    canvas._dragging_elec_cable_fixed_indices = {0, len(origin) - 1}
    canvas._scale = 2.0
    canvas._offset = QPointF(7, 11)
    hit = Mock(return_value=None)
    monkeypatch.setattr(canvas, "_hit_any_object", hit)
    monkeypatch.setattr(canvas, "_should_update_hover_hit_test", Mock(return_value=True))
    writes = observe_route_writes(monkeypatch, canvas)
    notifications = []

    def changed(element_id):
        notifications.append((element_id, deepcopy(
            document.to_dict()["canvas"]["elec_cables"]["EK-1"],
        )))

    canvas.document_data_changed.connect(changed)
    for dx, dy in ((15, -5), (30, 20), (-10, 12)):
        writes.clear()
        notifications.clear()
        mouse_move(canvas, (50 + dx) * 2 + 7, (60 + dy) * 2 + 11)
        expected = [origin[0]] + [[x + dx, y + dy] for x, y in origin[1:-1]] + [origin[-1]]
        assert writes == [("EK-1", expected)]
        assert notifications == [("EK-1", expected)]
        assert document.to_dict()["canvas"]["elec_cables"]["EK-1"] == expected
        assert canvas._dirty_moved_elec_cables == {"EK-1"}
        assert canvas._dragging_elec_cable_origin == [QPointF(*p) for p in origin]

    # Repeating the last position must not commit an equal route again.
    writes.clear()
    notifications.clear()
    mouse_move(canvas, 87, 155)
    assert writes == []
    assert notifications == []
    hit.assert_not_called()
    assert document.to_dict()["canvas"]["elec_points"] == {
        "AP-1": origin[0], "AP-2": origin[-1],
    }


@pytest.mark.parametrize("binding_source", ["canvas", "params"])
def test_sync_same_ap_at_both_ends_commits_once_and_equal_repeat_does_not_write(
    bound, monkeypatch, binding_source,
):
    canvas, document = bound
    origin = [[10, 20], [40, 50], [70, 80], [100, 110]]
    canvas._elec_cables["EK-1"] = [QPointF(*p) for p in origin]
    canvas._elec_points["AP-1"] = QPointF(250, 300)
    cable = document.elements["elec_cables"]["EK-1"]
    if binding_source == "params":
        cable.start_ap = "AP-1"
        cable.end_ap = "AP-1"
        assert "cable_start_ap" not in cable.geom
        assert "cable_end_ap" not in cable.geom
    else:
        canvas._cable_start_ap["EK-1"] = "AP-1"
        canvas._cable_end_ap["EK-1"] = "AP-1"
    writes = observe_route_writes(monkeypatch, canvas)
    changed = []
    canvas.document_data_changed.connect(changed.append)

    expected = [[250, 300], origin[1], origin[2], [250, 300]]
    assert canvas.sync_connected_elec_cable_endpoints("AP-1") == {"EK-1"}
    assert writes == [("EK-1", expected)]
    assert document.to_dict()["canvas"]["elec_cables"]["EK-1"] == expected
    assert canvas._cable_start_ap["EK-1"] == "AP-1"
    assert canvas._cable_end_ap["EK-1"] == "AP-1"
    if binding_source == "params":
        assert cable.start_ap == cable.end_ap == "AP-1"
    else:
        assert changed == ["EK-1"]

    # Metadata backfill is allowed once; repeated sync must cause no write at all.
    writes.clear()
    changed.clear()
    before = deepcopy(document.to_dict())
    for _ in range(3):
        assert canvas.sync_connected_elec_cable_endpoints("AP-1") == {"EK-1"}
    assert writes == []
    assert changed == []
    assert document.to_dict() == before