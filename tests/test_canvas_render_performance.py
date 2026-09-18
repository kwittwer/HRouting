"""Deterministic rendering/cache regressions; no timing or persisted settings.

The overlap oracle deliberately checks every pair and walks an adjacency graph.
It does not call the production overlap predicate, sweep, or lane-step helper.
"""
from __future__ import annotations

import math
import os
import random
import sys
from itertools import combinations
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas_widget import (  # noqa: E402
    CanvasWidget,
    ELEC_CABLE_OVERLAP_ANGLE_TOL_DEG,
    ELEC_CABLE_OVERLAP_TOLERANCE_PX,
)
from model.document import Document  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def canvas(app):
    widget = CanvasWidget()
    widget.resize(1000, 800)
    widget._grid_visible = False
    widget._scale = 1.0
    widget._offset = QPointF()
    widget._elec_cable_overlap_gap_px = 4.0
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()


def spy_on(monkeypatch, obj, name):
    spy = Mock(wraps=getattr(obj, name))
    monkeypatch.setattr(obj, name, spy)
    return spy


def points(*coordinates):
    return [QPointF(x, y) for x, y in coordinates]


def seed_pair(canvas):
    canvas._elec_cables = {
        cid: points((100, 100), (500, 300)) for cid in ("EK-1", "EK-2")
    }


def lookup(canvas, cid):
    return canvas._get_elec_cable_segment_offsets(cid, canvas._elec_cables[cid])


def visible_ids(canvas):
    return sorted(
        cid for cid, route in canvas._elec_cables.items()
        if canvas._elec_visible.get(cid, True) and len(route) >= 2
    )


def reference_overlap(first, second):
    """Original geometric contract, independently expressed with scalar vectors."""
    a, b, length_a, direction_a = first
    c, d, _length_b, direction_b = second
    tolerance = ELEC_CABLE_OVERLAP_TOLERANCE_PX
    for axis in (0, 1):
        if max(a[axis], b[axis]) + tolerance < min(c[axis], d[axis]):
            return False
        if max(c[axis], d[axis]) + tolerance < min(a[axis], b[axis]):
            return False
    cosine = abs(sum(x * y for x, y in zip(direction_a, direction_b)))
    if cosine < math.cos(math.radians(ELEC_CABLE_OVERLAP_ANGLE_TOL_DEG)):
        return False

    def distance(point, origin, unit):
        return abs((point[0] - origin[0]) * unit[1]
                   - (point[1] - origin[1]) * unit[0])

    if any(distance(point, origin, unit) > tolerance for point, origin, unit in (
        (a, c, direction_b), (b, c, direction_b),
        (c, a, direction_a), (d, a, direction_a),
    )):
        return False
    projections = [
        sum((point[axis] - a[axis]) * direction_a[axis] for axis in (0, 1))
        for point in (c, d)
    ]
    return min(length_a, max(projections)) - max(0.0, min(projections)) > 0.5


def brute_force_offsets(canvas, cable_ids):
    """All-pairs graph + DFS, not the production X sweep/union-find."""
    result = {
        cid: [0.0] * max(0, len(canvas._elec_cables[cid]) - 1)
        for cid in cable_ids
    }
    segments = {}
    for cid in cable_ids:
        route = canvas._elec_cables[cid]
        for index, (start, end) in enumerate(zip(route, route[1:])):
            a, b = (start.x(), start.y()), (end.x(), end.y())
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length >= 1e-6:
                segments[cid, index] = (a, b, length, (dx / length, dy / length))

    neighbors = {key: set() for key in segments}
    for left, right in combinations(segments, 2):
        if left[0] != right[0] and reference_overlap(segments[left], segments[right]):
            neighbors[left].add(right)
            neighbors[right].add(left)

    remaining = set(segments)
    while remaining:
        pending = [remaining.pop()]
        component = set(pending)
        while pending:
            for neighbor in neighbors[pending.pop()]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    pending.append(neighbor)
        cables = sorted({cid for cid, _index in component})
        if len(cables) < 2:
            continue
        stroke = max(float(canvas._elec_cable_stroke_width.get(cid, 2.0))
                     for cid, _index in component)
        gap = canvas._elec_cable_overlap_gap_px
        step = max(2.0, stroke + gap) / max(canvas._scale, 1e-9) if gap > 0 else 0.0
        for cid, index in component:
            result[cid][index] = (cables.index(cid) - (len(cables) - 1) / 2) * step
    return result


def assert_offsets(actual, expected):
    assert actual.keys() == expected.keys()
    for cid in expected:
        assert actual[cid] == pytest.approx(expected[cid], rel=1e-12, abs=1e-12), cid


@pytest.mark.parametrize("target", ["image", "grab"])
def test_real_paint_validates_signature_once_for_many_visible_cables(canvas, monkeypatch, target):
    # Both bounds dimensions are nonzero: horizontal lines currently get culled
    # as empty QRectFs, which would make a paint-performance assertion vacuous.
    for index in range(48):
        group = index // 2
        x, y = 60 + (group % 6) * 120, 60 + (group // 6) * 130
        canvas._elec_cables[f"EK-{index + 1}"] = points((x, y), (x + 75, y + 40))
    signature = spy_on(monkeypatch, canvas, "_build_elec_cable_overlap_signature")
    compute = spy_on(monkeypatch, canvas, "_compute_elec_cable_segment_offsets")
    original_draw = canvas._draw_elec_cable
    drawn_ids = []

    def observe_draw(painter, cid, route):
        # Mock.call_args would retain the paint-scoped QPainter past the event
        # and can crash native Qt teardown. Record only the cable ID instead.
        drawn_ids.append(cid)
        original_draw(painter, cid, route)

    monkeypatch.setattr(canvas, "_draw_elec_cable", observe_draw)
    original_scene = canvas._paint_scene
    paints = []

    def observe_scene(event):
        before_signature, before_draw = signature.call_count, len(drawn_ids)
        assert canvas._painting_scene is True
        assert canvas._elec_cable_paint_prepared is False
        original_scene(event)
        paints.append((signature.call_count - before_signature,
                       len(drawn_ids) - before_draw))

    monkeypatch.setattr(canvas, "_paint_scene", observe_scene)
    for scale, pan in ((1.0, QPointF()), (0.8, QPointF(15, 20)),
                       (1.1, QPointF(-10, -15)), (1.0, QPointF())):
        canvas._scale, canvas._offset = scale, pan
        previous_paints = len(paints)
        if target == "image":
            image = QImage(canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor("transparent"))
            canvas.render(image)  # Real QWidget paint event onto a QPaintDevice.
        else:
            pixmap = canvas.grab()
            assert not pixmap.isNull()
            image = pixmap.toImage()
        assert not image.isNull()
        assert len(paints) > previous_paints
        for signature_calls, draw_calls in paints[previous_paints:]:
            assert draw_calls == 48
            assert signature_calls == 1  # <= 1, and prove validation actually ran.
        assert canvas._painting_scene is False
        assert canvas._elec_cable_paint_prepared is False
    assert compute.call_count == 1


@pytest.mark.parametrize("previous", [(False, False), (True, False), (True, True)])
def test_paint_wrapper_restores_flags_after_exception(canvas, monkeypatch, previous):
    seed_pair(canvas)
    canvas._painting_scene, canvas._elec_cable_paint_prepared = previous

    def fail_scene(_event):
        assert canvas._painting_scene is True
        assert canvas._elec_cable_paint_prepared is False
        assert lookup(canvas, "EK-1") == [-3.0]
        assert canvas._elec_cable_paint_prepared is True
        raise RuntimeError("intentional paint failure")

    monkeypatch.setattr(canvas, "_paint_scene", fail_scene)
    # Direct entry only here: propagating Python exceptions through Qt's event
    # dispatcher is platform-dependent. No real QPainter is opened by the stub.
    with pytest.raises(RuntimeError, match="intentional paint failure"):
        canvas.paintEvent(QPaintEvent(canvas.rect()))
    assert canvas._painting_scene is previous[0]
    assert canvas._elec_cable_paint_prepared is False
    signature = spy_on(monkeypatch, canvas, "_build_elec_cable_overlap_signature")
    canvas._elec_visible["EK-2"] = False
    assert lookup(canvas, "EK-1") == [0.0]
    assert signature.call_count == 1  # Neither failed nor nested paint trusts stale data.


@pytest.mark.parametrize("change", [
    "point-in-place", "list-in-place", "visibility", "add", "remove", "stroke", "gap",
])
def test_external_edits_invalidate_without_setters_or_document_callbacks(canvas, monkeypatch, change):
    seed_pair(canvas)
    compute = spy_on(monkeypatch, canvas, "_compute_elec_cable_segment_offsets")
    assert lookup(canvas, "EK-1") == [-3.0]
    assert lookup(canvas, "EK-2") == [3.0]
    assert compute.call_count == 1
    previous_key = canvas._elec_cable_overlap_cache_key
    if change == "point-in-place":
        # Even an edit below display/path rounding precision changes content.
        canvas._elec_cables["EK-2"][0].setX(100 + 1e-8)
    elif change == "list-in-place":
        canvas._elec_cables["EK-2"][1] = QPointF(500, 500)
    elif change == "visibility":
        canvas._elec_visible["EK-2"] = False
    elif change == "add":
        canvas._elec_cables["EK-3"] = points((100, 100), (500, 300))
    elif change == "remove":
        del canvas._elec_cables["EK-2"]
    elif change == "stroke":
        canvas._elec_cable_stroke_width["EK-2"] = 7.25
    else:
        canvas._elec_cable_overlap_gap_px = 11.5
    expected = brute_force_offsets(canvas, visible_ids(canvas))
    assert_offsets({cid: lookup(canvas, cid) for cid in expected}, expected)
    assert compute.call_count == 2
    assert canvas._elec_cable_overlap_cache_key != previous_key
    assert_offsets({cid: lookup(canvas, cid) for cid in expected}, expected)
    assert compute.call_count == 2


def test_visibility_and_gap_round_trips_restore_cached_lanes(canvas, monkeypatch):
    seed_pair(canvas)
    compute = spy_on(monkeypatch, canvas, "_compute_elec_cable_segment_offsets")
    assert lookup(canvas, "EK-1") == [-3.0]
    canvas._elec_visible["EK-2"] = False
    assert lookup(canvas, "EK-1") == [0.0]
    canvas._elec_visible["EK-2"] = True
    assert lookup(canvas, "EK-1") == [-3.0]
    assert compute.call_count == 3
    canvas._elec_cable_overlap_gap_px = 0.0
    assert lookup(canvas, "EK-1") == [0.0]
    assert compute.call_count == 3
    # Geometry may change while the gap-disabled early return bypasses validation.
    canvas._elec_cables["EK-2"][1] = QPointF(500, 500)
    canvas._elec_cable_overlap_gap_px = 4.0
    assert lookup(canvas, "EK-1") == [0.0]
    assert compute.call_count == 4


def test_pan_zoom_reuses_classification_with_exact_offsets_and_shifted_hit_test(canvas, monkeypatch):
    seed_pair(canvas)
    canvas._elec_cable_overlap_gap_px = 30.0  # Shift exceeds the 8px hit radius.
    canvas._scale = 1.7
    compute = spy_on(monkeypatch, canvas, "_compute_elec_cable_segment_offsets")
    lookup(canvas, "EK-1")
    key = canvas._elec_cable_overlap_cache_key
    midpoint = QPointF(300, 200)
    normal = QPointF(-1 / math.sqrt(5), 2 / math.sqrt(5))
    for scale in (1.7, 0.1, 0.37, 1.0, 2.3, 7.0, 50.0, 1.7):
        for pan in (QPointF(), QPointF(1234, -567)):
            canvas._scale, canvas._offset = scale, pan
            for cid, sign in (("EK-1", -1), ("EK-2", 1)):
                offset = sign * 16.0 / scale
                assert lookup(canvas, cid) == pytest.approx([offset], rel=1e-14)
                shifted = midpoint + normal * offset
                assert canvas._hit_elec_cable_edge(shifted, cid) == (0, 1)
                assert canvas._hit_elec_cable_edge(midpoint, cid) is None
            assert canvas._elec_cable_overlap_cache_key == key
    assert compute.call_count == 1


def test_zoom_round_trip_never_mutates_cached_offsets_or_accumulates_drift(canvas, monkeypatch):
    seed_pair(canvas)
    canvas._scale = 1.3
    canvas._elec_cable_overlap_gap_px = 5.7
    compute = spy_on(monkeypatch, canvas, "_compute_elec_cable_segment_offsets")
    baseline = {cid: lookup(canvas, cid) for cid in visible_ids(canvas)}
    cache = canvas._elec_cable_segment_offset_cache
    snapshot = {cid: values[:] for cid, values in cache.items()}
    for _ in range(100):
        for scale in (0.1, 0.73, 13.7, 50.0, 1.3):
            canvas._scale = scale
            for cid, values in baseline.items():
                assert lookup(canvas, cid) == pytest.approx(
                    [value * (1.3 / scale) for value in values], rel=1e-14, abs=1e-14
                )
        assert {cid: lookup(canvas, cid) for cid in baseline} == baseline
    assert canvas._elec_cable_segment_offset_cache is cache
    assert cache == snapshot
    assert canvas._elec_cable_offset_cache_scale == 1.3
    assert compute.call_count == 1


def mixed_routes(seed):
    rng = random.Random(seed)
    routes = [
        # Isolated transitive component: first and third never overlap directly.
        [(0, -1000), (40, -1000)], [(30, -1000), (70, -1000)],
        [(60, -1000), (100, -1000)],
        # Crossing diagonals alone must not become one overlap component.
        [(-800, -800), (-700, -700)], [(-800, -700), (-700, -800)],
        [(0, 0), (0, 0)], [], [(12, 12)],
        # Same-cable overlapping segments are not a reason to assign lanes.
        [(-900, 800), (-850, 800), (-900, 800)],
    ]
    for group in range(12):
        angle = (0, math.pi / 2, math.pi / 4, -math.pi / 4)[group % 4]
        ux, uy = math.cos(angle), math.sin(angle)
        origin = (rng.uniform(-300, 300), rng.uniform(-300, 300))
        for lane in range(rng.randint(3, 5)):
            start = lane * 25 + rng.uniform(-3, 3)
            end = start + rng.uniform(35, 65)
            lateral = rng.uniform(-2, 2)
            a = (origin[0] + start * ux - lateral * uy,
                 origin[1] + start * uy + lateral * ux)
            b = (origin[0] + end * ux - lateral * uy,
                 origin[1] + end * uy + lateral * ux)
            route = [a, b] if rng.random() < 0.5 else [b, a]
            if lane == 0:
                route.insert(1, route[0])  # Zero-length segment inside a polyline.
            routes.append(route)
    entries = [(f"EK-{index + 1}", points(*route)) for index, route in enumerate(routes)]
    rng.shuffle(entries)  # Sweep cannot depend on input/insertion order.
    return dict(entries)


@pytest.mark.parametrize("seed", [7, 41, 20260918])
@pytest.mark.parametrize("scale", [0.1, 0.73, 2.5, 50.0])
def test_sweep_matches_independent_brute_force_mixed_segments(canvas, seed, scale):
    canvas._elec_cables = mixed_routes(seed)
    canvas._scale = scale
    canvas._elec_cable_stroke_width = {
        cid: 1.0 + int(cid.split("-")[1]) % 5 for cid in canvas._elec_cables
    }
    ids = list(canvas._elec_cables)
    expected = brute_force_offsets(canvas, ids)
    assert expected["EK-1"][0] < 0
    assert expected["EK-2"] == [0.0]
    assert expected["EK-3"][0] > 0
    for cid in ("EK-4", "EK-5", "EK-6", "EK-9"):
        assert all(value == 0.0 for value in expected[cid])
    assert_offsets(canvas._compute_elec_cable_segment_offsets(ids), expected)
    assert_offsets(canvas._compute_elec_cable_segment_offsets(list(reversed(ids))), expected)


@pytest.mark.parametrize("reverse_assignment", [False, True], ids=["A-B", "B-A"])
def test_x_sweep_preserves_directional_endpoint_pair_order(canvas, reverse_assignment):
    # Near-parallel endpoint overlap is 0.49px projected onto A, but
    # ~0.6374px onto B: opposite sides of the legacy 0.5px threshold.
    route_a = ((0.0, 0.0), (40.0, 0.0))
    route_b = ((-40.0, 1.0), (0.49, 3.0))

    def reference_segment(route):
        start, end = route
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        return start, end, length, (dx / length, dy / length)

    segment_a, segment_b = map(reference_segment, (route_a, route_b))
    assert reference_overlap(segment_a, segment_b) is False
    assert reference_overlap(segment_b, segment_a) is True
    # X sorting visits B first, reversing the original A-B pair orientation.
    assert min(x for x, _y in route_a) > min(x for x, _y in route_b)

    first, second = (route_b, route_a) if reverse_assignment else (route_a, route_b)
    canvas._elec_cables = {"EK-1": points(*first), "EK-2": points(*second)}
    ids = visible_ids(canvas)
    expected = brute_force_offsets(canvas, ids)
    assert_offsets(expected, {
        "EK-1": [-3.0 if reverse_assignment else 0.0],
        "EK-2": [3.0 if reverse_assignment else 0.0],
    })
    assert_offsets(canvas._compute_elec_cable_segment_offsets(ids), expected)


@pytest.mark.parametrize("start_x,y,overlaps", [
    (39.5, 0.0, False), (39.499, 0.0, True),
    (10.0, 8.0, True), (10.0, 8.000001, False),
])
def test_sweep_preserves_overlap_and_distance_boundaries(canvas, start_x, y, overlaps):
    canvas._elec_cables = {
        "EK-1": points((-40, 0), (40, 0)),
        "EK-2": points((start_x, y), (80, y)),
    }
    expected = {"EK-1": [-3.0 if overlaps else 0.0],
                "EK-2": [3.0 if overlaps else 0.0]}
    assert_offsets(brute_force_offsets(canvas, visible_ids(canvas)), expected)
    assert_offsets(canvas._compute_elec_cable_segment_offsets(visible_ids(canvas)), expected)


@pytest.mark.parametrize("separation_axis", ["x", "y"])
def test_two_hundred_distant_segments_need_few_narrowphase_calls(canvas, monkeypatch, separation_axis):
    for index in range(199):
        x, y = (index * 100, 0) if separation_axis == "x" else (0, index * 100)
        canvas._elec_cables[f"EK-{index + 1}"] = points((x, y), (x + 20, y + 10))
    canvas._elec_cables["EK-200"] = points((0, 0), (20, 10))
    assert len(canvas._elec_cables) == 200
    narrowphase = spy_on(monkeypatch, canvas, "_segments_overlap_for_lane")
    actual = canvas._compute_elec_cable_segment_offsets(visible_ids(canvas))
    assert 1 <= narrowphase.call_count <= 2  # Versus 19,900 unordered pairs.
    assert actual["EK-1"] == [-3.0]
    assert actual["EK-200"] == [3.0]
    assert all(values == [0.0] for cid, values in actual.items()
               if cid not in ("EK-1", "EK-200"))


def test_manual_route_cache_skips_offsets_until_geometry_or_line_distance_changes(canvas, monkeypatch):
    route = points((40, 40), (140, 90), (200, 180))
    canvas._manual_routes["HK-1"] = route
    canvas._route_line_dist_px["HK-1"] = 8.0
    offset = spy_on(monkeypatch, canvas, "_offset_route_points")
    image = QImage(400, 400, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("transparent"))
    painter = QPainter(image)
    assert painter.isActive()

    def draw():
        canvas._draw_manual_route(painter, "HK-1", route, QColor("white"))
        return canvas._manual_route_path_cache["HK-1"][1]

    try:
        path = draw()
        assert not path.isEmpty()
        assert offset.call_count == 2
        assert [call.args[1] for call in offset.call_args_list] == [4.0, -4.0]
        for scale in (0.1, 0.73, 2.5, 50.0, 1.0):
            canvas._scale = scale
            canvas._offset = QPointF(123, -456)
            assert draw() is path
        assert offset.call_count == 2
        route[1].setX(140 + 1e-8)
        changed = draw()
        assert changed is not path
        assert offset.call_count == 4
        assert draw() is changed
        route[1] = QPointF(150, 110)
        changed_again = draw()
        assert changed_again is not changed
        assert offset.call_count == 6
        canvas._route_line_dist_px["HK-1"] = 14.0
        wider = draw()
        assert wider is not changed_again
        assert offset.call_count == 8
        assert [call.args[1] for call in offset.call_args_list[-2:]] == [7.0, -7.0]
        assert draw() is wider
        assert offset.call_count == 8
    finally:
        painter.end()


class RecordingLabels(dict):
    def __init__(self, initial):
        super().__init__(initial)
        self.writes = []

    def __setitem__(self, key, value):
        self.writes.append(key)
        super().__setitem__(key, value)


@pytest.mark.parametrize("name,expected", [("  Renamed outlet  ", "Renamed outlet"), ("  ", "AP-1")])
def test_bound_single_element_mutation_updates_only_its_label(canvas, monkeypatch, name, expected):
    document = Document.from_dict({
        "canvas": {"elec_points": {"AP-1": [10, 20], "AP-2": [30, 40]}},
        "params": {"elec_points": {
            "AP-1": {"point_id": "AP-1", "name": "First"},
            "AP-2": {"point_id": "AP-2", "name": "Second"},
        }},
    })
    canvas.set_document(document)  # Real binding, not a mocked callback or settings load.
    assert canvas._label_map == {"AP-1": "First", "AP-2": "Second"}
    labels = RecordingLabels(canvas._label_map)
    canvas._label_map = labels
    rebuild = spy_on(monkeypatch, canvas, "_rebuild_label_map")
    changed = []
    canvas.document_data_changed.connect(changed.append)
    document.elements["elec_points"]["AP-1"].data["name"] = name
    canvas._elec_points["AP-1"] = QPointF(77, 88)
    assert document.to_dict()["canvas"]["elec_points"]["AP-1"] == [77.0, 88.0]
    assert changed == ["AP-1"]
    assert labels == {"AP-1": expected, "AP-2": "Second"}
    assert labels.writes == ["AP-1"]
    rebuild.assert_not_called()