"""Navigator: Baumansicht aller tatsächlich vorhandenen Projektelemente."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QDockWidget,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model.document import Document
from model.elements import ELEMENT_TYPES, Element, Furniture
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
    "Texte",
    (
        ("Texte", ("TextAnnotation",)),
    ),
)


def _index_element_types() -> dict[str, type[Element]]:
    mapping = {cls.__name__: cls for cls in ELEMENT_TYPES}
    return mapping


_ELEMENT_TYPES_BY_NAME = _index_element_types()


class NavigatorDock(QDockWidget):
    """Zeigt Grundrisse mit ihren Elementen; leere Kategorien entfallen."""

    element_selected = Signal(str)
    floorplan_activated = Signal(str)
    visibility_changed = Signal(str, bool)
    context_requested = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Navigator", parent)
        self.setObjectName("dock_navigator")
        self._document: Document | None = None
        self._selectable_layers: set[LayerId] = set(LayerId)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._suspend_item_events = False

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._filter = QLineEdit(container)
        self._filter.setPlaceholderText("Filtern…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tree = QTreeWidget(container)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setStyleSheet(
            "QTreeWidget::item:selected { background-color: #4fc3f7; color: #0b1d2a; }"
        )
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
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
            fp_item.setFlags(fp_item.flags() | Qt.ItemIsUserCheckable)
            fp_item.setCheckState(0, self._to_state(document.is_visible(fp_id)))
            self._items[fp_id] = fp_item

            self._build_floorplan_groups(fp_item, fp_id)

            self._refresh_branch_state(fp_item)
            fp_item.setExpanded(True)

        self._apply_selectability()
        self._highlight_active()
        self._apply_filter(self._filter.text())
        self._tree.blockSignals(False)
        self._suspend_item_events = False

    def _add_element_item(self, parent: QTreeWidgetItem, element: Element) -> None:
        label = element.name or element.id
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, _ID_ROLE, element.id)
        item.setData(0, _KIND_ROLE, "element")
        item.setData(0, _KIND_ROLE + 1, type(element).LAYER.value)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(
            0,
            self._to_state(self._document.is_visible(element.id) if self._document else True),
        )
        color = element.color
        base_brush = QBrush()
        if color:
            base_brush = QBrush(QColor(color))
            item.setForeground(0, base_brush)
        item.setData(0, _BASE_BRUSH_ROLE, base_brush)
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
        self._build_nested_group(fp_item, fp_id, _ELECTRICAL_GROUP, LayerId.ELECTRICAL)

        furniture = [
            f for f in document.furniture.values()
            if f.floor_plan_id in ("", fp_id)
        ]
        if furniture:
            furn_group = self._new_group_item(fp_item, Furniture.CATEGORY_LABEL, LayerId.FURNITURE)
            for element in sorted(furniture, key=_sort_key):
                self._add_element_item(furn_group, element)
            self._refresh_branch_state(furn_group)

        self._build_nested_group(fp_item, fp_id, _ANNOTATION_GROUP, LayerId.ANNOTATION)

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
            if kind == "element":
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
                base_brush = QBrush(QColor(color)) if color else QBrush()
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
            return
        kind = items[0].data(0, _KIND_ROLE)
        if kind not in ("element", "floorplan"):
            return
        element_id = items[0].data(0, _ID_ROLE)
        if element_id:
            self.element_selected.emit(element_id)

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, _KIND_ROLE) == "floorplan":
            fp_id = item.data(0, _ID_ROLE)
            if fp_id:
                self.floorplan_activated.emit(fp_id)

    def _on_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, _KIND_ROLE)
        if kind not in ("element", "floorplan"):
            return
        element_id = item.data(0, _ID_ROLE)
        if not element_id:
            return
        global_pos = self._tree.viewport().mapToGlobal(pos)
        self.context_requested.emit(element_id, kind, global_pos)

    def select(self, element_id: str) -> None:
        item = self._items.get(element_id) or self._find_item_by_id(element_id)
        if item is None:
            return
        try:
            self._tree.blockSignals(True)
            self._tree.setCurrentItem(item)
            self._tree.blockSignals(False)
            self._tree.scrollToItem(item)
        except RuntimeError:
            # Item ungültig -> einmal aus frischem Tree auflösen und erneut versuchen.
            fresh = self._find_item_by_id(element_id)
            if fresh is None:
                return
            self._tree.blockSignals(True)
            self._tree.setCurrentItem(fresh)
            self._tree.blockSignals(False)
            self._tree.scrollToItem(fresh)

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
        if kind == "element":
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

    def _set_branch_state(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        item.setCheckState(0, state)
        for index in range(item.childCount()):
            self._set_branch_state(item.child(index), state)

    def _emit_branch_visibility(self, item: QTreeWidgetItem, visible: bool) -> None:
        kind = item.data(0, _KIND_ROLE)
        if kind in ("element", "floorplan"):
            element_id = item.data(0, _ID_ROLE)
            if element_id:
                self.visibility_changed.emit(element_id, visible)
        for index in range(item.childCount()):
            self._emit_branch_visibility(item.child(index), visible)

    def _collect_branch_ids(self, item: QTreeWidgetItem) -> list[str]:
        ids: list[str] = []
        kind = item.data(0, _KIND_ROLE)
        if kind in ("element", "floorplan"):
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
