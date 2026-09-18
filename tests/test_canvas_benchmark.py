"""Isolated benchmark tests: no AppWindow construction or real project."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QSettings, QTimer
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication

import benchmark_canvas_perf as bench
import benchmark_event_support as event_bench
from gui.app_window import AppWindow
from model.elements import ElecCable
from storage.asset_data_uri import encode_file_to_data_uri


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setFallbacksEnabled(False)
    monkeypatch.setattr(bench.layout_store, "settings", lambda: store)
    yield store


@pytest.fixture(autouse=True)
def forbid_window_workflows(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Benchmark must not construct/load a window or access its settings")

    for name in ("__init__", "_settings", "_restore_layout", "_load_display_settings",
                 "_auto_load_last_project", "open_project_file", "_set_document"):
        monkeypatch.setattr(AppWindow, name, forbidden)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project.hrp"
    path.write_text(json.dumps({
        "canvas": {"view_scale": 1.5, "view_offset": [17, 29], "grid_visible": False},
        "params": {},
    }), encoding="utf-8")
    return path


def _restore_app_reference(canvas, document):
    """Only the known scene-load calls, on an independent non-QWidget host.

    Deliberately do not use the benchmark adapter or the full shell loader:
    that would either hide restore-order bugs or require settings/dock stubs.
    """
    host = SimpleNamespace(_document=document, canvas=canvas,
                           _project_path=document.source_path)
    host._point_position = MethodType(AppWindow._point_position, host)
    host._sync_cable_auto_name = MethodType(AppWindow._sync_cable_auto_name, host)
    host._rebuild_schema_cable_geometry = MethodType(AppWindow._rebuild_schema_cable_geometry, host)
    MethodType(AppWindow._normalize_loaded_cable_bindings, host)(document)
    canvas.from_dict(document.to_dict().get("canvas", {}))
    initial = (canvas._scale, QPointF(canvas._offset))
    canvas.set_document(document)
    MethodType(AppWindow._load_floor_plan_images, host)(document)
    MethodType(AppWindow._reload_elec_points_to_canvas, host)(document)
    # The benchmark intentionally preserves the saved view over image auto-fit.
    canvas._scale, canvas._offset = initial


@pytest.mark.parametrize("bindings,route,expected_bindings,expected_route", [
    ((" AP-1 ", "AP-2", "AP-2", "AP-1"), [[1, 2], [150, 80], [3, 4]],
     ("AP-1", "AP-2"), [[40, 50], [150, 80], [240, 150]]),
    (("", "", "AP-1", " AP-2 "), [],
     ("AP-1", "AP-2"), [[40, 50], [240, 150]]),
    (("AP-999", "AP-2", "AP-1", "AP-1"), [[1, 2], [3, 4]],
     ("", "AP-2"), [[1, 2], [240, 150]]),
    (("AP-1", "AP-999", "", "AP-2"), [],
     ("AP-1", ""), [[40, 50], [160, 90]]),
    (("", "AP-2", "", ""), [],
     ("", "AP-2"), [[120, 110], [240, 150]]),
    (("AP-1", "", "", ""), [[9, 8]],
     ("AP-1", ""), [[40, 50], [9, 8]]),
    (("", "AP-2", "", ""), [[9, 8]],
     ("", "AP-2"), [[9, 8], [240, 150]]),
    (("AP-1", "AP-2", "", ""), [[9, 8]],
     ("AP-1", "AP-2"), [[40, 50], [240, 150]]),
    (("", "", "AP-999", "AP-999"), [["1", "2"], ["bad", 5], [8], [3, 4]],
     ("", ""), [[1, 2], [3, 4]]),
    (("", "", "", ""), [], ("", ""), []),
])
def test_restore_matches_app_normalization(app, project, monkeypatch, bindings, route,
                                          expected_bindings, expected_route):
    def forbidden():
        pytest.fail("Explicit restore must not read settings")
    monkeypatch.setattr(bench.layout_store, "settings", forbidden)
    start, end, geom_start, geom_end = bindings
    raw = {
        "canvas": {
            "view_scale": 1.25, "view_offset": [17, 29], "grid_visible": False,
            "elec_points": {"AP-1": [40, 50], "AP-2": [240, 150]},
            "elec_cables": {"EK-1": route},
            "cable_start_ap": {"EK-1": geom_start}, "cable_end_ap": {"EK-1": geom_end},
        },
        "params": {
            "elec_points": {"AP-1": {"name": "Source"}, "AP-2": {"name": "Target"}},
            "elec_cables": {"EK-1": {"name": "STALE", "start_ap": start, "end_ap": end,
                                      "visible": True, "label_visible": True}},
        },
    }
    project.write_text(json.dumps(raw), encoding="utf-8")
    before = project.read_bytes()
    files_before = set(project.parent.iterdir())
    document = bench._load_document(project)
    reference_document = bench._load_document(project)
    canvas, reference = bench.CanvasWidget(), bench.CanvasWidget()
    try:
        canvas.resize(640, 480)
        reference.resize(640, 480)
        # Poison only the canvas document pointer: normalization must use the
        # NEW document for AP names/coordinates. Keep fresh, unbound maps as in
        # _run_scenario; from_dict on old bound views writes into the old doc.
        previous = bench.Document.from_dict({
            "canvas": {"elec_points": {"AP-1": [900, 901]}},
            "params": {"elec_points": {"AP-1": {"name": "Wrong document"}}},
        })
        canvas._document = previous
        previous_before = previous.to_dict()
        assert bench._restore_canvas(canvas, document) == []
        _restore_app_reference(reference, reference_document)
        assert document.to_dict() == reference_document.to_dict()
        assert previous.to_dict() == previous_before
        assert canvas.document() is document
        cable = document.elements["elec_cables"]["EK-1"]
        assert isinstance(cable, ElecCable)
        assert (cable.start_ap, cable.end_ap) == expected_bindings
        assert (canvas._cable_start_ap["EK-1"], canvas._cable_end_ap["EK-1"]) == expected_bindings
        assert cable.geom["elec_cables"] == expected_route
        assert [(p.x(), p.y()) for p in canvas._elec_cables["EK-1"]] == [tuple(p) for p in expected_route]
        assert canvas._label_map["EK-1"] == reference._label_map["EK-1"] == cable.name
        assert cable.name != "STALE" and "Wrong document" not in cable.name
        if expected_bindings == ("AP-1", "AP-2"):
            assert "Source" in cable.name and "Target" in cable.name
        assert canvas._elec_visible["EK-1"] is True
        assert canvas._label_visible["EK-1"] is True
        # Compare the actual visible scene, not just mirrored binding IDs.
        actual_image = canvas.grab().toImage()
        assert not actual_image.isNull()
        assert actual_image == reference.grab().toImage()
        assert project.read_bytes() == before
        assert set(project.parent.iterdir()) == files_before
    finally:
        canvas.deleteLater()
        reference.deleteLater()
        app.processEvents()


def test_restore_full_project_style_matches_app(app, project):
    raw = {
        "canvas": {
            "view_scale": 1.4, "view_offset": [31, 47], "mm_per_px": 2,
            "bg_color": "#102030", "grid_visible": True, "grid_spacing_mm": 50,
            "grid_color": [100, 120, 140, 80], "snap_angle": 45,
            "measure_color": "#654321", "helper_line_color": "#abcdef",
            "elec_points": {"AP-1": [40, 50], "AP-2": [240, 150], "AP-3": [60, 180]},
            "elec_point_size_px": {"AP-1": [999, 999]},
            "elec_cables": {"EK-1": [[1, 2], [150, 80], [3, 4]]},
            "elec_cable_stroke_width": {"EK-1": 4.5},
            "elec_cable_line_style": {"EK-1": "dashdot"},
            "elec_cable_type_text": {"EK-1": "NYM-J 3x1.5"},
            "elec_cable_type_label_visible": {"EK-1": True},
            "label_positions": {"EK-1": [130, 60]},
        },
        "params": {
            "elec_points": {
                "AP-1": {"name": "Source", "width": 40, "height": 20, "color": " #123456 "},
                "AP-2": {"name": "Target", "width": "invalid", "height": 80, "color": "#abcdef"},
                "AP-3": {"width": 0, "height": None, "color": "", "visible": False},
            },
            "elec_cables": {"EK-1": {"start_ap": "AP-1", "end_ap": "AP-2",
                                      "label_size": 17, "label_visible": True, "visible": True,
                                      "color": "#ff1234", "stroke_width": 9, "line_style": "dot"}},
        },
    }
    project.write_text(json.dumps(raw), encoding="utf-8")
    before = project.read_bytes()
    canvas, reference = bench.CanvasWidget(), bench.CanvasWidget()
    try:
        document, expected = bench._load_document(project), bench._load_document(project)
        assert bench._restore_canvas(canvas, document) == []
        _restore_app_reference(reference, expected)
        assert document.to_dict() == expected.to_dict()
        # Includes conflicting params/geometry styles: follow the actual app
        # load path, not an invented preference for params or per-type styles.
        for attr in ("_color_map", "_label_map", "_elec_point_size_px", "_label_positions",
                     "_label_font_sizes", "_label_visible", "_elec_visible",
                     "_elec_cable_stroke_width", "_elec_cable_line_style",
                     "_elec_cable_type_text", "_elec_cable_type_label_visible"):
            assert dict(getattr(canvas, attr)) == dict(getattr(reference, attr)), attr
        for attr in ("_scale", "_offset", "_mm_per_px", "_bg_color", "_grid_visible",
                     "_grid_spacing_mm", "_grid_color", "_snap_angle", "_measure_color",
                     "_helper_line_color", "_elec_cable_overlap_gap_px",
                     "_elec_cable_ap_approach_length_px", "_elec_point_fill_alpha"):
            assert getattr(canvas, attr) == getattr(reference, attr), attr
        assert canvas._color_map["AP-1"] == QColor("#123456")
        assert canvas._color_map["AP-3"] == QColor("#4fc3f7")
        assert canvas._elec_point_size_px["AP-1"] == (20, 10)
        assert canvas._elec_point_size_px["AP-2"] == (15, 15)
        assert canvas._elec_point_size_px["AP-3"] == (15, 15)
        assert canvas._elec_cable_stroke_width["EK-1"] == 4.5
        # load_raw migration mirrors the params line style, but leaves the
        # existing canvas stroke width intact (same as application loading).
        assert canvas._elec_cable_line_style["EK-1"] == "dot"
        assert canvas._label_font_sizes["EK-1"] == 17
        assert canvas._elec_visible["AP-3"] is False
        assert canvas.grab().toImage() == reference.grab().toImage()
        assert project.read_bytes() == before
        assert not project.with_suffix(".hrp.bak").exists()
    finally:
        canvas.deleteLater()
        reference.deleteLater()
        app.processEvents()


def test_cli_preserves_defaults_and_positional():
    args = bench._parse_args([])
    assert (args.project, args.width, args.height, args.frames, args.warmup) == (
        bench.DEFAULT_PROJECT, 1280, 800, 12, 3)
    assert args.scenario == "static" and args.json_output is None
    assert (args.mode, args.platform, args.grid, args.repeats) == ("grab", "offscreen", "project", 1)
    args = bench._parse_args(["custom.hrp", "--frames", "300", "--warmup", "20", "--scenario", "all"])
    assert (args.project, args.frames, args.warmup, args.scenario) == ("custom.hrp", 300, 20, "all")


def test_cli_last_project_conflict_even_with_explicit_default():
    with pytest.raises(SystemExit) as exc:
        bench._parse_args([bench.DEFAULT_PROJECT, "--last-project"])
    assert exc.value.code == 2


def test_last_project_read_only(project, isolated_settings):
    isolated_settings.setValue("last_project_path", str(project))
    isolated_settings.setValue("sentinel", "unchanged")
    isolated_settings.sync()
    before = Path(isolated_settings.fileName()).read_bytes()
    assert bench._resolve_project(bench._parse_args(["--last-project"])) == project
    isolated_settings.sync()
    assert Path(isolated_settings.fileName()).read_bytes() == before


@pytest.mark.parametrize("value", ["", "does-not-exist.hrp"])
def test_last_project_missing_has_no_example_fallback(value, isolated_settings):
    isolated_settings.setValue("last_project_path", value)
    with pytest.raises(ValueError):
        bench._resolve_project(bench._parse_args(["--last-project"]))


def test_explicit_project_does_not_read_settings(project, monkeypatch):
    def forbidden():
        pytest.fail("Explicit project must not access settings")
    monkeypatch.setattr(bench.layout_store, "settings", forbidden)
    assert bench._resolve_project(bench._parse_args([str(project)])) == project


def test_stats_interpolation_and_single_sample():
    stats = bench._sample_stats([40, 10, 30, 20])
    assert stats == pytest.approx({"count": 4, "avg_ms": 25, "min_ms": 10, "max_ms": 40,
                                  "p50_ms": 25, "p95_ms": 38.5, "p99_ms": 39.7})
    assert bench._sample_stats([7])["p99_ms"] == 7
    with pytest.raises(ValueError):
        bench._sample_stats([])


@pytest.mark.parametrize("embedded", [False, True])
def test_assets_restore_from_project_not_cwd(app, tmp_path, monkeypatch, embedded):
    image_path = tmp_path / "asset.png"
    image = QImage(16, 12, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    assert image.save(str(image_path))
    asset = encode_file_to_data_uri(image_path) if embedded else "asset.png"
    path = tmp_path / "assets.hrp"
    path.write_text(json.dumps({
        "canvas": {"view_scale": 1.75, "view_offset": [23, 41], "mm_per_px": 2,
                   "floor_plans": [{"fp_id": "grundriss-1", "mm_per_px": 2}],
                   "elec_points": {"AP-1": [10, 10]}},
        "params": {"floorplans": {"grundriss-1": {"fp_id": "grundriss-1", "file_path": asset}},
                   "elec_points": {"AP-1": {"point_id": "AP-1", "icon_path": asset,
                                             "width": 40, "height": 20, "color": "#123456"}}},
    }), encoding="utf-8")
    before = path.read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    canvas = bench.CanvasWidget()
    try:
        doc = bench._load_document(path)
        assert doc.source_path == path
        assert bench._restore_canvas(canvas, doc) == []
        layer = canvas._floor_plans["grundriss-1"]
        assert layer.pixmap is not None and not layer.pixmap.isNull()
        assert canvas._get_cached_pixmap(asset) is not None
        assert canvas._elec_point_icons["AP-1"] is not None
        assert canvas._elec_point_size_px["AP-1"] == (20, 10)
        assert canvas._scale == 1.75 and canvas._offset == QPointF(23, 41)
        assert path.read_bytes() == before
        assert not path.with_suffix(".hrp.bak").exists()
    finally:
        canvas.deleteLater()
        app.processEvents()


def test_scenario_transforms_deterministic_and_zoom_anchor(app):
    canvas = bench.CanvasWidget()
    canvas.resize(640, 480)
    initial = (1.5, QPointF(17, 29))
    try:
        for scenario in bench.SCENARIOS:
            bench._scenario_view(canvas, scenario, 0, initial)
            assert (canvas._scale, canvas._offset) == initial
            bench._scenario_view(canvas, scenario, 30, initial)
            view = (canvas._scale, QPointF(canvas._offset))
            bench._scenario_view(canvas, scenario, 150, initial)
            assert (canvas._scale, canvas._offset) == view
        anchor = QPointF(canvas.width() / 2, canvas.height() / 2)
        assert (anchor - canvas._offset) / canvas._scale == (anchor - initial[1]) / initial[0]
    finally:
        canvas.deleteLater()
        app.processEvents()


def test_svg_furniture_and_builtin_icons(app, project):
    svg = project.parent / "furniture.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30">'
                   '<rect width="40" height="30" fill="red"/></svg>', encoding="utf-8")
    raw = json.loads(project.read_text(encoding="utf-8"))
    raw["canvas"]["floor_plans"] = [{"fp_id": "einrichtung-1"}]
    raw["canvas"]["elec_points"] = {"AP-1": [10, 10], "AP-2": [20, 20]}
    raw["params"] = {
        "furniture": {"einrichtung-1": {"file_path": "furniture.svg"}},
        "elec_points": {
            "AP-1": {"builtin_symbol": "Steckdose"},
            "AP-2": {"icon_path": encode_file_to_data_uri(svg)},
        },
    }
    project.write_text(json.dumps(raw), encoding="utf-8")
    canvas = bench.CanvasWidget()
    try:
        assert bench._restore_canvas(canvas, bench._load_document(project)) == []
        assert canvas._floor_plans["einrichtung-1"].renderer is not None
        assert canvas._elec_point_icons.get("AP-1") is not None or canvas._elec_point_svgs.get("AP-1") is not None
        assert canvas._elec_point_svgs["AP-2"] is not None
    finally:
        canvas.deleteLater()
        app.processEvents()


def test_missing_assets_reported_without_dumping_paths(app, project):
    raw = json.loads(project.read_text(encoding="utf-8"))
    raw["canvas"]["floor_plans"] = [{"fp_id": "grundriss-1"}]
    raw["canvas"]["elec_points"] = {"AP-1": [10, 10]}
    raw["params"] = {
        "floorplans": {"grundriss-1": {"file_path": "missing.png"}},
        "elec_points": {"AP-1": {"icon_path": "missing.png"}},
    }
    project.write_text(json.dumps(raw), encoding="utf-8")
    canvas = bench.CanvasWidget()
    try:
        warnings = bench._restore_canvas(canvas, bench._load_document(project))
        assert len(warnings) == 2
        assert warnings[0].startswith("grundriss-1:")
        assert warnings[1].startswith("AP-1:")
        assert all(str(project.parent) not in warning for warning in warnings)
    finally:
        canvas.deleteLater()
        app.processEvents()


def test_all_scenarios_json_and_read_only_project(app, project, tmp_path, capsys, isolated_settings):
    output = tmp_path / "measurements.json"
    before = project.read_bytes()
    assert bench.main([str(project), "--frames", "2", "--warmup", "1", "--scenario", "all",
                       "--json-output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == 3
    assert report["measurement"] == bench.MEASUREMENT
    assert report["scene_profile"] == bench.SCENE_PROFILE
    assert report["baseline_note"] == bench.BASELINE_NOTE
    assert "not directly comparable" in report["baseline_note"]
    assert report["environment"]["python"] and report["environment"]["qt"]
    assert report["mode"] == "grab" and report["repeats"] == 1
    assert report["environment"]["requested_qt_platform"] == report["environment"]["qt_platform"] == "offscreen"
    assert report["environment"]["counter"] == "time.perf_counter"
    results = report["scenarios"]
    assert [r["scenario"] for r in results] == list(bench.SCENARIOS)
    for result in results:
        assert result["initial_view"] == {"scale": 1.5, "offset": [17, 29]}
        assert len(result["samples_ms"]) == result["stats"]["count"] == 2
        assert result["dpr"] > 0 and result["asset_warnings"] == []
        assert result["display_style"]["source"] == "CanvasWidget defaults; no personal settings"
        assert result["display_style"]["cable_ap_approach_length_px"] == 12.0
        assert result["display_style"]["point_fill_alpha"] == 60
    assert project.read_bytes() == before
    assert isolated_settings.allKeys() == []
    stdout = capsys.readouterr().out
    assert f"scene_profile={bench.SCENE_PROFILE}" in stdout
    assert f"baseline_note={bench.BASELINE_NOTE}" in stdout
    assert len(stdout.splitlines()) == 7
    saved_output = output.read_bytes()
    assert bench.main([str(project), "--json-output", str(output)]) == 2
    assert output.read_bytes() == saved_output


def test_no_output_by_default_and_no_overwrite(app, project, tmp_path):
    before = set(tmp_path.iterdir())
    assert bench.main([str(project), "--frames", "1", "--warmup", "0"]) == 0
    assert set(tmp_path.iterdir()) == before
    content = project.read_bytes()
    assert bench.main([str(project), "--json-output", str(project)]) == 2
    assert project.read_bytes() == content


def test_event_cli_defaults_and_options():
    args = bench._parse_args(["--mode", "events"])
    assert (args.scenario, args.platform, args.interval_ms, args.idle_ms, args.deadline_ms) == (
        "pan", "offscreen", 16, 250, 60000)
    args = bench._parse_args(["--mode", "events", "--platform", "windows", "--scenario", "all",
                             "--repeats", "5", "--grid", "off", "--interval-ms", "2"])
    assert (args.mode, args.platform, args.scenario, args.repeats, args.grid, args.interval_ms) == (
        "events", "windows", "all", 5, "off", 2)


@pytest.mark.parametrize("options", [
    ["--mode", "events", "--scenario", "static"], ["--platform", "invalid"],
    ["--repeats", "0"], ["--interval-ms", "0"], ["--idle-ms", "-1"], ["--deadline-ms", "0"],
])
def test_invalid_event_options(options):
    with pytest.raises(SystemExit) as exc:
        bench._parse_args(options)
    assert exc.value.code == 2


@pytest.mark.parametrize("requested", ["windows", "offscreen"])
def test_platform_selected_before_application_construction(monkeypatch, requested):
    # Fake only the app factory: do not create/switch the process's real Qt app.
    monkeypatch.setenv("QT_QPA_PLATFORM", "sentinel")
    monkeypatch.setattr(bench.sys, "platform", "win32")
    calls = []
    sentinel = object()

    class Factory:
        @staticmethod
        def instance():
            return None

        def __new__(cls, argv):
            calls.append((os.environ["QT_QPA_PLATFORM"], argv))
            return sentinel

    monkeypatch.setattr(bench, "QApplication", Factory)
    assert bench._get_application(requested) is sentinel
    assert calls == [(requested, [])]


def test_existing_platform_cannot_be_changed(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "untouched")
    existing = SimpleNamespace(platformName=lambda: "offscreen")
    monkeypatch.setattr(bench, "QApplication", SimpleNamespace(instance=lambda: existing))
    assert bench._get_application("offscreen") is existing
    with pytest.raises(ValueError, match="fresh process"):
        bench._get_application("windows")
    assert os.environ["QT_QPA_PLATFORM"] == "untouched"


def test_event_targets_and_zoom_cycle_are_deterministic():
    anchor = QPointF(320, 240)
    assert event_bench._pan_target(anchor, 119) == anchor
    assert event_bench._pan_target(anchor, 4) == event_bench._pan_target(anchor, 124)
    deltas = [event_bench._wheel_delta(i) for i in range(120)]
    assert deltas == ([120] * 10 + [-120] * 20 + [120] * 10) * 3
    assert sum(deltas) == 0
    assert deltas == [event_bench._wheel_delta(i) for i in range(120, 240)]


def _assert_event_samples(result, frames):
    events = result["events"]
    rows, paints, counts = events["inputs"], events["paints"], events["counters"]
    expected = frames + (2 if result["scenario"] == "pan" else 0)
    assert counts["posted"] == counts["dispatched"] == counts["inputs_with_paint"] == expected
    assert counts["undispatched"] == counts["inputs_without_paint"] == 0
    assert counts["step_events_posted"] == frames
    assert counts["paint_events"] == len(paints)
    assert counts["natural_paint_events"] >= 1
    assert events["status"] == "done" and events["initial_paint_ms"] >= 0
    assert not result["panning_at_end"]
    assert result["mode"] == "events" and "stats" not in result
    assert events["counter"] == "time.perf_counter_ns"
    assert [row["id"] for row in rows] == list(range(1, expected + 1))
    assert sorted(seq for paint in paints for seq in paint["input_ids"]) == list(range(1, expected + 1))
    by_id = {paint["id"]: paint for paint in paints}
    for row in rows:
        paint = by_id[row["paint_id"]]
        assert row["enqueue_ms"] <= row["dispatch_ms"] <= row["handler_end_ms"] <= paint["start_ms"] <= paint["end_ms"]
        assert row["handler_ms"] >= 0
        assert row["queue_delay_ms"] == pytest.approx(row["dispatch_ms"] - row["enqueue_ms"])
        assert row["enqueue_to_paint_ms"] == pytest.approx(paint["end_ms"] - row["enqueue_ms"])
        assert row["input_to_paint_ms"] == pytest.approx(paint["end_ms"] - row["dispatch_ms"])
        assert row["enqueue_to_paint_ms"] >= row["input_to_paint_ms"]
        assert row["id"] in paint["input_ids"]
        assert paint["phase"] == "natural"  # The forced final probe must not rescue missing paints.
    probe = events["idle_probe"]
    assert probe["forced_async_update"] is True
    assert probe["requested_ms"] >= rows[-1]["handler_end_ms"] + events["idle_ms"] - 1
    idle_paint = by_id[probe["paint_id"]]
    assert idle_paint["phase"] == "idle_probe" and idle_paint["input_ids"] == []
    assert idle_paint["start_ms"] >= probe["requested_ms"]
    assert probe["completion_ms"] == idle_paint["end_ms"]
    assert probe["last_input_to_completion_ms"] == pytest.approx(idle_paint["end_ms"] - rows[-1]["dispatch_ms"])
    assert probe["last_enqueue_to_completion_ms"] == pytest.approx(idle_paint["end_ms"] - rows[-1]["enqueue_ms"])
    for name in ("input_handler", "queue_delay", "enqueue_to_paint", "input_to_paint"):
        assert result["metrics"][name]["stats"]["count"] == expected
    assert result["metrics"]["paint"]["stats"]["count"] == counts["natural_paint_events"]
    assert counts["extra_inputs_sharing_paint"] == sum(max(0, len(p["input_ids"]) - 1) for p in paints)


def test_event_scenarios_json_repeats_read_only_and_no_forced_per_input_render(
        app, project, tmp_path, monkeypatch, capsys, isolated_settings):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Event mode must not grab/repaint or access personal settings")

    monkeypatch.setattr(bench.CanvasWidget, "grab", forbidden)
    monkeypatch.setattr(bench.CanvasWidget, "repaint", forbidden)
    monkeypatch.setattr(bench.layout_store, "settings", forbidden)
    output = tmp_path / "events.json"
    before, files_before = project.read_bytes(), set(tmp_path.iterdir())
    assert bench.main([str(project), "--mode", "events", "--scenario", "all", "--frames", "8",
                       "--warmup", "2", "--interval-ms", "1", "--idle-ms", "20", "--deadline-ms", "5000",
                       "--repeats", "2", "--grid", "off", "--json-output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == 3 and report["mode"] == "events"
    assert report["measurement"] == event_bench.EVENT_MEASUREMENT != bench.MEASUREMENT
    assert report["scene_profile"] == bench.SCENE_PROFILE and report["baseline_note"] == bench.BASELINE_NOTE
    assert report["repeats"] == 2 and report["grid"] == "off"
    assert report["environment"]["counter"] == "time.perf_counter_ns"
    assert report["environment"]["qt_platform"] == "offscreen"
    assert [(r["repeat"], r["scenario"]) for r in report["scenarios"]] == [
        (1, "pan"), (1, "zoom"), (2, "pan"), (2, "zoom")]
    for result in report["scenarios"]:
        _assert_event_samples(result, 8)
        assert result["initial_view"] == {"scale": 1.5, "offset": [17, 29]}
        assert result["warmup"] == 2 and result["asset_warnings"] == []
        assert result["grid"] == {"requested": "off", "visible": False}
        anchor = QPointF(result["viewport"][0] / 2, result["viewport"][1] / 2)
        if result["scenario"] == "pan":
            expected_offset = QPointF(17, 29) + event_bench._pan_target(anchor, 7) - anchor
            assert result["final_view"]["scale"] == 1.5
            assert [r["role"] for r in result["events"]["inputs"]] == ["begin"] + ["step"] * 8 + ["end"]
            assert result["events"]["inputs"][0]["type"] == "MouseButtonPress"
            assert result["events"]["inputs"][-1]["type"] == "MouseButtonRelease"
        else:
            expected_scale = 1.5 * 1.15 ** 8
            assert result["final_view"]["scale"] == pytest.approx(expected_scale)
            expected_offset = anchor - (anchor - QPointF(17, 29)) * (expected_scale / 1.5)
            assert all(row["type"] == "Wheel" for row in result["events"]["inputs"])
        assert result["final_view"]["offset"] == pytest.approx([expected_offset.x(), expected_offset.y()])
    assert project.read_bytes() == before
    assert set(tmp_path.iterdir()) == files_before | {output}
    assert isolated_settings.allKeys() == []
    assert "mode=events platform=offscreen" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["grab", "events"])
@pytest.mark.parametrize("grid,expected", [("project", False), ("off", False), ("on", True)])
def test_grid_ablation_in_memory_only(app, project, mode, grid, expected):
    before = project.read_bytes()
    args = bench._parse_args([str(project), "--mode", mode, "--grid", grid, "--frames", "8",
                              "--warmup", "0", "--interval-ms", "1", "--idle-ms", "20",
                              "--deadline-ms", "5000"])
    result = bench._run_scenario(app, project, args, args.scenario)
    assert result["grid"] == {"requested": grid, "visible": expected}
    assert project.read_bytes() == before
    assert not project.with_suffix(".hrp.bak").exists()


def test_coalesced_inputs_share_one_following_paint(app, project, monkeypatch):
    original_issue = event_bench._EventRun.issue

    def burst(self):
        # One timeout posts the entire batch before Qt can process its updates.
        for _ in range(self.frames):
            original_issue(self)

    monkeypatch.setattr(event_bench._EventRun, "issue", burst)
    args = bench._parse_args([str(project), "--mode", "events", "--scenario", "zoom", "--frames", "8",
                              "--warmup", "0", "--interval-ms", "1", "--idle-ms", "20",
                              "--deadline-ms", "5000"])
    result = bench._run_scenario(app, project, args, "zoom")
    _assert_event_samples(result, 8)
    shared = [p for p in result["events"]["paints"] if len(p["input_ids"]) > 1]
    assert shared and result["events"]["counters"]["coalesced_paints"] == len(shared)
    for paint in shared:
        # Each input gets its own latency origin, not only the most recent one.
        rows = [r for r in result["events"]["inputs"] if r["id"] in paint["input_ids"]]
        assert len({r["paint_id"] for r in rows}) == 1
        assert rows[0]["enqueue_to_paint_ms"] >= rows[-1]["enqueue_to_paint_ms"]


def test_no_paint_deadline_and_timer_cleanup(app):
    canvas = event_bench.EventCanvas()
    canvas.setUpdatesEnabled(False)
    runner = event_bench._EventRun(canvas, "pan", 8, 1, 20, 60)
    try:
        with pytest.raises(event_bench.EventDeadlineError) as exc:
            runner.run()
        assert exc.value.report["status"] == "timed_out"
        assert exc.value.report["counters"]["posted"] == 0
        assert exc.value.report["counters"]["paint_events"] == 0
        assert canvas._benchmark_run is None and not canvas._panning
        assert all(not timer.isActive() for timer in runner.findChildren(QTimer))
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def test_missing_input_paint_not_rescued_by_idle_probe(app, monkeypatch):
    def silent_wheel(self, event):
        self._scale *= 1.15
        # Deterministically suppress all paints after initial exposure.
        self.setUpdatesEnabled(False)

    monkeypatch.setattr(bench.CanvasWidget, "wheelEvent", silent_wheel)
    canvas = event_bench.EventCanvas()
    runner = event_bench._EventRun(canvas, "zoom", 8, 1, 10, 1000)
    try:
        with pytest.raises(event_bench.EventDeadlineError) as exc:
            runner.run()
        counts = exc.value.report["counters"]
        assert counts["posted"] == counts["dispatched"] == 8
        assert counts["inputs_without_paint"] == 8
        assert exc.value.report["idle_probe"]["requested_ms"] is None
        assert canvas._benchmark_run is None
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("method", ["mouseMoveEvent", "paintEvent"])
def test_qt_callback_exceptions_propagate_and_cleanup(app, monkeypatch, method):
    error = RuntimeError("injected callback failure")

    def fail(*_args):
        raise error

    monkeypatch.setattr(bench.CanvasWidget, method, fail)
    canvas = event_bench.EventCanvas()
    runner = event_bench._EventRun(canvas, "pan", 8, 1, 20, 5000)
    try:
        with pytest.raises(RuntimeError) as exc:
            runner.run()
        assert exc.value is error
        assert canvas._benchmark_run is None and not canvas._panning
        assert all(not timer.isActive() for timer in runner.findChildren(QTimer))
    finally:
        # Do not leak the throwing base virtual into cleanup's processEvents.
        monkeypatch.undo()
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def test_timer_callback_exception_propagates(app, monkeypatch):
    error = RuntimeError("injected timer failure")

    def fail(_self):
        raise error

    monkeypatch.setattr(event_bench._EventRun, "issue", fail)
    canvas = event_bench.EventCanvas()
    try:
        with pytest.raises(RuntimeError) as exc:
            event_bench.measure_events(canvas, "zoom", 8, interval_ms=1, idle_ms=20, deadline_ms=5000)
        assert exc.value is error and canvas._benchmark_run is None
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def test_event_loading_normalizes_without_mutating_project_or_scene(app, project, monkeypatch):
    project.write_text(json.dumps({
        "canvas": {"view_scale": 1.5, "view_offset": [17, 29], "grid_visible": False,
                   "elec_points": {"AP-1": [40, 50], "AP-2": [240, 150]},
                   "elec_cables": {"EK-1": [[1, 2], [120, 80], [3, 4]]}},
        "params": {"elec_points": {"AP-1": {"name": "Source"}, "AP-2": {"name": "Target"}},
                   "elec_cables": {"EK-1": {"name": "STALE", "start_ap": " AP-1 ", "end_ap": "AP-2"}}},
    }), encoding="utf-8")
    before = project.read_bytes()
    files_before = set(project.parent.iterdir())
    restored = []
    original_restore = bench._restore_canvas

    def restore(widget, document):
        warnings = original_restore(widget, document)
        cable = document.elements["elec_cables"]["EK-1"]
        assert (cable.start_ap, cable.end_ap) == ("AP-1", "AP-2")
        assert cable.geom["elec_cables"] == [[40, 50], [120, 80], [240, 150]]
        assert "Source" in cable.name and "Target" in cable.name
        restored.append((document, document.to_dict()))
        return warnings

    monkeypatch.setattr(bench, "_restore_canvas", restore)
    args = bench._parse_args([str(project), "--mode", "events", "--frames", "8", "--warmup", "0",
                             "--interval-ms", "1", "--idle-ms", "20", "--deadline-ms", "5000"])
    for scenario in event_bench.EVENT_SCENARIOS:
        _assert_event_samples(bench._run_scenario(app, project, args, scenario), 8)
    assert len(restored) == 2 and restored[0][0] is not restored[1][0]
    for document, snapshot in restored:
        assert document.to_dict() == snapshot
    assert project.read_bytes() == before and set(project.parent.iterdir()) == files_before


def test_empty_event_metrics_have_no_fabricated_samples():
    metrics = bench._event_metrics({"inputs": [], "paints": []})
    assert all(metric == {"stats": None, "samples_ms": []} for metric in metrics.values())


def test_short_idle_warmup_does_not_leak_quality_state(app, project, monkeypatch):
    original = event_bench.measure_events
    initial_states = []

    def measure(widget, *args, **kwargs):
        initial_states.append((widget._interactive_quality, widget._quality_idle_timer.isActive()))
        result = original(widget, *args, **kwargs)
        # Deterministic warmup residual, independent of machine speed.
        if len(initial_states) == 1:
            widget._begin_interaction_quality()
        return result

    monkeypatch.setattr(event_bench, "measure_events", measure)
    args = bench._parse_args([str(project), "--mode", "events", "--scenario", "zoom", "--frames", "2",
                             "--warmup", "1", "--interval-ms", "1", "--idle-ms", "5", "--deadline-ms", "5000"])
    bench._run_scenario(app, project, args, "zoom")
    assert initial_states == [(False, False), (False, False)]


def test_quality_metrics_exclude_idle_probe_and_unknown_quality():
    metrics = bench._event_metrics({"inputs": [], "paints": [
        {"duration_ms": 11, "phase": "natural", "reduced_grid_quality": True},
        {"duration_ms": 23, "phase": "natural", "reduced_grid_quality": False},
        {"duration_ms": 31, "phase": "natural", "reduced_grid_quality": None},
        {"duration_ms": 100, "phase": "idle_probe", "reduced_grid_quality": False},
    ]})
    assert metrics["paint"]["samples_ms"] == [11, 23, 31]
    assert metrics["interactive_paint"]["samples_ms"] == [11]
    assert metrics["full_quality_paint"]["samples_ms"] == [23]


@pytest.mark.parametrize("final_quality", [False, True, None])
def test_refinement_report_requires_natural_full_quality_after_last_input(app, final_quality):
    canvas = event_bench.EventCanvas()
    runner = event_bench._EventRun(canvas, "zoom", 1, 16, 250, 5000)
    try:
        runner.inputs = [{"enqueue_ms": 90, "dispatch_ms": 100, "paint_id": 2, "role": "step"}]
        runner.paints = [
            {"id": 1, "start_ms": 99, "end_ms": 101, "phase": "natural",
             "reduced_grid_quality": False, "input_ids": []},
            {"id": 2, "start_ms": 110, "end_ms": 130, "phase": "natural",
             "reduced_grid_quality": final_quality, "input_ids": [1]},
            {"id": 3, "start_ms": 400, "end_ms": 410, "phase": "idle_probe",
             "reduced_grid_quality": False, "input_ids": []},
        ]
        runner.idle_probe_paint_id = 3
        report = runner.report()
        refinement = report["final_full_quality"]
        assert refinement["paint_id"] == (2 if final_quality is False else None)
        assert refinement["last_input_to_completion_ms"] == (30 if final_quality is False else None)
        assert report["idle_probe"]["last_input_to_completion_ms"] == 310
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()