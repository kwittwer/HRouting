"""Canvas-only quality lifecycle tests: no AppWindow, settings or native export.

Reuse the existing real-event helpers, but bind a fresh minimal Document. Timer
checks pump Qt in small QTest waits with a generous watchdog, not an FPS/deadline
benchmark. Export grab is replaced by a QImage grid capture (or deliberate
exception); the actual export guard and _draw_grid are never stubbed out.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFocusEvent, QKeyEvent, QMouseEvent, QPainter, QPixmap, QWheelEvent,
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas_widget import ToolMode  # noqa: E402
from gui.workspaces import workspace  # noqa: E402
from model.document import Document  # noqa: E402
from model.elements import ElecCable, ElecPoint  # noqa: E402
from model.views import DocumentMapView  # noqa: E402
from test_canvas_interaction_performance import (  # noqa: E402
    RecordingCanvas, mouse_move, start_pan,
)
from test_canvas_grid_cache import (  # noqa: E402
    assert_pixels, count_lines, image_background, legacy_grid,
)


class QualityCanvas(RecordingCanvas):
    def __init__(self):
        self.quality_updates = []
        super().__init__()

    def update(self, *args):
        if hasattr(self, "_interactive_quality"):
            self.quality_updates.append((self._interactive_quality, self._full_quality_render_depth))
        super().update(*args)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def bound(app):
    document = Document()
    document.add(ElecPoint.create("AP-1"))
    canvas = QualityCanvas()
    canvas.set_document(document)
    canvas.setMinimumSize(0, 0)
    canvas.resize(160, 120)
    canvas._scale = 1.0
    canvas._offset = QPointF()
    canvas._mm_per_px = 4.0
    canvas._grid_spacing_mm = 12.0  # 3 px: visibly thinned, still snappable.
    canvas._grid_color = QColor(239, 211, 173, 103)
    canvas._grid_visible = True
    canvas._drag_render_interval_ms = 0.0
    canvas.quality_updates.clear()
    assert canvas.document() is document
    assert isinstance(canvas._elec_points, DocumentMapView)
    try:
        yield canvas, document
    finally:
        canvas._quality_idle_timer.stop()
        canvas._interaction_update_timer.stop()
        canvas.close()
        canvas.deleteLater()
        QApplication.sendPostedEvents(canvas, QEvent.Type.DeferredDelete)


def wheel(canvas, delta=120, *, horizontal=0, pixels=0):
    event = QWheelEvent(QPointF(53.25, 41.5), QPointF(53.25, 41.5),
                        QPoint(0, pixels), QPoint(horizontal, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(canvas, event)
    return event


def release(canvas, button=Qt.MouseButton.MiddleButton):
    event = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(70, 60), QPointF(70, 60),
                        button, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas, event)


def wait_for_idle(canvas):
    watchdog = QElapsedTimer()
    watchdog.start()
    while canvas._quality_idle_timer.isActive() and watchdog.elapsed() < 1500:
        QTest.qWait(5)
    assert not canvas._quality_idle_timer.isActive(), "Quality QTimer did not fire"
    assert not canvas._interactive_quality


def capture_grid(canvas, *, legacy=False):
    image = image_background(canvas.width(), canvas.height(), 1.0, "transparent")
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(canvas._offset)
        painter.scale(canvas._scale, canvas._scale)
        if legacy:
            legacy_grid(painter, canvas.width(), canvas.height(), canvas._scale,
                        canvas._offset, canvas._grid_spacing_mm / canvas._mm_per_px,
                        canvas._grid_color)
        else:
            canvas._draw_grid(painter)
    finally:
        painter.end()
    return image


def observe_grid(monkeypatch, canvas):
    draws = []
    original = canvas._grid_layer_cache.draw

    def record(painter, width, height, scale, offset, spacing, color, *, use_cache=True):
        draws.append(dict(interactive=canvas._interactive_quality,
                          depth=canvas._full_quality_render_depth,
                          spacing=spacing, scale=scale, use_cache=use_cache))
        return original(painter, width, height, scale, offset, spacing, color, use_cache=use_cache)

    monkeypatch.setattr(canvas._grid_layer_cache, "draw", record)
    return draws


def snapshot(value):
    """Materialize bound views, rather than deepcopy their QWidget callbacks."""
    if isinstance(value, Mapping):
        return {key: snapshot(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [snapshot(item) for item in value]
    return deepcopy(value)


def test_real_wheel_idle_timer_finishes_with_full_quality_update_without_document_writes(bound):
    canvas, document = bound
    timer = canvas._quality_idle_timer
    assert timer.isSingleShot()
    assert timer.interval() == 120
    before, revision = deepcopy(document.to_dict()), document.revision
    notifications, timeouts = [], []
    document.element_changed.connect(notifications.append)
    document.structure_changed.connect(lambda: notifications.append("structure"))
    canvas.document_data_changed.connect(notifications.append)
    timer.timeout.connect(lambda: timeouts.append(True))
    anchor_before = canvas._to_canvas(QPointF(53.25, 41.5))
    wheel(canvas)
    assert canvas._interactive_quality and timer.isActive()
    assert canvas._scale == pytest.approx(1.15)
    anchor_after = canvas._to_canvas(QPointF(53.25, 41.5))
    assert anchor_after.x() == pytest.approx(anchor_before.x())
    assert anchor_after.y() == pytest.approx(anchor_before.y())
    canvas.quality_updates.clear()
    wait_for_idle(canvas)
    assert timeouts == [True]
    assert canvas.quality_updates == [(False, 0)]
    assert document.to_dict() == before
    assert document.revision == revision
    assert notifications == []
    canvas._finish_interaction_quality()
    assert canvas.quality_updates == [(False, 0)], "Repeated finish must not request more frames"


def test_repeated_input_rearms_one_idle_timer(bound):
    canvas, _document = bound
    timer = canvas._quality_idle_timer
    timer.setInterval(60)
    fired = []
    timer.timeout.connect(lambda: fired.append(True))
    wheel(canvas)
    first_id = timer.timerId()
    QTest.qWait(5)
    wheel(canvas, -120)
    assert canvas._interactive_quality and timer.isActive()
    assert timer.timerId() != first_id, "New input must restart the idle countdown"
    assert not fired
    wait_for_idle(canvas)
    assert fired == [True]


@pytest.mark.parametrize("already_active", [False, True])
@pytest.mark.parametrize("horizontal,pixels", [(0, 0), (120, 0), (0, 15)])
def test_zero_vertical_wheel_is_ignored_without_zoom_or_timer_restart(bound, already_active, horizontal, pixels):
    canvas, document = bound
    if already_active:
        wheel(canvas)
    before = (canvas._scale, QPointF(canvas._offset), canvas._interactive_quality,
              canvas._quality_idle_timer.timerId(), list(canvas.quality_updates), document.to_dict())
    event = wheel(canvas, 0, horizontal=horizontal, pixels=pixels)
    assert not event.isAccepted()
    assert (canvas._scale, canvas._offset, canvas._interactive_quality,
            canvas._quality_idle_timer.timerId(), canvas.quality_updates, document.to_dict()) == before


def test_actual_pan_move_and_release_complete_quality_without_model_changes(bound):
    canvas, document = bound
    before = deepcopy(document.to_dict())
    start_pan(canvas, QPointF(0, 0))
    assert not canvas._interactive_quality
    mouse_move(canvas, 17.25, -8.5, Qt.MouseButton.MiddleButton)
    assert canvas._interactive_quality and canvas._quality_idle_timer.isActive()
    assert canvas._offset == QPointF(17.25, -8.5)
    release(canvas)
    assert not canvas._panning and canvas._pan_start is None
    assert not canvas._interactive_quality and not canvas._quality_idle_timer.isActive()
    assert canvas.quality_updates[-1] == (False, 0)
    assert document.to_dict() == before


def test_hover_stays_full_quality_but_real_placement_preview_uses_unchanged_snap(bound):
    canvas, document = bound
    mouse_move(canvas, 37.4, 28.2, Qt.MouseButton.NoButton)
    assert not canvas._interactive_quality
    assert not canvas._quality_idle_timer.isActive()
    canvas.start_place_elec_point("AP-1", 12, 12)
    before = deepcopy(document.to_dict())
    mouse_move(canvas, 37.4, 28.2, Qt.MouseButton.NoButton)
    assert canvas._interactive_quality and canvas._quality_idle_timer.isActive()
    assert canvas._ghost_preview_pos == QPointF(36, 27)
    assert document.to_dict() == before
    wait_for_idle(canvas)
    assert canvas._ghost_preview_pos == QPointF(36, 27)
    assert document.to_dict() == before


def test_real_cable_point_press_move_release_ends_quality(bound):
    canvas, document = bound
    document.add(ElecCable.create("EK-1"))
    canvas._elec_cables["EK-1"] = [QPointF(30, 30), QPointF(75, 60), QPointF(120, 90)]
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(75, 60), QPointF(75, 60),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas, press)
    assert canvas._dragging_route_point == ("EK-1", 1)
    mouse_move(canvas, 83.4, 67.2)
    assert canvas._interactive_quality
    assert canvas._elec_cables["EK-1"][1] == QPointF(84, 66)
    release(canvas, Qt.MouseButton.LeftButton)
    assert not canvas._interactive_quality and not canvas._quality_idle_timer.isActive()
    assert canvas._dragging_route_point is None
    assert canvas._elec_cables["EK-1"][1] == QPointF(84, 66)


@pytest.mark.parametrize("ending", ["focus", "hide", "workspace", "escape", "return", "enter", "mode"])
def test_lifecycle_paths_finish_quality_and_request_final_frame(bound, ending):
    canvas, document = bound
    if ending == "hide":
        canvas.show()
        QApplication.processEvents()
    wheel(canvas)
    assert canvas._interactive_quality
    before = deepcopy(document.to_dict())
    canvas.quality_updates.clear()
    if ending == "focus":
        QApplication.sendEvent(canvas, QFocusEvent(QEvent.Type.FocusOut))
    elif ending == "hide":
        canvas.hide()
    elif ending == "workspace":
        # Same canvas boundary called by AppWindow's workspace switch.
        canvas.set_selectable_layers(workspace("electrical").selectable_layers)
    elif ending == "mode":
        canvas.set_tool_mode(ToolMode.MEASURE)
    else:
        key = {"escape": Qt.Key.Key_Escape, "return": Qt.Key.Key_Return, "enter": Qt.Key.Key_Enter}[ending]
        QApplication.sendEvent(canvas, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
    assert not canvas._interactive_quality
    assert not canvas._quality_idle_timer.isActive()
    assert canvas.quality_updates and all(state == (False, 0) for state in canvas.quality_updates)
    assert document.to_dict() == before


@pytest.mark.parametrize("ending", ["hide", "rebind"])
def test_hide_and_document_rebind_release_grid_cache(bound, monkeypatch, ending):
    canvas, document = bound
    if ending == "hide":
        canvas.show()
        QApplication.processEvents()
    canvas._grid_layer_cache.clear()
    calls = count_lines(monkeypatch)
    capture_grid(canvas)
    capture_grid(canvas)
    assert len(calls) == 1
    assert canvas._grid_layer_cache.pixmap is not None
    before = deepcopy(document.to_dict())
    canvas._begin_interaction_quality()
    if ending == "hide":
        canvas._interaction_update_timer.start(10000)
        canvas.hide()
        assert not canvas._interaction_update_timer.isActive()
    else:
        replacement = Document()
        replacement_before = deepcopy(replacement.to_dict())
        canvas.set_document(replacement)
        assert canvas.document() is replacement
        assert replacement.to_dict() == replacement_before
    assert not canvas._interactive_quality and not canvas._quality_idle_timer.isActive()
    cache = canvas._grid_layer_cache
    assert (cache.key, cache.pixmap, cache.byte_size) == (None, None, 0)
    assert document.to_dict() == before
    capture_grid(canvas)
    assert len(calls) == 2, "First draw after cleanup must rebuild the layer"


@pytest.mark.parametrize("scale", [0.75, 1.25, 3.0])
def test_interactive_grid_only_thins_display_and_never_changes_snapping(bound, monkeypatch, scale):
    canvas, document = bound
    canvas._scale = scale
    canvas._offset = QPointF(-11.25, -6.5)
    points = [QPointF(7.4, -10.2), QPointF(-22.1, 17.6), QPointF(0, 0)]
    expected_snap = [QPointF(6, -9), QPointF(-21, 18), QPointF()]
    before, revision = deepcopy(document.to_dict()), document.revision
    draws = observe_grid(monkeypatch, canvas)
    capture_grid(canvas)
    assert draws[-1]["use_cache"] and draws[-1]["spacing"] == 3
    assert [canvas._snap_to_grid(p) for p in points] == expected_snap
    canvas._begin_interaction_quality()
    capture_grid(canvas)
    expected_spacing = 3 * max(1, math.ceil(8 / (3 * scale)))
    assert draws[-1] == dict(interactive=True, depth=0, spacing=expected_spacing, scale=scale, use_cache=False)
    assert [canvas._snap_to_grid(p) for p in points] == expected_snap
    canvas._finish_interaction_quality()
    capture_grid(canvas)
    assert draws[-1]["spacing"] == 3 and draws[-1]["use_cache"]
    assert [canvas._snap_to_grid(p) for p in points] == expected_snap
    assert canvas._grid_spacing_mm == 12 and canvas._mm_per_px == 4
    assert document.to_dict() == before and document.revision == revision


@pytest.mark.parametrize("raises", [False, True], ids=["success", "grab-failure"])
@pytest.mark.parametrize("outer_depth", [0, 2], ids=["normal", "nested-guard"])
def test_export_forces_full_quality_bypasses_cache_and_restores_even_on_raise(bound, monkeypatch, raises, outer_depth):
    canvas, document = bound
    canvas._scale = 1.25
    canvas._offset = QPointF(-9.5, 3.75)
    canvas.setMinimumSize(110, 90)
    capture_grid(canvas)
    cache = canvas._grid_layer_cache
    cached = (cache.key, cache.pixmap.cacheKey(), cache.byte_size)
    canvas._begin_interaction_quality()
    canvas._full_quality_render_depth = outer_depth
    before = (canvas._scale, QPointF(canvas._offset), canvas.size(), canvas.minimumSize())
    serialized = snapshot(canvas.to_dict())
    document_before, revision = deepcopy(document.to_dict()), document.revision
    timer_id = canvas._quality_idle_timer.timerId()
    draws = observe_grid(monkeypatch, canvas)
    captures = []

    def fake_grab():
        assert canvas._interactive_quality is True
        assert canvas._full_quality_render_depth == outer_depth + 1
        assert canvas.width() == 96 and canvas.height() == 80
        # Export must also block interaction-quality restarts from nested code.
        canvas._begin_interaction_quality()
        assert canvas._quality_idle_timer.timerId() == timer_id
        image = capture_grid(canvas)
        assert_pixels(image, capture_grid(canvas, legacy=True), tolerance=0)
        captures.append(image)
        if raises:
            raise RuntimeError("deliberate grab failure")
        return QPixmap.fromImage(image)

    monkeypatch.setattr(canvas, "grab", fake_grab)
    source = QRectF(-11.25, 6.5, 128, 96)
    try:
        if raises:
            with pytest.raises(RuntimeError, match="deliberate grab failure"):
                canvas.render_for_export(source, 96, 80)
        else:
            result = canvas.render_for_export(source, 96, 80)
            assert_pixels(result, captures[0], tolerance=0)
        assert len(captures) == 1
        assert draws == [dict(interactive=True, depth=outer_depth + 1,
                              spacing=3.0, scale=0.75, use_cache=False)]
        assert (canvas._scale, canvas._offset, canvas.size(), canvas.minimumSize()) == before
        assert canvas._full_quality_render_depth == outer_depth
        assert canvas._interactive_quality is True
        assert canvas._quality_idle_timer.isActive()
        assert canvas._quality_idle_timer.timerId() == timer_id
        assert (cache.key, cache.pixmap.cacheKey(), cache.byte_size) == cached
        assert snapshot(canvas.to_dict()) == serialized
        assert document.to_dict() == document_before and document.revision == revision
    finally:
        canvas._full_quality_render_depth = 0


def test_transient_cache_quality_and_timers_are_not_serialized(bound):
    canvas, document = bound
    before_canvas = snapshot(canvas.to_dict())
    before_document, revision = deepcopy(document.to_dict()), document.revision
    capture_grid(canvas)
    canvas._begin_interaction_quality()
    canvas._full_quality_render_depth = 1
    try:
        assert canvas._grid_layer_cache.byte_size > 0
        assert canvas._interactive_quality and canvas._quality_idle_timer.isActive()
        assert snapshot(canvas.to_dict()) == before_canvas
        assert document.to_dict() == before_document
    finally:
        canvas._full_quality_render_depth = 0
    wait_for_idle(canvas)
    assert snapshot(canvas.to_dict()) == before_canvas
    assert document.to_dict() == before_document and document.revision == revision