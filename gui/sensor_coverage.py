"""Qt geometry for detector coverage within room boundaries."""

from math import atan2, cos, hypot, sin

from PySide6.QtCore import QLineF, QPointF
from PySide6.QtGui import QPainterPath, QPolygonF


def visible_room_path(center: QPointF, polygon: list[QPointF]) -> QPainterPath:
    distances = [hypot(vertex.x() - center.x(), vertex.y() - center.y())
                 for vertex in polygon]
    ray_length = max(distances, default=0.0) * 2.0 + 1.0
    edges = [QLineF(vertex, polygon[(index + 1) % len(polygon)])
             for index, vertex in enumerate(polygon)]
    angles = []
    for vertex in polygon:
        angle = atan2(vertex.y() - center.y(), vertex.x() - center.x())
        angles.extend((angle - 1e-4, angle, angle + 1e-4))
    visible = []
    for angle in sorted(angles):
        endpoint = QPointF(center.x() + cos(angle) * ray_length,
                           center.y() + sin(angle) * ray_length)
        ray = QLineF(center, endpoint)
        nearest = center
        nearest_distance = float("inf")
        for edge in edges:
            kind, intersection = ray.intersects(edge)
            if kind != QLineF.BoundedIntersection:
                continue
            distance = QLineF(center, intersection).length()
            if distance > 1e-8 and distance < nearest_distance:
                nearest = intersection
                nearest_distance = distance
        visible.append(nearest)
    path = QPainterPath()
    path.addPolygon(QPolygonF(visible))
    path.closeSubpath()
    return path