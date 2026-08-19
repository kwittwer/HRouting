"""Aus dem Schema erzeugte Eigenschaften-Formulare."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from model.computed import computed_values
from model.document import Document
from model.elements import Element, FloorPlan
from model.field_access import apply_display_value, display_value, get_field
from model.schema import ElementSchema, FieldSpec, GLOBAL_FIELDS, groups_of

from .field_widgets import (
    BoolFieldWidget,
    ChoiceFieldWidget,
    ColorFieldWidget,
    FieldWidget,
    FileFieldWidget,
    MultilineFieldWidget,
    NumberFieldWidget,
    ReadOnlyFieldWidget,
    TextFieldWidget,
    create_field_widget,
)


class _RefLengthFieldWidget(FieldWidget):
    """Editor for reference length with selectable input unit.

    Stored value remains in mm. Display unit can be mm / cm / m.
    """

    _UNIT_FACTORS = {
        "mm": 1.0,
        "cm": 10.0,
        "m": 1000.0,
    }

    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._spin = QDoubleSpinBox(self)
        self._spin.setRange(0.001, max(spec.maximum, 999_999_999.0))
        self._spin.setDecimals(3)
        self._spin.setSingleStep(1.0)
        self._spin.setKeyboardTracking(False)
        self._spin.valueChanged.connect(self._on_value_changed)

        self._unit = QComboBox(self)
        self._unit.addItems(["mm", "cm", "m"])
        self._unit.currentTextChanged.connect(self._on_unit_changed)

        layout.addWidget(self._spin, 1)
        layout.addWidget(self._unit)

        self._stored_mm = float(spec.default or 0.0)

    def _current_factor(self) -> float:
        return self._UNIT_FACTORS.get(self._unit.currentText(), 1.0)

    def _on_value_changed(self, display_value: float) -> None:
        self._stored_mm = float(display_value) * self._current_factor()
        self._emit(self._stored_mm)

    def _on_unit_changed(self, _unit: str) -> None:
        # Keep the same physical length, only change the representation.
        factor = self._current_factor()
        self._spin.blockSignals(True)
        self._spin.setValue(self._stored_mm / factor if factor else self._stored_mm)
        self._spin.blockSignals(False)

    def value(self) -> object:
        return self._stored_mm

    def set_value(self, value: object) -> None:
        try:
            self._stored_mm = float(value)
        except (TypeError, ValueError):
            self._stored_mm = float(self.spec.default or 0.0)
        factor = self._current_factor()
        self._spin.setValue(self._stored_mm / factor if factor else self._stored_mm)


class GenericElementEditor(QWidget):
    """Formular für ein Element, vollständig aus dem Schema erzeugt."""

    field_changed = Signal(str, str, object)  # (element_id, key, wert)
    action_triggered = Signal(str, str)       # (element_id, action_id)
    pre_change = Signal()                     # fires BEFORE the field write (for undo)

    def __init__(
        self,
        document: Document,
        element: Element,
        schema: ElementSchema,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._document = document
        self._element = element
        self._schema = schema
        self._widgets: dict[str, FieldWidget] = {}
        self._computed_labels: dict[str, QLabel] = {}
        self._header: QLabel | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._header = QLabel(self._header_text(), self)
        self._header.setWordWrap(True)
        self._header.setObjectName("element_header")
        layout.addWidget(self._header)

        for group_name, specs in groups_of(schema):
            layout.addWidget(self._build_group(group_name, specs))

        if schema.computed:
            layout.addWidget(self._build_computed_group())

        if schema.actions:
            layout.addWidget(self._build_actions())

        layout.addStretch(1)
        self.refresh()

    def _header_text(self) -> str:
        return f"<b>{self._schema.title}</b> · {self._element.id}"

    # ------------------------------------------------------------------
    def _build_group(self, title: str, specs: list[FieldSpec]) -> QGroupBox:
        box = QGroupBox(title, self)
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)
        for spec in specs:
            if isinstance(self._element, FloorPlan) and spec.key == "ref_length_mm":
                widget = _RefLengthFieldWidget(spec, box)
            else:
                options = (
                    spec.resolve_options(self._document)
                    if spec.document_options is not None
                    else None
                )
                widget = create_field_widget(spec, box, options)
            widget.value_changed.connect(self._on_field_changed)
            self._widgets[spec.key] = widget
            field_widget: QWidget = widget

            # Beim Grundriss steht neben der Referenzlänge ein expliziter
            # Aktualisieren-Button, der den Maßstab neu aus der Referenzlinie
            # berechnet.
            if isinstance(self._element, FloorPlan) and spec.key == "ref_length_mm":
                container = QWidget(box)
                row = QHBoxLayout(container)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4)
                row.addWidget(widget, 1)

                refresh_btn = QPushButton("Aktualisieren", container)
                refresh_btn.setToolTip(
                    "Maßstab aus Referenzlinie und Referenzlänge neu berechnen"
                )
                refresh_btn.clicked.connect(
                    lambda _checked=False: self.action_triggered.emit(
                        self._element.id, "recompute_scale"
                    )
                )
                side_actions = QWidget(container)
                side_layout = QVBoxLayout(side_actions)
                side_layout.setContentsMargins(0, 0, 0, 0)
                side_layout.setSpacing(4)
                side_layout.addWidget(refresh_btn)

                draw_ref_btn = QPushButton("Referenzlinie zeichnen", side_actions)
                draw_ref_btn.setToolTip("Neue Referenzlinie im Grundriss zeichnen")
                draw_ref_btn.clicked.connect(
                    lambda _checked=False: self.action_triggered.emit(
                        self._element.id, "draw_ref_line"
                    )
                )
                side_layout.addWidget(draw_ref_btn)
                side_layout.addStretch(1)

                row.addWidget(side_actions)
                field_widget = container

            form.addRow(f"{spec.label}:", field_widget)
        return box

    def _build_computed_group(self) -> QGroupBox:
        box = QGroupBox("Berechnung", self)
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)
        for key, label in self._schema.computed:
            value_label = QLabel("–", box)
            self._computed_labels[key] = value_label
            form.addRow(f"{label}:", value_label)
        return box

    def _build_actions(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._action_buttons: dict[str, QPushButton] = {}
        row: QHBoxLayout | None = None
        visible_actions = list(self._schema.actions)
        if isinstance(self._element, FloorPlan):
            # This action is presented next to reference length for floor plans.
            visible_actions = [a for a in visible_actions if a.id != "draw_ref_line"]

        for index, action in enumerate(visible_actions):
            if index % 2 == 0:
                row = QHBoxLayout()
                row.setSpacing(4)
                layout.addLayout(row)
            button = QPushButton(action.label, container)
            if action.tooltip:
                button.setToolTip(action.tooltip)
            if action.destructive:
                button.setStyleSheet("color: #e53935;")
            button.clicked.connect(
                lambda _checked=False, aid=action.id: self.action_triggered.emit(
                    self._element.id, aid
                )
            )
            assert row is not None
            row.addWidget(button)
            self._action_buttons[action.id] = button
        return container

    def _update_action_visibility(self) -> None:
        """Blendet Aktionen aus bzw. sperrt sie, wenn sie nicht anwendbar sind."""
        buttons = getattr(self, "_action_buttons", None)
        if not buttons:
            return
        values = {
            spec.key: get_field(self._element, spec) for spec in self._schema.fields
        }
        for action in self._schema.actions:
            button = buttons.get(action.id)
            if button is None:
                continue
            button.setVisible(action.is_visible_for(values))
            enabled = action.is_enabled_for(self._element)
            button.setEnabled(enabled)
            if not enabled:
                button.setToolTip("Noch keine Geometrie vorhanden")
            elif action.tooltip:
                button.setToolTip(action.tooltip)

    def _refresh_dynamic_options(self) -> None:
        """Aktualisiert Auswahllisten, die vom Projektinhalt abhängen."""
        for spec in self._schema.fields:
            if spec.document_options is None:
                continue
            widget = self._widgets.get(spec.key)
            if widget is not None and hasattr(widget, "set_options"):
                widget.set_options(spec.resolve_options(self._document))

    # ------------------------------------------------------------------
    def _on_field_changed(self, key: str, value: Any) -> None:
        spec = next((s for s in self._schema.fields if s.key == key), None)
        if spec is None:
            return
        self.pre_change.emit()  # snapshot BEFORE the write
        apply_display_value(self._element, spec, value)
        self.field_changed.emit(self._element.id, key, value)
        self.refresh_computed()
        self._update_action_visibility()

    def refresh(self) -> None:
        """Alle Feldwerte aus dem Element übernehmen."""
        if self._header is not None:
            self._header.setText(self._header_text())
        self._refresh_dynamic_options()
        for spec in self._schema.fields:
            widget = self._widgets.get(spec.key)
            if widget is not None:
                widget.update_silently(display_value(self._element, spec))
        self.refresh_computed()
        self._update_action_visibility()

    def refresh_computed(self) -> None:
        if not self._computed_labels:
            return
        values = computed_values(self._document, self._element)
        for key, label in self._computed_labels.items():
            label.setText(str(values.get(key, "–")))

    @property
    def element(self) -> Element:
        return self._element


class GenericMultiElementEditor(QWidget):
    """Formular für die gemeinsame Bearbeitung mehrerer Elemente."""

    field_changed = Signal(list, str, object)  # (element_ids, key, wert)
    pre_change = Signal()                      # fires BEFORE batch write (for undo)

    _MIXED_TEXT = "Verschiedene Werte"
    _MIXED_CHOICE = f"-- { _MIXED_TEXT } --"

    def __init__(
        self,
        document: Document,
        elements: list[Element],
        schema: ElementSchema,
        editable_specs: list[FieldSpec],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._document = document
        self._elements = list(elements)
        self._schema = schema
        self._specs = list(editable_specs)
        self._spec_by_key = {spec.key: spec for spec in self._specs}
        self._widgets: dict[str, FieldWidget] = {}
        self._mixed_keys: set[str] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        type_counts: dict[str, int] = {}
        for element in self._elements:
            label = str(getattr(type(element), "CATEGORY_LABEL", type(element).__name__) or type(element).__name__)
            type_counts[label] = type_counts.get(label, 0) + 1

        if len(type_counts) == 1:
            category = next(iter(type_counts.keys()))
            header_text = f"<b>{len(self._elements)}x {category}</b> · Gemeinsame Bearbeitung"
            type_detail = ""
        else:
            header_text = f"<b>{len(self._elements)}x Gemischte Auswahl</b> · Gemeinsame Bearbeitung"
            type_detail = "Typen: " + ", ".join(
                f"{count}x {label}" for label, count in sorted(type_counts.items())
            )

        ids_preview = ", ".join(element.id for element in self._elements[:8])
        if len(self._elements) > 8:
            ids_preview = f"{ids_preview}, ..."

        header = QLabel(header_text, self)
        header.setWordWrap(True)
        header.setStyleSheet("font-size: 14px;")
        layout.addWidget(header)

        detail_parts = []
        if type_detail:
            detail_parts.append(type_detail)
        detail_parts.append(f"Auswahl: {ids_preview}")
        detail = QLabel(" | ".join(detail_parts), self)
        detail.setWordWrap(True)
        detail.setStyleSheet("color: #9aa5b1;")
        layout.addWidget(detail)

        box = QGroupBox("Gemeinsame Eigenschaften", self)
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)
        for spec in self._specs:
            options = (
                spec.resolve_options(self._document)
                if spec.document_options is not None
                else None
            )
            widget = create_field_widget(spec, box, options)
            widget.value_changed.connect(self._on_field_changed)
            self._widgets[spec.key] = widget
            form.addRow(f"{spec.label}:", widget)
        layout.addWidget(box)
        layout.addStretch(1)

        self.refresh()

    def _set_mixed_state(self, spec: FieldSpec, widget: FieldWidget) -> None:
        self._mixed_keys.add(spec.key)
        if isinstance(widget, BoolFieldWidget):
            widget._box.blockSignals(True)
            widget._box.setTristate(True)
            widget._box.setCheckState(Qt.PartiallyChecked)
            widget._box.blockSignals(False)
            return
        if isinstance(widget, ChoiceFieldWidget):
            combo = widget._combo
            combo.blockSignals(True)
            idx = combo.findText(self._MIXED_CHOICE)
            if idx < 0:
                combo.insertItem(0, self._MIXED_CHOICE)
                idx = 0
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
            return
        if isinstance(widget, NumberFieldWidget):
            line_edit = widget._spin.lineEdit()
            if line_edit is not None:
                line_edit.setPlaceholderText(self._MIXED_TEXT)
                line_edit.clear()
            return
        if isinstance(widget, TextFieldWidget):
            widget._edit.setPlaceholderText(self._MIXED_TEXT)
            widget._edit.clear()
            return
        if isinstance(widget, FileFieldWidget):
            widget._edit.setPlaceholderText(self._MIXED_TEXT)
            widget._edit.clear()
            return
        if isinstance(widget, MultilineFieldWidget):
            widget._edit.setPlaceholderText(self._MIXED_TEXT)
            widget._edit.clear()
            return
        if isinstance(widget, ColorFieldWidget):
            widget._button.setText(self._MIXED_TEXT)
            widget._button.setStyleSheet("background-color: #6c757d; color: #ffffff;")
            return
        if isinstance(widget, ReadOnlyFieldWidget):
            widget._label.setText(self._MIXED_TEXT)

    def _clear_mixed_state(self, spec: FieldSpec, widget: FieldWidget) -> None:
        self._mixed_keys.discard(spec.key)
        if isinstance(widget, BoolFieldWidget):
            widget._box.setTristate(False)
            return
        if isinstance(widget, ChoiceFieldWidget):
            combo = widget._combo
            idx = combo.findText(self._MIXED_CHOICE)
            if idx >= 0:
                combo.removeItem(idx)

    def _on_field_changed(self, key: str, value: Any) -> None:
        spec = self._spec_by_key.get(key)
        if spec is None:
            return
        self.pre_change.emit()
        for element in self._elements:
            apply_display_value(element, spec, value)
        self.field_changed.emit([element.id for element in self._elements], key, value)
        self.refresh()

    def refresh(self) -> None:
        for spec in self._specs:
            widget = self._widgets.get(spec.key)
            if widget is None:
                continue
            values = [display_value(element, spec) for element in self._elements]
            if values and all(value == values[0] for value in values):
                self._clear_mixed_state(spec, widget)
                widget.update_silently(values[0])
            else:
                self._set_mixed_state(spec, widget)

    @property
    def element_ids(self) -> list[str]:
        return [element.id for element in self._elements]


class GlobalSettingsEditor(QWidget):
    """Formular für die globalen Projektparameter."""

    setting_changed = Signal(str, object)
    pre_change = Signal()  # fires BEFORE the settings write (for undo)

    def __init__(self, document: Document, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._widgets: dict[str, FieldWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(QLabel("<b>Projektparameter</b>", self))

        box = QGroupBox("Heizung", self)
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)
        for spec in GLOBAL_FIELDS:
            widget = create_field_widget(spec, box)
            widget.value_changed.connect(self._on_changed)
            self._widgets[spec.key] = widget
            form.addRow(f"{spec.label}:", widget)
        layout.addWidget(box)
        layout.addStretch(1)

        self.refresh()

    def _on_changed(self, key: str, value: Any) -> None:
        self.pre_change.emit()  # snapshot BEFORE the write
        self._document.settings[key] = value
        self.setting_changed.emit(key, value)

    def refresh(self) -> None:
        for spec in GLOBAL_FIELDS:
            widget = self._widgets.get(spec.key)
            if widget is not None:
                widget.update_silently(
                    self._document.settings.get(spec.key, spec.default)
                )
