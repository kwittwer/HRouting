"""Eigenschaften-Dock: zeigt das Panel des aktuell selektierten Elements."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QWidget,
)


class PropertiesDock(QDockWidget):
    """Hält die Parameter-Panels der Elemente und zeigt jeweils eines an."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Eigenschaften", parent)
        self.setObjectName("dock_properties")

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)

        self._stack = QStackedWidget(self._scroll)
        self._placeholder = QLabel("Kein Element ausgewählt")
        self._placeholder.setContentsMargins(12, 12, 12, 12)
        self._stack.addWidget(self._placeholder)

        self._scroll.setWidget(self._stack)
        self.setWidget(self._scroll)

        self._panels: dict[str, QWidget] = {}

    # ------------------------------------------------------------------
    def register_panel(self, element_id: str, panel: QWidget) -> None:
        old = self._panels.pop(element_id, None)
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        self._panels[element_id] = panel
        self._stack.addWidget(panel)

    def unregister_panel(self, element_id: str) -> None:
        panel = self._panels.pop(element_id, None)
        if panel is not None:
            self._stack.removeWidget(panel)
            panel.deleteLater()
        if not self._panels:
            self.show_placeholder()

    def show_element(self, element_id: str) -> None:
        panel = self._panels.get(element_id)
        if panel is None:
            self.show_placeholder()
            return
        self._stack.setCurrentWidget(panel)

    def show_placeholder(self, text: str = "Kein Element ausgewählt") -> None:
        self._placeholder.setText(text)
        self._stack.setCurrentWidget(self._placeholder)

    def clear(self) -> None:
        for element_id in list(self._panels):
            self.unregister_panel(element_id)
        self.show_placeholder()
