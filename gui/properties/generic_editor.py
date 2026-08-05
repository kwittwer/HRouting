"""Aus dem Schema erzeugte Eigenschaften-Formulare."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
from model.elements import Element
from model.field_access import apply_display_value, display_value, get_field
from model.schema import ElementSchema, FieldSpec, GLOBAL_FIELDS, groups_of

from .field_widgets import FieldWidget, create_field_widget


class GenericElementEditor(QWidget):
    """Formular für ein Element, vollständig aus dem Schema erzeugt."""

    field_changed = Signal(str, str, object)  # (element_id, key, wert)
    action_triggered = Signal(str, str)       # (element_id, action_id)

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QLabel(f"<b>{schema.title}</b> · {element.id}", self)
        header.setWordWrap(True)
        layout.addWidget(header)

        for group_name, specs in groups_of(schema):
            layout.addWidget(self._build_group(group_name, specs))

        if schema.computed:
            layout.addWidget(self._build_computed_group())

        if schema.actions:
            layout.addWidget(self._build_actions())

        layout.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------
    def _build_group(self, title: str, specs: list[FieldSpec]) -> QGroupBox:
        box = QGroupBox(title, self)
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)
        for spec in specs:
            options = (
                spec.resolve_options(self._document)
                if spec.document_options is not None
                else None
            )
            widget = create_field_widget(spec, box, options)
            widget.value_changed.connect(self._on_field_changed)
            self._widgets[spec.key] = widget
            form.addRow(f"{spec.label}:", widget)
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
        for index, action in enumerate(self._schema.actions):
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
        apply_display_value(self._element, spec, value)
        self.field_changed.emit(self._element.id, key, value)
        self.refresh_computed()
        self._update_action_visibility()

    def refresh(self) -> None:
        """Alle Feldwerte aus dem Element übernehmen."""
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


class GlobalSettingsEditor(QWidget):
    """Formular für die globalen Projektparameter."""

    setting_changed = Signal(str, object)

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
        self._document.settings[key] = value
        self.setting_changed.emit(key, value)

    def refresh(self) -> None:
        for spec in GLOBAL_FIELDS:
            widget = self._widgets.get(spec.key)
            if widget is not None:
                widget.update_silently(
                    self._document.settings.get(spec.key, spec.default)
                )
