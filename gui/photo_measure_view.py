"""Photo interaction with canonical coordinates independent of preview and zoom."""

from copy import deepcopy

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from logic.photo_geometry import rectify_photo, transform_points


class PhotoMeasureView(QGraphicsView):
    measurement_created = Signal(list)
    boundary_created = Signal(list)
    interaction_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMinimumSize(260, 240)
        self.image = None
        self.region = None
        self.rectified = False
        self.H_preview = None
        self._inverse_preview = None
        self.mode = "pan"
        self.measurement_enabled = False
        self._points = []
        self._measurements = []
        self._detections = []
        self._overlays = []
        self._pixmap_item = None

    def clear_photo(self):
        self.scene().clear()
        self.image = None
        self.region = None
        self._pixmap_item = None
        self.H_preview = self._inverse_preview = None
        self.rectified = False
        self._overlays = []
        self._points = []
        self._measurements = []
        self._detections = []
        self.measurement_enabled = False

    def set_photo(self, image, region=None, measurements=None, detections=None):
        self.clear_photo()
        self.image = image
        self.region = deepcopy(region)
        self._measurements = deepcopy(measurements or [])
        self._detections = deepcopy(detections or [])
        self._render_image(image)
        self._draw_overlays()
        self.fit_photo()

    def _render_image(self, image):
        import numpy as np

        if image.ndim == 2:
            rgb = np.ascontiguousarray(image)
            image_format = QImage.Format_Grayscale8
        else:
            rgb = np.ascontiguousarray(image[:, :, :3][:, :, ::-1])
            image_format = QImage.Format_RGB888
        height, width = rgb.shape[:2]
        qimage = QImage(rgb.data, width, height, rgb.strides[0], image_format).copy()
        if self._pixmap_item is None:
            self._pixmap_item = self.scene().addPixmap(QPixmap.fromImage(qimage))
        else:
            self._pixmap_item.setPixmap(QPixmap.fromImage(qimage))
        self._pixmap_item.setZValue(-10)
        self.setSceneRect(0, 0, width, height)

    def set_rectified(self, enabled):
        import numpy as np

        if self.image is None:
            return False
        try:
            if enabled:
                if self.region is None:
                    raise ValueError("Keine gueltige Kalibrierung")
                preview, matrix = rectify_photo(self.image, self.region)
                inverse = np.linalg.inv(np.asarray(matrix, dtype=float)).tolist()
                self.H_preview, self._inverse_preview = matrix, inverse
            else:
                preview = self.image
                self.H_preview = self._inverse_preview = None
            self.rectified = bool(enabled)
            self._points = []
            self._render_image(preview)
            self._draw_overlays()
            self.fit_photo()
            return True
        except (ValueError, RuntimeError, ImportError, np.linalg.LinAlgError) as error:
            self.interaction_error.emit(str(error))
            return False

    def canonical_to_preview(self, points):
        return transform_points(points, self.H_preview) if self.H_preview is not None else deepcopy(points)

    def preview_to_canonical(self, points):
        return transform_points(points, self._inverse_preview) if self._inverse_preview is not None else deepcopy(points)

    def set_mode(self, mode):
        self.mode = mode
        self._points = []
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode == "pan" else QGraphicsView.NoDrag)
        self.setCursor(Qt.OpenHandCursor if mode == "pan" else Qt.CrossCursor)
        self._draw_overlays()

    def set_overlays(self, measurements, detections=None, region=None):
        self._measurements = deepcopy(measurements)
        if detections is not None:
            self._detections = deepcopy(detections)
        if region is not None:
            self.region = deepcopy(region)
        self._draw_overlays()

    def _draw_overlays(self):
        for item in self._overlays:
            self.scene().removeItem(item)
        self._overlays = []
        if self.image is None:
            return

        def polygon(points, color, closed=True, label=""):
            mapped = self.canonical_to_preview(points)
            pen = QPen(QColor(color), 2)
            pen.setCosmetic(True)
            if closed:
                item = self.scene().addPolygon(QPolygonF([QPointF(*point) for point in mapped]), pen)
            elif len(mapped) >= 2:
                item = self.scene().addLine(*mapped[0], *mapped[1], pen)
            else:
                item = self.scene().addEllipse(mapped[0][0] - 3, mapped[0][1] - 3, 6, 6, pen)
            item.setZValue(1)
            self._overlays.append(item)
            if label and mapped:
                text = self.scene().addText(label)
                text.setDefaultTextColor(QColor(color))
                text.setFlag(text.GraphicsItemFlag.ItemIgnoresTransformations)
                text.setPos(*mapped[0])
                text.setZValue(2)
                self._overlays.append(text)

        try:
            for detection in self._detections:
                if len(detection.get("corners", [])) == 4:
                    label = detection.get("point_id") or detection.get("marker_id") or "Unbekannter Marker"
                    polygon(detection["corners"], "#ffce54", label=label)
            if self.region and self.region.get("mask_px"):
                polygon(self.region["mask_px"], "#40dfb5", label="Messbereich")
            for record in self._measurements:
                if len(record.get("points", [])) == 2:
                    value = record.get("value_mm")
                    label = record.get("name", "Messung")
                    if value is not None:
                        label += f"  {value / 10:.1f} cm"
                    polygon(record["points"], "#ff6666" if record.get("stale") else "#66e9ff", False, label)
            for point in self._points:
                polygon([point], "#ffffff", False)
        except ValueError as error:
            self.interaction_error.emit(str(error))

    def accept_canonical_point(self, point):
        if self.image is None or self.mode == "pan":
            return
        if self.mode != "mask" and not self.measurement_enabled:
            self.interaction_error.emit("Messung gesperrt: Kalibrierung oder Foto pruefen")
            return
        horizontal, vertical = map(float, point)
        height, width = self.image.shape[:2]
        if not (0 <= horizontal < width and 0 <= vertical < height):
            return
        self._points.append([horizontal, vertical])
        required = 4 if self.mode == "mask" else 1 if self.mode in ("floor", "origin", "ap") else 2
        if len(self._points) == required:
            points, self._points = self._points, []
            if self.mode == "mask":
                self.boundary_created.emit(points)
            else:
                self.measurement_created.emit(points)
        self._draw_overlays()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.mode != "pan":
            try:
                position = self.mapToScene(event.position().toPoint())
                self.accept_canonical_point(self.preview_to_canonical([[position.x(), position.y()]])[0])
            except ValueError as error:
                self.interaction_error.emit(str(error))
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._points = []
            self._draw_overlays()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if 0.01 <= self.transform().m11() * factor <= 100:
            self.scale(factor, factor)
        event.accept()

    def fit_photo(self):
        if self.image is not None:
            self.resetTransform()
            self.fitInView(self.sceneRect().adjusted(-6, -6, 6, 6), Qt.KeepAspectRatio)