"""Persistenz der benutzerdefinierten QColorDialog-Farben."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog


PARAMS_COLOR_DIALOG_CUSTOM_COLORS_KEY = "ui_color_dialog_custom_colors"


def capture_custom_colors() -> list[str]:
    """Liest alle benutzerdefinierten Farben des Qt-Farbdialogs als Hexliste."""
    colors: list[str] = []
    for idx in range(QColorDialog.customCount()):
        raw_color = QColorDialog.customColor(idx)
        if isinstance(raw_color, QColor):
            qcolor = raw_color
        else:
            try:
                qcolor = QColor.fromRgba(int(raw_color))
            except (TypeError, ValueError):
                # Unerwartete Rückgabetypen ignorieren statt Speichern zu blockieren.
                continue
        colors.append(qcolor.name(QColor.NameFormat.HexRgb))
    return colors


def apply_custom_colors(raw_colors: Sequence[object] | None) -> None:
    """Schreibt gespeicherte Farben in die benutzerdefinierte Dialogpalette."""
    if not isinstance(raw_colors, Sequence):
        return

    limit = min(QColorDialog.customCount(), len(raw_colors))
    for idx in range(limit):
        value = raw_colors[idx]
        if not isinstance(value, str):
            continue
        color = QColor(value)
        if not color.isValid():
            continue
        QColorDialog.setCustomColor(idx, color)
