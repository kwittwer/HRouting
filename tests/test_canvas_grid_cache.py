"""Grid regression contracts, independent of projects/settings/native exports.

The oracle below is the pre-cache world-space grid algorithm, NOT draw_lines.
Compare premultiplied RGBA bytes at the SAME physical pixel: Qt's intermediate
transparent layer introduces up to two levels of integer SourceOver rounding
(also on transparent output). Unpremultiplying tiny alpha amplifies that error.
No image alignment, shifted pixels, blur, percentage allowance or golden update
is used. Separate coverage checks reject changes to grid/clip locations.
"""
from __future__ import annotations

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

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QRegion, QTransform,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.grid_renderer import GridLayerCache  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def legacy_grid(painter, width, height, scale, offset, spacing, color):
    """Frozen legacy construction: no production grid/cache helper is called."""
    left = -offset.x() / scale
    top = -offset.y() / scale
    right = left + width / scale
    bottom = top + height / scale
    pen = QPen(color)
    pen.setWidth(1)
    pen.setCosmetic(True)
    painter.setPen(pen)
    x = (left // spacing) * spacing
    while x <= right:
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        x += spacing
    y = (top // spacing) * spacing
    while y <= bottom:
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        y += spacing


def image_background(width, height, dpr, background):
    image = QImage(math.ceil(width * dpr), math.ceil(height * dpr),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(QColor("transparent") if background == "transparent"
               else QColor(17, 29, 43))
    if background == "pattern":
        painter = QPainter(image)
        try:
            for y in range(0, height, 7):
                for x in range(0, width, 9):
                    if (x // 9 + y // 7) % 2:
                        painter.fillRect(QRect(x, y, 9, 7), QColor(67, 13, 89))
        finally:
            painter.end()
    return image


def render_grid(cache=None, *, width=97, height=73, dpr=1.0, scale=1.0,
                offset=(-13.25, -8.625), spacing=9.375,
                rgba=(239, 211, 173, 103), aa=True, smooth=False,
                background="solid", clip="none", use_cache=True, setup=None):
    """QImage is always the destination, including fractional-DPR cache hits."""
    image = image_background(width, height, dpr, background)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, aa)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        # Install in logical device coordinates, BEFORE the world transform.
        if clip == "region":
            region = QRegion(QRect(3, 5, width - 9, height - 12))
            region -= QRegion(QRect(31, 24, 19, 17))
            painter.setClipRegion(region)
        elif clip == "fractional":
            painter.setClipRect(QRectF(4.25, 7.5, width - 16.75, height - 18.25))
        painter.translate(*offset)
        painter.scale(scale, scale)
        if setup is not None:
            setup(painter)
        args = (painter, width, height, scale, QPointF(*offset), spacing, QColor(*rgba))
        if cache is None:
            legacy_grid(*args)
        else:
            cache.draw(*args, use_cache=use_cache)
    finally:
        painter.end()
    return image


def rgba_bytes(image):
    # constBits is a borrowed buffer: its QImage owner must outlive bytes().
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    return bytes(converted.constBits())


def assert_pixels(actual, expected, tolerance=2):
    assert actual.size() == expected.size()
    assert actual.devicePixelRatio() == expected.devicePixelRatio()
    a, b = rgba_bytes(actual), rgba_bytes(expected)
    if a == b:
        return
    difference, index = max((abs(x - y), i) for i, (x, y) in enumerate(zip(a, b)))
    assert difference <= tolerance, (
        f"max channel error {difference} > {tolerance} at physical pixel "
        f"({index // 4 % actual.width()}, {index // 4 // actual.width()}), "
        f"channel {index % 4}: actual={a[index]}, legacy={b[index]}"
    )


def assert_same_coverage(actual, expected, background):
    a, b, bg = map(rgba_bytes, (actual, expected, background))
    actual_mask = [a[i:i + 4] != bg[i:i + 4] for i in range(0, len(a), 4)]
    expected_mask = [b[i:i + 4] != bg[i:i + 4] for i in range(0, len(b), 4)]
    assert any(expected_mask), "Vacuous test: the legacy grid painted no pixels"
    assert not all(expected_mask), "Vacuous test: no unpainted grid/clip gaps"
    assert actual_mask == expected_mask, "Grid or clip coverage moved/appeared/disappeared"


def count_lines(monkeypatch):
    calls = []
    original = GridLayerCache.draw_lines

    def record(painter, width, height, scale, offset, spacing, color):
        # Never retain a paint-scoped QPainter in Mock.call_args.
        calls.append((width, height, scale, offset.x(), offset.y(), spacing, color.rgba()))
        return original(painter, width, height, scale, offset, spacing, color)

    monkeypatch.setattr(GridLayerCache, "draw_lines", staticmethod(record))
    return calls


VIEWS = [
    pytest.param(1.0, 9.375, (-13.25, -8.625), id="unit-negative"),
    pytest.param(0.65, 7.125, (-27.375, 4.125), id="zoom-out-mixed"),
    pytest.param(2.375, 3.625, (-0.375, -19.875), id="zoom-in-negative"),
]


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
@pytest.mark.parametrize("scale,spacing,offset", VIEWS)
@pytest.mark.parametrize("aa", [False, True], ids=["aliased", "antialiased"])
@pytest.mark.parametrize("background", ["solid", "pattern", "transparent"])
@pytest.mark.parametrize("clip", ["none", "region", "fractional"])
def test_cached_and_direct_pixels_match_independent_legacy(
    app, monkeypatch, dpr, scale, spacing, offset, aa, background, clip,
):
    options = dict(dpr=dpr, scale=scale, spacing=spacing, offset=offset,
                   aa=aa, background=background, clip=clip)
    expected = render_grid(**options)
    calls = count_lines(monkeypatch)
    direct_cache = GridLayerCache()
    direct = render_grid(direct_cache, use_cache=False, **options)
    assert_pixels(direct, expected, tolerance=0)
    assert direct_cache.pixmap is None
    cache = GridLayerCache()
    cold = render_grid(cache, **options)
    warm = render_grid(cache, **options)
    assert len(calls) == 2, "One direct render + one miss; repeat must be a real cache hit"
    assert cache.pixmap is not None
    assert cache.byte_size == math.ceil(97 * dpr) * math.ceil(73 * dpr) * 4
    assert_pixels(cold, expected)
    assert_pixels(warm, expected)
    assert_pixels(warm, cold, tolerance=0)
    bg = image_background(97, 73, dpr, background)
    assert_same_coverage(cold, expected, bg)


@pytest.mark.parametrize("change", [
    {"width": 99}, {"height": 75}, {"dpr": 1.5}, {"dpr": 2.0},
    {"scale": 1.00000001}, {"offset": (-13.25000001, -8.625)},
    {"offset": (-13.25, -8.62500001)}, {"spacing": 9.37500001},
    {"rgba": (238, 211, 173, 103)}, {"rgba": (239, 211, 173, 104)},
    {"aa": False}, {"smooth": True},
], ids=["width", "height", "dpr150", "dpr200", "exact-zoom", "exact-pan-x",
        "exact-pan-y", "exact-spacing", "color", "alpha", "aa", "render-hints"])
def test_exact_view_style_key_and_single_entry_replacement(app, monkeypatch, change):
    calls = count_lines(monkeypatch)
    cache = GridLayerCache()
    assert cache.max_bytes == 32 * 1024 * 1024
    render_grid(cache)
    old_key = cache.key
    old_pixmap_key = cache.pixmap.cacheKey()
    render_grid(cache)
    assert len(calls) == 1
    changed = render_grid(cache, **change)
    assert len(calls) == 2
    assert cache.key != old_key
    assert cache.pixmap.cacheKey() != old_pixmap_key
    assert cache.byte_size == cache.pixmap.width() * cache.pixmap.height() * 4
    assert cache.byte_size <= cache.max_bytes
    assert_pixels(changed, render_grid(**change))
    render_grid(cache, **change)
    assert len(calls) == 2
    render_grid(cache)
    assert len(calls) == 3, "Returning to A after B must miss: single entry, not an LRU"
    assert cache.key == old_key


def test_background_and_clip_are_not_baked_into_cached_layer(app, monkeypatch):
    calls = count_lines(monkeypatch)
    cache = GridLayerCache()
    for background, clip in [("solid", "region"), ("pattern", "none"),
                             ("transparent", "fractional"), ("solid", "none")]:
        actual = render_grid(cache, background=background, clip=clip, dpr=1.5)
        expected = render_grid(background=background, clip=clip, dpr=1.5)
        assert_pixels(actual, expected)
    assert len(calls) == 1


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_budget_exact_boundary_fallback_and_clear(app, monkeypatch, dpr):
    calls = count_lines(monkeypatch)
    required = math.ceil(97 * dpr) * math.ceil(73 * dpr) * 4
    cache = GridLayerCache(max_bytes=required)
    render_grid(cache, dpr=dpr)
    render_grid(cache, dpr=dpr)
    assert len(calls) == 1
    assert cache.byte_size == required
    for _ in range(2):
        actual = render_grid(cache, dpr=dpr, width=98)
        assert_pixels(actual, render_grid(dpr=dpr, width=98), tolerance=0)
        assert (cache.key, cache.pixmap, cache.byte_size) == (None, None, 0)
    assert len(calls) == 3
    render_grid(cache, dpr=dpr)
    assert len(calls) == 4
    cache.clear()
    cache.clear()
    assert (cache.key, cache.pixmap, cache.byte_size) == (None, None, 0)
    render_grid(cache, dpr=dpr)
    assert len(calls) == 5


def test_default_32_mib_budget_rejects_before_allocating_pixmap(app, monkeypatch):
    import gui.grid_renderer as renderer

    cache = GridLayerCache()
    render_grid(cache)  # Prove fallback also drops an existing smaller entry.
    calls = count_lines(monkeypatch)

    def forbidden_allocation(*args):
        pytest.fail("Over-budget view must not allocate a QPixmap")

    monkeypatch.setattr(renderer, "QPixmap", forbidden_allocation)
    # A tiny destination is sufficient to exercise a virtual oversized viewport.
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        cache.draw(painter, 4097, 2048, 1.0, QPointF(), 100.0, QColor("white"))
    finally:
        painter.end()
    assert len(calls) == 1
    assert (cache.key, cache.pixmap, cache.byte_size) == (None, None, 0)


@pytest.mark.parametrize("unsupported", ["rotation", "shear", "view", "opacity", "composition", "bypass"])
def test_unsupported_painter_states_use_exact_direct_path(app, monkeypatch, unsupported):
    def setup(painter):
        if unsupported == "rotation":
            painter.rotate(11.0)
        elif unsupported == "shear":
            painter.shear(0.15, 0.05)
        elif unsupported == "view":
            painter.setWindow(QRect(2, 3, 83, 61))
        elif unsupported == "opacity":
            painter.setOpacity(0.625)
        elif unsupported == "composition":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)

    cache = GridLayerCache()
    render_grid(cache)
    original = (cache.key, cache.pixmap.cacheKey(), cache.byte_size)
    calls = count_lines(monkeypatch)
    for _ in range(2):
        actual = render_grid(cache, setup=setup, use_cache=unsupported != "bypass")
        assert_pixels(actual, render_grid(setup=setup), tolerance=0)
    assert len(calls) == 2
    assert (cache.key, cache.pixmap.cacheKey(), cache.byte_size) == original


def painter_state(painter):
    return (painter.worldTransform(), painter.combinedTransform(),
            painter.window(), painter.viewport(), painter.hasClipping(),
            painter.clipPath(), painter.clipRegion(), painter.opacity(),
            painter.compositionMode(), painter.renderHints(), painter.brush(),
            painter.brushOrigin(), painter.background(), painter.backgroundMode(),
            painter.font(), painter.layoutDirection())


@pytest.mark.parametrize("path", ["cold", "warm", "direct", "budget", "unsupported"])
def test_painter_state_is_preserved_except_legacy_direct_pen(app, path):
    cache = GridLayerCache(max_bytes=0 if path == "budget" else 32 * 1024 * 1024)
    image = image_background(97, 73, 1.5, "pattern")
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        clip = QPainterPath()
        clip.addRect(QRectF(4.5, 6.25, 71.0, 53.5))
        painter.setClipPath(clip)
        transform = QTransform().translate(-13.25, -8.625).scale(1.25, 1.25)
        painter.setWorldTransform(transform)
        painter.setPen(QPen(QColor("magenta"), 3.25, Qt.PenStyle.DashLine))
        painter.setBrush(QBrush(QColor("green"), Qt.BrushStyle.Dense3Pattern))
        painter.setBrushOrigin(QPoint(7, 9))
        painter.setBackground(QBrush(QColor("navy")))
        painter.setBackgroundMode(Qt.BGMode.TransparentMode)
        painter.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        if path == "unsupported":
            painter.setOpacity(0.75)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        args = (painter, 97, 73, 1.25, QPointF(-13.25, -8.625), 9.375, QColor(200, 170, 140, 97))
        if path == "warm":
            cache.draw(*args)
        before, pen = painter_state(painter), QPen(painter.pen())
        cache.draw(*args, use_cache=path != "direct")
        assert painter_state(painter) == before
        if path in ("cold", "warm"):
            assert painter.pen() == pen, "Cached blit must not leak layer painter state"
        else:
            assert painter.pen().isCosmetic()
            assert painter.pen().width() == 1
            assert painter.pen().color() == args[-1]
    finally:
        painter.end()


@pytest.mark.parametrize("invalid", [
    {"scale": 0.0}, {"scale": -1.0}, {"scale": float("nan")},
    {"spacing": 0.0}, {"spacing": -1.0}, {"spacing": float("inf")},
    {"spacing": 1.999}, {"width": 0}, {"height": -1},
    {"offset": QPointF(float("inf"), 0)},
])
def test_invalid_or_subpixel_grids_do_not_draw_or_allocate(app, monkeypatch, invalid):
    cache = GridLayerCache()
    calls = count_lines(monkeypatch)
    image = image_background(20, 20, 1.0, "solid")
    before = image.copy()
    args = dict(width=20, height=20, scale=1.0, offset=QPointF(),
                spacing=10.0, color=QColor("white"))
    args.update(invalid)
    painter = QPainter(image)
    try:
        cache.draw(painter, **args)
    finally:
        painter.end()
    assert not calls
    assert_pixels(image, before, tolerance=0)
    assert (cache.key, cache.pixmap, cache.byte_size) == (None, None, 0)