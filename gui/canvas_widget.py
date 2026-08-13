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
import hashlib
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QPointF, Signal, QRectF, QByteArray
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPolygonF, QPainterPath, QPixmap, QFont,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QApplication, QToolTip

from storage.asset_data_uri import is_data_uri, is_svg_asset_ref, parse_data_uri

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

HIT_POINT_RADIUS_PX = 10.0
HIT_EDGE_RADIUS_PX = 8.0
HIT_CABLE_POINT_RADIUS_PX = 20.0
MIN_SYMBOL_PICK_HALF_PX = 8.0
_HELPER_NAV_ID_PREFIX = "NAV-HLP::"


def _parse_helper_nav_id(nav_id: str) -> tuple[str, str] | None:
    if not nav_id.startswith(_HELPER_NAV_ID_PREFIX):
        return None
    payload = nav_id[len(_HELPER_NAV_ID_PREFIX):]
    floor_id, sep, helper_id = payload.partition("::")
    if not sep or not floor_id or not helper_id:
        return None
    return floor_id, helper_id

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
    MEASURE_ANGLE    = auto()
    DRAW_HELPER_LINE = auto()
    EDIT_HELPER_LINE = auto()
    DRAW_EXPORT_FRAME = auto()
    PLACE_TEXT       = auto()
    MOVE_TEXT        = auto()
    MOVE_MEASURE_LABEL = auto()

class CanvasWidget(QWidget):
    polygon_finished  = Signal(str, list)
    elec_room_polygon_finished = Signal(str, list)
    ref_line_set      = Signal()          # Linie fertig, Länge kommt vom Panel
    start_point_moved = Signal(str, tuple)
    route_changed     = Signal(str)
    polygon_changed   = Signal(str)       # emitted when polygon is edited (point moved/added/deleted)
    elec_room_polygon_changed = Signal(str)
    elec_point_placed  = Signal(str)
    elec_cable_changed = Signal(str)
    hkv_placed         = Signal(str)
    hkv_line_changed   = Signal(str)
    text_placed        = Signal(str)
    object_clicked     = Signal(str, str)  # (object_type, object_id) – single click selection
    object_switched_from_edit = Signal(str, str)  # (object_type, object_id)
    object_double_clicked = Signal(str, str)  # (object_type, object_id)
    context_menu_requested = Signal(str, str, object, object)  # (object_type, object_id, canvas_pt, global_pos)
    floor_plan_transform_updated = Signal(str, float, float, float)  # (fp_id, ox, oy, rot)
    floor_plan_polygon_changed = Signal(str)       # emitted when floor/furniture polygon point is moved/added/deleted
    floor_plan_polygon_finished = Signal(str, list)
    mode_changed = Signal()  # emitted when tool mode changes
    export_frame_drawn = Signal(object)  # emitted with QRectF when export frame is finalized
    helper_lines_changed = Signal()
    label_moved = Signal(str)
    measure_changed = Signal()
    multi_selection_changed = Signal(set)  # emitted with set of (obj_type, obj_id) tuples
    will_move_multi_objects = Signal()  # emitted before multi-object move starts
    multi_objects_moved = Signal()  # emitted after multi-object move completes
    document_data_changed = Signal(str)  # (element_id) – Projektdaten über eine View geändert

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(400, 400)

        self._svg_renderer: Optional[QSvgRenderer] = None
        self._bg_pixmap: Optional[QPixmap] = None
        self._svg_size = (800.0, 600.0)
        self._pixmap_cache: Dict[str, QPixmap] = {}
        self._svg_renderer_cache: Dict[str, QSvgRenderer] = {}

        # Multiple floor plan layers
        self._floor_plans: Dict[str, FloorPlanLayer] = {}
        self._floor_plan_order: List[str] = []     # render back→front
        self._ref_floor_id: Optional[str] = None   # which floor we're drawing ref line for
        self._active_floor_id: Optional[str] = None    # floor plan being moved/rotated
        self._floor_drag_start: Optional[QPointF] = None
        self._floor_rotate_start_angle: float = 0.0
        self._floor_rotate_orig: float = 0.0
        self._floor_polygon_world_cache: Dict[str, Tuple[tuple, List[QPointF], QPolygonF]] = {}
        self._manual_route_path_cache: Dict[str, Tuple[tuple, QPainterPath]] = {}
        self._supply_line_path_cache: Dict[str, Tuple[tuple, QPainterPath]] = {}
        self._elec_cable_path_cache: Dict[str, Tuple[tuple, QPainterPath]] = {}
        self._hkv_line_path_cache: Dict[str, Tuple[tuple, QPainterPath]] = {}

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
        self._floor_helper_lines: Dict[str, Dict[str, List[QPointF]]] = {}
        self._floor_helper_line_visible: Dict[str, Dict[str, bool]] = {}
        self._floor_helper_line_length_mm: Dict[str, Dict[str, float]] = {}  # {floor_id: {helper_id: length_mm}}
        self._floor_helper_line_fixed: Dict[str, Dict[str, bool]] = {}      # {floor_id: {helper_id: length_fixed}}
        self._floor_helper_settings: Dict[str, Dict[str, object]] = {}
        self._helper_line_counter: int                            = 0
        self._helper_selected_id: Optional[str]                  = None
        self._helper_selected_floor_id: Optional[str]            = None
        self._helper_hover_endpoint: Optional[Tuple[str, str, int]] = None
        self._helper_active_floor_id: Optional[str]              = None
        self._helper_dragging_endpoint: Optional[Tuple[str, int]] = None
        self._helper_dragging_whole_id: Optional[str]            = None
        self._helper_drag_start: Optional[QPointF]               = None
        self._helper_drag_origin: List[QPointF]                  = []
        self._helper_draw_start: Optional[QPointF]               = None
        self._helper_draw_current: Optional[QPointF]             = None
        self._helper_target_length_mm: float                     = 1000.0
        self._helper_line_color: str                              = "#f8f32b"
        self._helper_line_intersections: List[tuple] = []  # [(intersection_pt, angle_deg, hid1, hid2), ...]
        self._manual_routes: Dict[str, List[QPointF]]            = {}
        self._route_wall_dist_px: Dict[str, float]               = {}
        self._route_line_dist_px: Dict[str, float]               = {}
        self._circuit_visible: Dict[str, bool]                    = {}

        # Anschlussleitungen (supply lines, one per circuit)
        self._supply_lines:       Dict[str, List[QPointF]]        = {}

        # Elektro
        self._elec_points:        Dict[str, QPointF]              = {}
        self._elec_room_polygons: Dict[str, List[QPointF]]        = {}
        self._elec_room_visible:  Dict[str, bool]                 = {}
        self._elec_point_size_px: Dict[str, Tuple[float, float]]  = {}
        self._elec_point_icons:   Dict[str, Optional[QPixmap]]    = {}
        self._elec_point_svgs:    Dict[str, Optional[QSvgRenderer]] = {}
        self._elec_point_position: Dict[str, str]                 = {}  # "Wand", "Decke", "Boden", "Freitext"
        self._elec_point_height:   Dict[str, float]               = {}  # Höhe vom Boden in mm
        self._elec_point_notes:    Dict[str, str]                 = {}
        self._elec_point_smarthome_device: Dict[str, str]         = {}
        self._elec_point_smarthome_device_color: Dict[str, str]   = {}
        self._elec_cables:        Dict[str, List[QPointF]]        = {}
        self._elec_cable_notes:   Dict[str, str]                  = {}
        self._elec_cable_stroke_width: Dict[str, float]            = {}
        self._elec_cable_type_text: Dict[str, str]                 = {}
        self._elec_cable_type_label_visible: Dict[str, bool]       = {}
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

        # MCP planning metadata
        self._planning_context:   Dict[str, object]               = {}
        self._planning_log:       List[Dict[str, object]]         = []

        # Labels (movable + resizable)
        self._label_positions:    Dict[str, QPointF]              = {}
        self._label_font_sizes:   Dict[str, float]                = {}
        self._label_visible:      Dict[str, bool]                 = {}
        self._label_rects:        Dict[str, QRectF]               = {}  # hit testing (transient)
        self._label_draw_pos:     Dict[str, QPointF]              = {}  # transient
        self._dragging_label:     Optional[str]                   = None
        self._label_drag_offset:  QPointF                         = QPointF(0, 0)

        self._color_index   = 0
        self._dragging_start: Optional[str] = None
        self._dragging_route_point: Optional[Tuple[str, int]] = None
        self._dragging_elec_cable_id: Optional[str] = None
        self._dragging_elec_cable_start: Optional[QPointF] = None
        self._dragging_elec_cable_origin: List[QPointF] = []
        self._dragging_elec_cable_fixed_indices: set[int] = set()
        self._last_clicked_object: Optional[Tuple[str, str]] = None  # (obj_type, obj_id)
        self._mode          = ToolMode.NONE
        #: gebundenes Projektdokument (Single Source of Truth, siehe set_document)
        self._document = None
        #: aktive Layer für die Selektion (None = keine Einschränkung)
        self._selectable_layers: Optional[set[str]] = None
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
        self._measure_color: str = "#00e5ff"  # Color for measurement tool
        # Positions for measurement labels (persisted)
        self._measure_label_positions: List[Tuple[float, float]] = []
        self._angle_measure_label_positions: List[Tuple[float, float]] = []
        self._debug_measure_last_store_idx: Optional[int] = None
        self._debug_measure_pos_logs: bool = os.getenv("HROUTING_DEBUG_MEASURE_POS", "0") in {"1", "true", "True", "yes", "on"}
        # Positions for helper line labels (persisted)
        self._helper_label_positions: Dict[str, Dict[str, Tuple[float, float]]] = {}
        # Currently dragging label index
        self._dragging_measure_label_idx: Optional[int] = None
        self._angle_measure_p1: Optional[QPointF] = None
        self._angle_measure_p2: Optional[QPointF] = None
        self._angle_measure_p3: Optional[QPointF] = None
        self._angle_measurements: List[Tuple[QPointF, QPointF, QPointF, float]] = []

        # Reference line configuration per floor plan
        self._ref_line_colors: Dict[str, str] = {}  # fp_id -> hex color
        self._ref_line_visible: Dict[str, bool] = {}  # fp_id -> visible

        # Export frame (for SVG/PDF crop)
        self._export_frame: Optional[QRectF] = None
        self._export_frame_start: Optional[QPointF] = None
        self._export_frame_current: Optional[QPointF] = None

        # Elektro edit state
        self._placing_elec_point_id: Optional[str] = None
        self._current_elec_room_id: Optional[str] = None
        self._current_elec_cable_id: Optional[str] = None
        self._current_elec_cable_points: List[QPointF] = []
        self._current_elec_cable_preview: Optional[QPointF] = None
        self._drawing_cable_from_start: bool = False  # True wenn vom Anfang des Kabels aus gezeichnet wird
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
        self._edit_elec_room_id: Optional[str] = None
        self._edit_floor_polygon_id: Optional[str] = None
        self._insert_between_indices: Optional[Tuple[int, int]] = None
        self._edit_route_cid: Optional[str] = None
        self._edit_selected_owner: Optional[str] = None
        self._edit_selected_indices: set[int] = set()
        self._edit_selection_rect_start: Optional[QPointF] = None
        self._edit_selection_rect_end: Optional[QPointF] = None
        self._edit_drag_last_pos: Optional[QPointF] = None

        # Selection highlight from treeview
        self._selected_item_id: Optional[str] = None  # id of currently treeview-selected element
        self._selected_item_type: Optional[str] = None  # type: polygon, elec_point, hkv, etc
        self._hover_object: Optional[Tuple[str, str]] = None
        self._ghost_preview_pos: Optional[QPointF] = None

        # Multi-select and batch move functionality
        self._multi_selected: Set[Tuple[str, str]] = set()  # Set von (obj_type, obj_id)
        self._selection_rect: Optional[QRectF] = None  # Drag-Rahmen beim Box-Select
        self._selection_start: Optional[QPointF] = None  # Startpunkt beim Rahmen-Ziehen
        self._is_selecting_by_drag: bool = False  # Flag: gerade Box-Selection?
        self._dragging_multi: Set[Tuple[str, str]] = set()  # Aktuell verschobene Objekte
        self._drag_multi_start_positions: Dict[Tuple[str, str], QPointF] = {}  # Start-Positionen
        self._drag_multi_anchor: Optional[QPointF] = None  # Maus-Anchor beim Multi-Drag

    def _debug_measure_pos(self, tag: str, **values) -> None:
        """Emit optional debug logs for measurement position tracing."""
        if not self._debug_measure_pos_logs:
            return
        details = "  ".join(f"{k}={v}" for k, v in values.items())
        print(f"[MEASURE-{tag}] {details}", flush=True)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def load_svg(self, filepath: str):
        """Load an SVG, PNG, or JPG as the background floor plan (legacy)."""
        self._svg_renderer = None
        self._bg_pixmap = None
        if is_data_uri(filepath):
            parsed = parse_data_uri(filepath)
            if parsed is None:
                self._svg_size = (100, 100)
                self.update()
                return
            mime, data = parsed
            if mime.lower() == "image/svg+xml":
                renderer = QSvgRenderer(QByteArray(data))
                if renderer.isValid():
                    self._svg_renderer = renderer
                    vb = renderer.viewBox()
                    self._svg_size = (float(vb.width()), float(vb.height()))
                else:
                    self._svg_size = (100, 100)
            else:
                pm = QPixmap()
                if pm.loadFromData(data):
                    self._bg_pixmap = pm
                    self._svg_size = (float(pm.width()), float(pm.height()))
                else:
                    self._svg_size = (100, 100)
            self._fit_to_window()
            self.update()
            return
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

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = bool(visible)
        self.update()

    def set_grid_spacing_mm(self, spacing_mm: float) -> None:
        self._grid_spacing_mm = max(1.0, float(spacing_mm))
        self.update()

    def set_grid_color(self, color) -> None:
        self._grid_color = QColor(color)
        self.update()

    def set_snap_angle(self, angle_deg: float) -> None:
        self._snap_angle = max(0.0, float(angle_deg))
        self.update()

    def grid_visible(self) -> bool:
        return bool(self._grid_visible)

    def grid_spacing_mm(self) -> float:
        return float(self._grid_spacing_mm)

    def grid_color(self) -> QColor:
        return QColor(self._grid_color)

    def snap_angle(self) -> float:
        return float(self._snap_angle)

    # ── Floor plan layer management ────────────────────────────────

    def add_floor_plan(self, fp_id: str, filepath: str = "") -> FloorPlanLayer:
        layer = self._create_floor_plan_layer(fp_id)
        self._floor_plans[fp_id] = layer
        if fp_id not in self._floor_plan_order:
            self._floor_plan_order.append(fp_id)
        self._ensure_helper_floor(fp_id)
        if not self._helper_active_floor_id:
            self.set_active_helper_floor(fp_id)
        if filepath:
            self.load_floor_plan_image(fp_id, filepath)
        return layer

    def _create_floor_plan_layer(self, fp_id: str):
        """Erzeugt einen Layer – dokumentgebunden, sobald ein Dokument gesetzt ist."""
        document = getattr(self, "_document", None)
        if document is not None:
            element = document.floorplans.get(fp_id) or document.furniture.get(fp_id)
            if element is not None:
                from model.views import FloorPlanLayerView  # noqa: PLC0415

                return FloorPlanLayerView(element, self._on_document_data_changed)
        return FloorPlanLayer(fp_id=fp_id)

    def remove_floor_plan(self, fp_id: str):
        self._floor_plans.pop(fp_id, None)
        self._floor_polygon_world_cache.pop(fp_id, None)
        self._floor_helper_lines.pop(fp_id, None)
        self._floor_helper_line_visible.pop(fp_id, None)
        self._floor_helper_settings.pop(fp_id, None)
        if fp_id in self._floor_plan_order:
            self._floor_plan_order.remove(fp_id)
        if self._helper_selected_floor_id == fp_id:
            self._helper_selected_id = None
            self._helper_selected_floor_id = None
        if self._helper_active_floor_id == fp_id:
            self._helper_active_floor_id = None
            self.set_active_helper_floor(None)
        self.update()

    def load_floor_plan_image(self, fp_id: str, filepath: str):
        layer = self._floor_plans.get(fp_id)
        if not layer:
            return
        had_loaded_image = layer.renderer is not None or layer.pixmap is not None
        old_global_mpp = self._mm_per_px if self._mm_per_px > 0 else 1.0
        old_native_size = tuple(layer.size)
        old_layer_mpp = layer.mm_per_px if layer.mm_per_px > 0 else old_global_mpp
        old_render_size = self._layer_render_size_for_scale(
            layer,
            old_global_mpp,
            layer_mm_per_px=old_layer_mpp,
            native_size=old_native_size,
        )
        old_ref_image_points = None
        if had_loaded_image and layer.ref_p1 is not None and layer.ref_p2 is not None:
            old_ref_image_points = (
                self._world_to_layer_image_point(
                    layer, layer.ref_p1, old_render_size, old_native_size
                ),
                self._world_to_layer_image_point(
                    layer, layer.ref_p2, old_render_size, old_native_size
                ),
            )
        layer.file_path = filepath
        # Preserve existing polygon (important for undo restore)
        saved_polygon = layer.polygon
        layer.renderer = None
        layer.pixmap = None
        if is_data_uri(filepath):
            if is_svg_asset_ref(filepath):
                renderer = self._get_cached_svg_renderer(filepath)
                if renderer is not None:
                    layer.renderer = renderer
                    vb = renderer.viewBox()
                    layer.size = (float(vb.width()), float(vb.height()))
                else:
                    layer.size = (100.0, 100.0)
            else:
                pm = self._get_cached_pixmap(filepath)
                if pm is not None:
                    layer.pixmap = QPixmap(pm)
                    layer.size = (float(pm.width()), float(pm.height()))
                else:
                    layer.size = (100.0, 100.0)
        else:
            if not os.path.exists(filepath):
                layer.size = (100.0, 100.0)
                self.update()
                return
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".svg":
                renderer = self._get_cached_svg_renderer(filepath)
                if renderer is not None:
                    layer.renderer = renderer
                    vb = renderer.viewBox()
                    layer.size = (float(vb.width()), float(vb.height()))
                else:
                    layer.size = (100.0, 100.0)
            else:
                pm = self._get_cached_pixmap(filepath)
                if pm is not None:
                    layer.pixmap = QPixmap(pm)
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
        if old_ref_image_points and old_native_size[0] > 0 and old_native_size[1] > 0:
            new_native_size = tuple(layer.size)
            p1_img_old, p2_img_old = old_ref_image_points
            p1_img_new = QPointF(
                p1_img_old.x() * new_native_size[0] / old_native_size[0],
                p1_img_old.y() * new_native_size[1] / old_native_size[1],
            )
            p2_img_new = QPointF(
                p2_img_old.x() * new_native_size[0] / old_native_size[0],
                p2_img_old.y() * new_native_size[1] / old_native_size[1],
            )

            if not (layer.fixed_width_mm > 0 and layer.fixed_height_mm > 0):
                px_len = math.hypot(
                    p2_img_new.x() - p1_img_new.x(),
                    p2_img_new.y() - p1_img_new.y(),
                )
                if px_len > 1e-9 and layer.ref_length_mm > 0:
                    layer.mm_per_px = layer.ref_length_mm / px_len

            new_render_size = self._layer_render_size_for_scale(
                layer,
                self._mm_per_px,
                native_size=new_native_size,
            )
            layer.ref_p1 = self._layer_image_to_world_point(
                layer, p1_img_new, new_render_size, new_native_size
            )
            layer.ref_p2 = self._layer_image_to_world_point(
                layer, p2_img_new, new_render_size, new_native_size
            )
            if self._ref_floor_id == fp_id:
                self._ref_p1 = QPointF(layer.ref_p1)
                self._ref_p2 = QPointF(layer.ref_p2)
        # Restore polygon if it existed (e.g. during undo restore)
        if saved_polygon:
            layer.polygon = saved_polygon
        self.update()

    @staticmethod
    def _cache_key(path: str) -> str:
        if is_data_uri(path):
            digest = hashlib.sha1(path.encode("utf-8")).hexdigest()
            return f"data-uri:{digest}"
        return os.path.normcase(os.path.normpath(path or ""))

    def _resolve_icon_path(self, path: str) -> str:
        """Resolve icon path, handling both absolute and relative paths.
        
        For relative paths (e.g., 'icons/Steckdose.png'), resolve relative to
        the project directory (where the .hrp file is).
        """
        if not path:
            return ""
        if is_data_uri(path):
            return path
        p = Path(path)
        if p.is_absolute() and p.exists():
            return str(path)
        # Try relative to project root
        if self._document and hasattr(self._document, "source_path"):
            project_dir = Path(self._document.source_path).parent
            rel_path = project_dir / path
            if rel_path.exists():
                return str(rel_path)
        # Try from current working directory
        if Path(path).exists():
            return str(Path(path).resolve())
        return str(path)  # Return original, will fail to load

    def _get_cached_pixmap(self, path: str) -> Optional[QPixmap]:
        resolved_path = self._resolve_icon_path(path)
        key = self._cache_key(resolved_path)
        if not key:
            return None
        cached = self._pixmap_cache.get(key)
        if cached is not None and not cached.isNull():
            return cached
        pm = QPixmap()
        if is_data_uri(resolved_path):
            parsed = parse_data_uri(resolved_path)
            if parsed is None or not pm.loadFromData(parsed[1]):
                self._pixmap_cache.pop(key, None)
                return None
        else:
            pm = QPixmap(resolved_path)
        if pm.isNull():
            self._pixmap_cache.pop(key, None)
            return None
        self._pixmap_cache[key] = pm
        return pm

    def _get_cached_svg_renderer(self, path: str) -> Optional[QSvgRenderer]:
        resolved_path = self._resolve_icon_path(path)
        key = self._cache_key(resolved_path)
        if not key:
            return None
        cached = self._svg_renderer_cache.get(key)
        if cached is not None and cached.isValid():
            return cached
        if is_data_uri(resolved_path):
            parsed = parse_data_uri(resolved_path)
            if parsed is None:
                return None
            renderer = QSvgRenderer(QByteArray(parsed[1]))
        else:
            renderer = QSvgRenderer(resolved_path)
        if not renderer.isValid():
            self._svg_renderer_cache.pop(key, None)
            return None
        self._svg_renderer_cache[key] = renderer
        return renderer

    def set_floor_plan_transform(self, fp_id: str,
                                  offset_x: float, offset_y: float,
                                  rotation: float):
        layer = self._floor_plans.get(fp_id)
        if layer:
            old_rotation = float(layer.rotation)
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

            # Keep ref-line anchors aligned with the image when rotation changes
            # via the properties panel (mouse-rotate already does this in drag path).
            delta_rot = float(rotation) - old_rotation
            if abs(delta_rot) > 1e-9 and (layer.ref_p1 or layer.ref_p2):
                layer.offset_x = offset_x
                layer.offset_y = offset_y
                sw, sh = self._layer_render_size(layer)
                cx = sw / 2 + layer.offset_x
                cy = sh / 2 + layer.offset_y
                rad = math.radians(delta_rot)
                cos_r, sin_r = math.cos(rad), math.sin(rad)
                for attr in ("ref_p1", "ref_p2"):
                    pt = getattr(layer, attr)
                    if pt is None:
                        continue
                    rx = pt.x() - cx
                    ry = pt.y() - cy
                    setattr(
                        layer,
                        attr,
                        QPointF(cx + rx * cos_r - ry * sin_r,
                                cy + rx * sin_r + ry * cos_r),
                    )
                if self._ref_floor_id == fp_id:
                    self._ref_p1 = layer.ref_p1
                    self._ref_p2 = layer.ref_p2

            layer.offset_x = offset_x
            layer.offset_y = offset_y
            layer.rotation = rotation
            self.update()

    @staticmethod
    def _world_to_layer_local_point(world_pt: QPointF,
                                    rendered_size: Tuple[float, float],
                                    offset_x: float,
                                    offset_y: float,
                                    rotation_deg: float) -> QPointF:
        sw = rendered_size[0] if rendered_size[0] > 0 else 1.0
        sh = rendered_size[1] if rendered_size[1] > 0 else 1.0
        cx = sw / 2 + offset_x
        cy = sh / 2 + offset_y
        rad = math.radians(-rotation_deg)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        dx = world_pt.x() - cx
        dy = world_pt.y() - cy
        return QPointF(
            dx * cos_r - dy * sin_r + sw / 2,
            dx * sin_r + dy * cos_r + sh / 2,
        )

    @staticmethod
    def _layer_local_to_world_point(local_pt: QPointF,
                                    rendered_size: Tuple[float, float],
                                    offset_x: float,
                                    offset_y: float,
                                    rotation_deg: float) -> QPointF:
        sw = rendered_size[0] if rendered_size[0] > 0 else 1.0
        sh = rendered_size[1] if rendered_size[1] > 0 else 1.0
        cx = sw / 2 + offset_x
        cy = sh / 2 + offset_y
        rad = math.radians(rotation_deg)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        rx = local_pt.x() - sw / 2
        ry = local_pt.y() - sh / 2
        return QPointF(
            cx + rx * cos_r - ry * sin_r,
            cy + rx * sin_r + ry * cos_r,
        )

    def _world_to_layer_image_point(self,
                                    layer: "FloorPlanLayer",
                                    world_pt: QPointF,
                                    rendered_size: Tuple[float, float],
                                    native_size: Tuple[float, float]) -> QPointF:
        local_pt = self._world_to_layer_local_point(
            world_pt,
            rendered_size,
            layer.offset_x,
            layer.offset_y,
            layer.rotation,
        )
        sw = rendered_size[0] if rendered_size[0] > 0 else 1.0
        sh = rendered_size[1] if rendered_size[1] > 0 else 1.0
        nw = native_size[0] if native_size[0] > 0 else 1.0
        nh = native_size[1] if native_size[1] > 0 else 1.0
        return QPointF(local_pt.x() * nw / sw, local_pt.y() * nh / sh)

    def _layer_image_to_world_point(self,
                                    layer: "FloorPlanLayer",
                                    image_pt: QPointF,
                                    rendered_size: Tuple[float, float],
                                    native_size: Tuple[float, float]) -> QPointF:
        nw = native_size[0] if native_size[0] > 0 else 1.0
        nh = native_size[1] if native_size[1] > 0 else 1.0
        sw = rendered_size[0] if rendered_size[0] > 0 else 1.0
        sh = rendered_size[1] if rendered_size[1] > 0 else 1.0
        local_pt = QPointF(image_pt.x() * sw / nw, image_pt.y() * sh / nh)
        return self._layer_local_to_world_point(
            local_pt,
            rendered_size,
            layer.offset_x,
            layer.offset_y,
            layer.rotation,
        )

    def remap_layer_ref_points(self, fp_id: str,
                               old_render_size: Tuple[float, float],
                               new_render_size: Tuple[float, float],
                               old_native_size: Optional[Tuple[float, float]] = None,
                               new_native_size: Optional[Tuple[float, float]] = None):
        """Adjust ref_p1/ref_p2 for changed render and/or source image sizes."""
        layer = self._floor_plans.get(fp_id)
        if not layer:
            return
        old_native_size = old_native_size or layer.size
        new_native_size = new_native_size or layer.size
        if (
            old_render_size == new_render_size
            and old_native_size == new_native_size
        ):
            return
        if old_native_size[0] <= 0 or old_native_size[1] <= 0:
            return
        for attr in ("ref_p1", "ref_p2"):
            pt = getattr(layer, attr)
            if pt is None:
                continue
            old_image_pt = self._world_to_layer_image_point(
                layer, pt, old_render_size, old_native_size
            )
            new_image_pt = QPointF(
                old_image_pt.x() * new_native_size[0] / old_native_size[0],
                old_image_pt.y() * new_native_size[1] / old_native_size[1],
            )
            setattr(
                layer,
                attr,
                self._layer_image_to_world_point(
                    layer, new_image_pt, new_render_size, new_native_size
                ),
            )
        if self._ref_floor_id == fp_id:
            self._ref_p1 = layer.ref_p1
            self._ref_p2 = layer.ref_p2

    def rescale_all_layer_ref_points(self,
                                     old_global_mm_per_px: float,
                                     new_global_mm_per_px: float,
                                     skip_fp_id: Optional[str] = None):
        if abs(new_global_mm_per_px - old_global_mm_per_px) <= 1e-9:
            return
        for fid, layer in self._floor_plans.items():
            if fid == skip_fp_id or (layer.ref_p1 is None and layer.ref_p2 is None):
                continue
            old_render_size = self._layer_render_size_for_scale(
                layer, old_global_mm_per_px
            )
            new_render_size = self._layer_render_size_for_scale(
                layer, new_global_mm_per_px
            )
            self.remap_layer_ref_points(fid, old_render_size, new_render_size)

    def rescale_layer_ref_points(self, fp_id: str,
                                  old_ls: float, new_ls: float):
        """Adjust ref_p1/ref_p2 when the layer's render scale changes."""
        layer = self._floor_plans.get(fp_id)
        if not layer or old_ls == new_ls or old_ls == 0:
            return
        w, h = layer.size
        old_render_size = (w * old_ls, h * old_ls)
        new_render_size = (w * new_ls, h * new_ls)
        self.remap_layer_ref_points(fp_id, old_render_size, new_render_size)

    def set_floor_plan_size_mm(self, fp_id: str,
                               width_mm: float, height_mm: float):
        """Setzt feste Abmessungen (mm) für ein Einrichtungselement."""
        layer = self._floor_plans.get(fp_id)
        if layer:
            layer.fixed_width_mm = width_mm
            layer.fixed_height_mm = height_mm
            self.update()

    def _layer_render_size_for_scale(self, layer: "FloorPlanLayer",
                                     global_mm_per_px: float,
                                     layer_mm_per_px: Optional[float] = None,
                                     native_size: Optional[Tuple[float, float]] = None) -> Tuple[float, float]:
        ref_mpp = global_mm_per_px if global_mm_per_px > 0 else 1.0
        if layer.fixed_width_mm > 0 and layer.fixed_height_mm > 0:
            return (layer.fixed_width_mm / ref_mpp,
                    layer.fixed_height_mm / ref_mpp)
        mm_per_px = layer.mm_per_px if layer_mm_per_px is None else layer_mm_per_px
        ls = mm_per_px / ref_mpp if mm_per_px > 0 else 1.0
        w, h = native_size or layer.size
        return (w * ls, h * ls)

    def _layer_render_size(self, layer: "FloorPlanLayer") -> Tuple[float, float]:
        """Gibt die gerenderte (Breite, Höhe) in Canvas-Pixeln zurück.
        Wenn fixed_width_mm/fixed_height_mm gesetzt sind, werden diese verwendet,
        andernfalls die mm_per_px-basierte Skalierung."""
        return self._layer_render_size_for_scale(layer, self._mm_per_px)

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
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def clear_measurements(self):
        """Remove all persisted measurement lines."""
        self._measure_lines.clear()
        self.measure_changed.emit()
        self.update()

    def delete_measurement_at(self, index: int) -> bool:
        if index < 0 or index >= len(self._measure_lines):
            return False
        del self._measure_lines[index]
        if 0 <= index < len(self._measure_label_positions):
            del self._measure_label_positions[index]
        self.measure_changed.emit()
        self.update()
        return True

    def start_angle_measure(self):
        """Enter angle measurement mode (3 points)."""
        self._mode = ToolMode.MEASURE_ANGLE
        self._angle_measure_p1 = None
        self._angle_measure_p2 = None
        self._angle_measure_p3 = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def clear_angle_measurements(self):
        self._angle_measurements.clear()
        self._angle_measure_label_positions.clear()
        self.measure_changed.emit()
        self.update()

    def delete_angle_measurement_at(self, index: int) -> bool:
        if index < 0 or index >= len(self._angle_measurements):
            return False
        del self._angle_measurements[index]
        if 0 <= index < len(self._angle_measure_label_positions):
            del self._angle_measure_label_positions[index]
        self.measure_changed.emit()
        self.update()
        return True

    def cancel_active_placement(self) -> bool:
        if self._mode not in (ToolMode.PLACE_ELEC_POINT, ToolMode.PLACE_HKV, ToolMode.PLACE_TEXT):
            return False
        self._placing_elec_point_id = None
        self._placing_hkv_id = None
        self._placing_text_id = None
        self._ghost_preview_pos = None
        self._mode = ToolMode.NONE
        self.setCursor(Qt.ArrowCursor)
        self.mode_changed.emit()
        self.update()
        return True

    def _default_helper_settings(self) -> Dict[str, object]:
        return {
            "visible": True,
            "color": "#f8f32b",
            "target_length_mm": 1000.0,
            "line_width_px": 2.0,
            "line_style": "dash",
        }

    def _ensure_helper_floor(self, floor_id: Optional[str]) -> Optional[str]:
        if floor_id and floor_id in self._floor_plans:
            fid = floor_id
        elif self._helper_active_floor_id and self._helper_active_floor_id in self._floor_plans:
            fid = self._helper_active_floor_id
        elif self._floor_plan_order:
            fid = self._floor_plan_order[0]
        else:
            fid = None
        if not fid:
            return None
        self._floor_helper_lines.setdefault(fid, {})
        self._floor_helper_line_visible.setdefault(fid, {})
        self._floor_helper_line_length_mm.setdefault(fid, {})
        self._floor_helper_line_fixed.setdefault(fid, {})
        settings = self._floor_helper_settings.setdefault(fid, self._default_helper_settings())
        default_settings = self._default_helper_settings()
        for key, value in default_settings.items():
            settings.setdefault(key, value)
        return fid

    def _helper_settings(self, floor_id: Optional[str]) -> Dict[str, object]:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return self._default_helper_settings()
        return self._floor_helper_settings[fid]

    def _resolve_draw_helper_floor(self, preferred_floor_id: Optional[str]) -> Optional[str]:
        """Resolve helper draw target to a visible floor layer when possible."""
        fid = self._ensure_helper_floor(preferred_floor_id)
        if not fid:
            return None
        layer = self._floor_plans.get(fid)
        if layer is not None and bool(getattr(layer, "visible", True)):
            return fid
        for candidate_id in self._floor_plan_order:
            candidate = self._floor_plans.get(candidate_id)
            if candidate is not None and bool(getattr(candidate, "visible", True)):
                self._ensure_helper_floor(candidate_id)
                return candidate_id
        return fid

    def _helper_floor_lines(self, floor_id: Optional[str]) -> Dict[str, List[QPointF]]:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return {}
        return self._floor_helper_lines[fid]

    def _helper_floor_visible_map(self, floor_id: Optional[str]) -> Dict[str, bool]:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return {}
        return self._floor_helper_line_visible[fid]

    def _helper_line_pen_style(self, style_key: str):
        style = str(style_key or "dash").strip().lower()
        if style == "solid":
            return Qt.SolidLine
        if style == "dot":
            return Qt.DotLine
        if style == "dashdot":
            return Qt.DashDotLine
        return Qt.DashLine

    def set_active_helper_floor(self, floor_id: Optional[str]):
        fid = self._ensure_helper_floor(floor_id)
        self._helper_active_floor_id = fid
        if fid:
            settings = self._helper_settings(fid)
            self._helper_target_length_mm = float(settings.get("target_length_mm", 1000.0))
            self._helper_line_color = str(settings.get("color", "#f8f32b"))
        self.update()

    def set_helper_line_target_length_mm(self, length_mm: float,
                                         floor_id: Optional[str] = None,
                                         resize_selected: bool = False):
        fid = self._ensure_helper_floor(floor_id)
        length_mm = max(1.0, float(length_mm))
        self._helper_target_length_mm = length_mm
        if not fid:
            return
        settings = self._helper_settings(fid)
        settings["target_length_mm"] = length_mm
        if (resize_selected
                and self._helper_selected_id
                and self._helper_selected_floor_id == fid
                and self._mm_per_px > 0):
            pts = self._floor_helper_lines.get(fid, {}).get(self._helper_selected_id)
            if pts and len(pts) >= 2:
                start = pts[0]
                end = pts[1]
                dx = end.x() - start.x()
                dy = end.y() - start.y()
                direction_len = math.hypot(dx, dy)
                if direction_len > 1e-9:
                    target_px = length_mm / self._mm_per_px
                    ux = dx / direction_len
                    uy = dy / direction_len
                    pts[1] = QPointF(start.x() + ux * target_px,
                                     start.y() + uy * target_px)
                    self.helper_lines_changed.emit()
        self.update()

    def get_helper_line_target_length_mm(self, floor_id: Optional[str] = None) -> float:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return float(self._helper_target_length_mm)
        settings = self._helper_settings(fid)
        return float(settings.get("target_length_mm", 1000.0))

    def set_helper_line_color(self, color: str, floor_id: Optional[str] = None):
        fid = self._ensure_helper_floor(floor_id)
        self._helper_line_color = color
        if fid:
            settings = self._helper_settings(fid)
            settings["color"] = str(color)
        self.update()

    def get_helper_line_color(self, floor_id: Optional[str] = None) -> str:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return self._helper_line_color
        settings = self._helper_settings(fid)
        return str(settings.get("color", "#f8f32b"))

    def set_helper_line_visible(self, floor_id: str, visible: bool):
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return
        settings = self._helper_settings(fid)
        settings["visible"] = bool(visible)
        self.update()

    def get_helper_line_visible(self, floor_id: str) -> bool:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return True
        settings = self._helper_settings(fid)
        return bool(settings.get("visible", True))

    def set_helper_line_width(self, floor_id: str, width_px: float):
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return
        settings = self._helper_settings(fid)
        settings["line_width_px"] = max(0.5, float(width_px))
        self.update()

    def get_helper_line_width(self, floor_id: str) -> float:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return 2.0
        settings = self._helper_settings(fid)
        return float(settings.get("line_width_px", 2.0))

    def set_helper_line_style(self, floor_id: str, style_key: str):
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return
        style = str(style_key or "dash").strip().lower()
        if style not in {"solid", "dash", "dot", "dashdot"}:
            style = "dash"
        settings = self._helper_settings(fid)
        settings["line_style"] = style
        self.update()

    def get_helper_line_style(self, floor_id: str) -> str:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return "dash"
        settings = self._helper_settings(fid)
        style = str(settings.get("line_style", "dash")).strip().lower()
        return style if style in {"solid", "dash", "dot", "dashdot"} else "dash"

    def start_draw_helper_line(self, floor_id: Optional[str] = None):
        self.set_active_helper_floor(self._resolve_draw_helper_floor(floor_id))
        if self._helper_active_floor_id:
            self.set_helper_line_visible(self._helper_active_floor_id, True)
        self._mode = ToolMode.DRAW_HELPER_LINE
        self._helper_draw_start = None
        self._helper_draw_current = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def start_edit_helper_lines(self, floor_id: Optional[str] = None):
        self.set_active_helper_floor(floor_id)
        self._mode = ToolMode.EDIT_HELPER_LINE
        self._helper_dragging_endpoint = None
        self._helper_dragging_whole_id = None
        self._helper_drag_start = None
        self._helper_drag_origin = []
        self.setCursor(Qt.ArrowCursor)
        self.mode_changed.emit()
        self.update()

    def clear_helper_lines(self, floor_id: Optional[str] = None):
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return
        self._floor_helper_lines.get(fid, {}).clear()
        self._floor_helper_line_visible.get(fid, {}).clear()
        self._floor_helper_line_length_mm.get(fid, {}).clear()
        self._floor_helper_line_fixed.get(fid, {}).clear()
        self._helper_selected_id = None
        self._helper_selected_floor_id = None
        self.helper_lines_changed.emit()
        self.update()

    def delete_selected_helper_line(self):
        if not self._helper_selected_id or not self._helper_selected_floor_id:
            return
        lines = self._floor_helper_lines.get(self._helper_selected_floor_id, {})
        visible = self._floor_helper_line_visible.get(self._helper_selected_floor_id, {})
        lengths = self._floor_helper_line_length_mm.get(self._helper_selected_floor_id, {})
        fixed_map = self._floor_helper_line_fixed.get(self._helper_selected_floor_id, {})
        if self._helper_selected_id in lines:
            lines.pop(self._helper_selected_id, None)
            visible.pop(self._helper_selected_id, None)
            lengths.pop(self._helper_selected_id, None)
            fixed_map.pop(self._helper_selected_id, None)
            self._helper_selected_id = None
            self._helper_selected_floor_id = None
            self.helper_lines_changed.emit()
            self.update()

    def delete_helper_line(self, floor_id: str, helper_id: str):
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return
        self._floor_helper_lines.get(fid, {}).pop(helper_id, None)
        self._floor_helper_line_visible.get(fid, {}).pop(helper_id, None)
        self._floor_helper_line_length_mm.get(fid, {}).pop(helper_id, None)
        self._floor_helper_line_fixed.get(fid, {}).pop(helper_id, None)
        if self._helper_selected_id == helper_id and self._helper_selected_floor_id == fid:
            self._helper_selected_id = None
            self._helper_selected_floor_id = None
        self.helper_lines_changed.emit()
        self.update()

    def move_helper_line(self, old_floor_id: str, helper_id: str, new_floor_id: str) -> None:
        """Verschiebt eine Hilfslinie auf einen anderen Grundriss.

        Alle zugehörigen Metadaten (Sichtbarkeit, Länge, Fixed-Flag) werden
        mitgenommen.  Das ``helper_lines_changed``-Signal wird emittiert.
        """
        old_fid = old_floor_id if old_floor_id in self._floor_plans else None
        new_fid = new_floor_id if new_floor_id in self._floor_plans else None
        if not old_fid or not new_fid or old_fid == new_fid:
            return
        pts = self._floor_helper_lines.get(old_fid, {}).pop(helper_id, None)
        if pts is None:
            return
        visible = self._floor_helper_line_visible.get(old_fid, {}).pop(helper_id, True)
        length_mm = self._floor_helper_line_length_mm.get(old_fid, {}).pop(helper_id, None)
        fixed = self._floor_helper_line_fixed.get(old_fid, {}).pop(helper_id, False)
        self._floor_helper_lines.setdefault(new_fid, {})[helper_id] = pts
        self._floor_helper_line_visible.setdefault(new_fid, {})[helper_id] = visible
        if length_mm is not None:
            self._floor_helper_line_length_mm.setdefault(new_fid, {})[helper_id] = length_mm
        self._floor_helper_line_fixed.setdefault(new_fid, {})[helper_id] = fixed
        if self._helper_selected_id == helper_id and self._helper_selected_floor_id == old_fid:
            self._helper_selected_id = None
            self._helper_selected_floor_id = None
        self.helper_lines_changed.emit()
        self.update()

    def is_helper_line_length_fixed(self, floor_id: str, helper_id: str) -> bool:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return False
        return bool(self._floor_helper_line_fixed.get(fid, {}).get(helper_id, False))

    def set_helper_line_length_fixed(self, floor_id: str, helper_id: str, fixed: bool):
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return
        self._floor_helper_line_fixed.setdefault(fid, {})[helper_id] = bool(fixed)
        self.helper_lines_changed.emit()
        self.update()

    def get_helper_line_length_mm(self, floor_id: str, helper_id: str) -> float:
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return 0.0
        cached = self._floor_helper_line_length_mm.get(fid, {}).get(helper_id)
        if cached is not None:
            return float(cached)
        pts = self._floor_helper_lines.get(fid, {}).get(helper_id, [])
        if len(pts) < 2 or self._mm_per_px <= 0:
            return 0.0
        return _qdist(pts[0], pts[1]) * self._mm_per_px

    def set_helper_line_length_mm(self, floor_id: str, helper_id: str, length_mm: float):
        fid = self._ensure_helper_floor(floor_id)
        if not fid:
            return
        pts = self._floor_helper_lines.get(fid, {}).get(helper_id)
        if not pts or len(pts) < 2 or self._mm_per_px <= 0:
            return
        length_mm = max(1.0, float(length_mm))
        p1, p2 = pts[0], pts[1]
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        current_len = math.hypot(dx, dy)
        if current_len <= 1e-9:
            p2 = QPointF(p1.x() + (length_mm / self._mm_per_px), p1.y())
        else:
            ux = dx / current_len
            uy = dy / current_len
            target_px = length_mm / self._mm_per_px
            p2 = QPointF(p1.x() + ux * target_px, p1.y() + uy * target_px)
        self._floor_helper_lines[fid][helper_id] = [QPointF(p1), QPointF(p2)]
        self._floor_helper_line_length_mm.setdefault(fid, {})[helper_id] = length_mm
        self.helper_lines_changed.emit()
        self.update()

    def _next_helper_line_id(self) -> str:
        self._helper_line_counter += 1
        existing_ids = set()
        for floor_map in self._floor_helper_lines.values():
            existing_ids.update(floor_map.keys())
        while f"HL-{self._helper_line_counter}" in existing_ids:
            self._helper_line_counter += 1
        return f"HL-{self._helper_line_counter}"

    def _snap_measure_point(self, pt: QPointF) -> QPointF:
        ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        if self._grid_visible and not ctrl_held:
            return self._snap_to_grid(pt)
        return pt

    def set_measure_color(self, color: str):
        """Set color for measurement tool (hex string, e.g. '#00ff00')."""
        self._measure_color = color
        self.update()

    def get_measure_color(self) -> str:
        """Get current measurement tool color."""
        return self._measure_color

    def set_ref_line_color(self, fp_id: str, color: str):
        """Set reference line color for a specific floor plan."""
        self._ref_line_colors[fp_id] = color
        self.update()

    def get_ref_line_color(self, fp_id: str) -> str:
        """Get reference line color for a floor plan (defaults to #ffdd00)."""
        return self._ref_line_colors.get(fp_id, "#ffdd00")

    def set_ref_line_visible(self, fp_id: str, visible: bool):
        """Set reference line visibility for a specific floor plan."""
        self._ref_line_visible[fp_id] = visible
        self.update()

    def get_ref_line_visible(self, fp_id: str) -> bool:
        """Get reference line visibility for a floor plan (defaults to True)."""
        return self._ref_line_visible.get(fp_id, True)

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

    def grab_source_rect(self, rect: QRectF) -> QPixmap:
        """Render a specific rectangular region to a preview pixmap (capped at 2×)."""
        if rect.width() <= 0 or rect.height() <= 0:
            return QPixmap()

        # Target pixmap size - aim for preview size
        target_size = 400
        scale_x = target_size / rect.width() if rect.width() > 0 else 1.0
        scale_y = target_size / rect.height() if rect.height() > 0 else 1.0
        scale = min(scale_x, scale_y, 2.0)  # Cap at 2x to avoid huge pixmaps

        pm_width = max(1, int(rect.width() * scale))
        pm_height = max(1, int(rect.height() * scale))

        # Save current view state
        old_scale = float(self._scale)
        old_offset = QPointF(self._offset)

        old_min_size = self.minimumSize()
        old_parent = self.parentWidget()
        try:
            # Temporarily adjust scale/offset to show only the rect
            self._scale = scale
            self._offset = QPointF(-rect.x() * scale, -rect.y() * scale)

            # Detach from the layout-managing parent (if any) so its layout
            # doesn't override our explicit resize below, and bypass the
            # minimum size constraint so requesting a pixmap smaller than the
            # UI minimum works.
            old_size = (self.width(), self.height())
            if old_parent is not None:
                self.setParent(None)
            self.setMinimumSize(0, 0)
            self.resize(pm_width, pm_height)

            # Grab the visible area
            pm = self.grab()

            # Restore widget size
            self.resize(old_size[0], old_size[1])

            return pm
        finally:
            # Always restore original view state
            self._scale = old_scale
            self._offset = old_offset
            self.setMinimumSize(old_min_size)
            if old_parent is not None:
                self.setParent(old_parent)
            self.update()

    def get_default_source_rect(self) -> QRectF:
        """Return the default source rect for export (export frame or SVG size)."""
        if self._export_frame:
            return QRectF(self._export_frame.normalized())
        w, h = self._svg_size
        if w > 0 and h > 0:
            return QRectF(0, 0, w, h)
        return QRectF(0, 0, 800, 600)

    def render_for_export(
        self,
        source_rect: "Optional[QRectF]" = None,
        output_w: int = 2480,
        output_h: int = 1754,
    ) -> "QImage":
        """Render canvas content to a high-resolution QImage for export.

        Uses the same paint path as the regular view but at the requested output
        resolution.  The canvas is temporarily resized and the view transform
        adjusted so *source_rect* fills the output.  ``grab()`` triggers a
        synchronous ``paintEvent`` without an event-loop round-trip.
        """
        from PySide6.QtGui import QImage  # noqa: PLC0415

        if source_rect is None:
            source_rect = self.get_default_source_rect()
        if source_rect.isEmpty():
            source_rect = QRectF(0, 0, 800, 600)

        sx = output_w / max(source_rect.width(), 1)
        sy = output_h / max(source_rect.height(), 1)
        s = min(sx, sy)
        sw = source_rect.width() * s
        sh = source_rect.height() * s
        off = QPointF(
            (output_w - sw) / 2.0 - source_rect.x() * s,
            (output_h - sh) / 2.0 - source_rect.y() * s,
        )

        old_scale = float(self._scale)
        old_offset = QPointF(self._offset)
        old_w = self.width()
        old_h = self.height()
        old_min_size = self.minimumSize()
        old_parent = self.parentWidget()

        try:
            self._scale = s
            self._offset = off
            # Temporarily detach from the layout so resize() is not overridden
            # by the parent's layout manager (same approach as grab_source_rect).
            if old_parent is not None:
                self.setParent(None)  # type: ignore[arg-type]
            self.setMinimumSize(0, 0)
            self.resize(output_w, output_h)
            pixmap = self.grab()
        finally:
            self._scale = old_scale
            self._offset = old_offset
            self.resize(old_w, old_h)
            self.setMinimumSize(old_min_size)
            if old_parent is not None:
                self.setParent(old_parent)  # type: ignore[arg-type]
            self.update()

        return pixmap.toImage()

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
        self._current_elec_room_id = None
        self._current_points = []
        self._ensure_color(circuit_id)
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()

    def start_draw_elec_room(self, room_id: str):
        self._mode = ToolMode.DRAW_POLY
        self._current_circuit_id = None
        self._current_elec_room_id = room_id
        self._current_points = []
        self._elec_room_visible.setdefault(room_id, True)
        self._ensure_color(room_id)
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()

    def start_draw_floor_plan_polygon(self, fp_id: str):
        """Start drawing a polygon as alternative source for a floor plan layer."""
        if fp_id not in self._floor_plans:
            return
        self._mode = ToolMode.DRAW_FURNITURE_POLY
        self._current_furniture_id = fp_id
        self._current_points = []
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()

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
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def start_edit_polygon(self, circuit_id: str):
        if circuit_id not in self._polygons:
            return
        self._edit_floor_polygon_id = None
        self._edit_elec_room_id = None
        self._edit_polygon_cid = circuit_id
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
        self._mode = ToolMode.EDIT_POLYGON
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_edit_floor_plan_polygon(self, fp_id: str):
        layer = self._floor_plans.get(fp_id)
        if not layer or len(layer.polygon) < 3:
            return
        self._edit_polygon_cid = None
        self._edit_elec_room_id = None
        self._edit_floor_polygon_id = fp_id
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
        self._mode = ToolMode.EDIT_POLYGON
        self.setCursor(Qt.CrossCursor)
        self.update()

    def start_edit_elec_room_polygon(self, room_id: str):
        if room_id not in self._elec_room_polygons:
            return
        self._edit_polygon_cid = None
        self._edit_floor_polygon_id = None
        self._edit_elec_room_id = room_id
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
        self._mode = ToolMode.EDIT_POLYGON
        self.setCursor(Qt.CrossCursor)
        self.update()

    def _floor_polygon_render_size(self, layer: "FloorPlanLayer") -> Tuple[float, float]:
        """Rendered size for floor polygons. Polygons ignore mm scaling."""
        w, h = layer.size
        return (max(1.0, w), max(1.0, h))

    def _floor_polygon_world_cache_key(self, layer: "FloorPlanLayer") -> tuple:
        return (
            layer.offset_x,
            layer.offset_y,
            layer.rotation,
            layer.size[0],
            layer.size[1],
            tuple((p.x(), p.y()) for p in layer.polygon),
        )

    def _floor_polygon_points_world(self, fp_id: str) -> List[QPointF]:
        """Return polygon points transformed to canvas coordinates."""
        layer = self._floor_plans.get(fp_id)
        if not layer or not layer.polygon:
            return []
        key = self._floor_polygon_world_cache_key(layer)
        cached = self._floor_polygon_world_cache.get(fp_id)
        if cached and cached[0] == key:
            return cached[1]
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
        self._floor_polygon_world_cache[fp_id] = (key, out, QPolygonF(out))
        return out

    def _floor_polygon_world_polygon(self, fp_id: str) -> QPolygonF:
        layer = self._floor_plans.get(fp_id)
        if not layer or not layer.polygon:
            return QPolygonF()
        key = self._floor_polygon_world_cache_key(layer)
        cached = self._floor_polygon_world_cache.get(fp_id)
        if cached and cached[0] == key:
            return cached[2]
        self._floor_polygon_points_world(fp_id)
        cached = self._floor_polygon_world_cache.get(fp_id)
        return cached[2] if cached else QPolygonF()

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
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_floor_polygon_edge(self, canvas_pt: QPointF, fp_id: str) -> Optional[Tuple[int, int]]:
        pts = self._floor_polygon_points_world(fp_id)
        if len(pts) < 2:
            return None
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
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
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
        self._mode = ToolMode.EDIT_ROUTE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def _hit_polygon_point(self, canvas_pt: QPointF, cid: str) -> Optional[int]:
        pts = self._polygons.get(cid, [])
        if not pts:
            pts = self._elec_room_polygons.get(cid, [])
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_polygon_edge(self, canvas_pt: QPointF, cid: str) -> Optional[Tuple[int, int]]:
        pts = self._polygons.get(cid, [])
        if not pts:
            pts = self._elec_room_polygons.get(cid, [])
        if len(pts) < 2:
            return None
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
        for i in range(len(pts)):
            p1 = pts[i]
            p2 = pts[(i + 1) % len(pts)]
            proj = _project_on_segment(canvas_pt, p1, p2)
            if _qdist(canvas_pt, proj) < threshold:
                return (i, (i + 1) % len(pts))
        return None

    def _hit_route_point_in_circuit(self, canvas_pt: QPointF, cid: str) -> Optional[int]:
        pts = self._manual_routes.get(cid, [])
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_route_edge(self, canvas_pt: QPointF, cid: str) -> Optional[Tuple[int, int]]:
        pts = self._manual_routes.get(cid, [])
        if len(pts) < 2:
            return None
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
        for i in range(len(pts) - 1):
            p1 = pts[i]
            p2 = pts[i + 1]
            proj = _project_on_segment(canvas_pt, p1, p2)
            if _qdist(canvas_pt, proj) < threshold:
                return (i, i + 1)
        return None

    def _delete_polygon_point(self, cid: str, idx: int):
        if cid in self._polygons:
            if len(self._polygons[cid]) <= 3:
                return
            del self._polygons[cid][idx]
            self.polygon_changed.emit(cid)
            self.update()
            return
        if cid in self._elec_room_polygons:
            if len(self._elec_room_polygons[cid]) <= 3:
                return
            del self._elec_room_polygons[cid][idx]
            self.elec_room_polygon_changed.emit(cid)
            self.update()

    def _insert_polygon_point(self, cid: str, idx1: int, idx2: int, pt: QPointF):
        pts = self._polygons.get(cid)
        is_room = False
        if pts is None:
            pts = self._elec_room_polygons.get(cid)
            is_room = True
        if pts is None:
            return
        next_idx = (idx1 + 1) % len(pts)
        if idx2 == next_idx:
            pts.insert(next_idx, pt)
            if is_room:
                self.elec_room_polygon_changed.emit(cid)
            else:
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

    def context_insert_point(self, obj_type: str, obj_id: str, canvas_pt: QPointF) -> bool:
        if self._mode != ToolMode.NONE:
            return False

        if obj_type == "elec_cable":
            hit = self._hit_elec_cable_edge(canvas_pt, obj_id)
            if hit is None:
                return False
            idx1, idx2 = hit
            pts = self._elec_cables.get(obj_id)
            if not pts:
                return False
            p1, p2 = pts[idx1], pts[idx2]
            mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
            pts.insert(idx2, mid)
            self.elec_cable_changed.emit(obj_id)
            self.update()
            return True

        if obj_type == "supply_line":
            hit = self._hit_supply_line_edge(canvas_pt, obj_id)
            if hit is None:
                return False
            idx1, idx2 = hit
            pts = self._supply_lines.get(obj_id)
            if not pts:
                return False
            p1, p2 = pts[idx1], pts[idx2]
            mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
            pts.insert(idx2, mid)
            self.supply_line_changed.emit(obj_id)
            self.update()
            return True

        if obj_type == "hkv_line":
            hit = self._hit_hkv_line_edge(canvas_pt, obj_id)
            if hit is None:
                return False
            idx1, idx2 = hit
            pts = self._hkv_lines.get(obj_id)
            if not pts:
                return False
            p1, p2 = pts[idx1], pts[idx2]
            mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
            pts.insert(idx2, mid)
            self.hkv_line_changed.emit(obj_id)
            self.update()
            return True

        if obj_type == "route":
            hit = self._hit_route_edge(canvas_pt, obj_id)
            if hit is None:
                return False
            idx1, idx2 = hit
            pts = self._manual_routes.get(obj_id)
            if not pts:
                return False
            p1, p2 = pts[idx1], pts[idx2]
            mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
            self._insert_route_point(obj_id, idx1, idx2, mid)
            return True

        if obj_type in {"polygon", "elec_room"}:
            hit = self._hit_polygon_edge(canvas_pt, obj_id)
            if hit is None:
                return False
            idx1, idx2 = hit
            pts = self._polygons.get(obj_id)
            if pts is None:
                pts = self._elec_room_polygons.get(obj_id)
            if not pts:
                return False
            p1, p2 = pts[idx1], pts[idx2]
            mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
            self._insert_polygon_point(obj_id, idx1, idx2, mid)
            return True

        if obj_type == "floor_polygon":
            hit = self._hit_floor_polygon_edge(canvas_pt, obj_id)
            if hit is None:
                return False
            idx1, idx2 = hit
            self._insert_floor_polygon_point(obj_id, idx1, idx2, canvas_pt)
            return True

        return False

    def context_delete_point(self, obj_type: str, obj_id: str, canvas_pt: QPointF) -> bool:
        if self._mode != ToolMode.NONE:
            return False

        if obj_type == "elec_cable":
            hit = self._hit_elec_cable_point(canvas_pt, obj_id)
            if hit is None:
                return False
            pts = self._elec_cables.get(obj_id, [])
            if len(pts) <= 2:
                return False
            del pts[hit]
            self.elec_cable_changed.emit(obj_id)
            self.update()
            return True

        if obj_type == "supply_line":
            hit = self._hit_supply_line_point(canvas_pt, obj_id)
            if hit is None:
                return False
            pts = self._supply_lines.get(obj_id, [])
            if len(pts) <= 2:
                return False
            del pts[hit]
            self.supply_line_changed.emit(obj_id)
            self.update()
            return True

        if obj_type == "hkv_line":
            hit = self._hit_hkv_line_point(canvas_pt, obj_id)
            if hit is None:
                return False
            pts = self._hkv_lines.get(obj_id, [])
            if len(pts) <= 2:
                return False
            del pts[hit]
            self.hkv_line_changed.emit(obj_id)
            self.update()
            return True

        if obj_type == "route":
            hit = self._hit_route_point_in_circuit(canvas_pt, obj_id)
            if hit is None:
                return False
            pts = self._manual_routes.get(obj_id, [])
            if len(pts) <= 2:
                return False
            self._delete_route_point(obj_id, hit)
            return True

        if obj_type in {"polygon", "elec_room"}:
            hit = self._hit_polygon_point(canvas_pt, obj_id)
            if hit is None:
                return False
            before = len(self._polygons.get(obj_id, self._elec_room_polygons.get(obj_id, [])))
            self._delete_polygon_point(obj_id, hit)
            after = len(self._polygons.get(obj_id, self._elec_room_polygons.get(obj_id, [])))
            return after < before

        if obj_type == "floor_polygon":
            hit = self._hit_floor_polygon_point(canvas_pt, obj_id)
            if hit is None:
                return False
            layer = self._floor_plans.get(obj_id)
            if not layer:
                return False
            before = len(layer.polygon)
            self._delete_floor_polygon_point(obj_id, hit)
            after = len(layer.polygon)
            return after < before

        return False

    def set_color(self, circuit_id: str, color: QColor):
        self._color_map[circuit_id] = color
        self.update()

    def set_polygon_name(self, circuit_id: str, name: str):
        self._label_map[circuit_id] = name
        self.update()

    def delete_elec_room(self, room_id: str):
        for d in (
            self._elec_room_polygons,
            self._elec_room_visible,
            self._label_positions,
            self._label_font_sizes,
            self._label_visible,
            self._label_rects,
            self._label_draw_pos,
        ):
            d.pop(room_id, None)
        self._color_map.pop(room_id, None)
        self._label_map.pop(room_id, None)
        self.update()

    def get_elec_room_for_point(self, point_id: str) -> str:
        pt = self._elec_points.get(point_id)
        if pt is None:
            return ""
        for rid, poly in self._elec_room_polygons.items():
            if not self._elec_room_visible.get(rid, True):
                continue
            if len(poly) < 3:
                continue
            if self._point_in_polygon(pt, poly):
                return rid
        return ""

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

    def set_label_visible(self, item_id: str, visible: bool):
        self._label_visible[item_id] = bool(visible)
        self.update()

    def delete_circuit(self, circuit_id: str):
        for d in (self._polygons, self._color_map, self._start_points,
                  self._label_map, self._helper_lines, self._show_helper_line,
                  self._manual_routes, self._route_wall_dist_px,
                  self._route_line_dist_px, self._supply_lines,
                  self._label_positions, self._label_font_sizes, self._label_visible,
                  self._label_rects, self._label_draw_pos):
            d.pop(circuit_id, None)
        self._manual_route_path_cache.pop(circuit_id, None)
        self._supply_line_path_cache.pop(circuit_id, None)
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
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def start_edit_supply_line(self, circuit_id: str):
        if circuit_id not in self._supply_lines:
            return
        self._edit_supply_cid = circuit_id
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
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
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_supply_line_edge(self, canvas_pt: QPointF,
                               cid: str) -> Optional[Tuple[int, int]]:
        pts = self._supply_lines.get(cid, [])
        if len(pts) < 2:
            return None
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
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
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def update_elec_point_size(self, point_id: str,
                                width_mm: float, height_mm: float):
        scale = max(self._mm_per_px, 1e-9)
        self._elec_point_size_px[point_id] = (width_mm / scale, height_mm / scale)
        self.update()

    def set_elec_point_icon(self, point_id: str, path: str):
        if path and is_svg_asset_ref(path):
            renderer = self._get_cached_svg_renderer(path)
            if renderer is not None:
                self._elec_point_svgs[point_id] = renderer
                self._elec_point_icons[point_id] = None
            else:
                self._elec_point_svgs[point_id] = None
                self._elec_point_icons[point_id] = None
        elif path:
            pm = self._get_cached_pixmap(path)
            self._elec_point_icons[point_id] = QPixmap(pm) if pm is not None else None
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
        self._drawing_cable_from_start = False
        self._current_elec_cable_preview = None
        self._mode = ToolMode.DRAW_ELEC_CABLE
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def start_draw_elec_cable_from_ap(self, cable_id: str, ap_id: str):
        """Startet das Kabelzeichnen mit festem Start am angegebenen AP."""
        self.start_draw_elec_cable(cable_id)
        ap_pos = self._elec_points.get(ap_id)
        if ap_pos is None:
            return
        self._current_elec_cable_points = [QPointF(ap_pos)]
        self._cable_start_ap[cable_id] = ap_id
        self._drawing_cable_from_start = False
        self._current_elec_cable_preview = None
        self.update()

    def start_edit_elec_cable(self, cable_id: str):
        if cable_id not in self._elec_cables:
            return
        self._edit_elec_cable_id = cable_id
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
        self._mode = ToolMode.EDIT_ELEC_CABLE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def delete_elec_point(self, point_id: str):
        for d in (self._elec_points, self._elec_point_size_px,
                  self._elec_point_icons, self._elec_visible,
                  self._elec_point_position, self._elec_point_height,
                  self._elec_point_notes, self._elec_point_smarthome_device,
                  self._elec_point_smarthome_device_color,
                  self._label_positions, self._label_font_sizes, self._label_visible,
                  self._label_rects, self._label_draw_pos):
            d.pop(point_id, None)
        self._color_map.pop(point_id, None)
        self.update()

    def set_elec_cable_stroke_width(self, cable_id: str, width: float):
        self._elec_cable_stroke_width[cable_id] = max(0.5, min(10.0, width))
        self.update()

    def set_elec_cable_type_text(self, cable_id: str, cable_type: str):
        self._elec_cable_type_text[cable_id] = str(cable_type or "").strip()
        self.update()

    def set_elec_cable_type_label_visible(self, cable_id: str, visible: bool):
        self._elec_cable_type_label_visible[cable_id] = bool(visible)
        self.update()

    def _elec_cable_type_label_id(self, cable_id: str) -> str:
        return f"{cable_id}::type"

    def delete_elec_cable(self, cable_id: str):
        cable_type_label_id = self._elec_cable_type_label_id(cable_id)
        for d in (self._elec_cables, self._elec_visible,
                  self._elec_cable_notes, self._elec_cable_stroke_width,
                  self._elec_cable_type_text, self._elec_cable_type_label_visible,
                  self._cable_start_ap, self._cable_end_ap,
                  self._label_positions, self._label_font_sizes, self._label_visible,
                  self._label_rects, self._label_draw_pos):
            d.pop(cable_id, None)
            d.pop(cable_type_label_id, None)
        self._elec_cable_path_cache.pop(cable_id, None)
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
            min_pick_half = self._px_to_canvas_units(MIN_SYMBOL_PICK_HALF_PX)
            half_w = max(w / 2, min_pick_half)
            half_h = max(h / 2, min_pick_half)
            rect = QRectF(pos.x() - half_w, pos.y() - half_h,
                          half_w * 2, half_h * 2)
            if rect.contains(canvas_pt):
                return pid
        return None

    def _hit_elec_cable_point(self, canvas_pt: QPointF,
                               cable_id: str) -> Optional[int]:
        pts = self._elec_cables.get(cable_id, [])
        threshold = self._px_to_canvas_units(HIT_CABLE_POINT_RADIUS_PX)
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_elec_cable_edge(self, canvas_pt: QPointF,
                              cable_id: str) -> Optional[Tuple[int, int]]:
        pts = self._elec_cables.get(cable_id, [])
        if len(pts) < 2:
            return None
        sw = self._elec_cable_stroke_width.get(cable_id, 2.0)
        threshold = max(8.0, sw * 2) / self._scale
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

    def _apply_helper_construction_snap(self,
                                        anchor: QPointF,
                                        target: QPointF,
                                        tolerance_deg: float = 3.0,
                                        floor_id: Optional[str] = None,
                                        exclude_line_id: str = "") -> QPointF:
        """Snap helper-line direction to exact 45° multiples when close enough.

        This yields exact 45°/90° constructions (and corresponding opposite angles)
        while keeping free movement outside the tolerance window.
        """
        dx = target.x() - anchor.x()
        dy = target.y() - anchor.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return target

        angle_deg = math.degrees(math.atan2(dy, dx))

        candidate_angles: List[float] = []

        # 1) Globale Konstruktionsachsen: Vielfache von 45°
        for k in range(-8, 9):
            candidate_angles.append(k * 45.0)

        # 2) Relative Konstruktionsachsen: an vorhandene Hilfslinien am Anchor andocken
        fid = floor_id or self._helper_active_floor_id
        if fid:
            lines = self._floor_helper_lines.get(fid, {})
            visible = self._floor_helper_line_visible.get(fid, {})
            anchor_tol = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
            for hid, pts in lines.items():
                if hid == exclude_line_id or not visible.get(hid, True) or len(pts) < 2:
                    continue
                p1, p2 = pts[0], pts[1]
                base_vec = None
                if _qdist(anchor, p1) <= anchor_tol:
                    base_vec = (p2.x() - p1.x(), p2.y() - p1.y())
                elif _qdist(anchor, p2) <= anchor_tol:
                    base_vec = (p1.x() - p2.x(), p1.y() - p2.y())
                if base_vec is None:
                    continue
                bvx, bvy = base_vec
                blen = math.hypot(bvx, bvy)
                if blen < 1e-9:
                    continue
                base_angle = math.degrees(math.atan2(bvy, bvx))
                # ±45° und ±90° relativ zur Basis, plus Fortsetzung (0°)
                for delta in (-180.0, -135.0, -90.0, -45.0, 0.0, 45.0, 90.0, 135.0, 180.0):
                    candidate_angles.append(base_angle + delta)

        # Bestes Kandidatenwinkelziel wählen
        best_angle = None
        best_diff = float("inf")
        for cand in candidate_angles:
            diff = abs(((angle_deg - cand + 180.0) % 360.0) - 180.0)
            if diff < best_diff:
                best_diff = diff
                best_angle = cand

        if best_angle is None or best_diff > tolerance_deg:
            return target

        rad = math.radians(best_angle)
        return QPointF(
            anchor.x() + math.cos(rad) * dist,
            anchor.y() + math.sin(rad) * dist,
        )

    # ------------------------------------------------------------------
    # Layer-basierter Selektionsfilter (Workspace-Tabs)
    # ------------------------------------------------------------------
    #: Objekttyp aus _hit_any_object -> Layer-Wert (model.layers.LayerId)
    _OBJECT_LAYERS = {
        "elec_point": "electrical",
        "elec_cable": "electrical",
        "elec_room": "electrical",
        "hkv": "heating",
        "hkv_line": "heating",
        "supply_line": "heating",
        "route": "heating",
        "polygon": "heating",
        "helper_line": "annotation",
        "text": "annotation",
        "distance_measure": "annotation",
        "angle_measure": "annotation",
    }

    def set_tool_mode(self, mode: "ToolMode") -> None:
        """Setzt den Werkzeugmodus (wird von der Werkzeugpalette genutzt)."""
        if mode is self._mode:
            return
        self._mode = mode
        self.mode_changed.emit()
        self.update()

    def tool_mode(self) -> "ToolMode":
        return self._mode

    # ------------------------------------------------------------------
    # Dokument-Anbindung
    # ------------------------------------------------------------------
    def set_document(self, document, stages=None) -> None:
        """Bindet den Canvas an ein ``Document``.

        Die Projektdaten-Container werden durch dict-kompatible Views ersetzt,
        die direkt auf das Dokument schreiben. Damit ist das Dokument die
        einzige Datenquelle; Zeichnen im Canvas verändert sofort das Projekt.

        Rein bildbezogene Daten (Pixmaps, SVG-Renderer) bleiben im Canvas.
        """
        from model.canvas_binding import bind_canvas  # lokal: Zyklus vermeiden

        self._document = document
        bind_canvas(self, document, stages, on_change=self._on_document_data_changed)
        self._rebuild_label_map()
        self.update()

    def _rebuild_label_map(self) -> None:
        """Befüllt ``_label_map`` aus den Elementnamen des gebundenen Dokuments."""
        if self._document is None:
            return
        for group in (
            self._document.elements.get("circuits", {}),
            self._document.elements.get("elec_rooms", {}),
            self._document.elements.get("elec_points", {}),
            self._document.elements.get("elec_cables", {}),
            self._document.elements.get("hkv_points", {}),
            self._document.elements.get("hkv_lines", {}),
        ):
            for eid, element in group.items():
                name = str(getattr(element, "name", "") or "").strip()
                self._label_map[eid] = name if name else eid

    def document(self):
        """Das aktuell gebundene ``Document`` (oder ``None``)."""
        return getattr(self, "_document", None)

    def set_element_visible(self, element_id: str, visible: bool) -> None:
        """Setzt die Sichtbarkeit eines Elements unabhängig von seinem Typ.

        Ersetzt direkte Zugriffe auf die internen Sichtbarkeits-Maps von außen.
        Grundrisse und Einrichtungsobjekte werden inklusive Referenzlinie und
        Hilfslinien geschaltet.
        """
        visible = bool(visible)

        if element_id in self._floor_plans:
            self.set_floor_plan_visible(element_id, visible)
            self.set_ref_line_visible(element_id, visible)
            self.set_helper_line_visible(element_id, visible)
            return

        for mapping in (
            self._circuit_visible,
            self._elec_visible,
            self._elec_room_visible,
            self._hkv_visible,
            self._hkv_line_visible,
            self._text_visible,
        ):
            if element_id in mapping:
                mapping[element_id] = visible

        self.update()

    def set_helper_line_item_visible(self, floor_id: str, helper_id: str, visible: bool) -> None:
        """Set visibility for one helper line on a specific floor plan."""
        fid = str(floor_id or "")
        hid = str(helper_id or "")
        if not fid or not hid:
            return
        self._floor_helper_line_visible.setdefault(fid, {})[hid] = bool(visible)
        self.helper_lines_changed.emit()
        self.update()

    def get_element_visible(self, element_id: str) -> bool:
        """Liest die Sichtbarkeit eines Elements unabhängig von seinem Typ."""
        if element_id in self._floor_plans:
            layer = self._floor_plans.get(element_id)
            return bool(getattr(layer, "visible", True))

        for mapping in (
            self._circuit_visible,
            self._elec_visible,
            self._elec_room_visible,
            self._hkv_visible,
            self._hkv_line_visible,
            self._text_visible,
        ):
            if element_id in mapping:
                return bool(mapping.get(element_id, True))
        return True

    def register_element(self, element_id: str, visible: bool = True) -> None:
        """Meldet ein neu angelegtes Element beim Canvas an.

        Ersetzt direkte Schreibzugriffe auf die Sichtbarkeits-Maps beim
        Anlegen neuer Elemente.
        """
        self.set_element_visible(element_id, visible)

    def _on_document_data_changed(self, element_id: str) -> None:
        """Wird von den Views bei jeder Datenänderung aufgerufen."""
        if self._document is not None:
            self._rebuild_label_map()
        self.document_data_changed.emit(element_id)

    def set_selectable_layers(self, layers) -> None:
        """Schränkt die Selektion auf bestimmte Layer ein.

        ``layers`` ist eine Menge von ``LayerId`` oder deren String-Werten.
        ``None`` hebt die Einschränkung auf. Die Sichtbarkeit bleibt
        unverändert – nicht aktive Elemente werden nur nicht selektierbar.
        """
        if layers is None:
            self._selectable_layers = None
        else:
            self._selectable_layers = {
                getattr(layer, "value", layer) for layer in layers
            }

        # Laufende Drag-Operationen beenden, wenn ihr Layer nicht mehr aktiv ist.
        if self._dragging_route_point:
            owner_id, _idx = self._dragging_route_point
            if not self._is_selectable(self._owner_obj_type(owner_id), owner_id):
                self._dragging_route_point = None
                if self._mode in (
                    ToolMode.MOVE_ROUTE_POINT,
                    ToolMode.EDIT_ROUTE,
                    ToolMode.EDIT_POLYGON,
                    ToolMode.EDIT_ELEC_CABLE,
                    ToolMode.EDIT_SUPPLY_LINE,
                    ToolMode.EDIT_HKV_LINE,
                ):
                    self._mode = ToolMode.NONE
                    self.setCursor(Qt.ArrowCursor)

        if self._dragging_elec_cable_id and not self._is_selectable("elec_cable", self._dragging_elec_cable_id):
            self._dragging_elec_cable_id = None
            self._dragging_elec_cable_start = None
            self._dragging_elec_cable_origin = []
            self._dragging_elec_cable_fixed_indices = set()

        self._dragging_multi = {
            item for item in self._dragging_multi if self._is_selectable(item[0], item[1])
        }
        self._multi_selected = {
            item for item in self._multi_selected if self._is_selectable(item[0], item[1])
        }

        if self._selected_item_id and self._selected_item_type:
            if not self._is_selectable(self._selected_item_type, self._selected_item_id):
                self._selected_item_id = None
                self._selected_item_type = None
        self.update()

    def _object_layer(self, obj_type: str, obj_id: str) -> str:
        if obj_type == "floor_polygon":
            return "furniture" if str(obj_id).startswith("einr") else "floorplan"
        return self._OBJECT_LAYERS.get(obj_type, "")

    def _owner_obj_type(self, owner_id: str) -> str:
        """Ermittelt den Objekttyp einer Punktlisten-ID für ``_is_selectable``.

        ``_dragging_route_point`` speichert nur ``(owner_id, index)`` – der Typ
        muss über die Zugehörigkeit zur jeweiligen Punktlisten-Map bestimmt
        werden. Reihenfolge = Eindeutigkeit der ID-Präfixe.
        """
        if owner_id in self._elec_cables:
            return "elec_cable"
        if owner_id in self._hkv_lines:
            return "hkv_line"
        if owner_id in self._elec_room_polygons:
            return "elec_room"
        if owner_id in self._manual_routes or owner_id in self._polygons:
            return "route"
        if owner_id in self._supply_lines:
            return "supply_line"
        if owner_id.startswith("MSRD-"):
            return "distance_measure"
        if owner_id.startswith("MSRA-"):
            return "angle_measure"
        if owner_id in self._floor_plans:
            return "floor_polygon"
        return ""

    def _measurement_obj_to_index(self, measurement_id: str, prefix: str) -> Optional[int]:
        if not measurement_id.startswith(prefix + "-"):
            return None
        try:
            idx = int(measurement_id.split("-", 1)[1]) - 1
        except (TypeError, ValueError, IndexError):
            return None
        return idx if idx >= 0 else None

    def _measurement_distance(self, p1: QPointF, p2: QPointF) -> float:
        return _qdist(p1, p2) * self._mm_per_px if self._mm_per_px > 0 else 0.0

    def _measurement_angle_deg(self, p1: QPointF, p2: QPointF, p3: QPointF) -> float:
        v1x, v1y = p1.x() - p2.x(), p1.y() - p2.y()
        v2x, v2y = p3.x() - p2.x(), p3.y() - p2.y()
        l1 = math.hypot(v1x, v1y)
        l2 = math.hypot(v2x, v2y)
        if l1 <= 1e-9 or l2 <= 1e-9:
            return 0.0
        dot = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (l1 * l2)))
        return math.degrees(math.acos(dot))

    def _measurement_style(self, measurement_id: str) -> Dict[str, object]:
        style: Dict[str, object] = {
            "visible": True,
            "color": self._measure_color,
            "line_style": "dashdot",
            "stroke_width": 2.0,
            "text_size": 10.0,
            "auto_label_pos": True,
            "name": measurement_id,
        }
        if self._document is None:
            return style

        element = self._document.get(measurement_id)
        if element is None:
            return style

        style["visible"] = bool(getattr(element, "visible", True))
        color = str(element.data.get("color") or "").strip()
        if color:
            style["color"] = color
        style["line_style"] = str(element.data.get("line_style") or style["line_style"])
        try:
            style["stroke_width"] = float(element.data.get("stroke_width", style["stroke_width"]))
        except (TypeError, ValueError):
            pass
        try:
            style["text_size"] = float(element.data.get("text_size", style["text_size"]))
        except (TypeError, ValueError):
            pass
        style["auto_label_pos"] = bool(element.data.get("auto_label_pos", style["auto_label_pos"]))
        name = str(getattr(element, "name", "") or "").strip()
        if name:
            style["name"] = name
        return style

    def _set_measure_auto_label_pos(self, measurement_id: str, enabled: bool) -> None:
        """Update auto-label flag for a persisted measurement element if present."""
        if self._document is None:
            return
        element = self._document.get(measurement_id)
        if element is None:
            return
        element.data["auto_label_pos"] = bool(enabled)

    def _normalize_measure_label_positions(self) -> None:
        """Keep distance label positions aligned 1:1 with stored distance lines."""
        line_count = len(self._measure_lines)
        label_count = len(self._measure_label_positions)
        if label_count > line_count:
            del self._measure_label_positions[line_count:]
            self._debug_measure_pos("LABELS-TRUNCATE", lines=line_count, labels_before=label_count)
            return
        if label_count < line_count:
            for idx in range(label_count, line_count):
                _p1, p2, _mm = self._measure_lines[idx]
                self._measure_label_positions.append((float(p2.x()), float(p2.y())))
            self._debug_measure_pos("LABELS-PAD", lines=line_count, labels_before=label_count)

    def _is_selectable(self, obj_type: str, obj_id: str) -> bool:
        allowed = getattr(self, "_selectable_layers", None)
        if not allowed:
            return True
        return self._object_layer(obj_type, obj_id) in allowed

    def _hit_any_object(self, canvas_pt: QPointF) -> Optional[Tuple[str, str]]:
        """Try to hit any clickable object. Returns (object_type, object_id) or None.
        Checks in this order: foreground objects first, floor plans last.
        Objekte nicht aktiver Layer werden übersprungen (Workspace-Filter)."""
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        selectable = self._is_selectable

        # 1. Electrical points (highest priority)
        ap = self._hit_elec_point(canvas_pt)
        if ap and selectable("elec_point", ap):
            return ("elec_point", ap)

        # 2. HKV points
        hkv = self._hit_hkv(canvas_pt)
        if hkv and selectable("hkv", hkv):
            return ("hkv", hkv)

        # 3. Electrical cables
        for kid, pts in self._elec_cables.items():
            if not self._elec_visible.get(kid, True) or not selectable("elec_cable", kid):
                continue
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        return ("elec_cable", kid)

        # 4. HKV lines
        for lid, pts in self._hkv_lines.items():
            if not self._hkv_line_visible.get(lid, True) or not selectable("hkv_line", lid):
                continue
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        return ("hkv_line", lid)

        # 5. Supply lines
        for cid, pts in self._supply_lines.items():
            if not self._circuit_visible.get(cid, True) or not selectable("supply_line", cid):
                continue
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        return ("supply_line", cid)

        # 6. Routes (manual)
        for cid, pts in self._manual_routes.items():
            if not self._circuit_visible.get(cid, True) or not selectable("route", cid):
                continue
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    proj = _project_on_segment(canvas_pt, pts[i], pts[i + 1])
                    if _qdist(canvas_pt, proj) < threshold:
                        return ("route", cid)

        # 6b. Distance measurements
        if selectable("distance_measure", ""):
            hit = self._hit_distance_measurement(canvas_pt)
            if hit is not None:
                return ("distance_measure", f"MSRD-{hit + 1}")

        # 6c. Angle measurements
        if selectable("angle_measure", ""):
            hit = self._hit_angle_measurement(canvas_pt)
            if hit is not None:
                return ("angle_measure", f"MSRA-{hit + 1}")

        # 7. Heating circuits polygons
        for cid, poly in self._polygons.items():
            if not self._circuit_visible.get(cid, True) or not selectable("polygon", cid):
                continue
            if self._point_in_polygon(canvas_pt, poly):
                return ("polygon", cid)

        # 7b. Electrical room polygons
        for rid, poly in self._elec_room_polygons.items():
            if not self._elec_room_visible.get(rid, True) or not selectable("elec_room", rid):
                continue
            if self._point_in_polygon(canvas_pt, poly):
                return ("elec_room", rid)

        # 8. Floor plan layers (check in reverse render order, front-to-back)
        # Keep this last so background layers don't shadow foreground objects.
        for fid in reversed(self._floor_plan_order):
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            if not selectable("floor_polygon", fid):
                continue
            if layer.polygon:
                poly = self._floor_polygon_world_polygon(fid)
                if poly.containsPoint(canvas_pt, Qt.OddEvenFill):
                    return ("floor_polygon", fid)
            if layer.renderer or layer.pixmap or layer.polygon:
                if layer.polygon:
                    sw, sh = self._floor_polygon_render_size(layer)
                else:
                    sw, sh = self._layer_render_size(layer)
                cx = sw / 2 + layer.offset_x
                cy = sh / 2 + layer.offset_y
                rect = QRectF(cx - sw / 2, cy - sh / 2, sw, sh)
                if rect.contains(canvas_pt):
                    return ("floor_polygon", fid)

        return None

    def _current_edit_target(self) -> Optional[Tuple[str, str]]:
        """Return the currently edited object as (type, id), if any."""
        if self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id:
            return ("elec_cable", self._edit_elec_cable_id)
        if self._mode == ToolMode.EDIT_SUPPLY_LINE and self._edit_supply_cid:
            return ("supply_line", self._edit_supply_cid)
        if self._mode == ToolMode.EDIT_HKV_LINE and self._edit_hkv_line_id:
            return ("hkv_line", self._edit_hkv_line_id)
        if self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid:
            return ("route", self._edit_route_cid)
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_polygon_cid:
            return ("polygon", self._edit_polygon_cid)
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_elec_room_id:
            return ("elec_room", self._edit_elec_room_id)
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_floor_polygon_id:
            return ("floor_polygon", self._edit_floor_polygon_id)
        return None

    def _is_edit_mode_active(self) -> bool:
        return self._current_edit_target() is not None

    def _exit_edit_mode(self):
        """Leave any active edit mode and reset edit-related state."""
        self._edit_elec_cable_id = None
        self._edit_supply_cid = None
        self._edit_hkv_line_id = None
        self._edit_route_cid = None
        self._edit_polygon_cid = None
        self._edit_elec_room_id = None
        self._edit_floor_polygon_id = None
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
        self._dragging_route_point = None
        if self._mode in (
            ToolMode.EDIT_ELEC_CABLE,
            ToolMode.EDIT_SUPPLY_LINE,
            ToolMode.EDIT_HKV_LINE,
            ToolMode.EDIT_ROUTE,
            ToolMode.EDIT_POLYGON,
        ):
            self._mode = ToolMode.NONE
            self.setCursor(Qt.ArrowCursor)
            self.update()

    def _is_object_visible(self, obj_type: str, obj_id: str) -> bool:
        """Check if an object is visible based on its visibility flags."""
        if obj_type == "polygon":
            return self._circuit_visible.get(obj_id, True)
        elif obj_type == "elec_point":
            return self._elec_visible.get(obj_id, True)
        elif obj_type == "elec_room":
            return self._elec_room_visible.get(obj_id, True)
        elif obj_type == "elec_cable":
            return self._elec_visible.get(obj_id, True)
        elif obj_type == "hkv":
            return self._hkv_visible.get(obj_id, True)
        elif obj_type == "hkv_line":
            return self._hkv_line_visible.get(obj_id, True)
        elif obj_type == "text":
            return self._text_visible.get(obj_id, True)
        return True

    def _is_multi_selectable_type(self, obj_type: str) -> bool:
        """Only these object types participate in multi-selection and Alt-drag."""
        return obj_type in {"elec_point", "hkv", "text", "elec_cable"}

    def _get_all_selectable_objects(self) -> List[Tuple[str, str]]:
        """Get list of all selectable (visible) objects on canvas."""
        result = []
        # Electrical points
        for ap_id in self._elec_points.keys():
            if self._is_object_visible("elec_point", ap_id) and self._is_selectable("elec_point", ap_id):
                result.append(("elec_point", ap_id))
        # HKV points
        for hkv_id in self._hkv_points.keys():
            if self._is_object_visible("hkv", hkv_id) and self._is_selectable("hkv", hkv_id):
                result.append(("hkv", hkv_id))
        # Text annotations
        for text_id in self._text_annotations.keys():
            if self._is_object_visible("text", text_id) and self._is_selectable("text", text_id):
                result.append(("text", text_id))
        # Electrical cables
        for cable_id in self._elec_cables.keys():
            if self._is_object_visible("elec_cable", cable_id) and self._is_selectable("elec_cable", cable_id):
                result.append(("elec_cable", cable_id))
        return result

    def _get_multiselect_points_world(self) -> Optional[Tuple[str, List[QPointF]]]:
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_polygon_cid:
            pts = self._polygons.get(self._edit_polygon_cid, [])
            return self._edit_polygon_cid, [QPointF(p) for p in pts]
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_elec_room_id:
            pts = self._elec_room_polygons.get(self._edit_elec_room_id, [])
            return self._edit_elec_room_id, [QPointF(p) for p in pts]
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_floor_polygon_id:
            fid = self._edit_floor_polygon_id
            return fid, self._floor_polygon_points_world(fid)
        if self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid:
            pts = self._manual_routes.get(self._edit_route_cid, [])
            return self._edit_route_cid, [QPointF(p) for p in pts]
        if self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id:
            pts = self._elec_cables.get(self._edit_elec_cable_id, [])
            return self._edit_elec_cable_id, [QPointF(p) for p in pts]
        if self._mode == ToolMode.EDIT_SUPPLY_LINE and self._edit_supply_cid:
            pts = self._supply_lines.get(self._edit_supply_cid, [])
            return self._edit_supply_cid, [QPointF(p) for p in pts]
        if self._mode == ToolMode.EDIT_HKV_LINE and self._edit_hkv_line_id:
            pts = self._hkv_lines.get(self._edit_hkv_line_id, [])
            return self._edit_hkv_line_id, [QPointF(p) for p in pts]
        return None

    def _set_multiselect_point_world(self, owner_id: str, idx: int, world_pt: QPointF):
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_polygon_cid == owner_id:
            if owner_id in self._polygons and 0 <= idx < len(self._polygons[owner_id]):
                self._polygons[owner_id][idx] = QPointF(world_pt)
            return
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_elec_room_id == owner_id:
            if owner_id in self._elec_room_polygons and 0 <= idx < len(self._elec_room_polygons[owner_id]):
                self._elec_room_polygons[owner_id][idx] = QPointF(world_pt)
            return
        if self._mode == ToolMode.EDIT_POLYGON and self._edit_floor_polygon_id == owner_id:
            layer = self._floor_plans.get(owner_id)
            if layer and 0 <= idx < len(layer.polygon):
                layer.polygon[idx] = self._world_to_floor_polygon_local(owner_id, world_pt)
            return
        if self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid == owner_id:
            if owner_id in self._manual_routes and 0 <= idx < len(self._manual_routes[owner_id]):
                self._manual_routes[owner_id][idx] = QPointF(world_pt)
            return
        if self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id == owner_id:
            if owner_id in self._elec_cables and 0 <= idx < len(self._elec_cables[owner_id]):
                self._elec_cables[owner_id][idx] = QPointF(world_pt)
            return
        if self._mode == ToolMode.EDIT_SUPPLY_LINE and self._edit_supply_cid == owner_id:
            if owner_id in self._supply_lines and 0 <= idx < len(self._supply_lines[owner_id]):
                self._supply_lines[owner_id][idx] = QPointF(world_pt)
            return
        if self._mode == ToolMode.EDIT_HKV_LINE and self._edit_hkv_line_id == owner_id:
            if owner_id in self._hkv_lines and 0 <= idx < len(self._hkv_lines[owner_id]):
                self._hkv_lines[owner_id][idx] = QPointF(world_pt)

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
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def update_hkv_size(self, hkv_id: str,
                        width_mm: float, height_mm: float):
        scale = max(self._mm_per_px, 1e-9)
        self._hkv_size_px[hkv_id] = (width_mm / scale, height_mm / scale)
        self.update()

    def set_hkv_icon(self, hkv_id: str, path: str):
        if path and is_svg_asset_ref(path):
            renderer = self._get_cached_svg_renderer(path)
            if renderer is not None:
                self._hkv_svgs[hkv_id] = renderer
                self._hkv_icons[hkv_id] = None
            else:
                self._hkv_svgs[hkv_id] = None
                self._hkv_icons[hkv_id] = None
        elif path:
            pm = self._get_cached_pixmap(path)
            self._hkv_icons[hkv_id] = QPixmap(pm) if pm is not None else None
            self._hkv_svgs[hkv_id] = None
        else:
            self._hkv_icons[hkv_id] = None
            self._hkv_svgs[hkv_id] = None
        self.update()

    def delete_hkv(self, hkv_id: str):
        for d in (self._hkv_points, self._hkv_size_px, self._hkv_icons,
                  self._hkv_svgs, self._hkv_visible,
                  self._label_positions, self._label_font_sizes, self._label_visible,
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
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
        self.update()

    def start_edit_hkv_line(self, line_id: str):
        if line_id not in self._hkv_lines:
            return
        self._edit_hkv_line_id = line_id
        self._edit_selected_owner = None
        self._edit_selected_indices.clear()
        self._edit_selection_rect_start = None
        self._edit_selection_rect_end = None
        self._edit_drag_last_pos = None
        self._mode = ToolMode.EDIT_HKV_LINE
        self.setCursor(Qt.CrossCursor)
        self.update()

    def delete_hkv_line(self, line_id: str):
        for d in (self._hkv_lines, self._hkv_line_start,
                  self._hkv_line_end, self._hkv_line_visible,
                  self._label_positions, self._label_font_sizes, self._label_visible,
                  self._label_rects, self._label_draw_pos):
            d.pop(line_id, None)
        self._hkv_line_path_cache.pop(line_id, None)
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
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for i, pt in enumerate(pts):
            if _qdist(canvas_pt, pt) < threshold:
                return i
        return None

    def _hit_hkv_line_edge(self, canvas_pt: QPointF,
                            line_id: str) -> Optional[Tuple[int, int]]:
        pts = self._hkv_lines.get(line_id, [])
        if len(pts) < 2:
            return None
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
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
        self._ghost_preview_pos = None
        self.setCursor(Qt.CrossCursor)
        self.mode_changed.emit()
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

    def _hit_helper_line(self, canvas_pt: QPointF, radius_px: float = HIT_EDGE_RADIUS_PX) -> Optional[tuple]:
        """
        Prüfe ob ein Punkt eine Hilfslinie trifft.
        
        Returns:
            Tuple (floor_id, helper_id) oder None
        """
        radius_canvas = radius_px / self._scale if self._scale > 0 else radius_px
        
        for fid in self._floor_plan_order:
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            
            helper_lines = self._floor_helper_lines.get(fid, {})
            visible_map = self._floor_helper_line_visible.get(fid, {})
            
            for hid, pts in helper_lines.items():
                if not visible_map.get(hid, True) or len(pts) < 2:
                    continue
                
                # Prüfe Distanz zu Strecke
                p1, p2 = pts[0], pts[1]
                dist = _point_segment_distance(canvas_pt, p1, p2)
                
                if dist < radius_canvas:
                    return (fid, hid)
        
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
        self._floor_polygon_world_cache.clear()
        self._manual_route_path_cache.clear()
        self._supply_line_path_cache.clear()
        self._elec_cable_path_cache.clear()
        self._hkv_line_path_cache.clear()
        self._floor_plan_order.clear()
        self._ref_floor_id = None
        self._polygons.clear()
        self._start_points.clear()
        self._color_map.clear()
        self._label_map.clear()
        self._helper_lines.clear()
        self._show_helper_line.clear()
        self._floor_helper_lines.clear()
        self._floor_helper_line_visible.clear()
        self._floor_helper_settings.clear()
        self._helper_selected_id = None
        self._helper_selected_floor_id = None
        self._helper_active_floor_id = None
        self._helper_dragging_endpoint = None
        self._helper_dragging_whole_id = None
        self._helper_drag_start = None
        self._helper_drag_origin = []
        self._helper_draw_start = None
        self._helper_draw_current = None
        self._helper_line_counter = 0
        self._manual_routes.clear()
        self._route_wall_dist_px.clear()
        self._route_line_dist_px.clear()
        self._circuit_visible.clear()
        self._supply_lines.clear()
        self._elec_points.clear()
        self._elec_room_polygons.clear()
        self._elec_room_visible.clear()
        self._elec_point_size_px.clear()
        self._elec_point_icons.clear()
        self._elec_point_svgs.clear()
        self._elec_point_position.clear()
        self._elec_point_height.clear()
        self._elec_point_notes.clear()
        self._elec_point_smarthome_device.clear()
        self._elec_point_smarthome_device_color.clear()
        self._elec_cables.clear()
        self._elec_cable_notes.clear()
        self._elec_cable_stroke_width.clear()
        self._elec_cable_type_text.clear()
        self._elec_cable_type_label_visible.clear()
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
        self._measure_lines.clear()
        self._measure_label_positions.clear()
        self._measure_p1 = None
        self._measure_p2 = None
        self._angle_measurements.clear()
        self._angle_measure_label_positions.clear()
        self._angle_measure_p1 = None
        self._angle_measure_p2 = None
        self._angle_measure_p3 = None
        self._label_positions.clear()
        self._label_font_sizes.clear()
        self._label_visible.clear()
        self._label_rects.clear()
        self._label_draw_pos.clear()
        self._ref_p1 = None
        self._ref_p2 = None
        self._current_elec_room_id = None
        self._edit_elec_room_id = None
        self._export_frame = None
        self._export_frame_start = None
        self._export_frame_current = None
        self.update()

    def set_selected_item(self, item_id: str):
        """Set the item to highlight in the canvas (from treeview selection)."""
        self._selected_item_id = item_id if item_id else None
        helper_ref = _parse_helper_nav_id(item_id) if item_id else None
        if helper_ref is not None:
            fid, hid = helper_ref
            if hid in self._floor_helper_lines.get(fid, {}):
                self._helper_selected_floor_id = fid
                self._helper_selected_id = hid
                self._selected_item_type = "helper_line"
            else:
                self._selected_item_type = None
            self.update()
            return
        self._helper_selected_id = None
        self._helper_selected_floor_id = None
        # Auto-detect type from the item id
        if item_id:
            if item_id in self._polygons:
                self._selected_item_type = "polygon"
            elif item_id in self._start_points:
                self._selected_item_type = "circuit"
            elif item_id in self._elec_points:
                self._selected_item_type = "elec_point"
            elif item_id in self._elec_room_polygons:
                self._selected_item_type = "elec_room"
            elif item_id in self._elec_cables:
                self._selected_item_type = "elec_cable"
            elif item_id in self._hkv_points:
                self._selected_item_type = "hkv"
            elif item_id in self._hkv_lines:
                self._selected_item_type = "hkv_line"
            elif item_id in self._supply_lines:
                self._selected_item_type = "supply_line"
            elif item_id in self._manual_routes:
                self._selected_item_type = "route"
            elif item_id in self._text_annotations:
                self._selected_item_type = "text"
            elif self._measurement_obj_to_index(item_id, "MSRD") is not None:
                idx = self._measurement_obj_to_index(item_id, "MSRD")
                self._selected_item_type = "distance_measure" if idx is not None and idx < len(self._measure_lines) else None
            elif self._measurement_obj_to_index(item_id, "MSRA") is not None:
                idx = self._measurement_obj_to_index(item_id, "MSRA")
                self._selected_item_type = "angle_measure" if idx is not None and idx < len(self._angle_measurements) else None
            elif item_id in self._floor_plans:
                self._selected_item_type = "floor_polygon"
            else:
                self._selected_item_type = None
        else:
            self._selected_item_type = None
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
            "export_frame": [
                float(self._export_frame.x()),
                float(self._export_frame.y()),
                float(self._export_frame.width()),
                float(self._export_frame.height()),
            ] if self._export_frame else None,
            "measure_color": self._measure_color,
            "measure_label_positions": [list(p) for p in self._measure_label_positions],
            "helper_label_positions": {
                fid: {hid: [float(pos[0]), float(pos[1])] for hid, pos in helper_map.items()}
                for fid, helper_map in self._helper_label_positions.items()
            },
            "helper_line_color": self._helper_line_color,
            "ref_line_colors": dict(self._ref_line_colors),
            "ref_line_visible": dict(self._ref_line_visible),
            "floor_helper_lines": {
                fid: {
                    hid: [(pts[0].x(), pts[0].y()), (pts[1].x(), pts[1].y())]
                    for hid, pts in helper_map.items() if len(pts) >= 2
                }
                for fid, helper_map in self._floor_helper_lines.items()
            },
            "floor_helper_line_visible": {
                fid: dict(visible_map)
                for fid, visible_map in self._floor_helper_line_visible.items()
            },
            "helper_lines_per_floor": {
                fid: {
                    hid: {
                        "points": [(pts[0].x(), pts[0].y()), (pts[1].x(), pts[1].y())],
                        "length_mm": float(self._floor_helper_line_length_mm.get(fid, {}).get(hid, 1000.0)),
                        "length_fixed": bool(self._floor_helper_line_fixed.get(fid, {}).get(hid, False)),
                        "color": str(self._helper_settings(fid).get("color", "#f8f32b")),
                        "visible": bool(self._floor_helper_line_visible.get(fid, {}).get(hid, True)),
                    }
                    for hid, pts in helper_map.items() if len(pts) >= 2
                }
                for fid, helper_map in self._floor_helper_lines.items()
            },
            "floor_helper_settings": {
                fid: {
                    "visible": bool(settings.get("visible", True)),
                    "color": str(settings.get("color", "#f8f32b")),
                    "target_length_mm": float(settings.get("target_length_mm", 1000.0)),
                    "line_width_px": float(settings.get("line_width_px", 2.0)),
                    "line_style": str(settings.get("line_style", "dash")),
                }
                for fid, settings in self._floor_helper_settings.items()
            },
            "helper_lines_per_floor": {
                fid: {
                    hid: {
                        "points": [(pts[0].x(), pts[0].y()), (pts[1].x(), pts[1].y())],
                        "length_mm": float(self.get_helper_line_length_mm(fid, hid)),
                        "length_fixed": bool(self._floor_helper_line_fixed.get(fid, {}).get(hid, False)),
                        "color": str(self._helper_settings(fid).get("color", "#f8f32b")),
                        "visible": bool(self._floor_helper_line_visible.get(fid, {}).get(hid, True)),
                    }
                    for hid, pts in helper_map.items() if len(pts) >= 2
                }
                for fid, helper_map in self._floor_helper_lines.items()
            },
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
            "elec_rooms": {
                rid: [(p.x(), p.y()) for p in pts]
                for rid, pts in self._elec_room_polygons.items()
            },
            "elec_room_visible": dict(self._elec_room_visible),
            "elec_point_size_px": {
                pid: list(s)
                for pid, s in self._elec_point_size_px.items()
            },
            "elec_point_position": dict(self._elec_point_position),
            "elec_point_height": dict(self._elec_point_height),
            "elec_point_notes": dict(self._elec_point_notes),
            "elec_point_smarthome_device": dict(self._elec_point_smarthome_device),
            "elec_point_smarthome_device_color": dict(self._elec_point_smarthome_device_color),
            "elec_cables": {
                cid: [(p.x(), p.y()) for p in pts]
                for cid, pts in self._elec_cables.items()
            },
            "elec_cable_notes": dict(self._elec_cable_notes),
            "elec_cable_stroke_width": dict(self._elec_cable_stroke_width),
            "elec_cable_type_text": dict(self._elec_cable_type_text),
            "elec_cable_type_label_visible": dict(self._elec_cable_type_label_visible),
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
            "label_visible": dict(self._label_visible),
            "text_annotations": {
                tid: {
                    "pos": (pt.x(), pt.y()),
                    "content": self._text_contents.get(tid, ""),
                    "font_size": self._text_font_sizes.get(tid, 14.0),
                    "color": self._text_colors.get(tid, "#ffffff"),
                    "comment": self._text_comments.get(tid, ""),
                    "visible": self._text_visible.get(tid, True),
                }
                for tid, p in self._text_annotations.items()
                for pt in [self._coerce_canvas_point(p)]
                if pt is not None
            },
            "distance_measurements": {
                f"MSRD-{idx + 1}": [(p1.x(), p1.y()), (p2.x(), p2.y())]
                for idx, (p1, p2, _mm_len) in enumerate(self._measure_lines)
            },
            "distance_label_positions": {
                f"MSRD-{idx + 1}": [float(lp[0]), float(lp[1])]
                for idx, lp in enumerate(self._measure_label_positions)
            },
            "angle_measurements": {
                f"MSRA-{idx + 1}": [(p1.x(), p1.y()), (p2.x(), p2.y()), (p3.x(), p3.y())]
                for idx, (p1, p2, p3, _angle_deg) in enumerate(self._angle_measurements)
            },
            "angle_label_positions": {
                f"MSRA-{idx + 1}": [float(lp[0]), float(lp[1])]
                for idx, lp in enumerate(self._angle_measure_label_positions)
            },
        }
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
        
        # Restore export frame
        ef = d.get("export_frame")
        if ef and len(ef) == 4:
            try:
                self._export_frame = QRectF(float(ef[0]), float(ef[1]), float(ef[2]), float(ef[3]))
            except (TypeError, ValueError):
                self._export_frame = None
        else:
            self._export_frame = None
        self._export_frame_start = None
        self._export_frame_current = None
        
        # Restore color settings
        if "measure_color" in d:
            self._measure_color = d["measure_color"]
        if "measure_label_positions" in d:
            try:
                self._measure_label_positions = [ (float(x), float(y)) for x, y in d.get("measure_label_positions", []) ]
            except Exception:
                self._measure_label_positions = []
        if "helper_label_positions" in d:
            try:
                self._helper_label_positions = {
                    fid: {
                        hid: (float(pos[0]), float(pos[1]))
                        for hid, pos in helper_map.items()
                    }
                    for fid, helper_map in d.get("helper_label_positions", {}).items()
                }
            except Exception:
                self._helper_label_positions = {}
        if "helper_line_color" in d:
            self._helper_line_color = str(d["helper_line_color"])
        if "ref_line_colors" in d:
            self._ref_line_colors = dict(d["ref_line_colors"])
        if "ref_line_visible" in d:
            self._ref_line_visible = {
                k: bool(v) for k, v in d["ref_line_visible"].items()
            }

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
        legacy_measure_labels = list(self._measure_label_positions)
        self._measure_lines = []
        self._angle_measurements = []
        self._measure_label_positions = []
        self._angle_measure_label_positions = []

        distance_measurements = d.get("distance_measurements", {})
        for _measurement_id, points in sorted(distance_measurements.items()):
            if not isinstance(points, (list, tuple)) or len(points) < 2:
                continue
            p1 = QPointF(float(points[0][0]), float(points[0][1]))
            p2 = QPointF(float(points[1][0]), float(points[1][1]))
            mm_len = _qdist(p1, p2) * self._mm_per_px if self._mm_per_px > 0 else 0.0
            self._measure_lines.append((p1, p2, mm_len))

        distance_labels = d.get("distance_label_positions", {})
        for _measurement_id, label_pos in sorted(distance_labels.items()):
            if not isinstance(label_pos, (list, tuple)) or len(label_pos) < 2:
                continue
            self._measure_label_positions.append((float(label_pos[0]), float(label_pos[1])))
        if not self._measure_label_positions and legacy_measure_labels:
            self._measure_label_positions = legacy_measure_labels

        angle_measurements = d.get("angle_measurements", {})
        for _measurement_id, points in sorted(angle_measurements.items()):
            if not isinstance(points, (list, tuple)) or len(points) < 3:
                continue
            p1 = QPointF(float(points[0][0]), float(points[0][1]))
            p2 = QPointF(float(points[1][0]), float(points[1][1]))
            p3 = QPointF(float(points[2][0]), float(points[2][1]))
            v1x, v1y = p1.x() - p2.x(), p1.y() - p2.y()
            v2x, v2y = p3.x() - p2.x(), p3.y() - p2.y()
            l1 = math.hypot(v1x, v1y)
            l2 = math.hypot(v2x, v2y)
            angle_deg = 0.0
            if l1 > 1e-9 and l2 > 1e-9:
                dot = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (l1 * l2)))
                angle_deg = math.degrees(math.acos(dot))
            self._angle_measurements.append((p1, p2, p3, angle_deg))

        angle_labels = d.get("angle_label_positions", {})
        for _measurement_id, label_pos in sorted(angle_labels.items()):
            if not isinstance(label_pos, (list, tuple)) or len(label_pos) < 2:
                continue
            self._angle_measure_label_positions.append((float(label_pos[0]), float(label_pos[1])))

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
        for rid, pts in d.get("elec_rooms", {}).items():
            self._elec_room_polygons[rid] = [QPointF(x, y) for x, y in pts]
            self._elec_room_visible.setdefault(rid, True)
            self._ensure_color(rid)
        self._elec_room_visible.update(
            {k: bool(v) for k, v in d.get("elec_room_visible", {}).items()}
        )
        for pid, s in d.get("elec_point_size_px", {}).items():
            self._elec_point_size_px[pid] = tuple(s)
        self._elec_point_position = dict(d.get("elec_point_position", {}))
        self._elec_point_height = {
            pid: float(h) for pid, h in d.get("elec_point_height", {}).items()
        }
        self._elec_point_notes = {
            pid: str(v) for pid, v in d.get("elec_point_notes", {}).items()
        }
        self._elec_point_smarthome_device = {
            pid: str(v) for pid, v in d.get("elec_point_smarthome_device", {}).items()
        }
        self._elec_point_smarthome_device_color = {
            pid: str(v) for pid, v in d.get("elec_point_smarthome_device_color", {}).items()
        }
        for cid, pts in d.get("elec_cables", {}).items():
            self._elec_cables[cid] = [QPointF(x, y) for x, y in pts]
            self._ensure_color(cid)
        self._elec_cable_notes = {
            cid: str(v) for cid, v in d.get("elec_cable_notes", {}).items()
        }
        self._elec_cable_stroke_width = {
            cid: float(v) for cid, v in d.get("elec_cable_stroke_width", {}).items()
        }
        self._elec_cable_type_text = {
            cid: str(v) for cid, v in d.get("elec_cable_type_text", {}).items()
        }
        loaded_type_visibility = d.get("elec_cable_type_label_visible", {})
        self._elec_cable_type_label_visible = {
            cid: bool(loaded_type_visibility.get(cid, False))
            for cid in self._elec_cables.keys()
        }
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
        self._label_visible = {
            k: bool(v) for k, v in d.get("label_visible", {}).items()
        }
        # Text annotations
        for tid, tdata in d.get("text_annotations", {}).items():
            pos = tdata.get("pos", (0, 0))
            self._text_annotations[tid] = QPointF(pos[0], pos[1])
            self._text_contents[tid] = tdata.get("content", "")
            self._text_font_sizes[tid] = tdata.get("font_size", 14.0)
            self._text_colors[tid] = tdata.get("color", "#ffffff")
            self._text_comments[tid] = tdata.get("comment", "")
            self._text_visible[tid] = tdata.get("visible", True)
        self._floor_helper_lines.clear()
        self._floor_helper_line_visible.clear()
        self._floor_helper_line_length_mm.clear()
        self._floor_helper_line_fixed.clear()
        self._floor_helper_settings.clear()
        self._helper_line_counter = 0

        for fid, settings in d.get("floor_helper_settings", {}).items():
            fid_s = str(fid)
            if not isinstance(settings, dict):
                settings = {}
            self._floor_helper_settings[fid_s] = {
                "visible": bool(settings.get("visible", True)),
                "color": str(settings.get("color", self._helper_line_color)),
                "target_length_mm": max(1.0, float(settings.get("target_length_mm", self._helper_target_length_mm))),
                "line_width_px": max(0.5, float(settings.get("line_width_px", 2.0))),
                "line_style": str(settings.get("line_style", "dash")).strip().lower(),
            }

        for fid, helper_map in d.get("floor_helper_lines", {}).items():
            fid_s = str(fid)
            lines: Dict[str, List[QPointF]] = {}
            self._floor_helper_line_length_mm.setdefault(fid_s, {})
            self._floor_helper_line_fixed.setdefault(fid_s, {})
            for hid, pts in (helper_map or {}).items():
                if not isinstance(pts, list) or len(pts) < 2:
                    continue
                p1 = QPointF(float(pts[0][0]), float(pts[0][1]))
                p2 = QPointF(float(pts[1][0]), float(pts[1][1]))
                hid_s = str(hid)
                lines[hid_s] = [p1, p2]
                self._floor_helper_line_length_mm[fid_s][hid_s] = _qdist(p1, p2) * self._mm_per_px if self._mm_per_px > 0 else 0.0
                self._floor_helper_line_fixed[fid_s][hid_s] = False
                try:
                    if hid_s.startswith("HL-"):
                        self._helper_line_counter = max(self._helper_line_counter, int(hid_s.split("-")[1]))
                except (IndexError, ValueError):
                    pass
            self._floor_helper_lines[fid_s] = lines

        for fid, visible_map in d.get("floor_helper_line_visible", {}).items():
            fid_s = str(fid)
            vis_dict: Dict[str, bool] = {}
            for hid, vis in (visible_map or {}).items():
                vis_dict[str(hid)] = bool(vis)
            self._floor_helper_line_visible[fid_s] = vis_dict

        # Legacy migration: global helper lines -> first serialized floor plan
        legacy_lines = d.get("global_helper_lines", {})
        if legacy_lines:
            legacy_floor_id = None
            fp_list = d.get("floor_plans", [])
            if fp_list and isinstance(fp_list, list):
                first_fp = fp_list[0] if fp_list else {}
                if isinstance(first_fp, dict):
                    legacy_floor_id = str(first_fp.get("fp_id", "") or "")
            if not legacy_floor_id:
                legacy_floor_id = "grundriss-1"

            self._floor_helper_lines.setdefault(legacy_floor_id, {})
            self._floor_helper_line_visible.setdefault(legacy_floor_id, {})
            self._floor_helper_line_length_mm.setdefault(legacy_floor_id, {})
            self._floor_helper_line_fixed.setdefault(legacy_floor_id, {})
            self._floor_helper_settings.setdefault(legacy_floor_id, self._default_helper_settings())

            for hid, pts in legacy_lines.items():
                if not isinstance(pts, list) or len(pts) < 2:
                    continue
                hid_s = str(hid)
                if hid_s in self._floor_helper_lines[legacy_floor_id]:
                    continue
                p1 = QPointF(float(pts[0][0]), float(pts[0][1]))
                p2 = QPointF(float(pts[1][0]), float(pts[1][1]))
                self._floor_helper_lines[legacy_floor_id][hid_s] = [p1, p2]
                self._floor_helper_line_visible[legacy_floor_id][hid_s] = True
                self._floor_helper_line_length_mm[legacy_floor_id][hid_s] = _qdist(p1, p2) * self._mm_per_px if self._mm_per_px > 0 else 0.0
                self._floor_helper_line_fixed[legacy_floor_id][hid_s] = False
                try:
                    if hid_s.startswith("HL-"):
                        self._helper_line_counter = max(self._helper_line_counter, int(hid_s.split("-")[1]))
                except (IndexError, ValueError):
                    pass

            legacy_visible = d.get("helper_line_visible", {})
            for hid, vis in legacy_visible.items():
                hid_s = str(hid)
                if hid_s in self._floor_helper_lines[legacy_floor_id]:
                    self._floor_helper_line_visible[legacy_floor_id][hid_s] = bool(vis)
        
        # Load extended helper line metadata from helper_lines_per_floor (neue Struktur)
        for fid, helper_map in d.get("helper_lines_per_floor", {}).items():
            fid_s = str(fid)
            self._floor_helper_lines.setdefault(fid_s, {})
            self._floor_helper_line_visible.setdefault(fid_s, {})
            self._floor_helper_line_length_mm.setdefault(fid_s, {})
            self._floor_helper_line_fixed.setdefault(fid_s, {})
            
            for hid, helper_data in (helper_map or {}).items():
                if not isinstance(helper_data, dict):
                    continue
                
                pts = helper_data.get("points", [])
                if not isinstance(pts, list) or len(pts) < 2:
                    continue
                
                hid_s = str(hid)
                p1 = QPointF(float(pts[0][0]), float(pts[0][1]))
                p2 = QPointF(float(pts[1][0]), float(pts[1][1]))
                
                # Storepoints
                self._floor_helper_lines[fid_s][hid_s] = [p1, p2]
                
                # Store metadata
                self._floor_helper_line_length_mm[fid_s][hid_s] = float(helper_data.get("length_mm", 1000.0))
                self._floor_helper_line_fixed[fid_s][hid_s] = bool(helper_data.get("length_fixed", False))
                self._floor_helper_line_visible[fid_s][hid_s] = bool(helper_data.get("visible", True))
                
                # Update color if provided
                if "color" in helper_data:
                    settings = self._floor_helper_settings.setdefault(fid_s, self._default_helper_settings())
                    settings["color"] = str(helper_data.get("color", "#f8f32b"))
                
                try:
                    if hid_s.startswith("HL-") or hid_s.startswith("helper-"):
                        try:
                            num = int(hid_s.split("-")[1])
                            self._helper_line_counter = max(self._helper_line_counter, num)
                        except (IndexError, ValueError):
                            pass
                except (IndexError, ValueError):
                    pass
        
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
            self._ensure_helper_floor(fid)

        # Ensure visibility map and settings are aligned with existing lines
        for fid, lines in self._floor_helper_lines.items():
            vis_map = self._floor_helper_line_visible.setdefault(fid, {})
            for hid in lines.keys():
                vis_map.setdefault(hid, True)
            self._floor_helper_settings.setdefault(fid, self._default_helper_settings())

        self._helper_selected_id = None
        self._helper_selected_floor_id = None
        self.set_active_helper_floor(self._helper_active_floor_id)
        self.update()

    # ------------------------------------------------------------------ #
    #  Koordinaten                                                         #
    # ------------------------------------------------------------------ #

    def _to_canvas(self, screen: QPointF) -> QPointF:
        return QPointF(
            (screen.x() - self._offset.x()) / self._scale,
            (screen.y() - self._offset.y()) / self._scale,
        )

    def _px_to_canvas_units(self, px: float) -> float:
        return px / max(self._scale, 1e-9)

    def _coerce_canvas_point(self, value) -> Optional[QPointF]:
        if isinstance(value, QPointF):
            return value
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return QPointF(float(value[0]), float(value[1]))
            except (TypeError, ValueError):
                return None
        return None

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
            color_hex = ""
            if self._document:
                element = self._document.get(cid)
                if element and hasattr(element, 'color'):
                    color_hex = str(element.color or "").strip()
            if color_hex and color_hex.startswith("#"):
                self._color_map[cid] = QColor(color_hex)
            else:
                self._color_map[cid] = QColor(COLORS[self._color_index % len(COLORS)])
                self._color_index += 1

    def _hit_start_point(self, canvas_pt: QPointF) -> Optional[str]:
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for cid, sp in self._start_points.items():
            if not self._circuit_visible.get(cid, True):
                continue
            if _qdist(canvas_pt, sp) < threshold:
                return cid
        return None

    def _hit_route_point(self, canvas_pt: QPointF) -> Optional[Tuple[str, int]]:
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for cid, pts in self._manual_routes.items():
            if not self._circuit_visible.get(cid, True):
                continue
            for i, pt in enumerate(pts):
                if _qdist(canvas_pt, pt) < threshold:
                    return cid, i
        return None

    def _hit_global_helper_line_endpoint(self, canvas_pt: QPointF) -> Optional[Tuple[str, int]]:
        fid = self._ensure_helper_floor(self._helper_active_floor_id)
        if not fid:
            return None
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        lines = self._floor_helper_lines.get(fid, {})
        visible = self._floor_helper_line_visible.get(fid, {})
        for hid, pts in lines.items():
            if not visible.get(hid, True) or len(pts) < 2:
                continue
            if _qdist(canvas_pt, pts[0]) < threshold:
                return hid, 0
            if _qdist(canvas_pt, pts[1]) < threshold:
                return hid, 1
        return None

    def _hit_any_helper_line_endpoint(self, canvas_pt: QPointF) -> Optional[Tuple[str, str, int]]:
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for fid in self._floor_plan_order:
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            lines = self._floor_helper_lines.get(fid, {})
            visible = self._floor_helper_line_visible.get(fid, {})
            for hid, pts in lines.items():
                if not visible.get(hid, True) or len(pts) < 2:
                    continue
                if _qdist(canvas_pt, pts[0]) < threshold:
                    return fid, hid, 0
                if _qdist(canvas_pt, pts[1]) < threshold:
                    return fid, hid, 1
        return None

    def _hit_global_helper_line(self, canvas_pt: QPointF) -> Optional[str]:
        fid = self._ensure_helper_floor(self._helper_active_floor_id)
        if not fid:
            return None
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
        lines = self._floor_helper_lines.get(fid, {})
        visible = self._floor_helper_line_visible.get(fid, {})
        for hid, pts in lines.items():
            if not visible.get(hid, True) or len(pts) < 2:
                continue
            if _point_segment_distance(canvas_pt, pts[0], pts[1]) <= threshold:
                return hid
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

        # Check inter-segment distance based on the configured loop spacing.
        # line_dist is the Vorlauf↔Ruecklauf distance of one loop.
        # For route-center lines this results in a required half-band of
        # (line_dist / 2 + line_dist) on each side.
        if line_dist <= 0.0:
            return None

        min_center_dist = 1.5 * line_dist
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
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)

        # In Draw-Polygon mode: double-click on last point finishes polygon
        if self._mode == ToolMode.DRAW_POLY and self._current_points:
            last_pt = self._current_points[-1]
            if _qdist(canvas_pt, last_pt) < threshold:
                if len(self._current_points) >= 3:
                    pts = [(p.x(), p.y()) for p in self._current_points]
                    if self._current_elec_room_id:
                        rid = self._current_elec_room_id
                        self._elec_room_polygons[rid] = list(self._current_points)
                        self._elec_room_visible.setdefault(rid, True)
                        self.elec_room_polygon_finished.emit(rid, pts)
                    elif self._current_circuit_id:
                        self._polygons[self._current_circuit_id] = list(self._current_points)
                        self._start_points[self._current_circuit_id] = self._current_points[0]
                        self.polygon_finished.emit(self._current_circuit_id, pts)
                self._mode = ToolMode.NONE
                self._current_points = []
                self._current_elec_room_id = None
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
            return

        # In Draw-Furniture-Polygon mode: double-click on last point finishes polygon
        if self._mode == ToolMode.DRAW_FURNITURE_POLY and self._current_points:
            last_pt = self._current_points[-1]
            if _qdist(canvas_pt, last_pt) < threshold:
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
                        layer.polygon = [QPointF(p.x() - min_x, p.y() - min_y) for p in self._current_points]
                        pts = [(p.x(), p.y()) for p in self._current_points]
                        self.floor_plan_polygon_finished.emit(self._current_furniture_id, pts)
                self._mode = ToolMode.NONE
                self._current_furniture_id = None
                self._current_points = []
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
            return

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
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if len(self._supply_lines.get(cid, [])) >= 2:
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
                self._drawing_cable_from_start = False
                self._current_elec_cable_preview = None
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if len(self._elec_cables.get(cid, [])) >= 2:
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
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if len(self._hkv_lines.get(lid, [])) >= 2:
                    self.hkv_line_changed.emit(lid)
                self.update()
            return

        # In Draw-Route mode: double-click on last point finishes the route
        if self._mode == ToolMode.DRAW_ROUTE and self._current_route_cid and self._current_route_points:
            last_pt = self._current_route_points[-1]
            if _qdist(canvas_pt, last_pt) < threshold:
                cid = self._current_route_cid
                if len(self._current_route_points) >= 2:
                    self._manual_routes[cid] = list(self._current_route_points)
                self._current_route_cid = None
                self._current_route_points = []
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if len(self._manual_routes.get(cid, [])) >= 2:
                    self.route_changed.emit(cid)
                self.update()
            return

        # In Edit-Route mode: snap route point on double-click
        if self._mode == ToolMode.EDIT_ROUTE and self._edit_route_cid:
            hit = self._hit_route_point_in_circuit(canvas_pt, self._edit_route_cid)
            if hit is not None:
                self._snap_route_point_to_valid(self._edit_route_cid, hit)
                return
            edge_hit = self._hit_route_edge(canvas_pt, self._edit_route_cid)
            if edge_hit is not None:
                idx1, idx2 = edge_hit
                pts = self._manual_routes[self._edit_route_cid]
                p1 = pts[idx1]
                p2 = pts[idx2]
                midpt = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                self._insert_route_point(self._edit_route_cid, idx1, idx2, midpt)
            return

        if self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id:
            cid = self._edit_elec_cable_id
            hit = self._hit_elec_cable_point(canvas_pt, cid)
            if hit is not None:
                pts = self._elec_cables.get(cid, [])
                if len(pts) > 2:
                    del pts[hit]
                    self.elec_cable_changed.emit(cid)
                    self.update()
                return
            edge_hit = self._hit_elec_cable_edge(canvas_pt, cid)
            if edge_hit is not None:
                idx1, idx2 = edge_hit
                pts = self._elec_cables[cid]
                p1, p2 = pts[idx1], pts[idx2]
                mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                pts.insert(idx2, mid)
                self.elec_cable_changed.emit(cid)
                self.update()
            return

        if self._mode == ToolMode.EDIT_SUPPLY_LINE and self._edit_supply_cid:
            cid = self._edit_supply_cid
            hit = self._hit_supply_line_point(canvas_pt, cid)
            if hit is not None:
                pts = self._supply_lines.get(cid, [])
                if len(pts) > 2:
                    del pts[hit]
                    self.supply_line_changed.emit(cid)
                    self.update()
                return
            edge_hit = self._hit_supply_line_edge(canvas_pt, cid)
            if edge_hit is not None:
                idx1, idx2 = edge_hit
                pts = self._supply_lines[cid]
                p1, p2 = pts[idx1], pts[idx2]
                mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                pts.insert(idx2, mid)
                self.supply_line_changed.emit(cid)
                self.update()
            return

        if self._mode == ToolMode.EDIT_HKV_LINE and self._edit_hkv_line_id:
            lid = self._edit_hkv_line_id
            hit = self._hit_hkv_line_point(canvas_pt, lid)
            if hit is not None:
                pts = self._hkv_lines.get(lid, [])
                if len(pts) > 2:
                    del pts[hit]
                    self.hkv_line_changed.emit(lid)
                    self.update()
                return
            edge_hit = self._hit_hkv_line_edge(canvas_pt, lid)
            if edge_hit is not None:
                idx1, idx2 = edge_hit
                pts = self._hkv_lines[lid]
                p1, p2 = pts[idx1], pts[idx2]
                mid = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                pts.insert(idx2, mid)
                self.hkv_line_changed.emit(lid)
                self.update()
            return

        if self._mode == ToolMode.EDIT_POLYGON and self._edit_polygon_cid:
            cid = self._edit_polygon_cid
            hit = self._hit_polygon_point(canvas_pt, cid)
            if hit is not None:
                self._delete_polygon_point(cid, hit)
                return
            edge_hit = self._hit_polygon_edge(canvas_pt, cid)
            if edge_hit is not None:
                idx1, idx2 = edge_hit
                p1 = self._polygons[cid][idx1]
                p2 = self._polygons[cid][idx2]
                midpt = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                self._insert_polygon_point(cid, idx1, idx2, midpt)
            return

        if self._mode == ToolMode.EDIT_POLYGON and self._edit_elec_room_id:
            rid = self._edit_elec_room_id
            hit = self._hit_polygon_point(canvas_pt, rid)
            if hit is not None:
                self._delete_polygon_point(rid, hit)
                return
            edge_hit = self._hit_polygon_edge(canvas_pt, rid)
            if edge_hit is not None:
                idx1, idx2 = edge_hit
                p1 = self._elec_room_polygons[rid][idx1]
                p2 = self._elec_room_polygons[rid][idx2]
                midpt = QPointF((p1.x() + p2.x()) * 0.5, (p1.y() + p2.y()) * 0.5)
                self._insert_polygon_point(rid, idx1, idx2, midpt)
            return

        if self._mode == ToolMode.EDIT_POLYGON and self._edit_floor_polygon_id:
            fid = self._edit_floor_polygon_id
            hit = self._hit_floor_polygon_point(canvas_pt, fid)
            if hit is not None:
                self._delete_floor_polygon_point(fid, hit)
                return
            edge_hit = self._hit_floor_polygon_edge(canvas_pt, fid)
            if edge_hit is not None:
                idx1, idx2 = edge_hit
                self._insert_floor_polygon_point(fid, idx1, idx2, canvas_pt)
            return

        # Outside NONE mode: snap any route point or ignore
        if self._mode != ToolMode.NONE:
            route_hit = self._hit_route_point(canvas_pt)
            if route_hit:
                self._snap_route_point_to_valid(route_hit[0], route_hit[1])
            return
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)

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

        # 3. Elektro-Kabel – Doppelklick auf Anfang oder Ende → Zeichenmodus fortsetzen
        for kid, pts in self._elec_cables.items():
            if not self._elec_visible.get(kid, True):
                continue
            if len(pts) >= 2:
                # Last point hit → resume drawing from the end
                if _qdist(canvas_pt, pts[-1]) < threshold:
                    self._current_elec_cable_points = list(pts)
                    self._current_elec_cable_id = kid
                    self._drawing_cable_from_start = False
                    self._mode = ToolMode.DRAW_ELEC_CABLE
                    self._current_elec_cable_preview = None
                    self.setCursor(Qt.CrossCursor)
                    self.mode_changed.emit()
                    self.update()
                    return
                # First point hit → resume drawing from the start
                if _qdist(canvas_pt, pts[0]) < threshold:
                    self._current_elec_cable_points = list(reversed(pts))
                    self._current_elec_cable_id = kid
                    self._drawing_cable_from_start = True
                    self._mode = ToolMode.DRAW_ELEC_CABLE
                    self._current_elec_cable_preview = None
                    self.setCursor(Qt.CrossCursor)
                    self.mode_changed.emit()
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
                    self.mode_changed.emit()
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
                    self.mode_changed.emit()
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
                    self.mode_changed.emit()
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
            poly = self._floor_polygon_world_polygon(fid)
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

        # 9. Elektro-Raum Polygon (point inside)
        for rid, poly in self._elec_room_polygons.items():
            if not self._elec_room_visible.get(rid, True):
                continue
            if self._point_in_polygon(canvas_pt, poly):
                self.object_double_clicked.emit("elec_room", rid)
                return

    def mousePressEvent(self, event):
        pos       = QPointF(event.position())
        canvas_pt = self._to_canvas(pos)

        if event.button() == Qt.MiddleButton:
            self._pan_start = pos
            self._panning   = True
            return

        if event.button() == Qt.RightButton:
            obj = self._hit_any_object(canvas_pt)
            if not obj:
                text_hit = self._hit_text_annotation(canvas_pt)
                if text_hit:
                    obj = ("text", text_hit)
            if not obj:
                label_hit = self._hit_label(canvas_pt)
                if label_hit:
                    obj = ("label", label_hit)
            if not obj:
                helper_hit = self._hit_helper_line(canvas_pt)
                if helper_hit:
                    fid, hid = helper_hit
                    obj = ("helper_line", hid)
                    self._helper_selected_id = hid
                    self._helper_selected_floor_id = fid
                    self.update()
            obj_type = obj[0] if obj else ""
            obj_id = obj[1] if obj else ""
            if obj_id:
                self.object_clicked.emit(obj_type, obj_id)
            self.context_menu_requested.emit(obj_type, obj_id, QPointF(canvas_pt), event.globalPosition())
            return

        # ──── Shift+Click: Toggle multi-selection (BEFORE all mode checks) ────
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier and self._mode == ToolMode.NONE:
            # APs should be easy to add/remove with Shift, even near overlapping cables.
            obj = None
            ap_hit = self._find_nearest_ap(canvas_pt, threshold_px=22.0)
            if ap_hit:
                obj = ("elec_point", ap_hit)
            else:
                obj = self._hit_any_object(canvas_pt)
                if not obj:
                    text_hit = self._hit_text_annotation(canvas_pt)
                    if text_hit:
                        obj = ("text", text_hit)
            if obj:
                obj_type, obj_id = obj
                if (
                    self._is_multi_selectable_type(obj_type)
                    and self._is_object_visible(obj_type, obj_id)
                    and self._is_selectable(obj_type, obj_id)
                ):
                    item = (obj_type, obj_id)
                    if item in self._multi_selected:
                        self._multi_selected.discard(item)
                    else:
                        self._multi_selected.add(item)
                    self.multi_selection_changed.emit(self._multi_selected.copy())
                    self.update()
            return

        # ──── Ctrl+Drag: Box-selection (BEFORE all mode checks) ────
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier and self._mode == ToolMode.NONE:
            self._is_selecting_by_drag = True
            self._selection_start = canvas_pt
            self._selection_rect = None
            self.setCursor(Qt.CrossCursor)
            self.update()
            return

        # ── Multi-Select & Multi-Move: Alt+Drag ──
        if event.button() == Qt.LeftButton and self._mode == ToolMode.NONE:
            # Alt+Drag: Move multi-selected objects
            if event.modifiers() & Qt.AltModifier:
                obj = None
                ap_hit = self._find_nearest_ap(canvas_pt, threshold_px=22.0)
                if ap_hit:
                    obj = ("elec_point", ap_hit)
                else:
                    obj = self._hit_any_object(canvas_pt)
                    if not obj:
                        text_hit = self._hit_text_annotation(canvas_pt)
                        if text_hit:
                            obj = ("text", text_hit)
                if obj:
                    obj_type, obj_id = obj
                    if (
                        not self._is_multi_selectable_type(obj_type)
                        or not self._is_selectable(obj_type, obj_id)
                    ):
                        return
                    item = (obj_type, obj_id)
                    # If clicked object is in multi-selection, move all selected
                    if item in self._multi_selected:
                        self._dragging_multi = self._multi_selected.copy()
                    # Otherwise move only the clicked object
                    else:
                        self._dragging_multi = {item}
                    
                    if self._dragging_multi:
                        self._drag_multi_anchor = canvas_pt
                        self._drag_multi_start_positions.clear()
                        # Store start positions for all selected objects
                        for sel_type, sel_id in self._dragging_multi:
                            if sel_type == "elec_point":
                                if sel_id in self._elec_points:
                                    self._drag_multi_start_positions[(sel_type, sel_id)] = QPointF(self._elec_points[sel_id])
                            elif sel_type == "hkv":
                                if sel_id in self._hkv_points:
                                    self._drag_multi_start_positions[(sel_type, sel_id)] = QPointF(self._hkv_points[sel_id])
                            elif sel_type == "text":
                                if sel_id in self._text_annotations:
                                    pos = self._coerce_canvas_point(self._text_annotations[sel_id])
                                    if pos is not None:
                                        self._text_annotations[sel_id] = pos
                                        self._drag_multi_start_positions[(sel_type, sel_id)] = QPointF(pos)
                            elif sel_type == "elec_cable":
                                if sel_id in self._elec_cables:
                                    pts = [QPointF(p) for p in self._elec_cables[sel_id]]
                                    self._drag_multi_start_positions[(sel_type, sel_id)] = pts
                        self.will_move_multi_objects.emit()
                        self.setCursor(Qt.ClosedHandCursor)
                        self.update()
                        return

        # ── EARLY CHECK: Click on any elec cable point to drag it directly ──
        # This allows quick editing without needing to enter edit mode first
        if event.button() == Qt.LeftButton and self._mode == ToolMode.NONE:
            # APs have priority over overlapping cable geometry.
            # If an AP is hit, let the AP handling below take over.
            prioritize_ap = False
            ap_hit = self._hit_elec_point(canvas_pt)
            if ap_hit and self._is_selectable("elec_point", ap_hit):
                prioritize_ap = True

            # Check all cables for a point hit.
            # Skip endpoints that are anchored to an AP – those positions are
            # "owned" by the AP and should be handled by the AP drag logic below.
            if not prioritize_ap:
                for cid, pts in self._elec_cables.items():
                    if not self._elec_visible.get(cid, True) or not self._is_selectable("elec_cable", cid):
                        continue
                    threshold = self._px_to_canvas_units(HIT_CABLE_POINT_RADIUS_PX)
                    start_ap = self._cable_start_ap.get(cid, "")
                    end_ap   = self._cable_end_ap.get(cid, "")
                    last_idx = len(pts) - 1
                    for i, pt in enumerate(pts):
                        # Skip AP-anchored endpoints so the AP can be dragged instead
                        if i == 0 and start_ap and start_ap in self._elec_points:
                            continue
                        if i == last_idx and end_ap and end_ap in self._elec_points:
                            continue
                        if _qdist(canvas_pt, pt) < threshold:
                            # Found a cable point - start dragging it
                            self._dragging_route_point = (cid, i)
                            self.setCursor(Qt.ClosedHandCursor)
                            self.update()
                            return

            # Check cable edges for whole-cable drag
            if not prioritize_ap:
                for cid, pts in self._elec_cables.items():
                    if (
                        not self._elec_visible.get(cid, True)
                        or len(pts) < 2
                        or not self._is_selectable("elec_cable", cid)
                    ):
                        continue
                    if self._hit_elec_cable_edge(canvas_pt, cid) is None:
                        continue
                    self.object_clicked.emit("elec_cable", cid)
                    self._dragging_elec_cable_id = cid
                    self._dragging_elec_cable_start = QPointF(canvas_pt)
                    self._dragging_elec_cable_origin = [QPointF(p) for p in pts]
                    self._dragging_elec_cable_fixed_indices = set()
                    start_ap = self._cable_start_ap.get(cid)
                    end_ap = self._cable_end_ap.get(cid)
                    if start_ap and start_ap in self._elec_points:
                        self._dragging_elec_cable_fixed_indices.add(0)
                    if end_ap and end_ap in self._elec_points:
                        self._dragging_elec_cable_fixed_indices.add(len(pts) - 1)
                    self.setCursor(Qt.ClosedHandCursor)
                    self.update()
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
                snapped_now = self._snap_measure_point(canvas_pt)
                # Keep preview and commit source in sync with the click event.
                # This prevents stale mouse-move state from influencing finalize.
                self._mouse_pos = QPointF(snapped_now)
                if self._measure_p1 is None:
                    self._measure_p1 = snapped_now
                    self._measure_p2 = None
                else:
                    snapped_click = snapped_now
                    preview_pt = QPointF(self._mouse_pos) if self._mouse_pos is not None else None
                    if preview_pt is None:
                        self._measure_p2 = snapped_click
                        source = "click"
                    else:
                        # Use preview point only if it matches the current click
                        # position within a small tolerance; this avoids stale
                        # mouse-move state causing wrong finalize points.
                        tol = self._px_to_canvas_units(2.0)
                        if _qdist(preview_pt, snapped_click) <= tol:
                            self._measure_p2 = preview_pt
                            source = "preview"
                        else:
                            self._measure_p2 = snapped_click
                            source = "click-fallback"
                    # Save measurement and start next one
                    if self._mm_per_px > 0:
                        px_len = _qdist(self._measure_p1, self._measure_p2)
                        if px_len <= 1e-6:
                            self._debug_measure_pos(
                                "DROP-ZERO",
                                p1=f"({self._measure_p1.x():.2f},{self._measure_p1.y():.2f})",
                                p2=f"({self._measure_p2.x():.2f},{self._measure_p2.y():.2f})",
                                source=source,
                            )
                            self._measure_p1 = None
                            self._measure_p2 = None
                            self.update()
                            return
                        mm_len = px_len * self._mm_per_px
                        self._measure_lines.append(
                            (QPointF(self._measure_p1),
                             QPointF(self._measure_p2), mm_len))
                        line_idx = len(self._measure_lines) - 1
                        self._normalize_measure_label_positions()
                        # Place label exactly at the second point (p2).
                        anchor_x = self._measure_p2.x()
                        anchor_y = self._measure_p2.y()
                        if line_idx < len(self._measure_label_positions):
                            self._measure_label_positions[line_idx] = (float(anchor_x), float(anchor_y))
                        else:
                            self._measure_label_positions.append((float(anchor_x), float(anchor_y)))
                        self._debug_measure_last_store_idx = line_idx
                        self._debug_measure_pos(
                            "STORE",
                            idx=line_idx,
                            lines=len(self._measure_lines),
                            labels=len(self._measure_label_positions),
                            p1=f"({self._measure_p1.x():.2f},{self._measure_p1.y():.2f})",
                            p2=f"({self._measure_p2.x():.2f},{self._measure_p2.y():.2f})",
                            clicked=f"({snapped_click.x():.2f},{snapped_click.y():.2f})",
                            preview=f"({preview_pt.x():.2f},{preview_pt.y():.2f})" if preview_pt is not None else "None",
                            label=f"({anchor_x:.2f},{anchor_y:.2f})",
                            source=source,
                            scale=f"{self._scale:.4f}",
                        )
                        self.measure_changed.emit()
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
                    self._ghost_preview_pos = None
                    self.setCursor(Qt.ArrowCursor)
                    self.mode_changed.emit()
                    self.update()
            return

        # ── Winkel messen ──
        if self._mode == ToolMode.MEASURE_ANGLE:
            if event.button() == Qt.LeftButton:
                snapped_pt = self._snap_measure_point(canvas_pt)
                if self._angle_measure_p1 is None:
                    self._angle_measure_p1 = snapped_pt
                elif self._angle_measure_p2 is None:
                    self._angle_measure_p2 = snapped_pt
                else:
                    self._angle_measure_p3 = snapped_pt
                    p1 = self._angle_measure_p1
                    p2 = self._angle_measure_p2
                    p3 = self._angle_measure_p3
                    v1x, v1y = p1.x() - p2.x(), p1.y() - p2.y()
                    v2x, v2y = p3.x() - p2.x(), p3.y() - p2.y()
                    l1 = math.hypot(v1x, v1y)
                    l2 = math.hypot(v2x, v2y)
                    if l1 > 1e-9 and l2 > 1e-9:
                        dot = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (l1 * l2)))
                        angle_deg = math.degrees(math.acos(dot))
                        self._angle_measurements.append((QPointF(p1), QPointF(p2), QPointF(p3), angle_deg))
                        anchor_x = p3.x() + 10.0 / max(self._scale, 1e-9)
                        anchor_y = p3.y() - 6.0 / max(self._scale, 1e-9)
                        self._angle_measure_label_positions.append((float(anchor_x), float(anchor_y)))
                        self.measure_changed.emit()
                    self._angle_measure_p1 = None
                    self._angle_measure_p2 = None
                    self._angle_measure_p3 = None
                self.update()
            elif event.button() == Qt.RightButton:
                if self._angle_measure_p2 is not None:
                    self._angle_measure_p2 = None
                    self._angle_measure_p3 = None
                elif self._angle_measure_p1 is not None:
                    self._angle_measure_p1 = None
                else:
                    self._mode = ToolMode.NONE
                    self.setCursor(Qt.ArrowCursor)
                    self.mode_changed.emit()
                self.update()
            return

        # ── Hilfslinie zeichnen ──
        if self._mode == ToolMode.DRAW_HELPER_LINE:
            fid = self._resolve_draw_helper_floor(self._helper_active_floor_id)
            if not fid:
                return
            if self._helper_active_floor_id != fid:
                self._helper_active_floor_id = fid
            settings = self._helper_settings(fid)
            if not bool(settings.get("visible", True)):
                settings["visible"] = True
            if event.button() == Qt.LeftButton:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                snapped_pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                if self._helper_draw_start is None:
                    self._helper_draw_start = QPointF(snapped_pt)
                    self._helper_draw_current = QPointF(snapped_pt)
                else:
                    start = self._helper_draw_start
                    end = self._helper_draw_current or snapped_pt
                    dx = end.x() - start.x()
                    dy = end.y() - start.y()
                    direction_len = math.hypot(dx, dy)
                    if direction_len > 1e-9 and self._mm_per_px > 0:
                        target_length_mm = float(settings.get("target_length_mm", self._helper_target_length_mm))
                        target_px = target_length_mm / self._mm_per_px
                        ux = dx / direction_len
                        uy = dy / direction_len
                        final_end = QPointF(start.x() + ux * target_px,
                                            start.y() + uy * target_px)
                        hid = self._next_helper_line_id()
                        self._floor_helper_lines.setdefault(fid, {})[hid] = [QPointF(start), final_end]
                        self._floor_helper_line_visible.setdefault(fid, {})[hid] = True
                        self._floor_helper_line_length_mm.setdefault(fid, {})[hid] = float(target_length_mm)
                        self._floor_helper_line_fixed.setdefault(fid, {})[hid] = False
                        self._helper_selected_id = hid
                        self._helper_selected_floor_id = fid
                        self.helper_lines_changed.emit()
                    self._helper_draw_start = None
                    self._helper_draw_current = None
                self.update()
            elif event.button() == Qt.RightButton:
                self._helper_draw_start = None
                self._helper_draw_current = None
                self.update()
            return

        # ── Hilfslinien bearbeiten ──
        if self._mode == ToolMode.EDIT_HELPER_LINE:
            fid = self._ensure_helper_floor(self._helper_active_floor_id)
            if not fid:
                return
            if event.button() == Qt.LeftButton:
                endpoint_hit = self._hit_global_helper_line_endpoint(canvas_pt)
                if endpoint_hit:
                    hid, idx = endpoint_hit
                    self._helper_selected_id = hid
                    self._helper_selected_floor_id = fid
                    self._helper_dragging_endpoint = (hid, idx)
                    self.setCursor(Qt.ClosedHandCursor)
                    self.update()
                    return
                line_hit = self._hit_global_helper_line(canvas_pt)
                if line_hit:
                    self._helper_selected_id = line_hit
                    self._helper_selected_floor_id = fid
                    self._helper_dragging_whole_id = line_hit
                    self._helper_drag_start = canvas_pt
                    pts = self._floor_helper_lines.get(fid, {}).get(line_hit, [])
                    self._helper_drag_origin = [QPointF(p) for p in pts]
                    self.setCursor(Qt.ClosedHandCursor)
                    self.update()
                    return
                self._helper_selected_id = None
                self._helper_selected_floor_id = None
                self.update()
            elif event.button() == Qt.RightButton:
                line_hit = self._hit_global_helper_line(canvas_pt)
                if line_hit:
                    self._floor_helper_lines.get(fid, {}).pop(line_hit, None)
                    self._floor_helper_line_visible.get(fid, {}).pop(line_hit, None)
                    self._floor_helper_line_length_mm.get(fid, {}).pop(line_hit, None)
                    self._floor_helper_line_fixed.get(fid, {}).pop(line_hit, None)
                    if self._helper_selected_id == line_hit:
                        self._helper_selected_id = None
                        self._helper_selected_floor_id = None
                    self.helper_lines_changed.emit()
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
                    pts = [(p.x(), p.y()) for p in self._current_points]
                    if self._current_elec_room_id:
                        rid = self._current_elec_room_id
                        self._elec_room_polygons[rid] = list(self._current_points)
                        self._elec_room_visible.setdefault(rid, True)
                        self.elec_room_polygon_finished.emit(rid, pts)
                    elif self._current_circuit_id:
                        self._polygons[self._current_circuit_id] = \
                            list(self._current_points)
                        self._start_points[self._current_circuit_id] = \
                            self._current_points[0]
                        self.polygon_finished.emit(self._current_circuit_id, pts)
                self._mode = ToolMode.NONE
                self._current_points = []
                self._current_elec_room_id = None
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
                if len(self._current_route_points) >= 2:
                    self._manual_routes[cid] = list(self._current_route_points)
                self._current_route_cid = None
                self._current_route_points = []
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if len(self._manual_routes.get(cid, [])) >= 2:
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
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
                    # First point → start AP (only if drawing from end)
                    if len(self._current_elec_cable_points) == 1 and not self._drawing_cable_from_start:
                        self._cable_start_ap[self._current_elec_cable_id] = ap
                    # First point when drawing from start → this is new first point (becomes end AP)
                    elif len(self._current_elec_cable_points) == 1 and self._drawing_cable_from_start:
                        pass  # Will be handled at right-click
                self._current_elec_cable_points.append(snapped)
                self._current_elec_cable_preview = None
                self.update()
            elif event.button() == Qt.RightButton and self._current_elec_cable_id:
                cid = self._current_elec_cable_id
                has_geometry = False
                if len(self._current_elec_cable_points) >= 2:
                    # If we were drawing from the start, reverse the points back to normal order
                    if self._drawing_cable_from_start:
                        self._current_elec_cable_points = list(reversed(self._current_elec_cable_points))
                        
                        # The first point of reversed list is the new start AP
                        first_pt = self._current_elec_cable_points[0]
                        start_ap = self._find_nearest_ap(first_pt)
                        if start_ap:
                            self._current_elec_cable_points[0] = QPointF(self._elec_points[start_ap])
                            self._cable_start_ap[cid] = start_ap
                        else:
                            self._cable_start_ap.pop(cid, None)
                    
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
                    has_geometry = True
                else:
                    self._cable_start_ap.pop(cid, None)
                    self._cable_end_ap.pop(cid, None)
                self._current_elec_cable_id = None
                self._current_elec_cable_points = []
                self._drawing_cable_from_start = False
                self._current_elec_cable_preview = None
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if has_geometry:
                    self.elec_cable_changed.emit(cid)
                self.update()
            return

        # ── Edit Elec Cable: handle point dragging ──
        if self._mode == ToolMode.EDIT_ELEC_CABLE and self._edit_elec_cable_id:
            cid = self._edit_elec_cable_id
            if event.button() == Qt.LeftButton:
                hit = self._hit_elec_cable_point(canvas_pt, cid)
                if hit is not None:
                    self._dragging_route_point = (cid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                    self.update()
                    return
                # No point hit: check if another object was clicked to switch editing
                clicked_obj = self._hit_any_object(canvas_pt)
                if clicked_obj and clicked_obj != ("elec_cable", cid):
                    self._exit_edit_mode()
                    self.object_clicked.emit(clicked_obj[0], clicked_obj[1])
                    self.object_switched_from_edit.emit(clicked_obj[0], clicked_obj[1])
                    return
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_elec_cable_id = None
                self._dragging_route_point = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
                return
            # Consume any other button event while in edit mode
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
                has_geometry = False
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
                    has_geometry = True
                self._current_supply_cid = None
                self._current_supply_points = []
                self._current_supply_preview = None
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if has_geometry:
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
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_supply_cid = None
                self._dragging_route_point = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
                has_geometry = False
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
                    has_geometry = True
                else:
                    self._hkv_line_start.pop(lid, None)
                    self._hkv_line_end.pop(lid, None)
                self._current_hkv_line_id = None
                self._current_hkv_line_points = []
                self._current_hkv_line_preview = None
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if has_geometry:
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
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_hkv_line_id = None
                self._dragging_route_point = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_polygon_cid = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
                return

        if self._mode == ToolMode.EDIT_POLYGON and self._edit_elec_room_id:
            rid = self._edit_elec_room_id
            if event.button() == Qt.LeftButton:
                hit = self._hit_polygon_point(canvas_pt, rid)
                if hit is not None:
                    self._dragging_route_point = (rid, hit)
                    self.setCursor(Qt.ClosedHandCursor)
                return
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_elec_room_id = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_floor_polygon_id = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
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
            elif event.button() == Qt.MiddleButton:
                self._mode = ToolMode.NONE
                self._edit_route_cid = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
                return

        # ── Text-Annotation verschieben ──
        if event.button() == Qt.LeftButton and self._mode == ToolMode.NONE:
            text_hit = self._hit_text_annotation(canvas_pt)
            if text_hit:
                self.object_clicked.emit("text", text_hit)
                self._dragging_text = text_hit
                self.setCursor(Qt.ClosedHandCursor)
                return

        # ── Label verschieben ──
        if event.button() == Qt.LeftButton and self._mode == ToolMode.NONE:
            label_hit = self._hit_label(canvas_pt)
            if label_hit:
                self.object_clicked.emit("label", label_hit)
                draw_pos = self._label_draw_pos.get(label_hit, canvas_pt)
                self._label_drag_offset = QPointF(
                    canvas_pt.x() - draw_pos.x(),
                    canvas_pt.y() - draw_pos.y())
                self._dragging_label = label_hit
                self.setCursor(Qt.ClosedHandCursor)
                return

        # ── Startpunkt verschieben ──
        if event.button() == Qt.LeftButton:
            if self._mode == ToolMode.NONE:
                helper_ep_hit = self._hit_any_helper_line_endpoint(canvas_pt)
                if helper_ep_hit:
                    fid, hid, idx = helper_ep_hit
                    self._helper_selected_floor_id = fid
                    self._helper_selected_id = hid
                    self._helper_dragging_endpoint = (hid, idx)
                    self.setCursor(Qt.ClosedHandCursor)
                    self.update()
                    return

            # Treffer werden gegen die aktiven Workspace-Layer gefiltert.
            # Bei gesperrtem Treffer NICHT abbrechen, sondern zum nächsten
            # Hit-Test durchfallen – so bleibt ein dahinterliegendes, erlaubtes
            # Objekt greifbar (gleiches Verhalten wie in _hit_any_object).
            route_hit = self._hit_route_point(canvas_pt)
            if route_hit and self._is_selectable("route", route_hit[0]):
                self._dragging_route_point = route_hit
                self._mode = ToolMode.MOVE_ROUTE_POINT
                self.setCursor(Qt.ClosedHandCursor)
                return

            if self._is_selectable("distance_measure", ""):
                distance_point_hit = self._hit_distance_measurement_point(canvas_pt)
                if distance_point_hit is not None:
                    measure_id, point_idx = distance_point_hit
                    self.object_clicked.emit("distance_measure", measure_id)
                    self._dragging_route_point = (measure_id, point_idx)
                    self._mode = ToolMode.MOVE_ROUTE_POINT
                    self.setCursor(Qt.ClosedHandCursor)
                    return

            if self._is_selectable("angle_measure", ""):
                angle_point_hit = self._hit_angle_measurement_point(canvas_pt)
                if angle_point_hit is not None:
                    measure_id, point_idx = angle_point_hit
                    self.object_clicked.emit("angle_measure", measure_id)
                    self._dragging_route_point = (measure_id, point_idx)
                    self._mode = ToolMode.MOVE_ROUTE_POINT
                    self.setCursor(Qt.ClosedHandCursor)
                    return

            hit = self._hit_start_point(canvas_pt)
            if hit and self._is_selectable("polygon", hit):
                self._dragging_start = hit
                self._mode = ToolMode.MOVE_START
                self.setCursor(Qt.ClosedHandCursor)
                return
            elec_hit = self._hit_elec_point(canvas_pt)
            if elec_hit and self._is_selectable("elec_point", elec_hit):
                self.object_clicked.emit("elec_point", elec_hit)
                self._dragging_elec_point = elec_hit
                self._mode = ToolMode.MOVE_ELEC_POINT
                self.setCursor(Qt.ClosedHandCursor)
                return
            hkv_hit = self._hit_hkv(canvas_pt)
            if hkv_hit and self._is_selectable("hkv", hkv_hit):
                self.object_clicked.emit("hkv", hkv_hit)
                self._dragging_hkv = hkv_hit
                self._mode = ToolMode.MOVE_HKV
                self.setCursor(Qt.ClosedHandCursor)
                return

            # ── Unified object selection: try to hit any other object ──
            obj = self._hit_any_object(canvas_pt)
            if obj:
                obj_type, obj_id = obj
                self.object_clicked.emit(obj_type, obj_id)
                # Store for potential drag-to-move
                self._last_clicked_object = obj
                return

            self._pan_start = pos
            self._panning   = True

    def mouseMoveEvent(self, event):
        pos       = QPointF(event.position())
        canvas_pt = self._to_canvas(pos)
        if self._mode != ToolMode.NONE:
            self._helper_hover_endpoint = None
        if self._mode == ToolMode.MEASURE_ANGLE:
            self._mouse_pos = self._snap_measure_point(canvas_pt)
        elif self._mode == ToolMode.MEASURE:
            self._mouse_pos = self._snap_measure_point(canvas_pt)
        else:
            self._mouse_pos = canvas_pt

        if self._mode in (ToolMode.PLACE_ELEC_POINT, ToolMode.PLACE_HKV, ToolMode.PLACE_TEXT):
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            self._ghost_preview_pos = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
            self.update()
        elif self._ghost_preview_pos is not None:
            self._ghost_preview_pos = None

        if self._mode == ToolMode.NONE:
            hover_obj = self._hit_any_object(canvas_pt)
            if not hover_obj:
                text_hit = self._hit_text_annotation(canvas_pt)
                if text_hit:
                    hover_obj = ("text", text_hit)
            if not hover_obj:
                label_hit = self._hit_label(canvas_pt)
                if label_hit:
                    hover_obj = ("label", label_hit)
            if hover_obj != self._hover_object:
                self._hover_object = hover_obj
                self.update()
        elif self._hover_object is not None:
            self._hover_object = None
            self.update()

        # ── Handle box-selection drag (Ctrl+Drag) ──
        # Note: cables are excluded from box-selection (only via Shift+Click)
        if self._is_selecting_by_drag and self._selection_start:
            self._selection_rect = QRectF(self._selection_start, canvas_pt).normalized()
            # Update multi-selected to include all point-type objects in rect
            # Keep any cables that were already selected via Shift+Click
            cables_in_selection = {item for item in self._multi_selected if item[0] == "elec_cable"}
            self._multi_selected.clear()
            self._multi_selected.update(cables_in_selection)
            for obj_type, obj_id in self._get_all_selectable_objects():
                if obj_type == "elec_point":
                    pt = self._elec_points.get(obj_id)
                    if pt and self._selection_rect.contains(pt):
                        self._multi_selected.add((obj_type, obj_id))
                elif obj_type == "hkv":
                    pt = self._hkv_points.get(obj_id)
                    if pt and self._selection_rect.contains(pt):
                        self._multi_selected.add((obj_type, obj_id))
                elif obj_type == "text":
                    pt = self._text_annotations.get(obj_id)
                    if pt and self._selection_rect.contains(pt):
                        self._multi_selected.add((obj_type, obj_id))
            self.update()
            return

        # ── Handle multi-object drag (Alt+Drag) ──
        if self._dragging_multi and self._drag_multi_anchor:
            self._dragging_multi = {
                item for item in self._dragging_multi
                if self._is_selectable(item[0], item[1])
            }
            if not self._dragging_multi:
                self._finalize_multi_drag()
                return
            left_held = bool(event.buttons() & Qt.LeftButton)
            alt_held = bool(QApplication.keyboardModifiers() & Qt.AltModifier)

            # Multi-move is active only while Alt + Left Mouse are both held.
            if not left_held or not alt_held:
                self._finalize_multi_drag()
                return

            delta = canvas_pt - self._drag_multi_anchor
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            moved_ap_ids = set()
            
            for sel_type, sel_id in self._dragging_multi:
                if sel_type == "elec_point" and sel_id in self._elec_points:
                    start_pos = self._drag_multi_start_positions.get((sel_type, sel_id))
                    if start_pos:
                        new_pos = QPointF(start_pos.x() + delta.x(), start_pos.y() + delta.y())
                        new_pos = new_pos if ctrl_held else self._snap_to_grid(new_pos)
                        self._elec_points[sel_id] = new_pos
                        moved_ap_ids.add(sel_id)
                        
                elif sel_type == "hkv" and sel_id in self._hkv_points:
                    start_pos = self._drag_multi_start_positions.get((sel_type, sel_id))
                    if start_pos:
                        new_pos = QPointF(start_pos.x() + delta.x(), start_pos.y() + delta.y())
                        new_pos = new_pos if ctrl_held else self._snap_to_grid(new_pos)
                        self._hkv_points[sel_id] = new_pos
                        
                elif sel_type == "text" and sel_id in self._text_annotations:
                    start_pos = self._drag_multi_start_positions.get((sel_type, sel_id))
                    if start_pos:
                        new_pos = QPointF(start_pos.x() + delta.x(), start_pos.y() + delta.y())
                        new_pos = new_pos if ctrl_held else self._snap_to_grid(new_pos)
                        self._text_annotations[sel_id] = new_pos
                        
                elif sel_type == "elec_cable" and sel_id in self._elec_cables:
                    start_pts = self._drag_multi_start_positions.get((sel_type, sel_id))
                    if start_pts:
                        new_pts = []
                        for start_pt in start_pts:
                            new_pt = QPointF(start_pt.x() + delta.x(), start_pt.y() + delta.y())
                            new_pt = new_pt if ctrl_held else self._snap_to_grid(new_pt)
                            new_pts.append(new_pt)
                        self._elec_cables[sel_id] = new_pts

            if moved_ap_ids:
                for cid, ap_id in self._cable_start_ap.items():
                    if ap_id in moved_ap_ids and cid in self._elec_cables and self._elec_cables[cid]:
                        self._elec_cables[cid][0] = QPointF(self._elec_points[ap_id])
                for cid, ap_id in self._cable_end_ap.items():
                    if ap_id in moved_ap_ids and cid in self._elec_cables and self._elec_cables[cid]:
                        self._elec_cables[cid][-1] = QPointF(self._elec_points[ap_id])
            
            self.update()
            return

        # ── Handle panning ──
        if self._panning and self._pan_start:
            delta = pos - self._pan_start
            self._offset += delta
            self._pan_start = pos
            self._current_route_preview_end = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            self.update()
            return

        if self._mode == ToolMode.MEASURE:
            self.update()
            return

        if self._mode == ToolMode.MEASURE_ANGLE:
            self.update()
            return

        # ── Dragging any label ──
        if self._dragging_label:
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            raw_pos = QPointF(
                canvas_pt.x() - self._label_drag_offset.x(),
                canvas_pt.y() - self._label_drag_offset.y())
            new_pos = raw_pos if ctrl_held else self._snap_to_grid(raw_pos)
            if self._dragging_label.startswith("measure:"):
                try:
                    idx = int(self._dragging_label.split(":", 1)[1])
                except ValueError:
                    idx = None
                if idx is not None:
                    if 0 <= idx < len(self._measure_label_positions):
                        old_lp = self._measure_label_positions[idx]
                        self._measure_label_positions[idx] = (float(new_pos.x()), float(new_pos.y()))
                        self._set_measure_auto_label_pos(f"MSRD-{idx + 1}", False)
                        self._debug_measure_pos(
                            "LABEL-DRAG",
                            idx=idx,
                            old=f"({old_lp[0]:.2f},{old_lp[1]:.2f})",
                            new=f"({new_pos.x():.2f},{new_pos.y():.2f})",
                            ctrl=ctrl_held,
                        )
                    else:
                        self._measure_label_positions.append((float(new_pos.x()), float(new_pos.y())))
                        self._set_measure_auto_label_pos(f"MSRD-{idx + 1}", False)
                        self._debug_measure_pos(
                            "LABEL-APPEND",
                            idx=idx,
                            new=f"({new_pos.x():.2f},{new_pos.y():.2f})",
                            ctrl=ctrl_held,
                        )
            elif self._dragging_label.startswith("helper:"):
                parts = self._dragging_label.split(":", 2)
                if len(parts) == 3:
                    fid, hid = parts[1], parts[2]
                    self._helper_label_positions.setdefault(fid, {})[hid] = (float(new_pos.x()), float(new_pos.y()))
            else:
                self._label_positions[self._dragging_label] = new_pos
            self.update()
            return

        # ── Hover: hand cursor for helper endpoints / measurement labels (NONE mode) ──
        if self._mode == ToolMode.NONE and not self._helper_dragging_endpoint:
            helper_ep_hit = self._hit_any_helper_line_endpoint(canvas_pt)
            if helper_ep_hit is not None:
                self._helper_hover_endpoint = helper_ep_hit
                self.setCursor(Qt.OpenHandCursor)
                self.update()
            else:
                self._helper_hover_endpoint = None
                if self._measure_label_positions:
                    thresh = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
                    for lp in self._measure_label_positions:
                        try:
                            lab_pt = QPointF(lp[0], lp[1])
                        except Exception:
                            continue
                        if _qdist(canvas_pt, lab_pt) < thresh:
                            self.setCursor(Qt.OpenHandCursor)
                            break
                    else:
                        self.setCursor(Qt.ArrowCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)

        # ── Handle dragging of whole elec cable (NONE mode) ──
        if (
            self._mode == ToolMode.NONE
            and self._dragging_elec_cable_id
            and self._dragging_elec_cable_start
        ):
            cid = self._dragging_elec_cable_id
            if not self._is_selectable("elec_cable", cid):
                self._dragging_elec_cable_id = None
                self._dragging_elec_cable_start = None
                self._dragging_elec_cable_origin = []
                self._dragging_elec_cable_fixed_indices = set()
                return
            pts = self._elec_cables.get(cid)
            origin = self._dragging_elec_cable_origin
            if pts and len(pts) == len(origin):
                dx = canvas_pt.x() - self._dragging_elec_cable_start.x()
                dy = canvas_pt.y() - self._dragging_elec_cable_start.y()
                for i, orig in enumerate(origin):
                    if i in self._dragging_elec_cable_fixed_indices:
                        continue
                    pts[i] = QPointF(orig.x() + dx, orig.y() + dy)

                # Keep AP-bound endpoints fixed at AP position
                if 0 in self._dragging_elec_cable_fixed_indices:
                    start_ap = self._cable_start_ap.get(cid)
                    if start_ap and start_ap in self._elec_points:
                        pts[0] = QPointF(self._elec_points[start_ap])
                if (len(pts) - 1) in self._dragging_elec_cable_fixed_indices:
                    end_ap = self._cable_end_ap.get(cid)
                    if end_ap and end_ap in self._elec_points:
                        pts[-1] = QPointF(self._elec_points[end_ap])

                self.update()
            return

        # ── Handle dragging of elec cable points (at any time, not just in edit mode) ──
        if self._dragging_route_point and self._mode == ToolMode.NONE:
            cid, idx = self._dragging_route_point
            if not self._is_selectable("elec_cable", cid):
                self._dragging_route_point = None
                return
            if cid in self._elec_cables:
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

        if self._mode == ToolMode.DRAW_HELPER_LINE and self._helper_draw_start is not None:
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            snapped_pt = _snap_to_helper_line_points(self, canvas_pt, self._helper_active_floor_id, snap_radius_px=15.0)
            current_pt = QPointF(snapped_pt) if snapped_pt else QPointF(canvas_pt)
            if not ctrl_held:
                angled_pt = self._apply_helper_construction_snap(
                    self._helper_draw_start,
                    current_pt,
                    tolerance_deg=3.0,
                    floor_id=self._helper_active_floor_id,
                )
                if _qdist(angled_pt, current_pt) > 1e-6:
                    current_pt = angled_pt
                elif snapped_pt is None:
                    current_pt = self._snap_to_grid(current_pt)
            self._helper_draw_current = current_pt
            self.update()
            return

        if self._mode == ToolMode.NONE and self._helper_dragging_endpoint and self._helper_selected_floor_id:
            hid, idx = self._helper_dragging_endpoint
            fid = self._helper_selected_floor_id
            pts = self._floor_helper_lines.get(fid, {}).get(hid)
            if pts and len(pts) == 2:
                ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                snapped_pt = _snap_to_helper_line_points(self, canvas_pt, fid, snap_radius_px=15.0, exclude_line_id=hid)
                new_pt = QPointF(snapped_pt) if snapped_pt else QPointF(canvas_pt)
                anchor_idx = 1 if idx == 0 else 0
                if not ctrl_held:
                    angled_pt = self._apply_helper_construction_snap(
                        pts[anchor_idx],
                        new_pt,
                        tolerance_deg=3.0,
                        floor_id=fid,
                        exclude_line_id=hid,
                    )
                    if _qdist(angled_pt, new_pt) > 1e-6:
                        new_pt = angled_pt
                    elif snapped_pt is None:
                        new_pt = self._snap_to_grid(new_pt)

                # Bei fixierter Länge darf Richtung geändert werden, aber nicht die Länge.
                if self._floor_helper_line_fixed.get(fid, {}).get(hid, False) and self._mm_per_px > 0:
                    anchor_pt = pts[anchor_idx]
                    dx = new_pt.x() - anchor_pt.x()
                    dy = new_pt.y() - anchor_pt.y()
                    direction_len = math.hypot(dx, dy)
                    target_mm = self._floor_helper_line_length_mm.get(fid, {}).get(hid)
                    if target_mm is None:
                        target_mm = _qdist(pts[0], pts[1]) * self._mm_per_px
                    target_px = max(1.0, float(target_mm)) / self._mm_per_px
                    if direction_len < 1e-9:
                        old_other = pts[idx]
                        dx = old_other.x() - anchor_pt.x()
                        dy = old_other.y() - anchor_pt.y()
                        direction_len = math.hypot(dx, dy)
                    if direction_len > 1e-9:
                        ux = dx / direction_len
                        uy = dy / direction_len
                        new_pt = QPointF(anchor_pt.x() + ux * target_px,
                                         anchor_pt.y() + uy * target_px)
                pts[idx] = new_pt
                if self._mm_per_px > 0:
                    self._floor_helper_line_length_mm.setdefault(fid, {})[hid] = _qdist(pts[0], pts[1]) * self._mm_per_px
                self.update()
            return

        if self._mode == ToolMode.EDIT_HELPER_LINE:
            fid = self._ensure_helper_floor(self._helper_active_floor_id)
            if not fid:
                return
            if self._helper_dragging_endpoint:
                hid, idx = self._helper_dragging_endpoint
                pts = self._floor_helper_lines.get(fid, {}).get(hid)
                if pts and len(pts) == 2:
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    snapped_pt = _snap_to_helper_line_points(self, canvas_pt, fid, snap_radius_px=15.0, exclude_line_id=hid)
                    new_pt = QPointF(snapped_pt) if snapped_pt else QPointF(canvas_pt)
                    anchor_idx = 1 if idx == 0 else 0
                    if not ctrl_held:
                        angled_pt = self._apply_helper_construction_snap(
                            pts[anchor_idx],
                            new_pt,
                            tolerance_deg=3.0,
                            floor_id=fid,
                            exclude_line_id=hid,
                        )
                        if _qdist(angled_pt, new_pt) > 1e-6:
                            new_pt = angled_pt
                        elif snapped_pt is None:
                            new_pt = self._snap_to_grid(new_pt)

                    if self._floor_helper_line_fixed.get(fid, {}).get(hid, False) and self._mm_per_px > 0:
                        anchor_pt = pts[anchor_idx]
                        dx = new_pt.x() - anchor_pt.x()
                        dy = new_pt.y() - anchor_pt.y()
                        direction_len = math.hypot(dx, dy)
                        target_mm = self._floor_helper_line_length_mm.get(fid, {}).get(hid)
                        if target_mm is None:
                            target_mm = _qdist(pts[0], pts[1]) * self._mm_per_px
                        target_px = max(1.0, float(target_mm)) / self._mm_per_px
                        if direction_len < 1e-9:
                            old_other = pts[idx]
                            dx = old_other.x() - anchor_pt.x()
                            dy = old_other.y() - anchor_pt.y()
                            direction_len = math.hypot(dx, dy)
                        if direction_len > 1e-9:
                            ux = dx / direction_len
                            uy = dy / direction_len
                            new_pt = QPointF(anchor_pt.x() + ux * target_px,
                                             anchor_pt.y() + uy * target_px)
                    pts[idx] = new_pt
                    if self._mm_per_px > 0:
                        self._floor_helper_line_length_mm.setdefault(fid, {})[hid] = _qdist(pts[0], pts[1]) * self._mm_per_px
                    self.update()
                return
            if self._helper_dragging_whole_id and self._helper_drag_start:
                hid = self._helper_dragging_whole_id
                pts = self._floor_helper_lines.get(fid, {}).get(hid)
                if pts and len(pts) == 2 and len(self._helper_drag_origin) == 2:
                    dx = canvas_pt.x() - self._helper_drag_start.x()
                    dy = canvas_pt.y() - self._helper_drag_start.y()
                    pts[0] = QPointF(self._helper_drag_origin[0].x() + dx,
                                    self._helper_drag_origin[0].y() + dy)
                    pts[1] = QPointF(self._helper_drag_origin[1].x() + dx,
                                    self._helper_drag_origin[1].y() + dy)
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    if not ctrl_held:
                        pts[0] = self._snap_to_grid(pts[0])
                        pts[1] = self._snap_to_grid(pts[1])
                    if self._mm_per_px > 0:
                        self._floor_helper_line_length_mm.setdefault(fid, {})[hid] = _qdist(pts[0], pts[1]) * self._mm_per_px
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
            owner_id, idx = self._dragging_route_point
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            if ctrl_held:
                constrained = canvas_pt
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
            else:
                constrained = self._constrain_dragged_route_point(owner_id, idx, canvas_pt)
            if owner_id in self._manual_routes and 0 <= idx < len(self._manual_routes[owner_id]):
                self._manual_routes[owner_id][idx] = constrained
            elif owner_id.startswith("MSRD-"):
                m_idx = self._measurement_obj_to_index(owner_id, "MSRD")
                if m_idx is not None and 0 <= m_idx < len(self._measure_lines):
                    p1, p2, _old_len = self._measure_lines[m_idx]
                    new_pt = constrained if ctrl_held else self._snap_to_grid(constrained)
                    if idx == 0:
                        p1 = new_pt
                    elif idx == 1:
                        # Move label together with the last point (p2)
                        old_p2 = QPointF(p2)
                        p2 = new_pt
                        if m_idx < len(self._measure_label_positions):
                            measurement_id = f"MSRD-{m_idx + 1}"
                            style = self._measurement_style(measurement_id)
                            if bool(style.get("auto_label_pos", True)):
                                offset_x = 0.0
                                offset_y = 0.0
                            else:
                                lx, ly = self._measure_label_positions[m_idx]
                                offset_x = lx - old_p2.x()
                                offset_y = ly - old_p2.y()
                            self._measure_label_positions[m_idx] = (
                                p2.x() + offset_x,
                                p2.y() + offset_y,
                            )
                            self._debug_measure_pos(
                                "ENDPOINT-DRAG-LABEL",
                                idx=m_idx,
                                old_p2=f"({old_p2.x():.2f},{old_p2.y():.2f})",
                                new_p2=f"({p2.x():.2f},{p2.y():.2f})",
                                offset=f"({offset_x:.2f},{offset_y:.2f})",
                                label=f"({self._measure_label_positions[m_idx][0]:.2f},{self._measure_label_positions[m_idx][1]:.2f})",
                                auto=bool(style.get("auto_label_pos", True)),
                                ctrl=ctrl_held,
                            )
                    mm_len = self._measurement_distance(p1, p2)
                    self._measure_lines[m_idx] = (QPointF(p1), QPointF(p2), mm_len)
                    self._debug_measure_pos(
                        "ENDPOINT-DRAG",
                        idx=m_idx,
                        endpoint=idx,
                        p1=f"({p1.x():.2f},{p1.y():.2f})",
                        p2=f"({p2.x():.2f},{p2.y():.2f})",
                        mm_len=f"{mm_len:.2f}",
                        ctrl=ctrl_held,
                    )
            elif owner_id.startswith("MSRA-"):
                a_idx = self._measurement_obj_to_index(owner_id, "MSRA")
                if a_idx is not None and 0 <= a_idx < len(self._angle_measurements):
                    p1, p2, p3, _old_angle = self._angle_measurements[a_idx]
                    new_pt = constrained if ctrl_held else self._snap_to_grid(constrained)
                    if idx == 0:
                        p1 = new_pt
                    elif idx == 1:
                        p2 = new_pt
                    elif idx == 2:
                        # Move label together with the last point (p3)
                        old_p3 = QPointF(p3)
                        p3 = new_pt
                        if a_idx < len(self._angle_measure_label_positions):
                            lx, ly = self._angle_measure_label_positions[a_idx]
                            offset_x = lx - old_p3.x()
                            offset_y = ly - old_p3.y()
                            self._angle_measure_label_positions[a_idx] = (
                                p3.x() + offset_x, p3.y() + offset_y
                            )
                    angle_deg = self._measurement_angle_deg(p1, p2, p3)
                    self._angle_measurements[a_idx] = (QPointF(p1), QPointF(p2), QPointF(p3), angle_deg)
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
                    return
                edge_hit = self._hit_polygon_edge(canvas_pt, self._edit_polygon_cid)
                self.setCursor(Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
            return

        if self._mode == ToolMode.EDIT_POLYGON and self._edit_elec_room_id:
            rid = self._edit_elec_room_id
            if self._dragging_route_point:
                oid, idx = self._dragging_route_point
                if oid == rid and rid in self._elec_room_polygons:
                    ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
                    pt = canvas_pt if ctrl_held else self._snap_to_grid(canvas_pt)
                    self._elec_room_polygons[rid][idx] = pt
                    self.update()
            else:
                hit = self._hit_polygon_point(canvas_pt, rid)
                if hit is not None:
                    self.setCursor(Qt.OpenHandCursor)
                    return
                edge_hit = self._hit_polygon_edge(canvas_pt, rid)
                self.setCursor(Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
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
                    return
                edge_hit = self._hit_floor_polygon_edge(canvas_pt, fid)
                self.setCursor(Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
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
                    return
                edge_hit = self._hit_route_edge(canvas_pt, self._edit_route_cid)
                self.setCursor(Qt.PointingHandCursor if edge_hit else Qt.CrossCursor)
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

            # If Alt is pressed while dragging a selected AP, switch to
            # multi-object drag immediately.
            alt_held = bool(QApplication.keyboardModifiers() & Qt.AltModifier)
            left_held = bool(event.buttons() & Qt.LeftButton)
            if alt_held and left_held and ("elec_point", pid) in self._multi_selected:
                self._dragging_multi = self._multi_selected.copy()
                self._drag_multi_anchor = canvas_pt
                self._drag_multi_start_positions.clear()
                for sel_type, sel_id in self._dragging_multi:
                    if sel_type == "elec_point" and sel_id in self._elec_points:
                        self._drag_multi_start_positions[(sel_type, sel_id)] = QPointF(self._elec_points[sel_id])
                    elif sel_type == "hkv" and sel_id in self._hkv_points:
                        self._drag_multi_start_positions[(sel_type, sel_id)] = QPointF(self._hkv_points[sel_id])
                    elif sel_type == "text" and sel_id in self._text_annotations:
                        pos = self._coerce_canvas_point(self._text_annotations[sel_id])
                        if pos is not None:
                            self._text_annotations[sel_id] = pos
                            self._drag_multi_start_positions[(sel_type, sel_id)] = QPointF(pos)
                    elif sel_type == "elec_cable" and sel_id in self._elec_cables:
                        self._drag_multi_start_positions[(sel_type, sel_id)] = [QPointF(p) for p in self._elec_cables[sel_id]]

                self._dragging_elec_point = None
                self._mode = ToolMode.NONE
                self.will_move_multi_objects.emit()
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                return

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
                    if idx == 0:
                        # First point stays locked to the heating circuit start point
                        locked = self._start_points.get(cid)
                        if locked:
                            pts[0] = QPointF(locked)
                    elif idx == len(pts) - 1:
                        # Snap last point to nearest HKV
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
            ctrl_held = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            raw_pos = QPointF(
                canvas_pt.x() - self._label_drag_offset.x(),
                canvas_pt.y() - self._label_drag_offset.y())
            new_pos = raw_pos if ctrl_held else self._snap_to_grid(raw_pos)
            label_id = self._dragging_label
            if label_id.startswith("measure:"):
                try:
                    idx = int(label_id.split(":", 1)[1])
                except (TypeError, ValueError, IndexError):
                    idx = -1
                if 0 <= idx < len(self._measure_label_positions):
                    self._measure_label_positions[idx] = (float(new_pos.x()), float(new_pos.y()))
            elif label_id.startswith("angle:"):
                try:
                    idx = int(label_id.split(":", 1)[1])
                except (TypeError, ValueError, IndexError):
                    idx = -1
                if 0 <= idx < len(self._angle_measure_label_positions):
                    self._angle_measure_label_positions[idx] = (float(new_pos.x()), float(new_pos.y()))
            else:
                self._label_positions[label_id] = new_pos
            self.update()
            return

        if self._mode == ToolMode.NONE:
            state_cleared = (
                self._current_route_preview_end is not None
                or self._constraint_violation_point is not None
                or self._constraint_violation_line is not None
                or bool(self._constraint_violation_reason)
            )
            self._current_route_preview_end = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            hover_cursor = Qt.ArrowCursor

            # ── Check if hovering over elec cable point ──
            threshold = self._px_to_canvas_units(HIT_CABLE_POINT_RADIUS_PX)
            for cid, pts in self._elec_cables.items():
                if not self._elec_visible.get(cid, True):
                    continue
                if any(_qdist(canvas_pt, pt) < threshold for pt in pts):
                    hover_cursor = Qt.OpenHandCursor
                    break

            if hover_cursor == Qt.ArrowCursor:
                label_hit = self._hit_label(canvas_pt)
                if label_hit:
                    hover_cursor = Qt.SizeAllCursor

            if hover_cursor == Qt.ArrowCursor:
                route_hit = self._hit_route_point(canvas_pt)
                if route_hit:
                    hover_cursor = Qt.OpenHandCursor

            if hover_cursor == Qt.ArrowCursor:
                if self._hit_distance_measurement_point(canvas_pt) is not None:
                    hover_cursor = Qt.OpenHandCursor

            if hover_cursor == Qt.ArrowCursor:
                if self._hit_angle_measurement_point(canvas_pt) is not None:
                    hover_cursor = Qt.OpenHandCursor

            if hover_cursor == Qt.ArrowCursor:
                ap_hit = self._hit_elec_point(canvas_pt)
                if ap_hit:
                    hover_cursor = Qt.OpenHandCursor

            if hover_cursor == Qt.ArrowCursor:
                hkv_hover = self._hit_hkv(canvas_pt)
                if hkv_hover:
                    hover_cursor = Qt.OpenHandCursor

            if hover_cursor == Qt.ArrowCursor:
                hit = self._hit_start_point(canvas_pt)
                if hit:
                    hover_cursor = Qt.OpenHandCursor

            self.setCursor(hover_cursor)
            if state_cleared:
                self.update()

        # Tooltip for APs, cables and text annotations on hover
        tooltip_text = ""

        ap_hit = self._hit_elec_point(canvas_pt)
        if ap_hit:
            device = self._elec_point_smarthome_device.get(ap_hit, "").strip()
            device_color = self._elec_point_smarthome_device_color.get(ap_hit, "").strip()
            note = self._elec_point_notes.get(ap_hit, "").strip()
            parts: list[str] = []
            if device:
                parts.append(f"Unterputz-Gerät: {device}")
            if device_color:
                parts.append(f"Gerätefarbe: {device_color}")
            if note:
                parts.append(note)
            tooltip_text = "\n".join(parts)

        if not tooltip_text:
            for cid, pts in self._elec_cables.items():
                if not self._elec_visible.get(cid, True) or len(pts) < 2:
                    continue
                hit_point = self._hit_elec_cable_point(canvas_pt, cid)
                hit_edge = self._hit_elec_cable_edge(canvas_pt, cid)
                if hit_point is not None or hit_edge is not None:
                    note = self._elec_cable_notes.get(cid, "").strip()
                    if note:
                        tooltip_text = note
                    break

        if not tooltip_text:
            text_hit = self._hit_text_annotation(canvas_pt)
            if text_hit:
                tooltip_text = self._text_comments.get(text_hit, "").strip()

        if tooltip_text:
            QToolTip.showText(event.globalPosition().toPoint(), tooltip_text, self)
        else:
            QToolTip.hideText()

    def _finalize_multi_drag(self):
        """Finalize Alt+drag multi-move with the same semantics as mouse release."""
        if not self._dragging_multi:
            return

        changed_cable_ids = set()
        # Repair cable endpoints to nearest APs
        for sel_type, sel_id in self._dragging_multi:
            if sel_type == "elec_cable" and sel_id in self._elec_cables:
                pts = self._elec_cables[sel_id]
                if len(pts) >= 2:
                    start_ap = self._find_nearest_ap(pts[0])
                    if start_ap:
                        self._cable_start_ap[sel_id] = start_ap
                        pts[0] = QPointF(self._elec_points[start_ap])
                    else:
                        self._cable_start_ap.pop(sel_id, None)
                    end_ap = self._find_nearest_ap(pts[-1])
                    if end_ap:
                        self._cable_end_ap[sel_id] = end_ap
                        pts[-1] = QPointF(self._elec_points[end_ap])
                    else:
                        self._cable_end_ap.pop(sel_id, None)
                    changed_cable_ids.add(sel_id)
            elif sel_type == "elec_point":
                self.elec_point_changed.emit(sel_id)
                for cid, ap_id in self._cable_start_ap.items():
                    if ap_id == sel_id:
                        changed_cable_ids.add(cid)
                for cid, ap_id in self._cable_end_ap.items():
                    if ap_id == sel_id:
                        changed_cable_ids.add(cid)
            elif sel_type == "hkv":
                self.hkv_placed.emit(sel_id)
            elif sel_type == "text":
                self.label_moved.emit(sel_id)

        for cid in changed_cable_ids:
            self.elec_cable_changed.emit(cid)

        self._dragging_multi.clear()
        self._drag_multi_start_positions.clear()
        self._drag_multi_anchor = None
        self.multi_objects_moved.emit()
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning   = False
            self._pan_start = None
            return

        if event.button() == Qt.LeftButton:
            # Finalize box-selection drag (Ctrl+Drag)
            if self._is_selecting_by_drag:
                self._is_selecting_by_drag = False
                self._selection_start = None
                self._selection_rect = None
                self.multi_selection_changed.emit(self._multi_selected.copy())
                self.setCursor(Qt.ArrowCursor)
                self.update()
                return

            # Finalize multi-object drag (Alt+Drag)
            if self._dragging_multi:
                self._finalize_multi_drag()
                return

            # Finalize measurement label drag
            if self._mode == ToolMode.MOVE_MEASURE_LABEL and self._dragging_measure_label_idx is not None:
                self._dragging_measure_label_idx = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.measure_changed.emit()
                self.update()
                return
            if self._mode == ToolMode.NONE and self._dragging_elec_cable_id:
                cid = self._dragging_elec_cable_id
                self._dragging_elec_cable_id = None
                self._dragging_elec_cable_start = None
                self._dragging_elec_cable_origin = []
                self._dragging_elec_cable_fixed_indices = set()
                self.setCursor(Qt.ArrowCursor)
                self.elec_cable_changed.emit(cid)
                self.update()
                return

            # ── Handle dragging of elec cable points (in NONE mode) ──
            if self._dragging_route_point and self._mode == ToolMode.NONE:
                cid, idx = self._dragging_route_point
                self._dragging_route_point = None
                self.setCursor(Qt.ArrowCursor)
                # Update AP binding if first or last point was moved
                pts = self._elec_cables.get(cid, [])
                if pts and (idx == 0 or idx == len(pts) - 1):
                    ap = self._find_nearest_ap(pts[idx])
                    if idx == 0:
                        if ap:
                            self._cable_start_ap[cid] = ap
                            self._elec_cables[cid][0] = QPointF(self._elec_points[ap])
                        else:
                            self._cable_start_ap.pop(cid, None)
                    else:
                        if ap:
                            self._cable_end_ap[cid] = ap
                            self._elec_cables[cid][-1] = QPointF(self._elec_points[ap])
                        else:
                            self._cable_end_ap.pop(cid, None)
                self.elec_cable_changed.emit(cid)
                self.update()
                return

            # ── Export-Rahmen Zeichnen abschliessen ──
            if self._mode == ToolMode.DRAW_EXPORT_FRAME and self._export_frame_start:
                end_pt = self._export_frame_current or self._export_frame_start
                rect = QRectF(self._export_frame_start, end_pt).normalized()
                if rect.width() > 1.0 and rect.height() > 1.0:
                    self._export_frame = rect
                    self.export_frame_drawn.emit(QRectF(rect))
                self._export_frame_start = None
                self._export_frame_current = None
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
                return

            if self._mode == ToolMode.EDIT_HELPER_LINE:
                changed = False
                if self._helper_dragging_endpoint:
                    self._helper_dragging_endpoint = None
                    changed = True
                if self._helper_dragging_whole_id:
                    self._helper_dragging_whole_id = None
                    self._helper_drag_start = None
                    self._helper_drag_origin = []
                    changed = True
                if changed:
                    self.setCursor(Qt.ArrowCursor)
                    self.helper_lines_changed.emit()
                    self.update()
                    return

            if self._mode == ToolMode.NONE and self._helper_dragging_endpoint:
                self._helper_dragging_endpoint = None
                self._helper_hover_endpoint = None
                self.setCursor(Qt.ArrowCursor)
                self.helper_lines_changed.emit()
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
                self.setCursor(Qt.CrossCursor)
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
                owner_id, _ = self._dragging_route_point
                self._dragging_route_point = None
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self.setCursor(Qt.ArrowCursor)
                if owner_id.startswith("MSRD-") or owner_id.startswith("MSRA-"):
                    self.measure_changed.emit()
                else:
                    self.route_changed.emit(owner_id)
                return
            if self._dragging_route_point and self._mode == ToolMode.EDIT_POLYGON:
                cid, _ = self._dragging_route_point
                self._dragging_route_point = None
                self._edit_drag_last_pos = None
                if self._edit_floor_polygon_id and cid == self._edit_floor_polygon_id:
                    self.floor_plan_polygon_changed.emit(cid)
                    self.update()
                    return
                if self._edit_elec_room_id and cid == self._edit_elec_room_id:
                    self.elec_room_polygon_changed.emit(cid)
                else:
                    self.polygon_changed.emit(cid)
                self.update()
                return
            if self._dragging_route_point and self._mode == ToolMode.EDIT_ROUTE:
                cid, _ = self._dragging_route_point
                self._dragging_route_point = None
                self._edit_drag_last_pos = None
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
                self._edit_drag_last_pos = None
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
                self._edit_drag_last_pos = None
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
                self._edit_drag_last_pos = None
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
                moved_id = self._dragging_label
                self._dragging_label = None
                self._label_drag_offset = QPointF(0, 0)
                self.setCursor(Qt.ArrowCursor)
                self.label_moved.emit(moved_id)
                return
            if self._dragging_text:
                moved_text = self._dragging_text
                self._dragging_text = None
                self.setCursor(Qt.ArrowCursor)
                self.text_placed.emit(moved_text)
                return
            self._panning   = False
            self._pan_start = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._mode == ToolMode.DRAW_POLY:
                if len(self._current_points) >= 3:
                    pts = [(p.x(), p.y()) for p in self._current_points]
                    if self._current_elec_room_id:
                        rid = self._current_elec_room_id
                        self._elec_room_polygons[rid] = list(self._current_points)
                        self._elec_room_visible.setdefault(rid, True)
                        self.elec_room_polygon_finished.emit(rid, pts)
                    elif self._current_circuit_id:
                        self._polygons[self._current_circuit_id] = list(self._current_points)
                        self._start_points[self._current_circuit_id] = self._current_points[0]
                        self.polygon_finished.emit(self._current_circuit_id, pts)
                self._mode = ToolMode.NONE
                self._current_points = []
                self._current_elec_room_id = None
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
                return

            if self._mode == ToolMode.DRAW_FURNITURE_POLY:
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
                        layer.polygon = [QPointF(p.x() - min_x, p.y() - min_y) for p in self._current_points]
                        pts = [(p.x(), p.y()) for p in self._current_points]
                        self.floor_plan_polygon_finished.emit(self._current_furniture_id, pts)
                self._mode = ToolMode.NONE
                self._current_furniture_id = None
                self._current_points = []
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                self.update()
                return

            if self._mode == ToolMode.DRAW_ROUTE and self._current_route_cid:
                cid = self._current_route_cid
                if len(self._current_route_points) >= 2:
                    self._manual_routes[cid] = list(self._current_route_points)
                self._current_route_cid = None
                self._current_route_points = []
                self._current_route_preview_end = None
                self._constraint_violation_point = None
                self._constraint_violation_line = None
                self._constraint_violation_reason = ""
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if len(self._manual_routes.get(cid, [])) >= 2:
                    self.route_changed.emit(cid)
                self.update()
                return

            if self._mode == ToolMode.DRAW_ELEC_CABLE and self._current_elec_cable_id:
                cid = self._current_elec_cable_id
                has_geometry = False
                if len(self._current_elec_cable_points) >= 2:
                    if self._drawing_cable_from_start:
                        self._current_elec_cable_points = list(reversed(self._current_elec_cable_points))
                        first_pt = self._current_elec_cable_points[0]
                        start_ap = self._find_nearest_ap(first_pt)
                        if start_ap:
                            self._current_elec_cable_points[0] = QPointF(self._elec_points[start_ap])
                            self._cable_start_ap[cid] = start_ap
                        else:
                            self._cable_start_ap.pop(cid, None)
                    last_pt = self._current_elec_cable_points[-1]
                    end_ap = self._find_nearest_ap(last_pt)
                    if end_ap:
                        self._current_elec_cable_points[-1] = QPointF(self._elec_points[end_ap])
                        self._cable_end_ap[cid] = end_ap
                    else:
                        self._cable_end_ap[cid] = ""
                    self._elec_cables[cid] = list(self._current_elec_cable_points)
                    has_geometry = True
                else:
                    self._cable_start_ap.pop(cid, None)
                    self._cable_end_ap.pop(cid, None)
                self._current_elec_cable_id = None
                self._current_elec_cable_points = []
                self._drawing_cable_from_start = False
                self._current_elec_cable_preview = None
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if has_geometry:
                    self.elec_cable_changed.emit(cid)
                self.update()
                return

            if self._mode == ToolMode.DRAW_SUPPLY_LINE and self._current_supply_cid:
                cid = self._current_supply_cid
                has_geometry = False
                if len(self._current_supply_points) >= 2:
                    last_pt = self._current_supply_points[-1]
                    hkv = self._find_nearest_hkv(last_pt)
                    if hkv:
                        self._current_supply_points[-1] = QPointF(self._hkv_points[hkv])
                        self._supply_hkv[cid] = hkv
                    else:
                        self._supply_hkv.pop(cid, None)
                    self._supply_lines[cid] = list(self._current_supply_points)
                    has_geometry = True
                self._current_supply_cid = None
                self._current_supply_points = []
                self._current_supply_preview = None
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if has_geometry:
                    self.supply_line_changed.emit(cid)
                self.update()
                return

            if self._mode == ToolMode.DRAW_HKV_LINE and self._current_hkv_line_id:
                lid = self._current_hkv_line_id
                has_geometry = False
                if len(self._current_hkv_line_points) >= 2:
                    last_pt = self._current_hkv_line_points[-1]
                    hkv = self._find_nearest_hkv(last_pt)
                    if hkv:
                        self._current_hkv_line_points[-1] = QPointF(self._hkv_points[hkv])
                        self._hkv_line_end[lid] = hkv
                    else:
                        self._hkv_line_end.pop(lid, None)
                    self._hkv_lines[lid] = list(self._current_hkv_line_points)
                    has_geometry = True
                else:
                    self._hkv_line_start.pop(lid, None)
                    self._hkv_line_end.pop(lid, None)
                self._current_hkv_line_id = None
                self._current_hkv_line_points = []
                self._current_hkv_line_preview = None
                self._mode = ToolMode.NONE
                self._ghost_preview_pos = None
                self.setCursor(Qt.ArrowCursor)
                self.mode_changed.emit()
                if has_geometry:
                    self.hkv_line_changed.emit(lid)
                self.update()
                return

        if event.key() == Qt.Key_Escape:
            self._mode           = ToolMode.NONE
            self._current_points = []
            self._current_furniture_id = None
            self._current_elec_room_id = None
            self._current_route_points = []
            self._current_route_cid = None
            self._current_route_preview_end = None
            self._dragging_start = None
            self._dragging_route_point = None
            self._dragging_elec_cable_id = None
            self._dragging_elec_cable_start = None
            self._dragging_elec_cable_origin = []
            self._dragging_elec_cable_fixed_indices = set()
            self._edit_polygon_cid = None
            self._edit_elec_room_id = None
            self._edit_floor_polygon_id = None
            self._edit_route_cid = None
            self._edit_selected_owner = None
            self._edit_selected_indices.clear()
            self._edit_selection_rect_start = None
            self._edit_selection_rect_end = None
            self._edit_drag_last_pos = None
            self._constraint_violation_point = None
            self._constraint_violation_line = None
            self._constraint_violation_reason = ""
            # Elektro
            self._placing_elec_point_id = None
            self._current_elec_cable_id = None
            self._current_elec_cable_points = []
            self._drawing_cable_from_start = False
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
            self._angle_measure_p1 = None
            self._angle_measure_p2 = None
            self._angle_measure_p3 = None
            self._helper_draw_start = None
            self._helper_draw_current = None
            self._helper_dragging_endpoint = None
            self._helper_dragging_whole_id = None
            self._helper_drag_start = None
            self._helper_drag_origin = []
            self._helper_selected_floor_id = None
            self._export_frame_start = None
            self._export_frame_current = None
            self._ghost_preview_pos = None
            # Multi-select
            self._is_selecting_by_drag = False
            self._selection_start = None
            self._selection_rect = None
            if self._dragging_multi:
                self._dragging_multi.clear()
                self._drag_multi_start_positions.clear()
                self._drag_multi_anchor = None
            self._multi_selected.clear()
            self.multi_selection_changed.emit(set())
            self.setCursor(Qt.ArrowCursor)
            self.mode_changed.emit()
            self.update()
        elif event.key() == Qt.Key_Delete and self._dragging_route_point:
            cid, idx = self._dragging_route_point
            if self._mode == ToolMode.EDIT_POLYGON and cid == self._edit_polygon_cid:
                self._delete_polygon_point(cid, idx)
            elif self._mode == ToolMode.EDIT_POLYGON and cid == self._edit_elec_room_id:
                self._delete_polygon_point(cid, idx)
            elif self._mode == ToolMode.EDIT_POLYGON and cid == self._edit_floor_polygon_id:
                self._delete_floor_polygon_point(cid, idx)
            elif self._mode == ToolMode.EDIT_ROUTE and cid == self._edit_route_cid:
                self._delete_route_point(cid, idx)
            self._dragging_route_point = None
            self.update()
        elif event.key() == Qt.Key_Delete and self._mode == ToolMode.EDIT_HELPER_LINE:
            self.delete_selected_helper_line()

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
        self._label_rects.clear()
        self._label_draw_pos.clear()

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

        # Elektro-Raum-Polygone
        for rid, pts in self._elec_room_polygons.items():
            if not self._elec_room_visible.get(rid, True):
                continue
            label = self._label_map.get(rid, rid)
            self._draw_polygon(
                painter,
                pts,
                self._color_map.get(rid, QColor("#43aa8b")),
                label,
            )

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
        elif self._mode == ToolMode.EDIT_POLYGON and self._edit_elec_room_id:
            self._draw_edit_polygon_overlay(painter, self._edit_elec_room_id)
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
        for cid, pts in self._polygons.items():
            if not self._circuit_visible.get(cid, True):
                continue
            if not self._label_visible.get(cid, True):
                continue
            color = self._color_map.get(cid, QColor("blue"))
            text = self._label_map.get(cid, cid)
            default_pos = QPointF(
                sum(p.x() for p in pts) / len(pts),
                sum(p.y() for p in pts) / len(pts))
            self._draw_item_label(painter, cid, default_pos, text, color)

        for rid, pts in self._elec_room_polygons.items():
            if not self._elec_room_visible.get(rid, True):
                continue
            if not self._label_visible.get(rid, True):
                continue
            if len(pts) < 3:
                continue
            text = self._label_map.get(rid, rid)
            default_pos = QPointF(
                sum(p.x() for p in pts) / len(pts),
                sum(p.y() for p in pts) / len(pts),
            )
            self._draw_item_label(
                painter,
                rid,
                default_pos,
                text,
                self._color_map.get(rid, QColor("#43aa8b")),
            )
        for pid in self._elec_points:
            if not self._elec_visible.get(pid, True):
                continue
            if not self._label_visible.get(pid, True):
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
            name_visible = self._label_visible.get(kid, True)
            text = self._label_map.get(kid, kid)
            col = self._color_map.get(kid, QColor("#ff9800"))
            if name_visible:
                self._draw_item_label(painter, kid, mid, text, col)

            type_label_id = self._elec_cable_type_label_id(kid)
            cable_type = self._elec_cable_type_text.get(kid, "")
            type_visible = self._elec_cable_type_label_visible.get(kid, False)
            if type_visible and cable_type:
                type_default_pos = QPointF(mid)
                if name_visible:
                    type_default_pos = QPointF(mid.x(), mid.y() + 14.0)
                self._draw_item_label(
                    painter,
                    type_label_id,
                    type_default_pos,
                    cable_type,
                    col,
                    visible_override=True,
                    size_override=self._label_font_sizes.get(kid, 12.0),
                )

        # HKV labels
        for hid in self._hkv_points:
            if not self._hkv_visible.get(hid, True):
                continue
            if not self._label_visible.get(hid, True):
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
            if not self._label_visible.get(lid, True):
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
        self._draw_angle_measurements(painter)
        self._draw_global_helper_lines(painter)
        self._calculate_helper_line_intersections()
        self._draw_helper_line_angles(painter)

        # ── Maße beim Verschieben anzeigen ────────────────────────
        self._draw_drag_distance_overlay(painter)

        # ── Export-Rahmen ──────────────────────────────────────────
        self._draw_export_frame(painter)

        # ── Ghost preview while placing objects ────────────────────
        self._draw_placement_ghost(painter)

        # ── Selection highlight ────────────────────────────────────
        self._draw_selection_highlight(painter)
        self._draw_hover_highlight(painter)
        # Multi-selection must be drawn in the main paint path so it is
        # always visible (not only in polygon edit overlays).
        self._draw_multi_selection_highlights(painter)

        painter.restore()

    def _draw_placement_ghost(self, painter: QPainter):
        if not self._ghost_preview_pos:
            return
        if self._mode not in (ToolMode.PLACE_ELEC_POINT, ToolMode.PLACE_HKV, ToolMode.PLACE_TEXT):
            return
        painter.save()
        painter.setOpacity(0.5)
        gp = self._ghost_preview_pos
        r = self._px_to_canvas_units(8.0)
        if self._mode == ToolMode.PLACE_ELEC_POINT and self._placing_elec_point_id:
            pid = self._placing_elec_point_id
            w, h = self._elec_point_size_px.get(pid, (30.0, 30.0))
            painter.setPen(QPen(QColor("#4fc3f7"), 2.0 / self._scale, Qt.DashLine))
            painter.setBrush(QBrush(QColor(79, 195, 247, 40)))
            painter.drawRect(QRectF(gp.x() - w / 2, gp.y() - h / 2, w, h))
        elif self._mode == ToolMode.PLACE_HKV and self._placing_hkv_id:
            hid = self._placing_hkv_id
            w, h = self._hkv_size_px.get(hid, (30.0, 30.0))
            painter.setPen(QPen(QColor("#e53935"), 2.0 / self._scale, Qt.DashLine))
            painter.setBrush(QBrush(QColor(229, 57, 53, 40)))
            painter.drawRect(QRectF(gp.x() - w / 2, gp.y() - h / 2, w, h))
        elif self._mode == ToolMode.PLACE_TEXT and self._placing_text_id:
            painter.setPen(QPen(QColor("#ffffff"), 2.0 / self._scale, Qt.DashLine))
            painter.setBrush(QBrush(QColor(255, 255, 255, 30)))
            size = self._px_to_canvas_units(20.0)
            painter.drawRect(QRectF(gp.x() - size, gp.y() - size * 0.7, size * 2, size * 1.4))
            font = painter.font()
            font.setPointSizeF(max(8.0 / self._scale, 6.0))
            painter.setFont(font)
            painter.drawText(QPointF(gp.x() - size * 0.2, gp.y() + size * 0.3), "T")
        else:
            painter.setPen(QPen(QColor("#ffffff"), 1.5 / self._scale, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(gp, r, r)
        painter.restore()

    def _draw_hover_highlight(self, painter: QPainter):
        if not self._hover_object:
            return
        obj_type, obj_id = self._hover_object
        painter.save()
        pen = QPen(QColor(255, 255, 255, 220), 2.0 / self._scale, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if obj_type == "polygon":
            pts = self._polygons.get(obj_id, [])
            if len(pts) >= 3:
                painter.drawPolygon(QPolygonF(pts))
        elif obj_type == "elec_room":
            pts = self._elec_room_polygons.get(obj_id, [])
            if len(pts) >= 3:
                painter.drawPolygon(QPolygonF(pts))
        elif obj_type == "elec_point":
            pos = self._elec_points.get(obj_id)
            if pos:
                w, h = self._elec_point_size_px.get(obj_id, (30.0, 30.0))
                painter.drawRect(QRectF(pos.x() - w / 2, pos.y() - h / 2, w, h))
        elif obj_type == "hkv":
            pos = self._hkv_points.get(obj_id)
            if pos:
                w, h = self._hkv_size_px.get(obj_id, (30.0, 30.0))
                painter.drawRect(QRectF(pos.x() - w / 2, pos.y() - h / 2, w, h))
        elif obj_type == "text":
            rect = self._text_rects.get(obj_id)
            if rect:
                painter.drawRect(rect)
        elif obj_type == "label":
            rect = self._label_rects.get(obj_id)
            if rect:
                painter.drawRect(rect)
        elif obj_type == "distance_measure":
            idx = self._measurement_obj_to_index(obj_id, "MSRD")
            if idx is not None and 0 <= idx < len(self._measure_lines):
                p1, p2, _mm_len = self._measure_lines[idx]
                painter.drawLine(p1, p2)
        elif obj_type == "angle_measure":
            idx = self._measurement_obj_to_index(obj_id, "MSRA")
            if idx is not None and 0 <= idx < len(self._angle_measurements):
                p1, p2, p3, _angle = self._angle_measurements[idx]
                painter.drawLine(p2, p1)
                painter.drawLine(p2, p3)
        painter.restore()

    # ── Measurement drawing ───────────────────────────────────────── #

    def _draw_measurements(self, painter: QPainter):
        self._normalize_measure_label_positions()
        r = 4.0 / self._scale
        base_font = painter.font()

        def draw_text_with_background(pt: QPointF, text: str, text_size_pt: float,
                                      label_id: Optional[str] = None,
                                      text_color: Optional[QColor] = None):
            font = QFont(base_font)
            font.setPointSizeF(max(1.0, float(text_size_pt)) / self._scale)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            pad = 3.0 / self._scale
            width = metrics.horizontalAdvance(text)
            height = metrics.height()
            ascent = metrics.ascent()
            rect = QRectF(
                pt.x() - pad,
                pt.y() - ascent - pad,
                width + 2 * pad,
                height + 2 * pad,
            )
            painter.fillRect(rect, QColor(0, 0, 0, 180))
            painter.setPen(QPen(text_color or color))
            painter.drawText(pt, text)
            if label_id:
                self._label_rects[label_id] = rect
                self._label_draw_pos[label_id] = QPointF(pt)

        # Draw persisted measurement lines
        for idx, (p1, p2, mm_len) in enumerate(self._measure_lines):
            measurement_id = f"MSRD-{idx + 1}"
            style = self._measurement_style(measurement_id)
            if not bool(style.get("visible", True)):
                continue
            color = QColor(str(style.get("color", self._measure_color)))
            pen = QPen(
                color,
                max(0.5, float(style.get("stroke_width", 2.0))) / self._scale,
                self._helper_line_pen_style(str(style.get("line_style", "dashdot"))),
            )
            painter.setPen(pen)
            painter.drawLine(p1, p2)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(p1, r, r)
            painter.drawEllipse(p2, r, r)
            if idx < len(self._measure_label_positions):
                lp = self._measure_label_positions[idx]
                anchor_pos = QPointF(lp[0], lp[1])
            else:
                anchor_pos = QPointF(p2.x(), p2.y())
            draw_text_with_background(
                anchor_pos,
                f"{mm_len / 1000:.3f} m",
                float(style.get("text_size", 10.0)),
                f"measure:{idx}",
                color,
            )
            if self._debug_measure_last_store_idx == idx:
                self._debug_measure_pos(
                    "DRAW-AFTER-STORE",
                    idx=idx,
                    measurement_id=measurement_id,
                    p2=f"({p2.x():.2f},{p2.y():.2f})",
                    anchor=f"({anchor_pos.x():.2f},{anchor_pos.y():.2f})",
                    text_size=f"{float(style.get('text_size', 10.0)):.2f}",
                    line_style=str(style.get("line_style", "dashdot")),
                    auto=bool(style.get("auto_label_pos", True)),
                )
                self._debug_measure_last_store_idx = None

        # Draw in-progress measurement
        if self._mode == ToolMode.MEASURE and self._measure_p1:
            p2 = self._mouse_pos if self._mouse_pos else self._measure_p1
            next_measurement_id = f"MSRD-{len(self._measure_lines) + 1}"
            next_style = self._measurement_style(next_measurement_id)
            color = QColor(str(next_style.get("color", self._measure_color)))
            pen = QPen(
                color,
                max(0.5, float(next_style.get("stroke_width", 2.0))) / self._scale,
                self._helper_line_pen_style(str(next_style.get("line_style", "dashdot"))),
            )
            painter.setPen(pen)
            painter.drawLine(self._measure_p1, p2)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(self._measure_p1, r, r)
            painter.drawEllipse(p2, r, r)
            if self._mm_per_px > 0:
                px_len = _qdist(self._measure_p1, p2)
                mm_len = px_len * self._mm_per_px
                preview_pos = QPointF(p2.x(), p2.y())
                draw_text_with_background(
                    preview_pos,
                    f"{mm_len / 1000:.3f} m",
                    float(next_style.get("text_size", 10.0)),
                    text_color=color,
                )

    def _draw_angle_measurements(self, painter: QPainter):
        r = 4.0 / self._scale
        base_font = painter.font()

        def draw_text_with_background(pt: QPointF, text: str, text_size_pt: float,
                                      color: QColor,
                                      label_id: Optional[str] = None):
            font = QFont(base_font)
            font.setPointSizeF(max(1.0, float(text_size_pt)) / self._scale)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            pad = 3.0 / self._scale
            width = metrics.horizontalAdvance(text)
            height = metrics.height()
            ascent = metrics.ascent()
            rect = QRectF(
                pt.x() - pad,
                pt.y() - ascent - pad,
                width + 2 * pad,
                height + 2 * pad,
            )
            painter.fillRect(rect, QColor(0, 0, 0, 180))
            painter.setPen(QPen(color))
            painter.drawText(pt, text)
            if label_id:
                self._label_rects[label_id] = rect
                self._label_draw_pos[label_id] = QPointF(pt)

        def draw_triplet(
            p1: QPointF,
            p2: QPointF,
            p3: QPointF,
            angle_deg: float,
            color: QColor,
            line_style: str,
            stroke_width: float,
            text_size: float,
            auto_label_pos: bool,
            label_id: Optional[str] = None,
        ):
            pen = QPen(
                color,
                max(0.5, float(stroke_width)) / self._scale,
                self._helper_line_pen_style(line_style),
            )
            painter.setPen(pen)
            painter.drawLine(p2, p1)
            painter.drawLine(p2, p3)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(p1, r, r)
            painter.drawEllipse(p2, r, r)
            painter.drawEllipse(p3, r, r)
            if auto_label_pos:
                label_pos = QPointF(
                    p3.x() + 10.0 / max(self._scale, 1e-9),
                    p3.y() - 6.0 / max(self._scale, 1e-9),
                )
            else:
                label_pos = QPointF(
                    p3.x() + 10.0 / max(self._scale, 1e-9),
                    p3.y() - 6.0 / max(self._scale, 1e-9),
                )
            if label_id and label_id.startswith("angle:"):
                try:
                    idx = int(label_id.split(":", 1)[1])
                except (TypeError, ValueError, IndexError):
                    idx = -1
                if 0 <= idx < len(self._angle_measure_label_positions):
                    lp = self._angle_measure_label_positions[idx]
                    label_pos = QPointF(lp[0], lp[1])
            draw_text_with_background(label_pos, f"{angle_deg:.1f}°", text_size, color, label_id)

        for idx, (p1, p2, p3, angle_deg) in enumerate(self._angle_measurements):
            measurement_id = f"MSRA-{idx + 1}"
            style = self._measurement_style(measurement_id)
            if not bool(style.get("visible", True)):
                continue
            color = QColor(str(style.get("color", self._measure_color)))
            draw_triplet(
                p1,
                p2,
                p3,
                angle_deg,
                color,
                str(style.get("line_style", "dashdot")),
                float(style.get("stroke_width", 2.0)),
                float(style.get("text_size", 10.0)),
                bool(style.get("auto_label_pos", True)),
                f"angle:{idx}",
            )

        if self._mode == ToolMode.MEASURE_ANGLE:
            if self._angle_measure_p1 is not None and self._angle_measure_p2 is not None:
                p1 = self._angle_measure_p1
                p2 = self._angle_measure_p2
                p3 = self._mouse_pos if self._mouse_pos is not None else self._angle_measure_p2
                v1x, v1y = p1.x() - p2.x(), p1.y() - p2.y()
                v2x, v2y = p3.x() - p2.x(), p3.y() - p2.y()
                l1 = math.hypot(v1x, v1y)
                l2 = math.hypot(v2x, v2y)
                angle_deg = 0.0
                if l1 > 1e-9 and l2 > 1e-9:
                    dot = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (l1 * l2)))
                    angle_deg = math.degrees(math.acos(dot))
                color = QColor(self._measure_color)
                draw_triplet(p1, p2, p3, angle_deg, color, "dashdot", 2.0, 10.0, True)
            elif self._angle_measure_p1 is not None:
                p2 = self._mouse_pos if self._mouse_pos is not None else self._angle_measure_p1
                color = QColor(self._measure_color)
                pen = QPen(color, 2.0 / self._scale, Qt.DashDotLine)
                painter.setPen(pen)
                painter.drawLine(self._angle_measure_p1, p2)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(self._angle_measure_p1, r, r)
                painter.drawEllipse(p2, r, r)

    def _draw_global_helper_lines(self, painter: QPainter):
        r = 3.5 / self._scale
        font = painter.font()
        font.setPointSizeF(10.0 / self._scale)

        def draw_text_with_background(pt: QPointF, text: str, text_color: QColor, label_id: Optional[str] = None):
            painter.setFont(font)
            metrics = painter.fontMetrics()
            pad = 3.0 / self._scale
            width = metrics.horizontalAdvance(text)
            height = metrics.height()
            ascent = metrics.ascent()
            rect = QRectF(
                pt.x() - pad,
                pt.y() - ascent - pad,
                width + 2 * pad,
                height + 2 * pad,
            )
            painter.fillRect(rect, QColor(0, 0, 0, 180))
            painter.setPen(QPen(text_color))
            painter.drawText(pt, text)
            if label_id:
                self._label_rects[label_id] = rect
                self._label_draw_pos[label_id] = QPointF(pt)

        draw_order: List[str] = []
        seen_floor_ids: set[str] = set()
        for fid in self._floor_plan_order:
            draw_order.append(fid)
            seen_floor_ids.add(fid)
        for fid in self._floor_helper_lines.keys():
            if fid not in seen_floor_ids:
                draw_order.append(fid)
                seen_floor_ids.add(fid)

        for fid in draw_order:
            layer = self._floor_plans.get(fid)
            is_forced_visible = fid in {
                self._helper_active_floor_id,
                self._helper_selected_floor_id,
            }
            if not layer:
                if not is_forced_visible:
                    continue
            elif not layer.visible and not is_forced_visible:
                continue
            settings = self._helper_settings(fid)
            if not bool(settings.get("visible", True)):
                continue
            color = QColor(str(settings.get("color", "#f8f32b")))
            width_px = max(0.5, float(settings.get("line_width_px", 2.0)))
            style = self._helper_line_pen_style(str(settings.get("line_style", "dash")))
            base_pen = QPen(color, width_px / self._scale, style)
            visible_map = self._floor_helper_line_visible.get(fid, {})
            for hid, pts in self._floor_helper_lines.get(fid, {}).items():
                if not visible_map.get(hid, True) or len(pts) < 2:
                    continue
                p1, p2 = pts[0], pts[1]
                pen = QPen(base_pen)
                if hid == self._helper_selected_id and fid == self._helper_selected_floor_id:
                    pen.setColor(QColor("#ffd166"))
                    pen.setWidthF(max(pen.widthF(), 3.0 / self._scale))
                painter.setPen(pen)
                painter.setBrush(QBrush(pen.color()))
                painter.drawLine(p1, p2)
                painter.drawEllipse(p1, r, r)
                painter.drawEllipse(p2, r, r)
                if self._helper_hover_endpoint is not None:
                    hf, hh, hi = self._helper_hover_endpoint
                    if hf == fid and hh == hid:
                        hover_pt = p1 if hi == 0 else p2
                        hover_pen = QPen(QColor("#ffffff"), 2.2 / self._scale, Qt.SolidLine)
                        painter.setPen(hover_pen)
                        painter.setBrush(Qt.NoBrush)
                        painter.drawEllipse(hover_pt, r * 1.8, r * 1.8)
                px_len = _qdist(p1, p2)
                mm_len = px_len * self._mm_per_px if self._mm_per_px > 0 else 0.0
                helper_id = f"helper:{fid}:{hid}"
                if fid in self._helper_label_positions and hid in self._helper_label_positions[fid]:
                    label_pos = QPointF(*self._helper_label_positions[fid][hid])
                else:
                    label_pos = QPointF((p1.x() + p2.x()) / 2,
                                         (p1.y() + p2.y()) / 2 - 10 / self._scale)
                draw_text_with_background(label_pos, f"{mm_len / 1000:.3f} m", pen.color(), helper_id)

        if self._mode == ToolMode.DRAW_HELPER_LINE and self._helper_draw_start and self._helper_draw_current:
            fid = self._resolve_draw_helper_floor(self._helper_active_floor_id)
            if not fid:
                return
            settings = self._helper_settings(fid)
            p1 = self._helper_draw_start
            p2 = self._helper_draw_current
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            direction_len = math.hypot(dx, dy)
            if direction_len > 1e-9 and self._mm_per_px > 0:
                target_px = float(settings.get("target_length_mm", self._helper_target_length_mm)) / self._mm_per_px
                ux = dx / direction_len
                uy = dy / direction_len
                p2 = QPointF(p1.x() + ux * target_px, p1.y() + uy * target_px)
            color = QColor(str(settings.get("color", "#f8f32b")))
            width_px = max(0.5, float(settings.get("line_width_px", 2.0)))
            style = self._helper_line_pen_style(str(settings.get("line_style", "dash")))
            base_pen = QPen(color, width_px / self._scale, style)
            painter.setPen(base_pen)
            painter.setBrush(QBrush(base_pen.color()))
            painter.drawLine(p1, p2)
            painter.drawEllipse(p1, r, r)
            painter.drawEllipse(p2, r, r)
            px_len = _qdist(p1, p2)
            mm_len = px_len * self._mm_per_px if self._mm_per_px > 0 else 0.0
            mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2 - 10 / self._scale)
            draw_text_with_background(mid, f"{mm_len / 1000:.3f} m", base_pen.color())

    def _calculate_helper_line_intersections(self):
        """Berechne Schnittpunkte und Winkel zwischen allen Hilfslinien auf aktiven Floors."""
        self._helper_line_intersections.clear()
        
        for fid in self._floor_plan_order:
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            
            helper_lines = self._floor_helper_lines.get(fid, {})
            hid_list = list(helper_lines.items())
            
            # Prüfe alle Paare von Hilfslinien
            for i, (hid1, pts1) in enumerate(hid_list):
                if len(pts1) < 2:
                    continue
                for j, (hid2, pts2) in enumerate(hid_list[i+1:], start=i+1):
                    if len(pts2) < 2:
                        continue
                    
                    # Berechne Schnittpunkt
                    p1_start, p1_end = pts1[0], pts1[1]
                    p2_start, p2_end = pts2[0], pts2[1]
                    
                    intersection_pt = _line_line_intersection(p1_start, p1_end, p2_start, p2_end)
                    if intersection_pt is None:
                        continue
                    
                    # Prüfe, ob der Schnittpunkt innerhalb beider Strecken liegt
                    proj1 = _project_on_segment(intersection_pt, p1_start, p1_end)
                    proj2 = _project_on_segment(intersection_pt, p2_start, p2_end)
                    
                    dist1 = _qdist(intersection_pt, proj1)
                    dist2 = _qdist(intersection_pt, proj2)
                    
                    # Toleranz für Schnittpunkt-Position
                    tol = 0.5 / self._scale if self._scale > 0 else 1.0
                    if dist1 > tol or dist2 > tol:
                        continue
                    
                    # Berechne Winkel zwischen den Linien
                    v1x = p1_end.x() - p1_start.x()
                    v1y = p1_end.y() - p1_start.y()
                    v2x = p2_end.x() - p2_start.x()
                    v2y = p2_end.y() - p2_start.y()
                    
                    len1 = math.hypot(v1x, v1y)
                    len2 = math.hypot(v2x, v2y)
                    
                    if len1 > 1e-9 and len2 > 1e-9:
                        dot = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (len1 * len2)))
                        angle_rad = math.acos(dot)
                        angle_deg = math.degrees(angle_rad)
                        # Wähle den kleineren Winkel (akut oder recht)
                        if angle_deg > 90:
                            angle_deg = 180 - angle_deg
                        
                        self._helper_line_intersections.append((intersection_pt, angle_deg, hid1, hid2, fid))

            # Live-Vorschau: während des Zeichnens die aktuelle Hilfslinie gegen bestehende prüfen
            if (
                self._mode == ToolMode.DRAW_HELPER_LINE
                and self._helper_draw_start is not None
                and self._helper_draw_current is not None
                and fid == self._helper_active_floor_id
            ):
                preview_p1 = QPointF(self._helper_draw_start)
                preview_p2 = QPointF(self._helper_draw_current)

                dx = preview_p2.x() - preview_p1.x()
                dy = preview_p2.y() - preview_p1.y()
                direction_len = math.hypot(dx, dy)
                if direction_len > 1e-9 and self._mm_per_px > 0:
                    settings = self._helper_settings(fid)
                    target_px = float(settings.get("target_length_mm", self._helper_target_length_mm)) / self._mm_per_px
                    ux = dx / direction_len
                    uy = dy / direction_len
                    preview_p2 = QPointF(preview_p1.x() + ux * target_px, preview_p1.y() + uy * target_px)

                visible_map = self._floor_helper_line_visible.get(fid, {})
                for hid, pts in helper_lines.items():
                    if len(pts) < 2 or not visible_map.get(hid, True):
                        continue

                    p2_start, p2_end = pts[0], pts[1]
                    intersection_pt = _line_line_intersection(preview_p1, preview_p2, p2_start, p2_end)
                    if intersection_pt is None:
                        continue

                    proj_preview = _project_on_segment(intersection_pt, preview_p1, preview_p2)
                    proj_existing = _project_on_segment(intersection_pt, p2_start, p2_end)
                    dist_preview = _qdist(intersection_pt, proj_preview)
                    dist_existing = _qdist(intersection_pt, proj_existing)

                    tol = 0.5 / self._scale if self._scale > 0 else 1.0
                    if dist_preview > tol or dist_existing > tol:
                        continue

                    v1x = preview_p2.x() - preview_p1.x()
                    v1y = preview_p2.y() - preview_p1.y()
                    v2x = p2_end.x() - p2_start.x()
                    v2y = p2_end.y() - p2_start.y()
                    len1 = math.hypot(v1x, v1y)
                    len2 = math.hypot(v2x, v2y)

                    if len1 > 1e-9 and len2 > 1e-9:
                        dot = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (len1 * len2)))
                        angle_deg = math.degrees(math.acos(dot))
                        if angle_deg > 90:
                            angle_deg = 180 - angle_deg
                        self._helper_line_intersections.append((intersection_pt, angle_deg, "__preview__", hid, fid))

    def _draw_helper_line_angles(self, painter: QPainter):
        """Zeichne Winkel-Markierungen an Hilfslinienschnittpunkten."""
        if not self._helper_line_intersections:
            return
        
        color = QColor("#ffeb3b")  # Gelb für Winkel-Markierungen
        font = painter.font()
        font.setPointSizeF(9.0 / self._scale)
        r = 5.0 / self._scale
        arc_radius = 15.0 / self._scale
        
        pen = QPen(color, 1.5 / self._scale)
        pen.setDashPattern([4.0, 4.0])
        
        for intersection_pt, angle_deg, hid1, hid2, fid in self._helper_line_intersections:
            painter.setPen(pen)
            painter.setBrush(QBrush(color))
            
            # Zeichne Kreis am Schnittpunkt
            painter.drawEllipse(intersection_pt, r, r)
            
            # Zeichne einen Winkelbogen (optional, je nach Anforderung)
            # Für jetzt: einfach Text mit Winkel
            
            # Text mit Hintergrund
            label_text = f"{angle_deg:.1f}°"
            painter.setFont(font)
            metrics = painter.fontMetrics()
            pad = 2.0 / self._scale
            width = metrics.horizontalAdvance(label_text)
            height = metrics.height()
            ascent = metrics.ascent()
            
            text_pt = QPointF(
                intersection_pt.x() + arc_radius + 3.0 / self._scale,
                intersection_pt.y() - arc_radius - 3.0 / self._scale
            )
            
            rect = QRectF(
                text_pt.x() - pad,
                text_pt.y() - ascent - pad,
                width + 2 * pad,
                height + 2 * pad,
            )
            
            painter.fillRect(rect, QColor(0, 0, 0, 200))
            painter.setPen(QPen(color))
            painter.drawText(text_pt, label_text)

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

    def _draw_selection_highlight(self, painter: QPainter):
        """Draw a highlight around the currently selected item from treeview."""
        if not self._selected_item_id or not self._selected_item_type:
            return

        item_id = self._selected_item_id
        item_type = self._selected_item_type
        highlight_color = QColor("#ffff00")  # Yellow highlight
        highlight_color.setAlpha(200)
        pen = QPen(highlight_color, 4.0 / self._scale, Qt.SolidLine)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # Draw highlight based on type
        if item_type == "polygon" and item_id in self._polygons:
            pts = self._polygons[item_id]
            if len(pts) >= 3:
                poly = QPolygonF(pts)
                painter.drawPolygon(poly)

        elif item_type == "elec_room" and item_id in self._elec_room_polygons:
            pts = self._elec_room_polygons[item_id]
            if len(pts) >= 3:
                poly = QPolygonF(pts)
                painter.drawPolygon(poly)

        elif item_type == "circuit" and item_id in self._start_points:
            sp = self._start_points[item_id]
            r = 15.0 / self._scale
            painter.drawEllipse(sp, r, r)

        elif item_type == "elec_point" and item_id in self._elec_points:
            pos = self._elec_points[item_id]
            w, h = self._elec_point_size_px.get(item_id, (30, 30))
            padding = 5.0 / self._scale
            rect = QRectF(pos.x() - w/2 - padding, pos.y() - h/2 - padding,
                         w + 2*padding, h + 2*padding)
            painter.drawRect(rect)

        elif item_type == "hkv" and item_id in self._hkv_points:
            pos = self._hkv_points[item_id]
            w, h = self._hkv_size_px.get(item_id, (30, 30))
            padding = 5.0 / self._scale
            rect = QRectF(pos.x() - w/2 - padding, pos.y() - h/2 - padding,
                         w + 2*padding, h + 2*padding)
            painter.drawRect(rect)

        elif item_type == "route" and item_id in self._manual_routes:
            pts = self._manual_routes[item_id]
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))

        elif item_type == "supply_line" and item_id in self._supply_lines:
            pts = self._supply_lines[item_id]
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))

        elif item_type == "elec_cable" and item_id in self._elec_cables:
            pts = self._elec_cables[item_id]
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))

        elif item_type == "hkv_line" and item_id in self._hkv_lines:
            pts = self._hkv_lines[item_id]
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))

        elif item_type == "helper_line" and self._helper_selected_floor_id:
            pts = self._floor_helper_lines.get(self._helper_selected_floor_id, {}).get(item_id, [])
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))
                r = 6.0 / self._scale
                painter.drawEllipse(pts[0], r, r)
                painter.drawEllipse(pts[1], r, r)

        elif item_type == "text" and item_id in self._text_annotations:
            pos = self._coerce_canvas_point(self._text_annotations[item_id])
            if pos is not None:
                self._text_annotations[item_id] = pos
                r = 8.0 / self._scale
                painter.drawEllipse(pos, r, r)

        elif item_type == "distance_measure":
            idx = self._measurement_obj_to_index(item_id, "MSRD")
            if idx is not None and 0 <= idx < len(self._measure_lines):
                p1, p2, _mm_len = self._measure_lines[idx]
                painter.drawLine(p1, p2)
                r = 7.0 / self._scale
                painter.drawEllipse(p1, r, r)
                painter.drawEllipse(p2, r, r)

        elif item_type == "angle_measure":
            idx = self._measurement_obj_to_index(item_id, "MSRA")
            if idx is not None and 0 <= idx < len(self._angle_measurements):
                p1, p2, p3, _angle = self._angle_measurements[idx]
                painter.drawLine(p2, p1)
                painter.drawLine(p2, p3)
                r = 7.0 / self._scale
                painter.drawEllipse(p1, r, r)
                painter.drawEllipse(p2, r, r)
                painter.drawEllipse(p3, r, r)

    # ── Text Annotations drawing ─────────────────────────────────── #

    def _draw_text_annotations(self, painter: QPainter):
        """Render all visible text annotations on the canvas."""
        self._text_rects.clear()
        for tid, pos in self._text_annotations.items():
            if not self._text_visible.get(tid, True):
                continue
            pos_pt = self._coerce_canvas_point(pos)
            if pos_pt is None:
                continue
            if not isinstance(pos, QPointF):
                self._text_annotations[tid] = pos_pt
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
            bg_rect = QRectF(pos_pt.x() - pad,
                             pos_pt.y() - fm.ascent() - pad,
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
                    QPointF(pos_pt.x(), pos_pt.y() + i * line_height), line)
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
                          default_pos: QPointF, text: str, color: QColor,
                          visible_override: Optional[bool] = None,
                          size_override: Optional[float] = None):
        if visible_override is None and not self._label_visible.get(item_id, True):
            return
        if visible_override is False:
            return
        pos = self._label_positions.get(item_id, default_pos)
        size = self._label_font_sizes.get(item_id, 12.0)
        if size_override is not None:
            size = size_override
        label_text = str(text or "")
        lines = label_text.split("\n") if label_text else [""]
        font = painter.font()
        font.setPointSizeF(size / self._scale)
        painter.setFont(font)
        # background for readability
        fm = painter.fontMetrics()
        tw = max((fm.horizontalAdvance(line) for line in lines), default=0)
        line_h = fm.height()
        th = line_h * len(lines)
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
        for index, line in enumerate(lines):
            painter.drawText(QPointF(pos.x(), pos.y() + index * line_h), line)
        # store for hit testing & dragging
        self._label_rects[item_id] = bg_rect
        self._label_draw_pos[item_id] = pos

    def _hit_label(self, canvas_pt: QPointF) -> Optional[str]:
        for item_id, rect in self._label_rects.items():
            if rect.contains(canvas_pt):
                return item_id
        return None

    def _hit_distance_measurement(self, canvas_pt: QPointF) -> Optional[int]:
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
        for idx, (p1, p2, _mm_len) in enumerate(self._measure_lines):
            proj = _project_on_segment(canvas_pt, p1, p2)
            if _qdist(canvas_pt, proj) < threshold:
                return idx
        return None

    def _hit_angle_measurement(self, canvas_pt: QPointF) -> Optional[int]:
        threshold = self._px_to_canvas_units(HIT_EDGE_RADIUS_PX)
        for idx, (p1, p2, p3, _angle) in enumerate(self._angle_measurements):
            proj1 = _project_on_segment(canvas_pt, p2, p1)
            proj2 = _project_on_segment(canvas_pt, p2, p3)
            if _qdist(canvas_pt, proj1) < threshold or _qdist(canvas_pt, proj2) < threshold:
                return idx
        return None

    def _hit_distance_measurement_point(self, canvas_pt: QPointF) -> Optional[Tuple[str, int]]:
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for idx, (p1, p2, _mm_len) in enumerate(self._measure_lines):
            if _qdist(canvas_pt, p1) < threshold:
                return (f"MSRD-{idx + 1}", 0)
            if _qdist(canvas_pt, p2) < threshold:
                return (f"MSRD-{idx + 1}", 1)
        return None

    def _hit_angle_measurement_point(self, canvas_pt: QPointF) -> Optional[Tuple[str, int]]:
        threshold = self._px_to_canvas_units(HIT_POINT_RADIUS_PX)
        for idx, (p1, p2, p3, _angle) in enumerate(self._angle_measurements):
            if _qdist(canvas_pt, p1) < threshold:
                return (f"MSRA-{idx + 1}", 0)
            if _qdist(canvas_pt, p2) < threshold:
                return (f"MSRA-{idx + 1}", 1)
            if _qdist(canvas_pt, p3) < threshold:
                return (f"MSRA-{idx + 1}", 2)
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
        r = 4.0 / self._scale
        pen_style = Qt.DashLine

        # Draw per-floor-plan ref lines (completed calibrations)
        drawn_floor_ids = set()
        for fid in self._floor_plan_order:
            layer = self._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            # Check if ref line is visible for this floor plan
            if not self.get_ref_line_visible(fid):
                continue
            if layer.ref_p1 and layer.ref_p2:
                # Skip the currently-being-drawn ref (shown via _ref_p1/_ref_p2)
                if self._ref_floor_id == fid and self._mode == ToolMode.DRAW_REF:
                    continue
                drawn_floor_ids.add(fid)
                color = QColor(self.get_ref_line_color(fid))
                pen = QPen(color, 2.0 / self._scale, pen_style)
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
            if self._mode != ToolMode.DRAW_REF and not self.get_ref_line_visible(self._ref_floor_id):
                return
        # If floor plans exist but no _ref_floor_id is set, the global
        # ref line is an orphan — don't draw it (each floor plan has its own).
        if self._floor_plans and not self._ref_floor_id and self._mode != ToolMode.DRAW_REF:
            return
        # Outside of draw mode, completed per-floor lines are already rendered above.
        # Do not draw the global fallback line, as it can override per-floor color/visibility.
        if self._mode != ToolMode.DRAW_REF:
            return
        if self._ref_p1 is None:
            return
        p2 = self._ref_p2 if self._ref_p2 else self._mouse_pos
        if p2 is None:
            return
        color = QColor(self.get_ref_line_color(self._ref_floor_id or ""))
        pen = QPen(color, 2.0 / self._scale, pen_style)
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
            path_key = (
                tuple((p.x(), p.y()) for p in points),
                line_dist,
            )
            cached = self._manual_route_path_cache.get(cid)
            if cached and cached[0] == path_key:
                path = cached[1]
            else:
                path = self._smooth_polyline_path(combined, offset)
                self._manual_route_path_cache[cid] = (path_key, path)
            painter.drawPath(path)

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
        painter.setPen(QPen(QColor(color), 2.0 / self._scale))
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
        sw = self._elec_cable_stroke_width.get(cable_id, 2.0)
        pen = QPen(color, sw / self._scale)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        rounding = 8.0 / self._scale
        path_key = (
            tuple((p.x(), p.y()) for p in points),
            round(self._scale, 6),
        )
        cached = self._elec_cable_path_cache.get(cable_id)
        if cached and cached[0] == path_key:
            path = cached[1]
        else:
            path = self._smooth_polyline_path(points, rounding)
            self._elec_cable_path_cache[cable_id] = (path_key, path)
        painter.drawPath(path)
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
        sw = self._elec_cable_stroke_width.get(
            self._current_elec_cable_id, 2.0)
        pen = QPen(color, sw / self._scale, Qt.DashLine)
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
            path_key = (
                tuple((p.x(), p.y()) for p in points),
                line_dist,
            )
            cached = self._supply_line_path_cache.get(cid)
            if cached and cached[0] == path_key:
                path = cached[1]
            else:
                path = self._smooth_polyline_path(combined, offset)
                self._supply_line_path_cache[cid] = (path_key, path)
            painter.drawPath(path)

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
        path_key = (
            tuple((p.x(), p.y()) for p in points),
            round(self._scale, 6),
        )
        cached = self._hkv_line_path_cache.get(lid)
        if cached and cached[0] == path_key:
            path = cached[1]
        else:
            path = QPainterPath()
            if len(line1) > 1:
                path.moveTo(line1[0])
                for p in line1[1:]:
                    path.lineTo(p)
            if len(line2) > 1:
                path.moveTo(line2[0])
                for p in line2[1:]:
                    path.lineTo(p)
            if line1 and line2:
                path.moveTo(line1[-1])
                path.lineTo(line2[-1])
                path.moveTo(line1[0])
                path.lineTo(line2[0])
            self._hkv_line_path_cache[lid] = (path_key, path)
        painter.drawPath(path)
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

    def _draw_multiselect_overlay(self, painter: QPainter):
        if self._mode not in (
            ToolMode.EDIT_POLYGON,
            ToolMode.EDIT_ROUTE,
            ToolMode.EDIT_ELEC_CABLE,
            ToolMode.EDIT_SUPPLY_LINE,
            ToolMode.EDIT_HKV_LINE,
        ):
            return

        if self._edit_selection_rect_start is not None:
            start = self._edit_selection_rect_start
            end = self._edit_selection_rect_end or start
            rect = QRectF(start, end).normalized()
            painter.save()
            painter.setBrush(QBrush(QColor(100, 180, 255, 40)))
            painter.setPen(QPen(QColor("#64b5f6"), 1.2 / self._scale, Qt.DashLine))
            painter.drawRect(rect)
            painter.restore()

        if not self._edit_selected_owner or not self._edit_selected_indices:
            return

        points_data = self._get_multiselect_points_world()
        if not points_data or points_data[0] != self._edit_selected_owner:
            return
        _, pts = points_data
        painter.save()
        r = 7.0 / self._scale
        painter.setPen(QPen(QColor("#ffffff"), 1.0 / self._scale))
        painter.setBrush(QBrush(QColor("#ffd166")))
        for idx in self._edit_selected_indices:
            if 0 <= idx < len(pts):
                painter.drawEllipse(pts[idx], r, r)
        painter.restore()

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

    def _draw_multi_selection_highlights(self, painter):
        """Draw highlights for multi-selected objects and active box-selection rect."""
        # Draw box-selection rectangle
        if self._selection_rect:
            pen = QPen(QColor("#ffdd00"), 2.0 / self._scale, Qt.DashLine)
            painter.setPen(pen)
            brush = QBrush(QColor(255, 221, 0, 40))  # semi-transparent yellow
            painter.setBrush(brush)
            painter.drawRect(self._selection_rect)

        # Draw highlights for multi-selected objects
        if not self._multi_selected:
            return
        
        pen = QPen(QColor("#ff9900"), 3.5 / self._scale, Qt.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        for obj_type, obj_id in self._multi_selected:
            if obj_type == "elec_point":
                pt = self._elec_points.get(obj_id)
                if pt:
                    w, h = self._elec_point_size_px.get(obj_id, (30.0, 30.0))
                    rect = QRectF(pt.x() - w / 2 - 3, pt.y() - h / 2 - 3, w + 6, h + 6)
                    painter.drawRect(rect)

            elif obj_type == "hkv":
                pt = self._hkv_points.get(obj_id)
                if pt:
                    w, h = self._hkv_size_px.get(obj_id, (40.0, 40.0))
                    rect = QRectF(pt.x() - w / 2 - 3, pt.y() - h / 2 - 3, w + 6, h + 6)
                    painter.drawRect(rect)

            elif obj_type == "text":
                rect = self._text_rects.get(obj_id)
                if rect:
                    painter.drawRect(rect.adjusted(-3, -3, 3, 3))

            elif obj_type == "elec_cable":
                pts = self._elec_cables.get(obj_id, [])
                if len(pts) >= 2:
                    dashed_pen = QPen(QColor("#ff9900"), 2.5 / self._scale, Qt.DashLine)
                    painter.setPen(dashed_pen)
                    for i in range(len(pts) - 1):
                        painter.drawLine(pts[i], pts[i + 1])
                    # Draw points
                    solid_pen = QPen(QColor("#ff9900"), 3.5 / self._scale)
                    painter.setPen(solid_pen)
                    for pt in pts:
                        r = 3.0 / self._scale
                        painter.drawEllipse(pt, r, r)

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
            # Visualise the spacing exactly like the route constraints:
            # configured loop spacing between Vorlauf/Ruecklauf and the
            # same spacing to the left and right of the loop.
            min_center_dist = 1.5 * line_dist
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
def _snap_to_helper_line_points(canvas_obj: 'CanvasWidget', 
                                 pt: QPointF, 
                                 current_floor_id: Optional[str],
                                 snap_radius_px: float = 15.0,
                                 exclude_line_id: str = "") -> Optional[QPointF]:
    """
    Snappet einen Punkt zu naheliegenden Endpunkten anderer Hilfslinien auf dem aktuellen Floor.
    
    Args:
        canvas_obj: Das Canvas-Widget
        pt: Der zu prüfende Punkt in Canvas-Pixeln
        current_floor_id: Die aktuelle Floor-ID
        snap_radius_px: Der Snap-Radius in Pixeln (default 15px)
        exclude_line_id: Eine Hilfslinie-ID ausschließen (z.B. die aktuelle Linie)
    
    Returns:
        Der gesnappte Punkt oder None, wenn kein Snap stattfand
    """
    if not current_floor_id or snap_radius_px <= 0:
        return None
    
    # Hole alle Hilfslinien auf diesem Floor
    helper_lines = canvas_obj._floor_helper_lines.get(current_floor_id, {})
    
    best_dist = snap_radius_px
    snapped_pt = None
    
    for hid, pts in helper_lines.items():
        if hid == exclude_line_id or len(pts) < 2:
            continue
        
        # Prüfe beide Endpunkte
        for endpoint in [pts[0], pts[1]]:
            dist = _qdist(pt, endpoint)
            if dist < best_dist:
                best_dist = dist
                snapped_pt = endpoint
    
    return snapped_pt