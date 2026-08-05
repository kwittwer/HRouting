"""Eigenschaften-Dock: zeigt den Editor des selektierten Elements.

Die Editoren werden aus dem Schema erzeugt (siehe :mod:`model.schema`) und
zwischengespeichert, damit ein Wechsel der Auswahl schnell bleibt.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QWidget,
)

from gui.properties import GenericElementEditor, GlobalSettingsEditor
from model.document import Document
from model.schema import schema_for


class PropertiesDock(QDockWidget):
    """Zeigt die Eigenschaften des aktuell selektierten Elements."""

    field_changed = Signal(str, str, object)   # (element_id, key, wert)
    action_triggered = Signal(str, str)        # (element_id, action_id)
    setting_changed = Signal(str, object)      # (key, wert)
    pre_change = Signal()                      # fires before any write (for undo)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Eigenschaften", parent)
        self.setObjectName("dock_properties")

        self._document: Document | None = None
        self._editors: dict[str, GenericElementEditor] = {}
        self._global_editor: GlobalSettingsEditor | None = None
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
        if document is not None:
            self.show_global_settings()

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

    def show_placeholder(self, text: str = "Kein Element ausgewählt") -> None:
        self._placeholder.setText(text)
        self._current_id = ""
        self._stack.setCurrentWidget(self._placeholder)

    def clear(self) -> None:
        for element_id in list(self._editors):
            editor = self._editors.pop(element_id)
            self._stack.removeWidget(editor)
            editor.deleteLater()
        if self._global_editor is not None:
            self._stack.removeWidget(self._global_editor)
            self._global_editor.deleteLater()
            self._global_editor = None
        self.show_placeholder()
