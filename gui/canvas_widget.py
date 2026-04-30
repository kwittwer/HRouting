# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import math
import os
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QPointF, Signal, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPolygonF, QPainterPath, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QApplication, QToolTip

Point = Tuple[float, float]


@dataclass
class FloorPlanLayer:
    """Data for a single background floor plan image."""
    fp_id: str
    file_path: str = ""
    renderer: Optional[QSvgRenderer] = field(default=None, repr=False)
    pixmap: Optional[QPixmap] = field(default=None, repr=False)
    size: Tuple[float, float] = (100.0, 100.0)
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation: float = 0.0
    opacity: float = 1.0
    visible: bool = True
    mm_per_px: float = 1.0
    ref_p1: Optional[QPointF] = field(default=None, repr=False)
    ref_p2: Optional[QPointF] = field(default=None, repr=False)
    ref_length_mm: float = 1000.0
    fixed_width_mm: float = 0.0   # wenn > 0: feste Breite in mm (ignoriert mm_per_px)
    fixed_height_mm: float = 0.0  # wenn > 0: feste Höhe in mm (ignoriert mm_per_px)
    polygon: List[QPointF] = field(default_factory=list, repr=False)
    polygon_color: str = "#8d99ae"

COLORS = [
    "#e63946", "#2a9d8f", "#e9c46a", "#f4a261",
    "#457b9d", "#8338ec", "#fb5607", "#06d6a0",
]

class ToolMode(Enum):
    NONE       = auto()
    DRAW_REF   = auto()
    DRAW_POLY  = auto()
    DRAW_FURNITURE_POLY = auto()
    MOVE_START = auto()
    DRAW_ROUTE = auto()
    MOVE_ROUTE_POINT = auto()
    EDIT_POLYGON = auto()
    INSERT_POLYGON_POINT = auto()
    EDIT_ROUTE = auto()
    INSERT_ROUTE_POINT = auto()
    PLACE_ELEC_POINT = auto()
    DRAW_ELEC_CABLE = auto()
    EDIT_ELEC_CABLE = auto()
    MOVE_ELEC_POINT = auto()
    DRAW_SUPPLY_LINE = auto()
    EDIT_SUPPLY_LINE = auto()
    PLACE_HKV        = auto()
    MOVE_HKV         = auto()
    DRAW_HKV_LINE    = auto()
    EDIT_HKV_LINE    = auto()
    MOVE_FLOOR_PLAN  = auto()
    ROTATE_FLOOR_PLAN = auto()
    MEASURE          = auto()
    DRAW_EXPORT_FRAME = auto()
    PLACE_TEXT       = auto()
    MOVE_TEXT        = auto()

class CanvasWidget(QWidget):
    polygon_finished  = Signal(str, list)
    ref_line_set      = Signal()          # Linie fertig, Länge kommt vom Panel
    start_point_moved = Signal(str, tuple)
    route_changed     = Signal(str)
    polygon_changed   = Signal(str)       # emitted when polygon is edited (point moved/added/deleted)
    elec_point_placed  = Signal(str)
    elec_cable_changed = Signal(str)
    hkv_placed         = Signal(str)
    hkv_line_changed   = Signal(str)
    text_placed        = Signal(str)
    object_double_clicked = Signal(str, str)  # (object_type, object_id)
    floor_plan_transform_updated = Signal(str, float, float, float)  # (fp_id, ox, oy, rot)
    floor_plan_polygon_finished = Signal(str, list)
    mode_changed = Signal()  # emitted when tool mode changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(400, 400)

        self._svg_renderer: Optional[QSvgRenderer] = None
        self._bg_pixmap: Optional[QPixmap] = None
        self._svg_size = (800.0, 600.0)

        # Multiple floor plan layers
        self._floor_plans: Dict[str, FloorPlanLayer] = {}
        self._floor_plan_order: List[str] = []     # render back→front
        self._ref_floor_id: Optional[str] = None   # which floor we're drawing ref line for
        self._active_floor_id: Optional[str] = None    # floor plan being moved/rotated
        self._floor_drag_start: Optional[QPointF] = None
        self._floor_rotate_start_angle: float = 0.0
        self._floor_rotate_orig: float = 0.0

        # Background color
        self._bg_color = QColor("#2b2b2b")

        # Grid overlay
        self._grid_visible = False
        self._grid_spacing_mm = 100.0   # default 100 mm
        self._grid_color = QColor(255, 255, 255, 60)

        # Zoom & Pan
        self._scale  = 1.0
        self._offset = QPointF(0.0, 0.0)
        self._pan_start: Optional[QPointF] = None
        self._panning = False
        self._scale_min = 0.1   # Minimum zoom: 0.1x
        self._scale_max = 50.0  # Maximum zoom: 50x (was 100x)

        # Maßstab
        self._mm_per_px = 1.0

        # Referenzlinie (canvas-Koordinaten)
        self._ref_p1: Optional[QPointF] = None
        self._ref_p2: Optional[QPointF] = None

        # Polygon-Zeichnen
        self._current_circuit_id: Optional[str] = None
        self._current_furniture_id: Optional[str] = None
        self._current_points: List[QPointF] = []

        # Daten
        self._polygons:      Dict[str, List[QPointF]]            = {}
        self._start_points:  Dict[str, QPointF]                  = {}
        self._color_map:     Dict[str, QColor]                   = {}
        self._label_map:     Dict[str, str]                      = {}
        self._helper_lines:  Dict[str, List[QPointF]]            = {}
        self._show_helper_line: Dict[str, bool]                  = {}
        self._manual_routes: Dict[str, List[QPointF]]            = {}
        self._route_wall_dist_px: Dict[str, float]               = {}
        self._route_line_dist_px: Dict[str, float]               = {}
        self._circuit_visible: Dict[str, bool]                    = {}

        # Anschlussleitungen (supply lines, one per circuit)
        self._supply_lines:       Dict[str, List[QPointF]]        = {}

        # Elektro
        self._elec_points:        Dict[str, QPointF]              = {}
        self._elec_point_size_px: Dict[str, Tuple[float, float]]  = {}
        self._elec_point_icons:   Dict[str, Optional[QPixmap]]    = {}
        self._elec_point_svgs:    Dict[str, Optional[QSvgRenderer]] = {}
        self._elec_point_position: Dict[str, str]                 = {}  # "Wand", "Decke", "Boden", "Freitext"
        self._elec_point_height:   Dict[str, float]               = {}  # Höhe vom Boden in mm
        self._elec_cables:        Dict[str, List[QPointF]]        = {}
        self._elec_visible:       Dict[str, bool]                 = {}

        # Cable ↔ AP connections  (cable_id → point_id or "")
        self._cable_start_ap:     Dict[str, str]                  = {}
        self._cable_end_ap:       Dict[str, str]                  = {}

        # Heizkreisverteiler (HKV)
        self._hkv_points:         Dict[str, QPointF]              = {}
        self._hkv_size_px:        Dict[str, Tuple[float, float]]  = {}
        self._hkv_icons:          Dict[str, Optional[QPixmap]]    = {}
        self._hkv_svgs:           Dict[str, Optional[QSvgRenderer]] = {}
        self._hkv_visible:        Dict[str, bool]                  = {}

        # Supply line ↔ HKV connections  (circuit_id → hkv_id)
        self._supply_hkv:         Dict[str, str]                  = {}

        # HKV connecting lines (double-pipe between two HKV)
        self._hkv_lines:          Dict[str, List[QPointF]]        = {}
        self._hkv_line_start:     Dict[str, str]                  = {}
        self._hkv_line_end:       Dict[str, str]                  = {}
        self._hkv_line_visible:   Dict[str, bool]                  = {}

        # Text annotations
        self._text_annotations:   Dict[str, QPointF]              = {}  # id → position
        self._text_contents:      Dict[str, str]                  = {}  # id → text content
        self._text_font_sizes:    Dict[str, float]                = {}  # id → font size pt
        self._text_colors:        Dict[str, str]                  = {}  # id → color hex
        self._text_comments:      Dict[str, str]                  = {}  # id → tooltip comment
        self._text_visible:       Dict[str, bool]                 = {}
        self._text_rects:         Dict[str, QRectF]               = {}  # transient hit rects

        # Labels (movable + resizable)
        self._label_positions:    Dict[str, QPointF]              = {}
        self._label_font_sizes:   Dict[str, float]                = {}
        self._label_rects:        Dict[str, QRectF]               = {}  # hit testing (transient)
        self._label_draw_pos:     Dict[str, QPointF]              = {}  # transient
        self._dragging_label:     Optional[str]                   = None
        self._label_drag_offset:  QPointF                         = QPointF(0, 0)

        self._color_index   = 0
        self._dragging_start: Optional[str] = None
        self._dragging_route_point: Optional[Tuple[str, int]] = None
        self._mode          = ToolMode.NONE
        self._mouse_pos:    Optional[QPointF] = None
        self._show_ref_line = True
        self._current_route_cid: Optional[str] = None
        self._current_route_points: List[QPointF] = []
        self._current_route_preview_end: Optional[QPointF] = None
        self._constraint_violation_point: Optional[QPointF] = None
        self._constraint_violation_line: Optional[Tuple[QPointF, QPointF]] = None
        self._constraint_violation_reason: str = ""
        self._snap_angle: float = 90.0     # angle snapping step (degrees), 0 = off

        # Measurement tool
        self._measure_p1: Optional[QPointF] = None
        self._measure_p2: Optional[QPointF] = None
        self._measure_lines: List[Tuple[QPointF, QPointF, float]] = []  # persisted lines

        # Export frame (for SVG/PDF crop)
        self._export_frame: Optional[QRectF] = None
        self._export_frame_start: Optional[QPointF] = None
        self._export_frame_current: Optional[QPointF] = None

        # Elektro edit state
        self._placing_elec_point_id: Optional[str] = None
        self._current_elec_cable_id: Optional[str] = None
        self._current_elec_cable_points: List[QPointF] = []
        self._current_elec_cable_preview: Optional[QPointF] = None
        self._edit_elec_cable_id: Optional[str] = None
        self._dragging_elec_point: Optional[str] = None

        # Supply line edit state
        self._current_supply_cid: Optional[str] = None
        self._current_supply_points: List[QPointF] = []
        self._current_supply_preview: Optional[QPointF] = None
        self._edit_supply_cid: Optional[str] = None

        # HKV edit state
        self._placing_hkv_id: Optional[str] = None
        self._dragging_hkv: Optional[str] = None
        self._current_hkv_line_id: Optional[str] = None
        self._current_hkv_line_points: List[QPointF] = []
        self._current_hkv_line_preview: Optional[QPointF] = None
        self._edit_hkv_line_id: Optional[str] = None

        # Text annotation edit state
        self._placing_text_id: Optional[str] = None
        self._dragging_text: Optional[str] = None

        # Edit modes
        self._edit_polygon_cid: Optional[str] = None
        self._edit_floor_polygon_id: Optional[str] = None
        self._insert_between_indices: Optional[Tuple[int, int]] = None
        self._edit_route_cid: Optional[str] = None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def load_svg(self, filepath: str):
        """Load an SVG, PNG, or JPG as the background floor plan (legacy)."""
        self._svg_renderer = None
        self._bg_pixmap = None
        if not os.path.exists(filepath):
            self._svg_size = (100, 100)
            self.update()
            return
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".svg":
            self._svg_renderer = QSvgRenderer(filepath)
            vb = self._svg_renderer.viewBox()
            self._svg_size = (float(vb.width()), float(vb.height()))
        else:
            pm = QPixmap(filepath)
            if not pm.isNull():
                self._bg_pixmap = pm
                self._svg_size = (float(pm.width()), float(pm.height()))
            else:
                self._svg_size = (100, 100)
        self._fit_to_window()
        self.update()

    # ── Floor plan layer management ────────────────────────────────

    def add_floor_plan(self, fp_id: str, filepath: str = "") -> FloorPlanLayer:
        layer = FloorPlanLayer(fp_id=fp_id)
        self._floor_plans[fp_id] = layer
        if fp_id not in self._floor_plan_order:
            self._floor_plan_order.append(fp_id)
        if filepath:
            self.load_floor_plan_image(fp_id, filepath)
        return layer

    def remove_floor_plan(self, fp_id: str):
        self._floor_plans.pop(fp_id, None)
        if fp_id in self._floor_plan_order:
            self._floor_plan_order.remove(fp_id)
        self.update()

    def load_floor_plan_image(self, fp_id: str, filepath: str):
        layer = self._floor_plans.get(fp_id)
        if not layer:
            return
        layer.file_path = filepath
        # Preserve existing polygon (important for undo restore)
        saved_polygon = layer.polygon
        layer.renderer = None
        layer.pixmap = None
        if not os.path.exists(filepath):
            layer.size = (100.0, 100.0)
            self.update()
            return
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".svg":
            layer.renderer = QSvgRenderer(filepath)
            vb = layer.renderer.viewBox()
            layer.size = (float(vb.width()), float(vb.height()))
        else:
            pm = QPixmap(filepath)
            if not pm.isNull():
                layer.pixmap = pm
                layer.size = (float(pm.width()), float(pm.height()))
            else:
                layer.size = (100.0, 100.0)
        # If this is the first floor plan, fit window to it
        if len(self._floor_plans) == 1:
            self._svg_size = layer.size
            self._fit_to_window()
        # If global scale is already calibrated but layer is not,
        # initialise layer to match so it renders at native pixel size.
        if self._mm_per_px > 1.0 and layer.mm_per_px == 1.0:
            layer.mm_per_px = self._mm_per_px
        # Restore polygon if it existed (e.g. during undo restore)
        if saved_polygon:
            layer.polygon = saved_polygon
        self.update()

    def set_floor_plan_transform(self, fp_id: str,
                                  offset_x: float, offset_y: float,
                                  rotation: float):
        layer = self._floor_plans.get(fp_id)
        if layer:
            # Move ref points by the offset delta
            dx = offset_x - layer.offset_x
            dy = offset_y - layer.offset_y
            if (dx != 0 or dy != 0) and (layer.ref_p1 or layer.ref_p2):
                if layer.ref_p1:
                    layer.ref_p1 = QPointF(layer.ref_p1.x() + dx,
                                           layer.ref_p1.y() + dy)
                if layer.ref_p2:
                    layer.ref_p2 = QPointF(layer.ref_p2.x() + dx,
                                           layer.ref_p2.y() + dy)
                if self._ref_floor_id == fp_id:
                    self._ref_p1 = layer.ref_p1
                    self._ref_p2 = layer.ref_p2
            layer.offset_x = offset_x
            layer.offset_y = offset_y
            layer.rotation = rotation
            self.update()

    def rescale_layer_ref_points(self, fp_id: str,
                                  old_ls: float, new_ls: float):
        """Adjust ref_p1/ref_p2 when the layer's render scale changes."""
        layer = self._floor_plans.get(fp_id)
        if not layer or old_ls == new_ls or old_ls == 0:
            return
        import math
        w, h = layer.size
        old_sw, old_sh = w * old_ls, h * old_ls
        new_sw, new_sh = w * new_ls, h * new_ls
        old_cx = old_sw / 2 + layer.offset_x
        old_cy = old_sh / 2 + layer.offset_y
        new_cx = new_sw / 2 + layer.offset_x
        new_cy = new_sh / 2 + layer.offset_y
        rot = math.radians(layer.rotation)
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        cos_nr, sin_nr = math.cos(-rot), math.sin(-rot)
        for attr in ("ref_p1", "ref_p2"):
            pt = getattr(layer, attr)
            if pt is None:
                continue
            # Undo old paint transform → image-pixel coords
            rx, ry = pt.x() - old_cx, pt.y() - old_cy
            ix = rx * cos_nr - ry * sin_nr + old_sw / 2
            iy = rx * sin_nr + ry * cos_nr + old_sh / 2
            img_x, img_y = ix / old_ls, iy / old_ls
            # Apply new paint transform
            nx = img_x * new_ls - new_sw / 2
            ny = img_y * new_ls - new_sh / 2
            fx = nx * cos_r - ny * sin_r + new_cx
            fy = nx * sin_r + ny * cos_r + new_cy
            setattr(layer, attr, QPointF(fx, fy))
        # Sync global ref points
        if self._ref_floor_id == fp_id:
            self._ref_p1 = layer.ref_p1
            self._ref_p2 = layer.ref_p2

    def set_floor_plan_size_mm(self, fp_id: str,
                               width_mm: float, height_mm: float):
        """Setzt feste Abmessungen (mm) für ein Einrichtungselement."""
        layer = self._floor_plans.get(fp_id)
        if layer:
            layer.fixed_width_mm = width_mm
            layer.fixed_height_mm = height_mm
            self.update()

    def _layer_render_size(self, layer: "FloorPlanLayer") -> Tuple[float, float]:
        """Gibt die gerenderte (Breite, Höhe) in Canvas-Pixeln zurück.
        Wenn fixed_width_mm/fixed_height_mm gesetzt sind, werden diese verwendet,
        andernfalls die mm_per_px-basierte Skalierung."""
        ref_mpp = self._mm_per_px if self._mm_per_px > 0 else 1.0
        if layer.fixed_width_mm > 0 and layer.fixed_height_mm > 0:
            return (layer.fixed_width_mm / ref_mpp,
                    layer.fixed_height_mm / ref_mpp)
        ls = layer.mm_per_px / ref_mpp if layer.mm_per_px > 0 else 1.0
        w, h = layer.size
        return (w * ls, h * ls)

    def set_floor_plan_opacity(self, fp_id: str, opacity: float):
        layer = self._floor_plans.get(fp_id)
        if layer:
            layer.opacity = max(0.0, min(1.0, opacity))
            self.update()

    def set_floor_plan_visible(self, fp_id: str, visible: bool):
        layer = self._floor_plans.get(fp_id)
        if layer:
            layer.visible = visible
            self.update()

    def set_floor_plan_polygon_color(self, fp_id: str, color: str):
        layer = self._floor_plans.get(fp_id)
        if layer:
            layer.polygon_color = color or "#8d99ae"
            self.update()

    def set_floor_plan_order(self, order: List[str]):
        self._floor_plan_order = [fid for fid in order
                                   if fid in self._floor_plans]

    def start_ref_line_for_floor(self, fp_id: str):
        """Start drawing a reference line for a specific floor plan."""
        self._ref_floor_id = fp_id
        self._mode = ToolMode.DRAW_REF
        layer = self._floor_plans.get(fp_id)
        if layer:
            layer.ref_p1 = None
            layer.ref_p2 = None
        self._ref_p1 = None
        self._ref_p2 = None
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_measure(self):
        """Enter measurement mode – click two points to measure distance."""
        self._mode = ToolMode.MEASURE
        self._measure_p1 = None
        self._measure_p2 = None
        self.setCursor(Qt.CrossCursor)
        self.update()

    def clear_measurements(self):
        """Remove all persisted measurement lines."""
        self._measure_lines.clear()
        self.update()

    def start_draw_export_frame(self):
        """Enter mode to draw an export frame rectangle."""
        self._mode = ToolMode.DRAW_EXPORT_FRAME
        self._export_frame_start = None
        self._export_frame_current = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def clear_export_frame(self):
        """Remove export frame and reset draw state."""
        self._export_frame = None
        self._export_frame_start = None
        self._export_frame_current = None
        if self._mode == ToolMode.DRAW_EXPORT_FRAME:
            self._mode = ToolMode.NONE
            self.setCursor(Qt.ArrowCursor)
            self.mode_changed.emit()
        self.update()

    def get_export_frame(self) -> Optional[QRectF]:
        """Return normalized export frame in canvas coordinates."""
        if not self._export_frame:
            return None
        return QRectF(self._export_frame.normalized())

    def start_move_floor_plan(self, fp_id: str):
        """Enter mode to drag-move a floor plan with the mouse."""
        if fp_id not in self._floor_plans:
            return
        self._active_floor_id = fp_id
        self._mode = ToolMode.MOVE_FLOOR_PLAN
        self.setCursor(Qt.SizeAllCursor)

    def start_rotate_floor_plan(self, fp_id: str):
        """Enter mode to rotate a floor plan with the mouse."""
        if fp_id not in self._floor_plans:
            return
        self._active_floor_id = fp_id
        self._mode = ToolMode.ROTATE_FLOOR_PLAN
        self.setCursor(Qt.CrossCursor)

    def start_ref_line(self):
        self._mode  = ToolMode.DRAW_REF
        self._ref_p1 = None
        self._ref_p2 = None
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_drawing(self, circuit_id: str):
        self._mode = ToolMode.DRAW_POLY
        self._current_circuit_id = circuit_id
        self._current_points = []
        self._ensure_color(circuit_id)
        self.setCursor(Qt.CrossCursor)

    def start_draw_floor_plan_polygon(self, fp_id: str):
        """Start drawing a polygon as alternative source for a floor plan layer."""
        if fp_id not in self._floor_plans:
            return
        self._mode = ToolMode.DRAW_FURNITURE_POLY
        self._current_furniture_id = fp_id
        self._current_points = []
        self.setCursor(Qt.CrossCursor)

    def start_route_drawing(self, circuit_id: str,
                            wall_distance_mm: float,
                            line_distance_mm: float):
        start = self._start_points.get(circuit_id)
        poly = self._polygons.get(circuit_id, [])
        if not start or len(poly) < 3:
            return
        scale = max(self._mm_per_px, 1e-9)
        self._route_wall_dist_px[circuit_id] = max(0.0, wall_distance_mm / scale)
        self._route_line_dist_px[circuit_id] = max(0.0, line_distance_mm / scale)
        existing = list(self._manual_routes.get(circuit_id, []))
        if existing:
            self._current_route_points = existing
        else:
            self._current_route_points = [QPointF(start.x(), start.y())]
        self._current_route_cid = circuit_id
        self._mode = ToolMode.DRAW_ROUTE
        self._current_route_preview_end = None
        self._constraint_violation_point = None
        self._constraint_violation_line = None
        self._constraint_violation_reason = ""
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_edit_polygon(self, circuit_id: str):
        if circuit_id not in self._polygons:
            return
        self._edit_floor_polygon_id = None
        self._edit_polygon_cid = circuit_id
        self._mode = ToolMode.EDIT_POLYGON
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_edit_floor_plan_polygon(self, fp_id: str):
        layer = self._floor_plans.get(fp_id)
        if not layer or len(layer.polygon) < 3:
            return
        self._edit_polygon_cid = None
        self._edit_floor_polygon_id = fp_id
        self._mode = ToolMode.EDIT_POLYGON
        self.setCursor(Qt.CrossCursor)
        self.update()

    def _floor_polygon_render_size(self, layer: "FloorPlanLayer") -> Tuple[float, float]:
        """Rendered size for floor polygons. Polygons ignore mm scaling."""
        w, h = layer.size
        return (max(1.0, w), max(1.0, h))

    def _floor_polygon_points_world(self, fp_id: str) -> List[QPointF]:
        """Return polygon points transformed to canvas coordinates."""
        layer = self._floor_plans.get(fp_id)
        if not layer or not layer.polygon:
            return []
        import math
        sw, sh = self._floor_polygon_render_size(layer)
        cx = sw / 2 + layer.offset_x
        cy = sh / 2 + layer.offset_y
        rad = math.radians(layer.rotation)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        out: List[QPointF] = []
        for p in layer.polygon:
            rx = p.x() - sw / 2
            ry = p.y() - sh / 2
            wx = cx + rx * cos_r - ry * sin_r
            wy = cy + rx * sin_r + ry * cos_r
            out.append(QPointF(wx, wy))
        return out

    def _world_to_floor_polygon_local(self, fp_id: str, world_pt: QPointF) -> QPointF:
        layer = self._floor_plans.get(fp_id)
        if not layer:
            return QPointF(world_pt)
        import math
        sw, sh = self._floor_polygon_render_size(layer)
        cx = sw / 2 + layer.offset_x
        cy = sh / 2 + layer.offset_y
        dx = world_pt.x() - cx
        dy = world_pt.y() - cy
        rad = math.radians(-layer.rotation)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        lx = dx * cos_r - dy * sin_r + sw / 2
        ly = dx * sin_r + dy * cos_r + sh / 2
        return QPointF(lx, ly)

    def _hit_floor_polygon_point(self, canvas_pt: QPointF, fp_id: str) -> Optional[int]:
        pts = self._floor_polygon_points_world(fp_id)
        if not pts:
            return None
        threshold = 10.0 / self._scale
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_floor_polygon_edge(self, canvas_pt: QPointF, fp_id: str) -> Optional[Tuple[int, int]]:
        pts = self._floor_polygon_points_world(fp_id)
        if len(pts) < 2:
            return None
        threshold = 8.0 / self._scale
        for i in range(len(pts)):
            p1 = pts[i]
            p2 = pts[(i + 1) % len(pts)]
            proj = _project_on_segment(canvas_pt, p1, p2)
            if _qdist(canvas_pt, proj) < threshold:
                return (i, (i + 1) % len(pts))
        return None

    def _delete_floor_polygon_point(self, fp_id: str, idx: int):
        layer = self._floor_plans.get(fp_id)
        if not layer or len(layer.polygon) <= 3:
            return
        del layer.polygon[idx]
        self.update()

    def _insert_floor_polygon_point(self, fp_id: str, idx1: int, idx2: int, canvas_pt: QPointF):
        layer = self._floor_plans.get(fp_id)
        if not layer or not layer.polygon:
            return
        pts = layer.polygon
        next_idx = (idx1 + 1) % len(pts)
        if idx2 == next_idx:
            pts.insert(next_idx, self._world_to_floor_polygon_local(fp_id, canvas_pt))
            self.update()

    def start_edit_route(self, circuit_id: str):
        if circuit_id not in self._manual_routes:
            return
        self._edit_route_cid = circuit_id
        self._mode = ToolMode.EDIT_ROUTE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def _hit_polygon_point(self, canvas_pt: QPointF, cid: str) -> Optional[int]:
        pts = self._polygons.get(cid, [])
        threshold = 10.0 / self._scale
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_polygon_edge(self, canvas_pt: QPointF, cid: str) -> Optional[Tuple[int, int]]:
        pts = self._polygons.get(cid, [])
        if len(pts) < 2:
            return None
        threshold = 8.0 / self._scale
        for i in range(len(pts)):
            p1 = pts[i]
            p2 = pts[(i + 1) % len(pts)]
            proj = _project_on_segment(canvas_pt, p1, p2)
            if _qdist(canvas_pt, proj) < threshold:
                return (i, (i + 1) % len(pts))
        return None

    def _hit_route_point_in_circuit(self, canvas_pt: QPointF, cid: str) -> Optional[int]:
        pts = self._manual_routes.get(cid, [])
        threshold = 10.0 / self._scale
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_route_edge(self, canvas_pt: QPointF, cid: str) -> Optional[Tuple[int, int]]:
        pts = self._manual_routes.get(cid, [])
        if len(pts) < 2:
            return None
        threshold = 8.0 / self._scale
        for i in range(len(pts) - 1):
            p1 = pts[i]
            p2 = pts[i + 1]
            proj = _project_on_segment(canvas_pt, p1, p2)
            if _qdist(canvas_pt, proj) < threshold:
                return (i, i + 1)
        return None

    def _delete_polygon_point(self, cid: str, idx: int):
        if cid not in self._polygons or len(self._polygons[cid]) <= 3:
            return
        del self._polygons[cid][idx]
        self.polygon_changed.emit(cid)
        self.update()

    def _insert_polygon_point(self, cid: str, idx1: int, idx2: int, pt: QPointF):
        if cid not in self._polygons:
            return
        pts = self._polygons[cid]
        next_idx = (idx1 + 1) % len(pts)
        if idx2 == next_idx:
            pts.insert(next_idx, pt)
            self.polygon_changed.emit(cid)
            self.update()

    def _delete_route_point(self, cid: str, idx: int):
        if cid not in self._manual_routes or len(self._manual_routes[cid]) <= 2:
            return
        del self._manual_routes[cid][idx]
        self.route_changed.emit(cid)
        self.update()

    def _snap_route_point_to_valid(self, cid: str, idx: int):
        """If a route point is in an invalid position, snap it to the nearest valid one."""
        pts = self._manual_routes.get(cid)
        if not pts or idx < 0 or idx >= len(pts):
            return
        current = pts[idx]
        constrained = self._constrain_dragged_route_point(cid, idx, current)
        if _qdist(current, constrained) > 1e-3:
            pts[idx] = constrained
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            self.route_changed.emit(cid)
            self.update()

    def _insert_route_point(self, cid: str, idx1: int, idx2: int, pt: QPointF):
        if cid not in self._manual_routes:
            return
        pts = self._manual_routes[cid]
        if 0 <= idx1 < len(pts) and idx2 == idx1 + 1:
            pts.insert(idx2, pt)
            self.route_changed.emit(cid)
            self.update()

    def set_color(self, circuit_id: str, color: QColor):
        self._color_map[circuit_id] = color
        self.update()

    def set_polygon_name(self, circuit_id: str, name: str):
        self._label_map[circuit_id] = name
        self.update()

    def set_helper_line(self, circuit_id: str, points: List[Point]):
        self._helper_lines[circuit_id] = [QPointF(x, y) for x, y in points]
        self.update()

    def set_show_helper_line(self, circuit_id: str, show: bool):
        self._show_helper_line[circuit_id] = show
        self.update()

    def set_show_ref_line(self, show: bool):
        self._show_ref_line = show
        self.update()

    def set_label_font_size(self, item_id: str, size: float):
        self._label_font_sizes[item_id] = size
        self.update()

    def delete_circuit(self, circuit_id: str):
        for d in (self._polygons, self._color_map, self._start_points,
                  self._label_map, self._helper_lines, self._show_helper_line,
                  self._manual_routes, self._route_wall_dist_px,
                  self._route_line_dist_px, self._supply_lines,
                  self._label_positions, self._label_font_sizes,
                  self._label_rects, self._label_draw_pos):
            d.pop(circuit_id, None)
        self.update()

    # ── Supply Line (Anschlussleitung) API ──────────────────────────── #

    supply_line_changed = Signal(str)

    def start_draw_supply_line(self, circuit_id: str):
        """Start drawing a supply line from S outward."""
        start = self._start_points.get(circuit_id)
        if not start:
            return
        existing = list(self._supply_lines.get(circuit_id, []))
        if existing:
            self._current_supply_points = existing
        else:
            self._current_supply_points = [QPointF(start.x(), start.y())]
        self._current_supply_cid = circuit_id
        self._current_supply_preview = None
        self._mode = ToolMode.DRAW_SUPPLY_LINE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_edit_supply_line(self, circuit_id: str):
        if circuit_id not in self._supply_lines:
            return
        self._edit_supply_cid = circuit_id
        self._mode = ToolMode.EDIT_SUPPLY_LINE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def get_supply_line_length_px(self, circuit_id: str) -> float:
        """Total supply pipe length: both parallel lines + connector."""
        pts = self._supply_lines.get(circuit_id, [])
        if len(pts) < 2:
            return 0.0
        line_dist = self._route_line_dist_px.get(circuit_id, 0.0)
        offset = line_dist / 2.0

        line1 = self._offset_route_points(pts, offset)
        line2 = self._offset_route_points(pts, -offset)

        total = 0.0
        for i in range(len(line1) - 1):
            total += _qdist(line1[i], line1[i + 1])
        for i in range(len(line2) - 1):
            total += _qdist(line2[i], line2[i + 1])
        if line1 and line2:
            total += _qdist(line1[-1], line2[-1])
        return total

    def _hit_supply_line_point(self, canvas_pt: QPointF,
                                cid: str) -> Optional[int]:
        pts = self._supply_lines.get(cid, [])
        threshold = 10.0 / self._scale
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_supply_line_edge(self, canvas_pt: QPointF,
                               cid: str) -> Optional[Tuple[int, int]]:
        pts = self._supply_lines.get(cid, [])
        if len(pts) < 2:
            return None
        threshold = 8.0 / self._scale
        for i in range(len(pts) - 1):
            proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
            if _qdist(canvas_pt, proj) < threshold:
                return (i, i + 1)
        return None

    # ── Elektro API ────────────────────────────────────────────────── #

    def start_place_elec_point(self, point_id: str,
                                width_mm: float, height_mm: float):
        scale = max(self._mm_per_px, 1e-9)
        self._elec_point_size_px[point_id] = (width_mm / scale, height_mm / scale)
        if point_id not in self._elec_point_icons:
            self._elec_point_icons[point_id] = None
        if point_id not in self._elec_point_svgs:
            self._elec_point_svgs[point_id] = None
        self._elec_visible.setdefault(point_id, True)
        self._ensure_color(point_id)
        self._placing_elec_point_id = point_id
        self._mode = ToolMode.PLACE_ELEC_POINT
        self.setCursor(Qt.CrossCursor)
        self.update()

    def update_elec_point_size(self, point_id: str,
                                width_mm: float, height_mm: float):
        scale = max(self._mm_per_px, 1e-9)
        self._elec_point_size_px[point_id] = (width_mm / scale, height_mm / scale)
        self.update()

    def set_elec_point_icon(self, point_id: str, path: str):
        if path and path.lower().endswith(".svg"):
            renderer = QSvgRenderer(path)
            if renderer.isValid():
                self._elec_point_svgs[point_id] = renderer
                self._elec_point_icons[point_id] = None
            else:
                self._elec_point_svgs[point_id] = None
                self._elec_point_icons[point_id] = None
        elif path:
            pm = QPixmap(path)
            self._elec_point_icons[point_id] = pm if not pm.isNull() else None
            self._elec_point_svgs[point_id] = None
        else:
            self._elec_point_icons[point_id] = None
            self._elec_point_svgs[point_id] = None
        self.update()

    def start_draw_elec_cable(self, cable_id: str):
        self._ensure_color(cable_id)
        self._elec_visible.setdefault(cable_id, True)
        existing = self._elec_cables.get(cable_id, [])
        self._current_elec_cable_points = list(existing) if existing else []
        self._current_elec_cable_id = cable_id
        self._current_elec_cable_preview = None
        self._mode = ToolMode.DRAW_ELEC_CABLE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_edit_elec_cable(self, cable_id: str):
        if cable_id not in self._elec_cables:
            return
        self._edit_elec_cable_id = cable_id
        self._mode = ToolMode.EDIT_ELEC_CABLE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def delete_elec_point(self, point_id: str):
        for d in (self._elec_points, self._elec_point_size_px,
                  self._elec_point_icons, self._elec_visible,
                  self._elec_point_position, self._elec_point_height,
                  self._label_positions, self._label_font_sizes,
                  self._label_rects, self._label_draw_pos):
            d.pop(point_id, None)
        self._color_map.pop(point_id, None)
        self.update()

    def delete_elec_cable(self, cable_id: str):
        for d in (self._elec_cables, self._elec_visible,
                  self._cable_start_ap, self._cable_end_ap,
                  self._label_positions, self._label_font_sizes,
                  self._label_rects, self._label_draw_pos):
            d.pop(cable_id, None)
        self._color_map.pop(cable_id, None)
        self.update()

    def get_elec_cable_length_px(self, cable_id: str) -> float:
        pts = self._elec_cables.get(cable_id, [])
        if len(pts) < 2:
            return 0.0
        total = 0.0
        for i in range(len(pts) - 1):
            total += _qdist(pts[i], pts[i + 1])
        return total

    def _hit_elec_point(self, canvas_pt: QPointF) -> Optional[str]:
        for pid, pos in self._elec_points.items():
            if not self._elec_visible.get(pid, True):
                continue
            w, h = self._elec_point_size_px.get(pid, (30, 30))
            rect = QRectF(pos.x() - w / 2, pos.y() - h / 2, w, h)
            if rect.contains(canvas_pt):
                return pid
        return None

    def _hit_elec_cable_point(self, canvas_pt: QPointF,
                               cable_id: str) -> Optional[int]:
        pts = self._elec_cables.get(cable_id, [])
        threshold = 10.0 / self._scale
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_elec_cable_edge(self, canvas_pt: QPointF,
                              cable_id: str) -> Optional[Tuple[int, int]]:
        pts = self._elec_cables.get(cable_id, [])
        if len(pts) < 2:
            return None
        threshold = 8.0 / self._scale
        for i in range(len(pts) - 1):
            proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
            if _qdist(canvas_pt, proj) < threshold:
                return (i, i + 1)
        return None

    def _apply_angle_snap_elec(self, target: QPointF) -> QPointF:
        if self._snap_angle <= 0 or not self._current_elec_cable_points:
            return target
        anchor = self._current_elec_cable_points[-1]
        dx = target.x() - anchor.x()
        dy = target.y() - anchor.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return target
        angle_deg = math.degrees(math.atan2(dy, dx))
        step = self._snap_angle
        snapped_angle = round(angle_deg / step) * step
        diff = abs(angle_deg - snapped_angle)
        if diff > 8.0:
            return target
        rad = math.radians(snapped_angle)
        return QPointF(anchor.x() + math.cos(rad) * dist,
                       anchor.y() + math.sin(rad) * dist)

    def _find_nearest_ap(self, canvas_pt: QPointF,
                         threshold_px: float = 20.0) -> str | None:
        """Return the point_id of the nearest visible AP within *threshold_px*
        (in screen pixels), or None."""
        best_id: str | None = None
        best_d = threshold_px / self._scale
        for pid, pos in self._elec_points.items():
            if not self._elec_visible.get(pid, True):
                continue
            d = _qdist(canvas_pt, pos)
            if d < best_d:
                best_d = d
                best_id = pid
        return best_id

    def get_cable_ap(self, cable_id: str) -> tuple[str, str]:
        """Return (start_ap, end_ap) for the given cable."""
        return (self._cable_start_ap.get(cable_id, ""),
                self._cable_end_ap.get(cable_id, ""))

    def _apply_angle_snap_supply(self, target: QPointF) -> QPointF:
        if self._snap_angle <= 0 or not self._current_supply_points:
            return target
        anchor = self._current_supply_points[-1]
        dx = target.x() - anchor.x()
        dy = target.y() - anchor.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return target
        angle_deg = math.degrees(math.atan2(dy, dx))
        step = self._snap_angle
        snapped_angle = round(angle_deg / step) * step
        diff = abs(angle_deg - snapped_angle)
        if diff > 8.0:
            return target
        rad = math.radians(snapped_angle)
        return QPointF(anchor.x() + math.cos(rad) * dist,
                       anchor.y() + math.sin(rad) * dist)

    # ── HKV (Heizkreisverteiler) API ────────────────────────────────── #

    def start_place_hkv(self, hkv_id: str,
                        width_mm: float, height_mm: float):
        scale = max(self._mm_per_px, 1e-9)
        self._hkv_size_px[hkv_id] = (width_mm / scale, height_mm / scale)
        if hkv_id not in self._hkv_icons:
            self._hkv_icons[hkv_id] = None
        if hkv_id not in self._hkv_svgs:
            self._hkv_svgs[hkv_id] = None
        self._hkv_visible.setdefault(hkv_id, True)
        self._ensure_color(hkv_id)
        self._placing_hkv_id = hkv_id
        self._mode = ToolMode.PLACE_HKV
        self.setCursor(Qt.CrossCursor)
        self.update()

    def update_hkv_size(self, hkv_id: str,
                        width_mm: float, height_mm: float):
        scale = max(self._mm_per_px, 1e-9)
        self._hkv_size_px[hkv_id] = (width_mm / scale, height_mm / scale)
        self.update()

    def set_hkv_icon(self, hkv_id: str, path: str):
        if path and path.lower().endswith(".svg"):
            renderer = QSvgRenderer(path)
            if renderer.isValid():
                self._hkv_svgs[hkv_id] = renderer
                self._hkv_icons[hkv_id] = None
            else:
                self._hkv_svgs[hkv_id] = None
                self._hkv_icons[hkv_id] = None
        elif path:
            pm = QPixmap(path)
            self._hkv_icons[hkv_id] = pm if not pm.isNull() else None
            self._hkv_svgs[hkv_id] = None
        else:
            self._hkv_icons[hkv_id] = None
            self._hkv_svgs[hkv_id] = None
        self.update()

    def delete_hkv(self, hkv_id: str):
        for d in (self._hkv_points, self._hkv_size_px, self._hkv_icons,
                  self._hkv_svgs, self._hkv_visible,
                  self._label_positions, self._label_font_sizes,
                  self._label_rects, self._label_draw_pos):
            d.pop(hkv_id, None)
        self._color_map.pop(hkv_id, None)
        # Remove supply→HKV links that reference this HKV
        for cid in list(self._supply_hkv):
            if self._supply_hkv[cid] == hkv_id:
                del self._supply_hkv[cid]
        # Remove HKV line links
        for lid in list(self._hkv_line_start):
            if self._hkv_line_start[lid] == hkv_id:
                del self._hkv_line_start[lid]
        for lid in list(self._hkv_line_end):
            if self._hkv_line_end[lid] == hkv_id:
                del self._hkv_line_end[lid]
        self.update()

    def _hit_hkv(self, canvas_pt: QPointF) -> Optional[str]:
        for hid, pos in self._hkv_points.items():
            if not self._hkv_visible.get(hid, True):
                continue
            w, h = self._hkv_size_px.get(hid, (30, 30))
            rect = QRectF(pos.x() - w / 2, pos.y() - h / 2, w, h)
            if rect.contains(canvas_pt):
                return hid
        return None

    def _find_nearest_hkv(self, canvas_pt: QPointF,
                          threshold_px: float = 20.0) -> str | None:
        """Return nearest visible HKV within threshold (screen px)."""
        best_id: str | None = None
        best_d = threshold_px / self._scale
        for hid, pos in self._hkv_points.items():
            if not self._hkv_visible.get(hid, True):
                continue
            d = _qdist(canvas_pt, pos)
            if d < best_d:
                best_d = d
                best_id = hid
        return best_id

    def get_supply_hkv(self, circuit_id: str) -> str:
        """Return the HKV id the supply line of *circuit_id* is connected to."""
        return self._supply_hkv.get(circuit_id, "")

    # ── HKV Lines (Verbindungsleitungen) API ────────────────────────── #

    def start_draw_hkv_line(self, line_id: str):
        self._ensure_color(line_id)
        self._hkv_line_visible.setdefault(line_id, True)
        existing = self._hkv_lines.get(line_id, [])
        self._current_hkv_line_points = list(existing) if existing else []
        self._current_hkv_line_id = line_id
        self._current_hkv_line_preview = None
        self._mode = ToolMode.DRAW_HKV_LINE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_edit_hkv_line(self, line_id: str):
        if line_id not in self._hkv_lines:
            return
        self._edit_hkv_line_id = line_id
        self._mode = ToolMode.EDIT_HKV_LINE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def delete_hkv_line(self, line_id: str):
        for d in (self._hkv_lines, self._hkv_line_start,
                  self._hkv_line_end, self._hkv_line_visible,
                  self._label_positions, self._label_font_sizes,
                  self._label_rects, self._label_draw_pos):
            d.pop(line_id, None)
        self._color_map.pop(line_id, None)
        self.update()

    def get_hkv_line_length_px(self, line_id: str) -> float:
        """Total length of an HKV connecting line (double pipe)."""
        pts = self._hkv_lines.get(line_id, [])
        if len(pts) < 2:
            return 0.0
        total = 0.0
        for i in range(len(pts) - 1):
            total += _qdist(pts[i], pts[i + 1])
        # Double-pipe → x2 plus connector
        return total * 2.0

    def get_hkv_line_ap(self, line_id: str) -> tuple[str, str]:
        return (self._hkv_line_start.get(line_id, ""),
                self._hkv_line_end.get(line_id, ""))

    def _hit_hkv_line_point(self, canvas_pt: QPointF,
                             line_id: str) -> Optional[int]:
        pts = self._hkv_lines.get(line_id, [])
        threshold = 10.0 / self._scale
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_hkv_line_edge(self, canvas_pt: QPointF,
                            line_id: str) -> Optional[Tuple[int, int]]:
        pts = self._hkv_lines.get(line_id, [])
        if len(pts) < 2:
            return None
        threshold = 8.0 / self._scale
        for i in range(len(pts) - 1):
            proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
            if _qdist(canvas_pt, proj) < threshold:
                return (i, i + 1)
        return None

    def _apply_angle_snap_hkv_line(self, target: QPointF) -> QPointF:
        if self._snap_angle <= 0 or not self._current_hkv_line_points:
            return target
        anchor = self._current_hkv_line_points[-1]
        dx = target.x() - anchor.x()
        dy = target.y() - anchor.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return target
        angle_deg = math.degrees(math.atan2(dy, dx))
        step = self._snap_angle
        snapped_angle = round(angle_deg / step) * step
        diff = abs(angle_deg - snapped_angle)
        if diff > 8.0:
            return target
        rad = math.radians(snapped_angle)
        return QPointF(anchor.x() + math.cos(rad) * dist,
                       anchor.y() + math.sin(rad) * dist)

    def get_polygon_px(self, circuit_id: str) -> List[Tuple[float, float]]:
        return [(p.x(), p.y()) for p in self._polygons.get(circuit_id, [])]

    # ── Text Annotations API ────────────────────────────────────────── #

    def start_place_text(self, text_id: str, content: str = "Text",
                         font_size: float = 14.0, color: str = "#ffffff"):
        """Enter text placement mode: next click places the annotation."""
        self._text_contents[text_id] = content
        self._text_font_sizes[text_id] = font_size
        self._text_colors[text_id] = color
        self._text_visible.setdefault(text_id, True)
        self._placing_text_id = text_id
        self._mode = ToolMode.PLACE_TEXT
        self.setCursor(Qt.CrossCursor)
        self.update()

    def update_text_content(self, text_id: str, content: str):
        self._text_contents[text_id] = content
        self.update()

    def update_text_font_size(self, text_id: str, size: float):
        self._text_font_sizes[text_id] = size
        self.update()

    def update_text_color(self, text_id: str, color: str):
        self._text_colors[text_id] = color
        self.update()

    def update_text_comment(self, text_id: str, comment: str):
        self._text_comments[text_id] = comment

    def set_text_visible(self, text_id: str, visible: bool):
        self._text_visible[text_id] = visible
        self.update()

    def delete_text_annotation(self, text_id: str):
        for d in (self._text_annotations, self._text_contents,
                  self._text_font_sizes, self._text_colors,
                  self._text_comments, self._text_visible, self._text_rects):
            d.pop(text_id, None)
        self.update()

    def _hit_text_annotation(self, canvas_pt: QPointF) -> Optional[str]:
        """Return the id of a text annotation hit at canvas_pt."""
        for tid, rect in self._text_rects.items():
            if rect.contains(canvas_pt) and self._text_visible.get(tid, True):
                return tid
        return None

    def get_start_point_px(self, circuit_id: str) -> Optional[Tuple[float, float]]:
        sp = self._start_points.get(circuit_id)
        return (sp.x(), sp.y()) if sp else None

    def get_mm_per_px(self) -> float:
        return self._mm_per_px

    def get_manual_route_length_px(self, circuit_id: str) -> float:
        """Total pipe length: both parallel lines + semicircle at the end."""
        pts = self._manual_routes.get(circuit_id, [])
        if len(pts) < 2:
            return 0.0
        line_dist = self._route_line_dist_px.get(circuit_id, 0.0)
        offset = line_dist / 2.0

        line1 = self._offset_route_points(pts, offset)
        line2 = self._offset_route_points(pts, -offset)

        total = 0.0
        for i in range(len(line1) - 1):
            total += _qdist(line1[i], line1[i + 1])
        for i in range(len(line2) - 1):
            total += _qdist(line2[i], line2[i + 1])
        # Connector at end (straight line between endpoints)
        if line1 and line2:
            total += _qdist(line1[-1], line2[-1])
        return total

    def set_mm_per_px(self, value: float):
        self._mm_per_px = value
        self.update()

    def clear_data(self):
        """Clear all geometric and object data (keeps SVG, zoom and grid settings)."""
        self._floor_plans.clear()
        self._floor_plan_order.clear()
        self._ref_floor_id = None
        self._polygons.clear()
        self._start_points.clear()
        self._color_map.clear()
        self._label_map.clear()
        self._helper_lines.clear()
        self._show_helper_line.clear()
        self._manual_routes.clear()
        self._route_wall_dist_px.clear()
        self._route_line_dist_px.clear()
        self._circuit_visible.clear()
        self._supply_lines.clear()
        self._elec_points.clear()
        self._elec_point_size_px.clear()
        self._elec_point_icons.clear()
        self._elec_point_svgs.clear()
        self._elec_cables.clear()
        self._elec_visible.clear()
        self._cable_start_ap.clear()
        self._cable_end_ap.clear()
        self._hkv_points.clear()
        self._hkv_size_px.clear()
        self._hkv_icons.clear()
        self._hkv_svgs.clear()
        self._hkv_visible.clear()
        self._supply_hkv.clear()
        self._hkv_lines.clear()
        self._hkv_line_start.clear()
        self._hkv_line_end.clear()
        self._hkv_line_visible.clear()
        self._text_annotations.clear()
        self._text_contents.clear()
        self._text_font_sizes.clear()
        self._text_colors.clear()
        self._text_comments.clear()
        self._text_visible.clear()
        self._text_rects.clear()
        self._label_positions.clear()
        self._label_font_sizes.clear()
        self._label_rects.clear()
        self._ref_p1 = None
        self._ref_p2 = None
        self.update()

    def to_dict(self) -> dict:
        result = {
            "view_scale": self._scale,
            "view_offset": [self._offset.x(), self._offset.y()],
            "bg_color": self._bg_color.name(),
            "grid_visible": self._grid_visible,
            "grid_spacing_mm": self._grid_spacing_mm,
            "grid_color": [self._grid_color.red(), self._grid_color.green(),
                           self._grid_color.blue(), self._grid_color.alpha()],
            "snap_angle": self._snap_angle,
            "export_frame": None,
            "polygons": {
                cid: [(p.x(), p.y()) for p in pts]
                for cid, pts in self._polygons.items()
            },
            "start_points": {
                cid: (p.x(), p.y())
                for cid, p in self._start_points.items()
            },
            "ref_line":  None,
            "mm_per_px": self._mm_per_px,
            "manual_routes": {
                cid: [(p.x(), p.y()) for p in pts]
                for cid, pts in self._manual_routes.items()
            },
            "route_wall_dist_px": self._route_wall_dist_px,
            "route_line_dist_px": self._route_line_dist_px,
            "supply_lines": {
                cid: [(p.x(), p.y()) for p in pts]
                for cid, pts in self._supply_lines.items()
            },
            "elec_points": {
                pid: (p.x(), p.y())
                for pid, p in self._elec_points.items()
            },
            "elec_point_size_px": {
                pid: list(s)
                for pid, s in self._elec_point_size_px.items()
            },
            "elec_point_position": dict(self._elec_point_position),
            "elec_point_height": dict(self._elec_point_height),
            "elec_cables": {
                cid: [(p.x(), p.y()) for p in pts]
                for cid, pts in self._elec_cables.items()
            },
            "cable_start_ap": dict(self._cable_start_ap),
            "cable_end_ap": dict(self._cable_end_ap),
            "elec_visible": dict(self._elec_visible),
            "hkv_points": {
                hid: (p.x(), p.y())
                for hid, p in self._hkv_points.items()
            },
            "hkv_size_px": {
                hid: list(s)
                for hid, s in self._hkv_size_px.items()
            },
            "hkv_visible": dict(self._hkv_visible),
            "supply_hkv": dict(self._supply_hkv),
            "hkv_lines": {
                lid: [(p.x(), p.y()) for p in pts]
                for lid, pts in self._hkv_lines.items()
            },
            "hkv_line_start": dict(self._hkv_line_start),
            "hkv_line_end": dict(self._hkv_line_end),
            "hkv_line_visible": dict(self._hkv_line_visible),
            "label_positions": {
                k: (p.x(), p.y())
                for k, p in self._label_positions.items()
            },
            "label_font_sizes": dict(self._label_font_sizes),
            "text_annotations": {
                tid: {
                    "pos": (p.x(), p.y()),
                    "content": self._text_contents.get(tid, ""),
                    "font_size": self._text_font_sizes.get(tid, 14.0),
                    "color": self._text_colors.get(tid, "#ffffff"),
                    "comment": self._text_comments.get(tid, ""),
                    "visible": self._text_visible.get(tid, True),
                }
                for tid, p in self._text_annotations.items()
            },
        }
        if self._export_frame:
            r = self._export_frame.normalized()
            result["export_frame"] = [r.x(), r.y(), r.width(), r.height()]
        if self._ref_p1 and self._ref_p2:
            result["ref_line"] = [
                (self._ref_p1.x(), self._ref_p1.y()),
                (self._ref_p2.x(), self._ref_p2.y()),
            ]
        # Floor plan layers
        fp_list = []
        for fid in self._floor_plan_order:
            layer = self._floor_plans.get(fid)
            if not layer:
                continue
            fp_d: dict = {
                "fp_id": fid,
                "offset_x": layer.offset_x,
                "offset_y": layer.offset_y,
                "rotation": layer.rotation,
                "opacity": layer.opacity,
                "visible": layer.visible,
                "mm_per_px": layer.mm_per_px,
                "ref_length_mm": layer.ref_length_mm,
                "fixed_width_mm": layer.fixed_width_mm,
                "fixed_height_mm": layer.fixed_height_mm,
                "polygon_color": layer.polygon_color,
            }
            if layer.ref_p1 and layer.ref_p2:
                fp_d["ref_line"] = [
                    (layer.ref_p1.x(), layer.ref_p1.y()),
                    (layer.ref_p2.x(), layer.ref_p2.y()),
                ]
            if layer.polygon:
                fp_d["polygon"] = [(p.x(), p.y()) for p in layer.polygon]
            fp_list.append(fp_d)
        result["floor_plans"] = fp_list
        return result

    def from_dict(self, d: dict):
        # Restore zoom & pan
        if "view_scale" in d:
            self._scale = float(d["view_scale"])
            # Clamp restored scale to limits
            self._scale = max(self._scale_min, min(self._scale_max, self._scale))
        if "view_offset" in d:
            ox, oy = d["view_offset"]
            self._offset = QPointF(float(ox), float(oy))

        # Restore UI settings
        if "bg_color" in d:
            self._bg_color = QColor(d["bg_color"])
        if "grid_visible" in d:
            self._grid_visible = bool(d["grid_visible"])
        if "grid_spacing_mm" in d:
            self._grid_spacing_mm = float(d["grid_spacing_mm"])
        if "grid_color" in d:
            gc = d["grid_color"]
            self._grid_color = QColor(gc[0], gc[1], gc[2], gc[3])
        if "snap_angle" in d:
            self._snap_angle = float(d["snap_angle"])
        ef = d.get("export_frame")
        if ef and len(ef) == 4:
            self._export_frame = QRectF(float(ef[0]), float(ef[1]),
                                        float(ef[2]), float(ef[3])).normalized()
        else:
            self._export_frame = None

        for cid, pts in d.get("polygons", {}).items():
            self._polygons[cid] = [QPointF(x, y) for x, y in pts]
            self._ensure_color(cid)
        for cid, pt in d.get("start_points", {}).items():
            self._start_points[cid] = QPointF(pt[0], pt[1])
        ref = d.get("ref_line")
        if ref:
            self._ref_p1 = QPointF(*ref[0])
            self._ref_p2 = QPointF(*ref[1])
        self._mm_per_px = d.get("mm_per_px", 1.0)
        for cid, pts in d.get("manual_routes", {}).items():
            self._manual_routes[cid] = [QPointF(x, y) for x, y in pts]
        self._route_wall_dist_px = {
            cid: float(v) for cid, v in d.get("route_wall_dist_px", {}).items()
        }
        self._route_line_dist_px = {
            cid: float(v) for cid, v in d.get("route_line_dist_px", {}).items()
        }
        for cid, pts in d.get("supply_lines", {}).items():
            self._supply_lines[cid] = [QPointF(x, y) for x, y in pts]
        for pid, pt in d.get("elec_points", {}).items():
            self._elec_points[pid] = QPointF(pt[0], pt[1])
        for pid, s in d.get("elec_point_size_px", {}).items():
            self._elec_point_size_px[pid] = tuple(s)
        self._elec_point_position = dict(d.get("elec_point_position", {}))
        self._elec_point_height = {
            pid: float(h) for pid, h in d.get("elec_point_height", {}).items()
        }
        for cid, pts in d.get("elec_cables", {}).items():
            self._elec_cables[cid] = [QPointF(x, y) for x, y in pts]
            self._ensure_color(cid)
        self._cable_start_ap = dict(d.get("cable_start_ap", {}))
        self._cable_end_ap = dict(d.get("cable_end_ap", {}))
        self._elec_visible = {
            k: bool(v) for k, v in d.get("elec_visible", {}).items()
        }
        # HKV
        for hid, pt in d.get("hkv_points", {}).items():
            self._hkv_points[hid] = QPointF(pt[0], pt[1])
        for hid, s in d.get("hkv_size_px", {}).items():
            self._hkv_size_px[hid] = tuple(s)
        self._hkv_visible = {
            k: bool(v) for k, v in d.get("hkv_visible", {}).items()
        }
        self._supply_hkv = dict(d.get("supply_hkv", {}))
        for lid, pts in d.get("hkv_lines", {}).items():
            self._hkv_lines[lid] = [QPointF(x, y) for x, y in pts]
            self._ensure_color(lid)
        self._hkv_line_start = dict(d.get("hkv_line_start", {}))
        self._hkv_line_end = dict(d.get("hkv_line_end", {}))
        self._hkv_line_visible = {
            k: bool(v) for k, v in d.get("hkv_line_visible", {}).items()
        }
        for k, pt in d.get("label_positions", {}).items():
            self._label_positions[k] = QPointF(pt[0], pt[1])
        self._label_font_sizes.update(d.get("label_font_sizes", {}))
        # Text annotations
        for tid, tdata in d.get("text_annotations", {}).items():
            pos = tdata.get("pos", (0, 0))
            self._text_annotations[tid] = QPointF(pos[0], pos[1])
            self._text_contents[tid] = tdata.get("content", "")
            self._text_font_sizes[tid] = tdata.get("font_size", 14.0)
            self._text_colors[tid] = tdata.get("color", "#ffffff")
            self._text_comments[tid] = tdata.get("comment", "")
            self._text_visible[tid] = tdata.get("visible", True)
        # Floor plan layers (geometry only – images are loaded by main_window)
        for fp_d in d.get("floor_plans", []):
            fid = fp_d.get("fp_id")
            if not fid:
                continue
            layer = self._floor_plans.get(fid)
            if not layer:
                layer = self.add_floor_plan(fid)
            layer.offset_x = fp_d.get("offset_x", 0.0)
            layer.offset_y = fp_d.get("offset_y", 0.0)
            layer.rotation = fp_d.get("rotation", 0.0)
            layer.opacity = fp_d.get("opacity", 1.0)
            layer.visible = fp_d.get("visible", True)
            layer.mm_per_px = fp_d.get("mm_per_px", 1.0)
            layer.ref_length_mm = fp_d.get("ref_length_mm", 1000.0)
            layer.fixed_width_mm = fp_d.get("fixed_width_mm", 0.0)
            layer.fixed_height_mm = fp_d.get("fixed_height_mm", 0.0)
            layer.polygon_color = fp_d.get("polygon_color", "#8d99ae")
            ref = fp_d.get("ref_line")
            if ref:
                layer.ref_p1 = QPointF(*ref[0])
                layer.ref_p2 = QPointF(*ref[1])
            poly = fp_d.get("polygon", [])
            layer.polygon = [QPointF(x, y) for x, y in poly]
        self.update()

    # ------------------------------------------------------------------ #
    #  Koordinaten                                                         #
    # ------------------------------------------------------------------ #

    def _to_canvas(self, screen: QPointF) -> QPointF:
        return QPointF(
            (screen.x() - self._offset.x()) / self._scale,
            (screen.y() - self._offset.y()) / self._scale,
        )

    def _fit_to_window(self):
        w, h = self._svg_size
        if w <= 0 or h <= 0:
            return
        sx = self.width()  / w
        sy = self.height() / h
        calculated_scale = min(sx, sy) * 0.95
        # Clamp to zoom limits
        self._scale = max(self._scale_min, min(self._scale_max, calculated_scale))
        self._offset = QPointF(
            (self.width()  - w * self._scale) / 2,
            (self.height() - h * self._scale) / 2,
        )

    def _snap_to_polygon_edge(self, circuit_id: str,
                               pt: QPointF) -> QPointF:
        pts = self._polygons.get(circuit_id)
        if not pts or len(pts) < 2:
            return pt
        best, best_d = pt, float("inf")
        n = len(pts)
        for i in range(n):
            proj = _project_on_segment(pt, pts[i], pts[(i + 1) % n])
            d = _qdist(pt, proj)
            if d < best_d:
                best_d, best = d, proj
        return best

    def _ensure_color(self, cid: str):
        if cid not in self._color_map:
            self._color_map[cid] = QColor(COLORS[self._color_index % len(COLORS)])
            self._color_index += 1

    def _hit_start_point(self, canvas_pt: QPointF) -> Optional[str]:
        threshold = 10.0 / self._scale
        for cid, sp in self._start_points.items():
            if _qdist(canvas_pt, sp) < threshold:
                return cid
        return None

    def _hit_route_point(self, canvas_pt: QPointF) -> Optional[Tuple[str, int]]:
        threshold = 10.0 / self._scale
        for cid, pts in self._manual_routes.items():
            for i, pt in enumerate(pts):
                if _qdist(canvas_pt, pt) < threshold:
                    return cid, i
        return None

    def _point_in_polygon(self, point: QPointF, polygon: List[QPointF]) -> bool:
        return QPolygonF(polygon).containsPoint(point, Qt.OddEvenFill)

    def _min_dist_to_polygon_edge(self, point: QPointF, polygon: List[QPointF]) -> float:
        if len(polygon) < 2:
            return 0.0
        best = float("inf")
        for i in range(len(polygon)):
            a = polygon[i]
            b = polygon[(i + 1) % len(polygon)]
            proj = _project_on_segment(point, a, b)
            best = min(best, _qdist(point, proj))
        return best

    def _route_segments(self, cid: str, include_current: bool = False) -> List[Tuple[QPointF, QPointF, int]]:
        points = list(self._manual_routes.get(cid, []))
        if include_current and self._mode == ToolMode.DRAW_ROUTE and self._current_route_cid == cid:
            points = list(self._current_route_points)
        segs: List[Tuple[QPointF, QPointF, int]] = []
        for i in range(len(points) - 1):
            segs.append((points[i], points[i + 1], i))
        return segs

    def _nearest_polygon_edge_segment(self, point: QPointF,
                                      polygon: List[QPointF]) -> Optional[Tuple[QPointF, QPointF]]:
        if len(polygon) < 2:
            return None
        best_pair: Optional[Tuple[QPointF, QPointF]] = None
        best_dist = float("inf")
        for i in range(len(polygon)):
            a = polygon[i]
            b = polygon[(i + 1) % len(polygon)]
            proj = _project_on_segment(point, a, b)
            d = _qdist(point, proj)
            if d < best_dist:
                best_dist = d
                best_pair = (a, b)
        return best_pair

    def _find_route_conflict_line(self, cid: str, a: QPointF, b: QPointF,
                                  ignore_segment_indices: Optional[set] = None,
                                  allow_start_on_boundary: bool = False) -> Optional[Tuple[Tuple[QPointF, QPointF], str]]:
        """Unified collision check for dual parallel pipes.
        Returns (conflict_line, reason) or None.
        """
        polygon = self._polygons.get(cid, [])
        if len(polygon) < 3 or _qdist(a, b) < 1e-6:
            return None

        wall_dist = self._route_wall_dist_px.get(cid, 0.0)
        line_dist = self._route_line_dist_px.get(cid, 0.0)
        offset = line_dist / 2.0
        seg_len = _qdist(a, b)
        samples = max(8, int(seg_len / 10.0))

        # Normal vector for offset calculation
        direction = QPointF(b.x() - a.x(), b.y() - a.y())
        length = math.hypot(direction.x(), direction.y())
        if length < 1e-6:
            return None
        normal = QPointF(-direction.y() / length, direction.x() / length)

        wall_dist_cm = wall_dist * self._mm_per_px / 10

        for i in range(samples + 1):
            t = i / samples
            p_center = QPointF(a.x() + (b.x() - a.x()) * t,
                               a.y() + (b.y() - a.y()) * t)
            
            # Both offset points
            p_left  = QPointF(p_center.x() + normal.x() * offset,
                              p_center.y() + normal.y() * offset)
            p_right = QPointF(p_center.x() - normal.x() * offset,
                              p_center.y() - normal.y() * offset)

            if allow_start_on_boundary and i == 0:
                continue

            # Check polygon containment for both offset lines
            if not self._point_in_polygon(p_left, polygon):
                seg = self._nearest_polygon_edge_segment(p_left, polygon)
                return (seg, "Außerhalb Polygon") if seg else None
            if not self._point_in_polygon(p_right, polygon):
                seg = self._nearest_polygon_edge_segment(p_right, polygon)
                return (seg, "Außerhalb Polygon") if seg else None

            # Check wall distance for both offset lines
            if wall_dist > 0.0:
                d_left = self._min_dist_to_polygon_edge(p_left, polygon)
                if d_left + 1e-6 < wall_dist:
                    seg = self._nearest_polygon_edge_segment(p_left, polygon)
                    actual_cm = d_left * self._mm_per_px / 10
                    return (seg, f"Randabstand {actual_cm:.1f}/{wall_dist_cm:.1f} cm") if seg else None
                d_right = self._min_dist_to_polygon_edge(p_right, polygon)
                if d_right + 1e-6 < wall_dist:
                    seg = self._nearest_polygon_edge_segment(p_right, polygon)
                    actual_cm = d_right * self._mm_per_px / 10
                    return (seg, f"Randabstand {actual_cm:.1f}/{wall_dist_cm:.1f} cm") if seg else None

        # Check inter-segment distance: center-to-center >= 2*line_dist
        if line_dist <= 0.0:
            return None

        min_center_dist = 2.0 * line_dist
        line_dist_cm = line_dist * self._mm_per_px / 10
        ignore = ignore_segment_indices or set()
        for s0, s1, seg_idx in self._route_segments(cid, include_current=True):
            if seg_idx in ignore:
                continue
            if (
                _qdist(a, s0) < 1e-6 or _qdist(a, s1) < 1e-6 or
                _qdist(b, s0) < 1e-6 or _qdist(b, s1) < 1e-6
            ):
                continue
            d = _segment_distance(a, b, s0, s1)
            if d + 1e-6 < min_center_dist:
                actual_cm = d * self._mm_per_px / 10
                return ((s0, s1), f"Verlegeabstand {actual_cm:.1f}/{line_dist_cm:.1f} cm")
        return None

    def _is_valid_route_segment(self, cid: str, a: QPointF, b: QPointF,
                                ignore_segment_indices: Optional[set] = None,
                                allow_start_on_boundary: bool = False) -> bool:
        polygon = self._polygons.get(cid, [])
        if len(polygon) < 3 or _qdist(a, b) < 1e-6:
            return False
        return self._find_route_conflict_line(
            cid, a, b,
            ignore_segment_indices=ignore_segment_indices,
            allow_start_on_boundary=allow_start_on_boundary,
        ) is None

    def _extract_conflict(self, result) -> Tuple[Optional[Tuple[QPointF, QPointF]], str]:
        """Extract conflict line and reason from _find_route_conflict_line result."""
        if result is None:
            return None, ""
        line, reason = result
        return line, reason

    def _constrain_route_candidate(self, cid: str, target: QPointF,
                                   allow_start_on_boundary: bool = False) -> QPointF:
        points = self._current_route_points
        if not points:
            self._current_route_preview_end = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            return target
        anchor = points[-1]
        ignore_set = {len(points) - 2} if len(points) >= 2 else set()

        def is_valid(point: QPointF) -> bool:
            return self._is_valid_route_segment(
                cid, anchor, point,
                ignore_segment_indices=ignore_set,
                allow_start_on_boundary=allow_start_on_boundary,
            )

        if self._is_valid_route_segment(
            cid, anchor, target,
            ignore_segment_indices=ignore_set,
            allow_start_on_boundary=allow_start_on_boundary,
        ):
            self._current_route_preview_end = target
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            return target

        best, violation = self._constrain_to_last_valid(anchor, target, is_valid)
        best = self._find_closest_valid_near_target(target, best, is_valid)
        self._current_route_preview_end = best
        self._constraint_violation_point = violation
        result = self._find_route_conflict_line(
            cid, anchor, violation,
            ignore_segment_indices=ignore_set,
            allow_start_on_boundary=allow_start_on_boundary,
        )
        if result is None:
            result = self._find_route_conflict_line(
                cid, anchor, target,
                ignore_segment_indices=ignore_set,
                allow_start_on_boundary=allow_start_on_boundary,
            )
        self._constraint_violation_line, self._constraint_violation_reason = self._extract_conflict(result)
        return best

    def _constrain_dragged_route_point(self, cid: str, idx: int,
                                       target: QPointF) -> QPointF:
        """Grid-snap a dragged route point. Constraint violations are shown
        as warnings only – the point is never blocked."""
        snapped = self._snap_to_grid(target)
        self._constraint_violation_point = None
        self._constraint_violation_line = None
        self._constraint_violation_reason = ""
        return snapped

    def _constrain_to_last_valid(self, origin: QPointF, target: QPointF, is_valid_fn):
        lo, hi = 0.0, 1.0
        best = origin
        violation = target
        for _ in range(16):
            mid = (lo + hi) * 0.5
            cand = QPointF(
                origin.x() + (target.x() - origin.x()) * mid,
                origin.y() + (target.y() - origin.y()) * mid,
            )
            if is_valid_fn(cand):
                best = cand
                lo = mid
            else:
                violation = cand
                hi = mid
        return best, violation

    def _find_closest_valid_near_target(self, target: QPointF, fallback_valid: QPointF,
                                        is_valid_fn) -> QPointF:
        best = fallback_valid
        if not is_valid_fn(best):
            return best

        best_dist = _qdist(target, best)
        step = max(4.0 / self._scale, 1.5)
        max_radius = max(42.0 / self._scale, best_dist + 4.0 / self._scale)
        angle_step_deg = 20

        radius = step
        while radius <= max_radius:
            found_on_ring = False
            for deg in range(0, 360, angle_step_deg):
                ang = math.radians(deg)
                cand = QPointF(
                    target.x() + math.cos(ang) * radius,
                    target.y() + math.sin(ang) * radius,
                )
                if not is_valid_fn(cand):
                    continue

                lo, hi = 0.0, 1.0
                refined = cand
                for _ in range(10):
                    mid = (lo + hi) * 0.5
                    test = QPointF(
                        target.x() + (cand.x() - target.x()) * mid,
                        target.y() + (cand.y() - target.y()) * mid,
                    )
                    if is_valid_fn(test):
                        refined = test
                        hi = mid
                    else:
                        lo = mid

                d = _qdist(target, refined)
                if d < best_dist:
                    best = refined
                    best_dist = d
                found_on_ring = True

            if found_on_ring and best_dist <= radius + 1e-6:
                break
            radius += step

        return best

    # ------------------------------------------------------------------ #
    #  Events                                                              #
    # ------------------------------------------------------------------ #

    def resizeEvent(self, event):
        if self._svg_renderer or self._bg_pixmap:
            self._fit_to_window()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        mouse  = QPointF(event.position())
        cp     = self._to_canvas(mouse)
        new_scale = self._scale * factor
        # Clamp zoom to limits
        new_scale = max(self._scale_min, min(self._scale_max, new_scale))
        self._scale = new_scale
        self._offset = QPointF(
            mouse.x() - cp.x() * self._scale,
            mouse.y() - cp.y() * self._scale,
        )
        self.update()

    # ── Doppelklick → Bearbeitungsmodus ──

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(event)

        canvas_pt = self._to_canvas(QPointF(event.position()))
        threshold = 10.0 / self._scale

        # In Draw-Supply-Line mode: double-click on last point finishes the supply line
        if self._mode == ToolMode.DRAW_SUPPLY_LINE and self._current_supply_cid and self._current_supply_points:
            last_pt = self._current_supply_points[-1]
            if _qdist(canvas_pt, last_pt) < threshold:
                cid = self._current_supply_cid
                if len(self._current_supply_points) >= 2:
                    # Check last point for HKV snap
                    last_pt = self._current_supply_points[-1]
                    hkv = self._find_nearest_hkv(last_pt)
                    if hkv:
                        self._current_supply_points[-1] = QPointF(
                            self._hkv_points[hkv])
                        self._supply_hkv[cid] = hkv
                    else:
                        self._supply_hkv.pop(cid, None)
                    self._supply_lines[cid] = list(self._current_supply_points)
                self._current_supply_cid = None
                self._current_supply_points = []
                self._current_supply_preview = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.supply_line_changed.emit(cid)
                self.update()
            return

        # In Draw-Elec-Cable mode: double-click on last point finishes the cable
        if self._mode == ToolMode.DRAW_ELEC_CABLE and self._current_elec_cable_id and self._current_elec_cable_points:
            last_pt = self._current_elec_cable_points[-1]
            if _qdist(canvas_pt, last_pt) < threshold:
                cid = self._current_elec_cable_id
                if len(self._current_elec_cable_points) >= 2:
                    # Check last point for AP snap
                    last_pt = self._current_elec_cable_points[-1]
                    ap = self._find_nearest_ap(last_pt)
                    if ap:
                        self._current_elec_cable_points[-1] = QPointF(
                            self._elec_points[ap])
                        self._cable_end_ap[cid] = ap
                    else:
                        self._cable_end_ap.pop(cid, None)
                    self._elec_cables[cid] = list(self._current_elec_cable_points)
                self._current_elec_cable_id = None
                self._current_elec_cable_points = []
                self._current_elec_cable_preview = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.elec_cable_changed.emit(cid)
                self.update()
            return

        # In Draw-HKV-Line mode: double-click on last point finishes the HKV line
        if self._mode == ToolMode.DRAW_HKV_LINE and self._current_hkv_line_id and self._current_hkv_line_points:
            last_pt = self._current_hkv_line_points[-1]
            if _qdist(canvas_pt, last_pt) < threshold:
                lid = self._current_hkv_line_id
                if len(self._current_hkv_line_points) >= 2:
                    # Check start and end points for HKV snap
                    start_pt = self._current_hkv_line_points[0]
                    end_pt = self._current_hkv_line_points[-1]
                    start_hkv = self._find_nearest_hkv(start_pt)
                    end_hkv = self._find_nearest_hkv(end_pt)
                    if start_hkv:
                        self._current_hkv_line_points[0] = QPointF(
                            self._hkv_points[start_hkv])
                        self._hkv_line_start[lid] = start_hkv
                    else:
                        self._hkv_line_start.pop(lid, None)
                    if end_hkv:
                        self._current_hkv_line_points[-1] = QPointF(
                            self._hkv_points[end_hkv])
                        self._hkv_line_end[lid] = end_hkv
                    else:
                        self._hkv_line_end.pop(lid, None)
                    self._hkv_lines[lid] = list(self._current_hkv_line_points)
                self._current_hkv_line_id = None
                self._current_hkv_line_points = []
                self._current_hkv_line_preview = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.hkv_line_changed.emit(lid)
                self.update()
            return

        # In Draw-Route mode: double-click on last point finishes the route
        if self._mode == ToolMode.DRAW_ROUTE and self._current_route_cid and self._current_route_points:
            last_pt = self._current_route_points[-1]
            if _qdist(canvas_pt, last_pt) < threshold:
                cid = self._current_route_cid
                self._manual_routes[cid] = list(self._current_route_points)
                self._current_route_cid = None
                self._current_route_points = []
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.route_changed.emit(cid)
                self.update()
            return

        # In Edit-Route mode: snap route point on double-click
        if self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid:
            hit = self._hit_route_point_in_circuit(canvas_pt, self._edit_route_cid)
            if hit is not None:
                self._snap_route_point_to_valid(self._edit_route_cid, hit)
            return

        # Outside NONE mode: snap any route point or ignore
        if self._mode != ToolMode.NONE:
            route_hit = self._hit_route_point(canvas_pt)
            if route_hit:
                self._snap_route_point_to_valid(route_hit[0], route_hit[1])
            return
        threshold = 10.0 / self._scale

        # 1. Elektro AP
        ap_hit = self._hit_elec_point(canvas_pt)
        if ap_hit:
            self.object_double_clicked.emit("elec_point", ap_hit)
            return

        # 2. HKV
        hkv_hit = self._hit_hkv(canvas_pt)
        if hkv_hit:
            self.object_double_clicked.emit("hkv", hkv_hit)
            return

        # 3. Elektro-Kabel – Doppelklick auf letzten Punkt → Zeichenmodus fortsetzen
        for kid, pts in self._elec_cables.items():
            if not self._elec_visible.get(kid, True):
                continue
            if len(pts) >= 2:
                # Last point hit → resume drawing
                if _qdist(canvas_pt, pts[-1]) < threshold:
                    self._current_elec_cable_points = list(pts)
                    self._current_elec_cable_id = kid
                    self._mode = ToolMode.DRAW_ELEC_CABLE
                    self._current_elec_cable_preview = None
                    self.setCursor(Qt.CrossCursor)
                    self.update()
                    return
                # Edge hit → edit mode
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        self.object_double_clicked.emit("elec_cable", kid)
                        return

        # 4. HKV-Leitung – Doppelklick auf letzten Punkt → Zeichenmodus fortsetzen
        for lid, pts in self._hkv_lines.items():
            if not self._hkv_line_visible.get(lid, True):
                continue
            if len(pts) >= 2:
                # Last point hit → resume drawing
                if _qdist(canvas_pt, pts[-1]) < threshold:
                    self._current_hkv_line_points = list(pts)
                    self._current_hkv_line_id = lid
                    self._mode = ToolMode.DRAW_HKV_LINE
                    self._current_hkv_line_preview = None
                    self.setCursor(Qt.CrossCursor)
                    self.update()
                    return
                # Edge hit → edit mode
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        self.object_double_clicked.emit("hkv_line", lid)
                        return

        # 5. Zuleitung – Doppelklick auf letzten Punkt → Zeichenmodus fortsetzen
        for cid, pts in self._supply_lines.items():
            if not self._circuit_visible.get(cid, True):
                continue
            if len(pts) >= 2:
                # Last point hit → resume drawing
                if _qdist(canvas_pt, pts[-1]) < threshold:
                    self._current_supply_points = list(pts)
                    self._current_supply_cid = cid
                    self._mode = ToolMode.DRAW_SUPPLY_LINE
                    self._current_supply_preview = None
                    self.setCursor(Qt.CrossCursor)
                    self.update()
                    return
                # Edge hit → edit mode
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        self.object_double_clicked.emit("supply_line", cid)
                        return

        # 6. Rohrverlauf – Doppelklick auf letzten Punkt → Zeichenmodus fortsetzen
        for cid, pts in self._manual_routes.items():
            if not self._circuit_visible.get(cid, True):
                continue
            if len(pts) >= 2:
                # Last point hit → resume drawing
                if _qdist(canvas_pt, pts[-1]) < threshold:
                    self._current_route_points = list(pts)
                    self._current_route_cid = cid
                    self._mode = ToolMode.DRAW_ROUTE
                    self._current_route_preview_end = None
                    self._constraint_violation_point = None
                    self._constraint_violation_line = None
                    self._constraint_violation_reason = ""
                    self.setCursor(Qt.CrossCursor)
                    self.update()
                    return
                # Edge hit → edit mode
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        self.object_double_clicked.emit("route", cid)
                        return

        # 7. Polygon (point inside)
        for fid in reversed(self._floor_plan_order):
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible or not layer.polygon:
                continue
            poly = QPolygonF(self._floor_polygon_points_world(fid))
            if poly.containsPoint(canvas_pt, Qt.OddEvenFill):
                self.object_double_clicked.emit("floor_polygon", fid)
                return

        # 8. Polygon (point inside)
        for cid, poly in self._polygons.items():
            if not self._circuit_visible.get(cid, True):
                continue
            if self._point_in_polygon(canvas_pt, poly):
                self.object_double_clicked.emit("polygon", cid)
                return

    def mousePressEvent(self, event):
        pos       = QPointF(event.position())
        canvas_pt = self._to_canvas(pos)

        if event.button() == Qt.MiddleButton:
            self._pan_start = pos
            self._panning   = True
            return

        # ── Referenzlinie ──
        if self._mode == ToolMode.DRAW_REF:
            if event.button() == Qt.LeftButton:
                if self._ref_p1 is None:
                    self._ref_p1 = canvas_pt
                else:
                    self._ref_p2 = canvas_pt
                    # Store on floor plan layer if applicable
                    if self._ref_floor_id:
                        layer = self._floor_plans.get(self._ref_floor_id)
                        if layer:
                            layer.ref_p1 = QPointF(self._ref_p1)
                            layer.ref_p2 = QPointF(self._ref_p2)
                    self._mode   = ToolMode.NONE
                    self.setCursor(Qt.ArrowCursor)
                    self.ref_line_set.emit()   # Panel kann jetzt Länge abfragen
                self.update()
            return

        # ── Messen ──
        if self._mode == ToolMode.MEASURE:
            if event.button() == Qt.LeftButton:
                if self._measure_p1 is None:
                    self._measure_p1 = canvas_pt
                    self._measure_p2 = None
                else:
                    self._measure_p2 = canvas_pt
                    # Save measurement and start next one
                    if self._mm_per_px > 0:
                        px_len = _qdist(self._measure_p1, self._measure_p2)
                        mm_len = px_len * self._mm_per_px
                        self._measure_lines.append(
                            (QPointF(self._measure_p1),
                             QPointF(self._measure_p2), mm_len))
                    self._measure_p1 = None
                    self._measure_p2 = None
                self.update()
            elif event.button() == Qt.RightButton:
                # Cancel current measurement or exit mode
                if self._measure_p1:
                    self._measure_p1 = None
                    self.update()
                else:
                    self._mode = ToolMode.NONE
                    self.setCursor(Qt.ArrowCursor)
                    self.update()
            return

        # ── Export-Rahmen zeichnen ──
        if self._mode == ToolMode.DRAW_EXPORT_FRAME:
            if event.button() == Qt.LeftButton:
                self._export_frame_start = QPointF(canvas_pt)
                self._export_frame_current = QPointF(canvas_pt)
                self.update()
            elif event.button() == Qt.RightButton:
                self.clear_export_frame()
            return

        # ── Grundriss verschieben ──
        if self._mode == ToolMode.MOVE_FLOOR_PLAN:
            if event.button() == Qt.LeftButton and self._active_floor_id:
                self._floor_drag_start = canvas_pt
                self.setCursor(Qt.ClosedHandCursor)
            return

        # ── Grundriss drehen ──
        if self._mode == ToolMode.ROTATE_FLOOR_PLAN:
            if event.button() == Qt.LeftButton and self._active_floor_id:
                layer = self._floor_plans.get(self._active_floor_id)
                if layer:
                    import math
                    if layer.polygon:
                        sw, sh = self._floor_polygon_render_size(layer)
                    else:
                        sw, sh = self._layer_render_size(layer)
                    cx = sw / 2 + layer.offset_x
                    cy = sh / 2 + layer.offset_y
                    dx = canvas_pt.x() - cx
                    dy = canvas_pt.y() - cy
                    self._floor_rotate_start_angle = math.degrees(math.atan2(dy, dx))
                    self._floor_rotate_orig = layer.rotation
                    self._floor_drag_start = canvas_pt
            return

        # ── Polygon zeichnen ──
        if self._mode == ToolMode.DRAW_POLY:
            if event.button() == Qt.LeftButton:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                self._current_points.append(pt)
                self.update()
            elif event.button() == Qt.RightButton:
                if len(self._current_points) >= 3:
                    self._polygons[self._current_circuit_id] = \
                        list(self._current_points)
                    self._start_points[self._current_circuit_id] = \
                        self._current_points[0]
                    pts = [(p.x(), p.y()) for p in self._current_points]
                    self.polygon_finished.emit(self._current_circuit_id, pts)
                self._mode = ToolMode.NONE
                self._current_points = []
                self.setCursor(Qt.ArrowCursor)
                self.update()
            return

        # ── Einrichtungs-Polygon zeichnen ──
        if self._mode == ToolMode.DRAW_FURNITURE_POLY:
            if event.button() == Qt.LeftButton:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                self._current_points.append(pt)
                self.update()
            elif event.button() == Qt.RightButton:
                if len(self._current_points) >= 3 and self._current_furniture_id:
                    layer = self._floor_plans.get(self._current_furniture_id)
                    if layer:
                        min_x = min(p.x() for p in self._current_points)
                        min_y = min(p.y() for p in self._current_points)
                        max_x = max(p.x() for p in self._current_points)
                        max_y = max(p.y() for p in self._current_points)
                        w = max(1.0, max_x - min_x)
                        h = max(1.0, max_y - min_y)
                        layer.size = (w, h)
                        layer.offset_x = min_x
                        layer.offset_y = min_y
                        layer.rotation = 0.0
                        layer.file_path = ""
                        layer.renderer = None
                        layer.pixmap = None
                        layer.polygon = [QPointF(p.x() - min_x, p.y() - min_y)
                                         for p in self._current_points]
                        pts = [(p.x(), p.y()) for p in self._current_points]
                        self.floor_plan_polygon_finished.emit(
                            self._current_furniture_id, pts
                        )
                self._mode = ToolMode.NONE
                self._current_furniture_id = None
                self._current_points = []
                self.setCursor(Qt.ArrowCursor)
                self.update()
            return

        # ── Rohrverlauf zeichnen ──
        if self._mode == ToolMode.DRAW_ROUTE:
            if event.button() == Qt.LeftButton and self._current_route_cid:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                if ctrl_held:
                    final_pt = canvas_pt
                else:
                    final_pt = self._snap_to_grid(
                        self._apply_angle_snap(canvas_pt)
                    )
                # Still compute violation for display only (not blocking)
                allow_start_on_boundary = (len(self._current_route_points) == 1)
                self._constrain_route_candidate(
                    self._current_route_cid,
                    final_pt,
                    allow_start_on_boundary=allow_start_on_boundary,
                )
                if _qdist(self._current_route_points[-1], final_pt) > 1.0:
                    self._current_route_points.append(final_pt)
                    self._current_route_preview_end = None
                    self._constraint_violation_point = None
                    self._constraint_violation_line = None
                    self._constraint_violation_reason = ""
                    self.update()
            elif event.button() == Qt.RightButton and self._current_route_cid:
                cid = self._current_route_cid
                self._manual_routes[cid] = list(self._current_route_points)
                self._current_route_cid = None
                self._current_route_points = []
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.route_changed.emit(cid)
                self.update()
            return

        # ── Anschlusspunkt platzieren ──
        if self._mode == ToolMode.PLACE_ELEC_POINT:
            if event.button() == Qt.LeftButton and self._placing_elec_point_id:
                pid = self._placing_elec_point_id
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                self._elec_points[pid] = pt
                self._placing_elec_point_id = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.elec_point_placed.emit(pid)
                self.update()
            return

        # ── Kabel zeichnen ──
        if self._mode == ToolMode.DRAW_ELEC_CABLE:
            if event.button() == Qt.LeftButton and self._current_elec_cable_id:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                if ctrl_held:
                    snapped = canvas_pt
                else:
                    snapped = self._snap_to_grid(self._apply_angle_snap_elec(canvas_pt))
                # Snap to an AP if close enough
                ap = self._find_nearest_ap(snapped)
                if ap:
                    snapped = QPointF(self._elec_points[ap])
                    # First point → start AP
                    if len(self._current_elec_cable_points) == 0:
                        self._cable_start_ap[self._current_elec_cable_id] = ap
                self._current_elec_cable_points.append(snapped)
                self._current_elec_cable_preview = None
                self.update()
            elif event.button() == Qt.RightButton and self._current_elec_cable_id:
                cid = self._current_elec_cable_id
                if len(self._current_elec_cable_points) >= 2:
                    # Check if last point is near an AP → end AP
                    last_pt = self._current_elec_cable_points[-1]
                    end_ap = self._find_nearest_ap(last_pt)
                    if end_ap:
                        self._current_elec_cable_points[-1] = QPointF(
                            self._elec_points[end_ap])
                        self._cable_end_ap[cid] = end_ap
                    else:
                        self._cable_end_ap[cid] = ""
                    self._elec_cables[cid] = list(
                        self._current_elec_cable_points)
                else:
                    self._cable_start_ap.pop(cid, None)
                    self._cable_end_ap.pop(cid, None)
                self._current_elec_cable_id = None
                self._current_elec_cable_points = []
                self._current_elec_cable_preview = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.elec_cable_changed.emit(cid)
                self.update()
            return

        # ── Kabel bearbeiten ──
        if self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id:
            cid = self._edit_elec_cable_id
            if event.button() == Qt.LeftButton:
                hit = self._hit_elec_cable_point(canvas_pt, cid)
                if hit is not None:
                    self._dragging_route_point = (cid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                return
            elif event.button() == Qt.RightButton:
                hit = self._hit_elec_cable_point(canvas_pt, cid)
                if hit is not None:
                    pts = self._elec_cables.get(cid, [])
                    if len(pts) > 2:
                        del pts[hit]
                        self.elec_cable_changed.emit(cid)
                        self.update()
                    return
                hit = self._hit_elec_cable_edge(canvas_pt, cid)
                if hit is not None:
                    idx1, idx2 = hit
                    pts = self._elec_cables[cid]
                    p1, p2 = pts[idx1], pts[idx2]
                    mid = QPointF((p1.x() + p2.x()) * 0.5,
                                  (p1.y() + p2.y()) * 0.5)
                    pts.insert(idx2, mid)
                    self.elec_cable_changed.emit(cid)
                    self.update()
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_elec_cable_id = None
                self.setCursor(Qt.ArrowCursor)
                self.update()
                return

        # ── Anschlussleitung zeichnen ──
        if self._mode == ToolMode.DRAW_SUPPLY_LINE:
            if event.button() == Qt.LeftButton and self._current_supply_cid:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                if ctrl_held:
                    snapped = canvas_pt
                else:
                    snapped = self._snap_to_grid(self._apply_angle_snap_supply(canvas_pt))
                # Snap to HKV on first point (already set) or any later point
                hkv = self._find_nearest_hkv(snapped)
                if hkv:
                    snapped = QPointF(self._hkv_points[hkv])
                self._current_supply_points.append(snapped)
                self._current_supply_preview = None
                self.update()
            elif event.button() == Qt.RightButton and self._current_supply_cid:
                cid = self._current_supply_cid
                if len(self._current_supply_points) >= 2:
                    # Check last point for HKV snap
                    last_pt = self._current_supply_points[-1]
                    hkv = self._find_nearest_hkv(last_pt)
                    if hkv:
                        self._current_supply_points[-1] = QPointF(
                            self._hkv_points[hkv])
                        self._supply_hkv[cid] = hkv
                    else:
                        self._supply_hkv.pop(cid, None)
                    self._supply_lines[cid] = list(self._current_supply_points)
                self._current_supply_cid = None
                self._current_supply_points = []
                self._current_supply_preview = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.supply_line_changed.emit(cid)
                self.update()
            return

        # ── Anschlussleitung bearbeiten ──
        if self._mode == ToolMode.EDIT_SUPPLY_LINE and self._edit_supply_cid:
            cid = self._edit_supply_cid
            if event.button() == Qt.LeftButton:
                hit = self._hit_supply_line_point(canvas_pt, cid)
                if hit is not None:
                    self._dragging_route_point = (cid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                return
            elif event.button() == Qt.RightButton:
                hit = self._hit_supply_line_point(canvas_pt, cid)
                if hit is not None:
                    pts = self._supply_lines.get(cid, [])
                    if len(pts) > 2:
                        del pts[hit]
                        self.supply_line_changed.emit(cid)
                        self.update()
                    return
                hit = self._hit_supply_line_edge(canvas_pt, cid)
                if hit is not None:
                    idx1, idx2 = hit
                    pts = self._supply_lines[cid]
                    p1, p2 = pts[idx1], pts[idx2]
                    mid = QPointF((p1.x() + p2.x()) * 0.5,
                                  (p1.y() + p2.y()) * 0.5)
                    pts.insert(idx2, mid)
                    self.supply_line_changed.emit(cid)
                    self.update()
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_supply_cid = None
                self.setCursor(Qt.ArrowCursor)
                self.update()
                return

        # ── HKV platzieren ──
        if self._mode == ToolMode.PLACE_HKV:
            if event.button() == Qt.LeftButton and self._placing_hkv_id:
                hid = self._placing_hkv_id
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                self._hkv_points[hid] = pt
                self._placing_hkv_id = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.hkv_placed.emit(hid)
                self.update()
            return

        # ── Text platzieren ──
        if self._mode == ToolMode.PLACE_TEXT:
            if event.button() == Qt.LeftButton and self._placing_text_id:
                tid = self._placing_text_id
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                self._text_annotations[tid] = pt
                self._placing_text_id = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.text_placed.emit(tid)
                self.update()
            return

        # ── HKV-Verbindungsleitung zeichnen ──
        if self._mode == ToolMode.DRAW_HKV_LINE:
            if event.button() == Qt.LeftButton and self._current_hkv_line_id:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                if ctrl_held:
                    snapped = canvas_pt
                else:
                    snapped = self._snap_to_grid(self._apply_angle_snap_hkv_line(canvas_pt))
                hkv = self._find_nearest_hkv(snapped)
                if hkv:
                    snapped = QPointF(self._hkv_points[hkv])
                    if len(self._current_hkv_line_points) == 0:
                        self._hkv_line_start[self._current_hkv_line_id] = hkv
                self._current_hkv_line_points.append(snapped)
                self._current_hkv_line_preview = None
                self.update()
            elif event.button() == Qt.RightButton and self._current_hkv_line_id:
                lid = self._current_hkv_line_id
                if len(self._current_hkv_line_points) >= 2:
                    last_pt = self._current_hkv_line_points[-1]
                    hkv = self._find_nearest_hkv(last_pt)
                    if hkv:
                        self._current_hkv_line_points[-1] = QPointF(
                            self._hkv_points[hkv])
                        self._hkv_line_end[lid] = hkv
                    else:
                        self._hkv_line_end.pop(lid, None)
                    self._hkv_lines[lid] = list(self._current_hkv_line_points)
                else:
                    self._hkv_line_start.pop(lid, None)
                    self._hkv_line_end.pop(lid, None)
                self._current_hkv_line_id = None
                self._current_hkv_line_points = []
                self._current_hkv_line_preview = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.hkv_line_changed.emit(lid)
                self.update()
            return

        # ── HKV-Verbindungsleitung bearbeiten ──
        if self._mode == ToolMode.EDIT_HKV_LINE and self._edit_hkv_line_id:
            lid = self._edit_hkv_line_id
            if event.button() == Qt.LeftButton:
                hit = self._hit_hkv_line_point(canvas_pt, lid)
                if hit is not None:
                    self._dragging_route_point = (lid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                return
            elif event.button() == Qt.RightButton:
                hit = self._hit_hkv_line_point(canvas_pt, lid)
                if hit is not None:
                    pts = self._hkv_lines.get(lid, [])
                    if len(pts) > 2:
                        del pts[hit]
                        self.hkv_line_changed.emit(lid)
                        self.update()
                    return
                hit = self._hit_hkv_line_edge(canvas_pt, lid)
                if hit is not None:
                    idx1, idx2 = hit
                    pts = self._hkv_lines[lid]
                    p1, p2 = pts[idx1], pts[idx2]
                    mid = QPointF((p1.x() + p2.x()) * 0.5,
                                  (p1.y() + p2.y()) * 0.5)
                    pts.insert(idx2, mid)
                    self.hkv_line_changed.emit(lid)
                    self.update()
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_hkv_line_id = None
                self.setCursor(Qt.ArrowCursor)
                self.update()
                return

        # ── Polygon bearbeiten ──
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_polygon_cid:
            cid = self._edit_polygon_cid
            if event.button() == Qt.LeftButton:
                hit = self._hit_polygon_point(canvas_pt, cid)
                if hit is not None:
                    self._dragging_route_point = (cid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                return
            elif event.button() == Qt.RightButton:
                hit = self._hit_polygon_point(canvas_pt, cid)
                if hit is not None:
                    self._delete_polygon_point(cid, hit)
                    return
                hit = self._hit_polygon_edge(canvas_pt, cid)
                if hit is not None:
                    idx1, idx2 = hit
                    p1 = self._polygons[cid][idx1]
                    p2 = self._polygons[cid][idx2]
                    midpt = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                    self._insert_polygon_point(cid, idx1, idx2, midpt)
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_polygon_cid = None
                self.setCursor(Qt.ArrowCursor)
                self.update()
                return

        if self._mode == ToolMode.EDIT_POLYGON and self._edit_floor_polygon_id:
            fid = self._edit_floor_polygon_id
            if event.button() == Qt.LeftButton:
                hit = self._hit_floor_polygon_point(canvas_pt, fid)
                if hit is not None:
                    self._dragging_route_point = (fid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                return
            elif event.button() == Qt.RightButton:
                hit = self._hit_floor_polygon_point(canvas_pt, fid)
                if hit is not None:
                    self._delete_floor_polygon_point(fid, hit)
                    return
                hit = self._hit_floor_polygon_edge(canvas_pt, fid)
                if hit is not None:
                    idx1, idx2 = hit
                    self._insert_floor_polygon_point(fid, idx1, idx2, canvas_pt)
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_floor_polygon_id = None
                self.setCursor(Qt.ArrowCursor)
                self.update()
                return

        # ── Rohrverlauf bearbeiten ──
        if self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid:
            cid = self._edit_route_cid
            if event.button() == Qt.LeftButton:
                hit = self._hit_route_point_in_circuit(canvas_pt, cid)
                if hit is not None:
                    self._dragging_route_point = (cid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                return
            elif event.button() == Qt.RightButton:
                hit = self._hit_route_point_in_circuit(canvas_pt, cid)
                if hit is not None:
                    self._delete_route_point(cid, hit)
                    return
                hit = self._hit_route_edge(canvas_pt, cid)
                if hit is not None:
                    idx1, idx2 = hit
                    pts = self._manual_routes[cid]
                    p1 = pts[idx1]
                    p2 = pts[idx2]
                    midpt = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                    self._insert_route_point(cid, idx1, idx2, midpt)
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_route_cid = None
                self.setCursor(Qt.ArrowCursor)
                self.update()
                return

        # ── Text-Annotation verschieben ──
        if event.button() == Qt.LeftButton:
            text_hit = self._hit_text_annotation(canvas_pt)
            if text_hit:
                self._dragging_text = text_hit
                self.setCursor(Qt.ClosedHandCursor)
                return

        # ── Label verschieben ──
        if event.button() == Qt.LeftButton:
            label_hit = self._hit_label(canvas_pt)
            if label_hit:
                self._dragging_label = label_hit
                draw_pos = self._label_draw_pos.get(label_hit, canvas_pt)
                self._label_drag_offset = QPointF(
                    canvas_pt.x() - draw_pos.x(),
                    canvas_pt.y() - draw_pos.y())
                self.setCursor(Qt.ClosedHandCursor)
                return

        # ── Startpunkt verschieben ──
        if event.button() == Qt.LeftButton:
            route_hit = self._hit_route_point(canvas_pt)
            if route_hit:
                self._dragging_route_point = route_hit
                self._mode = ToolMode.MOVE_ROUTE_POINT
                self.setCursor(Qt.ClosedHandCursor)
                return
            hit = self._hit_start_point(canvas_pt)
            if hit:
                self._dragging_start = hit
                self._mode = ToolMode.MOVE_START
                self.setCursor(Qt.ClosedHandCursor)
                return
            elec_hit = self._hit_elec_point(canvas_pt)
            if elec_hit:
                self._dragging_elec_point = elec_hit
                self._mode = ToolMode.MOVE_ELEC_POINT
                self.setCursor(Qt.ClosedHandCursor)
                return
            hkv_hit = self._hit_hkv(canvas_pt)
            if hkv_hit:
                self._dragging_hkv = hkv_hit
                self._mode = ToolMode.MOVE_HKV
                self.setCursor(Qt.ClosedHandCursor)
                return
            self._pan_start = pos
            self._panning   = True

    def mouseMoveEvent(self, event):
        pos       = QPointF(event.position())
        canvas_pt = self._to_canvas(pos)
        self._mouse_pos = canvas_pt

        if self._panning and self._pan_start:
            delta        = pos - self._pan_start
            self._offset += delta
            self._pan_start = pos
            self._current_route_preview_end = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            self.update()
            return

        # ── Grundriss verschieben ──
        if self._mode == ToolMode.MOVE_FLOOR_PLAN and self._active_floor_id:
            if self._floor_drag_start:
                layer = self._floor_plans.get(self._active_floor_id)
                if layer:
                    dx = canvas_pt.x() - self._floor_drag_start.x()
                    dy = canvas_pt.y() - self._floor_drag_start.y()
                    layer.offset_x += dx
                    layer.offset_y += dy
                    # Move ref line with the floor plan
                    if layer.ref_p1:
                        layer.ref_p1 = QPointF(layer.ref_p1.x() + dx,
                                               layer.ref_p1.y() + dy)
                    if layer.ref_p2:
                        layer.ref_p2 = QPointF(layer.ref_p2.x() + dx,
                                               layer.ref_p2.y() + dy)
                    # Sync global ref line if this is the active ref floor
                    if self._ref_floor_id == self._active_floor_id:
                        self._ref_p1 = layer.ref_p1
                        self._ref_p2 = layer.ref_p2
                    self._floor_drag_start = canvas_pt
                    self.update()
            return

        # ── Grundriss drehen ──
        if self._mode == ToolMode.ROTATE_FLOOR_PLAN and self._active_floor_id:
            if self._floor_drag_start:
                layer = self._floor_plans.get(self._active_floor_id)
                if layer:
                    import math
                    if layer.polygon:
                        sw, sh = self._floor_polygon_render_size(layer)
                    else:
                        sw, sh = self._layer_render_size(layer)
                    cx = sw / 2 + layer.offset_x
                    cy = sh / 2 + layer.offset_y
                    dx = canvas_pt.x() - cx
                    dy = canvas_pt.y() - cy
                    angle = math.degrees(math.atan2(dy, dx))
                    new_rot = self._floor_rotate_orig + (angle - self._floor_rotate_start_angle)
                    delta_rot = new_rot - layer.rotation
                    # Rotate ref line points around floor plan centre
                    if delta_rot != 0:
                        rad = math.radians(delta_rot)
                        cos_r, sin_r = math.cos(rad), math.sin(rad)
                        for attr in ("ref_p1", "ref_p2"):
                            pt = getattr(layer, attr)
                            if pt:
                                rx, ry = pt.x() - cx, pt.y() - cy
                                nx = cx + rx * cos_r - ry * sin_r
                                ny = cy + rx * sin_r + ry * cos_r
                                setattr(layer, attr, QPointF(nx, ny))
                        if self._ref_floor_id == self._active_floor_id:
                            self._ref_p1 = layer.ref_p1
                            self._ref_p2 = layer.ref_p2
                    layer.rotation = new_rot
                    self.update()
            return

        # ── Export-Rahmen zeichnen (Move) ──
        if self._mode == ToolMode.DRAW_EXPORT_FRAME and self._export_frame_start:
            self._export_frame_current = QPointF(canvas_pt)
            self.update()
            return

        if self._mode == ToolMode.MOVE_START and self._dragging_start:
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            base_pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
            snapped = self._snap_to_polygon_edge(self._dragging_start, base_pt)
            self._start_points[self._dragging_start] = snapped
            route = self._manual_routes.get(self._dragging_start)
            if route:
                route[0] = QPointF(snapped.x(), snapped.y())
            self._current_route_preview_end = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            self.update()
            return

        if self._mode == ToolMode.MOVE_ROUTE_POINT and self._dragging_route_point:
            cid, idx = self._dragging_route_point
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            if ctrl_held:
                constrained = canvas_pt
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
            else:
                constrained = self._constrain_dragged_route_point(cid, idx, canvas_pt)
            if cid in self._manual_routes and 0 <= idx < len(self._manual_routes[cid]):
                self._manual_routes[cid][idx] = constrained
            self.update()
            return

        # ── Edit Polygon: Punkt verschieben ──
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_polygon_cid:
            if self._dragging_route_point:
                cid, idx = self._dragging_route_point
                if cid == self._edit_polygon_cid and cid in self._polygons:
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                    self._polygons[cid][idx] = pt
                    self.update()
            else:
                hit = self._hit_polygon_point(canvas_pt, self._edit_polygon_cid)
                if hit is not None:
                    self.setCursor(Qt.OpenHandCursor)
                    self.update()
                    return
                edge_hit = self._hit_polygon_edge(canvas_pt, self._edit_polygon_cid)
                self.setCursor(Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
            self.update()
            return

        if self._mode == ToolMode.EDIT_POLYGON and self._edit_floor_polygon_id:
            fid = self._edit_floor_polygon_id
            if self._dragging_route_point:
                oid, idx = self._dragging_route_point
                layer = self._floor_plans.get(fid)
                if oid == fid and layer and 0 <= idx < len(layer.polygon):
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    snapped_pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                    layer.polygon[idx] = self._world_to_floor_polygon_local(fid, snapped_pt)
                    self.update()
            else:
                hit = self._hit_floor_polygon_point(canvas_pt, fid)
                if hit is not None:
                    self.setCursor(Qt.OpenHandCursor)
                    self.update()
                    return
                edge_hit = self._hit_floor_polygon_edge(canvas_pt, fid)
                self.setCursor(Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
            self.update()
            return

        # ── Edit Route: Punkt verschieben ──
        if self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid:
            if self._dragging_route_point:
                cid, idx = self._dragging_route_point
                if cid == self._edit_route_cid and cid in self._manual_routes:
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    if ctrl_held:
                        self._manual_routes[cid][idx] = canvas_pt
                        self._constraint_violation_point = None
                        self._constraint_violation_line = None
                        self._constraint_violation_reason = ""
                    else:
                        constrained = self._constrain_dragged_route_point(cid, idx, canvas_pt)
                        self._manual_routes[cid][idx] = constrained
                    self.update()
            else:
                hit = self._hit_route_point_in_circuit(canvas_pt, self._edit_route_cid)
                if hit is not None:
                    self.setCursor(Qt.OpenHandCursor)
                    self.update()
                    return
                edge_hit = self._hit_route_edge(canvas_pt, self._edit_route_cid)
                self.setCursor(Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
            self.update()
            return

        if self._mode == ToolMode.DRAW_ROUTE and self._current_route_cid and self._current_route_points:
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            if ctrl_held:
                preview_pt = canvas_pt
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
            else:
                preview_pt = self._snap_to_grid(
                    self._apply_angle_snap(canvas_pt)
                )
                allow_start_on_boundary = (len(self._current_route_points) == 1)
                # Compute violation for warning display only
                self._constrain_route_candidate(
                    self._current_route_cid,
                    preview_pt,
                    allow_start_on_boundary=allow_start_on_boundary,
                )
            self._current_route_preview_end = preview_pt
            self.update()
            return

        if self._mode == ToolMode.MOVE_ELEC_POINT and self._dragging_elec_point:
            pid = self._dragging_elec_point
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
            self._elec_points[pid] = pt
            # Move connected cable start/end points along with the AP
            for cid, ap_id in self._cable_start_ap.items():
                if ap_id == pid and cid in self._elec_cables:
                    self._elec_cables[cid][0] = QPointF(pt)
            for cid, ap_id in self._cable_end_ap.items():
                if ap_id == pid and cid in self._elec_cables:
                    self._elec_cables[cid][-1] = QPointF(pt)
            self.update()
            return

        if self._mode == ToolMode.MOVE_HKV and self._dragging_hkv:
            hid = self._dragging_hkv
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
            self._hkv_points[hid] = pt
            # Move connected supply line endpoints
            for cid, hkv_id in self._supply_hkv.items():
                if hkv_id == hid and cid in self._supply_lines:
                    self._supply_lines[cid][-1] = QPointF(pt)
            # Move connected HKV line start/end points
            for lid, hkv_id in self._hkv_line_start.items():
                if hkv_id == hid and lid in self._hkv_lines:
                    self._hkv_lines[lid][0] = QPointF(pt)
            for lid, hkv_id in self._hkv_line_end.items():
                if hkv_id == hid and lid in self._hkv_lines:
                    self._hkv_lines[lid][-1] = QPointF(pt)
            self.update()
            return

        if (self._mode == ToolMode.DRAW_ELEC_CABLE
                and self._current_elec_cable_id
                and self._current_elec_cable_points):
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            if ctrl_held:
                snapped = canvas_pt
            else:
                snapped = self._snap_to_grid(self._apply_angle_snap_elec(canvas_pt))
            self._current_elec_cable_preview = snapped
            self.update()
            return

        if self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id:
            if self._dragging_route_point:
                cid, idx = self._dragging_route_point
                if cid == self._edit_elec_cable_id and cid in self._elec_cables:
                    pts = self._elec_cables[cid]
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    base_pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                    # Snap first/last point to nearest AP while dragging
                    if idx == 0 or idx == len(pts) - 1:
                        ap = self._find_nearest_ap(base_pt)
                        if ap:
                            pts[idx] = QPointF(self._elec_points[ap])
                        else:
                            pts[idx] = base_pt
                    else:
                        pts[idx] = base_pt
                    self.update()
            else:
                hit = self._hit_elec_cable_point(
                    canvas_pt, self._edit_elec_cable_id)
                if hit is not None:
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    edge_hit = self._hit_elec_cable_edge(
                        canvas_pt, self._edit_elec_cable_id)
                    self.setCursor(
                        Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
            self.update()
            return

        # ── Anschlussleitung zeichnen (Move) ──
        if (self._mode == ToolMode.DRAW_SUPPLY_LINE
                and self._current_supply_cid
                and self._current_supply_points):
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            if ctrl_held:
                snapped = canvas_pt
            else:
                snapped = self._snap_to_grid(self._apply_angle_snap_supply(canvas_pt))
            self._current_supply_preview = snapped
            self.update()
            return

        # ── Anschlussleitung bearbeiten (Move) ──
        if self._mode == ToolMode.EDIT_SUPPLY_LINE and self._edit_supply_cid:
            if self._dragging_route_point:
                cid, idx = self._dragging_route_point
                if cid == self._edit_supply_cid and cid in self._supply_lines:
                    pts = self._supply_lines[cid]
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    base_pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                    # Snap last point to nearest HKV
                    if idx == len(pts) - 1:
                        hkv = self._find_nearest_hkv(base_pt)
                        if hkv:
                            pts[idx] = QPointF(self._hkv_points[hkv])
                        else:
                            pts[idx] = base_pt
                    else:
                        pts[idx] = base_pt
                    self.update()
            else:
                hit = self._hit_supply_line_point(
                    canvas_pt, self._edit_supply_cid)
                if hit is not None:
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    edge_hit = self._hit_supply_line_edge(
                        canvas_pt, self._edit_supply_cid)
                    self.setCursor(
                        Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
            self.update()
            return

        # ── HKV-Verbindungsleitung zeichnen (Move) ──
        if (self._mode == ToolMode.DRAW_HKV_LINE
                and self._current_hkv_line_id
                and self._current_hkv_line_points):
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            if ctrl_held:
                snapped = canvas_pt
            else:
                snapped = self._snap_to_grid(self._apply_angle_snap_hkv_line(canvas_pt))
            self._current_hkv_line_preview = snapped
            self.update()
            return

        # ── HKV-Verbindungsleitung bearbeiten (Move) ──
        if self._mode == ToolMode.EDIT_HKV_LINE and self._edit_hkv_line_id:
            if self._dragging_route_point:
                lid, idx = self._dragging_route_point
                if lid == self._edit_hkv_line_id and lid in self._hkv_lines:
                    pts = self._hkv_lines[lid]
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    base_pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                    if idx == 0 or idx == len(pts) - 1:
                        hkv = self._find_nearest_hkv(base_pt)
                        if hkv:
                            pts[idx] = QPointF(self._hkv_points[hkv])
                        else:
                            pts[idx] = base_pt
                    else:
                        pts[idx] = base_pt
                    self.update()
            else:
                hit = self._hit_hkv_line_point(
                    canvas_pt, self._edit_hkv_line_id)
                if hit is not None:
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    edge_hit = self._hit_hkv_line_edge(
                        canvas_pt, self._edit_hkv_line_id)
                    self.setCursor(
                        Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
            self.update()
            return

        # ── Text annotation dragging ──
        if self._dragging_text:
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
            self._text_annotations[self._dragging_text] = pt
            self.update()
            return

        # ── Label dragging (works in any mode / NONE) ──
        if self._dragging_label:
            new_pos = QPointF(
                canvas_pt.x() - self._label_drag_offset.x(),
                canvas_pt.y() - self._label_drag_offset.y())
            self._label_positions[self._dragging_label] = new_pos
            self.update()
            return

        if self._mode == ToolMode.NONE:
            self._current_route_preview_end = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            label_hit = self._hit_label(canvas_pt)
            if label_hit:
                self.setCursor(Qt.SizeAllCursor)
                self.update()
                return
            route_hit = self._hit_route_point(canvas_pt)
            if route_hit:
                self.setCursor(Qt.OpenHandCursor)
                self.update()
                return
            hit = self._hit_start_point(canvas_pt)
            self.setCursor(Qt.OpenHandCursor if hit else Qt.ArrowCursor)

        # Tooltip for text annotations on hover
        text_hit = self._hit_text_annotation(canvas_pt)
        if text_hit:
            comment = self._text_comments.get(text_hit, "")
            if comment:
                QToolTip.showText(event.globalPosition().toPoint(), comment, self)
            else:
                QToolTip.hideText()
        else:
            QToolTip.hideText()

        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning   = False
            self._pan_start = None
            return

        if event.button() == Qt.LeftButton:
            # ── Export-Rahmen Zeichnen abschliessen ──
            if self._mode == ToolMode.DRAW_EXPORT_FRAME and self._export_frame_start:
                end_pt = self._export_frame_current or self._export_frame_start
                rect = QRectF(self._export_frame_start, end_pt).normalized()
                if rect.width() > 1.0 and rect.height() > 1.0:
                    self._export_frame = rect
                self._export_frame_start = None
                self._export_frame_current = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
                return

            # ── Grundriss verschieben abschliessen ──
            if self._mode == ToolMode.MOVE_FLOOR_PLAN and self._active_floor_id:
                layer = self._floor_plans.get(self._active_floor_id)
                if layer:
                    self.floor_plan_transform_updated.emit(
                        self._active_floor_id,
                        layer.offset_x, layer.offset_y, layer.rotation)
                self._floor_drag_start = None
                # Stay in MOVE mode so user can drag again; ESC to exit
                self.setCursor(Qt.SizeAllCursor)
                return

            # ── Grundriss drehen abschliessen ──
            if self._mode == ToolMode.ROTATE_FLOOR_PLAN and self._active_floor_id:
                layer = self._floor_plans.get(self._active_floor_id)
                if layer:
                    self.floor_plan_transform_updated.emit(
                        self._active_floor_id,
                        layer.offset_x, layer.offset_y, layer.rotation)
                self._floor_drag_start = None
                # Stay in ROTATE mode; ESC to exit
                return

            if self._mode == ToolMode.MOVE_START and self._dragging_start:
                cid = self._dragging_start
                sp  = self._start_points.get(cid)
                if sp:
                    self.start_point_moved.emit(cid, (sp.x(), sp.y()))
                self._dragging_start = None
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                return
            if self._mode == ToolMode.MOVE_ROUTE_POINT and self._dragging_route_point:
                cid, _ = self._dragging_route_point
                self._dragging_route_point = None
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.route_changed.emit(cid)
                return
            if self._dragging_route_point and self._mode == ToolMode.EDIT_POLYGON:
                cid, _ = self._dragging_route_point
                self._dragging_route_point = None
                if self._edit_floor_polygon_id and cid == self._edit_floor_polygon_id:
                    self.update()
                    return
                self.polygon_changed.emit(cid)
                self.update()
                return
            if self._dragging_route_point and self._mode == ToolMode.EDIT_ROUTE:
                cid, _ = self._dragging_route_point
                self._dragging_route_point = None
                self.route_changed.emit(cid)
                return
            if self._mode == ToolMode.MOVE_ELEC_POINT and self._dragging_elec_point:
                pid = self._dragging_elec_point
                self._dragging_elec_point = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.elec_point_placed.emit(pid)
                # Emit cable changed for every cable connected to this AP
                for cid in list(self._cable_start_ap):
                    if self._cable_start_ap[cid] == pid:
                        self.elec_cable_changed.emit(cid)
                for cid in list(self._cable_end_ap):
                    if self._cable_end_ap[cid] == pid:
                        self.elec_cable_changed.emit(cid)
                return
            if self._dragging_route_point and self._mode == ToolMode.EDIT_ELEC_CABLE:
                cid, idx = self._dragging_route_point
                self._dragging_route_point = None
                # Update AP binding if first or last point was moved
                pts = self._elec_cables.get(cid, [])
                if pts and (idx == 0 or idx == len(pts) - 1):
                    ap = self._find_nearest_ap(pts[idx])
                    if idx == 0:
                        if ap:
                            self._cable_start_ap[cid] = ap
                            self._elec_cables[cid][0] = QPointF(
                                self._elec_points[ap])
                        else:
                            self._cable_start_ap.pop(cid, None)
                    else:
                        if ap:
                            self._cable_end_ap[cid] = ap
                            self._elec_cables[cid][-1] = QPointF(
                                self._elec_points[ap])
                        else:
                            self._cable_end_ap.pop(cid, None)
                self.elec_cable_changed.emit(cid)
                return
            if self._dragging_route_point and self._mode == ToolMode.EDIT_SUPPLY_LINE:
                cid, idx = self._dragging_route_point
                self._dragging_route_point = None
                # Update HKV binding if last point was moved
                pts = self._supply_lines.get(cid, [])
                if pts and idx == len(pts) - 1:
                    hkv = self._find_nearest_hkv(pts[-1])
                    if hkv:
                        self._supply_hkv[cid] = hkv
                        self._supply_lines[cid][-1] = QPointF(
                            self._hkv_points[hkv])
                    else:
                        self._supply_hkv.pop(cid, None)
                self.supply_line_changed.emit(cid)
                return
            if self._mode == ToolMode.MOVE_HKV and self._dragging_hkv:
                hid = self._dragging_hkv
                self._dragging_hkv = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.hkv_placed.emit(hid)
                # Emit supply_line_changed for connected supply lines
                for cid in list(self._supply_hkv):
                    if self._supply_hkv[cid] == hid:
                        self.supply_line_changed.emit(cid)
                # Emit hkv_line_changed for connected HKV lines
                for lid in list(self._hkv_line_start):
                    if self._hkv_line_start[lid] == hid:
                        self.hkv_line_changed.emit(lid)
                for lid in list(self._hkv_line_end):
                    if self._hkv_line_end[lid] == hid:
                        self.hkv_line_changed.emit(lid)
                return
            if self._dragging_route_point and self._mode == ToolMode.EDIT_HKV_LINE:
                lid, idx = self._dragging_route_point
                self._dragging_route_point = None
                pts = self._hkv_lines.get(lid, [])
                if pts and (idx == 0 or idx == len(pts) - 1):
                    hkv = self._find_nearest_hkv(pts[idx])
                    if idx == 0:
                        if hkv:
                            self._hkv_line_start[lid] = hkv
                            self._hkv_lines[lid][0] = QPointF(
                                self._hkv_points[hkv])
                        else:
                            self._hkv_line_start.pop(lid, None)
                    else:
                        if hkv:
                            self._hkv_line_end[lid] = hkv
                            self._hkv_lines[lid][-1] = QPointF(
                                self._hkv_points[hkv])
                        else:
                            self._hkv_line_end.pop(lid, None)
                self.hkv_line_changed.emit(lid)
                return
            if self._dragging_label:
                self._dragging_label = None
                self._label_drag_offset = QPointF(0, 0)
                self.setCursor(Qt.ArrowCursor)
                return
            if self._dragging_text:
                self._dragging_text = None
                self.setCursor(Qt.ArrowCursor)
                return
            self._panning   = False
            self._pan_start = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._mode           = ToolMode.NONE
            self._current_points = []
            self._current_furniture_id = None
            self._current_route_points = []
            self._current_route_cid = None
            self._current_route_preview_end = None
            self._dragging_start = None
            self._dragging_route_point = None
            self._edit_polygon_cid = None
            self._edit_floor_polygon_id = None
            self._edit_route_cid = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            # Elektro
            self._placing_elec_point_id = None
            self._current_elec_cable_id = None
            self._current_elec_cable_points = []
            self._current_elec_cable_preview = None
            self._edit_elec_cable_id = None
            self._dragging_elec_point = None
            # Supply line
            self._current_supply_cid = None
            self._current_supply_points = []
            self._current_supply_preview = None
            self._edit_supply_cid = None
            # HKV
            self._placing_hkv_id = None
            self._dragging_hkv = None
            self._current_hkv_line_id = None
            self._current_hkv_line_points = []
            self._current_hkv_line_preview = None
            self._edit_hkv_line_id = None
            self._dragging_label = None
            # Text annotations
            self._placing_text_id = None
            self._dragging_text = None
            # Floor plan move/rotate
            self._active_floor_id = None
            self._floor_drag_start = None
            # Measurement
            self._measure_p1 = None
            self._measure_p2 = None
            self._export_frame_start = None
            self._export_frame_current = None
            self.setCursor(Qt.ArrowCursor)
            self.mode_changed.emit()
            self.update()
        elif event.key() == Qt.Key_Delete and self._dragging_route_point:
            cid, idx = self._dragging_route_point
            if self._mode == ToolMode.EDIT_POLYGON and cid == self._edit_polygon_cid:
                self._delete_polygon_point(cid, idx)
            elif self._mode == ToolMode.EDIT_POLYGON and cid == self._edit_floor_polygon_id:
                self._delete_floor_polygon_point(cid, idx)
            elif self._mode == ToolMode.EDIT_ROUTE and cid == self._edit_route_cid:
                self._delete_route_point(cid, idx)
            self._dragging_route_point = None
            self.update()

    # ------------------------------------------------------------------ #
    #  Painting                                                            #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self._bg_color)

        painter.save()
        painter.translate(self._offset)
        painter.scale(self._scale, self._scale)

        # Background: floor plan layers (back → front)
        # Each layer is scaled so its real-world size matches the global
        # coordinate system (self._mm_per_px).  layer_scale converts from
        # the layer's native pixels to the canvas’ pixels.
        ref_mpp = self._mm_per_px if self._mm_per_px > 0 else 1.0
        for fid in self._floor_plan_order:
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            painter.save()
            # Real-world scaling: feste Abmessungen oder mm_per_px
            if layer.polygon:
                sw, sh = self._floor_polygon_render_size(layer)
            else:
                sw, sh = self._layer_render_size(layer)
            # Apply per-layer transform: translate then rotate around centre
            cx = sw / 2 + layer.offset_x
            cy = sh / 2 + layer.offset_y
            painter.translate(cx, cy)
            painter.rotate(layer.rotation)
            painter.translate(-sw / 2, -sh / 2)
            painter.setOpacity(layer.opacity)
            if layer.renderer:
                # QSvgRenderer fails when the effective pixel area
                # exceeds Qt's 256 MB allocation limit (~64M pixels).
                # Fall back to a capped intermediate pixmap.
                effective_w = sw * self._scale
                effective_h = sh * self._scale
                if effective_w * effective_h > 36_000_000 or effective_w > 10000 or effective_h > 10000:
                    cap = 6000.0
                    ratio = min(cap / max(effective_w, 1), cap / max(effective_h, 1), 1.0)
                    pm_w = max(1, int(effective_w * ratio))
                    pm_h = max(1, int(effective_h * ratio))
                    pm = QPixmap(pm_w, pm_h)
                    pm.fill(Qt.transparent)
                    pm_painter = QPainter(pm)
                    pm_painter.setRenderHint(QPainter.Antialiasing)
                    pm_painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    layer.renderer.render(pm_painter, QRectF(0, 0, pm_w, pm_h))
                    pm_painter.end()
                    painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    painter.drawPixmap(QRectF(0, 0, sw, sh), pm, QRectF(pm.rect()))
                else:
                    layer.renderer.render(painter, QRectF(0, 0, sw, sh))
            elif layer.pixmap:
                if sw > 0 and sh > 0:
                    painter.drawPixmap(QRectF(0, 0, sw, sh), layer.pixmap,
                                       QRectF(layer.pixmap.rect()))
            elif layer.polygon:
                bw = layer.size[0] if layer.size[0] > 0 else 1.0
                bh = layer.size[1] if layer.size[1] > 0 else 1.0
                poly = QPolygonF([
                    QPointF(p.x() * sw / bw, p.y() * sh / bh)
                    for p in layer.polygon
                ])
                fill = QColor(layer.polygon_color or "#8d99ae")
                fill.setAlpha(70)
                painter.setBrush(QBrush(fill))
                stroke = QColor(layer.polygon_color or "#8d99ae")
                stroke = stroke.lighter(140)
                painter.setPen(QPen(stroke, 2.0 / self._scale))
                painter.drawPolygon(poly)
            painter.restore()

        # Legacy single background (SVG or raster image)
        if not self._floor_plans:
            if self._svg_renderer:
                w, h = self._svg_size
                effective_w = w * self._scale
                effective_h = h * self._scale
                if effective_w * effective_h > 36_000_000 or effective_w > 10000 or effective_h > 10000:
                    cap = 6000.0
                    ratio = min(cap / max(effective_w, 1), cap / max(effective_h, 1), 1.0)
                    pm_w = max(1, int(effective_w * ratio))
                    pm_h = max(1, int(effective_h * ratio))
                    pm = QPixmap(pm_w, pm_h)
                    pm.fill(Qt.transparent)
                    pm_painter = QPainter(pm)
                    pm_painter.setRenderHint(QPainter.Antialiasing)
                    pm_painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    self._svg_renderer.render(pm_painter, QRectF(0, 0, pm_w, pm_h))
                    pm_painter.end()
                    painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    painter.drawPixmap(QRectF(0, 0, w, h), pm, QRectF(pm.rect()))
                else:
                    self._svg_renderer.render(painter, QRectF(0, 0, w, h))
            elif self._bg_pixmap:
                w, h = self._svg_size
                painter.drawPixmap(QRectF(0, 0, w, h), self._bg_pixmap,
                                   QRectF(self._bg_pixmap.rect()))

        # Grid overlay
        if self._grid_visible and self._mm_per_px > 0:
            self._draw_grid(painter)

        # Polygone
        for cid, pts in self._polygons.items():
            if not self._circuit_visible.get(cid, True):
                continue
            label = self._label_map.get(cid, cid)
            self._draw_polygon(painter, pts,
                               self._color_map.get(cid, QColor("blue")), label)

        # Collision zones (while dragging a route point or drawing a route)
        if self._dragging_route_point:
            drag_cid, drag_idx = self._dragging_route_point
            if (self._mode in (ToolMode.MOVE_ROUTE_POINT, ToolMode.EDIT_ROUTE)
                    and drag_cid in self._polygons):
                self._draw_collision_zones(painter, drag_cid, drag_idx)
        elif (self._mode == ToolMode.DRAW_ROUTE
              and self._current_route_cid
              and len(self._current_route_points) >= 1):
            self._draw_collision_zones(
                painter, self._current_route_cid,
                len(self._current_route_points) - 1)

        # Hilfslinien
        for cid, points in self._helper_lines.items():
            if not self._circuit_visible.get(cid, True):
                continue
            if self._show_helper_line.get(cid, False):
                self._draw_helper_line(painter, points,
                                       self._color_map.get(cid, QColor("white")))

        # Manuell gezeichnete Rohrverläufe
        for cid, points in self._manual_routes.items():
            if not self._circuit_visible.get(cid, True):
                continue
            self._draw_manual_route(
                painter,
                cid,
                points,
                self._color_map.get(cid, QColor("white"))
            )

        # Anschlussleitungen
        for cid, pts in self._supply_lines.items():
            if not self._circuit_visible.get(cid, True):
                continue
            self._draw_supply_line(painter, cid, pts)

        # Startpunkte
        for cid, sp in self._start_points.items():
            if not self._circuit_visible.get(cid, True):
                continue
            self._draw_start_point(painter, sp,
                                   self._color_map.get(cid, QColor("white")))

        # Referenzlinie
        self._draw_ref_line(painter)

        # Polygon in Arbeit
        if self._mode == ToolMode.DRAW_POLY and self._current_points:
            self._draw_in_progress(
                painter,
                self._color_map.get(self._current_circuit_id, QColor("gray"))
            )

        if self._mode == ToolMode.DRAW_FURNITURE_POLY and self._current_points:
            self._draw_in_progress(painter, QColor("#edf2f4"))

        if self._mode == ToolMode.DRAW_ROUTE and self._current_route_cid and self._current_route_points:
            self._draw_route_in_progress(
                painter,
                self._current_route_cid,
                self._current_route_points,
                self._color_map.get(self._current_route_cid, QColor("gray"))
            )

        if self._constraint_violation_line is not None:
            self._draw_constraint_violation(
                painter,
                self._constraint_violation_line[0],
                self._constraint_violation_line[1],
                self._constraint_violation_reason,
            )

        # Edit mode visualization
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_polygon_cid:
            self._draw_edit_polygon_overlay(painter, self._edit_polygon_cid)
        elif self._mode == ToolMode.EDIT_POLYGON and self._edit_floor_polygon_id:
            self._draw_edit_floor_polygon_overlay(
                painter, self._edit_floor_polygon_id)
            self._draw_floor_polygon_drag_distance_overlay(painter, self._edit_floor_polygon_id)
        elif self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid:
            self._draw_edit_route_overlay(painter, self._edit_route_cid)
        elif self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id:
            self._draw_edit_elec_cable_overlay(painter, self._edit_elec_cable_id)
        elif self._mode == ToolMode.EDIT_SUPPLY_LINE and self._edit_supply_cid:
            self._draw_edit_supply_line_overlay(painter, self._edit_supply_cid)
        elif self._mode == ToolMode.EDIT_HKV_LINE and self._edit_hkv_line_id:
            self._draw_edit_hkv_line_overlay(painter, self._edit_hkv_line_id)

        # Heizkreisverteiler
        for hid in self._hkv_points:
            if self._hkv_visible.get(hid, True):
                self._draw_hkv_point(painter, hid)

        # HKV Verbindungsleitungen
        for lid, pts in self._hkv_lines.items():
            if self._hkv_line_visible.get(lid, True):
                self._draw_hkv_line(painter, lid, pts)

        # HKV Leitung in Arbeit
        if (self._mode == ToolMode.DRAW_HKV_LINE
                and self._current_hkv_line_id):
            self._draw_hkv_line_in_progress(painter)

        # Elektro: Anschlusspunkte
        for pid in self._elec_points:
            if self._elec_visible.get(pid, True):
                self._draw_elec_point(painter, pid)

        # Elektro: Kabelverbindungen
        for cid, pts in self._elec_cables.items():
            if self._elec_visible.get(cid, True):
                self._draw_elec_cable(painter, cid, pts)

        # Kabel in Arbeit
        if (self._mode == ToolMode.DRAW_ELEC_CABLE
                and self._current_elec_cable_id):
            self._draw_elec_cable_in_progress(painter)

        # Anschlussleitung in Arbeit
        if (self._mode == ToolMode.DRAW_SUPPLY_LINE
                and self._current_supply_cid):
            self._draw_supply_line_in_progress(painter)

        # ── Labels (drawn last, always on top) ────────────────────────
        self._label_rects.clear()
        self._label_draw_pos.clear()
        for cid, pts in self._polygons.items():
            if not self._circuit_visible.get(cid, True):
                continue
            color = self._color_map.get(cid, QColor("blue"))
            text = self._label_map.get(cid, cid)
            default_pos = QPointF(
                sum(p.x() for p in pts) / len(pts),
                sum(p.y() for p in pts) / len(pts))
            self._draw_item_label(painter, cid, default_pos, text, color)
        for pid in self._elec_points:
            if not self._elec_visible.get(pid, True):
                continue
            pos = self._elec_points[pid]
            w, h = self._elec_point_size_px.get(pid, (30, 30))
            default_pos = QPointF(pos.x(), pos.y() + h / 2 + 14.0)
            text = self._label_map.get(pid, pid)
            self._draw_item_label(painter, pid, default_pos, text,
                                  self._color_map.get(pid, QColor("#4fc3f7")))
        for kid, kpts in self._elec_cables.items():
            if not self._elec_visible.get(kid, True):
                continue
            if len(kpts) < 2:
                continue
            mi = len(kpts) // 2
            if len(kpts) % 2 == 1:
                mid = kpts[mi]
            else:
                mid = QPointF((kpts[mi - 1].x() + kpts[mi].x()) / 2,
                              (kpts[mi - 1].y() + kpts[mi].y()) / 2)
            text = self._label_map.get(kid, kid)
            col = self._color_map.get(kid, QColor("#ff9800"))
            self._draw_item_label(painter, kid, mid, text, col)

        # HKV labels
        for hid in self._hkv_points:
            if not self._hkv_visible.get(hid, True):
                continue
            pos = self._hkv_points[hid]
            w, h = self._hkv_size_px.get(hid, (30, 30))
            default_pos = QPointF(pos.x(), pos.y() + h / 2 + 14.0)
            text = self._label_map.get(hid, hid)
            self._draw_item_label(painter, hid, default_pos, text,
                                  self._color_map.get(hid, QColor("#e53935")))
        # HKV line labels
        for lid, lpts in self._hkv_lines.items():
            if not self._hkv_line_visible.get(lid, True):
                continue
            if len(lpts) < 2:
                continue
            mi = len(lpts) // 2
            if len(lpts) % 2 == 1:
                mid = lpts[mi]
            else:
                mid = QPointF((lpts[mi - 1].x() + lpts[mi].x()) / 2,
                              (lpts[mi - 1].y() + lpts[mi].y()) / 2)
            text = self._label_map.get(lid, lid)
            col = self._color_map.get(lid, QColor("#e53935"))
            self._draw_item_label(painter, lid, mid, text, col)

        # ── Text-Annotationen ─────────────────────────────────────
        self._draw_text_annotations(painter)

        # ── Messlinien ────────────────────────────────────────────
        self._draw_measurements(painter)

        # ── Export-Rahmen ─────────────────────────────────────────
        self._draw_export_frame(painter)

        # ── Maße beim Verschieben anzeigen ────────────────────────
        self._draw_drag_distance_overlay(painter)

        painter.restore()

    # ── Measurement drawing ───────────────────────────────────────── #

    def _draw_measurements(self, painter: QPainter):
        color = QColor("#00e5ff")
        r = 4.0 / self._scale
        pen = QPen(color, 2.0 / self._scale, Qt.DashDotLine)
        font = painter.font()
        font.setPointSizeF(10.0 / self._scale)

        # Draw persisted measurement lines
        for p1, p2, mm_len in self._measure_lines:
            painter.setPen(pen)
            painter.drawLine(p1, p2)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(p1, r, r)
            painter.drawEllipse(p2, r, r)
            mid = QPointF(
                (p1.x() + p2.x()) / 2,
                (p1.y() + p2.y()) / 2 - 10 / self._scale,
            )
            painter.setFont(font)
            painter.setPen(QPen(color))
            painter.drawText(mid, f"{mm_len / 1000:.3f} m")

        # Draw in-progress measurement
        if self._mode == ToolMode.MEASURE and self._measure_p1:
            p2 = self._mouse_pos if self._mouse_pos else self._measure_p1
            painter.setPen(pen)
            painter.drawLine(self._measure_p1, p2)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(self._measure_p1, r, r)
            painter.drawEllipse(p2, r, r)
            if self._mm_per_px > 0:
                px_len = _qdist(self._measure_p1, p2)
                mm_len = px_len * self._mm_per_px
                mid = QPointF(
                    (self._measure_p1.x() + p2.x()) / 2,
                    (self._measure_p1.y() + p2.y()) / 2 - 10 / self._scale,
                )
                painter.setFont(font)
                painter.setPen(QPen(color))
                painter.drawText(mid, f"{mm_len / 1000:.3f} m")

    def _draw_export_frame(self, painter: QPainter):
        """Draw persisted and in-progress export frame."""
        frame = self._export_frame
        if self._mode == ToolMode.DRAW_EXPORT_FRAME and self._export_frame_start:
            end_pt = self._export_frame_current or self._export_frame_start
            frame = QRectF(self._export_frame_start, end_pt).normalized()

        if not frame:
            return

        pen = QPen(QColor("#00e676"), 2.0 / self._scale, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(frame)

        text = f"Export: {frame.width():.0f} × {frame.height():.0f} px"
        font = painter.font()
        font.setPointSizeF(10.0 / self._scale)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#00e676")))
        painter.drawText(QPointF(frame.x(), frame.y() - 6.0 / self._scale), text)

    # ── Text Annotations drawing ─────────────────────────────────── #

    def _draw_text_annotations(self, painter: QPainter):
        """Render all visible text annotations on the canvas."""
        self._text_rects.clear()
        for tid, pos in self._text_annotations.items():
            if not self._text_visible.get(tid, True):
                continue
            content = self._text_contents.get(tid, "")
            if not content:
                continue
            size = self._text_font_sizes.get(tid, 14.0)
            color_hex = self._text_colors.get(tid, "#ffffff")
            font = painter.font()
            font.setPointSizeF(size / self._scale)
            painter.setFont(font)
            fm = painter.fontMetrics()
            lines = content.split("\n")
            line_height = fm.height()
            max_width = max(fm.horizontalAdvance(line) for line in lines) if lines else 0
            total_height = line_height * len(lines)
            # Background
            pad = 4.0 / self._scale
            bg_rect = QRectF(pos.x() - pad,
                             pos.y() - fm.ascent() - pad,
                             max_width + 2 * pad,
                             total_height + 2 * pad)
            bg = QColor("#2b2b2b")
            bg.setAlpha(180)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawRoundedRect(bg_rect, 3.0 / self._scale, 3.0 / self._scale)
            # Text
            painter.setPen(QPen(QColor(color_hex)))
            painter.setBrush(Qt.NoBrush)
            for i, line in enumerate(lines):
                painter.drawText(
                    QPointF(pos.x(), pos.y() + i * line_height), line)
            # Store rect for hit testing
            self._text_rects[tid] = bg_rect

    # ── Drag-Distance Overlay ─────────────────────────────────────── #

    def _get_drag_neighbor_segments(self) -> list[tuple[QPointF, QPointF]]:
        """Return line segments adjacent to the currently dragged point."""
        segs: list[tuple[QPointF, QPointF]] = []

        if self._dragging_route_point:
            oid, idx = self._dragging_route_point
            pts: list[QPointF] | None = None
            is_polygon = False

            if self._mode == ToolMode.EDIT_POLYGON and oid == self._edit_polygon_cid:
                pts = self._polygons.get(oid)
                is_polygon = True
            elif self._mode == ToolMode.EDIT_ROUTE and oid == self._edit_route_cid:
                pts = self._manual_routes.get(oid)
            elif self._mode == ToolMode.MOVE_ROUTE_POINT:
                pts = self._manual_routes.get(oid)
            elif self._mode == ToolMode.EDIT_ELEC_CABLE and oid == self._edit_elec_cable_id:
                pts = self._elec_cables.get(oid)
            elif self._mode == ToolMode.EDIT_SUPPLY_LINE and oid == self._edit_supply_cid:
                pts = self._supply_lines.get(oid)
            elif self._mode == ToolMode.EDIT_HKV_LINE and oid == self._edit_hkv_line_id:
                pts = self._hkv_lines.get(oid)

            if pts and 0 <= idx < len(pts):
                cur = pts[idx]
                if is_polygon:
                    prev_idx = (idx - 1) % len(pts)
                    next_idx = (idx + 1) % len(pts)
                    segs.append((cur, pts[prev_idx]))
                    segs.append((cur, pts[next_idx]))
                else:
                    if idx > 0:
                        segs.append((cur, pts[idx - 1]))
                    if idx < len(pts) - 1:
                        segs.append((cur, pts[idx + 1]))

        return segs

    def _draw_drag_distance_overlay(self, painter: QPainter):
        """Draw distance annotations on segments adjacent to a dragged point."""
        segs = self._get_drag_neighbor_segments()
        if not segs:
            return

        mm_per_px = self._mm_per_px
        if mm_per_px <= 0:
            return

        painter.save()
        font = painter.font()
        font.setPointSizeF(10.0 / self._scale)
        painter.setFont(font)
        fm = painter.fontMetrics()

        for a, b in segs:
            dist_px = _qdist(a, b)
            dist_m = dist_px * mm_per_px / 1000.0

            mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
            dx = b.x() - a.x()
            dy = b.y() - a.y()
            length = math.hypot(dx, dy)
            if length < 1e-6:
                continue

            # Offset label perpendicular to the segment
            nx, ny = -dy / length, dx / length
            offset = 8.0 / self._scale
            label_pos = QPointF(mid.x() + nx * offset, mid.y() + ny * offset)

            text = f"{dist_m:.2f} m"
            tw = fm.horizontalAdvance(text)
            th = fm.height()

            # Background
            bg_rect = QRectF(label_pos.x() - tw / 2 - 2,
                             label_pos.y() - th / 2 - 1,
                             tw + 4, th + 2)
            bg = QColor("#000000")
            bg.setAlpha(180)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawRoundedRect(bg_rect, 3, 3)

            # Dashed measurement line
            pen = QPen(QColor("#ffdd00"), 1.0 / self._scale, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(a, b)

            # Text
            painter.setPen(QPen(QColor("#ffdd00")))
            painter.drawText(
                QRectF(label_pos.x() - tw / 2, label_pos.y() - th / 2,
                       tw, th),
                Qt.AlignCenter, text)

        painter.restore()

    def _draw_floor_polygon_drag_distance_overlay(self, painter: QPainter, fp_id: str):
        """Draw distance annotations for segments adjacent to a dragged polygon point."""
        if not self._dragging_route_point or self._dragging_route_point[0] != fp_id:
            return

        _, idx = self._dragging_route_point
        pts = self._floor_polygon_points_world(fp_id)
        if not pts or idx < 0 or idx >= len(pts):
            return

        mm_per_px = self._mm_per_px
        if mm_per_px <= 0:
            return

        # Get adjacent segments
        segments = []
        if idx > 0:
            segments.append((pts[idx - 1], pts[idx]))
        if idx < len(pts) - 1:
            segments.append((pts[idx], pts[idx + 1]))

        if not segments:
            return

        painter.save()
        font = painter.font()
        font.setPointSizeF(10.0 / self._scale)
        painter.setFont(font)
        fm = painter.fontMetrics()

        for a, b in segments:
            dist_px = _qdist(a, b)
            dist_m = dist_px * mm_per_px / 1000.0

            mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
            dx = b.x() - a.x()
            dy = b.y() - a.y()
            length = math.hypot(dx, dy)
            if length < 1e-6:
                continue

            # Offset label perpendicular to the segment
            nx, ny = -dy / length, dx / length
            offset = 8.0 / self._scale
            label_pos = QPointF(mid.x() + nx * offset, mid.y() + ny * offset)

            text = f"{dist_m:.2f} m"
            tw = fm.horizontalAdvance(text)
            th = fm.height()

            # Background
            bg_rect = QRectF(label_pos.x() - tw / 2 - 2,
                             label_pos.y() - th / 2 - 1,
                             tw + 4, th + 2)
            bg = QColor("#000000")
            bg.setAlpha(180)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawRoundedRect(bg_rect, 3, 3)

            # Dashed measurement line
            pen = QPen(QColor("#ffdd00"), 1.0 / self._scale, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(a, b)

            # Text
            painter.setPen(QPen(QColor("#ffdd00")))
            painter.drawText(
                QRectF(label_pos.x() - tw / 2, label_pos.y() - th / 2,
                       tw, th),
                Qt.AlignCenter, text)

        painter.restore()

    # ── Label helpers ──────────────────────────────────────────────── #

    def _draw_item_label(self, painter, item_id: str,
                          default_pos: QPointF, text: str, color: QColor):
        pos = self._label_positions.get(item_id, default_pos)
        size = self._label_font_sizes.get(item_id, 12.0)
        font = painter.font()
        font.setPointSizeF(size / self._scale)
        painter.setFont(font)
        # background for readability
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        bg_rect = QRectF(pos.x() - 2, pos.y() - fm.ascent() - 1,
                         tw + 4, th + 2)
        bg = QColor("#2b2b2b")
        bg.setAlpha(160)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(bg_rect, 2, 2)
        # text
        painter.setPen(QPen(color))
        painter.setBrush(Qt.NoBrush)
        painter.drawText(pos, text)
        # store for hit testing & dragging
        self._label_rects[item_id] = bg_rect
        self._label_draw_pos[item_id] = pos

    def _hit_label(self, canvas_pt: QPointF) -> Optional[str]:
        for item_id, rect in self._label_rects.items():
            if rect.contains(canvas_pt):
                return item_id
        return None

    # ------------------------------------------------------------------ #
    #  Grid drawing                                                        #
    # ------------------------------------------------------------------ #
    def _draw_grid(self, painter: QPainter):
        """Draw a regular grid overlay based on _grid_spacing_mm and _mm_per_px."""
        if self._mm_per_px <= 0:
            return
        spacing_px = self._grid_spacing_mm / self._mm_per_px
        if spacing_px < 2:
            return  # too dense to draw

        # Compute the visible canvas rectangle from the viewport
        vw, vh = self.width(), self.height()
        x0 = -self._offset.x() / self._scale
        y0 = -self._offset.y() / self._scale
        x1 = x0 + vw / self._scale
        y1 = y0 + vh / self._scale

        # Snap start to grid
        gx0 = (x0 // spacing_px) * spacing_px
        gy0 = (y0 // spacing_px) * spacing_px

        pen = QPen(self._grid_color)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setPen(pen)

        # vertical lines
        x = gx0
        while x <= x1:
            painter.drawLine(QPointF(x, y0), QPointF(x, y1))
            x += spacing_px

        # horizontal lines
        y = gy0
        while y <= y1:
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))
            y += spacing_px

    def _draw_polygon(self, painter, pts, color, label):
        if not pts:
            return
        poly = QPolygonF(pts)
        fill = QColor(color)
        fill.setAlpha(35)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(color, 2.0 / self._scale))
        painter.drawPolygon(poly)

    def _draw_start_point(self, painter, sp, color):
        r = 7.0 / self._scale
        path = QPainterPath()
        path.moveTo(sp.x(),     sp.y() - r)
        path.lineTo(sp.x() + r, sp.y())
        path.lineTo(sp.x(),     sp.y() + r)
        path.lineTo(sp.x() - r, sp.y())
        path.closeSubpath()
        fill = QColor(color)
        fill.setAlpha(200)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(Qt.white, 1.5 / self._scale))
        painter.drawPath(path)
        font = painter.font()
        font.setPointSizeF(9.0 / self._scale)
        painter.setFont(font)
        painter.setPen(QPen(Qt.white))
        painter.drawText(
            QPointF(sp.x() + r + 2 / self._scale, sp.y() + r / 2), "S"
        )

    def _draw_ref_line(self, painter):
        if not self._show_ref_line:
            return
        color = QColor("#ffdd00")
        r = 4.0 / self._scale
        pen = QPen(color, 2.0 / self._scale, Qt.DashLine)

        # Draw per-floor-plan ref lines (completed calibrations)
        drawn_floor_ids = set()
        for fid in self._floor_plan_order:
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            if layer.ref_p1 and layer.ref_p2:
                # Skip the currently-being-drawn ref (shown via _ref_p1/_ref_p2)
                if self._ref_floor_id == fid and self._mode == ToolMode.DRAW_REF:
                    continue
                drawn_floor_ids.add(fid)
                painter.setPen(pen)
                painter.drawLine(layer.ref_p1, layer.ref_p2)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(layer.ref_p1, r, r)
                painter.drawEllipse(layer.ref_p2, r, r)
                mid = QPointF(
                    (layer.ref_p1.x() + layer.ref_p2.x()) / 2,
                    (layer.ref_p1.y() + layer.ref_p2.y()) / 2
                    - 10 / self._scale,
                )
                font = painter.font()
                font.setPointSizeF(10.0 / self._scale)
                painter.setFont(font)
                painter.setPen(QPen(color))
                painter.drawText(mid, f"{layer.ref_length_mm / 1000:.3f} m")

        # Draw the active / in-progress ref line (skip if already drawn above)
        if self._ref_floor_id and self._ref_floor_id in drawn_floor_ids:
            return
        # Hide ref line when its floor plan is hidden
        if self._ref_floor_id:
            fl = self._floor_plans.get(self._ref_floor_id)
            if fl and not fl.visible:
                return
        # If floor plans exist but no _ref_floor_id is set, the global
        # ref line is an orphan — don't draw it (each floor plan has its own).
        if self._floor_plans and not self._ref_floor_id and self._mode != ToolMode.DRAW_REF:
            return
        if self._ref_p1 is None:
            return
        p2 = self._ref_p2 if self._ref_p2 else self._mouse_pos
        if p2 is None:
            return
        painter.setPen(pen)
        painter.drawLine(self._ref_p1, p2)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(self._ref_p1, r, r)
        if self._ref_p2:
            painter.drawEllipse(self._ref_p2, r, r)
            px_len = _qdist(self._ref_p1, self._ref_p2)
            mm_len = px_len * self._mm_per_px
            mid = QPointF(
                (self._ref_p1.x() + self._ref_p2.x()) / 2,
                (self._ref_p1.y() + self._ref_p2.y()) / 2
                - 10 / self._scale,
            )
            font = painter.font()
            font.setPointSizeF(10.0 / self._scale)
            painter.setFont(font)
            painter.setPen(QPen(color))
            painter.drawText(mid, f"{mm_len / 1000:.3f} m")

    def _draw_helper_line(self, painter, points: List[QPointF], color: QColor):
        if len(points) < 2:
            return
        painter.setPen(QPen(color, 1.0 / self._scale, Qt.DashLine))
        prev = points[0]
        for pt in points[1:]:
            painter.drawLine(prev, pt)
            prev = pt

    def _draw_manual_route(self, painter, cid: str,
                           points: List[QPointF], color: QColor):
        if len(points) < 2:
            return

        line_dist = self._route_line_dist_px.get(cid, 0.0)
        offset = line_dist / 2.0

        line1 = self._offset_route_points(points, offset)
        line2 = self._offset_route_points(points, -offset)

        # Build one continuous loop: line1 forward → line2 reversed
        combined = list(line1) + list(reversed(line2))

        pen = QPen(color, 2.0 / self._scale)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if len(combined) > 1:
            painter.drawPath(self._smooth_polyline_path(combined, offset))

        # Draw control points
        painter.setBrush(QBrush(color))
        r = 3.2 / self._scale
        for i, pt in enumerate(points):
            if i == 0:
                continue
            painter.drawEllipse(pt, r, r)

    def _draw_route_in_progress(self, painter, cid: str,
                                points: List[QPointF], color: QColor):
        if not points:
            return
        raw_pts = list(points)
        if self._current_route_preview_end is not None:
            raw_pts.append(self._current_route_preview_end)

        line_dist = self._route_line_dist_px.get(cid, 0.0)
        offset = line_dist / 2.0

        line1 = self._offset_route_points(raw_pts, offset)
        line2 = self._offset_route_points(raw_pts, -offset)

        # Build one continuous loop: line1 forward → line2 reversed
        combined = list(line1) + list(reversed(line2))

        pen = QPen(color, 2.0 / self._scale, Qt.DashLine)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if len(combined) > 1:
            painter.drawPath(self._smooth_polyline_path(combined, offset))

        # Draw control points
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for i, p in enumerate(points):
            if i == 0:
                continue
            painter.drawEllipse(p, r, r)

    # ── Elektro drawing ─────────────────────────────────────────────── #

    def _draw_elec_point(self, painter, point_id: str):
        pos = self._elec_points.get(point_id)
        if pos is None:
            return
        w, h = self._elec_point_size_px.get(point_id, (30, 30))
        rect = QRectF(pos.x() - w / 2, pos.y() - h / 2, w, h)
        color = self._color_map.get(point_id, QColor("#4fc3f7"))
        fill = QColor(color)
        fill.setAlpha(60)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(color, 2.0 / self._scale))
        painter.drawRect(rect)
        svg_r = self._elec_point_svgs.get(point_id)
        if svg_r and svg_r.isValid():
            svg_r.render(painter, rect)
        else:
            icon = self._elec_point_icons.get(point_id)
            if icon and not icon.isNull():
                painter.drawPixmap(rect.toRect(), icon)

    def _draw_elec_cable(self, painter, cable_id: str,
                          points: List[QPointF]):
        if len(points) < 2:
            return
        color = self._color_map.get(cable_id, QColor("#ff9800"))
        pen = QPen(color, 2.0 / self._scale)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        rounding = 8.0 / self._scale
        painter.drawPath(self._smooth_polyline_path(points, rounding))
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for pt in points:
            painter.drawEllipse(pt, r, r)

    def _draw_elec_cable_in_progress(self, painter):
        if not self._current_elec_cable_points:
            return
        color = self._color_map.get(
            self._current_elec_cable_id, QColor("#ff9800"))
        pts = list(self._current_elec_cable_points)
        if self._current_elec_cable_preview is not None:
            pts.append(self._current_elec_cable_preview)
        if len(pts) < 2:
            painter.setBrush(QBrush(color))
            r = 3.0 / self._scale
            painter.drawEllipse(pts[0], r, r)
            return
        pen = QPen(color, 2.0 / self._scale, Qt.DashLine)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        rounding = 8.0 / self._scale
        painter.drawPath(self._smooth_polyline_path(pts, rounding))
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for pt in self._current_elec_cable_points:
            painter.drawEllipse(pt, r, r)

    def _draw_edit_elec_cable_overlay(self, painter, cable_id: str):
        pts = self._elec_cables.get(cable_id, [])
        if not pts:
            return
        color = self._color_map.get(cable_id, QColor("#ff9800"))
        r = 5.0 / self._scale
        for i, p in enumerate(pts):
            if (self._dragging_route_point
                    and self._dragging_route_point[0] == cable_id
                    and self._dragging_route_point[1] == i):
                painter.setBrush(QBrush(QColor("#ff6b6b")))
            else:
                painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#ffffff"), 1.0 / self._scale))
            painter.drawEllipse(p, r, r)

    # ── Supply line drawing ─────────────────────────────────────────── #

    def _draw_supply_line(self, painter, cid: str, points: List[QPointF]):
        if len(points) < 2:
            return
        color = self._color_map.get(cid, QColor("white"))
        line_dist = self._route_line_dist_px.get(cid, 0.0)
        offset = line_dist / 2.0

        line1 = self._offset_route_points(points, offset)
        line2 = self._offset_route_points(points, -offset)
        combined = list(line1) + list(reversed(line2))

        pen = QPen(color, 2.0 / self._scale, Qt.DashDotLine)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if len(combined) > 1:
            painter.drawPath(self._smooth_polyline_path(combined, offset))

        # Draw control points
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for i, pt in enumerate(points):
            if i == 0:
                continue
            painter.drawEllipse(pt, r, r)

    def _draw_supply_line_in_progress(self, painter):
        if not self._current_supply_points:
            return
        cid = self._current_supply_cid
        color = self._color_map.get(cid, QColor("white"))
        raw_pts = list(self._current_supply_points)
        if self._current_supply_preview is not None:
            raw_pts.append(self._current_supply_preview)
        if len(raw_pts) < 2:
            painter.setBrush(QBrush(color))
            r = 3.0 / self._scale
            painter.drawEllipse(raw_pts[0], r, r)
            return

        line_dist = self._route_line_dist_px.get(cid, 0.0)
        offset = line_dist / 2.0

        line1 = self._offset_route_points(raw_pts, offset)
        line2 = self._offset_route_points(raw_pts, -offset)
        combined = list(line1) + list(reversed(line2))

        pen = QPen(color, 2.0 / self._scale, Qt.DashLine)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if len(combined) > 1:
            painter.drawPath(self._smooth_polyline_path(combined, offset))

        # Draw control points
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for i, p in enumerate(self._current_supply_points):
            if i == 0:
                continue
            painter.drawEllipse(p, r, r)

    def _draw_edit_supply_line_overlay(self, painter, cid: str):
        pts = self._supply_lines.get(cid, [])
        if not pts:
            return
        color = self._color_map.get(cid, QColor("white"))
        r = 5.0 / self._scale
        for i, p in enumerate(pts):
            if (self._dragging_route_point
                    and self._dragging_route_point[0] == cid
                    and self._dragging_route_point[1] == i):
                painter.setBrush(QBrush(QColor("#ff6b6b")))
            else:
                painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#ffffff"), 1.0 / self._scale))
            painter.drawEllipse(p, r, r)

    # ── HKV drawing ──────────────────────────────────────────────────── #

    def _draw_hkv_point(self, painter, hkv_id: str):
        pos = self._hkv_points.get(hkv_id)
        if pos is None:
            return
        w, h = self._hkv_size_px.get(hkv_id, (30, 30))
        rect = QRectF(pos.x() - w / 2, pos.y() - h / 2, w, h)
        color = self._color_map.get(hkv_id, QColor("#e53935"))
        fill = QColor(color)
        fill.setAlpha(60)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(color, 2.0 / self._scale))
        painter.drawRoundedRect(rect, 4.0 / self._scale, 4.0 / self._scale)
        svg_r = self._hkv_svgs.get(hkv_id)
        if svg_r and svg_r.isValid():
            svg_r.render(painter, rect)
        else:
            icon = self._hkv_icons.get(hkv_id)
            if icon and not icon.isNull():
                painter.drawPixmap(rect.toRect(), icon)

    def _draw_hkv_line(self, painter, lid: str, points: List[QPointF]):
        """Draw HKV connecting line as double pipe (like supply lines)."""
        if len(points) < 2:
            return
        color = self._color_map.get(lid, QColor("#e53935"))
        offset = 3.0 / self._scale  # fixed offset for double line
        line1 = self._offset_route_points(points, offset)
        line2 = self._offset_route_points(points, -offset)
        pen = QPen(color, 2.0 / self._scale, Qt.SolidLine)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if len(line1) > 1:
            path1 = QPainterPath()
            path1.moveTo(line1[0])
            for p in line1[1:]:
                path1.lineTo(p)
            painter.drawPath(path1)
        if len(line2) > 1:
            path2 = QPainterPath()
            path2.moveTo(line2[0])
            for p in line2[1:]:
                path2.lineTo(p)
            painter.drawPath(path2)
        # End connector
        if line1 and line2:
            painter.drawLine(line1[-1], line2[-1])
            painter.drawLine(line1[0], line2[0])
        # Control points
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for p in points:
            painter.drawEllipse(p, r, r)

    def _draw_hkv_line_in_progress(self, painter):
        if not self._current_hkv_line_points:
            return
        lid = self._current_hkv_line_id
        color = self._color_map.get(lid, QColor("#e53935"))
        raw_pts = list(self._current_hkv_line_points)
        if self._current_hkv_line_preview is not None:
            raw_pts.append(self._current_hkv_line_preview)
        if len(raw_pts) < 2:
            painter.setBrush(QBrush(color))
            r = 3.0 / self._scale
            painter.drawEllipse(raw_pts[0], r, r)
            return
        offset = 3.0 / self._scale
        line1 = self._offset_route_points(raw_pts, offset)
        line2 = self._offset_route_points(raw_pts, -offset)
        pen = QPen(color, 2.0 / self._scale, Qt.DashLine)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for line in (line1, line2):
            if len(line) > 1:
                path = QPainterPath()
                path.moveTo(line[0])
                for p in line[1:]:
                    path.lineTo(p)
                painter.drawPath(path)
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for p in self._current_hkv_line_points:
            painter.drawEllipse(p, r, r)

    def _draw_edit_hkv_line_overlay(self, painter, lid: str):
        pts = self._hkv_lines.get(lid, [])
        if not pts:
            return
        color = self._color_map.get(lid, QColor("#e53935"))
        r = 5.0 / self._scale
        for i, p in enumerate(pts):
            if (self._dragging_route_point
                    and self._dragging_route_point[0] == lid
                    and self._dragging_route_point[1] == i):
                painter.setBrush(QBrush(QColor("#ff6b6b")))
            else:
                painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#ffffff"), 1.0 / self._scale))
            painter.drawEllipse(p, r, r)

    @staticmethod
    def _smooth_polyline_path(points: List[QPointF],
                              rounding: float) -> QPainterPath:
        """Build a QPainterPath with quadratic Bézier curves at every corner.

        The rounding distance adapts to the corner angle: sharper angles
        get a longer curve (more rounding), gentle angles get less.
        *rounding* is the base distance; at 90° it equals *rounding*,
        at sharper angles it grows up to 3×, at gentle angles it shrinks.
        """
        path = QPainterPath()
        n = len(points)
        if n < 2:
            if n == 1:
                path.moveTo(points[0])
            return path
        if n == 2 or rounding <= 0:
            path.moveTo(points[0])
            for pt in points[1:]:
                path.lineTo(pt)
            return path

        rd = abs(rounding)
        path.moveTo(points[0])

        for i in range(1, n - 1):
            prev = points[i - 1]
            curr = points[i]
            nxt  = points[i + 1]

            # Distances to neighbours
            d_in  = _qdist(prev, curr)
            d_out = _qdist(curr, nxt)
            if d_in < 1e-6 or d_out < 1e-6:
                path.lineTo(curr)
                continue

            # Unit vectors
            ux_in  = (curr.x() - prev.x()) / d_in
            uy_in  = (curr.y() - prev.y()) / d_in
            ux_out = (nxt.x()  - curr.x()) / d_out
            uy_out = (nxt.y()  - curr.y()) / d_out

            # Cosine of the turning angle (dot product of directions)
            dot = ux_in * ux_out + uy_in * uy_out
            dot = max(-1.0, min(1.0, dot))

            # Scale factor: 1.0 at 90°, up to 3.0 at very sharp, down to 0.3 at gentle
            # dot = 0 → 90°, dot = -1 → 180° (U-turn), dot = 1 → 0° (straight)
            # We want: sharper (smaller dot / more negative) → bigger rounding
            scale = 1.0 + (1.0 - dot) * 0.8   # range ~0.2 (straight) to ~2.6 (U-turn)
            effective_rd = rd * scale

            # Clamp so we don't overshoot the segment
            t_in  = min(effective_rd, d_in  * 0.45)
            t_out = min(effective_rd, d_out * 0.45)

            # Points where the curve starts / ends
            frac_in  = t_in  / d_in
            frac_out = t_out / d_out
            p_start = QPointF(curr.x() + (prev.x() - curr.x()) * frac_in,
                              curr.y() + (prev.y() - curr.y()) * frac_in)
            p_end   = QPointF(curr.x() + (nxt.x()  - curr.x()) * frac_out,
                              curr.y() + (nxt.y()  - curr.y()) * frac_out)

            path.lineTo(p_start)
            path.quadTo(curr, p_end)

        path.lineTo(points[-1])
        return path

    def _offset_route_points(self, points: List[QPointF], offset: float) -> List[QPointF]:
        """Compute a truly parallel polyline at perpendicular distance *offset*.

        Each segment is shifted by *offset* along its normal, then adjacent
        offset segments are intersected to find the correct corner vertex.
        This keeps the lines genuinely parallel (inner side shorter, outer
        side longer at corners).
        """
        n = len(points)
        if n < 2:
            return list(points)
        if abs(offset) < 1e-9:
            return list(points)

        # 1. For every original segment compute the offset line (two endpoints).
        seg_lines: List[Tuple[QPointF, QPointF]] = []
        for i in range(n - 1):
            a, b = points[i], points[i + 1]
            dx = b.x() - a.x()
            dy = b.y() - a.y()
            length = math.hypot(dx, dy)
            if length < 1e-9:
                seg_lines.append((a, b))
                continue
            nx = -dy / length * offset
            ny =  dx / length * offset
            seg_lines.append((
                QPointF(a.x() + nx, a.y() + ny),
                QPointF(b.x() + nx, b.y() + ny),
            ))

        # 2. Build the result polyline.
        result: List[QPointF] = []
        # First point: start of first offset segment
        result.append(seg_lines[0][0])

        # Corner points: intersect consecutive offset segments
        for i in range(len(seg_lines) - 1):
            a1, a2 = seg_lines[i]
            b1, b2 = seg_lines[i + 1]
            pt = _line_line_intersection(a1, a2, b1, b2)
            if pt is not None:
                result.append(pt)
            else:
                # Parallel segments – just use the endpoint of the first
                result.append(seg_lines[i][1])

        # Last point: end of last offset segment
        result.append(seg_lines[-1][1])
        return result

    def _apply_angle_snap(self, target: QPointF) -> QPointF:
        """Snap *target* to the nearest multiple of self._snap_angle
        relative to the last route point.  If snap_angle is 0 or there
        is no previous point, return *target* unchanged.
        """
        if self._snap_angle <= 0 or not self._current_route_points:
            return target
        anchor = self._current_route_points[-1]
        dx = target.x() - anchor.x()
        dy = target.y() - anchor.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return target

        angle_deg = math.degrees(math.atan2(dy, dx))
        step = self._snap_angle
        snapped_angle = round(angle_deg / step) * step

        # Snap tolerance: if the free angle is within 8° of a snap line, snap
        diff = abs(angle_deg - snapped_angle)
        if diff > 8.0:
            return target

        rad = math.radians(snapped_angle)
        return QPointF(anchor.x() + math.cos(rad) * dist,
                       anchor.y() + math.sin(rad) * dist)

    def _snap_to_grid(self, pt: QPointF) -> QPointF:
        """Snap *pt* to the nearest grid intersection when grid is visible."""
        if not self._grid_visible or self._mm_per_px <= 0:
            return pt
        spacing_px = self._grid_spacing_mm / self._mm_per_px
        if spacing_px < 1.0:
            return pt
        x = round(pt.x() / spacing_px) * spacing_px
        y = round(pt.y() / spacing_px) * spacing_px
        return QPointF(x, y)

    def _draw_constraint_violation(self, painter,
                                   line_start: QPointF, line_end: QPointF,
                                   reason: str = ""):
        painter.setPen(QPen(QColor("#e63946"), 5.0 / self._scale, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(line_start, line_end)
        if reason:
            # Compute label position in canvas coords, then convert to screen
            cx = (line_start.x() + line_end.x()) / 2
            cy = (line_start.y() + line_end.y()) / 2
            # Save the current (scaled) transform and switch to screen coords
            painter.save()
            painter.resetTransform()
            # Map canvas point → screen point
            sx = self._offset.x() + cx * self._scale
            sy = self._offset.y() + cy * self._scale - 14
            font = painter.font()
            font.setPointSizeF(10.0)
            painter.setFont(font)
            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(reason)
            text_height = fm.height()
            pad_x, pad_y = 4, 2
            bg_rect = QRectF(sx - pad_x,
                             sy - text_height + pad_y,
                             text_width + 2 * pad_x,
                             text_height + pad_y)
            painter.setPen(Qt.NoPen)
            bg = QColor("#e63946")
            bg.setAlpha(200)
            painter.setBrush(QBrush(bg))
            painter.drawRoundedRect(bg_rect, 3, 3)
            painter.setPen(QPen(Qt.white))
            painter.drawText(QPointF(sx, sy), reason)
            painter.restore()

    def _draw_edit_polygon_overlay(self, painter, cid: str):
        pts = self._polygons.get(cid, [])
        if not pts:
            return
        color = self._color_map.get(cid, QColor("blue"))
        
        # Draw points
        r = 5.0 / self._scale
        for i, p in enumerate(pts):
            if self._dragging_route_point and self._dragging_route_point[0] == cid and self._dragging_route_point[1] == i:
                painter.setBrush(QBrush(QColor("#ff6b6b")))
            else:
                painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#ffffff"), 1.0 / self._scale))
            painter.drawEllipse(p, r, r)

    def _draw_edit_floor_polygon_overlay(self, painter, fp_id: str):
        pts = self._floor_polygon_points_world(fp_id)
        if not pts:
            return
        layer = self._floor_plans.get(fp_id)
        base_color = QColor("#8d99ae")
        if layer and layer.polygon_color:
            base_color = QColor(layer.polygon_color)

        r = 5.0 / self._scale
        for i, p in enumerate(pts):
            if (self._dragging_route_point
                    and self._dragging_route_point[0] == fp_id
                    and self._dragging_route_point[1] == i):
                painter.setBrush(QBrush(QColor("#ff6b6b")))
            else:
                painter.setBrush(QBrush(base_color))
            painter.setPen(QPen(QColor("#ffffff"), 1.0 / self._scale))
            painter.drawEllipse(p, r, r)

    def _draw_edit_route_overlay(self, painter, cid: str):
        pts = self._manual_routes.get(cid, [])
        if len(pts) < 2:
            return
        color = self._color_map.get(cid, QColor("white"))
        
        # Draw points
        r = 5.0 / self._scale
        for i, p in enumerate(pts):
            if self._dragging_route_point and self._dragging_route_point[0] == cid and self._dragging_route_point[1] == i:
                painter.setBrush(QBrush(QColor("#ff6b6b")))
            else:
                painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#ffffff"), 1.0 / self._scale))
            painter.drawEllipse(p, r, r)

    def _draw_in_progress(self, painter, color):
        painter.setPen(QPen(color, 2.0 / self._scale, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        pts = self._current_points
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i + 1])
        if self._mouse_pos:
            painter.drawLine(pts[-1], self._mouse_pos)
        painter.setBrush(QBrush(color))
        r = 3.0 / self._scale
        for p in pts:
            painter.drawEllipse(p, r, r)

    # ── Collision zone overlay ────────────────────────────────────────── #

    def _draw_collision_zones(self, painter, cid: str, dragged_idx: int):
        """Draw semi-transparent red collision zones while a route point is dragged.

        Two zone types are visualised:
        1. **Wall-distance zone** – a strip along the inside of the polygon edges.
        2. **Pipe-spacing zone** – a buffer around every other route segment
           (excluding the two segments adjacent to the dragged point).
        """
        polygon = self._polygons.get(cid, [])
        if len(polygon) < 3:
            return

        wall_dist = self._route_wall_dist_px.get(cid, 0.0)
        line_dist = self._route_line_dist_px.get(cid, 0.0)

        zone_color = QColor(255, 80, 80, 45)       # light-red, transparent

        # -- Build a clip path from the polygon so nothing leaks outside --
        clip_path = QPainterPath()
        clip_poly = QPolygonF(polygon)
        clip_path.addPolygon(clip_poly)
        clip_path.closeSubpath()

        painter.save()

        # ── 1. Wall-distance zone ──────────────────────────────────────
        if wall_dist > 1e-3:
            # The zone is: polygon ∖ inset(polygon, wall_dist).
            # We approximate the inset by offsetting each edge inward.
            inset_pts = self._inset_polygon(polygon, wall_dist)
            if inset_pts and len(inset_pts) >= 3:
                inset_path = QPainterPath()
                inset_path.addPolygon(QPolygonF(inset_pts))
                inset_path.closeSubpath()
                wall_zone_path = clip_path - inset_path        # ring shape
            else:
                # Inset collapsed → full polygon is in zone
                wall_zone_path = clip_path

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(zone_color))
            painter.drawPath(wall_zone_path)

        # ── 2. Pipe-spacing zone (around existing segments) ────────────
        if line_dist > 1e-3:
            min_center_dist = 2.0 * line_dist
            # Use in-progress points during route drawing, otherwise committed route
            if (self._mode == ToolMode.DRAW_ROUTE
                    and self._current_route_cid == cid
                    and self._current_route_points):
                pts = list(self._current_route_points)
            else:
                pts = self._manual_routes.get(cid, [])
            # Indices of segments adjacent to the dragged point (skip them)
            skip = set()
            if dragged_idx > 0:
                skip.add(dragged_idx - 1)
            skip.add(dragged_idx)

            spacing_path = QPainterPath()
            for i in range(len(pts) - 1):
                if i in skip:
                    continue
                a, b = pts[i], pts[i + 1]
                seg_len = _qdist(a, b)
                if seg_len < 1e-6:
                    continue
                # Build a rectangle (capsule) around the segment
                dx = b.x() - a.x()
                dy = b.y() - a.y()
                nx = -dy / seg_len * min_center_dist
                ny =  dx / seg_len * min_center_dist
                # Four corners of the expanded segment rectangle
                capsule = QPainterPath()
                capsule.moveTo(a.x() + nx, a.y() + ny)
                capsule.lineTo(b.x() + nx, b.y() + ny)
                capsule.lineTo(b.x() - nx, b.y() - ny)
                capsule.lineTo(a.x() - nx, a.y() - ny)
                capsule.closeSubpath()
                # Add semicircle caps at each end
                cap = QPainterPath()
                cap.addEllipse(a, min_center_dist, min_center_dist)
                capsule = capsule.united(cap)
                cap2 = QPainterPath()
                cap2.addEllipse(b, min_center_dist, min_center_dist)
                capsule = capsule.united(cap2)
                spacing_path = spacing_path.united(capsule)

            # Clip to polygon
            spacing_path = spacing_path.intersected(clip_path)

            if not spacing_path.isEmpty():
                spacing_color = QColor(255, 80, 80, 35)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(spacing_color))
                painter.drawPath(spacing_path)

        painter.restore()

    @staticmethod
    def _inset_polygon(polygon: List[QPointF], dist: float) -> List[QPointF]:
        """Compute an approximate inward offset of *polygon* by *dist* px.

        Uses the intersection of inward-shifted edges.  For very concave or
        small polygons the result may be empty or degenerate – the caller
        should handle that gracefully.
        """
        n = len(polygon)
        if n < 3 or dist <= 0:
            return list(polygon)

        # Determine winding: signed_area > 0 → CCW in screen coords (Y-down)
        signed_area = 0.0
        for i in range(n):
            a = polygon[i]
            b = polygon[(i + 1) % n]
            signed_area += (a.x() * b.y() - b.x() * a.y())
        # In screen coords (Y-down), signed_area > 0 means CCW, < 0 means CW.
        # We want the inward normal.  For a CCW polygon (screen) the inward
        # normal of edge (a→b) is (dy, -dx); for CW it is (-dy, dx).
        ccw = signed_area > 0

        # Compute inward normals for each edge
        edges = []
        for i in range(n):
            a = polygon[i]
            b = polygon[(i + 1) % n]
            dx = b.x() - a.x()
            dy = b.y() - a.y()
            length = math.hypot(dx, dy)
            if length < 1e-9:
                edges.append(None)
                continue
            if ccw:
                # inward normal for CCW (screen): (dy, -dx)
                nx =  dy / length
                ny = -dx / length
            else:
                # inward normal for CW (screen): (-dy, dx)
                nx = -dy / length
                ny =  dx / length
            edges.append((a, b, nx, ny))

        # Offset each edge inward by dist
        offset_edges = []
        for e in edges:
            if e is None:
                offset_edges.append(None)
                continue
            a, b, nx, ny = e
            oa = QPointF(a.x() + nx * dist, a.y() + ny * dist)
            ob = QPointF(b.x() + nx * dist, b.y() + ny * dist)
            offset_edges.append((oa, ob))

        # Intersect consecutive offset lines to find inset vertices
        result: List[QPointF] = []
        for i in range(n):
            e1 = offset_edges[i]
            e2 = offset_edges[(i + 1) % n]
            if e1 is None or e2 is None:
                continue
            pt = _line_line_intersection(e1[0], e1[1], e2[0], e2[1])
            if pt is not None:
                result.append(pt)
        return result

# ── Geometrie-Helfer ──────────────────────────────────────────────────── #

def _qdist(a: QPointF, b: QPointF) -> float:
    return math.hypot(b.x() - a.x(), b.y() - a.y())

def _line_line_intersection(a1: QPointF, a2: QPointF,
                            b1: QPointF, b2: QPointF) -> Optional[QPointF]:
    """Intersection of two infinite lines (a1→a2) and (b1→b2).  Returns None
    if the lines are (nearly) parallel."""
    dx1 = a2.x() - a1.x();  dy1 = a2.y() - a1.y()
    dx2 = b2.x() - b1.x();  dy2 = b2.y() - b1.y()
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-12:
        return None
    t = ((b1.x() - a1.x()) * dy2 - (b1.y() - a1.y()) * dx2) / denom
    return QPointF(a1.x() + t * dx1, a1.y() + t * dy1)

def _project_on_segment(p: QPointF, a: QPointF, b: QPointF) -> QPointF:
    ax, ay = a.x(), a.y()
    bx, by = b.x(), b.y()
    dx, dy = bx - ax, by - ay
    seg_sq = dx * dx + dy * dy
    if seg_sq < 1e-12:
        return a
    t = max(0.0, min(1.0, ((p.x() - ax) * dx + (p.y() - ay) * dy) / seg_sq))
    return QPointF(ax + t * dx, ay + t * dy)

def _orientation(a: QPointF, b: QPointF, c: QPointF) -> float:
    return (b.x() - a.x()) * (c.y() - a.y()) - (b.y() - a.y()) * (c.x() - a.x())

def _segments_intersect(a1: QPointF, a2: QPointF, b1: QPointF, b2: QPointF) -> bool:
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    eps = 1e-9
    return ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and \
           ((o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps))

def _point_segment_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
    proj = _project_on_segment(p, a, b)
    return _qdist(p, proj)

def _segment_distance(a1: QPointF, a2: QPointF, b1: QPointF, b2: QPointF) -> float:
    if _segments_intersect(a1, a2, b1, b2):
        return 0.0
    return min(
        _point_segment_distance(a1, b1, b2),
        _point_segment_distance(a2, b1, b2),
        _point_segment_distance(b1, a1, a2),
        _point_segment_distance(b2, a1, a2),
    )
