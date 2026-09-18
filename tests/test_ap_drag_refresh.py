"""AP drag keeps model writes live without rebuilding secondary views per move."""
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

from PySide6.QtCore import QEvent, QEventLoop, QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QHideEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from gui import app_window, layout_store
from gui.canvas_widget import ToolMode
from model.document import Document


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setFallbacksEnabled(False)
    monkeypatch.setattr(layout_store, "settings", lambda: store)
    monkeypatch.setattr(app_window.AppWindow, "_auto_load_last_project", lambda self: None)
    w = app_window.AppWindow()
    doc = Document.from_dict({
        "canvas": {
            "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
            "elec_points": {"AP-1": [100, 100], "AP-2": [400, 100]},
            "elec_cables": {
                "EK-1": [[100, 100], [200, 150], [400, 100]],
                "EK-2": [[400, 100], [100, 100]],
                "EK-3": [[100, 100], [200, 200], [100, 100]],
            },
            "cable_start_ap": {"EK-1": "AP-1", "EK-2": "AP-2", "EK-3": "AP-1"},
            "cable_end_ap": {"EK-1": "AP-2", "EK-2": "AP-1", "EK-3": "AP-1"},
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}},
            "elec_points": {pid: {"name": pid, "floor_plan_id": "grundriss-1"}
                            for pid in ("AP-1", "AP-2")},
            "elec_cables": {cid: {"floor_plan_id": "grundriss-1"}
                            for cid in ("EK-1", "EK-2", "EK-3")},
        },
    })
    w._set_document(doc)
    w._apply_workspace("electrical")
    w.canvas.set_active_helper_floor("grundriss-1")
    w.canvas._grid_visible = False
    w.canvas._scale, w.canvas._offset = 1.0, QPointF()
    app.processEvents()
    w._undo_group_timer.stop()
    w._finish_undo_group()
    w._undo_stack.clear()
    w._dirty = False
    try:
        yield w
    finally:
        w._dirty = False
        w.close()
        w.deleteLater()
        QApplication.sendPostedEvents(w, QEvent.DeferredDelete)
        app.processEvents()


def mouse(canvas, kind, point):
    p = point * canvas._scale + canvas._offset
    button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    event = QMouseEvent(kind, p, p, button, buttons, Qt.NoModifier)
    QApplication.sendEvent(canvas, event)


def start_drag(window):
    mouse(window.canvas, QEvent.MouseButtonPress, QPointF(100, 100))
    assert window.canvas.tool_mode() == ToolMode.MOVE_ELEC_POINT


def test_moves_write_live_model_but_defer_expensive_views(window, monkeypatch):
    c = window.canvas
    start_drag(window)
    schema = Mock(wraps=window._refresh_schema_windows)
    properties = Mock(wraps=window.properties.refresh_element)
    monkeypatch.setattr(window, "_refresh_schema_windows", schema)
    monkeypatch.setattr(window.properties, "refresh_element", properties)
    notifications = []
    c.document_data_changed.connect(notifications.append)
    for i in range(1, 11):
        point = QPointF(100 + i * 5, 100 + i * 2)
        mouse(c, QEvent.MouseMove, point)
        assert c._elec_points["AP-1"] == point
        assert window._document.get("AP-1").geom["elec_points"] == [point.x(), point.y()]
    schema.assert_not_called()
    properties.assert_not_called()
    assert notifications == ["AP-1"] * 10
    assert window._dirty and len(window._undo_stack) == 1
    # A timer firing while the button is still held must also stay cheap.
    window._flush_pending_canvas_refresh()
    schema.assert_not_called()
    mouse(c, QEvent.MouseButtonRelease, point)
    window._flush_pending_canvas_refresh()
    assert schema.call_count == 1
    assert {call.args[0] for call in properties.call_args_list} >= {"AP-1", "EK-1", "EK-2", "EK-3"}
    assert not window._pending_canvas_refresh_ids
    assert not window._canvas_refresh_timer.isActive()
    assert c._elec_cables["EK-1"] == [point, QPointF(200, 150), QPointF(400, 100)]
    assert c._elec_cables["EK-2"] == [QPointF(400, 100), point]
    assert c._elec_cables["EK-3"] == [point, QPointF(200, 200), point]


def test_release_and_undo_redo_restore_ap_and_cable_geometry(window):
    c = window.canvas
    before = deepcopy(window._document.to_dict())
    start_drag(window)
    mouse(c, QEvent.MouseMove, QPointF(170, 160))
    mouse(c, QEvent.MouseButtonRelease, QPointF(170, 160))
    after = deepcopy(window._document.to_dict())
    assert len(window._undo_stack) == 1
    # Undo is allowed before the queued UI refresh has executed.
    window._undo()
    assert not window._pending_canvas_refresh_ids
    window._flush_pending_canvas_refresh()
    restored = window._document.to_dict()
    for key in ("elec_points", "elec_cables", "cable_start_ap", "cable_end_ap"):
        assert restored["canvas"][key] == before["canvas"][key]
    window._redo()
    restored = window._document.to_dict()
    for key in ("elec_points", "elec_cables", "cable_start_ap", "cable_end_ap"):
        assert restored["canvas"][key] == after["canvas"][key]


def test_idle_pause_while_holding_does_not_split_undo(window, monkeypatch):
    start_drag(window)
    mouse(window.canvas, QEvent.MouseMove, QPointF(140, 140))
    snapshot = Mock(wraps=window._document.snapshot)
    monkeypatch.setattr(window._document, "snapshot", snapshot)
    window._finish_undo_group_when_idle()
    snapshot.assert_not_called()
    assert window._undo_group_open
    mouse(window.canvas, QEvent.MouseMove, QPointF(160, 160))
    mouse(window.canvas, QEvent.MouseButtonRelease, QPointF(160, 160))
    window._finish_undo_group_when_idle()
    assert len(window._undo_stack) == 1
    assert not window._undo_group_open


def test_click_and_unchanged_grid_moves_are_not_edits(window):
    c = window.canvas
    c._grid_visible = True
    c._grid_spacing_mm = 100
    before = deepcopy(window._document.to_dict())
    start_drag(window)
    mouse(c, QEvent.MouseMove, QPointF(101, 101))
    mouse(c, QEvent.MouseButtonRelease, QPointF(101, 101))
    assert window._document.to_dict() == before
    assert not window._dirty and not window._undo_stack


def test_rapid_move_requests_trailing_frame(window, monkeypatch):
    c = window.canvas
    start_drag(window)
    request = Mock(wraps=c._request_interaction_update)
    monkeypatch.setattr(c, "_request_interaction_update", request)
    monkeypatch.setattr("gui.canvas_widget.time.monotonic", lambda: 100.0)
    c._last_drag_render_time = 100000.0
    mouse(c, QEvent.MouseMove, QPointF(150, 160))
    request.assert_called_once()
    assert c._interaction_update_timer.isActive()


@pytest.mark.parametrize("finish", ["escape", "workspace", "focus", "hide"])
def test_interrupted_drag_finishes_bindings_and_refresh(window, finish):
    c = window.canvas
    start_drag(window)
    point = QPointF(170, 160)
    mouse(c, QEvent.MouseMove, point)
    if finish == "escape":
        c.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    elif finish == "workspace":
        c.set_selectable_layers({"heating"})
    elif finish == "focus":
        c.focusOutEvent(QFocusEvent(QEvent.FocusOut))
    else:
        c.hideEvent(QHideEvent())
    window._flush_pending_canvas_refresh()
    assert not c.is_elec_point_drag_active()
    assert not window._pending_canvas_refresh_ids
    assert c._elec_cables["EK-1"][0] == point
    assert not c._dirty_moved_elec_points


def test_document_switch_drops_queued_refresh_and_drag(window, monkeypatch):
    start_drag(window)
    mouse(window.canvas, QEvent.MouseMove, QPointF(170, 160))
    assert window._pending_canvas_refresh_ids
    window._set_document(Document())
    schema = Mock()
    monkeypatch.setattr(window, "_refresh_schema_windows", schema)
    window._flush_pending_canvas_refresh()
    schema.assert_not_called()
    assert not window._pending_canvas_refresh_ids
    assert not window.canvas.is_elec_point_drag_active()
    assert not window.canvas._dirty_moved_elec_points


def test_non_drag_canvas_edit_still_refreshes_immediately(window, monkeypatch):
    schema = Mock(wraps=window._refresh_schema_windows)
    monkeypatch.setattr(window, "_refresh_schema_windows", schema)
    window.canvas._elec_points["AP-1"] = QPointF(170, 160)
    schema.assert_called_once()
    assert not window._pending_canvas_refresh_ids


def test_release_delivers_queued_refresh_through_real_event_loop(window, monkeypatch):
    start_drag(window)
    schema = Mock(wraps=window._refresh_schema_windows)
    monkeypatch.setattr(window, "_refresh_schema_windows", schema)
    mouse(window.canvas, QEvent.MouseMove, QPointF(170, 160))
    mouse(window.canvas, QEvent.MouseButtonRelease, QPointF(170, 160))
    schema.assert_not_called()
    loop = QEventLoop()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    window._canvas_refresh_timer.timeout.connect(loop.quit)
    watchdog.timeout.connect(loop.quit)
    try:
        watchdog.start(2500)
        loop.exec()
    finally:
        watchdog.stop()
        window._canvas_refresh_timer.timeout.disconnect(loop.quit)
    schema.assert_called_once()
    assert not window._pending_canvas_refresh_ids