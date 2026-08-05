"""Meldungs-Dock: zentrale Senke für Hinweise, Warnungen und Fehler."""

from __future__ import annotations

import time

from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QDockWidget, QPlainTextEdit, QWidget

_LEVEL_COLORS = {
    "info": "#dddddd",
    "success": "#66bb6a",
    "warning": "#ffb74d",
    "error": "#ef5350",
}


class LogDock(QDockWidget):
    """Einfaches Protokollfenster."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Meldungen", parent)
        self.setObjectName("dock_log")

        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(2000)
        self.setWidget(self._view)

    def log(self, message: str, level: str = "info") -> None:
        timestamp = time.strftime("%H:%M:%S")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(_LEVEL_COLORS.get(level, _LEVEL_COLORS["info"])))
        cursor = self._view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(f"[{timestamp}] {message}\n", fmt)
        self._view.setTextCursor(cursor)
        self._view.ensureCursorVisible()

    def info(self, message: str) -> None:
        self.log(message, "info")

    def success(self, message: str) -> None:
        self.log(message, "success")

    def warning(self, message: str) -> None:
        self.log(message, "warning")

    def error(self, message: str) -> None:
        self.log(message, "error")

    def clear(self) -> None:
        self._view.clear()
