"""Opt-in Qt event-loop instrumentation; never imported by the application.

Only synthetic input handlers and paintEvent are timed, not OS delivery or
monitor presentation. Multiple dispatched inputs may share their first later
paint. This does NOT imply that every intermediate state was rendered, or that
a partial paint repainted the entire viewport. No grab/repaint calls are used.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent

from gui.canvas_widget import CanvasWidget


EVENT_SCENARIOS = ("pan", "zoom")
INPUT_TYPES = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
               QEvent.Type.MouseButtonRelease, QEvent.Type.Wheel)
EVENT_MEASUREMENT = "qt_posted_input_to_paint_completion_not_display_presentation"


def _pan_target(anchor: QPointF, index: int) -> QPointF:
    # Same 120-step cycle and amplitude as the grab benchmark, first move nonzero.
    phase = 2.0 * math.pi * ((index + 1) % 120) / 120.0
    return anchor + QPointF(120.0 * math.sin(phase), 80.0 * (1.0 - math.cos(phase)))


def _wheel_delta(index: int) -> int:
    # Four ten-step legs: +, -, -, + (three cycles per 120 inputs). Keep
    # excursions moderate (~4x, not 1.15**30); the widget still owns its limits.
    # Returns to initial scale unless clamped. Never assign transforms per input.
    return 120 if (index % 40) // 10 in (0, 3) else -120


def _mouse_event(widget: CanvasWidget, kind: QEvent.Type, pos: QPointF) -> QMouseEvent:
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.MiddleButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.MiddleButton
    return QMouseEvent(kind, pos, QPointF(widget.mapToGlobal(pos.toPoint())),
                       button, buttons, Qt.KeyboardModifier.NoModifier)


class EventCanvas(CanvasWidget):
    """Instrument only this benchmark-owned widget, never patch CanvasWidget."""

    def __init__(self) -> None:
        self._benchmark_run: _EventRun | None = None
        super().__init__()
        # Closing a repetition must not enqueue application-wide quit events.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

    def _input(self, event, handler: Callable) -> None:
        if self._benchmark_run is None:
            handler(event)
        else:
            self._benchmark_run.dispatch(event, handler)

    def mousePressEvent(self, event) -> None:
        self._input(event, super().mousePressEvent)

    def mouseMoveEvent(self, event) -> None:
        self._input(event, super().mouseMoveEvent)

    def mouseReleaseEvent(self, event) -> None:
        self._input(event, super().mouseReleaseEvent)

    def wheelEvent(self, event) -> None:
        self._input(event, super().wheelEvent)

    def paintEvent(self, event) -> None:
        runner = self._benchmark_run
        if runner is None:
            super().paintEvent(event)
            return
        if runner.error is not None:
            return
        # Snapshot at entry, not exit: inputs dispatched by a nested event loop
        # during a paint cannot be attributed to that already-started paint.
        pending = tuple(runner.pending)
        stage = runner.stage
        reduced_quality = bool(self._interactive_quality and not self._full_quality_render_depth)
        started = runner.now_ms()
        try:
            super().paintEvent(event)
            finished = runner.now_ms()
            runner.paint_completed(started, finished, pending, stage, event.region().rectCount(),
                                   reduced_quality=reduced_quality)
        except BaseException as exc:
            # Exceptions escaping Qt virtual methods otherwise reach sys.excepthook
            # rather than the caller of exec(). Re-raise after stopping the loop.
            runner.fail(exc)


class EventDeadlineError(RuntimeError):
    def __init__(self, report: dict) -> None:
        self.report = report
        super().__init__(f"Event benchmark deadline exceeded: {report['counters']}")


class _EventRun(QObject):
    def __init__(self, widget: EventCanvas, scenario: str, frames: int, interval_ms: int,
                 idle_ms: int, deadline_ms: int) -> None:
        if scenario not in EVENT_SCENARIOS:
            raise ValueError(f"Unknown event scenario: {scenario}")
        if min(frames, interval_ms, idle_ms, deadline_ms) < 1:
            raise ValueError("Event counts and timer intervals must be positive")
        super().__init__(widget)
        self.widget = widget
        self.scenario = scenario
        self.frames = frames
        self.interval_ms = interval_ms
        self.idle_ms = idle_ms
        self.deadline_ms = deadline_ms
        self.loop = QEventLoop()
        self.error: BaseException | None = None
        self.stage = "initial"
        self.origin_ns = time.perf_counter_ns()
        self.inputs: list[dict] = []
        self.paints: list[dict] = []
        self.pending: list[int] = []
        self.foreign_inputs = 0
        self.index = 0
        self.idle_elapsed = False
        self.idle_probe_requested_ms: float | None = None
        self.idle_probe_paint_id: int | None = None
        self.initial_paint_ms: float | None = None
        self.anchor = QPointF(widget.width() / 2.0, widget.height() / 2.0)
        self.last_pos = QPointF(self.anchor)
        self.issue_timer = self._timer(self.issue, interval_ms, single=False)
        self.idle_timer = self._timer(self.idle_finished, idle_ms)
        self.deadline_timer = self._timer(self.expired, deadline_ms)
        self.finish_timer = self._timer(self.finish, 0)

    def _timer(self, callback: Callable, interval: int, *, single: bool = True) -> QTimer:
        timer = QTimer(self)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setSingleShot(single)
        timer.setInterval(interval)

        def guarded() -> None:
            if self.error is not None:
                return
            try:
                self.check_deadline()
                callback()
            except BaseException as exc:
                self.fail(exc)

        timer.timeout.connect(guarded)
        return timer

    def now_ms(self) -> float:
        return (time.perf_counter_ns() - self.origin_ns) / 1_000_000.0

    def check_deadline(self) -> None:
        if self.now_ms() >= self.deadline_ms:
            self.expired()

    def expired(self) -> None:
        self.stage = "timed_out"
        raise EventDeadlineError(self.report())

    def fail(self, exc: BaseException) -> None:
        if self.error is None:
            self.error = exc
        self.stage = "failed"
        self.loop.quit()

    def post(self, event, role: str) -> None:
        seq = len(self.inputs) + 1
        # QInputEvent's C++ timestamp survives postEvent ownership transfer.
        # It is a correlation ID here, NOT an OS timestamp. Avoid Python attrs
        # on event wrappers (Qt may recreate them on delivery).
        event.setTimestamp(seq)
        row = {"id": seq, "type": event.type().name, "role": role,
               "position": [event.position().x(), event.position().y()],
               "angle_delta_y": event.angleDelta().y() if isinstance(event, QWheelEvent) else None,
             "enqueue_ms": None, "dispatch_ms": None, "handler_end_ms": None,
               "handler_ms": None, "paint_id": None, "queue_delay_ms": None,
               "enqueue_to_paint_ms": None, "input_to_paint_ms": None}
        self.inputs.append(row)
        row["enqueue_ms"] = self.now_ms()
        QCoreApplication.postEvent(self.widget, event)

    def issue(self) -> None:
        if self.stage != "inputs":
            return
        if self.scenario == "pan" and not self.inputs:
            self.post(_mouse_event(self.widget, QEvent.Type.MouseButtonPress, self.anchor), "begin")
            return
        if self.index < self.frames:
            if self.scenario == "pan":
                self.last_pos = _pan_target(self.anchor, self.index)
                event = _mouse_event(self.widget, QEvent.Type.MouseMove, self.last_pos)
            else:
                event = QWheelEvent(self.anchor, QPointF(self.widget.mapToGlobal(self.anchor.toPoint())),
                                    QPoint(), QPoint(0, _wheel_delta(self.index)),
                                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                    Qt.ScrollPhase.NoScrollPhase, False)
            self.post(event, "step")
            self.index += 1
            # Release on its own timer tick. Zoom has no pan-style button state.
            if self.index == self.frames and self.scenario == "zoom":
                self.issue_timer.stop()
                self.stage = "draining"
            return
        self.post(_mouse_event(self.widget, QEvent.Type.MouseButtonRelease, self.last_pos), "end")
        self.issue_timer.stop()
        self.stage = "draining"

    def dispatch(self, event, handler: Callable) -> None:
        if self.error is not None:
            return
        try:
            self.check_deadline()
            seq = int(event.timestamp())
            row = self.inputs[seq - 1] if 1 <= seq <= len(self.inputs) else None
            if event.spontaneous() or row is None or row["type"] != event.type().name or row["dispatch_ms"] is not None:
                # Do not let real pointer motion contaminate deterministic runs.
                self.foreign_inputs += 1
                event.accept()
                return
            row["dispatch_ms"] = self.now_ms()
            row["queue_delay_ms"] = row["dispatch_ms"] - row["enqueue_ms"]
            started = time.perf_counter_ns()
            try:
                handler(event)
            finally:
                finished = time.perf_counter_ns()
                row["handler_ms"] = (finished - started) / 1_000_000.0
                row["handler_end_ms"] = (finished - self.origin_ns) / 1_000_000.0
            self.pending.append(seq)
            # Idle starts at delivery of the final input, never at enqueue time.
            if self.stage == "draining" and seq == len(self.inputs):
                self.idle_timer.start()
        except BaseException as exc:
            self.fail(exc)

    def paint_completed(self, started: float, finished: float, pending: tuple[int, ...],
                        stage: str, rect_count: int, *, reduced_quality: bool | None = None) -> None:
        if self.error is not None:
            return
        self.check_deadline()
        if stage == "initial":
            self.initial_paint_ms = finished - started
            self.stage = "inputs"
            self.issue_timer.start()
            return
        if stage in ("failed", "done"):
            return
        paint_id = len(self.paints) + 1
        # A nested paint might have consumed some of the snapshot already.
        still_pending = set(self.pending)
        eligible = [seq for seq in pending if seq in still_pending]
        paint = {"id": paint_id, "start_ms": started, "end_ms": finished,
                 "duration_ms": finished - started, "input_ids": eligible,
                 "region_rect_count": rect_count,
                 "reduced_grid_quality": reduced_quality,
                 "phase": "idle_probe" if stage == "idle_probe" else "natural"}
        self.paints.append(paint)
        for seq in eligible:
            row = self.inputs[seq - 1]
            row["paint_id"] = paint_id
            row["enqueue_to_paint_ms"] = finished - row["enqueue_ms"]
            row["input_to_paint_ms"] = finished - row["dispatch_ms"]
        consumed = set(eligible)
        self.pending = [seq for seq in self.pending if seq not in consumed]
        if stage == "idle_probe":
            self.idle_probe_paint_id = paint_id
            self.finish_timer.start()
        else:
            self.maybe_probe()

    def idle_finished(self) -> None:
        self.idle_elapsed = True
        self.maybe_probe()

    def maybe_probe(self) -> None:
        if (self.stage == "draining" and self.idle_elapsed and not self.pending
                and all(row["paint_id"] is not None for row in self.inputs)):
            self.stage = "idle_probe"
            self.idle_probe_requested_ms = self.now_ms()
            # ONE asynchronous final idle probe, explicitly separate from natural
            # input paints. Never rescue missing input paints with this update.
            self.widget.update()

    def finish(self) -> None:
        self.stage = "done"
        self.loop.quit()

    def report(self) -> dict:
        dispatched = sum(row["dispatch_ms"] is not None for row in self.inputs)
        painted = sum(row["paint_id"] is not None for row in self.inputs)
        idle_paint = self.paints[self.idle_probe_paint_id - 1] if self.idle_probe_paint_id is not None else None
        idle_end = idle_paint["end_ms"] if idle_paint else None
        last_input = self.inputs[-1] if self.inputs else None
        last_dispatch = last_input["dispatch_ms"] if last_input else None
        refinement = next((paint for paint in self.paints
                           if last_dispatch is not None and paint["start_ms"] >= last_dispatch
                           and paint["phase"] == "natural"
                           and paint.get("reduced_grid_quality") is False), None)
        return {
            "counter": "time.perf_counter_ns", "timestamp_unit": "ms_relative_to_run_start",
            "event_timestamp": "synthetic_sequence_id_not_os_time",
            "cadence": "one_input_per_QTimer_timeout_no_catch_up; GUI_thread_overruns_delay_issue",
            "cycles": {"pan_steps": 120, "zoom_steps": 40, "zoom_legs": [10, -20, 10]},
            "paint_counter_scope": "excludes_initial_exposure_and_warmup; includes_separate_idle_probe",
            "instrumentation": "Python_bookkeeping_overhead_affects_cadence; excluded_from_handler_and_paint_durations",
            "latency_semantics": "first_paintEvent_completion_after_handler_return; coalesced_inputs_share_paint; not_presentation",
            "input_origin": "dispatch_ms_is_synthetic_handler_wrapper_entry_not_OS_input_time",
            "interval_ms": self.interval_ms, "idle_ms": self.idle_ms, "deadline_ms": self.deadline_ms,
            "elapsed_ms": self.now_ms(), "status": self.stage,
            "counters": {"posted": len(self.inputs), "dispatched": dispatched,
                         "undispatched": len(self.inputs) - dispatched,
                         "inputs_with_paint": painted, "inputs_without_paint": dispatched - painted,
                         "step_events_posted": sum(row["role"] == "step" for row in self.inputs),
                         "paint_events": len(self.paints),
                         "natural_paint_events": sum(p["phase"] == "natural" for p in self.paints),
                         "coalesced_paints": sum(len(p["input_ids"]) > 1 for p in self.paints),
                         "extra_inputs_sharing_paint": sum(max(0, len(p["input_ids"]) - 1) for p in self.paints),
                         "foreign_inputs_ignored": self.foreign_inputs},
            "initial_paint_ms": self.initial_paint_ms,
            "final_full_quality": {
                "paint_id": refinement["id"] if refinement else None,
                "last_input_to_completion_ms": refinement["end_ms"] - last_dispatch if refinement else None,
                "meaning": "first_natural_full_grid_quality_paint_starting_after_last_dispatched_input; not_presentation",
            },
            "idle_probe": {"forced_async_update": True, "requested_ms": self.idle_probe_requested_ms,
                           "paint_id": self.idle_probe_paint_id, "completion_ms": idle_end,
                           "last_enqueue_to_completion_ms": idle_end - last_input["enqueue_ms"]
                           if idle_end is not None and last_input else None,
                           "last_input_to_completion_ms": idle_end - last_input["dispatch_ms"]
                           if idle_end is not None and last_input else None,
                           "meaning": "paint_after_configured_idle_wait; not_automatic_refinement_latency_or_quality_verification"},
            "inputs": self.inputs, "paints": self.paints,
        }

    def run(self) -> dict:
        self.origin_ns = time.perf_counter_ns()
        self.widget._benchmark_run = self
        try:
            self.deadline_timer.start()
            self.widget.show()
            self.widget.update()  # Setup only: first natural paint gates issue.
            if self.error is None:
                self.loop.exec()
            if self.error is not None:
                raise self.error
            if self.stage != "done":
                raise RuntimeError("Event loop exited before benchmark completion")
            return self.report()
        finally:
            for timer in (self.issue_timer, self.idle_timer, self.deadline_timer, self.finish_timer):
                timer.stop()
            # Only this owned widget; never discard events from the application.
            for kind in INPUT_TYPES:
                QCoreApplication.removePostedEvents(self.widget, int(kind))
            self.widget._benchmark_run = None
            try:
                if self.widget._panning:
                    # Abort cleanup only, excluded from measurements. Avoid leaving
                    # a pressed button if the deadline interrupted the sequence.
                    CanvasWidget.mouseReleaseEvent(
                        self.widget, _mouse_event(self.widget, QEvent.Type.MouseButtonRelease, self.last_pos))
            finally:
                self.deleteLater()


def measure_events(widget: EventCanvas, scenario: str, frames: int, *, interval_ms: int,
                   idle_ms: int, deadline_ms: int) -> dict:
    return _EventRun(widget, scenario, frames, interval_ms, idle_ms, deadline_ms).run()