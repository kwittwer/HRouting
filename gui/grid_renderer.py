"""Single-entry, bounded raster layer for the canvas grid.

Keep the original world-space line construction for fractional spacing and
negative offsets. Do not quantize the view or tile a rounded raster period.
Only the passive grid is cached; snapping and project data are not involved.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QTransform


class GridLayerCache:
    def __init__(self, max_bytes: int = 32 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self.key: tuple | None = None
        self.pixmap: QPixmap | None = None
        self.byte_size = 0

    def clear(self) -> None:
        self.key = None
        self.pixmap = None
        self.byte_size = 0

    @staticmethod
    def draw_lines(painter: QPainter, width: int, height: int, scale: float,
                   offset: QPointF, spacing: float, color: QColor) -> None:
        """Reference renderer. Input spacing and offsets are canvas units."""
        x0, y0 = -offset.x() / scale, -offset.y() / scale
        x1, y1 = x0 + width / scale, y0 + height / scale
        x, y = (x0 // spacing) * spacing, (y0 // spacing) * spacing
        pen = QPen(color)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        while x <= x1:
            painter.drawLine(QPointF(x, y0), QPointF(x, y1))
            x += spacing
        while y <= y1:
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))
            y += spacing

    def draw(self, painter: QPainter, width: int, height: int, scale: float,
             offset: QPointF, spacing: float, color: QColor,
             *, use_cache: bool = True) -> None:
        values = (scale, spacing, offset.x(), offset.y())
        if (not all(math.isfinite(value) for value in values)
                or scale <= 0 or spacing <= 0 or spacing * scale < 2
                or width <= 0 or height <= 0):
            return

        expected = QTransform()
        expected.translate(offset.x(), offset.y())
        expected.scale(scale, scale)
        device = painter.device()
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        # Unsupported transforms/blending retain the direct reference path.
        # View transforms (window/viewport) would require another cache space.
        cacheable = (
            use_cache and math.isfinite(dpr) and dpr > 0
            and painter.worldTransform() == expected
            and painter.window() == painter.viewport()
            and painter.opacity() == 1.0
            and painter.compositionMode() == QPainter.CompositionMode_SourceOver
        )
        if not cacheable:
            self.draw_lines(painter, width, height, scale, offset, spacing, color)
            return

        pixel_w, pixel_h = math.ceil(width * dpr), math.ceil(height * dpr)
        needed = pixel_w * pixel_h * 4
        if needed > self.max_bytes:
            self.clear()
            self.draw_lines(painter, width, height, scale, offset, spacing, color)
            return

        key = (width, height, dpr, scale, offset.x(), offset.y(), spacing,
               color.rgba(), painter.renderHints().value)
        if key != self.key or self.pixmap is None:
            # Release the previous entry before allocation: avoid transiently
            # doubling a high-DPI viewport's cache footprint.
            self.clear()
            pixmap = QPixmap(pixel_w, pixel_h)
            if pixmap.isNull():
                self.draw_lines(painter, width, height, scale, offset, spacing, color)
                return
            pixmap.setDevicePixelRatio(dpr)
            pixmap.fill(Qt.transparent)
            layer_painter = QPainter(pixmap)
            if not layer_painter.isActive():
                self.draw_lines(painter, width, height, scale, offset, spacing, color)
                return
            try:
                layer_painter.setRenderHints(painter.renderHints())
                layer_painter.setWorldTransform(expected)
                self.draw_lines(layer_painter, width, height, scale, offset, spacing, color)
            finally:
                layer_painter.end()
            self.pixmap, self.key, self.byte_size = pixmap, key, needed

        painter.save()
        try:
            painter.resetTransform()
            # Native-resolution blit. Preserve Qt's paint-event clipping.
            painter.drawPixmap(QPointF(0, 0), self.pixmap)
        finally:
            painter.restore()