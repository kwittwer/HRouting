"""Zero-width/height world bounds must not cull visible lines or points."""
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from gui.canvas_widget import CanvasWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(app):
    c = CanvasWidget()
    c.resize(600, 500)
    c._scale, c._offset = 1.0, QPointF()
    c._grid_visible = False
    c._bg_color = QColor("#000000")
    try:
        yield c
    finally:
        c.close()
        c.deleteLater()
        QApplication.sendPostedEvents(c, QEvent.DeferredDelete)


@pytest.mark.parametrize("bounds,expected", [
    (QRectF(50, 20, 0, 60), True),  # vertical
    (QRectF(20, 50, 60, 0), True),  # horizontal
    (QRectF(50, 50, 0, 0), True),  # point
    (QRectF(50, -10, 0, 120), True),  # crossing the entire viewport
    (QRectF(-10, 50, 120, 0), True),
    (QRectF(0, 20, 0, 60), True),  # edge contact: conservative culling
    (QRectF(100, 20, 0, 60), True),
    (QRectF(20, 100, 60, 0), True),
    (QRectF(100, 100, 0, 0), True),
    (QRectF(-1, 20, 0, 60), False),
    (QRectF(101, 20, 0, 60), False),
    (QRectF(20, -1, 60, 0), False),
    (QRectF(20, 101, 60, 0), False),
    (QRectF(50, -30, 0, 20), False),
    (QRectF(50, 110, 0, 20), False),
    (QRectF(20, 20, 40, 40), True),
    (QRectF(110, 110, 40, 40), False),
    (QRectF(80, 80, -60, -60), True),
    (None, False),
])
def test_bounds_overlap_including_degenerate_lines(canvas, bounds, expected):
    assert canvas._bounds_visible_in_rect(bounds, QRectF(0, 0, 100, 100)) is expected


def test_empty_viewport_and_missing_points_are_not_visible(canvas):
    assert canvas._points_bounds([]) is None
    assert not canvas._bounds_visible_in_rect(QRectF(0, 0, 100, 100), QRectF())


@pytest.mark.parametrize("route", [
    [(80, 80), (80, 300)],
    [(80, 80), (300, 80)],
    [(300, 80), (80, 80)],
    [(80, 80), (80, 190), (80, 300)],
])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("scale,offset", [(1.0, (0, 0)), (0.65, (65, -10)), (1.25, (-35, 25))])
def test_axis_aligned_cable_reaches_painter_and_produces_pixels(canvas, monkeypatch, route, selected, scale, offset):
    c = canvas
    pts = [QPointF(*p) for p in route]
    c._elec_cables = {"EK-1": pts}
    c._elec_visible = {"EK-1": True}
    c._label_visible = {"EK-1": False}
    c._color_map = {"EK-1": QColor("#ffb300")}
    c._scale, c._offset = scale, QPointF(*offset)
    if selected:
        c.set_selected_item("EK-1")
    drawn = []
    original = c._draw_elec_cable

    def observe(painter, cid, points):
        drawn.append(cid)  # Never retain the paint-scoped QPainter.
        original(painter, cid, points)

    monkeypatch.setattr(c, "_draw_elec_cable", observe)
    image = c.grab().toImage()
    assert drawn == ["EK-1"]
    mid = ((pts[0] + pts[-1]) * 0.5 * scale + c._offset) * image.devicePixelRatio()
    pixels = [image.pixelColor(round(mid.x()) + dx, round(mid.y()) + dy)
              for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    assert any(color.red() > 100 and color.green() > 60 for color in pixels)


def test_hidden_and_offscreen_axis_aligned_cables_still_culled(canvas, monkeypatch):
    canvas._elec_cables = {
        "EK-1": [QPointF(80, 80), QPointF(80, 300)],
        "EK-2": [QPointF(800, 80), QPointF(800, 300)],
    }
    canvas._elec_visible = {"EK-1": False, "EK-2": True}
    drawn = []
    monkeypatch.setattr(canvas, "_draw_elec_cable", lambda painter, cid, points: drawn.append(cid))
    canvas.grab()
    assert drawn == []