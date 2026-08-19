"""Eigenschaften-Dock: zeigt den Editor des selektierten Elements.

Die Editoren werden aus dem Schema erzeugt (siehe :mod:`model.schema`) und
zwischengespeichert, damit ein Wechsel der Auswahl schnell bleibt.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.properties import GenericElementEditor, GenericMultiElementEditor, GlobalSettingsEditor
from model.document import Document
from model.elements import Element
from model.schema import FieldKind, FieldSpec, schema_for


# ---------------------------------------------------------------------------
# Hilfslinien-Editor – Eigenschaften einer einzelnen Hilfslinie
# ---------------------------------------------------------------------------

class HelperLineEditor(QWidget):
    """Zeigt und ändert die Eigenschaften einer einzelnen Hilfslinie."""

    pre_change = Signal()
    helper_deleted = Signal(str, str)

    def __init__(self, floor_id: str, helper_id: str, canvas: "QWidget",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._floor_id = floor_id
        self._helper_id = helper_id
        self._canvas = canvas
        self._updating = False
        self._current_color = "#f8f32b"
        self._build_ui()
        self.refresh()

    # -- UI ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"Hilfslinie {self._helper_id}")
        title.setStyleSheet("font-weight: bold; padding-bottom: 4px;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.toggled.connect(self._on_visible_changed)
        form.addRow(self.chk_visible)

        color_row = QHBoxLayout()
        self.btn_color = QPushButton()
        self.btn_color.setFixedHeight(28)
        self.btn_color.setToolTip("Farbe wählen (alle Hilfslinien dieses Grundrisses)")
        self.btn_color.clicked.connect(self._choose_color)
        color_row.addWidget(self.btn_color)
        form.addRow("Farbe (Grundriss):", color_row)

        self.sb_length = QDoubleSpinBox()
        self.sb_length.setRange(1.0, 200_000.0)
        self.sb_length.setDecimals(0)
        self.sb_length.setSingleStep(100.0)
        self.sb_length.setSuffix(" mm")
        self.sb_length.editingFinished.connect(self._on_length_changed)
        form.addRow("Länge:", self.sb_length)

        self.chk_fixed = QCheckBox("Länge fixiert (bleibt beim Bearbeiten)")
        self.chk_fixed.toggled.connect(self._on_fixed_changed)
        form.addRow(self.chk_fixed)

        self.cb_style = QComboBox()
        for label, data in [("Gestrichelt", "dash"), ("Durchgezogen", "solid"),
                             ("Gepunktet", "dot"), ("Strich-Punkt", "dashdot")]:
            self.cb_style.addItem(label, data)
        self.cb_style.currentIndexChanged.connect(self._on_style_changed)
        form.addRow("Linienstil:", self.cb_style)

        self.sb_width = QDoubleSpinBox()
        self.sb_width.setRange(0.5, 20.0)
        self.sb_width.setDecimals(1)
        self.sb_width.setSingleStep(0.5)
        self.sb_width.setSuffix(" px")
        self.sb_width.editingFinished.connect(self._on_width_changed)
        form.addRow("Linienstärke:", self.sb_width)

        layout.addLayout(form)

        self.btn_delete = QPushButton("Hilfslinie löschen")
        self.btn_delete.setToolTip("Löscht diese Hilfslinie")
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        layout.addWidget(self.btn_delete)

        layout.addStretch()

    # -- Public API ----------------------------------------------------------

    def refresh(self) -> None:
        canvas = self._canvas
        fid = self._floor_id
        hid = self._helper_id
        self._updating = True
        try:
            vis_map = canvas._floor_helper_line_visible.get(fid, {})
            self.chk_visible.setChecked(bool(vis_map.get(hid, True)))

            color = canvas.get_helper_line_color(fid)
            self._current_color = color
            self.btn_color.setStyleSheet(f"background:{color};")

            length_mm = canvas.get_helper_line_length_mm(fid, hid)
            self.sb_length.setValue(max(1.0, length_mm))

            self.chk_fixed.setChecked(canvas.is_helper_line_length_fixed(fid, hid))

            style = canvas.get_helper_line_style(fid)
            idx = self.cb_style.findData(style)
            if idx >= 0:
                self.cb_style.setCurrentIndex(idx)

            self.sb_width.setValue(canvas.get_helper_line_width(fid))
        finally:
            self._updating = False

    # -- Slots ---------------------------------------------------------------

    def _on_visible_changed(self, checked: bool) -> None:
        if self._updating:
            return
        self.pre_change.emit()
        self._canvas.set_helper_line_item_visible(self._floor_id, self._helper_id, checked)

    def _choose_color(self) -> None:
        from PySide6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self._current_color), self, "Farbe wählen")
        if color.isValid():
            self.pre_change.emit()
            self._current_color = color.name()
            self.btn_color.setStyleSheet(f"background:{self._current_color};")
            self._canvas.set_helper_line_color(self._current_color, self._floor_id)

    def _on_length_changed(self) -> None:
        if self._updating:
            return
        self.pre_change.emit()
        self._canvas.set_helper_line_length_mm(
            self._floor_id, self._helper_id, self.sb_length.value()
        )

    def _on_fixed_changed(self, checked: bool) -> None:
        if self._updating:
            return
        self.pre_change.emit()
        self._canvas.set_helper_line_length_fixed(self._floor_id, self._helper_id, checked)

    def _on_style_changed(self, _idx: int) -> None:
        if self._updating:
            return
        self.pre_change.emit()
        self._canvas.set_helper_line_style(
            self._floor_id, str(self.cb_style.currentData() or "dash")
        )

    def _on_width_changed(self) -> None:
        if self._updating:
            return
        self.pre_change.emit()
        self._canvas.set_helper_line_width(self._floor_id, self.sb_width.value())

    def _on_delete_clicked(self) -> None:
        self.pre_change.emit()
        self._canvas.delete_helper_line(self._floor_id, self._helper_id)
        self.helper_deleted.emit(self._floor_id, self._helper_id)


# ---------------------------------------------------------------------------
# HelperDrawPanel – Einstellungen beim Zeichnen einer neuen Hilfslinie
# ---------------------------------------------------------------------------

class HelperDrawPanel(QWidget):
    """Zeigt die Einstellungen für das Zeichnen einer neuen Hilfslinie."""

    pre_change = Signal()

    def __init__(self, floor_id: str, canvas: "QWidget",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._floor_id = floor_id
        self._canvas = canvas
        self._updating = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Hilfslinie zeichnen")
        title.setStyleSheet("font-weight: bold; padding-bottom: 4px;")
        layout.addWidget(title)

        hint = QLabel(
            "Klicken Sie auf den Startpunkt und ziehen Sie die Hilfslinie in "
            "die gewünschte Richtung.\nDie Länge wird durch den Wert unten bestimmt."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.sb_length = QDoubleSpinBox()
        self.sb_length.setRange(1.0, 200_000.0)
        self.sb_length.setDecimals(0)
        self.sb_length.setSingleStep(100.0)
        self.sb_length.setSuffix(" mm")
        self.sb_length.valueChanged.connect(self._on_length_changed)
        form.addRow("Länge der neuen Hilfslinie:", self.sb_length)

        layout.addLayout(form)
        layout.addStretch()

    def refresh(self) -> None:
        self._updating = True
        try:
            length_mm = self._canvas.get_helper_line_target_length_mm(self._floor_id)
            self.sb_length.setValue(max(1.0, length_mm))
        finally:
            self._updating = False

    def _on_length_changed(self, value: float) -> None:
        if self._updating:
            return
        self._canvas.set_helper_line_target_length_mm(value, self._floor_id)


class PropertiesDock(QDockWidget):
    """Zeigt die Eigenschaften des aktuell selektierten Elements."""

    field_changed = Signal(str, str, object)   # (element_id, key, wert)
    batch_field_changed = Signal(list, str, object)  # (element_ids, key, wert)
    action_triggered = Signal(str, str)        # (element_id, action_id)
    setting_changed = Signal(str, object)      # (key, wert)
    pre_change = Signal()                      # fires before any write (for undo)

    _MULTI_EDITABLE_KEYS = frozenset(
        {
            "color",
            "visible",
            "label_visible",
            "label_size",
            "type",
            "ap_type",
            "builtin_symbol",
            "width",
            "height",
            "stroke_width",
            "font_size",
            "type_label_visible",
        }
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Eigenschaften", parent)
        self.setObjectName("dock_properties")

        self._document: Document | None = None
        self._editors: dict[str, GenericElementEditor] = {}
        self._global_editor: GlobalSettingsEditor | None = None
        self._multi_editor: GenericMultiElementEditor | None = None
        self._helper_editor: HelperLineEditor | None = None
        self._helper_draw_panel: HelperDrawPanel | None = None
        self._current_id: str = ""

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)

        self._stack = QStackedWidget(self._scroll)
        self._placeholder = QLabel("Kein Element ausgewählt")
        self._placeholder.setContentsMargins(12, 12, 12, 12)
        self._stack.addWidget(self._placeholder)

        self._scroll.setWidget(self._stack)
        self.setWidget(self._scroll)

    # ------------------------------------------------------------------
    def set_document(self, document: Document | None) -> None:
        """Bindet das Dock an ein Dokument und verwirft alte Editoren."""
        self.clear()
        self._document = document
        self._global_editor = None
        self._multi_editor = None
        if document is not None:
            self.show_global_settings()

    def show_elements(self, element_ids: list[str]) -> None:
        """Zeigt den Mehrfach-Editor für gemeinsame Eigenschaften."""
        document = self._document
        if document is None:
            self.show_placeholder()
            return

        ids = [element_id for element_id in element_ids if element_id and document.get(element_id) is not None]
        if len(ids) <= 1:
            self.show_element(ids[0] if ids else "")
            return

        elements = [document.get(element_id) for element_id in ids]
        elements = [element for element in elements if element is not None]
        if len(elements) <= 1:
            self.show_element(elements[0].id if elements else "")
            return

        schema = schema_for(elements[0])
        if schema is None:
            self.show_placeholder("Für diese Auswahl gibt es keine gemeinsamen Eigenschaften")
            return

        editable_specs = self._common_editable_specs(elements)
        if not editable_specs:
            self.show_placeholder(
                "Für diese Auswahl sind keine gemeinsamen Felder verfügbar\n"
                f"Aktuelle Auswahl: {self._selection_type_summary(elements)}"
            )
            return

        if self._multi_editor is not None:
            self._stack.removeWidget(self._multi_editor)
            self._multi_editor.deleteLater()

        self._multi_editor = GenericMultiElementEditor(
            document,
            elements,
            schema,
            editable_specs,
            self._stack,
        )
        self._multi_editor.field_changed.connect(self.batch_field_changed)
        self._multi_editor.pre_change.connect(self.pre_change)
        self._stack.addWidget(self._multi_editor)
        self._current_id = ""
        self._stack.setCurrentWidget(self._multi_editor)

    @staticmethod
    def _selection_type_summary(elements: list[Element]) -> str:
        counts: dict[str, int] = {}
        for element in elements:
            label = str(getattr(type(element), "CATEGORY_LABEL", type(element).__name__))
            counts[label] = counts.get(label, 0) + 1
        return ", ".join(f"{count}x {label}" for label, count in sorted(counts.items()))

    def _common_editable_specs(self, elements: list[Element]) -> list[FieldSpec]:
        document = self._document
        if document is None or not elements:
            return []

        schema_maps: list[dict[str, FieldSpec]] = []
        first_schema = schema_for(elements[0])
        if first_schema is None:
            return []

        ordered_keys = [spec.key for spec in first_schema.fields]
        for element in elements:
            schema = schema_for(element)
            if schema is None:
                return []
            schema_maps.append(
                {
                    spec.key: spec
                    for spec in schema.fields
                    if spec.key in self._MULTI_EDITABLE_KEYS
                    and spec.kind not in (FieldKind.READONLY, FieldKind.FILE)
                }
            )

        common_keys = set(schema_maps[0].keys())
        for spec_map in schema_maps[1:]:
            common_keys.intersection_update(spec_map.keys())

        merged_specs = []
        for key in ordered_keys:
            if key not in common_keys:
                continue
            candidates = [spec_map[key] for spec_map in schema_maps]
            if not self._specs_are_compatible(candidates):
                continue

            merged = candidates[0]
            if merged.kind in (FieldKind.CHOICE, FieldKind.EDITABLE_CHOICE):
                all_options = [tuple(spec.resolve_options(document)) for spec in candidates]
                if merged.kind is FieldKind.CHOICE:
                    options = [
                        option
                        for option in all_options[0]
                        if all(option in choices for choices in all_options[1:])
                    ]
                else:
                    options = []
                    for choices in all_options:
                        for option in choices:
                            if option not in options:
                                options.append(option)
                if not options:
                    continue
                merged = replace(merged, options=tuple(options), document_options=None)

            merged_specs.append(merged)

        return merged_specs

    @staticmethod
    def _specs_are_compatible(specs: list[FieldSpec]) -> bool:
        if not specs:
            return False
        first = specs[0]

        # Feldtyp muss identisch sein, sonst kann kein gemeinsames Widget entstehen.
        if any(spec.kind is not first.kind for spec in specs[1:]):
            return False

        # Numerische Felder nur zusammenführen, wenn Darstellung konsistent bleibt.
        if first.kind is FieldKind.NUMBER:
            for spec in specs[1:]:
                if (
                    spec.scale != first.scale
                    or spec.unit != first.unit
                    or spec.decimals != first.decimals
                ):
                    return False

        # Choice/EditableChoice werden in _common_editable_specs über Optionen
        # zusammengeführt. Hier reicht die Kind-Kompatibilität.
        return True

    def show_element(self, element_id: str) -> None:
        """Zeigt den Editor eines Elements; erzeugt ihn bei Bedarf."""
        document = self._document
        if document is None or not element_id:
            self.show_placeholder()
            return

        element = document.get(element_id)
        if element is None:
            self.show_placeholder()
            return

        schema = schema_for(element)
        if schema is None:
            self.show_placeholder(f"Für {element_id} gibt es keine Eigenschaften.")
            return

        editor = self._editors.get(element_id)
        if editor is None or editor.element is not element:
            if editor is not None:
                self._stack.removeWidget(editor)
                editor.deleteLater()
            editor = GenericElementEditor(document, element, schema, self._stack)
            editor.field_changed.connect(self.field_changed)
            editor.action_triggered.connect(self.action_triggered)
            editor.pre_change.connect(self.pre_change)
            self._editors[element_id] = editor
            self._stack.addWidget(editor)
        else:
            editor.refresh()

        self._current_id = element_id
        self._stack.setCurrentWidget(editor)

    def show_global_settings(self) -> None:
        """Zeigt die globalen Projektparameter."""
        document = self._document
        if document is None:
            self.show_placeholder()
            return
        if self._global_editor is None:
            self._global_editor = GlobalSettingsEditor(document, self._stack)
            self._global_editor.setting_changed.connect(self.setting_changed)
            self._global_editor.pre_change.connect(self.pre_change)
            self._stack.addWidget(self._global_editor)
        else:
            self._global_editor.refresh()
        self._current_id = ""
        self._stack.setCurrentWidget(self._global_editor)

    def refresh_element(self, element_id: str) -> None:
        """Aktualisiert einen bereits gebauten Editor (z. B. nach Canvas-Änderung)."""
        editor = self._editors.get(element_id)
        if editor is not None:
            editor.refresh()

    def refresh_current(self) -> None:
        if self._current_id:
            self.refresh_element(self._current_id)
        elif self._multi_editor is not None and self._stack.currentWidget() is self._multi_editor:
            self._multi_editor.refresh()
        elif self._global_editor is not None:
            self._global_editor.refresh()

    def forget_element(self, element_id: str) -> None:
        """Entfernt den Editor eines gelöschten Elements."""
        editor = self._editors.pop(element_id, None)
        if editor is not None:
            self._stack.removeWidget(editor)
            editor.deleteLater()
        if self._current_id == element_id:
            self.show_global_settings()
        if self._multi_editor is not None and element_id in self._multi_editor.element_ids:
            self.show_global_settings()

    def show_placeholder(self, text: str = "Kein Element ausgewählt") -> None:
        self._placeholder.setText(text)
        self._current_id = ""
        self._stack.setCurrentWidget(self._placeholder)

    def show_helper_line(self, floor_id: str, helper_id: str, canvas: "QWidget",
                         nav_id: str = "") -> None:
        """Zeigt den Eigenschaften-Editor einer einzelnen Hilfslinie."""
        # Reuse the existing editor if it targets the same line.
        if (self._helper_editor is not None
                and self._helper_editor._floor_id == floor_id
                and self._helper_editor._helper_id == helper_id):
            self._helper_editor.refresh()
        else:
            if self._helper_editor is not None:
                self._stack.removeWidget(self._helper_editor)
                self._helper_editor.deleteLater()
            self._helper_editor = HelperLineEditor(floor_id, helper_id, canvas, self._stack)
            self._helper_editor.pre_change.connect(self.pre_change)
            self._helper_editor.helper_deleted.connect(self._on_helper_deleted)
            self._stack.addWidget(self._helper_editor)
        self._current_id = nav_id or f"NAV-HLP::{floor_id}::{helper_id}"
        self._stack.setCurrentWidget(self._helper_editor)

    def show_helper_draw_settings(self, floor_id: str, canvas: "QWidget") -> None:
        """Zeigt das Panel mit den Einstellungen für das Zeichnen einer neuen Hilfslinie."""
        if (self._helper_draw_panel is not None
                and self._helper_draw_panel._floor_id == floor_id):
            self._helper_draw_panel.refresh()
        else:
            if self._helper_draw_panel is not None:
                self._stack.removeWidget(self._helper_draw_panel)
                self._helper_draw_panel.deleteLater()
            self._helper_draw_panel = HelperDrawPanel(floor_id, canvas, self._stack)
            self._helper_draw_panel.pre_change.connect(self.pre_change)
            self._stack.addWidget(self._helper_draw_panel)
        self._current_id = ""
        self._stack.setCurrentWidget(self._helper_draw_panel)

    def refresh_helper(self, floor_id: str, helper_id: str) -> None:
        """Aktualisiert den Hilfslinie-Editor wenn er gerade angezeigt wird."""
        if (self._helper_editor is not None
                and self._helper_editor._floor_id == floor_id
                and self._helper_editor._helper_id == helper_id
                and self._stack.currentWidget() is self._helper_editor):
            self._helper_editor.refresh()

    def _on_helper_deleted(self, floor_id: str, helper_id: str) -> None:
        nav_id = f"NAV-HLP::{floor_id}::{helper_id}"
        if self._current_id == nav_id:
            self.show_placeholder("Hilfslinie gelöscht")

    def clear(self) -> None:
        for element_id in list(self._editors):
            editor = self._editors.pop(element_id)
            self._stack.removeWidget(editor)
            editor.deleteLater()
        if self._global_editor is not None:
            self._stack.removeWidget(self._global_editor)
            self._global_editor.deleteLater()
            self._global_editor = None
        if self._multi_editor is not None:
            self._stack.removeWidget(self._multi_editor)
            self._multi_editor.deleteLater()
            self._multi_editor = None
        if self._helper_editor is not None:
            self._stack.removeWidget(self._helper_editor)
            self._helper_editor.deleteLater()
            self._helper_editor = None
        if self._helper_draw_panel is not None:
            self._stack.removeWidget(self._helper_draw_panel)
            self._helper_draw_panel.deleteLater()
            self._helper_draw_panel = None
        self.show_placeholder()
