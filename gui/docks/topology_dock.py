from __future__ import annotations

from collections import defaultdict, deque
import math
from typing import Callable

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgGenerator, QSvgRenderer
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.elec_topology_types import ApNode, CableEdge
from gui.parameter_panel import BUILTIN_SYMBOLS
from model.document import Document
from storage.asset_data_uri import is_data_uri, is_svg_asset_ref, parse_data_uri


def _line_style_to_pen_style(style_key: str):
    style = str(style_key or "solid").strip().lower()
    if style == "dash":
        return Qt.PenStyle.DashLine
    if style == "dot":
        return Qt.PenStyle.DotLine
    if style == "dashdot":
        return Qt.PenStyle.DashDotLine
    return Qt.PenStyle.SolidLine


class _TopologyGraphicsView(QGraphicsView):
    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        self.scale(factor, factor)
        event.accept()


class ElecTopologyWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ap_nodes: dict[str, ApNode] = {}
        self._cable_edges: dict[str, CableEdge] = {}
        self._selected_root_ap_id = ""
        self._node_positions: dict[str, tuple[float, float]] = {}
        self._tree_edge_ids: set[str] = set()
        self._node_component_centers: dict[str, tuple[float, float]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        self._summary_label = QLabel("Keine Topologie geladen")
        self._summary_label.setWordWrap(True)
        toolbar.addWidget(self._summary_label, 1)

        self._fit_button = QPushButton("Einpassen")
        self._fit_button.clicked.connect(self.fit_to_content)
        toolbar.addWidget(self._fit_button)

        self._export_button = QPushButton("SVG exportieren…")
        self._export_button.clicked.connect(self._export_svg_via_dialog)
        toolbar.addWidget(self._export_button)
        root.addLayout(toolbar)

        self.scene = QGraphicsScene(self)
        self.view = _TopologyGraphicsView(self)
        self.view.setScene(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        root.addWidget(self.view, 1)

    def set_data(
        self,
        ap_nodes: list[ApNode],
        cable_edges: list[CableEdge],
        root_ap_id: str = "",
    ) -> None:
        visible_nodes = {node.point_id: node for node in ap_nodes if node.visible}
        visible_edges: dict[str, CableEdge] = {}
        connected_node_ids: set[str] = set()
        for edge in cable_edges:
            if not edge.visible:
                continue
            start_ap_id = str(edge.start_ap_id or "").strip()
            end_ap_id = str(edge.end_ap_id or "").strip()
            if not start_ap_id or not end_ap_id:
                continue
            if start_ap_id not in visible_nodes or end_ap_id not in visible_nodes:
                continue
            if start_ap_id == end_ap_id:
                continue
            visible_edges[edge.cable_id] = edge
            connected_node_ids.add(start_ap_id)
            connected_node_ids.add(end_ap_id)

        self._ap_nodes = {
            point_id: node
            for point_id, node in visible_nodes.items()
            if point_id in connected_node_ids
        }
        self._cable_edges = visible_edges
        self._selected_root_ap_id = str(root_ap_id or "").strip()
        self._render()

    def set_root_ap_id(self, root_ap_id: str) -> None:
        self._selected_root_ap_id = str(root_ap_id or "").strip()
        self._render()

    def root_ap_id(self) -> str:
        return self._selected_root_ap_id

    def export_svg(self, path: str) -> bool:
        scene_rect = self._scene_export_rect()
        if scene_rect.isNull() or scene_rect.width() <= 0 or scene_rect.height() <= 0:
            return False

        generator = QSvgGenerator()
        generator.setFileName(path)
        generator.setSize(
            QSize(
                max(1, int(round(scene_rect.width()))),
                max(1, int(round(scene_rect.height()))),
            )
        )
        generator.setViewBox(scene_rect.toRect())
        generator.setTitle("HRouting Elektro-Topologie")
        generator.setDescription("Automatisch generierte Topologie der Elektroinstallation")

        painter = QPainter(generator)
        try:
            self.scene.render(painter, QRectF(), scene_rect)
        finally:
            painter.end()
        return True

    def render_to_painter(self, painter: QPainter, target_rect: QRectF) -> bool:
        scene_rect = self._scene_export_rect()
        if scene_rect.isNull() or scene_rect.width() <= 0 or scene_rect.height() <= 0:
            return False
        self.scene.render(painter, target_rect, scene_rect)
        return True

    @classmethod
    def render_snapshot_to_painter(
        cls,
        painter: QPainter,
        target_rect: QRectF,
        ap_nodes: list[ApNode],
        cable_edges: list[CableEdge],
        root_ap_id: str = "",
    ) -> bool:
        widget = cls()
        try:
            widget.set_data(ap_nodes, cable_edges, root_ap_id)
            for item in widget.scene.items():
                if isinstance(item, QGraphicsSimpleTextItem):
                    item.setBrush(QBrush(QColor("#000000")))
            return widget.render_to_painter(painter, target_rect)
        finally:
            widget.deleteLater()

    def fit_to_content(self) -> None:
        rect = self._scene_export_rect()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _export_svg_via_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Topologie als SVG exportieren",
            "elektro-topologie.svg",
            "SVG (*.svg)",
        )
        if not path:
            return
        self.export_svg(path)

    def _scene_export_rect(self) -> QRectF:
        rect = self.scene.itemsBoundingRect().adjusted(-24.0, -24.0, 24.0, 24.0)
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            rect = QRectF(0.0, 0.0, 640.0, 360.0)
        return rect

    def _guess_root_ap_id(self, node_ids: set[str]) -> str:
        if not node_ids:
            return ""
        if self._selected_root_ap_id and self._selected_root_ap_id in node_ids:
            return self._selected_root_ap_id
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self._cable_edges.values():
            start_ap_id = str(edge.start_ap_id or "").strip()
            end_ap_id = str(edge.end_ap_id or "").strip()
            if start_ap_id in node_ids and end_ap_id in node_ids and start_ap_id != end_ap_id:
                adjacency[start_ap_id].add(end_ap_id)
                adjacency[end_ap_id].add(start_ap_id)
        uv_candidates = [node_id for node_id in node_ids if self._ap_nodes[node_id].ap_type == "uv"]
        if uv_candidates:
            return max(uv_candidates, key=lambda node_id: len(adjacency[node_id]))
        return max(sorted(node_ids), key=lambda node_id: (len(adjacency[node_id]), node_id))

    def _compute_layout_positions(self) -> dict[str, tuple[float, float]]:
        node_ids = set(self._ap_nodes.keys())
        adjacency: dict[str, set[str]] = defaultdict(set)
        edge_ids_by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
        for edge in self._cable_edges.values():
            start_ap_id = str(edge.start_ap_id or "").strip()
            end_ap_id = str(edge.end_ap_id or "").strip()
            if start_ap_id in node_ids and end_ap_id in node_ids and start_ap_id != end_ap_id:
                adjacency[start_ap_id].add(end_ap_id)
                adjacency[end_ap_id].add(start_ap_id)
                pair = tuple(sorted((start_ap_id, end_ap_id)))
                edge_ids_by_pair[pair].append(edge.cable_id)

        unvisited = set(node_ids)
        components: list[list[str]] = []
        while unvisited:
            seed = sorted(unvisited)[0]
            queue = deque([seed])
            component: list[str] = []
            unvisited.remove(seed)
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbor in sorted(adjacency[current]):
                    if neighbor in unvisited:
                        unvisited.remove(neighbor)
                        queue.append(neighbor)
            components.append(component)

        positions: dict[str, tuple[float, float]] = {}
        self._tree_edge_ids = set()
        self._node_component_centers = {}
        component_roots: list[tuple[list[str], str]] = []
        for component in components:
            component_nodes = set(component)
            root_ap_id = self._guess_root_ap_id(component_nodes)
            component_roots.append((component, root_ap_id))

        component_roots.sort(
            key=lambda entry: (
                0 if entry[1] == self._selected_root_ap_id and self._selected_root_ap_id else 1,
                -len(entry[0]),
                (self._ap_nodes[entry[1]].name or entry[1]).lower(),
            )
        )

        component_centers: list[tuple[float, float]] = [(0.0, 0.0)]
        if len(component_roots) > 1:
            orbit_radius = 520.0
            for idx in range(1, len(component_roots)):
                angle = ((idx - 1) * (2.0 * 3.141592653589793 / max(1, len(component_roots) - 1))) - 1.5707963267948966
                component_centers.append(
                    (
                        orbit_radius * math.cos(angle),
                        orbit_radius * math.sin(angle),
                    )
                )

        for component_index, (component, root_ap_id) in enumerate(component_roots):
            levels: dict[str, int] = {root_ap_id: 0}
            parents: dict[str, str] = {}
            queue = deque([root_ap_id])
            while queue:
                current = queue.popleft()
                for neighbor in sorted(
                    adjacency[current],
                    key=lambda node_id: (
                        -len(adjacency[node_id]),
                        (self._ap_nodes[node_id].name or node_id).lower(),
                        node_id,
                    ),
                ):
                    if neighbor not in levels:
                        levels[neighbor] = levels[current] + 1
                        parents[neighbor] = current
                        queue.append(neighbor)

            for node_id, parent_id in parents.items():
                pair = tuple(sorted((node_id, parent_id)))
                cable_ids = edge_ids_by_pair.get(pair, [])
                if cable_ids:
                    self._tree_edge_ids.add(cable_ids[0])

            children: dict[str, list[str]] = defaultdict(list)
            for node_id, parent_id in parents.items():
                children[parent_id].append(node_id)

            subtree_weights: dict[str, float] = {}
            subtree_nodes: dict[str, set[str]] = {}

            def _subtree_weight(node_id: str) -> float:
                cached = subtree_weights.get(node_id)
                if cached is not None:
                    return cached
                child_ids = children.get(node_id, [])
                if not child_ids:
                    subtree_weights[node_id] = 1.0
                    return 1.0
                weight = sum(_subtree_weight(child_id) for child_id in child_ids)
                subtree_weights[node_id] = max(weight, 1.0)
                return subtree_weights[node_id]

            def _subtree_node_set(node_id: str) -> set[str]:
                cached = subtree_nodes.get(node_id)
                if cached is not None:
                    return cached
                node_set = {node_id}
                for child_id in children.get(node_id, []):
                    node_set.update(_subtree_node_set(child_id))
                subtree_nodes[node_id] = node_set
                return node_set

            _subtree_weight(root_ap_id)
            _subtree_node_set(root_ap_id)

            non_tree_pairs = {
                tuple(sorted((str(edge.start_ap_id or "").strip(), str(edge.end_ap_id or "").strip())))
                for edge in self._cable_edges.values()
                if edge.cable_id not in self._tree_edge_ids
            }

            def _branch_affinity(left_id: str, right_id: str) -> int:
                left_nodes = subtree_nodes.get(left_id, {left_id})
                right_nodes = subtree_nodes.get(right_id, {right_id})
                score = 0
                for start_id in left_nodes:
                    for end_id in right_nodes:
                        if tuple(sorted((start_id, end_id))) in non_tree_pairs:
                            score += 1
                return score

            def _order_children(parent_id: str) -> list[str]:
                child_ids = list(children.get(parent_id, []))
                if len(child_ids) <= 1:
                    return child_ids
                remaining = sorted(
                    child_ids,
                    key=lambda child_id: (
                        -len(adjacency[child_id]),
                        -_subtree_weight(child_id),
                        (self._ap_nodes[child_id].name or child_id).lower(),
                        child_id,
                    ),
                )
                if len(remaining) == 2:
                    return remaining
                totals = {
                    child_id: sum(_branch_affinity(child_id, other_id) for other_id in child_ids if other_id != child_id)
                    for child_id in child_ids
                }
                ordered = [max(remaining, key=lambda child_id: (totals[child_id], len(adjacency[child_id]), child_id))]
                remaining.remove(ordered[0])
                while remaining:
                    candidate = max(
                        remaining,
                        key=lambda child_id: (
                            max(_branch_affinity(child_id, ordered[0]), _branch_affinity(child_id, ordered[-1])),
                            totals[child_id],
                            len(adjacency[child_id]),
                            child_id,
                        ),
                    )
                    left_gain = _branch_affinity(candidate, ordered[0])
                    right_gain = _branch_affinity(candidate, ordered[-1])
                    if left_gain > right_gain:
                        ordered.insert(0, candidate)
                    else:
                        ordered.append(candidate)
                    remaining.remove(candidate)
                return ordered

            ordered_children: dict[str, list[str]] = {}
            for node_id in children:
                ordered_children[node_id] = _order_children(node_id)

            level_nodes: dict[int, list[str]] = defaultdict(list)
            for node_id, level in levels.items():
                level_nodes[level].append(node_id)

            max_diameter = max(
                max(42.0, min(68.0, max(node.width_px, node.height_px, 42.0)))
                for node in self._ap_nodes.values()
            )
            min_arc_gap = max_diameter + 44.0
            radial_gap = max_diameter + 118.0
            center_x, center_y = component_centers[min(component_index, len(component_centers) - 1)]

            for node_id in component:
                self._node_component_centers[node_id] = (center_x, center_y)

            positions[root_ap_id] = (center_x, center_y)
            node_angles: dict[str, float] = {root_ap_id: -math.pi / 2.0}

            def _assign_angles(node_id: str, start_angle: float, end_angle: float) -> None:
                child_ids = ordered_children.get(node_id, [])
                if not child_ids:
                    return
                level = levels[node_id] + 1
                radius = level * radial_gap
                parent_angle = node_angles.get(node_id, (start_angle + end_angle) / 2.0)
                available_span = max(0.3, end_angle - start_angle)
                if len(child_ids) == 1:
                    child_id = child_ids[0]
                    positions[child_id] = (
                        center_x + (radius * math.cos(parent_angle)),
                        center_y + (radius * math.sin(parent_angle)),
                    )
                    node_angles[child_id] = parent_angle
                    child_span = max(min_arc_gap / max(radius, 1.0), 0.28)
                    _assign_angles(child_id, parent_angle - (child_span / 2.0), parent_angle + (child_span / 2.0))
                    return
                required_span = 0.0
                for child_id in child_ids:
                    required_span += max(min_arc_gap / max(radius, 1.0), 0.24) * _subtree_weight(child_id)
                span = max(min(available_span, required_span), min(available_span, math.tau - 0.24))
                span = max(span, min(available_span, 0.45 * len(child_ids)))
                cursor = parent_angle - (span / 2.0)
                cursor = max(start_angle, min(cursor, end_angle - span))
                total_weight = sum(_subtree_weight(child_id) for child_id in child_ids)
                for child_id in child_ids:
                    share = span * (_subtree_weight(child_id) / max(total_weight, 1.0))
                    child_mid = cursor + (share / 2.0)
                    positions[child_id] = (
                        center_x + (radius * math.cos(child_mid)),
                        center_y + (radius * math.sin(child_mid)),
                    )
                    node_angles[child_id] = child_mid
                    _assign_angles(child_id, cursor, cursor + share)
                    cursor += share

            if children.get(root_ap_id):
                _assign_angles(root_ap_id, -math.pi, math.pi)

            overflow_nodes = [node_id for node_id in component if node_id not in positions]
            if overflow_nodes:
                base_radius = (max(levels.values(), default=0) + 1) * radial_gap
                extra_step = max_diameter + 36.0
                for index, node_id in enumerate(sorted(overflow_nodes)):
                    angle = (2.0 * math.pi * index) / max(1, len(overflow_nodes))
                    radius = base_radius + extra_step
                    positions[node_id] = (
                        center_x + (radius * math.cos(angle)),
                        center_y + (radius * math.sin(angle)),
                    )

        return positions

    def _render(self) -> None:
        self.scene.clear()
        self._node_positions = {}
        self._tree_edge_ids = set()
        self._node_component_centers = {}
        if not self._ap_nodes and not self._cable_edges:
            self.scene.addSimpleText("Keine APs/Kabel vorhanden.")
            self._summary_label.setText("Keine Topologie geladen")
            return

        positions = self._compute_layout_positions()
        self._node_positions = dict(positions)
        self._draw_cables(positions)
        self._draw_nodes(positions)
        self.scene.setSceneRect(self._scene_export_rect())
        self._summary_label.setText(
            f"Topologie: {len(self._ap_nodes)} APs, {len(self._cable_edges)} Kabel"
        )
        self.fit_to_content()

    def _draw_cables(self, positions: dict[str, tuple[float, float]]) -> None:
        for edge in self._cable_edges.values():
            start_ap_id = str(edge.start_ap_id or "").strip()
            end_ap_id = str(edge.end_ap_id or "").strip()
            start_pos = positions.get(start_ap_id)
            end_pos = positions.get(end_ap_id)
            if start_pos is None and end_pos is None:
                continue

            if start_pos is not None and end_pos is not None:
                sx, sy = start_pos
                ex, ey = end_pos
            elif start_pos is not None:
                sx, sy = start_pos
                ex, ey = sx + 110.0, sy + 50.0
            else:
                ex, ey = end_pos
                sx, sy = ex - 110.0, ey - 50.0

            path = QPainterPath()
            path.moveTo(sx, sy)
            is_tree_edge = edge.cable_id in self._tree_edge_ids
            if is_tree_edge or start_pos is None or end_pos is None:
                path.lineTo(ex, ey)
            else:
                center_x, center_y = self._node_component_centers.get(start_ap_id, (0.0, 0.0))
                start_angle = math.atan2(sy - center_y, sx - center_x)
                end_angle = math.atan2(ey - center_y, ex - center_x)
                start_radius = math.hypot(sx - center_x, sy - center_y)
                end_radius = math.hypot(ex - center_x, ey - center_y)
                outer_radius = max(start_radius, end_radius) + 90.0
                c1x = center_x + (outer_radius * math.cos(start_angle))
                c1y = center_y + (outer_radius * math.sin(start_angle))
                c2x = center_x + (outer_radius * math.cos(end_angle))
                c2y = center_y + (outer_radius * math.sin(end_angle))
                path.cubicTo(c1x, c1y, c2x, c2y, ex, ey)
            edge_color = QColor(edge.color or "#ff9800")
            if not is_tree_edge:
                edge_color.setAlpha(150)
            item = QGraphicsPathItem(path)
            item.setPen(
                QPen(
                    edge_color,
                    max(1.0, float(edge.stroke_width_px or 2.0)),
                    Qt.PenStyle.SolidLine if is_tree_edge else Qt.PenStyle.DashLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            item.setZValue(5.0 if is_tree_edge else 3.0)
            self.scene.addItem(item)

    def _draw_nodes(self, positions: dict[str, tuple[float, float]]) -> None:
        for point_id, node in self._ap_nodes.items():
            if point_id not in positions:
                continue
            x, y = positions[point_id]
            diameter = max(42.0, min(68.0, max(node.width_px, node.height_px, 42.0)))
            rect = QRectF(x - (diameter / 2.0), y - (diameter / 2.0), diameter, diameter)
            color = QColor(node.color or "#4fc3f7")
            fill = QColor(color)
            fill.setAlpha(46)

            body = QGraphicsEllipseItem(rect)
            body.setBrush(QBrush(fill))
            body.setPen(QPen(color, 2.0))
            body.setZValue(15.0)
            self.scene.addItem(body)

            icon_rect = QRectF(rect.left() + 7.0, rect.top() + 7.0, rect.width() - 14.0, rect.height() - 14.0)
            self._draw_symbol(body, icon_rect, node)

            label_text = node.name or point_id
            label = QGraphicsSimpleTextItem(label_text)
            font = label.font()
            font.setPointSizeF(max(8.0, min(11.0, float(node.label_size or 10.0) - 1.0)))
            label.setFont(font)
            label.setBrush(QBrush(QColor("#dceaf4")))
            label_rect = label.boundingRect()
            label.setPos(x - (label_rect.width() / 2.0), rect.bottom() + 6.0)
            label.setZValue(20.0)
            self.scene.addItem(label)

    def _draw_symbol(self, parent_item, rect: QRectF, node: ApNode) -> None:
        icon_path = str(node.icon_path or "").strip()
        if not icon_path:
            icon_path = BUILTIN_SYMBOLS.get(node.builtin_symbol or "", "")
        if not icon_path:
            return

        if is_svg_asset_ref(icon_path):
            if is_data_uri(icon_path):
                parsed = parse_data_uri(icon_path)
                if parsed is None:
                    return
                renderer = QSvgRenderer(QByteArray(parsed[1]))
            else:
                renderer = QSvgRenderer(icon_path)
            if renderer.isValid():
                image = QImage(
                    max(1, int(round(rect.width()))),
                    max(1, int(round(rect.height()))),
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                try:
                    renderer.render(painter, QRectF(0.0, 0.0, rect.width(), rect.height()))
                finally:
                    painter.end()
                pixmap = QPixmap.fromImage(image)
                item = QGraphicsPixmapItem(pixmap, parent_item)
                item.setPos(rect.left(), rect.top())
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                return

        pixmap = QPixmap()
        if is_data_uri(icon_path):
            parsed = parse_data_uri(icon_path)
            if parsed is None or not pixmap.loadFromData(parsed[1]):
                return
        else:
            pixmap = QPixmap(icon_path)
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            max(1, int(round(rect.width()))),
            max(1, int(round(rect.height()))),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        item = QGraphicsPixmapItem(scaled, parent_item)
        item.setPos(rect.center().x() - (scaled.width() / 2.0), rect.center().y() - (scaled.height() / 2.0))
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)


class TopologyDock(QDockWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Elektro-Topologie", parent)
        self.setObjectName("dock_topology")
        self._document: Document | None = None
        self._ap_nodes: list[ApNode] = []
        self._cable_edges: list[CableEdge] = []
        self._selected_root_ap_id = ""
        self._data_provider: Callable[[], tuple[list[ApNode], list[CableEdge]]] | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._apply_render_data)

        self._widget = ElecTopologyWidget(self)
        self.setWidget(self._widget)

        title_bar = QWidget(self)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(6, 4, 6, 4)
        title_layout.setSpacing(6)
        title_layout.addWidget(QLabel("Wurzel:", title_bar))
        self._root_combo = QComboBox(title_bar)
        self._root_combo.currentIndexChanged.connect(self._on_root_changed)
        title_layout.addWidget(self._root_combo, 1)
        self.setTitleBarWidget(title_bar)

    def set_document(self, document: Document | None) -> None:
        if self._document is not None:
            try:
                self._document.structure_changed.disconnect(self._schedule_refresh)
                self._document.element_added.disconnect(self._schedule_refresh)
                self._document.element_removed.disconnect(self._schedule_refresh)
                self._document.element_changed.disconnect(self._schedule_refresh)
            except RuntimeError:
                pass
        self._document = document
        if document is not None:
            document.structure_changed.connect(self._schedule_refresh)
            document.element_added.connect(self._schedule_refresh)
            document.element_removed.connect(self._schedule_refresh)
            document.element_changed.connect(self._schedule_refresh)
        else:
            self._ap_nodes = []
            self._cable_edges = []
            self.refresh_now()

    def set_data_provider(
        self,
        provider: Callable[[], tuple[list[ApNode], list[CableEdge]]] | None,
    ) -> None:
        self._data_provider = provider

    def set_topology_data(self, ap_nodes: list[ApNode], cable_edges: list[CableEdge]) -> None:
        self._ap_nodes = list(ap_nodes)
        self._cable_edges = list(cable_edges)
        self.refresh_now()

    def set_root_ap_id(self, root_ap_id: str) -> None:
        normalized = str(root_ap_id or "").strip()
        if normalized == self._selected_root_ap_id:
            return
        self._selected_root_ap_id = normalized
        self._sync_root_combo()
        self._widget.set_root_ap_id(normalized)

    def root_ap_id(self) -> str:
        return self._selected_root_ap_id

    def refresh_now(self) -> None:
        self._refresh_timer.stop()
        self._apply_render_data()

    def export_svg(self, path: str) -> bool:
        return self._widget.export_svg(path)

    def render_to_painter(self, painter: QPainter, target_rect: QRectF) -> bool:
        return self._widget.render_to_painter(painter, target_rect)

    def render_snapshot_to_painter(
        self,
        painter: QPainter,
        target_rect: QRectF,
        ap_nodes: list[ApNode],
        cable_edges: list[CableEdge],
        root_ap_id: str = "",
    ) -> bool:
        return ElecTopologyWidget.render_snapshot_to_painter(
            painter,
            target_rect,
            ap_nodes,
            cable_edges,
            str(root_ap_id or "").strip(),
        )

    def _schedule_refresh(self, *_args) -> None:
        self._refresh_timer.start()

    def _apply_render_data(self) -> None:
        if self._data_provider is not None:
            ap_nodes, cable_edges = self._data_provider()
            self._ap_nodes = list(ap_nodes)
            self._cable_edges = list(cable_edges)
        self._sync_root_combo()
        self._widget.set_data(self._ap_nodes, self._cable_edges, self._selected_root_ap_id)

    def _connected_root_choices(self) -> list[tuple[str, str]]:
        nodes = {node.point_id: node for node in self._ap_nodes if node.visible}
        connected: set[str] = set()
        for edge in self._cable_edges:
            if not edge.visible:
                continue
            start_ap_id = str(edge.start_ap_id or "").strip()
            end_ap_id = str(edge.end_ap_id or "").strip()
            if not start_ap_id or not end_ap_id:
                continue
            if start_ap_id not in nodes or end_ap_id not in nodes or start_ap_id == end_ap_id:
                continue
            connected.add(start_ap_id)
            connected.add(end_ap_id)
        choices = [
            (point_id, (nodes[point_id].name or point_id))
            for point_id in sorted(connected, key=lambda node_id: (nodes[node_id].name or node_id).lower())
        ]
        return choices

    def _sync_root_combo(self) -> None:
        choices = self._connected_root_choices()
        valid_ids = {point_id for point_id, _label in choices}
        if self._selected_root_ap_id not in valid_ids:
            self._selected_root_ap_id = ""
        self._root_combo.blockSignals(True)
        self._root_combo.clear()
        self._root_combo.addItem("Automatisch", "")
        for point_id, label in choices:
            self._root_combo.addItem(f"{label} ({point_id})", point_id)
        index = self._root_combo.findData(self._selected_root_ap_id)
        self._root_combo.setCurrentIndex(index if index >= 0 else 0)
        self._root_combo.setEnabled(bool(choices))
        self._root_combo.blockSignals(False)

    def _on_root_changed(self, _index: int) -> None:
        self._selected_root_ap_id = str(self._root_combo.currentData() or "")
        self._widget.set_root_ap_id(self._selected_root_ap_id)