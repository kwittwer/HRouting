"""Kleine Konfigurationsdialoge für spezielle Anschlusspunkt-Typen.

Die umfangreichen Dialoge für Unterverteilung und Unterputz-Verteilung liegen
weiterhin in :mod:`gui.parameter_panel` und werden von dort wiederverwendet.
Die beiden schlanken Dialoge für Hausanschlusskasten und Zähler waren dort nur
als eingebetteter Code vorhanden und sind hier als eigenständige Klassen
verfügbar gemacht.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

PHASE_OPTIONS = ("3-phasig", "1-phasig")


class _SimpleConfigDialog(QDialog):
    """Basis für die schlanken Konfigurationsdialoge."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(340, 150)

        self._layout = QVBoxLayout(self)
        self._form = QFormLayout()
        self._layout.addLayout(self._form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._layout.addWidget(buttons)


class HakConfigDialog(_SimpleConfigDialog):
    """Hausanschlusskasten: Spannung und Hauptsicherung."""

    def __init__(self, config: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__("HAK konfigurieren", parent)
        config = config or {}

        self._voltage = QLineEdit(str(config.get("incoming_voltage") or "400V"))
        self._fuse = QLineEdit(str(config.get("main_fuse_a") or "63"))

        self._form.addRow("Spannung:", self._voltage)
        self._form.addRow("Hauptsicherung (A):", self._fuse)

    def get_config(self) -> dict:
        return {
            "incoming_voltage": self._voltage.text().strip() or "400V",
            "main_fuse_a": self._fuse.text().strip() or "63",
        }


class ZaehlerConfigDialog(_SimpleConfigDialog):
    """Zähler: Zählernummer und Phasenanzahl."""

    def __init__(self, config: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Zähler konfigurieren", parent)
        config = config or {}

        self._meter_id = QLineEdit(str(config.get("meter_id") or ""))
        self._phases = QComboBox()
        self._phases.addItems(list(PHASE_OPTIONS))
        current = str(config.get("phases") or PHASE_OPTIONS[0])
        index = self._phases.findText(current)
        if index >= 0:
            self._phases.setCurrentIndex(index)

        self._form.addRow("Zählernummer:", self._meter_id)
        self._form.addRow("Phasen:", self._phases)

    def get_config(self) -> dict:
        return {
            "meter_id": self._meter_id.text().strip(),
            "phases": self._phases.currentText(),
        }
