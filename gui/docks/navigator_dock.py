"""Navigator: Baumansicht aller tatsächlich vorhandenen Projektelemente."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QSettings, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QPushButton,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from model.document import Document
from model.elements import (
    ELEMENT_TYPES,
    Element,
    ElecPoint,
    ElecRoom,
    ElecCable,
    Hkv,
    HkvLine,
    Furniture,
    TextAnnotation,
    DistanceMeasurement,
    AngleMeasurement,
)
from model.layers import LayerId

_ID_ROLE = Qt.UserRole + 1
_KIND_ROLE = Qt.UserRole + 2
_BASE_BRUSH_ROLE = Qt.UserRole + 10

_ACTIVE_COLOR = QColor("#4fc3f7")


_HEATING_GROUP = (
    "Heizung",
    (
        ("Heizkreisverteiler", ("Hkv", "HkvLine")),
        ("Heizkreise", ("Circuit",)),
    ),
)
_ELECTRICAL_GROUP = (
    "Elektro",
    (
        ("Räume", ("ElecRoom",)),
        ("Anschlusspunkte", ("ElecPoint",)),
        ("Kabel", ("ElecCable",)),
    ),
)
_ANNOTATION_GROUP = (
    "Annotationen",
    (
        "TextAnnotation",
        "DistanceMeasurement",
        "AngleMeasurement",
    ),
)

_HELPER_NAV_ID_PREFIX = "NAV-HLP::"


def make_helper_nav_id(floor_plan_id: str, helper_id: str) -> str:
    return f"{_HELPER_NAV_ID_PREFIX}{floor_plan_id}::{helper_id}"


def parse_helper_nav_id(nav_id: str) -> tuple[str, str] | None:
    if not nav_id.startswith(_HELPER_NAV_ID_PREFIX):
        return None
    payload = nav_id[len(_HELPER_NAV_ID_PREFIX):]
    floor_id, sep, helper_id = payload.partition("::")
    if not sep or not floor_id or not helper_id:
        return None
    return floor_id, helper_id


def _helper_sort_key(helper_id: str) -> tuple[int, int | str]:
    suffix = helper_id.rsplit("-", 1)[-1]
    if suffix.isdigit():
        return (0, int(suffix))
    return (1, helper_id.lower())


def _index_element_types() -> dict[str, type[Element]]:
    mapping = {cls.__name__: cls for cls in ELEMENT_TYPES}
    return mapping


_ELEMENT_TYPES_BY_NAME = _index_element_types()


class _NavigatorTree(QTreeWidget):
    """QTreeWidget mit Drag-and-Drop für Grundriss-Umzüge.

    Elemente (kind=element/helper_line) können auf Grundriss-Items
    (kind=floorplan) gezogen werden.  Das ``drop_onto_floorplan``-Signal
    wird mit (element_id, floorplan_id) emittiert.
    """

    drop_onto_floorplan = Signal(str, str)
    reorder_floorplans = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self._drag_item_id: str = ""
        self._drag_item_kind: str = ""
        self._drag_highlight_item: QTreeWidgetItem | None = None

    # -- highlight helpers --------------------------------------------
    def _clear_highlight(self) -> None:
        if self._drag_highlight_item is not None:
            try:
                self._drag_highlight_item.setBackground(0, QBrush())
            except RuntimeError:
                pass
            self._drag_highlight_item = None

    def _set_highlight(self, item: QTreeWidgetItem | None) -> None:
        if item is self._drag_highlight_item:
            return
        self._clear_highlight()
        if item is not None:
            try:
                item.setBackground(0, QBrush(QColor("#1a5276")))
                self._drag_highlight_item = item
            except RuntimeError:
                self._drag_highlight_item = None

    # -- QAbstractItemView overrides ----------------------------------
    def startDrag(self, supported_actions) -> None:
        items = self.selectedItems()
        if not items:
            return
        item = items[0]
        kind = item.data(0, _KIND_ROLE)
        if kind not in ("element", "helper_line", "floorplan"):
            self._drag_item_id = ""
            self._drag_item_kind = ""
            return
        element_id = item.data(0, _ID_ROLE) or ""
        if not element_id:
            self._drag_item_id = ""
            self._drag_item_kind = ""
            return
        self._drag_item_id = element_id
        self._drag_item_kind = str(kind)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(element_id)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)
        self._drag_item_id = ""
        self._drag_item_kind = ""
        self._clear_highlight()

    def dragEnterEvent(self, event) -> None:
        if self._drag_item_id:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._drag_item_id:
            self._clear_highlight()
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        if target is None or target.data(0, _KIND_ROLE) != "floorplan":
            self._clear_highlight()
            event.ignore()
            return

        target_id = target.data(0, _ID_ROLE) or ""
        if self._drag_item_kind == "floorplan" and target_id == self._drag_item_id:
            self._clear_highlight()
            event.ignore()
            return

        if self._drag_item_kind in ("element", "helper_line", "floorplan"):
            self._set_highlight(target)
            event.acceptProposedAction()
        else:
            self._clear_highlight()
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._clear_highlight()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._clear_highlight()
        if not self._drag_item_id:
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        if target is not None and target.data(0, _KIND_ROLE) == "floorplan":
            fp_id = target.data(0, _ID_ROLE) or ""
            if fp_id:
                if self._drag_item_kind in ("element", "helper_line"):
                    self.drop_onto_floorplan.emit(self._drag_item_id, fp_id)
                elif self._drag_item_kind == "floorplan" and self._drag_item_id != fp_id:
                    self.reorder_floorplans.emit(self._drag_item_id, fp_id)
        self._drag_item_id = ""
        self._drag_item_kind = ""
        event.accept()
        # Kein super().dropEvent() — verhindert das eingebaute Item-Verschieben.


class NavigatorDock(QDockWidget):
    """Zeigt Grundrisse mit ihren Elementen; leere Kategorien entfallen."""

    element_selected = Signal(str)
    selection_changed = Signal(list)
    floorplan_activated = Signal(str)
    visibility_changed = Signal(str, bool)
    context_requested = Signal(str, str, object)
    reassign_floorplan = Signal(str, str)  # element_id, new_fp_id
    floorplan_order_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Navigator", parent)
        self.setObjectName("dock_navigator")
        self._document: Document | None = None
        self._selectable_layers: set[LayerId] = set(LayerId)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._selected_ids: list[str] = []
        self._suspend_item_events = False
        self._expanded_state_key = "navigator/expanded_paths"

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._filter = QLineEdit(container)
        self._filter.setPlaceholderText("Filtern…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self._filter, 1)

        self._collapse_all_button = QPushButton("Alle zuklappen", container)
        self._collapse_all_button.clicked.connect(self._collapse_all)
        controls.addWidget(self._collapse_all_button)

        layout.addLayout(controls)

        self._tree = _NavigatorTree(container)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setStyleSheet(
            "QTreeWidget::item:selected { background-color: #4fc3f7; color: #0b1d2a; }"
        )
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemExpanded.connect(self._on_item_expanded_changed)
        self._tree.itemCollapsed.connect(self._on_item_expanded_changed)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.drop_onto_floorplan.connect(self.reassign_floorplan)
        self._tree.reorder_floorplans.connect(self._on_floorplan_reordered)
        layout.addWidget(self._tree, 1)

        self.setWidget(container)

    # ------------------------------------------------------------------
    def set_document(self, document: Document | None) -> None:
        if self._document is not None:
            self._document.structure_changed.disconnect(self.rebuild)
            self._document.element_added.disconnect(self._on_element_event)
            self._document.element_removed.disconnect(self._on_element_event)
            self._document.element_changed.disconnect(self._on_element_changed)
            self._document.active_floorplan_changed.disconnect(self._on_active_changed)
        self._document = document
        if document is not None:
            document.structure_changed.connect(self.rebuild)
            document.element_added.connect(self._on_element_event)
            document.element_removed.connect(self._on_element_event)
            document.element_changed.connect(self._on_element_changed)
            document.active_floorplan_changed.connect(self._on_active_changed)
        self.rebuild()

    def set_selectable_layers(self, layers: set[LayerId]) -> None:
        """Setzt den Workspace-Filter; nicht aktive Layer werden abgeblendet."""
        self._selectable_layers = set(layers)
        self._apply_selectability()

    # ------------------------------------------------------------------
    def rebuild(self, *_args) -> None:
        self._suspend_item_events = True
        self._tree.blockSignals(True)
        self._tree.clear()
        self._items.clear()
        self._selected_ids = []
        document = self._document
        if document is None:
            self._tree.blockSignals(False)
            self._suspend_item_events = False
            return

        for fp_id in document.floorplan_order:
            floorplan = document.floorplans.get(fp_id)
            if floorplan is None:
                continue
            fp_item = QTreeWidgetItem(self._tree, [floorplan.name or fp_id])
            fp_item.setData(0, _ID_ROLE, fp_id)
            fp_item.setData(0, _KIND_ROLE, "floorplan")
            fp_item.setFlags(
                fp_item.flags()
                | Qt.ItemIsUserCheckable
                | Qt.ItemIsDropEnabled
                | Qt.ItemIsDragEnabled
            )
            fp_item.setCheckState(0, self._to_state(document.is_visible(fp_id)))
            self._items[fp_id] = fp_item

            self._build_floorplan_groups(fp_item, fp_id)

            self._refresh_branch_state(fp_item)
            fp_item.setExpanded(True)

        self._apply_selectability()
        self._highlight_active()
        self._apply_filter(self._filter.text())
        self._restore_expanded_state()
        self._tree.blockSignals(False)
        self._suspend_item_events = False

    def _add_element_item(self, parent: QTreeWidgetItem, element: Element) -> None:
        label = element.name or element.id
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, _ID_ROLE, element.id)
        item.setData(0, _KIND_ROLE, "element")
        item.setData(0, _KIND_ROLE + 1, type(element).LAYER.value)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        item.setCheckState(
            0,
            self._to_state(self._document.is_visible(element.id) if self._document else True),
        )
        color = element.color
        if color:
            base_brush = QBrush(QColor(color))
        else:
            # Kein Element-Farbe gesetzt → helles Grau für dunklen Hintergrund
            base_brush = QBrush(QColor("#cccccc"))
        item.setForeground(0, base_brush)
        item.setData(0, _BASE_BRUSH_ROLE, base_brush)
        item.setToolTip(0, "Ziehen: auf Grundriss ablegen, um umzuordnen")
        self._items[element.id] = item

    def _new_group_item(self, parent: QTreeWidgetItem, label: str, layer: LayerId) -> QTreeWidgetItem:
        group = QTreeWidgetItem(parent, [label])
        group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
        group.setFlags(group.flags() | Qt.ItemIsUserCheckable)
        group.setData(0, _KIND_ROLE, "category")
        group.setData(0, _ID_ROLE, layer.value)
        return group

    def _build_floorplan_groups(self, fp_item: QTreeWidgetItem, fp_id: str) -> None:
        document = self._document
        if document is None:
            return

        self._build_nested_group(fp_item, fp_id, _HEATING_GROUP, LayerId.HEATING)
        self._build_electrical_group(fp_item, fp_id)

        furniture = [
            f for f in document.furniture.values()
            if f.floor_plan_id in ("", fp_id)
        ]
        if furniture:
            furn_group = self._new_group_item(fp_item, Furniture.CATEGORY_LABEL, LayerId.FURNITURE)
            for element in sorted(furniture, key=_sort_key):
                self._add_element_item(furn_group, element)
            self._refresh_branch_state(furn_group)

        self._build_annotation_group(fp_item, fp_id)

    def _iter_helper_lines_for_floor(self, fp_id: str) -> list[tuple[str, bool, str, float | None]]:
        """Return helper line metadata for a floor plan from canvas view data."""
        document = self._document
        if document is None:
            return []

        helper_items: list[tuple[str, bool, str, float | None]] = []
        seen: set[str] = set()

        default_color = "#f8f32b"
        floor_settings = document.view.get("floor_helper_settings", {})
        if isinstance(floor_settings, dict):
            floor_setting = floor_settings.get(fp_id, {})
            if isinstance(floor_setting, dict):
                default_color = str(floor_setting.get("color", default_color))

        per_floor = document.view.get("helper_lines_per_floor", {})
        if isinstance(per_floor, dict):
            floor_map = per_floor.get(fp_id, {})
            if isinstance(floor_map, dict):
                for helper_id, helper_data in floor_map.items():
                    hid = str(helper_id)
                    if not hid or hid in seen:
                        continue
                    visible = True
                    color = default_color
                    length_mm: float | None = None
                    if isinstance(helper_data, dict):
                        visible = bool(helper_data.get("visible", True))
                        color = str(helper_data.get("color", default_color))
                        length_raw = helper_data.get("length_mm")
                        if isinstance(length_raw, (int, float)) and length_raw > 0:
                            length_mm = float(length_raw)
                    helper_items.append((hid, visible, color, length_mm))
                    seen.add(hid)

        legacy_lines = document.view.get("floor_helper_lines", {})
        legacy_visible = document.view.get("floor_helper_line_visible", {})
        legacy_lengths = document.view.get("floor_helper_line_length_mm", {})
        if isinstance(legacy_lines, dict):
            floor_lines = legacy_lines.get(fp_id, {})
            floor_vis = legacy_visible.get(fp_id, {}) if isinstance(legacy_visible, dict) else {}
            floor_len = legacy_lengths.get(fp_id, {}) if isinstance(legacy_lengths, dict) else {}
            if isinstance(floor_lines, dict):
                for helper_id in floor_lines.keys():
                    hid = str(helper_id)
                    if not hid or hid in seen:
                        continue
                    visible = True
                    if isinstance(floor_vis, dict):
                        visible = bool(floor_vis.get(hid, True))
                    length_mm: float | None = None
                    if isinstance(floor_len, dict):
                        raw_len = floor_len.get(hid)
                        if isinstance(raw_len, (int, float)) and raw_len > 0:
                            length_mm = float(raw_len)
                    helper_items.append((hid, visible, default_color, length_mm))
                    seen.add(hid)

        return sorted(helper_items, key=lambda item: _helper_sort_key(item[0]))

    def _add_helper_line_item(
        self,
        parent: QTreeWidgetItem,
        floor_plan_id: str,
        helper_id: str,
        visible: bool,
        color: str,
        length_mm: float | None,
    ) -> None:
        nav_id = make_helper_nav_id(floor_plan_id, helper_id)
        label = f"Hilfslinie {helper_id}"
        if isinstance(length_mm, (int, float)) and length_mm > 0:
            label += f" ({length_mm:.0f} mm)"
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, _ID_ROLE, nav_id)
        item.setData(0, _KIND_ROLE, "helper_line")
        item.setData(0, _KIND_ROLE + 1, LayerId.ANNOTATION.value)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        item.setCheckState(0, self._to_state(visible))
        text_color = QColor(color)
        if not text_color.isValid():
            text_color = QColor("#f8f32b")
        base_brush = QBrush(text_color)
        item.setForeground(0, base_brush)
        item.setData(0, _BASE_BRUSH_ROLE, base_brush)
        if isinstance(length_mm, (int, float)) and length_mm > 0:
            item.setToolTip(0, f"Hilfslinie {helper_id}\nLänge: {length_mm:.0f} mm")
        else:
            item.setToolTip(0, f"Hilfslinie {helper_id}")
        self._items[nav_id] = item

    def _build_annotation_group(self, fp_item: QTreeWidgetItem, fp_id: str) -> None:
        document = self._document
        if document is None:
            return

        group_label, class_names = _ANNOTATION_GROUP

        elements: list[Element] = []
        for class_name in class_names:
            element_cls = _ELEMENT_TYPES_BY_NAME.get(class_name)
            if element_cls is None:
                continue
            elements.extend(document.elements_of(element_cls, fp_id))

        helper_lines = self._iter_helper_lines_for_floor(fp_id)

        if not elements and not helper_lines:
            return

        top_group = self._new_group_item(fp_item, group_label, LayerId.ANNOTATION)

        direct_items: list[tuple[tuple[int, int | str], str, object]] = []
        for element in sorted(elements, key=_sort_key):
            direct_items.append((_sort_key(element), "element", element))
        for helper_id, visible, color, length_mm in helper_lines:
            direct_items.append((_helper_sort_key(helper_id), "helper", (helper_id, visible, color, length_mm)))

        direct_items.sort(key=lambda item: item[0])

        for _sort_token, kind, payload in direct_items:
            if kind == "element":
                self._add_element_item(top_group, payload)
            else:
                helper_id, visible, color, length_mm = payload
                self._add_helper_line_item(top_group, fp_id, helper_id, visible, color, length_mm)

        self._refresh_branch_state(top_group)
        top_group.setExpanded(True)

    def _build_nested_group(
        self,
        fp_item: QTreeWidgetItem,
        fp_id: str,
        group_spec: tuple[str, tuple[tuple[str, tuple[str, ...]], ...]],
        layer: LayerId,
    ) -> None:
        document = self._document
        if document is None:
            return

        group_label, categories = group_spec
        prepared: list[tuple[str, list[Element]]] = []
        for category_label, class_names in categories:
            elements: list[Element] = []
            for class_name in class_names:
                element_cls = _ELEMENT_TYPES_BY_NAME.get(class_name)
                if element_cls is None:
                    continue
                elements.extend(document.elements_of(element_cls, fp_id))
            if elements:
                prepared.append((category_label, sorted(elements, key=_sort_key)))

        if not prepared:
            return

        top_group = self._new_group_item(fp_item, group_label, layer)
        for category_label, elements in prepared:
            cat_item = self._new_group_item(top_group, category_label, layer)
            for element in elements:
                self._add_element_item(cat_item, element)
            self._refresh_branch_state(cat_item)
            cat_item.setExpanded(True)
        self._refresh_branch_state(top_group)
        top_group.setExpanded(True)

    def _build_electrical_group(self, fp_item: QTreeWidgetItem, fp_id: str) -> None:
        """Baut die Elektro-Gruppe auf.

        Räume und Kabel werden flach aufgelistet. APs werden den Räumen
        zugeordnet, in denen sie geometrisch liegen (Point-in-Polygon).
        APs ohne Raum landen unter „Ohne Raum".
        """
        document = self._document
        if document is None:
            return

        rooms: list[ElecRoom] = list(document.elements_of(ElecRoom, fp_id))
        points: list[ElecPoint] = list(document.elements_of(ElecPoint, fp_id))
        cables: list[ElecCable] = list(document.elements_of(ElecCable, fp_id))

        if not rooms and not points and not cables:
            return

        # Raum-Polygone für PiP aufbauen (aus geom-Daten)
        room_polygons: dict[str, list[tuple[float, float]]] = {}
        for room in rooms:
            pts_raw = room.geom.get("elec_rooms") or []
            if len(pts_raw) >= 3:
                room_polygons[room.id] = [(float(p[0]), float(p[1])) for p in pts_raw]

        # AP → Raum-Zuordnung
        room_to_aps: dict[str, list[ElecPoint]] = {r.id: [] for r in rooms}
        unassigned: list[ElecPoint] = []
        for point in sorted(points, key=_sort_key):
            pos_raw = point.geom.get("elec_points")
            if pos_raw and len(pos_raw) >= 2:
                px, py = float(pos_raw[0]), float(pos_raw[1])
                found = ""
                for rid, poly in room_polygons.items():
                    if _point_in_polygon(px, py, poly):
                        found = rid
                        break
                if found:
                    room_to_aps[found].append(point)
                else:
                    unassigned.append(point)
            else:
                unassigned.append(point)

        top_group = self._new_group_item(fp_item, "Elektro", LayerId.ELECTRICAL)

        # Räume mit ihren APs
        if rooms:
            rooms_cat = self._new_group_item(top_group, "Räume", LayerId.ELECTRICAL)
            for room in sorted(rooms, key=_sort_key):
                room_item = self._new_group_item(rooms_cat, room.name or room.id, LayerId.ELECTRICAL)
                room_item.setData(0, _ID_ROLE, room.id)
                room_item.setData(0, _KIND_ROLE, "element")
                room_item.setData(0, _KIND_ROLE + 1, LayerId.ELECTRICAL.value)
                room_item.setFlags(room_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                room_item.setCheckState(
                    0,
                    self._to_state(document.is_visible(room.id) if document else True),
                )
                color = room.color
                base_brush = QBrush(QColor(color)) if color else QBrush(QColor("#cccccc"))
                room_item.setForeground(0, base_brush)
                room_item.setData(0, _BASE_BRUSH_ROLE, base_brush)
                self._items[room.id] = room_item
                for ap in room_to_aps.get(room.id, []):
                    self._add_element_item(room_item, ap)
                self._refresh_branch_state(room_item)
                room_item.setExpanded(True)
            self._refresh_branch_state(rooms_cat)
            rooms_cat.setExpanded(True)

        # APs ohne Raum (nur wenn vorhanden)
        if unassigned:
            no_room_cat = self._new_group_item(top_group, "Ohne Raum", LayerId.ELECTRICAL)
            for ap in unassigned:
                self._add_element_item(no_room_cat, ap)
            self._refresh_branch_state(no_room_cat)
            no_room_cat.setExpanded(True)

        # Kabel
        if cables:
            cables_cat = self._new_group_item(top_group, "Kabel", LayerId.ELECTRICAL)
            for cable in sorted(cables, key=_sort_key):
                self._add_element_item(cables_cat, cable)
            self._refresh_branch_state(cables_cat)
            cables_cat.setExpanded(True)

        self._refresh_branch_state(top_group)
        top_group.setExpanded(True)

    # ------------------------------------------------------------------
    def _highlight_active(self) -> None:
        document = self._document
        if document is None:
            return
        active = document.active_floorplan_id
        self._suspend_item_events = True
        self._tree.blockSignals(True)
        try:
            for fp_id in document.floorplans:
                item = self._find_item_by_id(fp_id)
                if item is None:
                    continue
                try:
                    font = item.font(0)
                    font.setBold(fp_id == active)
                    item.setFont(0, font)
                    item.setForeground(
                        0, QBrush(_ACTIVE_COLOR) if fp_id == active else QBrush()
                    )
                    if fp_id == active:
                        item.setToolTip(0, "Aktiver Grundriss")
                    else:
                        item.setToolTip(0, "Doppelklick: als aktiven Grundriss setzen")
                except RuntimeError:
                    # Item wurde während eines Rebuilds ungültig – ignorieren.
                    continue
        finally:
            self._tree.blockSignals(False)
            self._suspend_item_events = False

    def _apply_selectability(self) -> None:
        self._suspend_item_events = True
        self._tree.blockSignals(True)
        try:
            root = self._tree.invisibleRootItem()
            for index in range(root.childCount()):
                self._apply_selectability_recursive(root.child(index))
        except RuntimeError:
            # Tree wurde während eines Signalstorms neu aufgebaut.
            pass
        finally:
            self._tree.blockSignals(False)
            self._suspend_item_events = False

    def _apply_selectability_recursive(self, item: QTreeWidgetItem) -> None:
        try:
            kind = item.data(0, _KIND_ROLE)
            if kind in ("element", "helper_line"):
                layer_value = item.data(0, _KIND_ROLE + 1)
                enabled = layer_value in {layer.value for layer in self._selectable_layers}
                # Navigator bleibt immer auswählbar; nur die Canvas-Interaktion
                # ist workspace-gefiltert. Fremde Layer werden visuell gedimmt.
                item.setDisabled(False)
                font = item.font(0)
                base_brush = item.data(0, _BASE_BRUSH_ROLE)
                if not isinstance(base_brush, QBrush):
                    base_brush = QBrush()
                if enabled:
                    font.setItalic(False)
                    item.setFont(0, font)
                    item.setForeground(0, base_brush)
                    item.setToolTip(0, "")
                else:
                    font.setItalic(True)
                    item.setFont(0, font)
                    item.setForeground(0, _dim_brush(base_brush))
                    item.setToolTip(
                        0,
                        "Im aktuellen Workspace im Canvas nicht direkt auswählbar, "
                        "aber Eigenschaften sind editierbar.",
                    )
            for index in range(item.childCount()):
                self._apply_selectability_recursive(item.child(index))
        except RuntimeError:
            return

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        root = self._tree.invisibleRootItem()
        for index in range(root.childCount()):
            _filter_item(root.child(index), needle)

    # ------------------------------------------------------------------
    def _on_element_event(self, _element_id: str) -> None:
        self.rebuild()

    def _on_element_changed(self, element_id: str) -> None:
        """Leichtgewichtiges Update: kein voller Rebuild, nur Checkbox/Label.

        Wird u. a. durch Sichtbarkeitsänderungen ausgelöst. Ein voller
        Rebuild pro Element wäre bei Ast-Umschaltungen zu langsam und würde
        die gerade bearbeiteten Items invalidieren.
        """
        document = self._document
        if document is None:
            return
        item = self._items.get(element_id) or self._find_item_by_id(element_id)
        if item is None:
            return
        self._suspend_item_events = True
        self._tree.blockSignals(True)
        try:
            item.setCheckState(0, self._to_state(document.is_visible(element_id)))
            element = document.get(element_id)
            if element is not None:
                item.setText(0, element.name or element.id)
                color = str(element.color or "").strip()
                base_brush = QBrush(QColor(color)) if color else QBrush(QColor("#cccccc"))
                item.setData(0, _BASE_BRUSH_ROLE, base_brush)
        except RuntimeError:
            pass
        finally:
            self._tree.blockSignals(False)
            self._suspend_item_events = False
        self._apply_selectability()

    def _on_active_changed(self, _fp_id: str) -> None:
        self._highlight_active()

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            self._selected_ids = []
            self.selection_changed.emit([])
            return
        selected_ids: list[str] = []
        for item in items:
            kind = item.data(0, _KIND_ROLE)
            if kind not in ("element", "floorplan", "helper_line"):
                continue
            element_id = item.data(0, _ID_ROLE)
            if element_id:
                selected_ids.append(element_id)

        self._selected_ids = selected_ids
        self.selection_changed.emit(list(selected_ids))
        if len(selected_ids) != 1:
            return
        self.element_selected.emit(selected_ids[0])

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, _KIND_ROLE) == "floorplan":
            fp_id = item.data(0, _ID_ROLE)
            if fp_id:
                self.floorplan_activated.emit(fp_id)

    def _on_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            global_pos = self._tree.viewport().mapToGlobal(pos)
            self.context_requested.emit("", "navigator_root", global_pos)
            return
        kind = item.data(0, _KIND_ROLE)
        if kind not in ("element", "floorplan", "helper_line"):
            return
        element_id = item.data(0, _ID_ROLE)
        if not element_id:
            return
        global_pos = self._tree.viewport().mapToGlobal(pos)
        self.context_requested.emit(element_id, kind, global_pos)

    def _on_floorplan_reordered(self, source_fp_id: str, target_fp_id: str) -> None:
        if not source_fp_id or not target_fp_id or source_fp_id == target_fp_id:
            return
        source_item = self._items.get(source_fp_id) or self._find_item_by_id(source_fp_id)
        target_item = self._items.get(target_fp_id) or self._find_item_by_id(target_fp_id)
        if source_item is None or target_item is None:
            return
        if source_item.parent() is not None or target_item.parent() is not None:
            return

        source_index = self._tree.indexOfTopLevelItem(source_item)
        target_index = self._tree.indexOfTopLevelItem(target_item)
        if source_index < 0 or target_index < 0 or source_index == target_index:
            return

        moved = self._tree.takeTopLevelItem(source_index)
        if source_index < target_index:
            target_index -= 1
        self._tree.insertTopLevelItem(target_index, moved)
        self._tree.setCurrentItem(moved)

        order = self.get_floorplan_order()
        self._selected_ids = [source_fp_id]
        self.floorplan_order_changed.emit(order)

    def get_floorplan_order(self) -> list[str]:
        order: list[str] = []
        root = self._tree.invisibleRootItem()
        for index in range(root.childCount()):
            item = root.child(index)
            if item.data(0, _KIND_ROLE) != "floorplan":
                continue
            fp_id = item.data(0, _ID_ROLE) or ""
            if fp_id:
                order.append(fp_id)
        return order

    def select(self, element_id: str) -> None:
        item = self._items.get(element_id) or self._find_item_by_id(element_id)
        if item is None:
            return
        try:
            self._tree.blockSignals(True)
            self._tree.setCurrentItem(item)
            self._tree.blockSignals(False)
            self._tree.scrollToItem(item)
            self._selected_ids = [element_id]
        except RuntimeError:
            # Item ungültig -> einmal aus frischem Tree auflösen und erneut versuchen.
            fresh = self._find_item_by_id(element_id)
            if fresh is None:
                return
            self._tree.blockSignals(True)
            self._tree.setCurrentItem(fresh)
            self._tree.blockSignals(False)
            self._tree.scrollToItem(fresh)
            self._selected_ids = [element_id]

    def selected_ids(self) -> list[str]:
        return list(self._selected_ids)

    def _find_item_by_id(self, element_id: str) -> QTreeWidgetItem | None:
        root = self._tree.invisibleRootItem()
        for index in range(root.childCount()):
            found = self._find_item_by_id_recursive(root.child(index), element_id)
            if found is not None:
                return found
        return None

    def _find_item_by_id_recursive(self, item: QTreeWidgetItem, element_id: str) -> QTreeWidgetItem | None:
        if item.data(0, _ID_ROLE) == element_id:
            return item
        for index in range(item.childCount()):
            found = self._find_item_by_id_recursive(item.child(index), element_id)
            if found is not None:
                return found
        return None

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._suspend_item_events:
            return
        kind = item.data(0, _KIND_ROLE)
        state = item.checkState(0)
        visible = state != Qt.Unchecked
        if kind in ("element", "helper_line"):
            element_id = item.data(0, _ID_ROLE)
            if element_id:
                self.visibility_changed.emit(element_id, visible)
            return

        if kind in ("category", "floorplan"):
            affected_ids = self._collect_branch_ids(item)
            self._suspend_item_events = True
            self._tree.blockSignals(True)
            self._set_branch_state(item, Qt.Checked if visible else Qt.Unchecked)
            self._tree.blockSignals(False)
            self._suspend_item_events = False
            for element_id in affected_ids:
                self.visibility_changed.emit(element_id, visible)

    def _on_item_expanded_changed(self, _item: QTreeWidgetItem) -> None:
        self._save_expanded_state()

    def _collapse_all(self) -> None:
        self._tree.collapseAll()
        self._save_expanded_state()

    def _save_expanded_state(self) -> None:
        settings = QSettings("HRouting", "HRouting")
        expanded: list[str] = []
        root = self._tree.invisibleRootItem()
        for index in range(root.childCount()):
            self._collect_expanded_paths(root.child(index), [], expanded)
        settings.setValue(self._expanded_state_key, expanded)

    def _restore_expanded_state(self) -> None:
        settings = QSettings("HRouting", "HRouting")
        raw = settings.value(self._expanded_state_key, [])
        if isinstance(raw, str):
            saved = {raw}
        elif isinstance(raw, list):
            saved = {str(entry) for entry in raw}
        else:
            saved = set()
        if not saved:
            return
        root = self._tree.invisibleRootItem()
        for index in range(root.childCount()):
            self._restore_expanded_paths(root.child(index), [], saved)

    def _collect_expanded_paths(
        self,
        item: QTreeWidgetItem,
        prefix: list[str],
        out: list[str],
    ) -> None:
        path = prefix + [self._item_path_token(item)]
        if item.childCount() > 0 and item.isExpanded():
            out.append("/".join(path))
        for index in range(item.childCount()):
            self._collect_expanded_paths(item.child(index), path, out)

    def _restore_expanded_paths(
        self,
        item: QTreeWidgetItem,
        prefix: list[str],
        saved: set[str],
    ) -> None:
        path = prefix + [self._item_path_token(item)]
        item.setExpanded("/".join(path) in saved)
        for index in range(item.childCount()):
            self._restore_expanded_paths(item.child(index), path, saved)

    def _item_path_token(self, item: QTreeWidgetItem) -> str:
        kind = str(item.data(0, _KIND_ROLE) or "")
        item_id = str(item.data(0, _ID_ROLE) or "")
        text = str(item.text(0) or "")
        return f"{kind}:{item_id}:{text}"

    def _set_branch_state(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        item.setCheckState(0, state)
        for index in range(item.childCount()):
            self._set_branch_state(item.child(index), state)

    def _emit_branch_visibility(self, item: QTreeWidgetItem, visible: bool) -> None:
        kind = item.data(0, _KIND_ROLE)
        if kind in ("element", "floorplan", "helper_line"):
            element_id = item.data(0, _ID_ROLE)
            if element_id:
                self.visibility_changed.emit(element_id, visible)
        for index in range(item.childCount()):
            self._emit_branch_visibility(item.child(index), visible)

    def _collect_branch_ids(self, item: QTreeWidgetItem) -> list[str]:
        ids: list[str] = []
        kind = item.data(0, _KIND_ROLE)
        if kind in ("element", "floorplan", "helper_line"):
            element_id = item.data(0, _ID_ROLE)
            if element_id:
                ids.append(element_id)
        for index in range(item.childCount()):
            ids.extend(self._collect_branch_ids(item.child(index)))
        return ids

    def _refresh_parent_states(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            self._refresh_branch_state(parent)
            parent = parent.parent()

    def _refresh_branch_state(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 0:
            return
        checked = 0
        partial = False
        for index in range(item.childCount()):
            state = item.child(index).checkState(0)
            if state == Qt.PartiallyChecked:
                partial = True
            elif state == Qt.Checked:
                checked += 1
        if partial:
            state = Qt.PartiallyChecked
        elif checked == 0:
            state = Qt.Unchecked
        elif checked == item.childCount():
            state = Qt.Checked
        else:
            state = Qt.PartiallyChecked
        item.setCheckState(0, state)

    @staticmethod
    def _to_state(visible: bool) -> Qt.CheckState:
        return Qt.Checked if visible else Qt.Unchecked


def _sort_key(element: Element) -> tuple:

    suffix = element.id.rsplit("-", 1)[-1]
    return (0, int(suffix)) if suffix.isdigit() else (1, element.id)


def _point_in_polygon(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    """Ray-casting Point-in-Polygon für (px, py) gegen ein Polygon."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _filter_item(item: QTreeWidgetItem, needle: str) -> bool:
    """Blendet Items aus, die nicht passen. Gibt True zurück, wenn sichtbar."""
    match = not needle or needle in item.text(0).lower()
    child_match = False
    for index in range(item.childCount()):
        if _filter_item(item.child(index), needle):
            child_match = True
    visible = match or child_match
    item.setHidden(not visible)
    if needle and child_match:
        item.setExpanded(True)
    return visible


def _dim_brush(base_brush: QBrush) -> QBrush:
    color = base_brush.color()
    if not color.isValid():
        return QBrush(QColor("#8a8a8a"))
    # Farbton beibehalten, Sättigung reduzieren und leicht aufhellen.
    h, s, v, a = color.getHsv()
    s = int(max(0, min(255, s * 0.25)))
    v = int(max(0, min(255, v * 0.85 + 35)))
    dimmed = QColor.fromHsv(h, s, v, a)
    return QBrush(dimmed)
