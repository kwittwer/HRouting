"""Werkzeugpalette – zeigt nur die Werkzeuge des aktiven Workspaces."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QToolButton,
    QWidget,
)

from gui.flow_layout import FlowLayout
from gui.tool_registry import ToolSpec


class ToolsDock(QDockWidget):
    """Kontextabhängige Werkzeugpalette."""

    tool_activated = Signal(str)  # ToolSpec.id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Werkzeuge", parent)
        self.setObjectName("dock_tools")

        self._container = QWidget(self)
        self._layout = FlowLayout(self._container, margin=6, h_spacing=4, v_spacing=4)
        self._container.setLayout(self._layout)
        self.setWidget(self._container)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}

    # ------------------------------------------------------------------
    def set_tools(self, tools: list[ToolSpec]) -> None:
        for button in self._buttons.values():
            self._group.removeButton(button)
            self._layout.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        self._buttons.clear()

        for tool in tools:
            button = QToolButton(self._container)
            button.setText(tool.label)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setCheckable(tool.checkable)
            button.setAutoRaise(True)
            if tool.icon:
                button.setIcon(QIcon(tool.icon))
            if tool.shortcut:
                button.setShortcut(tool.shortcut)
            button.setToolTip(tool.tooltip or tool.label)
            button.clicked.connect(
                lambda _checked=False, tool_id=tool.id: self.tool_activated.emit(tool_id)
            )
            self._group.addButton(button)
            self._layout.addWidget(button)
            self._buttons[tool.id] = button

    def set_active_tool(self, tool_id: str) -> None:
        button = self._buttons.get(tool_id)
        if button is not None:
            button.setChecked(True)

    def set_tool_enabled(self, tool_id: str, enabled: bool) -> None:
        button = self._buttons.get(tool_id)
        if button is not None:
            button.setEnabled(enabled)
