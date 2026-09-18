from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from types import MethodType

import PySide6
from PySide6.QtCore import QPointF, qVersion
from PySide6.QtWidgets import QApplication

from gui.canvas_widget import CanvasWidget
from gui import layout_store
from model.document import Document
from storage.asset_data_uri import is_data_uri
from storage.hrp_io import load_raw


DEFAULT_PROJECT = "examples/Planung_Linda.hrp"
SCENARIOS = ("static", "pan", "zoom")
MEASUREMENT = "cpu_grab_plus_process_events_not_display_fps"
SCENE_PROFILE = "app_normalized_project_canvas_defaults_v1"
BASELINE_NOTE = (
    "Earlier benchmark/parent results restored raw cable bindings and routes. "
    "They are not directly comparable; rerun both revisions with this scene profile."
)


class _CanvasRestoreAdapter:
    """Plain Python host for the shell's narrowly scoped restore methods.

    Normalization only needs _document, canvas._label_map, and the three
    methods below. In particular, both names and AP positions must come from
    this document, not a previous document bound to the canvas. No QWidget
    inheritance, settings, window construction or project-opening workflow.
    Keep the explicit dependency list small; parity tests guard this contract.
    """

    def __init__(self, canvas: CanvasWidget, document: Document) -> None:
        from gui.app_window import AppWindow

        self.canvas = canvas
        self._document = document
        self._point_position = MethodType(AppWindow._point_position, self)
        self._sync_cable_auto_name = MethodType(AppWindow._sync_cable_auto_name, self)
        self._rebuild_schema_cable_geometry = MethodType(AppWindow._rebuild_schema_cable_geometry, self)
        self.normalize = MethodType(AppWindow._normalize_loaded_cable_bindings, self)
        self.reload_points = MethodType(AppWindow._reload_elec_points_to_canvas, self)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Canvas benchmark: grab CPU time or posted Qt input-to-paint completion. "
        "Neither mode measures display FPS or monitor presentation."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=None,
        help=f"HRP project path (default: {DEFAULT_PROJECT}).",
    )
    parser.add_argument("--last-project", action="store_true", help="Read last_project_path from settings; never write settings.")
    parser.add_argument("--width", type=int, default=1280, help="Widget width in pixels.")
    parser.add_argument("--height", type=int, default=800, help="Widget height in pixels.")
    parser.add_argument("--frames", type=int, default=12, help="Grab frames or event steps (excludes pan press/release).")
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Warm-up grab frames or event steps; excluded from measured samples.",
    )
    parser.add_argument("--mode", choices=("grab", "events"), default="grab")
    parser.add_argument("--platform", choices=("offscreen", "windows"), default="offscreen",
                        help="Qt platform selected before QApplication creation; cannot switch an existing app.")
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), default=None,
                        help="Default: static for grab, pan for events. Events all = pan + zoom.")
    parser.add_argument("--grid", choices=("on", "off", "project"), default="project")
    parser.add_argument("--repeats", type=int, default=1, help="Fresh document/widget per scenario and repetition.")
    parser.add_argument("--interval-ms", type=int, default=16, help="Event issue timer interval (not guaranteed cadence).")
    parser.add_argument("--idle-ms", type=int, default=250, help="Idle wait after final input delivery, before one final async paint probe.")
    parser.add_argument("--deadline-ms", type=int, default=60000,
                        help="Event-loop deadline per measured/warm-up run; cannot preempt a blocking Qt handler.")
    parser.add_argument("--json-output", type=Path, help="Write samples and metadata to a NEW JSON file (opt-in).")
    args = parser.parse_args(argv)
    if args.scenario is None:
        args.scenario = "pan" if args.mode == "events" else "static"
    if args.mode == "events" and args.scenario == "static":
        parser.error("events mode supports pan, zoom or all, not static")
    for name in ("repeats", "interval_ms", "idle_ms", "deadline_ms"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.last_project and args.project is not None:
        parser.error("an explicit project cannot be combined with --last-project")
    if args.project is None and not args.last_project:
        args.project = DEFAULT_PROJECT
    return args


def _get_application(requested_platform: str) -> QApplication:
    app = QApplication.instance()
    if app is not None:
        actual = app.platformName()
        if actual != requested_platform:
            raise ValueError(f"Existing QApplication uses {actual}, not {requested_platform}; start a fresh process")
        return app
    if requested_platform == "windows" and sys.platform != "win32":
        raise ValueError("--platform windows requires Windows")
    # Importing Qt does not select its QPA backend; constructing the app does.
    os.environ["QT_QPA_PLATFORM"] = requested_platform
    return QApplication([])


def _resolve_project(args: argparse.Namespace) -> Path:
    project = args.project
    if args.last_project:
        project = str(layout_store.settings().value("last_project_path", "") or "").strip()
        if not project:
            raise ValueError("No last_project_path is stored; pass an explicit project.")
    path = Path(project).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Project not found: {path}")
    return path


def _load_document(path: Path) -> Document:
    raw = load_raw(path)
    document = Document.from_dict(raw)
    document.source_path = path.resolve()
    return document


def _restore_canvas(widget: CanvasWidget, document: Document) -> list[str]:
    """Restore a fresh canvas via the app's scene-load path, without its shell.

    from_dict is this checkout's state-restore API. Keep the saved reference
    view even when loading a single background triggers automatic fit-to-window.
    Only the in-memory document is modified; no migration saves or backups.
    Project styles are restored by from_dict/set_document and the app's AP
    reload method. Personal overlap/approach/fill settings are deliberately
    NOT loaded: use CanvasWidget defaults, recorded in the scenario metadata.
    """
    from gui.parameter_panel import BUILTIN_SYMBOLS

    adapter = _CanvasRestoreAdapter(widget, document)
    adapter.normalize(document)
    raw = document.to_dict()
    widget.from_dict(raw.get("canvas", {}))
    initial_view = (float(widget._scale), QPointF(widget._offset))
    widget.set_document(document)
    missing: list[str] = []
    base_dir = Path(document.source_path).parent
    for fid, floor in {**document.floorplans, **document.furniture}.items():
        asset = str(floor.file_path or "").strip()
        if not asset:
            continue
        if not is_data_uri(asset):
            path = Path(asset)
            if not path.is_absolute():
                path = base_dir / path
            if not path.is_file():
                missing.append(f"{fid}: background not found")
                continue
            asset = str(path.resolve())
        widget.load_floor_plan_image(fid, asset)
        layer = widget._floor_plans.get(fid)
        if layer is None or (layer.pixmap is None and layer.renderer is None):
            missing.append(f"{fid}: background could not be decoded")

    adapter.reload_points(document)
    for pid, point in document.elements.get("elec_points", {}).items():
        icon = str(point.data.get("icon_path") or "").strip()
        if not icon:
            icon = str(BUILTIN_SYMBOLS.get(str(point.data.get("builtin_symbol") or "").strip(), "") or "")
        if icon and widget._elec_point_icons.get(pid) is None and widget._elec_point_svgs.get(pid) is None:
            missing.append(f"{pid}: icon not found or could not be decoded")

    widget._scale, widget._offset = initial_view
    widget.update()
    return missing


def _sample_stats(samples: list[float]) -> dict:
    """Linear interpolated percentiles at (n-1)*p; defined for n=1 too."""
    if not samples:
        raise ValueError("At least one sample is required")
    ordered = sorted(samples)

    def percentile(p: float) -> float:
        index = (len(ordered) - 1) * p
        lo, hi = math.floor(index), math.ceil(index)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)

    return {
        "count": len(samples),
        "avg_ms": statistics.fmean(samples),
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
    }


def _scenario_view(widget: CanvasWidget, scenario: str, index: int, initial: tuple[float, QPointF]) -> None:
    """Fixed 120-sample cycle, independent of frame count and wall clock.

    Render-only transforms, not synthetic input events or event-handler timings.
    Zoom is anchored at the viewport centre; every cycle starts at initial view.
    """
    scale, offset = initial
    phase = 2.0 * math.pi * (index % 120) / 120.0
    widget._scale = scale
    widget._offset = QPointF(offset)
    if scenario == "pan":
        widget._offset += QPointF(120.0 * math.sin(phase), 80.0 * (1.0 - math.cos(phase)))
    elif scenario == "zoom":
        widget._scale = max(widget._scale_min, min(widget._scale_max, scale * 2.0 ** math.sin(phase)))
        anchor = QPointF(widget.width() / 2.0, widget.height() / 2.0)
        widget._offset = anchor - (anchor - offset) * (widget._scale / scale)
    elif scenario != "static":
        raise ValueError(f"Unknown scenario: {scenario}")


def _scenario_metadata(widget: CanvasWidget, args: argparse.Namespace, scenario: str,
                       initial: tuple[float, QPointF], missing: list[str]) -> dict:
    return {
        "scenario": scenario, "mode": args.mode,
        "warmup": max(0, args.warmup), "viewport": [widget.width(), widget.height()],
        "dpr": widget.devicePixelRatioF(),
        "grid": {"requested": args.grid, "visible": bool(widget._grid_visible)},
        "display_style": {
            "source": "CanvasWidget defaults; no personal settings",
            "cable_overlap_gap_px": widget.elec_cable_overlap_gap_px(),
            "cable_ap_approach_length_px": widget.elec_cable_ap_approach_length_px(),
            "point_fill_alpha": widget.elec_point_fill_alpha(),
        },
        "initial_view": {"scale": initial[0], "offset": [initial[1].x(), initial[1].y()]},
        "asset_warnings": missing,
    }


def _event_metrics(events: dict) -> dict:
    inputs, paints = events["inputs"], events["paints"]
    samples = {name: [row[key] for row in inputs if row[key] is not None] for name, key in (
        ("input_handler", "handler_ms"), ("queue_delay", "queue_delay_ms"),
        ("enqueue_to_paint", "enqueue_to_paint_ms"), ("input_to_paint", "input_to_paint_ms"),
    )}
    # Natural paints only. The explicit idle probe is reported separately.
    samples["paint"] = [row["duration_ms"] for row in paints if row["phase"] == "natural"]
    samples["interactive_paint"] = [row["duration_ms"] for row in paints
                                   if row["phase"] == "natural" and row.get("reduced_grid_quality") is True]
    samples["full_quality_paint"] = [row["duration_ms"] for row in paints
                                    if row["phase"] == "natural" and row.get("reduced_grid_quality") is False]
    return {name: {"stats": _sample_stats(values) if values else None, "samples_ms": values}
            for name, values in samples.items()}


def _run_event_scenario(app: QApplication, project: Path, args: argparse.Namespace, scenario: str) -> dict:
    from benchmark_event_support import EventCanvas, measure_events

    widget = EventCanvas()
    try:
        widget.resize(max(1, args.width), max(1, args.height))
        missing = _restore_canvas(widget, _load_document(project))
        if args.grid != "project":
            widget.set_grid_visible(args.grid == "on")
        initial = (float(widget._scale), QPointF(widget._offset))
        options = {"interval_ms": args.interval_ms, "idle_ms": args.idle_ms,
                   "deadline_ms": args.deadline_ms}
        if args.warmup > 0:
            measure_events(widget, scenario, args.warmup, **options)
            # Reset only between runs, never per input. Warm-up includes its own
            # release/idle barrier, so no pressed button/pending input is carried.
            # A custom idle wait can be shorter than the quality idle timer.
            # Start the measured initial exposure at full quality nevertheless.
            widget._finish_interaction_quality()
            widget._scale, widget._offset = initial[0], QPointF(initial[1])
        events = measure_events(widget, scenario, max(1, args.frames), **options)
        return {
            **_scenario_metadata(widget, args, scenario, initial, missing),
            "events": events, "metrics": _event_metrics(events),
            "final_view": {"scale": float(widget._scale), "offset": [widget._offset.x(), widget._offset.y()]},
            "panning_at_end": bool(widget._panning),
        }
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def _run_scenario(app: QApplication, project: Path, args: argparse.Namespace, scenario: str) -> dict:
    if args.mode == "events":
        return _run_event_scenario(app, project, args, scenario)
    # A fresh document/widget avoids state and cache leakage between scenarios.
    widget = CanvasWidget()
    try:
        widget.resize(max(1, args.width), max(1, args.height))
        missing = _restore_canvas(widget, _load_document(project))
        if args.grid != "project":
            widget.set_grid_visible(args.grid == "on")
        initial = (float(widget._scale), QPointF(widget._offset))
        widget.show()
        app.processEvents()
        for i in range(max(0, args.warmup)):
            _scenario_view(widget, scenario, i, initial)
            widget.grab()
            app.processEvents()

        samples_ms: list[float] = []
        for i in range(max(1, args.frames)):
            _scenario_view(widget, scenario, i, initial)
            started = time.perf_counter()
            widget.grab()
            app.processEvents()
            samples_ms.append((time.perf_counter() - started) * 1000.0)
        return {
            **_scenario_metadata(widget, args, scenario, initial, missing),
            "stats": _sample_stats(samples_ms),
            "samples_ms": samples_ms,
        }
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        project_path = _resolve_project(args)
        if args.json_output and args.json_output.resolve() == project_path:
            raise ValueError("JSON output must not overwrite the project")
        if args.json_output and args.json_output.exists():
            raise ValueError("JSON output already exists; choose a new file")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        app = _get_application(args.platform)
        if args.mode == "events":
            from benchmark_event_support import EVENT_MEASUREMENT, EVENT_SCENARIOS
            available, measurement = EVENT_SCENARIOS, EVENT_MEASUREMENT
        else:
            available, measurement = SCENARIOS, MEASUREMENT
        scenarios = available if args.scenario == "all" else (args.scenario,)
        results = []
        for repeat in range(1, args.repeats + 1):
            for name in scenarios:
                results.append({**_run_scenario(app, project_path, args, name), "repeat": repeat})
        report = {
            "schema_version": 3,
            "mode": args.mode, "repeats": args.repeats, "grid": args.grid,
            "measurement": measurement,
            "scene_profile": SCENE_PROFILE,
            "baseline_note": BASELINE_NOTE,
            "scope": (
                "Render-only transforms (assignment excluded); grab + processEvents wall time. "
                if args.mode == "grab" else
                "Synthetic posted Qt pan/wheel events in a timer-driven QEventLoop. Input-handler, queue, "
                "paint and first-following-paint completion timing; coalesced inputs share paint samples. "
                "Includes pan control events; one separate forced asynchronous idle probe. "
                "Not OS input delivery, whole-window responsiveness or monitor presentation. "
            ) + "App-normalized scene, saved view, no personal display settings or AppWindow construction. No display FPS.",
            "project": str(project_path),
            "environment": {
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "qt": qVersion(),
                "pyside6": PySide6.__version__,
                "os": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "requested_qt_platform": args.platform,
                "qt_platform": app.platformName(),
                "counter": "time.perf_counter_ns" if args.mode == "events" else "time.perf_counter",
                "dpr": results[0]["dpr"],
            },
            "scenarios": results,
        }
        if args.json_output:
            with args.json_output.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, ensure_ascii=False)
        print(f"project={project_path}")
        print(f"measurement={measurement} mode={args.mode} platform={app.platformName()} repeats={args.repeats} grid={args.grid}")
        print(f"scene_profile={SCENE_PROFILE}")
        print(f"baseline_note={BASELINE_NOTE}")
        for result in results:
            prefix = (f"scenario={result['scenario']} repeat={result['repeat']} size={result['viewport']} "
                      f"dpr={result['dpr']} warmup={result['warmup']} asset_warnings={len(result['asset_warnings'])}")
            if args.mode == "grab":
                stats = result["stats"]
                print(f"{prefix} frames={stats['count']} avg_ms={stats['avg_ms']:.2f} "
                      f"p50_ms={stats['p50_ms']:.2f} p95_ms={stats['p95_ms']:.2f} "
                      f"p99_ms={stats['p99_ms']:.2f} max_ms={stats['max_ms']:.2f}")
            else:
                counts = result["events"]["counters"]
                summaries = []
                for name, metric in result["metrics"].items():
                    stats = metric["stats"]
                    summaries.append(f"{name}_p95_ms={stats['p95_ms']:.2f}" if stats else f"{name}=no_samples")
                print(f"{prefix} posted={counts['posted']} dispatched={counts['dispatched']} "
                      f"paints={counts['paint_events']} coalesced={counts['coalesced_paints']} " + " ".join(summaries))
        return 0
    except Exception as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
