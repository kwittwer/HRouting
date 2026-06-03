# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import copy

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel,
    QLineEdit, QDoubleSpinBox, QPushButton, QGroupBox,
    QScrollArea, QHBoxLayout, QFrame, QColorDialog,
    QCheckBox, QFileDialog, QTextEdit, QComboBox,
    QTreeWidget, QTreeWidgetItem, QSplitter, QAbstractItemView,
    QDialog, QDialogButtonBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QTabWidget, QSizePolicy,
)
from pathlib import Path
from PySide6.QtGui import QColor, QPixmap, QPainter, QFont, QPen, QBrush, QFontMetrics
from PySide6.QtCore import Signal, Qt

# ── Custom Spinbox: Completely disable mouse wheel ────────────── #
class SafeDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that never responds to mouse wheel scrolling.
    Prevents accidental value changes completely - only direct input allowed."""
    def wheelEvent(self, event):
        event.ignore()

class SafeComboBox(QComboBox):
    """QComboBox that never responds to mouse wheel scrolling.
    Prevents accidental value changes while scrolling."""
    def wheelEvent(self, event):
        event.ignore()


class DragDropTreeWidget(QTreeWidget):
    items_dropped = Signal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.items_dropped.emit()

# ── Eingebaute Elektro-Symbole (DIN EN 60617) ─────────────────── #
# Fallback für PyInstaller
import sys as _sys

def _symbol_root() -> Path:
    if hasattr(_sys, '_MEIPASS'):
        return Path(_sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _symbol_label_from_stem(stem: str) -> str:
    text = stem.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else stem


def _discover_builtin_symbols() -> dict[str, str]:
    root = _symbol_root()
    symbol_dirs = [root / "icons"]
    allowed_suffixes = {".svg", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}

    result: dict[str, str] = {"(kein Symbol)": ""}
    used_labels: set[str] = set(result.keys())

    for directory in symbol_dirs:
        if not directory.is_dir():
            continue
        for file_path in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if not file_path.is_file() or file_path.suffix.lower() not in allowed_suffixes:
                continue
            base_label = _symbol_label_from_stem(file_path.stem)
            label = base_label
            idx = 2
            while label in used_labels:
                label = f"{base_label} ({idx})"
                idx += 1
            used_labels.add(label)
            result[label] = str(file_path)

    return result


BUILTIN_SYMBOLS: dict[str, str] = _discover_builtin_symbols()


# ================================================================== #
#  Heizkreis Panel                                                     #
# ================================================================== #

class HeatingCircuitPanel(QWidget):
    delete_requested           = Signal(str)
    draw_route_requested       = Signal(str)
    edit_polygon_requested     = Signal(str)
    edit_route_requested       = Signal(str)
    draw_supply_requested      = Signal(str)
    edit_supply_requested      = Signal(str)
    name_changed               = Signal(str, str)
    color_changed              = Signal(str, str)
    spacing_changed            = Signal(str)
    wall_dist_changed          = Signal(str)
    visibility_changed         = Signal(str, bool)
    label_size_changed         = Signal(str, float)
    label_visibility_changed   = Signal(str, bool)
    hydraulics_param_changed   = Signal(str)

    def __init__(self, circuit_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.circuit_id = circuit_id
        self._name = name or circuit_id
        self._color = QColor(color or "#2a9d8f")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda checked: self.visibility_changed.emit(self.circuit_id, checked)
        )
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(True)
        self.chk_label_visible.toggled.connect(
            lambda checked: self.label_visibility_changed.emit(self.circuit_id, checked)
        )
        form.addRow(self.chk_label_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda value: self.name_changed.emit(self.circuit_id, value)
        )
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.sb_diameter = SafeDoubleSpinBox()
        self.sb_diameter.setMinimum(0.01)
        self.sb_diameter.setMaximum(999999.0)
        self.sb_diameter.setSingleStep(0.05)
        self.sb_diameter.setValue(1.6)
        self.sb_diameter.setDecimals(2)
        self.sb_diameter.setSuffix(" cm")
        form.addRow("Rohrdurchmesser:", self.sb_diameter)

        self.sb_spacing = SafeDoubleSpinBox()
        self.sb_spacing.setMinimum(0.01)
        self.sb_spacing.setMaximum(999999.0)
        self.sb_spacing.setSingleStep(0.5)
        self.sb_spacing.setValue(15.0)
        self.sb_spacing.setSuffix(" cm")
        self.sb_spacing.valueChanged.connect(
            lambda _: self.spacing_changed.emit(self.circuit_id)
        )
        form.addRow("Verlegeabstand:", self.sb_spacing)

        self.sb_wall_dist = SafeDoubleSpinBox()
        self.sb_wall_dist.setMinimum(0.01)
        self.sb_wall_dist.setMaximum(999999.0)
        self.sb_wall_dist.setSingleStep(0.5)
        self.sb_wall_dist.setValue(20.0)
        self.sb_wall_dist.setSuffix(" cm")
        self.sb_wall_dist.valueChanged.connect(
            lambda _: self.wall_dist_changed.emit(self.circuit_id)
        )
        form.addRow("Randabstand:", self.sb_wall_dist)

        self.sb_label_size = SafeDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(12.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.circuit_id, v)
        )
        form.addRow("Schriftgr\u00f6\u00dfe:", self.sb_label_size)

        # ── Heizungstechnische Parameter ──
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#555;")
        form.addRow(sep)

        self.sb_room_temp = SafeDoubleSpinBox()
        self.sb_room_temp.setRange(-50.0, 200.0)
        self.sb_room_temp.setSingleStep(0.5)
        self.sb_room_temp.setValue(20.0)
        self.sb_room_temp.setDecimals(1)
        self.sb_room_temp.setSuffix(" \u00b0C")
        self.sb_room_temp.valueChanged.connect(
            lambda _: self.hydraulics_param_changed.emit(self.circuit_id)
        )
        form.addRow("Soll-Raumtemp.:", self.sb_room_temp)

        from logic.heating_calc import FLOOR_COVERINGS
        self.cb_floor_covering = SafeComboBox()
        for name in FLOOR_COVERINGS:
            self.cb_floor_covering.addItem(name)
        self.cb_floor_covering.setCurrentText("Fliesen / Keramik")
        self.cb_floor_covering.currentIndexChanged.connect(
            lambda _: self.hydraulics_param_changed.emit(self.circuit_id)
        )
        form.addRow("Fu\u00dfbodenbelag:", self.cb_floor_covering)

        self.cb_distributor = SafeComboBox()
        self.cb_distributor.addItem("")
        form.addRow("Heizkreisverteiler:", self.cb_distributor)

        for label, signal in [
            ("\u270f\ufe0f Polygon bearbeiten",       self.edit_polygon_requested),
            ("\u270f\ufe0f Rohrverlauf zeichnen",     self.draw_route_requested),
            ("\u270f\ufe0f Rohrverlauf bearbeiten",   self.edit_route_requested),
            ("\u270f\ufe0f Zuleitung zeichnen",       self.draw_supply_requested),
            ("\u270f\ufe0f Zuleitung bearbeiten",     self.edit_supply_requested),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, s=signal: s.emit(self.circuit_id))
            form.addRow(btn)

        self.btn_delete = QPushButton("\U0001f5d1\ufe0f L\u00f6schen")
        self.btn_delete.clicked.connect(
            lambda: self.delete_requested.emit(self.circuit_id)
        )
        form.addRow(self.btn_delete)

        self.lbl_area = QLabel("Fl\u00e4che: \u2013")
        self.lbl_area.setStyleSheet("font-weight:bold; color:#ffffff; padding:2px;")
        form.addRow("Fl\u00e4che:", self.lbl_area)

        self.lbl_perimeter = QLabel("Umfang: \u2013")
        self.lbl_perimeter.setStyleSheet("font-weight:bold; color:#9ad1ff; padding:2px;")
        form.addRow("Umfang:", self.lbl_perimeter)

        self.lbl_length = QLabel("Rohrl\u00e4nge: \u2013")
        self.lbl_length.setStyleSheet("font-weight:bold; color:#2dc653; padding:2px;")
        form.addRow(self.lbl_length)

        self.lbl_supply_length = QLabel("Zuleitung: \u2013")
        self.lbl_supply_length.setStyleSheet("font-weight:bold; color:#e9c46a; padding:2px;")
        form.addRow(self.lbl_supply_length)

        self.lbl_total_length = QLabel("Gesamt: \u2013")
        self.lbl_total_length.setStyleSheet("font-weight:bold; color:#ff6b6b; padding:2px;")
        form.addRow(self.lbl_total_length)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color:#555;")
        form.addRow(sep2)

        self.lbl_power = QLabel("Leistung: \u2013")
        self.lbl_power.setStyleSheet("font-weight:bold; color:#f4a261; padding:2px;")
        form.addRow(self.lbl_power)

        self.lbl_volume_flow = QLabel("Volumenstrom: \u2013")
        self.lbl_volume_flow.setStyleSheet("font-weight:bold; color:#4fc3f7; padding:2px;")
        form.addRow(self.lbl_volume_flow)

        self.lbl_pressure_drop = QLabel("Druckverlust: \u2013")
        self.lbl_pressure_drop.setStyleSheet("font-weight:bold; color:#e9c46a; padding:2px;")
        form.addRow(self.lbl_pressure_drop)

        layout.addLayout(form)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "Heizkreis-Farbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.circuit_id, self._color.name())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(f"background:{self._color.name()}; color:white;")

    def get_parameters(self) -> dict:
        return {
            "name":           self.le_name.text().strip() or self.circuit_id,
            "color":          self._color.name(),
            "diameter":       self.sb_diameter.value() * 10,
            "spacing":        self.sb_spacing.value() * 10,
            "wall_dist":      self.sb_wall_dist.value() * 10,
            "visible":        self.chk_visible.isChecked(),
            "label_visible":  self.chk_label_visible.isChecked(),
            "label_size":     self.sb_label_size.value(),
            "room_temp":      self.sb_room_temp.value(),
            "floor_covering": self.cb_floor_covering.currentText(),
            "distributor":    self.cb_distributor.currentText().strip(),
        }

    def set_length(self, length_mm: float):
        self.lbl_length.setText(f"Rohrl\u00e4nge: {length_mm / 1000:.2f} m")

    def set_supply_length(self, length_mm: float):
        self.lbl_supply_length.setText(f"Zuleitung: {length_mm / 1000:.2f} m")

    def set_total_length(self, route_mm: float, supply_mm: float):
        total = route_mm + supply_mm
        self.lbl_total_length.setText(f"Gesamt: {total / 1000:.2f} m")

    def set_area(self, area_mm2: float):
        self.lbl_area.setText(f"Fl\u00e4che: {area_mm2 / 1_000_000:.2f} m\u00b2")

    def set_perimeter(self, perimeter_mm: float):
        self.lbl_perimeter.setText(f"Umfang: {perimeter_mm / 1000:.2f} m")

    def set_hydraulics(self, power_w: float, volume_flow_lmin: float,
                       pressure_drop_mbar: float, q_wm2: float):
        self.lbl_power.setText(f"Leistung: {power_w:.0f} W  ({q_wm2:.1f} W/m\u00b2)")
        self.lbl_volume_flow.setText(f"Volumenstrom: {volume_flow_lmin:.2f} l/min")
        self.lbl_pressure_drop.setText(f"Druckverlust: {pressure_drop_mbar:.1f} mbar")

    def set_color(self, color: str):
        self._color = QColor(color)
        self._update_color_button()

    def set_name(self, name: str):
        self.le_name.setText(name)

    def update_hkv_choices(self, hkv_names: list[str]):
        """Refresh the HKV dropdown with the current list of HKV names."""
        current = self.cb_distributor.currentText()
        self.cb_distributor.blockSignals(True)
        self.cb_distributor.clear()
        self.cb_distributor.addItem("")
        for n in hkv_names:
            self.cb_distributor.addItem(n)
        idx = self.cb_distributor.findText(current)
        if idx >= 0:
            self.cb_distributor.setCurrentIndex(idx)
        self.cb_distributor.blockSignals(False)

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["circuit_id"] = self.circuit_id
        return d

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.circuit_id))
        self.set_color(d.get("color", self._color.name()))
        self.sb_diameter.setValue(d.get("diameter", 16.0) / 10)
        self.sb_spacing.setValue(d.get("spacing", 150.0) / 10)
        self.sb_wall_dist.setValue(d.get("wall_dist", 200.0) / 10)
        self.chk_visible.setChecked(d.get("visible", True))
        self.chk_label_visible.setChecked(d.get("label_visible", True))
        self.sb_label_size.setValue(d.get("label_size", 12.0))
        self.sb_room_temp.setValue(d.get("room_temp", 20.0))
        fc = d.get("floor_covering", "Fliesen / Keramik")
        idx = self.cb_floor_covering.findText(fc)
        if idx >= 0:
            self.cb_floor_covering.setCurrentIndex(idx)
        dist = d.get("distributor", "")
        idx = self.cb_distributor.findText(dist)
        if idx >= 0:
            self.cb_distributor.setCurrentIndex(idx)
        else:
            self.cb_distributor.addItem(dist)
            self.cb_distributor.setCurrentText(dist)


# ================================================================== #
#  Elektro: UV-Planung                                                 #
# ================================================================== #

# Standard phase colors used consistently in rail widget, dialog and PDF
PHASE_COLORS: dict[str, str] = {
    "L1": "#e53935",
    "L2": "#43a047",
    "L3": "#1e88e5",
    "N":  "#1565c0",
    "PE": "#558b2f",
    "L":  "#ff7043",
}

# Special phase name for 3-phase rotating busbar (L1→L2→L3 per TE)
THREE_PHASE_LABEL = "L1/L2/L3"
_THREE_PHASE_ROTATION = ["L1", "L2", "L3"]


class UvSlotEditPopup(QDialog):
    """Small dialog to edit a single TE slot in the UV rail view."""

    UV_DEVICE_TYPES: list[str] = [
        "",
        "Reserve",
        "Hauptschalter",
        "LS",
        "LS 3-polig",
        "FI",
        "FI 4-polig",
        "FI/LS",
        "Überspannungsschutz",
        "Motorschutz",
        "Schütz",
        "Zeitschalter",
        "Klemme",
        "Steckdose UV",
        "Freitext",
    ]

    def __init__(self, slot: dict, cable_choices: list[str],
                 max_rows: int = 12, max_modules: int = 36,
                 phase_info: str = "",
                 parent=None):
        super().__init__(parent)
        self._max_rows = max_rows
        self._max_modules = max_modules
        self._phase_info = phase_info
        row_no = slot.get("row", "?")
        slot_no = slot.get("slot", "?")
        self.setWindowTitle(f"Slot R{row_no} / TE {slot_no} bearbeiten")
        self.setMinimumWidth(380)
        self._build_ui(slot, cable_choices)

    def _build_ui(self, slot: dict, cable_choices: list[str]):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Phase (read-only, from busbar config)
        if self._phase_info:
            lbl_phase = QLabel(self._phase_info)
            lbl_phase.setStyleSheet(
                f"color: {PHASE_COLORS.get(self._phase_info, '#ffffff')}; font-weight: bold;"
            )
            form.addRow("Phase:", lbl_phase)

        # Position
        pos_row = QHBoxLayout()
        self.sb_row = QSpinBox()
        self.sb_row.setRange(1, self._max_rows)
        self.sb_row.setValue(int(slot.get("row", 1) or 1))
        self.sb_row.setToolTip("Reihe in der Unterverteilung")
        pos_row.addWidget(QLabel("Reihe:"))
        pos_row.addWidget(self.sb_row)
        pos_row.addSpacing(12)
        self.sb_slot = QSpinBox()
        self.sb_slot.setRange(1, self._max_modules)
        self.sb_slot.setValue(int(slot.get("slot", 1) or 1))
        self.sb_slot.setToolTip("TE-Position innerhalb der Reihe")
        pos_row.addWidget(QLabel("TE-Position:"))
        pos_row.addWidget(self.sb_slot)
        pos_row.addStretch()
        form.addRow("Position:", pos_row)

        self.cmb_device = SafeComboBox()
        self.cmb_device.addItems(self.UV_DEVICE_TYPES)
        self.cmb_device.setCurrentText(str(slot.get("device_type", "") or ""))
        form.addRow("Belegung:", self.cmb_device)

        self.sb_te_size = QSpinBox()
        self.sb_te_size.setRange(1, 8)
        self.sb_te_size.setValue(int(slot.get("te_size", 1) or 1))
        self.sb_te_size.setToolTip("Anzahl der belegten TE (z.B. FI = 2 TE)")
        form.addRow("TE-Breite:", self.sb_te_size)

        self.le_spec = QLineEdit(str(slot.get("spec", "") or ""))
        self.le_spec.setPlaceholderText("z.B. B16, Typ A 30mA, 63A …")
        self.le_spec.setToolTip("Typ-Kennzeichnung des Geräts (wird im Schaltplan angezeigt)")
        form.addRow("Typ/Kennz.:", self.le_spec)

        self.le_label = QLineEdit(str(slot.get("label", "") or ""))
        form.addRow("Bezeichnung:", self.le_label)

        self.le_manufacturer = QLineEdit(str(slot.get("manufacturer", "") or ""))
        self.le_manufacturer.setPlaceholderText("z.B. Hager, ABB, Siemens")
        form.addRow("Hersteller:", self.le_manufacturer)

        self.le_article_number = QLineEdit(str(slot.get("article_number", "") or ""))
        self.le_article_number.setPlaceholderText("z.B. MBN116")
        form.addRow("Artikelnummer:", self.le_article_number)

        self.cmb_assignment = SafeComboBox()
        self.cmb_assignment.setEditable(True)
        self.cmb_assignment.addItem("")
        for c in cable_choices:
            if c and self.cmb_assignment.findText(c) < 0:
                self.cmb_assignment.addItem(c)
        self.cmb_assignment.setCurrentText(str(slot.get("assignment", "") or ""))
        form.addRow("Kabel/Stromkreis:", self.cmb_assignment)

        self.le_note = QLineEdit(str(slot.get("note", "") or ""))
        form.addRow("Notiz:", self.le_note)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_slot_data(self) -> dict:
        return {
            "row": self.sb_row.value(),
            "slot": self.sb_slot.value(),
            "device_type": self.cmb_device.currentText().strip(),
            "te_size": self.sb_te_size.value(),
            "spec": self.le_spec.text().strip(),
            "label": self.le_label.text().strip(),
            "manufacturer": self.le_manufacturer.text().strip(),
            "article_number": self.le_article_number.text().strip(),
            "assignment": self.cmb_assignment.currentText().strip(),
            "note": self.le_note.text().strip(),
        }


# ------------------------------------------------------------------ #

class UvRailWidget(QWidget):
    """Widget that paints a visual DIN-rail (Hutschiene) layout of a UV."""

    slot_clicked = Signal(int, int)  # row_no, slot_no

    SLOT_W = 28        # px per TE unit
    ROW_H = 82         # full height of one row band
    ROW_GAP = 14       # gap between rows
    LEFT_MARGIN = 44   # space for row labels
    TOP_MARGIN = 20    # space for TE numbers above first row
    BOTTOM_MARGIN = 12

    DEVICE_COLORS: dict[str, str] = {
        "":                    "#383838",
        "Reserve":             "#606060",
        "Hauptschalter":       "#c0392b",
        "LS":                  "#1553b5",
        "LS 3-polig":          "#0d3d8a",
        "FI":                  "#b85d10",
        "FI 4-polig":          "#8a3a00",
        "FI/LS":               "#6b22bf",
        "Überspannungsschutz": "#9b0000",
        "Motorschutz":         "#1a7a3a",
        "Schütz":              "#007070",
        "Zeitschalter":        "#5a5a00",
        "Klemme":              "#9a7000",
        "Steckdose UV":        "#2c6e49",
        "Freitext":            "#444444",
    }
    DEVICE_SHORT: dict[str, str] = {
        "":                    "",
        "Reserve":             "Res",
        "Hauptschalter":       "HS",
        "LS":                  "LS",
        "LS 3-polig":          "LS3",
        "FI":                  "FI",
        "FI 4-polig":          "FI4",
        "FI/LS":               "FI/LS",
        "Überspannungsschutz": "ÜSS",
        "Motorschutz":         "MOT",
        "Schütz":              "SCH",
        "Zeitschalter":        "Zeit",
        "Klemme":              "KL",
        "Steckdose UV":        "SD",
        "Freitext":            "...",
    }

    slot_moved = Signal(int, int, int, int)  # from_row, from_slot, to_row, to_slot

    _DRAG_THRESHOLD = 6  # px before drag starts

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = 0
        self._modules = 0
        self._slots: list[dict] = []
        self._busbars: list[dict] = []
        self._cable_choices: list[str] = []
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        # drag state
        self._drag_source: tuple[int, int] | None = None   # (row_no, slot_no)
        self._drag_slot_data: dict | None = None
        self._drag_press_x = 0
        self._drag_press_y = 0
        self._drag_x = 0
        self._drag_y = 0
        self._is_dragging = False
        self._drop_target: tuple[int, int] | None = None   # (row_no, slot_no)

    # ── public ──────────────────────────────────────────────── #

    def set_data(self, rows: int, modules_per_row: int,
                 slots_list: list[dict],
                 cable_choices: list[str] | None = None,
                 busbars: list[dict] | None = None):
        self._rows = rows
        self._modules = modules_per_row
        self._slots = list(slots_list)
        self._busbars = list(busbars or [])
        self._cable_choices = list(cable_choices or [])
        # Minimum size based on compact slot width; actual rendering scales to widget size
        min_w = self.LEFT_MARGIN + min(modules_per_row, 12) * (self.SLOT_W // 2) + 10
        min_h = self.TOP_MARGIN + rows * (50 + self.ROW_GAP) + self.BOTTOM_MARGIN
        self.setMinimumSize(min_w, min_h)
        self.updateGeometry()
        self.update()

    def _slot_w(self) -> float:
        """Compute the actual TE slot width in pixels to fill the widget."""
        if self._modules == 0:
            return float(self.SLOT_W)
        avail = self.width() - self.LEFT_MARGIN - 10
        return max(14.0, avail / self._modules)

    def _row_h(self) -> float:
        """Compute the actual row height in pixels to fill the widget."""
        if self._rows == 0:
            return float(self.ROW_H)
        avail = self.height() - self.TOP_MARGIN - self.BOTTOM_MARGIN
        return max(50.0, (avail - (self._rows - 1) * self.ROW_GAP) / self._rows)

    def sizeHint(self):
        from PySide6.QtCore import QSize
        total_h = self.TOP_MARGIN + self._rows * (self.ROW_H + self.ROW_GAP) + self.BOTTOM_MARGIN
        total_w = self.LEFT_MARGIN + self._modules * self.SLOT_W + 10
        return QSize(total_w, total_h)

    # ── layout helper ───────────────────────────────────────── #

    def _build_row_layout(self, row_no: int) -> list[tuple[int, int, dict | None]]:
        """Returns [(start_te, te_size, slot_dict_or_None), ...] for every
        device/gap in the row in order, filling empty TE as single gaps."""
        slots_in_row = {
            s["slot"]: s
            for s in self._slots
            if s.get("row") == row_no
        }
        result: list[tuple[int, int, dict | None]] = []
        te = 1
        while te <= self._modules:
            if te in slots_in_row:
                slot = slots_in_row[te]
                ts = max(1, int(slot.get("te_size", 1) or 1))
                ts = min(ts, self._modules - te + 1)  # clamp to row end
                result.append((te, ts, slot))
                te += ts
            else:
                result.append((te, 1, None))
                te += 1
        return result

    # ── painting ─────────────────────────────────────────────── #

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Dynamic layout values – scale to actual widget size
        slot_w = self._slot_w()
        row_h  = self._row_h()
        te_num_h = max(12, int(row_h * 0.17))
        rail_zone = max(14, int(row_h * 0.22))

        # Background
        painter.fillRect(self.rect(), QColor("#2b2b2b"))

        font_row_lbl = QFont()
        font_row_lbl.setPointSize(8)
        font_row_lbl.setBold(True)

        font_te_num = QFont()
        font_te_num.setPointSize(6)

        font_type = QFont()
        font_type.setPointSize(max(5, int(row_h * 0.11)))
        font_type.setBold(True)

        font_spec = QFont()
        font_spec.setPointSize(max(5, int(row_h * 0.09)))

        font_lbl = QFont()
        font_lbl.setPointSize(max(5, int(row_h * 0.08)))

        font_note = QFont()
        font_note.setPointSize(max(4, int(row_h * 0.07)))

        for row_idx in range(self._rows):
            row_no = row_idx + 1
            row_y = self.TOP_MARGIN + row_idx * (row_h + self.ROW_GAP)

            # ─ Row label ─
            painter.setFont(font_row_lbl)
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(2, int(row_y), self.LEFT_MARGIN - 4, int(row_h),
                             Qt.AlignVCenter | Qt.AlignRight, f"R{row_no}")

            # ─ TE numbers (continuous across rows) ─
            te_offset = row_idx * self._modules
            painter.setFont(font_te_num)
            painter.setPen(QColor("#777777"))
            for te in range(1, self._modules + 1):
                te_x = self.LEFT_MARGIN + (te - 1) * slot_w
                painter.drawText(int(te_x), int(row_y), int(slot_w), te_num_h,
                                 Qt.AlignHCenter | Qt.AlignTop, str(te_offset + te))

            # ─ DIN rail bar ─
            rail_y = row_y + row_h - rail_zone
            rail_w = self._modules * slot_w
            painter.setPen(QPen(QColor("#888888"), 1))
            painter.setBrush(QBrush(QColor("#606060")))
            painter.drawRect(int(self.LEFT_MARGIN), int(rail_y), int(rail_w), 7)
            # rail notch lines
            painter.setPen(QPen(QColor("#999999"), 1))
            for te in range(0, self._modules + 1, 2):
                nx = self.LEFT_MARGIN + te * slot_w
                painter.drawLine(int(nx), int(rail_y + 2), int(nx), int(rail_y + 5))

            # ─ Slots ─
            layout = self._build_row_layout(row_no)
            for start_te, ts, slot in layout:
                x = self.LEFT_MARGIN + (start_te - 1) * slot_w
                w = ts * slot_w - 2
                device_type = slot.get("device_type", "") if slot else ""
                color_hex = self.DEVICE_COLORS.get(
                    device_type, self.DEVICE_COLORS[""]
                )

                slot_top = row_y + te_num_h
                slot_bottom = rail_y - 1
                slot_h = slot_bottom - slot_top

                if slot and device_type:
                    # Filled device block
                    painter.setBrush(QBrush(QColor(color_hex)))
                    painter.setPen(QPen(QColor("#111111"), 1))
                    painter.drawRoundedRect(int(x + 1), int(slot_top), int(w), int(slot_h), 3, 3)

                    # Type short label (top area)
                    short = self.DEVICE_SHORT.get(device_type, device_type[:5])
                    type_h = max(12, int(slot_h * 0.32))
                    painter.setFont(font_type)
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(int(x + 1), int(slot_top + 2), int(w), type_h,
                                     Qt.AlignHCenter | Qt.AlignVCenter, short)

                    # Spec / Typ-Kennzeichnung (e.g. "B16", "Typ A 30mA")
                    spec = str(slot.get("spec", "") or "").strip()
                    spec_h = max(10, int(slot_h * 0.24))
                    spec_y = slot_top + 2 + type_h
                    if spec:
                        painter.setFont(font_spec)
                        painter.setPen(QColor("#ffe08a"))
                        painter.drawText(int(x + 1), int(spec_y), int(w), spec_h,
                                         Qt.AlignHCenter | Qt.AlignVCenter, spec)

                    # Designation
                    label_y = spec_y + (spec_h if spec else 0)
                    label = str(slot.get("label", "") or "").strip()
                    if label:
                        label_h = max(8, int(slot_bottom - 14 - label_y))
                        painter.setFont(font_lbl)
                        painter.setPen(QColor("#dddddd"))
                        painter.drawText(int(x + 2), int(label_y), int(w - 4), label_h,
                                         Qt.AlignHCenter | Qt.AlignTop
                                         | Qt.TextWordWrap, label)

                    # Note (bottom)
                    note = str(slot.get("note", "") or "").strip()
                    if note:
                        painter.setFont(font_note)
                        painter.setPen(QColor("#aaaaaa"))
                        painter.drawText(int(x + 2), int(slot_bottom - 14), int(w - 4), 12,
                                         Qt.AlignHCenter | Qt.AlignVCenter, note)
                else:
                    # Empty slot placeholder
                    painter.setBrush(QBrush(QColor("#383838")))
                    painter.setPen(QPen(QColor("#4a4a4a"), 1))
                    painter.drawRect(int(x + 1), int(slot_top), int(w), int(slot_h))

            # ─ Busbar phase bands (drawn on top of slot area, just above DIN rail) ─
            if self._busbars:
                busbar_strip_h = max(5, int(row_h * 0.09))
                by = int(rail_y - busbar_strip_h)
                te_row_start = row_idx * self._modules + 1  # global TE of first slot in this row
                font_busbar = QFont()
                font_busbar.setPointSize(max(4, int(row_h * 0.07)))
                font_busbar.setBold(True)
                for bb in self._busbars:
                    bb_te_start = int(bb.get("te_start", 1) or 1)
                    bb_te_end = int(bb.get("te_end", 1) or 1)
                    row_te_end = te_row_start + self._modules - 1
                    vis_start = max(bb_te_start, te_row_start)
                    vis_end = min(bb_te_end, row_te_end)
                    if vis_start > vis_end:
                        continue
                    bb_phase = str(bb.get("phase", "") or "")
                    if bb_phase == THREE_PHASE_LABEL:
                        # Dreiphasige Sammelschiene: jede TE einzeln einfärben
                        for te_g in range(vis_start, vis_end + 1):
                            local_te = te_g - te_row_start  # 0-based within row
                            te_bx = int(self.LEFT_MARGIN + local_te * slot_w)
                            te_bw = int(slot_w)
                            phase_idx = (te_g - bb_te_start) % 3
                            te_phase = _THREE_PHASE_ROTATION[phase_idx]
                            te_color = PHASE_COLORS.get(te_phase, "#888888")
                            painter.fillRect(te_bx, by, te_bw, busbar_strip_h, QColor(te_color))
                            painter.setFont(font_busbar)
                            painter.setPen(QColor("#ffffff"))
                            painter.drawText(te_bx + 1, by, te_bw - 2, busbar_strip_h,
                                             Qt.AlignHCenter | Qt.AlignVCenter, te_phase)
                    else:
                        local_start = vis_start - te_row_start  # 0-based within row
                        local_end = vis_end - te_row_start      # 0-based within row
                        bx = int(self.LEFT_MARGIN + local_start * slot_w)
                        bw_bb = int((local_end - local_start + 1) * slot_w)
                        bb_color = str(bb.get("color", "#888888") or "#888888")
                        painter.fillRect(bx, by, bw_bb, busbar_strip_h, QColor(bb_color))
                        if bb_phase:
                            painter.setFont(font_busbar)
                            painter.setPen(QColor("#ffffff"))
                            painter.drawText(bx + 2, by, bw_bb - 4, busbar_strip_h,
                                             Qt.AlignVCenter | Qt.AlignLeft, bb_phase)

        # ── Drag overlay ─
        if self._is_dragging and self._drag_slot_data is not None:
            sd = self._drag_slot_data
            device_type = str(sd.get("device_type", "") or "")
            drag_ts = max(1, int(sd.get("te_size", 1) or 1))
            ow = int(drag_ts * slot_w - 2)
            oh = int(row_h - rail_zone - te_num_h)
            ox = self._drag_x - ow // 2
            oy = self._drag_y - oh // 2
            color_hex = self.DEVICE_COLORS.get(device_type, self.DEVICE_COLORS[""])
            dc = QColor(color_hex)
            dc.setAlpha(200)
            painter.setBrush(QBrush(dc))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawRoundedRect(ox, oy, ow, oh, 4, 4)
            short = self.DEVICE_SHORT.get(device_type, device_type[:5])
            painter.setFont(font_type)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(ox, oy, ow, oh, Qt.AlignCenter, short)

            # Drop target highlight
            if self._drop_target is not None:
                tr, tslot = self._drop_target
                for row_idx2 in range(self._rows):
                    if row_idx2 + 1 == tr:
                        ty = self.TOP_MARGIN + row_idx2 * (row_h + self.ROW_GAP)
                        tx = self.LEFT_MARGIN + (tslot - 1) * slot_w
                        tw = int(drag_ts * slot_w - 2)
                        th = int(row_h - rail_zone - te_num_h)
                        painter.setBrush(Qt.NoBrush)
                        painter.setPen(QPen(QColor("#00e5ff"), 2, Qt.DashLine))
                        painter.drawRoundedRect(int(tx + 1), int(ty + te_num_h), tw, th, 4, 4)

        painter.end()

    # ── hit-test helper ──────────────────────────────────────── #

    def _hit_slot(self, x: int, y: int) -> tuple[int, int] | None:
        """Return (row_no, slot_no) for the logical slot at pixel (x, y), or None."""
        if x < self.LEFT_MARGIN:
            return None
        slot_w = self._slot_w()
        row_h  = self._row_h()
        for row_idx in range(self._rows):
            row_no = row_idx + 1
            row_y = self.TOP_MARGIN + row_idx * (row_h + self.ROW_GAP)
            if row_y <= y <= row_y + row_h:
                col = int((x - self.LEFT_MARGIN) / slot_w)
                te_clicked = col + 1
                if 1 <= te_clicked <= self._modules:
                    layout = self._build_row_layout(row_no)
                    for start_te, ts, _ in layout:
                        if start_te <= te_clicked < start_te + ts:
                            return (row_no, start_te)
                return None
        return None

    # ── mouse ────────────────────────────────────────────────── #

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x, y = event.x(), event.y()
        hit = self._hit_slot(x, y)
        if hit is not None:
            row_no, slot_no = hit
            # Only draggable if slot has content
            slot_data = next(
                (s for s in self._slots if s.get("row") == row_no and s.get("slot") == slot_no),
                None,
            )
            self._drag_source = hit
            self._drag_slot_data = dict(slot_data) if slot_data else None
            self._drag_press_x = x
            self._drag_press_y = y
            self._drag_x = x
            self._drag_y = y
            self._is_dragging = False
            self._drop_target = None

    def mouseMoveEvent(self, event):
        if self._drag_source is None:
            return
        x, y = event.x(), event.y()
        if not self._is_dragging:
            if (abs(x - self._drag_press_x) > self._DRAG_THRESHOLD
                    or abs(y - self._drag_press_y) > self._DRAG_THRESHOLD):
                if self._drag_slot_data is not None:
                    self._is_dragging = True
                    self.setCursor(Qt.ClosedHandCursor)
                else:
                    # empty slot – cancel drag attempt
                    self._drag_source = None
                    return
        if self._is_dragging:
            self._drag_x = x
            self._drag_y = y
            self._drop_target = self._hit_slot(x, y)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        was_dragging = self._is_dragging
        source = self._drag_source
        drop = self._drop_target
        # Reset drag state first
        self._drag_source = None
        self._drag_slot_data = None
        self._is_dragging = False
        self._drop_target = None
        self.setCursor(Qt.PointingHandCursor)
        self.update()

        if was_dragging:
            if (source is not None and drop is not None
                    and source != drop):
                self.slot_moved.emit(source[0], source[1], drop[0], drop[1])
        else:
            # Plain click
            if source is not None:
                self.slot_clicked.emit(source[0], source[1])


# ------------------------------------------------------------------ #

class UvConfigDialog(QDialog):
    MAX_UV_ROWS = 12
    MAX_UV_MODULES = 36
    UV_PRESETS: list[tuple[str, tuple[int, int]]] = [
        ("1-reihig / 12 TE", (1, 12)),
        ("2-reihig / 12 TE", (2, 12)),
        ("3-reihig / 12 TE", (3, 12)),
        ("4-reihig / 12 TE", (4, 12)),
        ("2-reihig / 18 TE", (2, 18)),
        ("3-reihig / 18 TE", (3, 18)),
        ("Benutzerdefiniert", (0, 0)),
    ]
    UV_DEVICE_TYPES: list[str] = [
        "",
        "Reserve",
        "Hauptschalter",
        "LS",
        "LS 3-polig",
        "FI",
        "FI 4-polig",
        "FI/LS",
        "Überspannungsschutz",
        "Motorschutz",
        "Schütz",
        "Zeitschalter",
        "Klemme",
        "Steckdose UV",
        "Freitext",
    ]

    def __init__(self, config: dict | None = None,
                 cable_choices: list[str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("UV planen")
        self.resize(1100, 740)
        self.setSizeGripEnabled(True)
        self._cable_choices = list(cable_choices or [])
        self._building = False
        self._build_ui()
        self._load_config(config or {})

    def _build_ui(self):
        root = QVBoxLayout(self)

        # ─ Top form (preset / dimensions) ─
        form = QFormLayout()
        self.cmb_preset = SafeComboBox()
        for label, dims in self.UV_PRESETS:
            self.cmb_preset.addItem(label, dims)
        self.cmb_preset.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("Preset:", self.cmb_preset)

        dims_row = QHBoxLayout()
        self.sb_rows = QSpinBox()
        self.sb_rows.setRange(1, self.MAX_UV_ROWS)
        self.sb_rows.valueChanged.connect(self._on_dimensions_changed)
        dims_row.addWidget(self.sb_rows)

        self.sb_modules = QSpinBox()
        self.sb_modules.setRange(1, self.MAX_UV_MODULES)
        self.sb_modules.valueChanged.connect(self._on_dimensions_changed)
        dims_row.addWidget(QLabel("×"))
        dims_row.addWidget(self.sb_modules)
        form.addRow("Raster (Reihen × TE):", dims_row)

        self.lbl_summary = QLabel("")
        form.addRow("Zusammenfassung:", self.lbl_summary)
        root.addLayout(form)

        # ─ Tabs: Visuell | Tabelle ─
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)

        # Tab 0 – visual rail view
        self.rail_widget = UvRailWidget()
        rail_scroll = QScrollArea()
        rail_scroll.setWidgetResizable(True)
        rail_scroll.setWidget(self.rail_widget)
        self.tabs.addTab(rail_scroll, "Visuell")
        self.rail_widget.slot_clicked.connect(self._on_rail_slot_clicked)
        self.rail_widget.slot_moved.connect(self._on_slot_moved)

        # Tab 1 – editable table
        # Columns: Reihe | TE | Belegung | TE-Br. | Typ/Kennz. | Bezeichnung |
        #          Kabel/Stromkreis | Hersteller | Artikelnummer | Notiz
        self.tbl_slots = QTableWidget(0, 10)
        self.tbl_slots.setHorizontalHeaderLabels(
            [
                "Reihe", "TE", "Belegung", "TE-Br.", "Typ/Kennz.", "Bezeichnung",
                "Kabel/Stromkreis", "Hersteller", "Artikelnummer", "Notiz",
            ]
        )
        hdr = self.tbl_slots.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.Stretch)
        hdr.setSectionResizeMode(7, QHeaderView.Stretch)
        hdr.setSectionResizeMode(8, QHeaderView.Stretch)
        hdr.setSectionResizeMode(9, QHeaderView.Stretch)
        self.tbl_slots.verticalHeader().setVisible(False)
        self.tabs.addTab(self.tbl_slots, "Tabelle")

        # Tab 2 – Phasenschienen (busbars)
        busbar_tab = QWidget()
        busbar_layout = QVBoxLayout(busbar_tab)
        busbar_btn_row = QHBoxLayout()
        btn_add_bb = QPushButton("+ Phasenschiene")
        btn_add_bb.clicked.connect(lambda: self._add_busbar_row())
        btn_del_bb = QPushButton("− Entfernen")
        btn_del_bb.clicked.connect(self._remove_busbar_row)
        busbar_btn_row.addWidget(btn_add_bb)
        busbar_btn_row.addWidget(btn_del_bb)
        busbar_btn_row.addStretch()
        busbar_layout.addLayout(busbar_btn_row)

        self.tbl_busbars = QTableWidget(0, 4)
        self.tbl_busbars.setHorizontalHeaderLabels(["Phase", "Farbe", "TE-Start", "TE-Ende"])
        bb_hdr = self.tbl_busbars.horizontalHeader()
        bb_hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        bb_hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        bb_hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        bb_hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_busbars.verticalHeader().setVisible(False)
        busbar_layout.addWidget(self.tbl_busbars)

        lbl_hint = QLabel(
            "Globale TE-Nummern (durchgehend über alle Reihen).\n"
            "Standardfarben: L1=rot, L2=grün, L3=blau, N=dunkelblau, PE=dunkelgrün"
        )
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #888888; font-size: 10px;")
        busbar_layout.addWidget(lbl_hint)
        self.tabs.addTab(busbar_tab, "Phasenschienen")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _capture_current_slots(self) -> list[dict]:
        """Read all rows from the table widget into a list of slot dicts.
        Columns: 0=Reihe, 1=TE, 2=Belegung(cmb), 3=TE-Breite(spinbox),
                 4=Typ/Kennz., 5=Bezeichnung, 6=Kabel/Stromkreis(cmb),
                 7=Hersteller, 8=Artikelnummer, 9=Notiz"""
        slots: list[dict] = []
        for row_idx in range(self.tbl_slots.rowCount()):
            row_item = self.tbl_slots.item(row_idx, 0)
            slot_item = self.tbl_slots.item(row_idx, 1)
            if row_item is None or slot_item is None:
                continue
            try:
                row_no = int(row_item.text())
                slot_no = int(slot_item.text())
            except (TypeError, ValueError):
                continue
            device_combo = self.tbl_slots.cellWidget(row_idx, 2)
            te_sb = self.tbl_slots.cellWidget(row_idx, 3)
            spec_item = self.tbl_slots.item(row_idx, 4)
            label_item = self.tbl_slots.item(row_idx, 5)
            assignment_combo = self.tbl_slots.cellWidget(row_idx, 6)
            manufacturer_item = self.tbl_slots.item(row_idx, 7)
            article_number_item = self.tbl_slots.item(row_idx, 8)
            note_item = self.tbl_slots.item(row_idx, 9)
            slots.append({
                "row": row_no,
                "slot": slot_no,
                "device_type": device_combo.currentText().strip() if isinstance(device_combo, QComboBox) else "",
                "te_size": te_sb.value() if isinstance(te_sb, QSpinBox) else 1,
                "spec": spec_item.text().strip() if spec_item else "",
                "label": label_item.text().strip() if label_item else "",
                "assignment": assignment_combo.currentText().strip() if isinstance(assignment_combo, QComboBox) else "",
                "manufacturer": manufacturer_item.text().strip() if manufacturer_item else "",
                "article_number": article_number_item.text().strip() if article_number_item else "",
                "note": note_item.text().strip() if note_item else "",
            })
        return slots

    def _indexed_slots(self, slots: list[dict], rows: int, modules_per_row: int) -> dict[tuple[int, int], dict]:
        indexed: dict[tuple[int, int], dict] = {}
        for slot in slots:
            try:
                row_no = int(slot.get("row", 0))
                slot_no = int(slot.get("slot", 0))
            except (TypeError, ValueError):
                continue
            if 1 <= row_no <= rows and 1 <= slot_no <= modules_per_row:
                indexed[(row_no, slot_no)] = {
                    "row": row_no,
                    "slot": slot_no,
                    "device_type": str(slot.get("device_type", "") or "").strip(),
                    "te_size": int(slot.get("te_size", 1) or 1),
                    "spec": str(slot.get("spec", "") or "").strip(),
                    "label": str(slot.get("label", "") or "").strip(),
                    "assignment": str(slot.get("assignment", "") or "").strip(),
                    "manufacturer": str(slot.get("manufacturer", "") or "").strip(),
                    "article_number": str(slot.get("article_number", "") or "").strip(),
                    "note": str(slot.get("note", "") or "").strip(),
                }
        return indexed

    def _rebuild_table(self, slots: list[dict] | None = None):
        """(Re-)populate the table and refresh the visual rail.
        Columns: 0=Reihe, 1=TE, 2=Belegung(cmb), 3=TE-Breite(spinbox),
                 4=Bezeichnung, 5=Kabel/Stromkreis(cmb), 6=Notiz"""
        rows = self.sb_rows.value()
        modules_per_row = self.sb_modules.value()
        slot_map = self._indexed_slots(slots or [], rows, modules_per_row)
        self.tbl_slots.setRowCount(rows * modules_per_row)
        for row_no in range(1, rows + 1):
            for slot_no in range(1, modules_per_row + 1):
                idx = (row_no - 1) * modules_per_row + (slot_no - 1)
                slot = slot_map.get((row_no, slot_no), {})

                row_item = QTableWidgetItem(str(row_no))
                row_item.setFlags(row_item.flags() & ~Qt.ItemIsEditable)
                self.tbl_slots.setItem(idx, 0, row_item)

                slot_item = QTableWidgetItem(str(slot_no))
                slot_item.setFlags(slot_item.flags() & ~Qt.ItemIsEditable)
                self.tbl_slots.setItem(idx, 1, slot_item)

                # col 2 – device type
                cmb_device = SafeComboBox()
                cmb_device.addItems(self.UV_DEVICE_TYPES)
                cmb_device.setCurrentText(str(slot.get("device_type", "") or "").strip())
                cmb_device.currentTextChanged.connect(self._refresh_visual)
                self.tbl_slots.setCellWidget(idx, 2, cmb_device)

                # col 3 – TE-Breite
                sb_te = QSpinBox()
                sb_te.setRange(1, 8)
                sb_te.setValue(int(slot.get("te_size", 1) or 1))
                sb_te.setToolTip("Anzahl belegter TE")
                sb_te.valueChanged.connect(self._refresh_visual)
                self.tbl_slots.setCellWidget(idx, 3, sb_te)

                # col 4 – Typ/Kennz.
                self.tbl_slots.setItem(
                    idx, 4, QTableWidgetItem(str(slot.get("spec", "") or "").strip())
                )

                # col 5 – Bezeichnung
                self.tbl_slots.setItem(
                    idx, 5, QTableWidgetItem(str(slot.get("label", "") or "").strip())
                )

                # col 6 – Kabel/Stromkreis
                cmb_assignment = SafeComboBox()
                cmb_assignment.setEditable(True)
                cmb_assignment.addItem("")
                for choice in self._cable_choices:
                    if choice and cmb_assignment.findText(choice) < 0:
                        cmb_assignment.addItem(choice)
                cmb_assignment.setCurrentText(str(slot.get("assignment", "") or "").strip())
                self.tbl_slots.setCellWidget(idx, 6, cmb_assignment)

                # col 7 – Hersteller
                self.tbl_slots.setItem(
                    idx, 7, QTableWidgetItem(str(slot.get("manufacturer", "") or "").strip())
                )

                # col 8 – Artikelnummer
                self.tbl_slots.setItem(
                    idx, 8, QTableWidgetItem(str(slot.get("article_number", "") or "").strip())
                )

                # col 9 – Notiz
                self.tbl_slots.setItem(
                    idx, 9, QTableWidgetItem(str(slot.get("note", "") or "").strip())
                )

        self.lbl_summary.setText(f"{rows} Reihen mit je {modules_per_row} TE")
        self._refresh_visual()

    def _refresh_visual(self):
        """Update the rail widget from current table state (no full rebuild)."""
        self.rail_widget.set_data(
            self.sb_rows.value(),
            self.sb_modules.value(),
            self._capture_current_slots(),
            self._cable_choices,
            busbars=self._capture_current_busbars(),
        )

    def _write_slot_to_table(self, row_no: int, slot_no: int, data: dict):
        """Write slot data dict into the table at (row_no, slot_no)."""
        mpr = self.sb_modules.value()
        tbl_idx = (row_no - 1) * mpr + (slot_no - 1)
        row_count = self.tbl_slots.rowCount()
        if not (0 <= tbl_idx < row_count):
            return
        cmb_d = self.tbl_slots.cellWidget(tbl_idx, 2)
        if isinstance(cmb_d, QComboBox):
            cmb_d.setCurrentText(data.get("device_type", ""))
        te_sb = self.tbl_slots.cellWidget(tbl_idx, 3)
        if isinstance(te_sb, QSpinBox):
            te_sb.setValue(int(data.get("te_size", 1) or 1))
        spec_item = self.tbl_slots.item(tbl_idx, 4)
        if spec_item:
            spec_item.setText(data.get("spec", ""))
        lbl_item = self.tbl_slots.item(tbl_idx, 5)
        if lbl_item:
            lbl_item.setText(data.get("label", ""))
        cmb_a = self.tbl_slots.cellWidget(tbl_idx, 6)
        if isinstance(cmb_a, QComboBox):
            cmb_a.setCurrentText(data.get("assignment", ""))
        manufacturer_item = self.tbl_slots.item(tbl_idx, 7)
        if manufacturer_item:
            manufacturer_item.setText(data.get("manufacturer", ""))
        article_number_item = self.tbl_slots.item(tbl_idx, 8)
        if article_number_item:
            article_number_item.setText(data.get("article_number", ""))
        note_item = self.tbl_slots.item(tbl_idx, 9)
        if note_item:
            note_item.setText(data.get("note", ""))

    def _clear_slot_in_table(self, row_no: int, slot_no: int):
        """Reset a table row to empty values."""
        self._write_slot_to_table(row_no, slot_no, {
            "device_type": "", "te_size": 1,
            "spec": "", "label": "", "assignment": "",
            "manufacturer": "", "article_number": "", "note": "",
        })

    def _on_rail_slot_clicked(self, row_no: int, slot_no: int):
        """Slot in rail was clicked: open edit popup, write result back."""
        current_slots = self._capture_current_slots()
        slot_data: dict = {"row": row_no, "slot": slot_no}
        for s in current_slots:
            if s["row"] == row_no and s["slot"] == slot_no:
                slot_data = dict(s)
                break

        # Compute phase info for this slot (global TE number)
        mpr = self.sb_modules.value()
        te_global = (row_no - 1) * mpr + slot_no
        phase_info = ""
        for bb in self._capture_current_busbars():
            bb_te_s = int(bb.get("te_start", 0))
            bb_te_e = int(bb.get("te_end", 0))
            if bb_te_s <= te_global <= bb_te_e:
                bb_phase = str(bb.get("phase", "") or "")
                if bb_phase == THREE_PHASE_LABEL:
                    # Resolve the actual phase for this specific TE
                    phase_idx = (te_global - bb_te_s) % 3
                    phase_info = _THREE_PHASE_ROTATION[phase_idx]
                else:
                    phase_info = bb_phase
                break

        dlg = UvSlotEditPopup(
            slot_data, self._cable_choices,
            max_rows=self.sb_rows.value(),
            max_modules=self.sb_modules.value(),
            phase_info=phase_info,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        updated = dlg.get_slot_data()
        new_row = updated["row"]
        new_slot = updated["slot"]

        # If position changed, move slot
        if new_row != row_no or new_slot != slot_no:
            self._on_slot_moved(row_no, slot_no, new_row, new_slot)
            # Update data at new position
            self._write_slot_to_table(new_row, new_slot, updated)
        else:
            self._write_slot_to_table(row_no, slot_no, updated)

        self.tabs.setCurrentIndex(0)
        self._refresh_visual()

    def _on_slot_moved(self, from_row: int, from_slot: int,
                       to_row: int, to_slot: int):
        """Move slot data from one position to another (swap if target occupied)."""
        current_slots = self._capture_current_slots()
        src_data: dict | None = None
        dst_data: dict | None = None
        for s in current_slots:
            if s["row"] == from_row and s["slot"] == from_slot:
                src_data = dict(s)
            if s["row"] == to_row and s["slot"] == to_slot:
                dst_data = dict(s)

        if src_data is None:
            return  # nothing to move

        # Write source data to target
        self._write_slot_to_table(to_row, to_slot, src_data)
        # Write target data (or blanks) to source
        if dst_data is not None:
            self._write_slot_to_table(from_row, from_slot, dst_data)
        else:
            self._clear_slot_in_table(from_row, from_slot)

        self._refresh_visual()

    def _preset_index_for_dims(self, rows: int, modules_per_row: int) -> int:
        for idx, (_label, dims) in enumerate(self.UV_PRESETS):
            if dims == (rows, modules_per_row):
                return idx
        return len(self.UV_PRESETS) - 1

    def _load_config(self, config: dict):
        rows = int(config.get("rows", 2) or 2)
        modules_per_row = int(config.get("modules_per_row", 12) or 12)
        slots = config.get("slots", [])
        busbars = config.get("busbars", [])
        self._building = True
        self.sb_rows.setValue(rows)
        self.sb_modules.setValue(modules_per_row)
        self.cmb_preset.setCurrentIndex(self._preset_index_for_dims(rows, modules_per_row))
        self._building = False
        self._rebuild_table(slots if isinstance(slots, list) else [])
        # Load busbars
        self.tbl_busbars.setRowCount(0)
        for bb in (busbars if isinstance(busbars, list) else []):
            self._add_busbar_row(bb)

    def _on_preset_changed(self):
        if self._building:
            return
        slots = self._capture_current_slots()
        dims = self.cmb_preset.currentData()
        if isinstance(dims, tuple) and dims != (0, 0):
            self._building = True
            self.sb_rows.setValue(int(dims[0]))
            self.sb_modules.setValue(int(dims[1]))
            self._building = False
        self._rebuild_table(slots)

    def _on_dimensions_changed(self):
        if self._building:
            return
        slots = self._capture_current_slots()
        self._building = True
        self.cmb_preset.setCurrentIndex(
            self._preset_index_for_dims(self.sb_rows.value(), self.sb_modules.value())
        )
        self._building = False
        self._rebuild_table(slots)

    def get_config(self) -> dict:
        rows = self.sb_rows.value()
        modules_per_row = self.sb_modules.value()
        slots: list[dict] = []
        for slot in self._capture_current_slots():
            if self._slot_has_content(slot):
                slots.append(slot)
        return {
            "rows": rows,
            "modules_per_row": modules_per_row,
            "preset": self.cmb_preset.currentText(),
            "slots": slots,
            "busbars": self._capture_current_busbars(),
        }

    @staticmethod
    def _slot_has_content(slot: dict) -> bool:
        if int(slot.get("te_size", 1) or 1) > 1:
            return True
        return any(
            str(slot.get(key, "") or "").strip()
            for key in (
                "device_type", "spec", "label", "assignment",
                "manufacturer", "article_number", "note",
            )
        )


class BomMetadataDialog(QDialog):
    """Editor for BOM catalog metadata and manual BOM rows."""

    SECTION_CHOICES: list[tuple[str, str]] = [
        ("hk_bom_rows", "Heizrohr"),
        ("cable_bom_rows", "Elektro-Kabel"),
        ("ap_bom_rows", "Anschlusspunkte"),
        ("hkv_line_bom_rows", "HKV-Leitungen"),
        ("uv_device_bom_rows", "UV-Geräte"),
        ("uv_busbar_bom_rows", "UV-Phasenschienen"),
        ("custom_bom_rows", "Manuelle Positionen"),
    ]

    def __init__(self, metadata: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stückliste bearbeiten")
        self.resize(1120, 660)
        self._base_metadata = copy.deepcopy(metadata or {})
        self._build_ui()
        self._load_from_metadata(self._base_metadata)

    def _build_ui(self):
        root = QVBoxLayout(self)

        info = QLabel(
            "Pflege von Hersteller/Artikelnummern für bestehende Positionen "
            "(Katalog) und frei manuelle Positionen."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#aaaaaa;")
        root.addWidget(info)

        tabs = QTabWidget()
        root.addWidget(tabs, stretch=1)

        catalog_tab = QWidget()
        catalog_layout = QVBoxLayout(catalog_tab)
        catalog_btns = QHBoxLayout()
        btn_add_catalog = QPushButton("+ Katalogzeile")
        btn_add_catalog.clicked.connect(self._add_catalog_row)
        btn_remove_catalog = QPushButton("− Entfernen")
        btn_remove_catalog.clicked.connect(self._remove_selected_catalog_rows)
        catalog_btns.addWidget(btn_add_catalog)
        catalog_btns.addWidget(btn_remove_catalog)
        catalog_btns.addStretch(1)
        catalog_layout.addLayout(catalog_btns)

        self.tbl_catalog = QTableWidget(0, 6)
        self.tbl_catalog.setHorizontalHeaderLabels([
            "Item-Type", "Key", "Beschreibung Override", "Hersteller", "Artikelnummer", "Notiz",
        ])
        self.tbl_catalog.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_catalog.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_catalog.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_catalog.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl_catalog.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_catalog.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.tbl_catalog.verticalHeader().setVisible(False)
        catalog_layout.addWidget(self.tbl_catalog)
        tabs.addTab(catalog_tab, "Katalog")

        custom_tab = QWidget()
        custom_layout = QVBoxLayout(custom_tab)
        custom_btns = QHBoxLayout()
        btn_add_custom = QPushButton("+ Manuelle Position")
        btn_add_custom.clicked.connect(self._add_custom_row)
        btn_remove_custom = QPushButton("− Entfernen")
        btn_remove_custom.clicked.connect(self._remove_selected_custom_rows)
        custom_btns.addWidget(btn_add_custom)
        custom_btns.addWidget(btn_remove_custom)
        custom_btns.addStretch(1)
        custom_layout.addLayout(custom_btns)

        self.tbl_custom = QTableWidget(0, 9)
        self.tbl_custom.setHorizontalHeaderLabels([
            "ID", "Bereich", "Kategorie", "Beschreibung", "Einheit", "Menge",
            "Hersteller", "Artikelnummer", "Notiz",
        ])
        custom_hdr = self.tbl_custom.horizontalHeader()
        custom_hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        custom_hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        custom_hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        custom_hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        custom_hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        custom_hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        custom_hdr.setSectionResizeMode(6, QHeaderView.Stretch)
        custom_hdr.setSectionResizeMode(7, QHeaderView.Stretch)
        custom_hdr.setSectionResizeMode(8, QHeaderView.Stretch)
        self.tbl_custom.verticalHeader().setVisible(False)
        custom_layout.addWidget(self.tbl_custom)
        tabs.addTab(custom_tab, "Manuell")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _next_custom_id(self) -> str:
        existing: set[int] = set()
        for row_idx in range(self.tbl_custom.rowCount()):
            item = self.tbl_custom.item(row_idx, 0)
            value = item.text().strip() if item else ""
            if value.startswith("BOM-") and value.split("-", 1)[1].isdigit():
                existing.add(int(value.split("-", 1)[1]))
        n = 1
        while n in existing:
            n += 1
        return f"BOM-{n}"

    def _add_catalog_row(self, entry: dict | None = None):
        entry = entry or {}
        row_idx = self.tbl_catalog.rowCount()
        self.tbl_catalog.insertRow(row_idx)
        self.tbl_catalog.setItem(row_idx, 0, QTableWidgetItem(str(entry.get("item_type", "") or "").strip()))
        self.tbl_catalog.setItem(row_idx, 1, QTableWidgetItem(str(entry.get("key", "") or "").strip()))
        self.tbl_catalog.setItem(row_idx, 2, QTableWidgetItem(str(entry.get("description_override", "") or "").strip()))
        self.tbl_catalog.setItem(row_idx, 3, QTableWidgetItem(str(entry.get("manufacturer", "") or "").strip()))
        self.tbl_catalog.setItem(row_idx, 4, QTableWidgetItem(str(entry.get("article_number", "") or "").strip()))
        self.tbl_catalog.setItem(row_idx, 5, QTableWidgetItem(str(entry.get("note", "") or "").strip()))

    def _add_custom_row(self, item: dict | None = None):
        item = item or {}
        row_idx = self.tbl_custom.rowCount()
        self.tbl_custom.insertRow(row_idx)
        custom_id = str(item.get("custom_id", "") or "").strip() or self._next_custom_id()
        self.tbl_custom.setItem(row_idx, 0, QTableWidgetItem(custom_id))

        cmb_section = SafeComboBox()
        cmb_section.setEditable(False)
        for key, label in self.SECTION_CHOICES:
            cmb_section.addItem(f"{label} ({key})", key)
        current_section = str(item.get("section_key", "custom_bom_rows") or "custom_bom_rows").strip()
        sec_idx = cmb_section.findData(current_section)
        cmb_section.setCurrentIndex(sec_idx if sec_idx >= 0 else cmb_section.findData("custom_bom_rows"))
        self.tbl_custom.setCellWidget(row_idx, 1, cmb_section)

        self.tbl_custom.setItem(row_idx, 2, QTableWidgetItem(str(item.get("category", "Manuell") or "Manuell").strip()))
        self.tbl_custom.setItem(row_idx, 3, QTableWidgetItem(str(item.get("description", "") or "").strip()))
        self.tbl_custom.setItem(row_idx, 4, QTableWidgetItem(str(item.get("unit", "Stk") or "Stk").strip()))
        self.tbl_custom.setItem(row_idx, 5, QTableWidgetItem(str(item.get("quantity", 0.0) or 0.0)))
        self.tbl_custom.setItem(row_idx, 6, QTableWidgetItem(str(item.get("manufacturer", "") or "").strip()))
        self.tbl_custom.setItem(row_idx, 7, QTableWidgetItem(str(item.get("article_number", "") or "").strip()))
        self.tbl_custom.setItem(row_idx, 8, QTableWidgetItem(str(item.get("note", "") or "").strip()))

    def _remove_selected_catalog_rows(self):
        rows = sorted({idx.row() for idx in self.tbl_catalog.selectedIndexes()}, reverse=True)
        for row_idx in rows:
            self.tbl_catalog.removeRow(row_idx)

    def _remove_selected_custom_rows(self):
        rows = sorted({idx.row() for idx in self.tbl_custom.selectedIndexes()}, reverse=True)
        for row_idx in rows:
            self.tbl_custom.removeRow(row_idx)

    def _load_from_metadata(self, metadata: dict):
        self.tbl_catalog.setRowCount(0)
        catalog = metadata.get("item_catalog", {}) if isinstance(metadata, dict) else {}
        if isinstance(catalog, dict):
            for catalog_key, entry in sorted(catalog.items(), key=lambda kv: str(kv[0]).lower()):
                item_type, item_key = "", ""
                raw_key = str(catalog_key or "")
                if "|" in raw_key:
                    item_type, item_key = raw_key.split("|", 1)
                else:
                    item_key = raw_key
                row_data = {
                    "item_type": item_type,
                    "key": item_key,
                    "description_override": str((entry or {}).get("description_override", "") or ""),
                    "manufacturer": str((entry or {}).get("manufacturer", "") or ""),
                    "article_number": str((entry or {}).get("article_number", "") or ""),
                    "note": str((entry or {}).get("note", "") or ""),
                }
                self._add_catalog_row(row_data)

        self.tbl_custom.setRowCount(0)
        custom_items = metadata.get("custom_items", []) if isinstance(metadata, dict) else []
        if isinstance(custom_items, list):
            for item in custom_items:
                if isinstance(item, dict):
                    self._add_custom_row(item)

    def get_metadata(self) -> dict:
        result = copy.deepcopy(self._base_metadata) if isinstance(self._base_metadata, dict) else {}

        item_catalog: dict[str, dict] = {}
        for row_idx in range(self.tbl_catalog.rowCount()):
            item_type_item = self.tbl_catalog.item(row_idx, 0)
            key_item = self.tbl_catalog.item(row_idx, 1)
            desc_item = self.tbl_catalog.item(row_idx, 2)
            manufacturer_item = self.tbl_catalog.item(row_idx, 3)
            article_item = self.tbl_catalog.item(row_idx, 4)
            note_item = self.tbl_catalog.item(row_idx, 5)

            item_type = item_type_item.text().strip() if item_type_item else ""
            key = key_item.text().strip() if key_item else ""
            if not item_type and not key:
                continue
            catalog_key = f"{item_type}|{key}" if item_type else key
            item_catalog[catalog_key] = {
                "description_override": desc_item.text().strip() if desc_item else "",
                "manufacturer": manufacturer_item.text().strip() if manufacturer_item else "",
                "article_number": article_item.text().strip() if article_item else "",
                "note": note_item.text().strip() if note_item else "",
            }

        custom_items: list[dict] = []
        for row_idx in range(self.tbl_custom.rowCount()):
            custom_id_item = self.tbl_custom.item(row_idx, 0)
            cmb_section = self.tbl_custom.cellWidget(row_idx, 1)
            category_item = self.tbl_custom.item(row_idx, 2)
            description_item = self.tbl_custom.item(row_idx, 3)
            unit_item = self.tbl_custom.item(row_idx, 4)
            quantity_item = self.tbl_custom.item(row_idx, 5)
            manufacturer_item = self.tbl_custom.item(row_idx, 6)
            article_item = self.tbl_custom.item(row_idx, 7)
            note_item = self.tbl_custom.item(row_idx, 8)

            custom_id = custom_id_item.text().strip() if custom_id_item else ""
            if not custom_id:
                custom_id = f"BOM-{row_idx + 1}"
            section_key = str(cmb_section.currentData() or "custom_bom_rows").strip() if isinstance(cmb_section, QComboBox) else "custom_bom_rows"
            category = category_item.text().strip() if category_item else "Manuell"
            description = description_item.text().strip() if description_item else ""
            unit = unit_item.text().strip() if unit_item else "Stk"
            try:
                quantity = float((quantity_item.text() if quantity_item else "0").replace(",", "."))
            except Exception:
                quantity = 0.0

            if not any([
                description,
                quantity,
                (manufacturer_item.text().strip() if manufacturer_item else ""),
                (article_item.text().strip() if article_item else ""),
                (note_item.text().strip() if note_item else ""),
            ]):
                continue

            custom_items.append({
                "custom_id": custom_id,
                "section_key": section_key,
                "category": category or "Manuell",
                "item_type": "custom",
                "key": custom_id,
                "description": description,
                "unit": unit or "Stk",
                "quantity": quantity,
                "manufacturer": manufacturer_item.text().strip() if manufacturer_item else "",
                "article_number": article_item.text().strip() if article_item else "",
                "note": note_item.text().strip() if note_item else "",
            })

        result["item_catalog"] = item_catalog
        result["custom_items"] = custom_items
        return result

    # ── Busbar (Phasenschienen) helpers ──────────────────────── #

    def _capture_current_busbars(self) -> list[dict]:
        """Read all rows from tbl_busbars into a list of busbar dicts."""
        busbars: list[dict] = []
        for row_idx in range(self.tbl_busbars.rowCount()):
            cmb_phase = self.tbl_busbars.cellWidget(row_idx, 0)
            btn_color = self.tbl_busbars.cellWidget(row_idx, 1)
            sb_start = self.tbl_busbars.cellWidget(row_idx, 2)
            sb_end = self.tbl_busbars.cellWidget(row_idx, 3)
            if not all([cmb_phase, btn_color, sb_start, sb_end]):
                continue
            phase = cmb_phase.currentText().strip() if isinstance(cmb_phase, QComboBox) else ""
            color = btn_color.property("busbar_color") or "#888888"
            te_start = sb_start.value() if isinstance(sb_start, QSpinBox) else 1
            te_end = sb_end.value() if isinstance(sb_end, QSpinBox) else 1
            if phase:
                busbars.append({
                    "phase": phase,
                    "color": color,
                    "te_start": te_start,
                    "te_end": te_end,
                })
        return busbars

    def _add_busbar_row(self, busbar: dict | None = None):
        """Append a row to tbl_busbars, optionally pre-filled from busbar dict."""
        total_te = self.sb_rows.value() * self.sb_modules.value()
        row_idx = self.tbl_busbars.rowCount()
        self.tbl_busbars.insertRow(row_idx)

        # Phase combo
        cmb = SafeComboBox()
        cmb.setEditable(True)
        for p in (THREE_PHASE_LABEL, "L1", "L2", "L3", "N", "PE", "L"):
            cmb.addItem(p)
        phase = str((busbar or {}).get("phase", "L1") or "L1")
        cmb.setCurrentText(phase)
        cmb.currentTextChanged.connect(lambda p, ri=row_idx: self._on_busbar_phase_changed(ri, p))
        cmb.currentTextChanged.connect(lambda _: self._refresh_visual())
        self.tbl_busbars.setCellWidget(row_idx, 0, cmb)

        # Color button
        is_3p = (phase == THREE_PHASE_LABEL)
        default_color = PHASE_COLORS.get(phase, "#888888")
        color = str((busbar or {}).get("color", default_color) or default_color)
        btn = QPushButton()
        btn.setFixedHeight(22)
        btn.setProperty("busbar_color", color)
        if is_3p:
            self._apply_three_phase_btn_style(btn)
        else:
            self._update_busbar_color_btn(btn, color)
            btn.clicked.connect(lambda checked=False, ri=row_idx: self._pick_busbar_color(ri))
        self.tbl_busbars.setCellWidget(row_idx, 1, btn)

        # TE-Start spinbox
        sb_start = QSpinBox()
        sb_start.setRange(1, max(1, total_te))
        sb_start.setValue(int((busbar or {}).get("te_start", 1) or 1))
        sb_start.valueChanged.connect(lambda _: self._refresh_visual())
        self.tbl_busbars.setCellWidget(row_idx, 2, sb_start)

        # TE-Ende spinbox
        sb_end = QSpinBox()
        sb_end.setRange(1, max(1, total_te))
        sb_end.setValue(int((busbar or {}).get("te_end", sb_start.value()) or sb_start.value()))
        sb_end.valueChanged.connect(lambda _: self._refresh_visual())
        self.tbl_busbars.setCellWidget(row_idx, 3, sb_end)

        self._refresh_visual()

    def _remove_busbar_row(self):
        """Remove the currently selected busbar row."""
        selected = self.tbl_busbars.currentRow()
        if selected >= 0:
            self.tbl_busbars.removeRow(selected)
        elif self.tbl_busbars.rowCount() > 0:
            self.tbl_busbars.removeRow(self.tbl_busbars.rowCount() - 1)
        self._refresh_visual()

    def _on_busbar_phase_changed(self, row_idx: int, phase: str):
        """When a phase is selected, update the color button accordingly."""
        btn = self.tbl_busbars.cellWidget(row_idx, 1)
        if not isinstance(btn, QPushButton):
            return
        if phase == THREE_PHASE_LABEL:
            self._apply_three_phase_btn_style(btn)
        else:
            btn.setEnabled(True)
            # Re-connect click if it was previously a 3-phase button (no click handler)
            try:
                btn.clicked.disconnect()
            except RuntimeError:
                pass
            btn.clicked.connect(lambda checked=False, ri=row_idx: self._pick_busbar_color(ri))
            if phase in PHASE_COLORS:
                self._update_busbar_color_btn(btn, PHASE_COLORS[phase])

    @staticmethod
    def _apply_three_phase_btn_style(btn: QPushButton):
        """Style a color button as the 3-phase gradient and disable it."""
        btn.setText("L1 / L2 / L3")
        btn.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #e53935, stop:0.32 #e53935, "
            "stop:0.34 #43a047, stop:0.65 #43a047, "
            "stop:0.67 #1e88e5, stop:1 #1e88e5);"
            "color: #ffffff; border: 1px solid #555; border-radius: 2px;"
        )
        btn.setEnabled(False)

    def _pick_busbar_color(self, row_idx: int):
        """Open color picker for the busbar color button at row_idx."""
        btn = self.tbl_busbars.cellWidget(row_idx, 1)
        if not isinstance(btn, QPushButton):
            return
        current = btn.property("busbar_color") or "#888888"
        color = QColorDialog.getColor(QColor(current), self, "Farbe wählen")
        if color.isValid():
            self._update_busbar_color_btn(btn, color.name())
            self._refresh_visual()

    @staticmethod
    def _update_busbar_color_btn(btn: QPushButton, color: str):
        btn.setProperty("busbar_color", color)
        btn.setStyleSheet(
            f"background-color: {color}; color: #ffffff; border: 1px solid #555; border-radius: 2px;"
        )
        btn.setText(color)


class UpDistributionDialog(QDialog):
    DEFAULT_CONDUCTORS: list[str] = [
        "L1", "L2", "L3", "N", "PE", "L", "S1", "S2",
    ]

    def __init__(
        self,
        config: dict | None = None,
        cable_choices: list[tuple[str, str]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Verteilung in Unterputzdose")
        self.resize(900, 560)
        self._cable_choices = list(cable_choices or [])
        self._build_ui()
        self._load_config(config or {})

    def _build_ui(self):
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.cmb_incoming = SafeComboBox()
        self.cmb_incoming.setEditable(False)
        self.cmb_incoming.addItem("", "")
        for cable_id, cable_name in self._cable_choices:
            text = cable_name if cable_name else cable_id
            self.cmb_incoming.addItem(f"{text} ({cable_id})", cable_id)
        form.addRow("Zuleitung:", self.cmb_incoming)

        self.le_note = QLineEdit()
        self.le_note.setPlaceholderText("Optionale Notiz zur Verteilung")
        form.addRow("Notiz:", self.le_note)
        root.addLayout(form)

        self.tbl_map = QTableWidget(0, 4)
        self.tbl_map.setHorizontalHeaderLabels(
            ["Ader (Zuleitung)", "Abgehendes Kabel", "Ader (Abgang)", "Notiz"]
        )
        self.tbl_map.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_map.verticalHeader().setVisible(False)
        root.addWidget(self.tbl_map, stretch=1)

        row_buttons = QHBoxLayout()
        self.btn_add_row = QPushButton("Zeile hinzufügen")
        self.btn_add_row.clicked.connect(self._add_mapping_row)
        row_buttons.addWidget(self.btn_add_row)
        self.btn_remove_row = QPushButton("Gewählte Zeile entfernen")
        self.btn_remove_row.clicked.connect(self._remove_selected_rows)
        row_buttons.addWidget(self.btn_remove_row)
        row_buttons.addStretch(1)
        root.addLayout(row_buttons)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _add_mapping_row(self, mapping: dict | None = None):
        mapping = mapping or {}
        row_idx = self.tbl_map.rowCount()
        self.tbl_map.insertRow(row_idx)

        cmb_from = SafeComboBox()
        cmb_from.setEditable(True)
        cmb_from.addItems(self.DEFAULT_CONDUCTORS)
        cmb_from.setCurrentText(str(mapping.get("from_conductor", "") or "").strip())
        self.tbl_map.setCellWidget(row_idx, 0, cmb_from)

        cmb_cable = SafeComboBox()
        cmb_cable.setEditable(False)
        cmb_cable.addItem("", "")
        for cable_id, cable_name in self._cable_choices:
            text = cable_name if cable_name else cable_id
            cmb_cable.addItem(f"{text} ({cable_id})", cable_id)
        to_cable_id = str(mapping.get("to_cable_id", "") or "").strip()
        if to_cable_id:
            idx = cmb_cable.findData(to_cable_id)
            if idx >= 0:
                cmb_cable.setCurrentIndex(idx)
        self.tbl_map.setCellWidget(row_idx, 1, cmb_cable)

        cmb_to = SafeComboBox()
        cmb_to.setEditable(True)
        cmb_to.addItems(self.DEFAULT_CONDUCTORS)
        cmb_to.setCurrentText(str(mapping.get("to_conductor", "") or "").strip())
        self.tbl_map.setCellWidget(row_idx, 2, cmb_to)

        note_item = QTableWidgetItem(str(mapping.get("note", "") or "").strip())
        self.tbl_map.setItem(row_idx, 3, note_item)

    def _remove_selected_rows(self):
        rows = sorted({i.row() for i in self.tbl_map.selectedIndexes()}, reverse=True)
        for row_idx in rows:
            self.tbl_map.removeRow(row_idx)

    def _capture_mappings(self) -> list[dict]:
        rows: list[dict] = []
        for row_idx in range(self.tbl_map.rowCount()):
            cmb_from = self.tbl_map.cellWidget(row_idx, 0)
            cmb_cable = self.tbl_map.cellWidget(row_idx, 1)
            cmb_to = self.tbl_map.cellWidget(row_idx, 2)
            note_item = self.tbl_map.item(row_idx, 3)
            entry = {
                "from_conductor": cmb_from.currentText().strip() if isinstance(cmb_from, QComboBox) else "",
                "to_cable_id": str(cmb_cable.currentData() or "").strip() if isinstance(cmb_cable, QComboBox) else "",
                "to_conductor": cmb_to.currentText().strip() if isinstance(cmb_to, QComboBox) else "",
                "note": note_item.text().strip() if note_item else "",
            }
            if entry["from_conductor"] or entry["to_cable_id"] or entry["to_conductor"] or entry["note"]:
                rows.append(entry)
        return rows

    def _load_config(self, config: dict):
        incoming = str(config.get("incoming_cable_id", "") or "").strip()
        if incoming:
            idx = self.cmb_incoming.findData(incoming)
            if idx >= 0:
                self.cmb_incoming.setCurrentIndex(idx)
        self.le_note.setText(str(config.get("note", "") or "").strip())

        mappings = config.get("mappings", [])
        if isinstance(mappings, list):
            for mapping in mappings:
                if isinstance(mapping, dict):
                    self._add_mapping_row(mapping)
        if self.tbl_map.rowCount() == 0:
            self._add_mapping_row()

    def get_config(self) -> dict:
        incoming_cable_id = str(self.cmb_incoming.currentData() or "").strip()
        mappings = self._capture_mappings()
        outgoing: list[str] = []
        for mapping in mappings:
            cable_id = str(mapping.get("to_cable_id", "") or "").strip()
            if cable_id and cable_id not in outgoing:
                outgoing.append(cable_id)
        return {
            "incoming_cable_id": incoming_cable_id,
            "outgoing_cable_ids": outgoing,
            "mappings": mappings,
            "note": self.le_note.text().strip(),
        }


# ================================================================== #
#  Elektro: Anschlusspunkt Panel                                       #
# ================================================================== #

class ElektroPointPanel(QWidget):
    delete_requested   = Signal(str)
    name_changed       = Signal(str, str)
    color_changed      = Signal(str, str)
    size_changed       = Signal(str)
    icon_changed       = Signal(str, str)
    visibility_changed = Signal(str, bool)
    place_requested    = Signal(str)
    label_size_changed = Signal(str, float)
    label_visibility_changed = Signal(str, bool)
    duplicate_requested = Signal(str)
    position_changed   = Signal(str, str)      # (point_id, position)
    height_changed     = Signal(str, float)    # (point_id, height_mm)
    note_changed       = Signal(str, str)
    smarthome_device_changed = Signal(str, str)
    smarthome_device_color_changed = Signal(str, str)
    ap_type_changed    = Signal(str, str)
    uv_config_changed  = Signal(str)
    up_distribution_changed = Signal(str)

    def __init__(self, point_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.point_id = point_id
        self._name = name or point_id
        self._icon_path: str | None = None
        self._color = QColor(color or "#4fc3f7")
        self._ap_type = "standard"
        self._uv_config: dict = {}
        self._uv_cable_choices: list[str] = []
        self._up_distribution_config: dict = {}
        self._up_cable_choices: list[tuple[str, str]] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda c: self.visibility_changed.emit(self.point_id, c)
        )
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(True)
        self.chk_label_visible.toggled.connect(
            lambda c: self.label_visibility_changed.emit(self.point_id, c)
        )
        form.addRow(self.chk_label_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.point_id, v)
        )
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.sb_width = SafeDoubleSpinBox()
        self.sb_width.setRange(0.01, 999999.0)
        self.sb_width.setSingleStep(0.5)
        self.sb_width.setValue(3.0)
        self.sb_width.setSuffix(" cm")
        self.sb_width.valueChanged.connect(lambda _: self.size_changed.emit(self.point_id))
        form.addRow("Breite:", self.sb_width)

        self.sb_height = SafeDoubleSpinBox()
        self.sb_height.setRange(0.01, 999999.0)
        self.sb_height.setSingleStep(0.5)
        self.sb_height.setValue(3.0)
        self.sb_height.setSuffix(" cm")
        self.sb_height.valueChanged.connect(lambda _: self.size_changed.emit(self.point_id))
        form.addRow("H\u00f6he:", self.sb_height)

        self.cmb_symbol = SafeComboBox()
        for label in BUILTIN_SYMBOLS:
            self.cmb_symbol.addItem(label)
        self.cmb_symbol.currentTextChanged.connect(self._on_symbol_selected)
        form.addRow("Symbol:", self.cmb_symbol)

        self.btn_icon = QPushButton("Eigenes Bild…")
        self.btn_icon.clicked.connect(self._load_icon)
        form.addRow("", self.btn_icon)

        self.cmb_ap_type = SafeComboBox()
        self.cmb_ap_type.addItem("Standard", "standard")
        self.cmb_ap_type.addItem("Unterverteilung (UV)", "uv")
        self.cmb_ap_type.addItem("Verteilung in Unterputzdose", "up_distribution")
        self.cmb_ap_type.currentIndexChanged.connect(self._on_ap_type_changed)
        form.addRow("AP-Typ:", self.cmb_ap_type)

        self.btn_uv_plan = QPushButton("🗂️ UV planen…")
        self.btn_uv_plan.clicked.connect(self._open_uv_dialog)
        form.addRow(self.btn_uv_plan)

        self.btn_up_distribution = QPushButton("Verteilung in Unterputzdose…")
        self.btn_up_distribution.clicked.connect(self._open_up_distribution_dialog)
        form.addRow(self.btn_up_distribution)

        self.sb_label_size = SafeDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(12.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.point_id, v)
        )
        form.addRow("Schriftgr\u00f6\u00dfe:", self.sb_label_size)

        # Position (Wand, Decke, Boden, Freitext)
        pos_layout = QHBoxLayout()
        self.cmb_position = SafeComboBox()
        self.cmb_position.addItems(["Wand", "Decke", "Boden", "Freitext"])
        self.cmb_position.currentTextChanged.connect(self._on_position_changed)
        pos_layout.addWidget(self.cmb_position)
        
        self.le_position_custom = QLineEdit()
        self.le_position_custom.setPlaceholderText("Z.B. Trockenbau, Fenster...")
        self.le_position_custom.setEnabled(False)
        self.le_position_custom.textChanged.connect(
            lambda v: self.position_changed.emit(self.point_id, v if self.le_position_custom.isEnabled() else self.cmb_position.currentText())
        )
        pos_layout.addWidget(self.le_position_custom)
        form.addRow("Position:", pos_layout)

        # H\u00f6he vom Boden in cm
        self.sb_height_from_floor = SafeDoubleSpinBox()
        self.sb_height_from_floor.setRange(0.0, 999.9)
        self.sb_height_from_floor.setSingleStep(1.0)
        self.sb_height_from_floor.setValue(0.0)
        self.sb_height_from_floor.setDecimals(1)
        self.sb_height_from_floor.setSuffix(" cm")
        self.sb_height_from_floor.valueChanged.connect(
            lambda v: self.height_changed.emit(self.point_id, v)
        )
        form.addRow("H\u00f6he v. Boden:", self.sb_height_from_floor)

        self.cmb_smarthome = SafeComboBox()
        self.cmb_smarthome.setEditable(True)
        self.cmb_smarthome.addItems(["", "Shelly", "Sonoff ZBMINIR2"])
        self.cmb_smarthome.editTextChanged.connect(
            lambda value: self.smarthome_device_changed.emit(self.point_id, value)
        )
        form.addRow("Unterputz-Ger\u00e4t:", self.cmb_smarthome)

        self.cmb_smarthome_color = SafeComboBox()
        self.cmb_smarthome_color.setEditable(True)
        self.cmb_smarthome_color.addItems(["", "wei\u00df", "schwarz"])
        self.cmb_smarthome_color.editTextChanged.connect(
            lambda value: self.smarthome_device_color_changed.emit(self.point_id, value)
        )
        form.addRow("Ger\u00e4tefarbe:", self.cmb_smarthome_color)

        self.te_note = QTextEdit()
        self.te_note.setMaximumHeight(50)
        self.te_note.setPlaceholderText("Notiz...")
        self.te_note.textChanged.connect(
            lambda: self.note_changed.emit(self.point_id, self.te_note.toPlainText())
        )
        form.addRow("Notiz:", self.te_note)

        self.btn_place = QPushButton("\U0001f4cd Platzieren")
        self.btn_place.clicked.connect(
            lambda: self.place_requested.emit(self.point_id)
        )
        form.addRow(self.btn_place)

        self.btn_duplicate = QPushButton("\U0001f4cb Duplizieren")
        self.btn_duplicate.clicked.connect(
            lambda: self.duplicate_requested.emit(self.point_id)
        )
        form.addRow(self.btn_duplicate)

        self.btn_delete = QPushButton("\U0001f5d1\ufe0f L\u00f6schen")
        self.btn_delete.clicked.connect(
            lambda: self.delete_requested.emit(self.point_id)
        )
        form.addRow(self.btn_delete)

        self.lbl_room = QLabel("Raum: –")
        self.lbl_room.setStyleSheet("color:#9ad1ff; padding:2px;")
        form.addRow(self.lbl_room)

        layout.addLayout(form)
        self._update_uv_controls()

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "Anschlusspunkt-Farbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.point_id, self._color.name())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(f"background:{self._color.name()}; color:white;")

    def _on_position_changed(self, pos: str):
        """Handle position dropdown changes - enable custom text field if 'Freitext' is selected."""
        is_custom = (pos == "Freitext")
        self.le_position_custom.setEnabled(is_custom)
        if not is_custom:
            self.le_position_custom.clear()
            self.position_changed.emit(self.point_id, pos)
        else:
            # When Freitext is selected, emit the custom text if any
            if self.le_position_custom.text():
                self.position_changed.emit(self.point_id, self.le_position_custom.text())

    def _on_symbol_selected(self, label: str):
        path = BUILTIN_SYMBOLS.get(label, "")
        if path:
            self._icon_path = path
            self.btn_icon.setText("Eigenes Bild…")
            self.icon_changed.emit(self.point_id, path)
        elif label == "(kein Symbol)":
            self._icon_path = None
            self.btn_icon.setText("Eigenes Bild…")
            self.icon_changed.emit(self.point_id, "")

    def _load_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Symbol laden", "", "Bilder (*.png *.jpg *.svg *.bmp)"
        )
        if path:
            self._icon_path = path
            # Dropdown auf "(kein Symbol)" stellen, da eigenes Bild gewählt
            self.cmb_symbol.blockSignals(True)
            self.cmb_symbol.setCurrentIndex(0)
            self.cmb_symbol.blockSignals(False)
            self.btn_icon.setText(path.split("/")[-1].split("\\")[-1])
            self.icon_changed.emit(self.point_id, path)

    def _on_ap_type_changed(self):
        data = self.cmb_ap_type.currentData()
        self._ap_type = str(data or "standard")
        if self._ap_type == "uv":
            self._up_distribution_config = {}
        elif self._ap_type == "up_distribution":
            self._uv_config = {}
        else:
            self._uv_config = {}
            self._up_distribution_config = {}
        self._update_uv_controls()
        self.ap_type_changed.emit(self.point_id, self._ap_type)

    def _update_uv_controls(self):
        ap_type = self.get_ap_type()
        self.btn_uv_plan.setVisible(ap_type == "uv")
        self.btn_up_distribution.setVisible(ap_type == "up_distribution")

    def get_ap_type(self) -> str:
        return self._ap_type or "standard"

    def set_ap_type(self, ap_type: str):
        raw = str(ap_type).strip().lower()
        if raw == "uv":
            value = "uv"
        elif raw in {"up_distribution", "up", "unterputzdose", "verteilung_in_unterputzdose"}:
            value = "up_distribution"
        else:
            value = "standard"
        self._ap_type = value
        if value == "uv":
            self._up_distribution_config = {}
        elif value == "up_distribution":
            self._uv_config = {}
        else:
            self._uv_config = {}
            self._up_distribution_config = {}
        idx = self.cmb_ap_type.findData(value)
        if idx < 0:
            idx = 0
        self.cmb_ap_type.blockSignals(True)
        self.cmb_ap_type.setCurrentIndex(idx)
        self.cmb_ap_type.blockSignals(False)
        self._update_uv_controls()

    def set_uv_config(self, config: dict | None):
        self._uv_config = copy.deepcopy(config or {})

    def set_uv_cable_choices(self, choices: list[str]):
        merged: list[str] = []
        for choice in choices:
            text = str(choice or "").strip()
            if text and text not in merged:
                merged.append(text)
        self._uv_cable_choices = merged

    def set_up_distribution_config(self, config: dict | None):
        self._up_distribution_config = copy.deepcopy(config or {})

    def set_up_distribution_cable_choices(self, choices: list[dict]):
        merged: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            cable_id = str(choice.get("cable_id", "") or "").strip()
            cable_name = str(choice.get("name", "") or "").strip()
            if not cable_id or cable_id in seen_ids:
                continue
            seen_ids.add(cable_id)
            merged.append((cable_id, cable_name))
        self._up_cable_choices = merged

    def _open_uv_dialog(self):
        dlg = UvConfigDialog(
            config=self._uv_config,
            cable_choices=self._uv_cable_choices,
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted:
            self._uv_config = dlg.get_config()
            self.uv_config_changed.emit(self.point_id)

    def _open_up_distribution_dialog(self):
        dlg = UpDistributionDialog(
            config=self._up_distribution_config,
            cable_choices=self._up_cable_choices,
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted:
            self._up_distribution_config = dlg.get_config()
            self.up_distribution_changed.emit(self.point_id)

    def get_parameters(self) -> dict:
        # Wenn Freitext ausgewählt ist, speichere den benutzerdefinierten Text
        position = self.cmb_position.currentText()
        if position == "Freitext":
            position = self.le_position_custom.text().strip() or "Freitext"
        
        return {
            "name":      self.le_name.text().strip() or self.point_id,
            "color":     self._color.name(),
            "width":     self.sb_width.value() * 10,
            "height":    self.sb_height.value() * 10,
            "icon_path": self._icon_path or "",
            "builtin_symbol": self.cmb_symbol.currentText(),
            "visible":   self.chk_visible.isChecked(),
            "label_visible": self.chk_label_visible.isChecked(),
            "label_size": self.sb_label_size.value(),
            "ap_type": self.get_ap_type(),
            "position":  position,
            "height_from_floor": self.sb_height_from_floor.value(),
            "smarthome_device": self.get_smarthome_device_text().strip(),
            "smarthome_device_color": self.get_smarthome_device_color_text().strip(),
            "note": self.te_note.toPlainText(),
            "uv_config": copy.deepcopy(self._uv_config),
            "up_distribution_config": copy.deepcopy(self._up_distribution_config),
        }

    def get_smarthome_device_text(self) -> str:
        return self.cmb_smarthome.currentText()

    def set_smarthome_device_text(self, device: str):
        value = (device or "").strip()
        self.cmb_smarthome.setCurrentText(value)

    def set_smarthome_device_choices(self, choices: list[str]):
        current = self.get_smarthome_device_text().strip()
        merged: list[str] = []
        for choice in choices:
            text = (choice or "").strip()
            if text and text not in merged:
                merged.append(text)

        self.cmb_smarthome.blockSignals(True)
        self.cmb_smarthome.clear()
        self.cmb_smarthome.addItem("")
        self.cmb_smarthome.addItems(merged)
        self.cmb_smarthome.setCurrentText(current)
        self.cmb_smarthome.blockSignals(False)

    def get_smarthome_device_color_text(self) -> str:
        return self.cmb_smarthome_color.currentText()

    def set_smarthome_device_color_text(self, color: str):
        value = (color or "").strip()
        self.cmb_smarthome_color.setCurrentText(value)

    def set_smarthome_device_color_choices(self, choices: list[str]):
        current = self.get_smarthome_device_color_text().strip()
        merged: list[str] = []
        for choice in choices:
            text = (choice or "").strip()
            if text and text not in merged:
                merged.append(text)

        self.cmb_smarthome_color.blockSignals(True)
        self.cmb_smarthome_color.clear()
        self.cmb_smarthome_color.addItem("")
        self.cmb_smarthome_color.addItems(merged)
        self.cmb_smarthome_color.setCurrentText(current)
        self.cmb_smarthome_color.blockSignals(False)

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["point_id"] = self.point_id
        return d

    def set_room_name(self, room_name: str):
        self.lbl_room.setText(f"Raum: {room_name or '–'}")

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.point_id))
        c = d.get("color", self._color.name())
        self._color = QColor(c)
        self._update_color_button()
        self.sb_width.setValue(d.get("width", 30.0) / 10)
        self.sb_height.setValue(d.get("height", 30.0) / 10)
        self.chk_visible.setChecked(d.get("visible", True))
        self.chk_label_visible.setChecked(d.get("label_visible", True))
        self.sb_label_size.setValue(d.get("label_size", 12.0))
        self.set_ap_type(d.get("ap_type", "standard"))
        self.set_uv_config(d.get("uv_config"))
        self.set_up_distribution_config(d.get("up_distribution_config"))
        
        # Position und Höhe vom Boden
        position = d.get("position", "Wand")
        # Prüfe ob Position eine Standardoption ist
        idx = self.cmb_position.findText(position)
        if idx >= 0:
            self.cmb_position.setCurrentIndex(idx)
        else:
            # Benutzerdefinierte Position - setze auf "Freitext" und speichere den Text
            self.cmb_position.setCurrentIndex(self.cmb_position.findText("Freitext"))
            self.le_position_custom.setText(position)
        
        self.sb_height_from_floor.setValue(d.get("height_from_floor", 0.0))
        self.set_smarthome_device_text(d.get("smarthome_device", ""))
        self.set_smarthome_device_color_text(d.get("smarthome_device_color", ""))
        self.te_note.setPlainText(d.get("note", ""))
        
        # Eingebautes Symbol wiederherstellen
        builtin = d.get("builtin_symbol", "")
        if builtin and builtin in BUILTIN_SYMBOLS:
            idx = self.cmb_symbol.findText(builtin)
            if idx >= 0:
                self.cmb_symbol.setCurrentIndex(idx)
        else:
            icon = d.get("icon_path", "")
            if icon:
                self._icon_path = icon
                self.btn_icon.setText(icon.split("/")[-1].split("\\")[-1])


# ================================================================== #
#  Elektro: Raum Panel                                                #
# ================================================================== #

class ElektroRoomPanel(QWidget):
    delete_requested = Signal(str)
    name_changed = Signal(str, str)
    color_changed = Signal(str, str)
    visibility_changed = Signal(str, bool)
    label_size_changed = Signal(str, float)
    label_visibility_changed = Signal(str, bool)
    draw_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(self, room_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.room_id = room_id
        self._name = name or room_id
        self._color = QColor(color or "#43aa8b")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda c: self.visibility_changed.emit(self.room_id, c)
        )
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(True)
        self.chk_label_visible.toggled.connect(
            lambda c: self.label_visibility_changed.emit(self.room_id, c)
        )
        form.addRow(self.chk_label_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.room_id, v)
        )
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.sb_label_size = SafeDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(12.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.room_id, v)
        )
        form.addRow("Schriftgröße:", self.sb_label_size)

        self.btn_draw = QPushButton("✏️ Raum-Polygon zeichnen")
        self.btn_draw.clicked.connect(lambda: self.draw_requested.emit(self.room_id))
        form.addRow(self.btn_draw)

        self.btn_edit = QPushButton("✏️ Raum-Polygon bearbeiten")
        self.btn_edit.clicked.connect(lambda: self.edit_requested.emit(self.room_id))
        form.addRow(self.btn_edit)

        self.btn_delete = QPushButton("🗑️ Löschen")
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(self.room_id))
        form.addRow(self.btn_delete)

        layout.addLayout(form)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "Raum-Farbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.room_id, self._color.name())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(f"background:{self._color.name()}; color:white;")

    def get_parameters(self) -> dict:
        return {
            "name": self.le_name.text().strip() or self.room_id,
            "color": self._color.name(),
            "visible": self.chk_visible.isChecked(),
            "label_visible": self.chk_label_visible.isChecked(),
            "label_size": self.sb_label_size.value(),
        }

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["room_id"] = self.room_id
        return d

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.room_id))
        c = d.get("color", self._color.name())
        self._color = QColor(c)
        self._update_color_button()
        self.chk_visible.setChecked(d.get("visible", True))
        self.chk_label_visible.setChecked(d.get("label_visible", True))
        self.sb_label_size.setValue(d.get("label_size", 12.0))


# ================================================================== #
#  Elektro: Kabelverbindung Panel                                      #
# ================================================================== #

class ElektroCablePanel(QWidget):
    DEFAULT_CABLE_TYPE = "5x1,5"

    delete_requested     = Signal(str)
    name_changed         = Signal(str, str)
    color_changed        = Signal(str, str)
    type_changed         = Signal(str, str)
    comment_changed      = Signal(str, str)
    draw_cable_requested = Signal(str)
    edit_cable_requested = Signal(str)
    visibility_changed   = Signal(str, bool)
    label_size_changed   = Signal(str, float)
    label_visibility_changed = Signal(str, bool)
    type_label_visibility_changed = Signal(str, bool)
    duplicate_requested  = Signal(str)
    stroke_width_changed = Signal(str, float)

    def __init__(self, cable_id: str, name: str | None = None,
                 color: str | None = None,
                 defaults: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.cable_id = cable_id
        self._defaults = defaults or {}
        self._name = name or self._defaults.get("name") or cable_id
        self._color = QColor(color or self._defaults.get("color", "#ff9800"))
        self._start_ap: str = ""
        self._end_ap: str = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(bool(self._defaults.get("visible", True)))
        self.chk_visible.toggled.connect(
            lambda c: self.visibility_changed.emit(self.cable_id, c)
        )
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(bool(self._defaults.get("label_visible", True)))
        self.chk_label_visible.toggled.connect(
            lambda c: self.label_visibility_changed.emit(self.cable_id, c)
        )
        form.addRow(self.chk_label_visible)

        self.chk_type_label_visible = QCheckBox("Kabeltyp im Plan")
        self.chk_type_label_visible.setChecked(
            bool(self._defaults.get("type_label_visible", False))
        )
        self.chk_type_label_visible.toggled.connect(
            lambda c: self.type_label_visibility_changed.emit(self.cable_id, c)
        )
        form.addRow(self.chk_type_label_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.cable_id, v)
        )
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.cmb_type = SafeComboBox()
        self.cmb_type.setEditable(True)
        default_type = (self._defaults.get("type") or self.DEFAULT_CABLE_TYPE).strip()
        self.cmb_type.addItem(default_type)
        self.cmb_type.setCurrentText(default_type)
        self.cmb_type.editTextChanged.connect(
            lambda value: self.type_changed.emit(self.cable_id, value)
        )
        form.addRow("Typ:", self.cmb_type)

        self.te_comment = QTextEdit()
        self.te_comment.setMaximumHeight(50)
        self.te_comment.setPlaceholderText("Kommentar...")
        self.te_comment.setPlainText(self._defaults.get("comment", ""))
        self.te_comment.textChanged.connect(self._on_comment_changed)
        form.addRow("Kommentar:", self.te_comment)

        self.sb_label_size = SafeDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(float(self._defaults.get("label_size", 12.0)))
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.cable_id, v)
        )
        form.addRow("Schriftgr\u00f6\u00dfe:", self.sb_label_size)

        self.sb_stroke_width = SafeDoubleSpinBox()
        self.sb_stroke_width.setRange(0.5, 10.0)
        self.sb_stroke_width.setSingleStep(0.5)
        self.sb_stroke_width.setDecimals(1)
        self.sb_stroke_width.setValue(float(self._defaults.get("stroke_width", 2.0)))
        self.sb_stroke_width.setSuffix(" px")
        self.sb_stroke_width.valueChanged.connect(
            lambda v: self.stroke_width_changed.emit(self.cable_id, v)
        )
        form.addRow("Strichst\u00e4rke:", self.sb_stroke_width)

        self.btn_draw = QPushButton("\u270f\ufe0f Kabel zeichnen")
        self.btn_draw.clicked.connect(
            lambda: self.draw_cable_requested.emit(self.cable_id)
        )
        form.addRow(self.btn_draw)

        self.btn_edit = QPushButton("\u270f\ufe0f Kabel bearbeiten")
        self.btn_edit.clicked.connect(
            lambda: self.edit_cable_requested.emit(self.cable_id)
        )
        form.addRow(self.btn_edit)

        self.btn_duplicate = QPushButton("\U0001f4cb Duplizieren")
        self.btn_duplicate.clicked.connect(
            lambda: self.duplicate_requested.emit(self.cable_id)
        )
        form.addRow(self.btn_duplicate)

        self.btn_delete = QPushButton("\U0001f5d1\ufe0f L\u00f6schen")
        self.btn_delete.clicked.connect(
            lambda: self.delete_requested.emit(self.cable_id)
        )
        form.addRow(self.btn_delete)

        self.lbl_length = QLabel("L\u00e4nge: \u2013")
        self.lbl_length.setStyleSheet("font-weight:bold; color:#ff9800; padding:2px;")
        form.addRow(self.lbl_length)

        self.lbl_start_ap = QLabel("Start-AP: \u2013")
        self.lbl_start_ap.setStyleSheet("color:#4fc3f7; padding:2px;")
        form.addRow(self.lbl_start_ap)

        self.lbl_end_ap = QLabel("End-AP: \u2013")
        self.lbl_end_ap.setStyleSheet("color:#4fc3f7; padding:2px;")
        form.addRow(self.lbl_end_ap)

        layout.addLayout(form)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "Kabel-Farbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.cable_id, self._color.name())

    def _on_comment_changed(self):
        self.comment_changed.emit(self.cable_id, self.te_comment.toPlainText())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(f"background:{self._color.name()}; color:white;")

    def set_length(self, length_mm: float):
        self.lbl_length.setText(f"L\u00e4nge: {length_mm / 1000:.2f} m")

    def set_start_ap(self, ap_name: str):
        self._start_ap = ap_name
        self.lbl_start_ap.setText(f"Start-AP: {ap_name or '\u2013'}")

    def set_end_ap(self, ap_name: str):
        self._end_ap = ap_name
        self.lbl_end_ap.setText(f"End-AP: {ap_name or '\u2013'}")

    def get_parameters(self) -> dict:
        return {
            "name":    self.le_name.text().strip() or self.cable_id,
            "color":   self._color.name(),
            "type":    self.get_type_text().strip(),
            "comment": self.te_comment.toPlainText(),
            "visible": self.chk_visible.isChecked(),
            "label_visible": self.chk_label_visible.isChecked(),
            "type_label_visible": self.chk_type_label_visible.isChecked(),
            "label_size": self.sb_label_size.value(),
            "stroke_width": self.sb_stroke_width.value(),
            "start_ap": self._start_ap,
            "end_ap":   self._end_ap,
        }

    def get_type_text(self) -> str:
        return self.cmb_type.currentText()

    def set_type_text(self, cable_type: str):
        value = (cable_type or "").strip() or self.DEFAULT_CABLE_TYPE
        self.cmb_type.setCurrentText(value)

    def set_type_choices(self, choices: list[str]):
        current = self.get_type_text().strip() or self.DEFAULT_CABLE_TYPE
        merged: list[str] = []

        for choice in choices:
            text = (choice or "").strip()
            if text and text not in merged:
                merged.append(text)

        if current not in merged:
            merged.append(current)
        if self.DEFAULT_CABLE_TYPE not in merged:
            merged.insert(0, self.DEFAULT_CABLE_TYPE)

        self.cmb_type.blockSignals(True)
        self.cmb_type.clear()
        self.cmb_type.addItems(merged)
        self.cmb_type.setCurrentText(current)
        self.cmb_type.blockSignals(False)

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["cable_id"] = self.cable_id
        return d

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.cable_id))
        c = d.get("color", self._color.name())
        self._color = QColor(c)
        self._update_color_button()
        self.set_type_text(d.get("type", self.DEFAULT_CABLE_TYPE))
        self.te_comment.setPlainText(d.get("comment", ""))
        self.chk_visible.setChecked(d.get("visible", True))
        self.chk_label_visible.setChecked(d.get("label_visible", True))
        self.chk_type_label_visible.setChecked(d.get("type_label_visible", False))
        self.sb_label_size.setValue(d.get("label_size", 12.0))
        self.sb_stroke_width.setValue(d.get("stroke_width", 2.0))
        self.set_start_ap(d.get("start_ap", ""))
        self.set_end_ap(d.get("end_ap", ""))


# ================================================================== #
#  HKV: Heizkreisverteiler Panel                                       #
# ================================================================== #

class HkvPanel(QWidget):
    """Panel for a Heizkreisverteiler (HKV) – behaves like an AP."""
    delete_requested   = Signal(str)
    name_changed       = Signal(str, str)
    color_changed      = Signal(str, str)
    size_changed       = Signal(str)
    icon_changed       = Signal(str, str)
    visibility_changed = Signal(str, bool)
    place_requested    = Signal(str)
    label_size_changed = Signal(str, float)
    label_visibility_changed = Signal(str, bool)

    def __init__(self, hkv_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.hkv_id = hkv_id
        self._name = name or hkv_id
        self._icon_path: str | None = None
        self._color = QColor(color or "#e53935")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda c: self.visibility_changed.emit(self.hkv_id, c))
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(True)
        self.chk_label_visible.toggled.connect(
            lambda c: self.label_visibility_changed.emit(self.hkv_id, c))
        form.addRow(self.chk_label_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.hkv_id, v))
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.sb_width = SafeDoubleSpinBox()
        self.sb_width.setRange(0.01, 999999.0)
        self.sb_width.setSingleStep(1.0)
        self.sb_width.setValue(5.0)
        self.sb_width.setSuffix(" cm")
        self.sb_width.valueChanged.connect(
            lambda _: self.size_changed.emit(self.hkv_id))
        form.addRow("Breite:", self.sb_width)

        self.sb_height = SafeDoubleSpinBox()
        self.sb_height.setRange(0.01, 999999.0)
        self.sb_height.setSingleStep(1.0)
        self.sb_height.setValue(5.0)
        self.sb_height.setSuffix(" cm")
        self.sb_height.valueChanged.connect(
            lambda _: self.size_changed.emit(self.hkv_id))
        form.addRow("H\u00f6he:", self.sb_height)

        self.btn_icon = QPushButton("Symbol laden\u2026")
        self.btn_icon.clicked.connect(self._load_icon)
        form.addRow("Symbol:", self.btn_icon)

        self.sb_label_size = SafeDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(12.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.hkv_id, v))
        form.addRow("Schriftgr\u00f6\u00dfe:", self.sb_label_size)

        self.btn_place = QPushButton("\U0001f4cd Platzieren")
        self.btn_place.clicked.connect(
            lambda: self.place_requested.emit(self.hkv_id))
        form.addRow(self.btn_place)

        self.btn_delete = QPushButton("\U0001f5d1\ufe0f L\u00f6schen")
        self.btn_delete.clicked.connect(
            lambda: self.delete_requested.emit(self.hkv_id))
        form.addRow(self.btn_delete)

        layout.addLayout(form)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "HKV-Farbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.hkv_id, self._color.name())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(
            f"background:{self._color.name()}; color:white;")

    def _load_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Symbol laden", "", "Bilder (*.png *.jpg *.svg *.bmp)")
        if path:
            self._icon_path = path
            self.btn_icon.setText(path.split("/")[-1].split("\\")[-1])
            self.icon_changed.emit(self.hkv_id, path)

    def get_parameters(self) -> dict:
        return {
            "name":      self.le_name.text().strip() or self.hkv_id,
            "color":     self._color.name(),
            "width":     self.sb_width.value() * 10,   # cm→mm
            "height":    self.sb_height.value() * 10,
            "icon_path": self._icon_path or "",
            "visible":   self.chk_visible.isChecked(),
            "label_visible": self.chk_label_visible.isChecked(),
            "label_size": self.sb_label_size.value(),
        }

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["hkv_id"] = self.hkv_id
        return d

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.hkv_id))
        c = d.get("color", self._color.name())
        self._color = QColor(c)
        self._update_color_button()
        self.sb_width.setValue(d.get("width", 50.0) / 10)
        self.sb_height.setValue(d.get("height", 50.0) / 10)
        self.chk_visible.setChecked(d.get("visible", True))
        self.chk_label_visible.setChecked(d.get("label_visible", True))
        self.sb_label_size.setValue(d.get("label_size", 12.0))
        icon = d.get("icon_path", "")
        if icon:
            self._icon_path = icon
            self.btn_icon.setText(icon.split("/")[-1].split("\\")[-1])


# ================================================================== #
#  HKV: Verbindungsleitung Panel                                       #
# ================================================================== #

class HkvLinePanel(QWidget):
    """Panel for an HKV connecting line (double-pipe between two HKVs)."""
    delete_requested      = Signal(str)
    name_changed          = Signal(str, str)
    color_changed         = Signal(str, str)
    draw_line_requested   = Signal(str)
    edit_line_requested   = Signal(str)
    visibility_changed    = Signal(str, bool)
    label_size_changed    = Signal(str, float)
    label_visibility_changed = Signal(str, bool)

    def __init__(self, line_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.line_id = line_id
        self._name = name or line_id
        self._color = QColor(color or "#e53935")
        self._start_hkv: str = ""
        self._end_hkv: str = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda c: self.visibility_changed.emit(self.line_id, c))
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(True)
        self.chk_label_visible.toggled.connect(
            lambda c: self.label_visibility_changed.emit(self.line_id, c))
        form.addRow(self.chk_label_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.line_id, v))
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.le_type = QLineEdit("DN20")
        form.addRow("Rohrtyp:", self.le_type)

        self.lbl_length = QLabel("L\u00e4nge: \u2013")
        form.addRow(self.lbl_length)

        self.lbl_start_hkv = QLabel("Start-HKV: \u2013")
        form.addRow(self.lbl_start_hkv)
        self.lbl_end_hkv = QLabel("End-HKV: \u2013")
        form.addRow(self.lbl_end_hkv)

        self.sb_label_size = SafeDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(12.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.line_id, v))
        form.addRow("Schriftgr\u00f6\u00dfe:", self.sb_label_size)

        self.btn_draw = QPushButton("\u270f\ufe0f Zeichnen")
        self.btn_draw.clicked.connect(
            lambda: self.draw_line_requested.emit(self.line_id))
        form.addRow(self.btn_draw)

        self.btn_edit = QPushButton("\u2702\ufe0f Bearbeiten")
        self.btn_edit.clicked.connect(
            lambda: self.edit_line_requested.emit(self.line_id))
        form.addRow(self.btn_edit)

        self.btn_delete = QPushButton("\U0001f5d1\ufe0f L\u00f6schen")
        self.btn_delete.clicked.connect(
            lambda: self.delete_requested.emit(self.line_id))
        form.addRow(self.btn_delete)

        layout.addLayout(form)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "Farbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.line_id, self._color.name())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(
            f"background:{self._color.name()}; color:white;")

    def set_length(self, length_mm: float):
        self.lbl_length.setText(f"L\u00e4nge: {length_mm / 1000:.2f} m")

    def set_start_hkv(self, name: str):
        self._start_hkv = name
        self.lbl_start_hkv.setText(f"Start-HKV: {name}" if name else "Start-HKV: \u2013")

    def set_end_hkv(self, name: str):
        self._end_hkv = name
        self.lbl_end_hkv.setText(f"End-HKV: {name}" if name else "End-HKV: \u2013")

    def get_parameters(self) -> dict:
        return {
            "name":      self.le_name.text().strip() or self.line_id,
            "color":     self._color.name(),
            "type":      self.le_type.text().strip() or "DN20",
            "visible":   self.chk_visible.isChecked(),
            "label_visible": self.chk_label_visible.isChecked(),
            "label_size": self.sb_label_size.value(),
            "start_hkv": self._start_hkv,
            "end_hkv":   self._end_hkv,
        }

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["line_id"] = self.line_id
        return d

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.line_id))
        c = d.get("color", self._color.name())
        self._color = QColor(c)
        self._update_color_button()
        self.le_type.setText(d.get("type", "DN20"))
        self.chk_visible.setChecked(d.get("visible", True))
        self.chk_label_visible.setChecked(d.get("label_visible", True))
        self.sb_label_size.setValue(d.get("label_size", 12.0))
        self.set_start_hkv(d.get("start_hkv", ""))
        self.set_end_hkv(d.get("end_hkv", ""))


# ================================================================== #
#  Text Annotation Panel                                               #
# ================================================================== #

class TextAnnotationPanel(QWidget):
    """Panel for a text annotation placed on the canvas."""

    delete_requested    = Signal(str)
    place_requested     = Signal(str)
    name_changed        = Signal(str, str)
    content_changed     = Signal(str, str)
    comment_changed     = Signal(str, str)
    font_size_changed   = Signal(str, float)
    color_changed       = Signal(str, str)
    visibility_changed  = Signal(str, bool)

    def __init__(self, text_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.text_id = text_id
        self._name = name or text_id
        self._color = QColor(color or "#ffffff")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda checked: self.visibility_changed.emit(self.text_id, checked))
        form.addRow(self.chk_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.text_id, v))
        form.addRow("Name:", self.le_name)

        self.te_content = QTextEdit()
        self.te_content.setMaximumHeight(80)
        self.te_content.setPlainText("Text")
        self.te_content.textChanged.connect(self._on_content_changed)
        form.addRow("Text:", self.te_content)

        self.sb_font_size = SafeDoubleSpinBox()
        self.sb_font_size.setRange(1.0, 999999.0)
        self.sb_font_size.setSingleStep(1.0)
        self.sb_font_size.setValue(14.0)
        self.sb_font_size.setSuffix(" pt")
        self.sb_font_size.valueChanged.connect(
            lambda v: self.font_size_changed.emit(self.text_id, v))
        form.addRow("Schriftgröße:", self.sb_font_size)

        self.te_comment = QTextEdit()
        self.te_comment.setMaximumHeight(60)
        self.te_comment.setPlaceholderText("Kommentar (Mouseover)")
        self.te_comment.textChanged.connect(self._on_comment_changed)
        form.addRow("Kommentar:", self.te_comment)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Textfarbe:", self.btn_color)

        self.btn_place = QPushButton("\U0001f4cd Platzieren")
        self.btn_place.clicked.connect(
            lambda: self.place_requested.emit(self.text_id))
        form.addRow(self.btn_place)

        self.btn_delete = QPushButton("\U0001f5d1\ufe0f Löschen")
        self.btn_delete.clicked.connect(
            lambda: self.delete_requested.emit(self.text_id))
        form.addRow(self.btn_delete)

        layout.addLayout(form)

    def _on_content_changed(self):
        self.content_changed.emit(self.text_id, self.te_content.toPlainText())

    def _on_comment_changed(self):
        self.comment_changed.emit(self.text_id, self.te_comment.toPlainText())

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "Textfarbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.text_id, color.name())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(
            f"background-color:{self._color.name()}; color:#000; padding:4px;")

    def get_parameters(self) -> dict:
        return {
            "name": self.le_name.text(),
            "content": self.te_content.toPlainText(),
            "font_size": self.sb_font_size.value(),
            "color": self._color.name(),
            "comment": self.te_comment.toPlainText(),
            "visible": self.chk_visible.isChecked(),
        }

    def set_parameters(self, d: dict):
        if "name" in d:
            self.le_name.setText(d["name"])
        if "content" in d:
            self.te_content.setPlainText(d["content"])
        if "font_size" in d:
            self.sb_font_size.setValue(d["font_size"])
        if "color" in d:
            self._color = QColor(d["color"])
            self._update_color_button()
        if "comment" in d:
            self.te_comment.setPlainText(d["comment"])
        if "visible" in d:
            self.chk_visible.setChecked(d["visible"])


# ================================================================== #
#  Grundriss Panel  (one per floor plan image)                         #
# ================================================================== #

class FloorPlanPanel(QWidget):
    """Property panel for a single floor plan / background image."""

    delete_requested        = Signal(str)
    name_changed           = Signal(str, str)       # (fp_id, new_name)
    visibility_changed     = Signal(str, bool)      # (fp_id, visible)
    file_browse_requested  = Signal(str)           # fp_id
    polygon_draw_requested = Signal(str)           # fp_id
    polygon_color_changed  = Signal(str, str)      # (fp_id, color)
    ref_line_requested     = Signal(str)            # fp_id
    ref_line_color_changed = Signal(str, str)       # (fp_id, color hex)
    ref_line_visibility_changed = Signal(str, bool) # (fp_id, visible)
    size_changed           = Signal(str)            # fp_id  (fixed_width/height)
    ref_length_confirmed   = Signal(str, float)     # (fp_id, length_mm)
    transform_changed      = Signal(str)            # fp_id  (offset / rotation)
    opacity_changed        = Signal(str, float)     # (fp_id, 0..1)
    move_requested         = Signal(str)            # fp_id
    rotate_requested       = Signal(str)            # fp_id
    move_up_requested      = Signal(str)            # fp_id
    move_down_requested    = Signal(str)            # fp_id
    add_furniture_requested = Signal(str)           # fp_id

    def __init__(self, fp_id: str, name: str | None = None, parent=None):
        super().__init__(parent)
        self.fp_id = fp_id
        self._name = name or fp_id
        self._file_path: str = ""
        self._polygon_color = QColor("#8d99ae")
        self._build_ui()

    # ── UI ─────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        # Name
        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(self._emit_name)
        form.addRow("Name:", self.le_name)

        # Visibility
        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda c: self.visibility_changed.emit(self.fp_id, c)
        )
        form.addRow(self.chk_visible)

        # File path + browse
        file_row = QHBoxLayout()
        self.lbl_file = QLabel("(kein Bild)")
        self.lbl_file.setStyleSheet("color:#aaa;")
        self.lbl_file.setWordWrap(True)
        file_row.addWidget(self.lbl_file, stretch=1)
        btn_browse = QPushButton("\U0001f4c2")
        btn_browse.setToolTip("Bilddatei wählen")
        btn_browse.setFixedWidth(32)
        btn_browse.clicked.connect(
            lambda: self.file_browse_requested.emit(self.fp_id)
        )
        file_row.addWidget(btn_browse)
        self.btn_draw_polygon = QPushButton("\u270f")
        self.btn_draw_polygon.setToolTip("Alternativ Polygon zeichnen")
        self.btn_draw_polygon.setFixedWidth(32)
        self.btn_draw_polygon.clicked.connect(
            lambda: self.polygon_draw_requested.emit(self.fp_id)
        )
        self.btn_draw_polygon.hide()
        file_row.addWidget(self.btn_draw_polygon)
        form.addRow("Datei:", file_row)

        # Opacity
        self.sb_opacity = SafeDoubleSpinBox()
        self.sb_opacity.setRange(0.0, 1.0)
        self.sb_opacity.setSingleStep(0.05)
        self.sb_opacity.setDecimals(2)
        self.sb_opacity.setValue(1.0)
        self.sb_opacity.valueChanged.connect(
            lambda v: self.opacity_changed.emit(self.fp_id, v)
        )
        form.addRow("Deckkraft:", self.sb_opacity)

        # Offset X / Y
        self.sb_offset_x = SafeDoubleSpinBox()
        self.sb_offset_x.setRange(-99999.0, 99999.0)
        self.sb_offset_x.setSingleStep(1.0)
        self.sb_offset_x.setDecimals(1)
        self.sb_offset_x.setSuffix(" px")
        self.sb_offset_x.valueChanged.connect(self._emit_transform)
        form.addRow("Versatz X:", self.sb_offset_x)

        self.sb_offset_y = SafeDoubleSpinBox()
        self.sb_offset_y.setRange(-99999.0, 99999.0)
        self.sb_offset_y.setSingleStep(1.0)
        self.sb_offset_y.setDecimals(1)
        self.sb_offset_y.setSuffix(" px")
        self.sb_offset_y.valueChanged.connect(self._emit_transform)
        form.addRow("Versatz Y:", self.sb_offset_y)

        # Rotation
        self.sb_rotation = SafeDoubleSpinBox()
        self.sb_rotation.setRange(-360.0, 360.0)
        self.sb_rotation.setSingleStep(0.5)
        self.sb_rotation.setDecimals(1)
        self.sb_rotation.setSuffix(" \u00b0")
        self.sb_rotation.valueChanged.connect(self._emit_transform)
        form.addRow("Drehung:", self.sb_rotation)

        # ── Maus-Interaktion ──────────────────────────────────────
        mouse_row = QHBoxLayout()
        self.btn_move = QPushButton("\u2725 Verschieben")
        self.btn_move.setToolTip("Grundriss per Maus verschieben (ESC zum Beenden)")
        self.btn_move.setStyleSheet(
            "background:#555; color:white; padding:4px;"
        )
        self.btn_move.clicked.connect(
            lambda: self.move_requested.emit(self.fp_id)
        )
        mouse_row.addWidget(self.btn_move)

        self.btn_rotate = QPushButton("\u21bb Drehen")
        self.btn_rotate.setToolTip("Grundriss per Maus drehen (ESC zum Beenden)")
        self.btn_rotate.setStyleSheet(
            "background:#555; color:white; padding:4px;"
        )
        self.btn_rotate.clicked.connect(
            lambda: self.rotate_requested.emit(self.fp_id)
        )
        mouse_row.addWidget(self.btn_rotate)
        form.addRow(mouse_row)

        layout.addLayout(form)

        # ── Maßstab ───────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#555;")
        layout.addWidget(sep)

        scale_title = QLabel("\U0001f4cf Ma\u00dfstab")
        scale_title.setStyleSheet("font-weight:bold; padding:4px 0;")
        layout.addWidget(scale_title)

        self.btn_ref = QPushButton("\u2460 Referenzlinie zeichnen")
        self.btn_ref.setStyleSheet(
            "background:#555; color:white; padding:5px; text-align:left;"
        )
        self.btn_ref.clicked.connect(
            lambda: self.ref_line_requested.emit(self.fp_id)
        )
        layout.addWidget(self.btn_ref)

        step2_lbl = QLabel("\u2461 Reale L\u00e4nge eingeben:")
        step2_lbl.setStyleSheet("color:#cccccc; margin-top:4px;")
        layout.addWidget(step2_lbl)

        input_row = QHBoxLayout()
        self.sb_ref_length = SafeDoubleSpinBox()
        self.sb_ref_length.setRange(0.01, 100.0)
        self.sb_ref_length.setDecimals(3)
        self.sb_ref_length.setSingleStep(0.1)
        self.sb_ref_length.setValue(1.0)
        self.sb_ref_length.setSuffix(" m")
        self.btn_apply = QPushButton("\u2714 Anwenden")
        self.btn_apply.setStyleSheet(
            "background:#0070b4; color:white; font-weight:bold; padding:4px;"
        )
        self.btn_apply.clicked.connect(self._on_apply_scale)
        input_row.addWidget(self.sb_ref_length)
        input_row.addWidget(self.btn_apply)
        layout.addLayout(input_row)

        self.lbl_scale = QLabel("Ma\u00dfstab: noch nicht gesetzt")
        self.lbl_scale.setStyleSheet(
            "color:#ffdd00; font-weight:bold; margin-top:4px;"
        )
        layout.addWidget(self.lbl_scale)

        # ── Referenzlinien-Optionen ────────────────────────────────
        sep_ref_options = QFrame()
        sep_ref_options.setFrameShape(QFrame.HLine)
        sep_ref_options.setStyleSheet("color:#555;")
        layout.addWidget(sep_ref_options)

        ref_options_title = QLabel("\U0001f4d1 Referenzlinie")
        ref_options_title.setStyleSheet("font-weight:bold; padding:4px 0;")
        layout.addWidget(ref_options_title)

        # Reference line visibility toggle
        self.chk_ref_visible = QCheckBox("Referenzlinie sichtbar")
        self.chk_ref_visible.setChecked(True)
        self.chk_ref_visible.toggled.connect(
            lambda c: self.ref_line_visibility_changed.emit(self.fp_id, c)
        )
        layout.addWidget(self.chk_ref_visible)

        # Reference line color
        ref_color_row = QHBoxLayout()
        ref_color_label = QLabel("Farbe:")
        self.btn_ref_color = QPushButton()
        self.btn_ref_color.setFixedHeight(32)
        self.btn_ref_color.setStyleSheet("background:#ffdd00;")
        self.btn_ref_color.clicked.connect(self._choose_ref_line_color)
        self._ref_line_color = "#ffdd00"
        ref_color_row.addWidget(ref_color_label)
        ref_color_row.addWidget(self.btn_ref_color, stretch=1)
        layout.addLayout(ref_color_row)

        # Reihenfolge (up/down) + Delete
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color:#555;")
        layout.addWidget(sep2)

        order_row = QHBoxLayout()
        self.btn_up = QPushButton("\u2b06 Nach oben")
        self.btn_up.setToolTip("Grundriss in der Hierarchie nach oben")
        self.btn_up.setStyleSheet(
            "background:#555; color:white; padding:4px;"
        )
        self.btn_up.clicked.connect(
            lambda: self.move_up_requested.emit(self.fp_id)
        )
        order_row.addWidget(self.btn_up)

        self.btn_down = QPushButton("\u2b07 Nach unten")
        self.btn_down.setToolTip("Grundriss in der Hierarchie nach unten")
        self.btn_down.setStyleSheet(
            "background:#555; color:white; padding:4px;"
        )
        self.btn_down.clicked.connect(
            lambda: self.move_down_requested.emit(self.fp_id)
        )
        order_row.addWidget(self.btn_down)
        layout.addLayout(order_row)

        self.btn_delete = QPushButton("\U0001f5d1 Grundriss entfernen")
        self.btn_delete.setStyleSheet(
            "background:#c62828; color:white; padding:4px;"
        )
        self.btn_delete.clicked.connect(
            lambda: self.delete_requested.emit(self.fp_id)
        )
        layout.addWidget(self.btn_delete)

        # ── Einrichtungsgegenstände ────────────────────────────────
        self._einr_sep = QFrame()
        self._einr_sep.setFrameShape(QFrame.HLine)
        self._einr_sep.setStyleSheet("color:#555;")
        layout.addWidget(self._einr_sep)

        # ── Feste Abmessungen (nur für Einrichtung, standardmäßig ausgeblendet) ──
        self._fixed_size_sep = QFrame()
        self._fixed_size_sep.setFrameShape(QFrame.HLine)
        self._fixed_size_sep.setStyleSheet("color:#555;")
        layout.addWidget(self._fixed_size_sep)
        self._fixed_size_sep.hide()

        self._fixed_size_title = QLabel("\U0001f4d0 Abmessungen")
        self._fixed_size_title.setStyleSheet("font-weight:bold; padding:4px 0;")
        layout.addWidget(self._fixed_size_title)
        self._fixed_size_title.hide()

        fixed_size_form = QFormLayout()
        fixed_size_form.setContentsMargins(0, 0, 0, 0)
        fixed_size_form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.sb_fixed_width = SafeDoubleSpinBox()
        self.sb_fixed_width.setRange(0.0, 100.0)
        self.sb_fixed_width.setDecimals(3)
        self.sb_fixed_width.setSingleStep(0.01)
        self.sb_fixed_width.setValue(0.0)
        self.sb_fixed_width.setSpecialValueText("\u2013\u2013 (auto)")
        self.sb_fixed_width.setSuffix(" m")
        self.sb_fixed_width.setToolTip("Breite der Einrichtung in Metern (0 = Referenzlinie nutzen)")
        self.sb_fixed_width.valueChanged.connect(
            lambda _: self.size_changed.emit(self.fp_id)
        )
        fixed_size_form.addRow("Breite:", self.sb_fixed_width)

        self.sb_fixed_height = SafeDoubleSpinBox()
        self.sb_fixed_height.setRange(0.0, 100.0)
        self.sb_fixed_height.setDecimals(3)
        self.sb_fixed_height.setSingleStep(0.01)
        self.sb_fixed_height.setValue(0.0)
        self.sb_fixed_height.setSpecialValueText("\u2013\u2013 (auto)")
        self.sb_fixed_height.setSuffix(" m")
        self.sb_fixed_height.setToolTip("Tiefe der Einrichtung in Metern (0 = Referenzlinie nutzen)")
        self.sb_fixed_height.valueChanged.connect(
            lambda _: self.size_changed.emit(self.fp_id)
        )
        fixed_size_form.addRow("Tiefe:", self.sb_fixed_height)

        self._fixed_size_widget = QWidget()
        self._fixed_size_widget.setLayout(fixed_size_form)
        layout.addWidget(self._fixed_size_widget)
        self._fixed_size_widget.hide()

        self._polygon_color_row = QHBoxLayout()
        self._polygon_color_btn = QPushButton("Farbe")
        self._polygon_color_btn.setToolTip("Farbe des Einrichtungs-Polygons")
        self._polygon_color_btn.clicked.connect(self._choose_polygon_color)
        self._update_polygon_color_button()
        self._polygon_color_row.addWidget(self._polygon_color_btn)
        self._polygon_color_widget = QWidget()
        self._polygon_color_widget.setLayout(self._polygon_color_row)
        fixed_size_form.addRow("Polygonfarbe:", self._polygon_color_widget)
        self._polygon_color_widget.hide()

        self.btn_add_furniture = QPushButton("\U0001fa91 Einrichtung hinzuf\u00fcgen")
        self.btn_add_furniture.setToolTip(
            "F\u00fcgt diesem Grundriss ein Einrichtungselement (SVG/Bild) hinzu"
        )
        self.btn_add_furniture.setStyleSheet(
            "background:#546e7a; color:white; padding:4px;"
        )
        self.btn_add_furniture.clicked.connect(
            lambda: self.add_furniture_requested.emit(self.fp_id)
        )
        layout.addWidget(self.btn_add_furniture)

    # ── Helpers ────────────────────────────────────────────────────

    def _emit_name(self, text: str):
        self._name = text
        self.name_changed.emit(self.fp_id, text)

    def _emit_transform(self):
        self.transform_changed.emit(self.fp_id)

    def set_transform_silent(self, offset_x: float, offset_y: float,
                             rotation: float):
        """Update spinboxes without emitting transform_changed."""
        self.sb_offset_x.blockSignals(True)
        self.sb_offset_y.blockSignals(True)
        self.sb_rotation.blockSignals(True)
        self.sb_offset_x.setValue(offset_x)
        self.sb_offset_y.setValue(offset_y)
        self.sb_rotation.setValue(rotation)
        self.sb_offset_x.blockSignals(False)
        self.sb_offset_y.blockSignals(False)
        self.sb_rotation.blockSignals(False)

    def _on_apply_scale(self):
        self.ref_length_confirmed.emit(
            self.fp_id, self.sb_ref_length.value() * 1000.0
        )

    def set_file_path(self, path: str):
        self._file_path = path
        from pathlib import Path as P
        self.lbl_file.setText(P(path).name if path else "(kein Bild)")

    def set_polygon_source(self):
        """Mark panel source as polygon (no image file path)."""
        self._file_path = ""
        self.lbl_file.setText("(Polygon)")

    def _choose_polygon_color(self):
        color = QColorDialog.getColor(
            self._polygon_color, self, "Polygonfarbe wählen"
        )
        if color.isValid():
            self._polygon_color = color
            self._update_polygon_color_button()
            self.polygon_color_changed.emit(self.fp_id, color.name())

    def _update_polygon_color_button(self):
        self._polygon_color_btn.setStyleSheet(
            f"background:{self._polygon_color.name()}; color:white;"
        )

    def _choose_ref_line_color(self):
        color = QColorDialog.getColor(
            QColor(self._ref_line_color), self, "Referenzlinien-Farbe wählen"
        )
        if color.isValid():
            self._ref_line_color = color.name()
            self.btn_ref_color.setStyleSheet(f"background:{self._ref_line_color};")
            self.ref_line_color_changed.emit(self.fp_id, self._ref_line_color)

    def update_scale_label(self, mm_per_px: float):
        self.lbl_scale.setText(f"Ma\u00dfstab: {mm_per_px / 1000:.6f} m/px")
        self.btn_apply.setStyleSheet(
            "background:#2dc653; color:white; font-weight:bold; padding:4px;"
        )

    def get_parameters(self) -> dict:
        return {
            "name": self.le_name.text(),
            "visible": self.chk_visible.isChecked(),
            "file_path": self._file_path,
            "polygon_color": self._polygon_color.name(),
            "ref_line_visible": self.chk_ref_visible.isChecked(),
            "ref_line_color": self._ref_line_color,
            "opacity": self.sb_opacity.value(),
            "offset_x": self.sb_offset_x.value(),
            "offset_y": self.sb_offset_y.value(),
            "rotation": self.sb_rotation.value(),
            "ref_length_mm": self.sb_ref_length.value() * 1000.0,
            "fixed_width_mm": self.sb_fixed_width.value() * 1000.0,
            "fixed_height_mm": self.sb_fixed_height.value() * 1000.0,
        }

    def to_dict(self) -> dict:
        return self.get_parameters()

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.fp_id))
        self.chk_visible.setChecked(d.get("visible", True))
        poly_col = d.get("polygon_color", "#8d99ae")
        self._polygon_color = QColor(poly_col)
        self._update_polygon_color_button()
        self._ref_line_color = d.get("ref_line_color", "#ffdd00")
        self.btn_ref_color.setStyleSheet(f"background:{self._ref_line_color};")
        self.chk_ref_visible.setChecked(d.get("ref_line_visible", True))
        self.sb_opacity.setValue(d.get("opacity", 1.0))
        self.sb_offset_x.setValue(d.get("offset_x", 0.0))
        self.sb_offset_y.setValue(d.get("offset_y", 0.0))
        self.sb_rotation.setValue(d.get("rotation", 0.0))
        self.sb_ref_length.setValue(d.get("ref_length_mm", 1000.0) / 1000.0)
        self.sb_fixed_width.blockSignals(True)
        self.sb_fixed_height.blockSignals(True)
        self.sb_fixed_width.setValue(d.get("fixed_width_mm", 0.0) / 1000.0)
        self.sb_fixed_height.setValue(d.get("fixed_height_mm", 0.0) / 1000.0)
        self.sb_fixed_width.blockSignals(False)
        self.sb_fixed_height.blockSignals(False)
        fp = d.get("file_path", "")
        if fp:
            self.set_file_path(fp)

    def configure_as_furniture(self):
        """Hide controls that only apply to top-level floor plans."""
        self.btn_up.hide()
        self.btn_down.hide()
        self._einr_sep.hide()
        self.btn_add_furniture.hide()
        self.btn_draw_polygon.show()
        self.btn_delete.setText("\U0001f5d1 Einrichtung entfernen")
        # Feste Abmessungen anzeigen
        self._fixed_size_sep.show()
        self._fixed_size_title.show()
        self._fixed_size_widget.show()
        self._polygon_color_widget.show()


# ================================================================== #
#  Main Parameter Panel  –  TreeView + Eigenschaftenfenster            #
# ================================================================== #

class ParameterPanel(QWidget):
    """Right-side panel: TreeView for element list + property editor for the
    currently selected element."""

    item_selected               = Signal(str)   # (item_id) - emitted when tree item is selected
    delete_requested            = Signal(str)
    add_floorplan_requested     = Signal()
    delete_floorplan_requested  = Signal(str)
    floorplan_file_browse       = Signal(str)
    floorplan_polygon_draw      = Signal(str)
    floorplan_polygon_color_changed = Signal(str, str)
    floorplan_ref_line          = Signal(str)
    floorplan_ref_line_color_changed = Signal(str, str)  # (fp_id, color hex)
    floorplan_ref_line_visibility_changed = Signal(str, bool)  # (fp_id, visible)
    floorplan_ref_confirmed     = Signal(str, float)
    floorplan_transform_changed = Signal(str)
    floorplan_opacity_changed   = Signal(str, float)
    floorplan_visibility_changed = Signal(str, bool)
    floorplan_move_requested     = Signal(str)
    floorplan_rotate_requested   = Signal(str)
    floorplan_order_changed      = Signal()
    ref_line_requested          = Signal()
    ref_length_confirmed        = Signal(float)
    add_circuit_requested       = Signal(str)   # fp_id
    add_elec_point_requested    = Signal(str)   # fp_id
    add_elec_room_requested     = Signal(str)   # fp_id
    add_elec_cable_requested    = Signal(str)   # fp_id
    add_hkv_requested           = Signal(str)   # fp_id
    add_hkv_line_requested      = Signal(str)   # fp_id
    delete_elec_point_requested = Signal(str)
    delete_elec_room_requested  = Signal(str)
    delete_elec_cable_requested = Signal(str)
    delete_hkv_requested        = Signal(str)
    delete_hkv_line_requested   = Signal(str)
    duplicate_elec_point_requested = Signal(str)
    duplicate_elec_cable_requested = Signal(str)
    all_hk_visibility_changed      = Signal(bool)
    all_elec_visibility_changed    = Signal(bool)
    heating_global_changed         = Signal()
    bom_metadata_changed           = Signal()
    add_furniture_requested        = Signal(str)   # parent_fp_id
    delete_furniture_requested     = Signal(str)   # furniture_id
    furniture_size_changed         = Signal(str)   # furniture_id
    add_text_requested             = Signal(str)   # fp_id
    delete_text_requested          = Signal(str)   # text_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.circuit_panels: dict[str, HeatingCircuitPanel] = {}
        self.elec_point_panels: dict[str, ElektroPointPanel] = {}
        self._last_elec_point_device: str = ""
        self._last_elec_point_device_color: str = ""
        self.elec_room_panels: dict[str, ElektroRoomPanel] = {}
        self.elec_cable_panels: dict[str, ElektroCablePanel] = {}
        self._last_elec_cable_defaults: dict = self._default_elec_cable_defaults()
        self._bom_metadata: dict = self._default_bom_metadata()
        self.hkv_panels: dict[str, HkvPanel] = {}
        self.hkv_line_panels: dict[str, HkvLinePanel] = {}
        self.text_panels: dict[str, TextAnnotationPanel] = {}
        self.floorplan_panels: dict[str, FloorPlanPanel] = {}
        self.furniture_panels: dict[str, FloorPlanPanel] = {}
        self._furniture_parent: dict[str, str] = {}  # furniture_id -> parent_fp_id
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._fp_sub_items: dict[str, dict] = {}      # fp_id -> {hk, hkv, hkv_line, ap, room, kv, text}
        self._element_floorplan: dict[str, str] = {}  # element_id -> fp_id
        self._loading = False
        self._active_panel: QWidget | None = None
        self._active_special: str | None = "empty"  # "empty", "heat", or None
        self._build_ui()

    # ──────────────────────────────────────────────────────────────── #
    #  UI                                                               #
    # ──────────────────────────────────────────────────────────────── #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── Hinzufügen-Buttons ─────────────────────────────────────
        btn_row0 = QHBoxLayout()
        self.btn_add_floorplan = QPushButton("\u2795 Grundriss")
        self.btn_add_floorplan.setStyleSheet(
            "background:#6d4c41; color:white; padding:4px;"
        )
        self.btn_add_floorplan.clicked.connect(self.add_floorplan_requested)
        btn_row0.addWidget(self.btn_add_floorplan)
        layout.addLayout(btn_row0)

        btn_row = QHBoxLayout()
        self.btn_add_circuit = QPushButton("\u2795 Heizkreis")
        self.btn_add_circuit.setStyleSheet(
            "background:#457b9d; color:white; padding:4px;"
        )
        self.btn_add_circuit.clicked.connect(
            lambda: self.add_circuit_requested.emit(self.get_active_floorplan_id() or ""))
        btn_row.addWidget(self.btn_add_circuit)

        self.btn_add_point = QPushButton("\u2795 AP")
        self.btn_add_point.setStyleSheet(
            "background:#ff9800; color:white; padding:4px;"
        )
        self.btn_add_point.clicked.connect(
            lambda: self.add_elec_point_requested.emit(self.get_active_floorplan_id() or ""))
        btn_row.addWidget(self.btn_add_point)

        self.btn_add_room = QPushButton("➕ Raum")
        self.btn_add_room.setStyleSheet(
            "background:#43aa8b; color:white; padding:4px;"
        )
        self.btn_add_room.clicked.connect(
            lambda: self.add_elec_room_requested.emit(self.get_active_floorplan_id() or ""))
        btn_row.addWidget(self.btn_add_room)

        self.btn_add_cable = QPushButton("\u2795 Kabel")
        self.btn_add_cable.setStyleSheet(
            "background:#ff9800; color:white; padding:4px;"
        )
        self.btn_add_cable.clicked.connect(
            lambda: self.add_elec_cable_requested.emit(self.get_active_floorplan_id() or ""))
        btn_row.addWidget(self.btn_add_cable)
        layout.addLayout(btn_row)

        btn_row2 = QHBoxLayout()
        self.btn_add_hkv = QPushButton("\u2795 HKV")
        self.btn_add_hkv.setStyleSheet(
            "background:#e53935; color:white; padding:4px;"
        )
        self.btn_add_hkv.clicked.connect(
            lambda: self.add_hkv_requested.emit(self.get_active_floorplan_id() or ""))
        btn_row2.addWidget(self.btn_add_hkv)

        self.btn_add_hkv_line = QPushButton("\u2795 HKV-Leitung")
        self.btn_add_hkv_line.setStyleSheet(
            "background:#e53935; color:white; padding:4px;"
        )
        self.btn_add_hkv_line.clicked.connect(
            lambda: self.add_hkv_line_requested.emit(self.get_active_floorplan_id() or ""))
        btn_row2.addWidget(self.btn_add_hkv_line)

        self.btn_add_text = QPushButton("\u2795 Text")
        self.btn_add_text.setStyleSheet(
            "background:#7b1fa2; color:white; padding:4px;"
        )
        self.btn_add_text.clicked.connect(
            lambda: self.add_text_requested.emit(self.get_active_floorplan_id() or ""))
        btn_row2.addWidget(self.btn_add_text)

        self.btn_bom = QPushButton("📦 Stückliste")
        self.btn_bom.setStyleSheet(
            "background:#546e7a; color:white; padding:4px;"
        )
        self.btn_bom.clicked.connect(self._open_bom_editor)
        btn_row2.addWidget(self.btn_bom)
        layout.addLayout(btn_row2)

        # ── Splitter: TreeView + Eigenschaften ─────────────────────
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        # -- TreeView -----------------------------------------------
        self._tree = DragDropTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        self._tree.setDragEnabled(True)
        self._tree.setAcceptDrops(True)
        self._tree.setDropIndicatorShown(True)
        self._tree.setDragDropMode(QAbstractItemView.InternalMove)
        self._tree.setDefaultDropAction(Qt.MoveAction)

        # Floor plan items are created dynamically via add_floorplan_panel()
        # Connect AFTER initial setup to avoid spurious signals
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        self._tree.itemChanged.connect(self._on_tree_item_changed)
        self._tree.items_dropped.connect(self._on_tree_items_dropped)
        splitter.addWidget(self._tree)

        # -- Eigenschaftenbereich (property panel) -------------------
        prop_scroll = QScrollArea()
        prop_scroll.setWidgetResizable(True)
        prop_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._prop_container = QWidget()
        self._prop_layout = QVBoxLayout(self._prop_container)
        self._prop_layout.setContentsMargins(4, 4, 4, 4)
        self._prop_layout.setSpacing(0)

        self._empty_label = QLabel(
            "W\u00e4hle einen Eintrag\naus der Liste oben."
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color:#888; padding:20px;")
        self._prop_layout.addWidget(self._empty_label)

        # ── Grundriss-Panels werden dynamisch per add_floorplan_panel() angelegt

        # ── Heizung Allgemein (shown when 🔥 Heizung header selected)
        self._heat_global_panel = QWidget()
        hg_layout = QVBoxLayout(self._heat_global_panel)
        hg_layout.setContentsMargins(4, 4, 4, 4)
        hg_title = QLabel("\U0001f321 Heizung Allgemein")
        hg_title.setStyleSheet("font-weight:bold; font-size:13px; padding:4px 0;")
        hg_layout.addWidget(hg_title)
        hg_form = QFormLayout()
        hg_form.setContentsMargins(0, 0, 0, 0)
        hg_form.setRowWrapPolicy(QFormLayout.WrapAllRows)

        self.sb_vorlauf = SafeDoubleSpinBox()
        self.sb_vorlauf.setRange(20.0, 90.0)
        self.sb_vorlauf.setSingleStep(0.5)
        self.sb_vorlauf.setValue(35.0)
        self.sb_vorlauf.setDecimals(1)
        self.sb_vorlauf.setSuffix(" \u00b0C")
        self.sb_vorlauf.valueChanged.connect(
            lambda _: self.heating_global_changed.emit()
        )
        hg_form.addRow("Vorlauftemperatur:", self.sb_vorlauf)

        self.sb_ruecklauf = SafeDoubleSpinBox()
        self.sb_ruecklauf.setRange(15.0, 80.0)
        self.sb_ruecklauf.setSingleStep(0.5)
        self.sb_ruecklauf.setValue(30.0)
        self.sb_ruecklauf.setDecimals(1)
        self.sb_ruecklauf.setSuffix(" \u00b0C")
        self.sb_ruecklauf.valueChanged.connect(
            lambda _: self.heating_global_changed.emit()
        )
        hg_form.addRow("R\u00fccklauftemperatur:", self.sb_ruecklauf)

        self.sb_norm_aussen = SafeDoubleSpinBox()
        self.sb_norm_aussen.setRange(-30.0, 5.0)
        self.sb_norm_aussen.setSingleStep(1.0)
        self.sb_norm_aussen.setValue(-12.0)
        self.sb_norm_aussen.setDecimals(1)
        self.sb_norm_aussen.setSuffix(" \u00b0C")
        hg_form.addRow("Normau\u00dfentemp.:", self.sb_norm_aussen)

        hg_layout.addLayout(hg_form)
        self._prop_layout.addWidget(self._heat_global_panel)
        self._heat_global_panel.hide()

        self._prop_layout.addStretch()

        prop_scroll.setWidget(self._prop_container)
        splitter.addWidget(prop_scroll)

        splitter.setStretchFactor(0, 1)   # tree
        splitter.setStretchFactor(1, 2)   # properties
        layout.addWidget(splitter, stretch=1)

        self.setMinimumWidth(340)
        self.setMaximumWidth(420)



    # ──────────────────────────────────────────────────────────────── #
    #  Tree management                                                  #
    # ──────────────────────────────────────────────────────────────── #

    def _add_tree_item(self, parent_item: QTreeWidgetItem,
                       item_id: str, name: str) -> QTreeWidgetItem:
        child = QTreeWidgetItem(parent_item, [name])
        child.setData(0, Qt.UserRole, item_id)
        child.setFlags(
            (child.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            & ~Qt.ItemIsDropEnabled
        )
        child.setCheckState(0, Qt.Checked)
        self._tree_items[item_id] = child
        parent_item.setExpanded(True)
        if not self._loading:
            self._tree.setCurrentItem(child)
        return child

    def _remove_tree_item(self, item_id: str):
        item = self._tree_items.pop(item_id, None)
        if item:
            try:
                parent = item.parent()
                if parent:
                    parent.removeChild(item)
                else:
                    idx = self._tree.indexOfTopLevelItem(item)
                    if idx >= 0:
                        self._tree.takeTopLevelItem(idx)
            except RuntimeError:
                # C++ object already deleted (e.g. parent removed first)
                pass

    def _update_tree_item_name(self, item_id: str, name: str):
        item = self._tree_items.get(item_id)
        if item:
            item.setText(0, name or item_id)

    def _on_tree_selection(self, current: QTreeWidgetItem | None,
                           previous: QTreeWidgetItem | None):
        """Show the property panel for the selected tree item."""
        if self._loading:
            return

        if not current:
            self._set_active_special("empty")
            self.item_selected.emit("")  # Clear selection on canvas
            return

        item_id = current.data(0, Qt.UserRole)
        if not item_id:
            # Sub-category headers — show heat global panel for "🔥 Heizkreise"
            for subs in self._fp_sub_items.values():
                if current is subs.get("hk"):
                    self._set_active_special("heat")
                    self.item_selected.emit("")  # Clear selection
                    return
            self._set_active_special("empty")
            self.item_selected.emit("")  # Clear selection
            return

        panel = (self.floorplan_panels.get(item_id)
                 or self.furniture_panels.get(item_id)
                 or self.circuit_panels.get(item_id)
                 or self.elec_point_panels.get(item_id)
                 or self.elec_room_panels.get(item_id)
                 or self.elec_cable_panels.get(item_id)
                 or self.hkv_panels.get(item_id)
                 or self.hkv_line_panels.get(item_id)
                 or self.text_panels.get(item_id))
        if panel:
            self._set_active_panel(panel)
            self.item_selected.emit(item_id)  # Highlight element on canvas
        else:
            self._set_active_special("empty")

    def _set_active_panel(self, panel: QWidget):
        if self._active_panel is panel and self._active_special is None:
            return
        if self._active_panel and self._active_panel is not panel:
            self._active_panel.hide()
        if self._active_special == "empty":
            self._empty_label.hide()
        elif self._active_special == "heat":
            self._heat_global_panel.hide()
        self._active_panel = panel
        self._active_special = None
        panel.show()

    def _set_active_special(self, mode: str):
        if self._active_special == mode and self._active_panel is None:
            return
        if self._active_panel:
            self._active_panel.hide()
            self._active_panel = None
        if self._active_special == "empty" and mode != "empty":
            self._empty_label.hide()
        if self._active_special == "heat" and mode != "heat":
            self._heat_global_panel.hide()

        if mode == "empty":
            self._heat_global_panel.hide()
            self._empty_label.show()
        else:
            self._empty_label.hide()
            self._heat_global_panel.show()
        self._active_special = mode

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle check-state changes on category items (group visibility)."""
        if self._loading:
            return

        item_id = item.data(0, Qt.UserRole)
        checked = item.checkState(0) == Qt.Checked
        st = Qt.Checked if checked else Qt.Unchecked

        # Floor plan item toggled → cascade to all sub-categories and elements
        if item_id and item_id in self.floorplan_panels:
            self._loading = True
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, st)
                for j in range(child.childCount()):
                    child.child(j).setCheckState(0, st)
            self._loading = False
            # Sync floor plan panel
            self.floorplan_panels[item_id].chk_visible.setChecked(checked)
            # Sync furniture children
            for fur_id, par_id in self._furniture_parent.items():
                if par_id == item_id:
                    fur_panel = self.furniture_panels.get(fur_id)
                    if fur_panel:
                        fur_panel.chk_visible.setChecked(checked)
            # Sync all elements belonging to this floor plan
            for eid, fid in self._element_floorplan.items():
                if fid == item_id:
                    panel = (self.circuit_panels.get(eid)
                             or self.elec_point_panels.get(eid)
                             or self.elec_room_panels.get(eid)
                             or self.elec_cable_panels.get(eid)
                             or self.hkv_panels.get(eid)
                             or self.hkv_line_panels.get(eid)
                             or self.text_panels.get(eid))
                    if panel:
                        panel.chk_visible.setChecked(checked)
            return

        # Sub-category item toggled → cascade to its element children only
        for fp_id, subs in self._fp_sub_items.items():
            if item in subs.values():
                self._loading = True
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, st)
                self._loading = False
                # Sync panels for elements under this specific sub-category
                for eid, fid in self._element_floorplan.items():
                    if fid != fp_id:
                        continue
                    panel = None
                    if item is subs["hk"]:
                        panel = self.circuit_panels.get(eid)
                    elif item is subs["hkv"]:
                        panel = self.hkv_panels.get(eid)
                    elif item is subs["hkv_line"]:
                        panel = self.hkv_line_panels.get(eid)
                    elif item is subs["ap"]:
                        panel = self.elec_point_panels.get(eid)
                    elif item is subs["room"]:
                        panel = self.elec_room_panels.get(eid)
                    elif item is subs["kv"]:
                        panel = self.elec_cable_panels.get(eid)
                    elif item is subs["text"]:
                        panel = self.text_panels.get(eid)
                    if panel:
                        panel.chk_visible.setChecked(checked)

                if item is subs.get("furniture"):
                    for fur_id, parent_id in self._furniture_parent.items():
                        if parent_id != fp_id:
                            continue
                        panel = self.furniture_panels.get(fur_id)
                        if panel:
                            panel.chk_visible.setChecked(checked)
                return

        # Individual leaf element (furniture or element) toggled
        if item_id:
            panel = (self.furniture_panels.get(item_id)
                     or self.circuit_panels.get(item_id)
                     or self.elec_point_panels.get(item_id)
                     or self.elec_room_panels.get(item_id)
                     or self.elec_cable_panels.get(item_id)
                     or self.hkv_panels.get(item_id)
                     or self.hkv_line_panels.get(item_id)
                     or self.text_panels.get(item_id))
            if panel:
                panel.chk_visible.setChecked(checked)

    def _sync_tree_checkbox(self, item_id: str, checked: bool):
        """Sync tree item checkbox when panel visibility changes."""
        tree_item = self._tree_items.get(item_id)
        if tree_item:
            self._loading = True
            tree_item.setCheckState(
                0, Qt.Checked if checked else Qt.Unchecked
            )
            self._loading = False

    def select_item(self, item_id: str):
        """Programmatically select an item in the tree."""
        tree_item = self._tree_items.get(item_id)
        if tree_item:
            self._tree.setCurrentItem(tree_item)

    def get_selected_item_id(self) -> str | None:
        current = self._tree.currentItem()
        if not current:
            return None
        item_id = current.data(0, Qt.UserRole)
        return item_id or None

    def _expected_subcategory_key(self, item_id: str) -> str | None:
        if item_id in self.furniture_panels:
            return "furniture"
        if item_id in self.circuit_panels:
            return "hk"
        if item_id in self.hkv_panels:
            return "hkv"
        if item_id in self.hkv_line_panels:
            return "hkv_line"
        if item_id in self.elec_point_panels:
            return "ap"
        if item_id in self.elec_room_panels:
            return "room"
        if item_id in self.elec_cable_panels:
            return "kv"
        if item_id in self.text_panels:
            return "text"
        return None

    def _ancestor_floorplan_id(self, item: QTreeWidgetItem | None) -> str | None:
        cur = item
        while cur:
            cid = cur.data(0, Qt.UserRole)
            if cid and cid in self.floorplan_panels:
                return cid
            cur = cur.parent()
        return None

    def _move_child_item(self, child: QTreeWidgetItem, new_parent: QTreeWidgetItem):
        old_parent = child.parent()
        if old_parent is new_parent:
            return
        if old_parent:
            idx = old_parent.indexOfChild(child)
            if idx >= 0:
                old_parent.takeChild(idx)
        else:
            idx = self._tree.indexOfTopLevelItem(child)
            if idx >= 0:
                self._tree.takeTopLevelItem(idx)
        new_parent.addChild(child)

    def _on_tree_items_dropped(self):
        if self._loading:
            return
        self._loading = True
        try:
            for i in range(self._tree.topLevelItemCount()):
                fp_item = self._tree.topLevelItem(i)
                fp_id = fp_item.data(0, Qt.UserRole)
                if not fp_id or fp_id not in self.floorplan_panels:
                    continue
                subs = self._fp_sub_items.get(fp_id, {})

                direct_children = [fp_item.child(j) for j in range(fp_item.childCount())]
                for child in direct_children:
                    cid = child.data(0, Qt.UserRole)
                    if not cid:
                        continue
                    expected = self._expected_subcategory_key(cid)
                    if expected and expected in subs:
                        self._move_child_item(child, subs[expected])

                for key, sub_item in subs.items():
                    sub_children = [sub_item.child(j) for j in range(sub_item.childCount())]
                    for child in sub_children:
                        cid = child.data(0, Qt.UserRole)
                        if not cid:
                            continue
                        expected = self._expected_subcategory_key(cid)
                        if expected and expected != key and expected in subs:
                            self._move_child_item(child, subs[expected])

            for eid in list(self._element_floorplan.keys()):
                item = self._tree_items.get(eid)
                if item:
                    anc = self._ancestor_floorplan_id(item)
                    if anc:
                        self._element_floorplan[eid] = anc

            for fur_id in list(self._furniture_parent.keys()):
                item = self._tree_items.get(fur_id)
                if item:
                    anc = self._ancestor_floorplan_id(item)
                    if anc:
                        self._furniture_parent[fur_id] = anc

            self.floorplan_order_changed.emit()
        finally:
            self._loading = False

    # ──────────────────────────────────────────────────────────────── #
    #  Grundrisse (Floor Plans)                                         #
    # ──────────────────────────────────────────────────────────────── #

    def add_floorplan_panel(self, fp_id: str,
                            name: str | None = None) -> FloorPlanPanel:
        existing = self.floorplan_panels.get(fp_id)
        if existing:
            if name:
                existing.le_name.setText(name)
            tree_item = self._tree_items.get(fp_id)
            if tree_item and name:
                tree_item.setText(0, name)
            return existing

        panel = FloorPlanPanel(fp_id, name=name)
        panel.delete_requested.connect(self.delete_floorplan_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda fid, c: (self._sync_tree_checkbox(fid, c),
                            self.floorplan_visibility_changed.emit(fid, c))
        )
        panel.file_browse_requested.connect(self.floorplan_file_browse)
        panel.polygon_draw_requested.connect(self.floorplan_polygon_draw)
        panel.polygon_color_changed.connect(self.floorplan_polygon_color_changed)
        panel.ref_line_requested.connect(self.floorplan_ref_line)
        panel.ref_line_color_changed.connect(self.floorplan_ref_line_color_changed)
        panel.ref_line_visibility_changed.connect(self.floorplan_ref_line_visibility_changed)
        panel.ref_length_confirmed.connect(self.floorplan_ref_confirmed)
        panel.transform_changed.connect(self.floorplan_transform_changed)
        panel.opacity_changed.connect(self.floorplan_opacity_changed)
        panel.move_requested.connect(self.floorplan_move_requested)
        panel.rotate_requested.connect(self.floorplan_rotate_requested)
        panel.move_up_requested.connect(self._move_floorplan_up)
        panel.move_down_requested.connect(self._move_floorplan_down)
        panel.add_furniture_requested.connect(self.add_furniture_requested)
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.floorplan_panels[fp_id] = panel
        # Create top-level floor plan tree item with sub-categories
        fp_item = QTreeWidgetItem(self._tree, [name or fp_id])
        fp_item.setData(0, Qt.UserRole, fp_id)
        fp_item.setFlags(
            fp_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
        )
        fp_item.setCheckState(0, Qt.Checked)
        self._tree_items[fp_id] = fp_item

        hk_item = QTreeWidgetItem(fp_item, ["\U0001f525 Heizkreise"])
        hk_item.setFlags((hk_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        hk_item.setCheckState(0, Qt.Checked)

        hkv_item = QTreeWidgetItem(fp_item, ["Heizkreisverteiler"])
        hkv_item.setFlags((hkv_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        hkv_item.setCheckState(0, Qt.Checked)

        hkv_line_item = QTreeWidgetItem(fp_item, ["HKV-Leitungen"])
        hkv_line_item.setFlags((hkv_line_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        hkv_line_item.setCheckState(0, Qt.Checked)

        ap_item = QTreeWidgetItem(fp_item, ["\u26a1 Anschlusspunkte"])
        ap_item.setFlags((ap_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        ap_item.setCheckState(0, Qt.Checked)

        room_item = QTreeWidgetItem(fp_item, ["🏠 Räume"])
        room_item.setFlags((room_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        room_item.setCheckState(0, Qt.Checked)

        kv_item = QTreeWidgetItem(fp_item, ["Kabelverbindungen"])
        kv_item.setFlags((kv_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        kv_item.setCheckState(0, Qt.Checked)

        text_item = QTreeWidgetItem(fp_item, ["\U0001f4dd Beschriftungen"])
        text_item.setFlags((text_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        text_item.setCheckState(0, Qt.Checked)

        furniture_item = QTreeWidgetItem(fp_item, ["🪑 Einrichtung"])
        furniture_item.setFlags((furniture_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)
        furniture_item.setCheckState(0, Qt.Checked)

        self._fp_sub_items[fp_id] = {
            "hk": hk_item, "hkv": hkv_item, "hkv_line": hkv_line_item,
            "ap": ap_item, "room": room_item, "kv": kv_item, "text": text_item,
            "furniture": furniture_item,
        }
        fp_item.setExpanded(True)
        if not self._loading:
            self._tree.setCurrentItem(fp_item)
        return panel

    def add_furniture_panel(self, fur_id: str, parent_fp_id: str,
                            name: str | None = None) -> "FloorPlanPanel":
        """Create a furniture layer panel as child of the given floor plan."""
        panel = FloorPlanPanel(fur_id, name=name)
        panel.configure_as_furniture()
        panel.delete_requested.connect(self.delete_furniture_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda fid, c: (self._sync_tree_checkbox(fid, c),
                            self.floorplan_visibility_changed.emit(fid, c))
        )
        panel.file_browse_requested.connect(self.floorplan_file_browse)
        panel.polygon_draw_requested.connect(self.floorplan_polygon_draw)
        panel.polygon_color_changed.connect(self.floorplan_polygon_color_changed)
        panel.ref_line_requested.connect(self.floorplan_ref_line)
        panel.ref_line_color_changed.connect(self.floorplan_ref_line_color_changed)
        panel.ref_line_visibility_changed.connect(self.floorplan_ref_line_visibility_changed)
        panel.ref_length_confirmed.connect(self.floorplan_ref_confirmed)
        panel.transform_changed.connect(self.floorplan_transform_changed)
        panel.opacity_changed.connect(self.floorplan_opacity_changed)
        panel.move_requested.connect(self.floorplan_move_requested)
        panel.rotate_requested.connect(self.floorplan_rotate_requested)
        panel.size_changed.connect(self.furniture_size_changed)
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.furniture_panels[fur_id] = panel
        self._furniture_parent[fur_id] = parent_fp_id
        parent_tree_item = self._fp_sub_items.get(parent_fp_id, {}).get("furniture")
        if parent_tree_item is None and self.floorplan_panels:
            first_fp = next(iter(self.floorplan_panels))
            parent_tree_item = self._fp_sub_items.get(first_fp, {}).get("furniture")
        if parent_tree_item:
            self._add_tree_item(parent_tree_item, fur_id, name or fur_id)
        return panel

    def remove_furniture_panel(self, fur_id: str):
        self._remove_tree_item(fur_id)
        self._furniture_parent.pop(fur_id, None)
        panel = self.furniture_panels.pop(fur_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    def remove_floorplan_panel(self, fp_id: str):
        self._fp_sub_items.pop(fp_id, None)
        self._remove_tree_item(fp_id)
        panel = self.floorplan_panels.pop(fp_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    def _move_floorplan_up(self, fp_id: str):
        """Move a floor plan one position up in the tree."""
        item = self._tree_items.get(fp_id)
        if not item:
            return
        idx = self._tree.indexOfTopLevelItem(item)
        if idx <= 0:
            return
        self._tree.takeTopLevelItem(idx)
        self._tree.insertTopLevelItem(idx - 1, item)
        # Restore check state from panel
        panel = self.floorplan_panels.get(fp_id)
        if panel:
            item.setCheckState(0, Qt.Checked if panel.chk_visible.isChecked() else Qt.Unchecked)
        self._tree.setCurrentItem(item)
        self.floorplan_order_changed.emit()

    def _move_floorplan_down(self, fp_id: str):
        """Move a floor plan one position down in the tree."""
        item = self._tree_items.get(fp_id)
        if not item:
            return
        idx = self._tree.indexOfTopLevelItem(item)
        if idx < 0 or idx >= self._tree.topLevelItemCount() - 1:
            return
        self._tree.takeTopLevelItem(idx)
        self._tree.insertTopLevelItem(idx + 1, item)
        # Restore check state from panel
        panel = self.floorplan_panels.get(fp_id)
        if panel:
            item.setCheckState(0, Qt.Checked if panel.chk_visible.isChecked() else Qt.Unchecked)
        self._tree.setCurrentItem(item)
        self.floorplan_order_changed.emit()

    def get_floorplan_order(self) -> list[str]:
        """Return floorplan IDs in tree order (top→bottom = back→front)."""
        order = []
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            fid = item.data(0, Qt.UserRole)
            if fid and fid in self.floorplan_panels:
                order.append(fid)
        return order

    def get_full_render_order(self) -> list[str]:
        """Return all layer IDs (floor plans + furniture) in render order."""
        order = []
        for i in range(self._tree.topLevelItemCount()):
            fp_item = self._tree.topLevelItem(i)
            fp_id = fp_item.data(0, Qt.UserRole)
            if fp_id and fp_id in self.floorplan_panels:
                order.append(fp_id)
                subs = self._fp_sub_items.get(fp_id, {})
                fur_group = subs.get("furniture")
                if fur_group:
                    for j in range(fur_group.childCount()):
                        child = fur_group.child(j)
                        fur_id = child.data(0, Qt.UserRole)
                        if fur_id and fur_id in self.furniture_panels:
                            order.append(fur_id)
        return order

    def get_active_floorplan_id(self) -> str | None:
        """Return the floor plan ID of the currently selected item or its ancestor."""
        current = self._tree.currentItem()
        if not current:
            return next(iter(self.floorplan_panels), None)
        item = current
        while item:
            item_id = item.data(0, Qt.UserRole)
            if item_id and item_id in self.floorplan_panels:
                return item_id
            item = item.parent()
        return next(iter(self.floorplan_panels), None)

    def _resolve_fp_id(self, fp_id: str | None) -> str | None:
        """Return fp_id if valid, otherwise first available floor plan."""
        if fp_id and fp_id in self._fp_sub_items:
            return fp_id
        return next(iter(self.floorplan_panels), None)

    # ──────────────────────────────────────────────────────────────── #
    #  Heizkreise                                                       #
    # ──────────────────────────────────────────────────────────────── #

    def add_circuit_panel(self, circuit_id: str,
                          fp_id: str | None = None,
                          name: str | None = None,
                          color: str | None = None) -> HeatingCircuitPanel:
        panel = HeatingCircuitPanel(circuit_id, name=name, color=color)
        panel.delete_requested.connect(self.delete_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda cid, c: self._sync_tree_checkbox(cid, c)
        )
        # Add to property container (hidden until selected)
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.circuit_panels[circuit_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[circuit_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("hk") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, circuit_id, name or circuit_id)
        return panel

    def remove_circuit_panel(self, circuit_id: str):
        self._remove_tree_item(circuit_id)
        self._element_floorplan.pop(circuit_id, None)
        panel = self.circuit_panels.pop(circuit_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    def get_circuit_params(self, circuit_id: str) -> dict | None:
        panel = self.circuit_panels.get(circuit_id)
        return panel.get_parameters() if panel else None

    def set_circuit_length(self, circuit_id: str, length_mm: float):
        if circuit_id in self.circuit_panels:
            self.circuit_panels[circuit_id].set_length(length_mm)

    def set_circuit_area(self, circuit_id: str, area_mm2: float):
        if circuit_id in self.circuit_panels:
            self.circuit_panels[circuit_id].set_area(area_mm2)

    def set_circuit_perimeter(self, circuit_id: str, perimeter_mm: float):
        if circuit_id in self.circuit_panels:
            self.circuit_panels[circuit_id].set_perimeter(perimeter_mm)

    def set_supply_length(self, circuit_id: str, supply_mm: float):
        if circuit_id in self.circuit_panels:
            self.circuit_panels[circuit_id].set_supply_length(supply_mm)

    def set_total_length(self, circuit_id: str, route_mm: float,
                         supply_mm: float):
        if circuit_id in self.circuit_panels:
            self.circuit_panels[circuit_id].set_total_length(route_mm, supply_mm)

    def update_all_hkv_choices(self):
        """Refresh the HKV dropdown in every circuit panel."""
        names = [p.get_parameters()["name"]
                 for p in self.hkv_panels.values()]
        for panel in self.circuit_panels.values():
            panel.update_hkv_choices(names)

    # ──────────────────────────────────────────────────────────────── #
    #  Elektro: Anschlusspunkte                                         #
    # ──────────────────────────────────────────────────────────────── #

    def add_elec_point_panel(self, point_id: str,
                             fp_id: str | None = None,
                             name: str | None = None,
                             color: str | None = None) -> ElektroPointPanel:
        panel = ElektroPointPanel(point_id, name=name, color=color)
        panel.delete_requested.connect(self.delete_elec_point_requested)
        panel.duplicate_requested.connect(self.duplicate_elec_point_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda pid, c: self._sync_tree_checkbox(pid, c)
        )
        panel.smarthome_device_changed.connect(self._on_elec_point_smarthome_device_changed)
        panel.smarthome_device_color_changed.connect(self._on_elec_point_smarthome_device_color_changed)
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.elec_point_panels[point_id] = panel
        panel.set_smarthome_device_text(self._last_elec_point_device)
        panel.set_smarthome_device_color_text(self._last_elec_point_device_color)
        panel.set_uv_cable_choices(self._collect_elec_cable_names())
        panel.set_up_distribution_cable_choices(self._collect_elec_cable_refs())
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[point_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("ap") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, point_id, name or point_id)
        self.update_all_elec_point_smarthome_choices()
        return panel

    def remove_elec_point_panel(self, point_id: str):
        self._remove_tree_item(point_id)
        self._element_floorplan.pop(point_id, None)
        panel = self.elec_point_panels.pop(point_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self.update_all_elec_point_smarthome_choices()
        self.update_all_elec_point_smarthome_color_choices()
        self._show_placeholder_if_empty()

    def get_elec_point_params(self, point_id: str) -> dict | None:
        panel = self.elec_point_panels.get(point_id)
        return panel.get_parameters() if panel else None

    def _on_elec_point_smarthome_device_changed(self, point_id: str, value: str):
        if self._loading:
            return
        text = (value or "").strip()
        self._last_elec_point_device = text
        self.update_all_elec_point_smarthome_choices()

    def _on_elec_point_smarthome_device_color_changed(self, point_id: str, value: str):
        if self._loading:
            return
        text = (value or "").strip()
        self._last_elec_point_device_color = text
        self.update_all_elec_point_smarthome_color_choices()

    def _collect_elec_point_smarthome_devices(self) -> list[str]:
        values: list[str] = []
        for panel in self.elec_point_panels.values():
            text = panel.get_smarthome_device_text().strip()
            if text and text not in values:
                values.append(text)
        return values

    def update_all_elec_point_smarthome_choices(self):
        choices = self._collect_elec_point_smarthome_devices()
        for panel in self.elec_point_panels.values():
            panel.set_smarthome_device_choices(choices)

    def _collect_elec_point_smarthome_colors(self) -> list[str]:
        values: list[str] = []
        for panel in self.elec_point_panels.values():
            text = panel.get_smarthome_device_color_text().strip()
            if text and text not in values:
                values.append(text)
        return values

    def update_all_elec_point_smarthome_color_choices(self):
        choices = self._collect_elec_point_smarthome_colors()
        for panel in self.elec_point_panels.values():
            panel.set_smarthome_device_color_choices(choices)

    def _collect_elec_cable_names(self) -> list[str]:
        values: list[str] = []
        for cable_id, panel in self.elec_cable_panels.items():
            text = str(panel.get_parameters().get("name", cable_id) or "").strip() or cable_id
            if text and text not in values:
                values.append(text)
        return values

    def _collect_elec_cable_refs(self) -> list[dict]:
        values: list[dict] = []
        for cable_id, panel in self.elec_cable_panels.items():
            name = str(panel.get_parameters().get("name", cable_id) or "").strip() or cable_id
            values.append({
                "cable_id": cable_id,
                "name": name,
            })
        return values

    def update_all_elec_point_uv_cable_choices(self):
        choices = self._collect_elec_cable_names()
        refs = self._collect_elec_cable_refs()
        for panel in self.elec_point_panels.values():
            panel.set_uv_cable_choices(choices)
            panel.set_up_distribution_cable_choices(refs)

    # ──────────────────────────────────────────────────────────────── #
    #  Elektro: Räume                                                  #
    # ──────────────────────────────────────────────────────────────── #

    def add_elec_room_panel(self, room_id: str,
                            fp_id: str | None = None,
                            name: str | None = None,
                            color: str | None = None) -> ElektroRoomPanel:
        panel = ElektroRoomPanel(room_id, name=name, color=color)
        panel.delete_requested.connect(self.delete_elec_room_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda rid, c: self._sync_tree_checkbox(rid, c)
        )
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.elec_room_panels[room_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[room_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("room") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, room_id, name or room_id)
        return panel

    def remove_elec_room_panel(self, room_id: str):
        self._remove_tree_item(room_id)
        self._element_floorplan.pop(room_id, None)
        panel = self.elec_room_panels.pop(room_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    def get_elec_room_params(self, room_id: str) -> dict | None:
        panel = self.elec_room_panels.get(room_id)
        return panel.get_parameters() if panel else None

    # ──────────────────────────────────────────────────────────────── #
    #  Elektro: Kabelverbindungen                                       #
    # ──────────────────────────────────────────────────────────────── #

    def add_elec_cable_panel(self, cable_id: str,
                             fp_id: str | None = None,
                             name: str | None = None,
                             color: str | None = None) -> ElektroCablePanel:
        effective_name = name or self._last_elec_cable_defaults.get("name") or cable_id
        panel = ElektroCablePanel(
            cable_id,
            name=effective_name,
            color=color,
            defaults=self._last_elec_cable_defaults,
        )
        panel.delete_requested.connect(self.delete_elec_cable_requested)
        panel.duplicate_requested.connect(self.duplicate_elec_cable_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.name_changed.connect(self._on_elec_cable_name_changed)
        panel.type_changed.connect(self._on_elec_cable_type_changed)
        panel.comment_changed.connect(self._on_elec_cable_comment_changed)
        panel.visibility_changed.connect(
            lambda cid, c: self._sync_tree_checkbox(cid, c)
        )
        panel.visibility_changed.connect(self._on_elec_cable_visibility_changed)
        panel.color_changed.connect(self._on_elec_cable_color_changed)
        panel.label_size_changed.connect(self._on_elec_cable_label_size_changed)
        panel.label_visibility_changed.connect(self._on_elec_cable_label_visibility_changed)
        panel.type_label_visibility_changed.connect(
            self._on_elec_cable_type_label_visibility_changed
        )
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.elec_cable_panels[cable_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[cable_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("kv") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, cable_id, effective_name)
        self.update_all_elec_cable_type_choices()
        self.update_all_elec_point_uv_cable_choices()
        return panel

    def remove_elec_cable_panel(self, cable_id: str):
        self._remove_tree_item(cable_id)
        self._element_floorplan.pop(cable_id, None)
        panel = self.elec_cable_panels.pop(cable_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self.update_all_elec_cable_type_choices()
        self.update_all_elec_point_uv_cable_choices()
        self._show_placeholder_if_empty()

    def _on_elec_cable_type_changed(self, cable_id: str, value: str):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)
        self.update_all_elec_cable_type_choices()

    def _on_elec_cable_name_changed(self, cable_id: str, value: str):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)
        self.update_all_elec_point_uv_cable_choices()

    def _on_elec_cable_comment_changed(self, cable_id: str, value: str):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)

    def _on_elec_cable_visibility_changed(self, cable_id: str, value: bool):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)

    def _on_elec_cable_color_changed(self, cable_id: str, value: str):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)

    def _on_elec_cable_label_size_changed(self, cable_id: str, value: float):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)

    def _on_elec_cable_label_visibility_changed(self, cable_id: str, value: bool):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)

    def _on_elec_cable_type_label_visibility_changed(self, cable_id: str, value: bool):
        if self._loading:
            return
        self._update_last_elec_cable_defaults(cable_id)

    def _default_elec_cable_defaults(self) -> dict:
        return {
            "name": "",
            "color": "#ff9800",
            "type": ElektroCablePanel.DEFAULT_CABLE_TYPE,
            "comment": "",
            "visible": True,
            "label_visible": True,
            "type_label_visible": False,
            "label_size": 12.0,
        }

    def _sanitize_elec_cable_defaults(self, defaults: dict | None) -> dict:
        sanitized = self._default_elec_cable_defaults()
        if not defaults:
            return sanitized
        if defaults.get("name"):
            sanitized["name"] = str(defaults.get("name"))
        if defaults.get("color"):
            sanitized["color"] = str(defaults.get("color"))
        if defaults.get("type"):
            sanitized["type"] = str(defaults.get("type"))
        if "comment" in defaults:
            sanitized["comment"] = str(defaults.get("comment", ""))
        if "visible" in defaults:
            sanitized["visible"] = bool(defaults.get("visible", True))
        if "label_visible" in defaults:
            sanitized["label_visible"] = bool(defaults.get("label_visible", True))
        if "type_label_visible" in defaults:
            sanitized["type_label_visible"] = bool(
                defaults.get("type_label_visible", False)
            )
        if "label_size" in defaults:
            try:
                sanitized["label_size"] = float(defaults.get("label_size", 12.0))
            except (TypeError, ValueError):
                pass
        return sanitized

    def _update_last_elec_cable_defaults(self, cable_id: str):
        panel = self.elec_cable_panels.get(cable_id)
        if not panel:
            return
        params = panel.get_parameters()
        self._last_elec_cable_defaults = self._sanitize_elec_cable_defaults({
            "name": params.get("name", ""),
            "color": params.get("color", "#ff9800"),
            "type": params.get("type", ElektroCablePanel.DEFAULT_CABLE_TYPE),
            "comment": params.get("comment", ""),
            "visible": params.get("visible", True),
            "label_visible": params.get("label_visible", True),
            "type_label_visible": params.get("type_label_visible", False),
            "label_size": params.get("label_size", 12.0),
        })

    def _collect_elec_cable_types(self) -> list[str]:
        cable_types: list[str] = []
        for panel in self.elec_cable_panels.values():
            text = panel.get_type_text().strip()
            if text and text not in cable_types:
                cable_types.append(text)
        if ElektroCablePanel.DEFAULT_CABLE_TYPE not in cable_types:
            cable_types.insert(0, ElektroCablePanel.DEFAULT_CABLE_TYPE)
        return cable_types

    def update_all_elec_cable_type_choices(self):
        choices = self._collect_elec_cable_types()
        for panel in self.elec_cable_panels.values():
            panel.set_type_choices(choices)

    def set_cable_length(self, cable_id: str, length_mm: float):
        if cable_id in self.elec_cable_panels:
            self.elec_cable_panels[cable_id].set_length(length_mm)

    # ── HKV panels ──

    def add_hkv_panel(self, hkv_id: str,
                      fp_id: str | None = None,
                      name: str | None = None,
                      color: str | None = None) -> HkvPanel:
        panel = HkvPanel(hkv_id, name=name, color=color)
        panel.delete_requested.connect(self.delete_hkv_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda hid, c: self._sync_tree_checkbox(hid, c)
        )
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.hkv_panels[hkv_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[hkv_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("hkv") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, hkv_id, name or hkv_id)
        return panel

    def remove_hkv_panel(self, hkv_id: str):
        self._remove_tree_item(hkv_id)
        self._element_floorplan.pop(hkv_id, None)
        panel = self.hkv_panels.pop(hkv_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    def get_hkv_params(self, hkv_id: str) -> dict | None:
        panel = self.hkv_panels.get(hkv_id)
        return panel.get_parameters() if panel else None

    def add_hkv_line_panel(self, line_id: str,
                           fp_id: str | None = None,
                           name: str | None = None,
                           color: str | None = None) -> HkvLinePanel:
        panel = HkvLinePanel(line_id, name=name, color=color)
        panel.delete_requested.connect(self.delete_hkv_line_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda lid, c: self._sync_tree_checkbox(lid, c)
        )
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.hkv_line_panels[line_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[line_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("hkv_line") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, line_id, name or line_id)
        return panel

    def remove_hkv_line_panel(self, line_id: str):
        self._remove_tree_item(line_id)
        self._element_floorplan.pop(line_id, None)
        panel = self.hkv_line_panels.pop(line_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    def set_hkv_line_length(self, line_id: str, length_mm: float):
        if line_id in self.hkv_line_panels:
            self.hkv_line_panels[line_id].set_length(length_mm)

    # ──────────────────────────────────────────────────────────────── #
    #  Text Annotations                                                 #
    # ──────────────────────────────────────────────────────────────── #

    def add_text_panel(self, text_id: str,
                       fp_id: str | None = None,
                       name: str | None = None,
                       color: str | None = None) -> TextAnnotationPanel:
        panel = TextAnnotationPanel(text_id, name=name, color=color)
        panel.delete_requested.connect(self.delete_text_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda tid, c: self._sync_tree_checkbox(tid, c)
        )
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.text_panels[text_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[text_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("text") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, text_id, name or text_id)
        return panel

    def remove_text_panel(self, text_id: str):
        self._remove_tree_item(text_id)
        self._element_floorplan.pop(text_id, None)
        panel = self.text_panels.pop(text_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    # ──────────────────────────────────────────────────────────────── #
    #  Helpers                                                          #
    # ──────────────────────────────────────────────────────────────── #

    def _show_placeholder_if_empty(self):
        """Show the 'select an item' label when no panel is visible."""
        if self._active_panel and self._active_panel.parent() is None:
            self._active_panel = None
        if not any(p.isVisible() for p in
                   list(self.floorplan_panels.values()) +
                   list(self.furniture_panels.values()) +
                   list(self.circuit_panels.values()) +
                   list(self.elec_point_panels.values()) +
                 list(self.elec_room_panels.values()) +
                   list(self.elec_cable_panels.values()) +
                   list(self.hkv_panels.values()) +
                   list(self.hkv_line_panels.values()) +
                   list(self.text_panels.values())):
            self._set_active_special("empty")

    def _dedupe_floorplan_tree(self):
        """Remove duplicate top-level floorplan entries from the tree."""
        seen: dict[str, QTreeWidgetItem] = {}
        remove_indices: list[int] = []

        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            fp_id = item.data(0, Qt.UserRole)
            if not fp_id or fp_id not in self.floorplan_panels:
                continue
            if fp_id in seen:
                remove_indices.append(i)
                continue
            seen[fp_id] = item
            self._tree_items[fp_id] = item

        for i in reversed(remove_indices):
            self._tree.takeTopLevelItem(i)

    # ──────────────────────────────────────────────────────────────── #
    #  General heating params                                           #
    # ──────────────────────────────────────────────────────────────── #

    def get_heating_params(self) -> dict:
        return {
            "t_supply": self.sb_vorlauf.value(),
            "t_return": self.sb_ruecklauf.value(),
            "t_norm_outdoor": self.sb_norm_aussen.value(),
        }

    def _open_bom_editor(self):
        dlg = BomMetadataDialog(self._bom_metadata, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._bom_metadata = self._sanitize_bom_metadata(dlg.get_metadata())
        if not self._loading:
            self.bom_metadata_changed.emit()

    def _default_bom_metadata(self) -> dict:
        return {
            "version": 1,
            "sections": {
                "hk_bom_rows": True,
                "cable_bom_rows": True,
                "ap_bom_rows": True,
                "hkv_line_bom_rows": True,
                "uv_device_bom_rows": True,
                "uv_busbar_bom_rows": True,
                "custom_bom_rows": True,
            },
            "rounding": {
                "length_m": 1,
                "quantity": 1,
            },
            "last_generated_at": "",
            "item_catalog": {},
            "custom_items": [],
        }

    def _sanitize_bom_metadata(self, value) -> dict:
        default = self._default_bom_metadata()
        if not isinstance(value, dict):
            return default

        sections = dict(default["sections"])
        for key, section_value in value.get("sections", {}).items():
            sections[str(key)] = bool(section_value)

        rounding = dict(default["rounding"])
        for key, rounding_value in value.get("rounding", {}).items():
            try:
                rounding[str(key)] = max(0, min(6, int(rounding_value)))
            except Exception:
                continue

        version = value.get("version", default["version"])
        try:
            version_int = max(1, int(version))
        except Exception:
            version_int = default["version"]

        last_generated_at = str(value.get("last_generated_at", "") or "").strip()

        item_catalog: dict[str, dict] = {}
        raw_catalog = value.get("item_catalog", {})
        if isinstance(raw_catalog, dict):
            for raw_key, raw_value in raw_catalog.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                rv = raw_value if isinstance(raw_value, dict) else {}
                item_catalog[key] = {
                    "manufacturer": str(rv.get("manufacturer", "") or "").strip(),
                    "article_number": str(rv.get("article_number", "") or "").strip(),
                    "description_override": str(rv.get("description_override", "") or "").strip(),
                    "note": str(rv.get("note", "") or "").strip(),
                }

        custom_items: list[dict] = []
        raw_custom_items = value.get("custom_items", [])
        if isinstance(raw_custom_items, list):
            for index, raw_item in enumerate(raw_custom_items, start=1):
                if not isinstance(raw_item, dict):
                    continue
                custom_id = str(raw_item.get("custom_id", "") or "").strip() or f"BOM-{index}"
                section_key = str(raw_item.get("section_key", "custom_bom_rows") or "custom_bom_rows").strip()
                category = str(raw_item.get("category", "Manuell") or "Manuell").strip()
                item_type = str(raw_item.get("item_type", "custom") or "custom").strip()
                item_key = str(raw_item.get("key", custom_id) or custom_id).strip()
                description = str(raw_item.get("description", "") or "").strip()
                unit = str(raw_item.get("unit", "Stk") or "Stk").strip()
                try:
                    quantity = float(raw_item.get("quantity", 0.0) or 0.0)
                except Exception:
                    quantity = 0.0
                custom_items.append({
                    "custom_id": custom_id,
                    "section_key": section_key,
                    "category": category,
                    "item_type": item_type,
                    "key": item_key,
                    "description": description,
                    "unit": unit,
                    "quantity": quantity,
                    "manufacturer": str(raw_item.get("manufacturer", "") or "").strip(),
                    "article_number": str(raw_item.get("article_number", "") or "").strip(),
                    "note": str(raw_item.get("note", "") or "").strip(),
                })

        return {
            "version": version_int,
            "sections": sections,
            "rounding": rounding,
            "last_generated_at": last_generated_at,
            "item_catalog": item_catalog,
            "custom_items": custom_items,
        }

    def get_bom_metadata(self) -> dict:
        return copy.deepcopy(self._bom_metadata)

    def set_bom_metadata(self, metadata: dict | None):
        self._bom_metadata = self._sanitize_bom_metadata(metadata)

    # ──────────────────────────────────────────────────────────────── #
    #  Serialization                                                    #
    # ──────────────────────────────────────────────────────────────── #

    def clear_all_panels(self):
        """Remove all object panels (circuits, elec, HKV, floorplans) from the tree + layout."""
        self._last_elec_cable_defaults = self._default_elec_cable_defaults()
        self._bom_metadata = self._default_bom_metadata()
        # Remove children first (they live under floorplan tree items)
        for tid in list(self.text_panels):
            self.remove_text_panel(tid)
        for lid in list(self.hkv_line_panels):
            self.remove_hkv_line_panel(lid)
        for hid in list(self.hkv_panels):
            self.remove_hkv_panel(hid)
        for cid in list(self.elec_cable_panels):
            self.remove_elec_cable_panel(cid)
        for rid in list(self.elec_room_panels):
            self.remove_elec_room_panel(rid)
        for pid in list(self.elec_point_panels):
            self.remove_elec_point_panel(pid)
        for cid in list(self.circuit_panels):
            self.remove_circuit_panel(cid)
        # Remove furniture and floorplans last (they are parents)
        for fur_id in list(self.furniture_panels):
            self.remove_furniture_panel(fur_id)
        for fid in list(self.floorplan_panels):
            self.remove_floorplan_panel(fid)

    def to_dict(self) -> dict:
        return {
            "t_supply": self.sb_vorlauf.value(),
            "t_return": self.sb_ruecklauf.value(),
            "t_norm_outdoor": self.sb_norm_aussen.value(),
            "elec_cable_defaults": dict(self._last_elec_cable_defaults),
            "bom": copy.deepcopy(self._bom_metadata),
            "floorplans_order": self.get_floorplan_order(),
            "floorplans": {
                fid: p.to_dict() for fid, p in self.floorplan_panels.items()
            },
            "furniture": {
                fur_id: {**p.to_dict(),
                         "parent_fp_id": self._furniture_parent.get(fur_id, "")}
                for fur_id, p in self.furniture_panels.items()
            },
            "circuits": {
                cid: {**p.to_dict(), "floor_plan_id": self._element_floorplan.get(cid, "")}
                for cid, p in self.circuit_panels.items()
            },
            "elec_points": {
                pid: {**p.to_dict(), "floor_plan_id": self._element_floorplan.get(pid, "")}
                for pid, p in self.elec_point_panels.items()
            },
            "elec_rooms": {
                rid: {**p.to_dict(), "floor_plan_id": self._element_floorplan.get(rid, "")}
                for rid, p in self.elec_room_panels.items()
            },
            "elec_cables": {
                cid: {**p.to_dict(), "floor_plan_id": self._element_floorplan.get(cid, "")}
                for cid, p in self.elec_cable_panels.items()
            },
            "hkv_points": {
                hid: {**p.to_dict(), "floor_plan_id": self._element_floorplan.get(hid, "")}
                for hid, p in self.hkv_panels.items()
            },
            "hkv_lines": {
                lid: {**p.to_dict(), "floor_plan_id": self._element_floorplan.get(lid, "")}
                for lid, p in self.hkv_line_panels.items()
            },
            "text_annotations": {
                tid: {**p.get_parameters(), "floor_plan_id": self._element_floorplan.get(tid, "")}
                for tid, p in self.text_panels.items()
            },
        }

    def update_panels_from_dict(self, d: dict):
        """Incrementally restore panels from a snapshot dict.

        Unlike from_dict() which clears everything and rebuilds all QWidgets,
        this reuses existing panels where IDs match and only creates/removes
        panels for IDs that actually changed.  Typical undo/redo operations
        (no structural ID change) run ~100x faster than the clear+rebuild path.
        """
        self._loading = True
        self._tree.setUpdatesEnabled(False)
        try:
            # Global heating params (blockSignals to avoid triggering recalc)
            for sb, key, default in [
                (self.sb_vorlauf, "t_supply", 35.0),
                (self.sb_ruecklauf, "t_return", 30.0),
                (self.sb_norm_aussen, "t_norm_outdoor", -12.0),
            ]:
                sb.blockSignals(True)
                sb.setValue(d.get(key, default))
                sb.blockSignals(False)
            self._last_elec_cable_defaults = self._sanitize_elec_cable_defaults(
                d.get("elec_cable_defaults")
            )
            self._bom_metadata = self._sanitize_bom_metadata(d.get("bom"))

            # ---- Floor plans (preserve tree order) ------------------
            fp_order_new = d.get("floorplans_order", [])
            fp_data = d.get("floorplans", {})

            # Remove deleted floor plans (furniture children first)
            for fid in set(self.floorplan_panels.keys()) - set(fp_order_new):
                for fur_id in [k for k, v in list(self._furniture_parent.items()) if v == fid]:
                    self.remove_furniture_panel(fur_id)
                self.remove_floorplan_panel(fid)

            for fid in fp_order_new:
                values = fp_data.get(fid, {})
                if fid not in self.floorplan_panels:
                    panel = self.add_floorplan_panel(fid, name=values.get("name", fid))
                    panel.blockSignals(True)
                    panel.from_dict(values)
                    panel.blockSignals(False)
                else:
                    panel = self.floorplan_panels[fid]
                    panel.blockSignals(True)
                    panel.from_dict(values)
                    panel.blockSignals(False)
                    ti = self._tree_items.get(fid)
                    if ti:
                        ti.setText(0, values.get("name", fid))

            # Reorder tree to match fp_order_new if necessary
            current_order = self.get_floorplan_order()
            if current_order != fp_order_new:
                for i, fid in enumerate(fp_order_new):
                    item = self._tree_items.get(fid)
                    if not item:
                        continue
                    idx = self._tree.indexOfTopLevelItem(item)
                    if idx >= 0 and idx != i:
                        self._tree.takeTopLevelItem(idx)
                        self._tree.insertTopLevelItem(i, item)

            # ---- Furniture ------------------------------------------
            furniture_data = d.get("furniture", {})
            for fur_id in set(self.furniture_panels.keys()) - set(furniture_data.keys()):
                self.remove_furniture_panel(fur_id)
            for fur_id, values in furniture_data.items():
                parent_fp_id = values.get("parent_fp_id", "")
                if fur_id not in self.furniture_panels:
                    panel = self.add_furniture_panel(
                        fur_id, parent_fp_id=parent_fp_id, name=values.get("name", fur_id)
                    )
                    panel.blockSignals(True)
                    panel.from_dict(values)
                    panel.blockSignals(False)
                else:
                    panel = self.furniture_panels[fur_id]
                    panel.blockSignals(True)
                    panel.from_dict(values)
                    panel.blockSignals(False)
                    ti = self._tree_items.get(fur_id)
                    if ti:
                        ti.setText(0, values.get("name", fur_id))

            # ---- Generic diff-update helper -------------------------
            def _diff(existing, data_dict, add_fn, remove_fn):
                for eid in set(existing.keys()) - set(data_dict.keys()):
                    remove_fn(eid)
                for eid, values in data_dict.items():
                    if eid not in existing:
                        panel = add_fn(
                            eid,
                            fp_id=values.get("floor_plan_id"),
                            name=values.get("name", eid),
                            color=values.get("color"),
                        )
                        panel.blockSignals(True)
                        panel.from_dict(values)
                        panel.blockSignals(False)
                    else:
                        panel = existing[eid]
                        panel.blockSignals(True)
                        panel.from_dict(values)
                        panel.blockSignals(False)
                        new_fp = values.get("floor_plan_id", "")
                        if new_fp:
                            self._element_floorplan[eid] = new_fp
                        ti = self._tree_items.get(eid)
                        if ti:
                            ti.setText(0, values.get("name", eid))

            # HKV first (needed for circuit distributor dropdown)
            _diff(self.hkv_panels, d.get("hkv_points", {}), self.add_hkv_panel, self.remove_hkv_panel)
            self.update_all_hkv_choices()

            _diff(self.circuit_panels, d.get("circuits", {}), self.add_circuit_panel, self.remove_circuit_panel)
            _diff(self.elec_point_panels, d.get("elec_points", {}), self.add_elec_point_panel, self.remove_elec_point_panel)
            _diff(self.elec_room_panels, d.get("elec_rooms", {}), self.add_elec_room_panel, self.remove_elec_room_panel)
            _diff(self.elec_cable_panels, d.get("elec_cables", {}), self.add_elec_cable_panel, self.remove_elec_cable_panel)
            _diff(self.hkv_line_panels, d.get("hkv_lines", {}), self.add_hkv_line_panel, self.remove_hkv_line_panel)

            # Text panels use set_parameters instead of from_dict
            text_data = d.get("text_annotations", {})
            for tid in set(self.text_panels.keys()) - set(text_data.keys()):
                self.remove_text_panel(tid)
            for tid, values in text_data.items():
                if tid not in self.text_panels:
                    panel = self.add_text_panel(
                        tid,
                        fp_id=values.get("floor_plan_id"),
                        name=values.get("name", tid),
                        color=values.get("color", "#ffffff"),
                    )
                    panel.blockSignals(True)
                    panel.set_parameters(values)
                    panel.blockSignals(False)
                else:
                    panel = self.text_panels[tid]
                    panel.blockSignals(True)
                    panel.set_parameters(values)
                    panel.blockSignals(False)
                    ti = self._tree_items.get(tid)
                    if ti:
                        ti.setText(0, values.get("name", tid))

            # Post-restore choices sync
            self.update_all_elec_point_smarthome_choices()
            self.update_all_elec_point_smarthome_color_choices()
            self.update_all_elec_cable_type_choices()
            self.update_all_elec_point_uv_cable_choices()
            self._dedupe_floorplan_tree()
        finally:
            self._tree.setUpdatesEnabled(True)
            self._loading = False

    def from_dict(self, d: dict):
        self._loading = True

        self.sb_vorlauf.setValue(d.get("t_supply", 35.0))
        self.sb_ruecklauf.setValue(d.get("t_return", 30.0))
        self.sb_norm_aussen.setValue(d.get("t_norm_outdoor", -12.0))
        self._last_elec_cable_defaults = self._sanitize_elec_cable_defaults(
            d.get("elec_cable_defaults")
        )
        self._bom_metadata = self._sanitize_bom_metadata(d.get("bom"))

        # Floorplans (in saved order)
        fp_order = d.get("floorplans_order", [])
        fp_data = d.get("floorplans", {})
        for fid in fp_order:
            values = fp_data.get(fid, {})
            panel = self.add_floorplan_panel(
                fid, name=values.get("name", fid)
            )
            panel.from_dict(values)
        # Legacy: old single-floor projects
        if not fp_order and "ref_length_mm" in d:
            panel = self.add_floorplan_panel("grundriss-1", name="Grundriss 1")
            panel.sb_ref_length.setValue(d["ref_length_mm"] / 1000.0)

        # Einrichtungsgegenstände
        for fur_id, values in d.get("furniture", {}).items():
            parent_fp_id = values.get("parent_fp_id", "")
            panel = self.add_furniture_panel(
                fur_id, parent_fp_id=parent_fp_id,
                name=values.get("name", fur_id),
            )
            panel.from_dict(values)

        # HKV-Punkte VOR Heizkreisen laden (für Verteiler-Dropdown)
        for hid, values in d.get("hkv_points", {}).items():
            panel = self.add_hkv_panel(
                hid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", hid),
                color=values.get("color", "#e53935"),
            )
            panel.from_dict(values)

        for cid, values in d.get("circuits", {}).items():
            panel = self.add_circuit_panel(
                cid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", cid),
                color=values.get("color", "#2a9d8f"),
            )
            # Populate HKV choices before restoring distributor
            self.update_all_hkv_choices()
            panel.from_dict(values)
        for pid, values in d.get("elec_points", {}).items():
            panel = self.add_elec_point_panel(
                pid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", pid),
                color=values.get("color", "#4fc3f7"),
            )
            panel.from_dict(values)
        self.update_all_elec_point_smarthome_choices()
        self.update_all_elec_point_smarthome_color_choices()
        if self.elec_point_panels:
            last_point_id = next(reversed(self.elec_point_panels))
            last_params = self.elec_point_panels[last_point_id].get_parameters()
            self._last_elec_point_device = str(last_params.get("smarthome_device", "")).strip()
            self._last_elec_point_device_color = str(last_params.get("smarthome_device_color", "")).strip()
        for rid, values in d.get("elec_rooms", {}).items():
            panel = self.add_elec_room_panel(
                rid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", rid),
                color=values.get("color", "#43aa8b"),
            )
            panel.from_dict(values)
        for cid, values in d.get("elec_cables", {}).items():
            panel = self.add_elec_cable_panel(
                cid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", cid),
                color=values.get("color", "#ff9800"),
            )
            panel.from_dict(values)
        self.update_all_elec_cable_type_choices()
        if not d.get("elec_cable_defaults") and self.elec_cable_panels:
            last_cable_id = next(reversed(self.elec_cable_panels))
            self._update_last_elec_cable_defaults(last_cable_id)
        self.update_all_elec_point_uv_cable_choices()
        for lid, values in d.get("hkv_lines", {}).items():
            panel = self.add_hkv_line_panel(
                lid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", lid),
                color=values.get("color", "#e53935"),
            )
            panel.from_dict(values)

        for tid, values in d.get("text_annotations", {}).items():
            panel = self.add_text_panel(
                tid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", tid),
                color=values.get("color", "#ffffff"),
            )
            panel.set_parameters(values)

        self._dedupe_floorplan_tree()

        self._loading = False
