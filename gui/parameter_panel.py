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

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel,
    QLineEdit, QDoubleSpinBox, QPushButton, QGroupBox,
    QScrollArea, QHBoxLayout, QFrame, QColorDialog,
    QCheckBox, QFileDialog, QTextEdit, QComboBox,
    QTreeWidget, QTreeWidgetItem, QSplitter,
)
from pathlib import Path
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtCore import Signal, Qt

# ── Eingebaute Elektro-Symbole (DIN EN 60617) ─────────────────── #
_SYMBOLS_DIR = Path(__file__).resolve().parent.parent / "assets" / "symbols"
# Fallback für PyInstaller
import sys as _sys
if hasattr(_sys, '_MEIPASS'):
    _SYMBOLS_DIR = Path(_sys._MEIPASS) / "assets" / "symbols"

BUILTIN_SYMBOLS: dict[str, str] = {
    "(kein Symbol)": "",
    "Steckdose":        str(_SYMBOLS_DIR / "steckdose.svg"),
    "Doppelsteckdose":  str(_SYMBOLS_DIR / "doppelsteckdose.svg"),
    "Leuchte":          str(_SYMBOLS_DIR / "leuchte.svg"),
    "Ausschalter":      str(_SYMBOLS_DIR / "ausschalter.svg"),
    "Wechselschalter":  str(_SYMBOLS_DIR / "wechselschalter.svg"),
    "Serienschalter":   str(_SYMBOLS_DIR / "serienschalter.svg"),
    "Kreuzschalter":    str(_SYMBOLS_DIR / "kreuzschalter.svg"),
    "Taster":           str(_SYMBOLS_DIR / "taster.svg"),
}


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

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda value: self.name_changed.emit(self.circuit_id, value)
        )
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.sb_diameter = QDoubleSpinBox()
        self.sb_diameter.setRange(1.0, 3.2)
        self.sb_diameter.setSingleStep(0.05)
        self.sb_diameter.setValue(1.6)
        self.sb_diameter.setDecimals(2)
        self.sb_diameter.setSuffix(" cm")
        form.addRow("Rohrdurchmesser:", self.sb_diameter)

        self.sb_spacing = QDoubleSpinBox()
        self.sb_spacing.setRange(5.0, 30.0)
        self.sb_spacing.setSingleStep(0.5)
        self.sb_spacing.setValue(15.0)
        self.sb_spacing.setSuffix(" cm")
        self.sb_spacing.valueChanged.connect(
            lambda _: self.spacing_changed.emit(self.circuit_id)
        )
        form.addRow("Verlegeabstand:", self.sb_spacing)

        self.sb_wall_dist = QDoubleSpinBox()
        self.sb_wall_dist.setRange(0.0, 50.0)
        self.sb_wall_dist.setSingleStep(0.5)
        self.sb_wall_dist.setValue(20.0)
        self.sb_wall_dist.setSuffix(" cm")
        self.sb_wall_dist.valueChanged.connect(
            lambda _: self.wall_dist_changed.emit(self.circuit_id)
        )
        form.addRow("Randabstand:", self.sb_wall_dist)

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(4.0, 80.0)
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

        self.sb_room_temp = QDoubleSpinBox()
        self.sb_room_temp.setRange(10.0, 35.0)
        self.sb_room_temp.setSingleStep(0.5)
        self.sb_room_temp.setValue(20.0)
        self.sb_room_temp.setDecimals(1)
        self.sb_room_temp.setSuffix(" \u00b0C")
        self.sb_room_temp.valueChanged.connect(
            lambda _: self.hydraulics_param_changed.emit(self.circuit_id)
        )
        form.addRow("Soll-Raumtemp.:", self.sb_room_temp)

        from logic.heating_calc import FLOOR_COVERINGS
        self.cb_floor_covering = QComboBox()
        for name in FLOOR_COVERINGS:
            self.cb_floor_covering.addItem(name)
        self.cb_floor_covering.setCurrentText("Fliesen / Keramik")
        self.cb_floor_covering.currentIndexChanged.connect(
            lambda _: self.hydraulics_param_changed.emit(self.circuit_id)
        )
        form.addRow("Fu\u00dfbodenbelag:", self.cb_floor_covering)

        self.cb_distributor = QComboBox()
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
    duplicate_requested = Signal(str)

    def __init__(self, point_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.point_id = point_id
        self._name = name or point_id
        self._icon_path: str | None = None
        self._color = QColor(color or "#4fc3f7")
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

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.point_id, v)
        )
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.sb_width = QDoubleSpinBox()
        self.sb_width.setRange(0.5, 20.0)
        self.sb_width.setSingleStep(0.5)
        self.sb_width.setValue(3.0)
        self.sb_width.setSuffix(" cm")
        self.sb_width.valueChanged.connect(lambda _: self.size_changed.emit(self.point_id))
        form.addRow("Breite:", self.sb_width)

        self.sb_height = QDoubleSpinBox()
        self.sb_height.setRange(0.5, 20.0)
        self.sb_height.setSingleStep(0.5)
        self.sb_height.setValue(3.0)
        self.sb_height.setSuffix(" cm")
        self.sb_height.valueChanged.connect(lambda _: self.size_changed.emit(self.point_id))
        form.addRow("H\u00f6he:", self.sb_height)

        self.cmb_symbol = QComboBox()
        for label in BUILTIN_SYMBOLS:
            self.cmb_symbol.addItem(label)
        self.cmb_symbol.currentTextChanged.connect(self._on_symbol_selected)
        form.addRow("Symbol:", self.cmb_symbol)

        self.btn_icon = QPushButton("Eigenes Bild…")
        self.btn_icon.clicked.connect(self._load_icon)
        form.addRow("", self.btn_icon)

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(4.0, 80.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(12.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.point_id, v)
        )
        form.addRow("Schriftgr\u00f6\u00dfe:", self.sb_label_size)

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

        layout.addLayout(form)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "Anschlusspunkt-Farbe")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self.color_changed.emit(self.point_id, self._color.name())

    def _update_color_button(self):
        self.btn_color.setStyleSheet(f"background:{self._color.name()}; color:white;")

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

    def get_parameters(self) -> dict:
        return {
            "name":      self.le_name.text().strip() or self.point_id,
            "color":     self._color.name(),
            "width":     self.sb_width.value() * 10,
            "height":    self.sb_height.value() * 10,
            "icon_path": self._icon_path or "",
            "builtin_symbol": self.cmb_symbol.currentText(),
            "visible":   self.chk_visible.isChecked(),
            "label_size": self.sb_label_size.value(),
        }

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["point_id"] = self.point_id
        return d

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.point_id))
        c = d.get("color", self._color.name())
        self._color = QColor(c)
        self._update_color_button()
        self.sb_width.setValue(d.get("width", 30.0) / 10)
        self.sb_height.setValue(d.get("height", 30.0) / 10)
        self.chk_visible.setChecked(d.get("visible", True))
        self.sb_label_size.setValue(d.get("label_size", 12.0))
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
#  Elektro: Kabelverbindung Panel                                      #
# ================================================================== #

class ElektroCablePanel(QWidget):
    delete_requested     = Signal(str)
    name_changed         = Signal(str, str)
    color_changed        = Signal(str, str)
    draw_cable_requested = Signal(str)
    edit_cable_requested = Signal(str)
    visibility_changed   = Signal(str, bool)
    label_size_changed   = Signal(str, float)
    duplicate_requested  = Signal(str)

    def __init__(self, cable_id: str, name: str | None = None,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.cable_id = cable_id
        self._name = name or cable_id
        self._color = QColor(color or "#ff9800")
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
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(
            lambda c: self.visibility_changed.emit(self.cable_id, c)
        )
        form.addRow(self.chk_visible)

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.cable_id, v)
        )
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.le_type = QLineEdit("5x1,5")
        form.addRow("Typ:", self.le_type)

        self.te_comment = QTextEdit()
        self.te_comment.setMaximumHeight(50)
        self.te_comment.setPlaceholderText("Kommentar...")
        form.addRow("Kommentar:", self.te_comment)

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(4.0, 80.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setValue(12.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.valueChanged.connect(
            lambda v: self.label_size_changed.emit(self.cable_id, v)
        )
        form.addRow("Schriftgr\u00f6\u00dfe:", self.sb_label_size)

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
            "type":    self.le_type.text().strip(),
            "comment": self.te_comment.toPlainText(),
            "visible": self.chk_visible.isChecked(),
            "label_size": self.sb_label_size.value(),
            "start_ap": self._start_ap,
            "end_ap":   self._end_ap,
        }

    def to_dict(self) -> dict:
        d = self.get_parameters()
        d["cable_id"] = self.cable_id
        return d

    def from_dict(self, d: dict):
        self.le_name.setText(d.get("name", self.cable_id))
        c = d.get("color", self._color.name())
        self._color = QColor(c)
        self._update_color_button()
        self.le_type.setText(d.get("type", "5x1,5"))
        self.te_comment.setPlainText(d.get("comment", ""))
        self.chk_visible.setChecked(d.get("visible", True))
        self.sb_label_size.setValue(d.get("label_size", 12.0))
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

        self.le_name = QLineEdit(self._name)
        self.le_name.textChanged.connect(
            lambda v: self.name_changed.emit(self.hkv_id, v))
        form.addRow("Name:", self.le_name)

        self.btn_color = QPushButton("Farbe")
        self.btn_color.clicked.connect(self._choose_color)
        self._update_color_button()
        form.addRow("Farbe:", self.btn_color)

        self.sb_width = QDoubleSpinBox()
        self.sb_width.setRange(1.0, 50.0)
        self.sb_width.setSingleStep(1.0)
        self.sb_width.setValue(5.0)
        self.sb_width.setSuffix(" cm")
        self.sb_width.valueChanged.connect(
            lambda _: self.size_changed.emit(self.hkv_id))
        form.addRow("Breite:", self.sb_width)

        self.sb_height = QDoubleSpinBox()
        self.sb_height.setRange(1.0, 50.0)
        self.sb_height.setSingleStep(1.0)
        self.sb_height.setValue(5.0)
        self.sb_height.setSuffix(" cm")
        self.sb_height.valueChanged.connect(
            lambda _: self.size_changed.emit(self.hkv_id))
        form.addRow("H\u00f6he:", self.sb_height)

        self.btn_icon = QPushButton("Symbol laden\u2026")
        self.btn_icon.clicked.connect(self._load_icon)
        form.addRow("Symbol:", self.btn_icon)

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(4.0, 80.0)
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

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(4.0, 80.0)
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
        self.sb_label_size.setValue(d.get("label_size", 12.0))
        self.set_start_hkv(d.get("start_hkv", ""))
        self.set_end_hkv(d.get("end_hkv", ""))


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
        self.sb_opacity = QDoubleSpinBox()
        self.sb_opacity.setRange(0.0, 1.0)
        self.sb_opacity.setSingleStep(0.05)
        self.sb_opacity.setDecimals(2)
        self.sb_opacity.setValue(1.0)
        self.sb_opacity.valueChanged.connect(
            lambda v: self.opacity_changed.emit(self.fp_id, v)
        )
        form.addRow("Deckkraft:", self.sb_opacity)

        # Offset X / Y
        self.sb_offset_x = QDoubleSpinBox()
        self.sb_offset_x.setRange(-99999.0, 99999.0)
        self.sb_offset_x.setSingleStep(1.0)
        self.sb_offset_x.setDecimals(1)
        self.sb_offset_x.setSuffix(" px")
        self.sb_offset_x.valueChanged.connect(self._emit_transform)
        form.addRow("Versatz X:", self.sb_offset_x)

        self.sb_offset_y = QDoubleSpinBox()
        self.sb_offset_y.setRange(-99999.0, 99999.0)
        self.sb_offset_y.setSingleStep(1.0)
        self.sb_offset_y.setDecimals(1)
        self.sb_offset_y.setSuffix(" px")
        self.sb_offset_y.valueChanged.connect(self._emit_transform)
        form.addRow("Versatz Y:", self.sb_offset_y)

        # Rotation
        self.sb_rotation = QDoubleSpinBox()
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
        self.sb_ref_length = QDoubleSpinBox()
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

        self.sb_fixed_width = QDoubleSpinBox()
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

        self.sb_fixed_height = QDoubleSpinBox()
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

    delete_requested            = Signal(str)
    add_floorplan_requested     = Signal()
    delete_floorplan_requested  = Signal(str)
    floorplan_file_browse       = Signal(str)
    floorplan_polygon_draw      = Signal(str)
    floorplan_polygon_color_changed = Signal(str, str)
    floorplan_ref_line          = Signal(str)
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
    add_elec_cable_requested    = Signal(str)   # fp_id
    add_hkv_requested           = Signal(str)   # fp_id
    add_hkv_line_requested      = Signal(str)   # fp_id
    delete_elec_point_requested = Signal(str)
    delete_elec_cable_requested = Signal(str)
    delete_hkv_requested        = Signal(str)
    delete_hkv_line_requested   = Signal(str)
    duplicate_elec_point_requested = Signal(str)
    duplicate_elec_cable_requested = Signal(str)
    all_hk_visibility_changed      = Signal(bool)
    all_elec_visibility_changed    = Signal(bool)
    heating_global_changed         = Signal()
    add_furniture_requested        = Signal(str)   # parent_fp_id
    delete_furniture_requested     = Signal(str)   # furniture_id
    furniture_size_changed         = Signal(str)   # furniture_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.circuit_panels: dict[str, HeatingCircuitPanel] = {}
        self.elec_point_panels: dict[str, ElektroPointPanel] = {}
        self.elec_cable_panels: dict[str, ElektroCablePanel] = {}
        self.hkv_panels: dict[str, HkvPanel] = {}
        self.hkv_line_panels: dict[str, HkvLinePanel] = {}
        self.floorplan_panels: dict[str, FloorPlanPanel] = {}
        self.furniture_panels: dict[str, FloorPlanPanel] = {}
        self._furniture_parent: dict[str, str] = {}  # furniture_id -> parent_fp_id
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._fp_sub_items: dict[str, dict] = {}      # fp_id -> {hk, hkv, hkv_line, ap, kv}
        self._element_floorplan: dict[str, str] = {}  # element_id -> fp_id
        self._loading = False
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
        layout.addLayout(btn_row2)

        # ── Splitter: TreeView + Eigenschaften ─────────────────────
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        # -- TreeView -----------------------------------------------
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)

        # Floor plan items are created dynamically via add_floorplan_panel()
        # Connect AFTER initial setup to avoid spurious signals
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        self._tree.itemChanged.connect(self._on_tree_item_changed)
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

        self.sb_vorlauf = QDoubleSpinBox()
        self.sb_vorlauf.setRange(20.0, 90.0)
        self.sb_vorlauf.setSingleStep(0.5)
        self.sb_vorlauf.setValue(35.0)
        self.sb_vorlauf.setDecimals(1)
        self.sb_vorlauf.setSuffix(" \u00b0C")
        self.sb_vorlauf.valueChanged.connect(
            lambda _: self.heating_global_changed.emit()
        )
        hg_form.addRow("Vorlauftemperatur:", self.sb_vorlauf)

        self.sb_ruecklauf = QDoubleSpinBox()
        self.sb_ruecklauf.setRange(15.0, 80.0)
        self.sb_ruecklauf.setSingleStep(0.5)
        self.sb_ruecklauf.setValue(30.0)
        self.sb_ruecklauf.setDecimals(1)
        self.sb_ruecklauf.setSuffix(" \u00b0C")
        self.sb_ruecklauf.valueChanged.connect(
            lambda _: self.heating_global_changed.emit()
        )
        hg_form.addRow("R\u00fccklauftemperatur:", self.sb_ruecklauf)

        self.sb_norm_aussen = QDoubleSpinBox()
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
        child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
        child.setCheckState(0, Qt.Checked)
        self._tree_items[item_id] = child
        parent_item.setExpanded(True)
        if not self._loading:
            self._tree.setCurrentItem(child)
        return child

    def _remove_tree_item(self, item_id: str):
        item = self._tree_items.pop(item_id, None)
        if item:
            parent = item.parent()
            if parent:
                parent.removeChild(item)
            else:
                idx = self._tree.indexOfTopLevelItem(item)
                if idx >= 0:
                    self._tree.takeTopLevelItem(idx)

    def _update_tree_item_name(self, item_id: str, name: str):
        item = self._tree_items.get(item_id)
        if item:
            item.setText(0, name or item_id)

    def _on_tree_selection(self, current: QTreeWidgetItem | None,
                           previous: QTreeWidgetItem | None):
        """Show the property panel for the selected tree item."""
        if self._loading:
            return

        # Hide everything first
        self._empty_label.hide()
        self._heat_global_panel.hide()
        for p in self.floorplan_panels.values():
            p.hide()
        for p in self.furniture_panels.values():
            p.hide()
        for p in self.circuit_panels.values():
            p.hide()
        for p in self.elec_point_panels.values():
            p.hide()
        for p in self.elec_cable_panels.values():
            p.hide()
        for p in self.hkv_panels.values():
            p.hide()
        for p in self.hkv_line_panels.values():
            p.hide()

        if not current:
            self._empty_label.show()
            return

        item_id = current.data(0, Qt.UserRole)
        if not item_id:
            # Sub-category headers — show heat global panel for "🔥 Heizkreise"
            for subs in self._fp_sub_items.values():
                if current is subs.get("hk"):
                    self._heat_global_panel.show()
                    return
            self._empty_label.show()
            return

        panel = (self.floorplan_panels.get(item_id)
                 or self.furniture_panels.get(item_id)
                 or self.circuit_panels.get(item_id)
                 or self.elec_point_panels.get(item_id)
                 or self.elec_cable_panels.get(item_id)
                 or self.hkv_panels.get(item_id)
                 or self.hkv_line_panels.get(item_id))
        if panel:
            panel.show()
        else:
            self._empty_label.show()

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
                             or self.elec_cable_panels.get(eid)
                             or self.hkv_panels.get(eid)
                             or self.hkv_line_panels.get(eid))
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
                    elif item is subs["kv"]:
                        panel = self.elec_cable_panels.get(eid)
                    if panel:
                        panel.chk_visible.setChecked(checked)
                return

        # Individual leaf element (furniture or element) toggled
        if item_id:
            panel = (self.furniture_panels.get(item_id)
                     or self.circuit_panels.get(item_id)
                     or self.elec_point_panels.get(item_id)
                     or self.elec_cable_panels.get(item_id)
                     or self.hkv_panels.get(item_id)
                     or self.hkv_line_panels.get(item_id))
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

    # ──────────────────────────────────────────────────────────────── #
    #  Grundrisse (Floor Plans)                                         #
    # ──────────────────────────────────────────────────────────────── #

    def add_floorplan_panel(self, fp_id: str,
                            name: str | None = None) -> FloorPlanPanel:
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
        fp_item.setFlags(fp_item.flags() | Qt.ItemIsUserCheckable)
        fp_item.setCheckState(0, Qt.Checked)
        self._tree_items[fp_id] = fp_item

        hk_item = QTreeWidgetItem(fp_item, ["\U0001f525 Heizkreise"])
        hk_item.setFlags(hk_item.flags() | Qt.ItemIsUserCheckable)
        hk_item.setCheckState(0, Qt.Checked)

        hkv_item = QTreeWidgetItem(fp_item, ["Heizkreisverteiler"])
        hkv_item.setFlags(hkv_item.flags() | Qt.ItemIsUserCheckable)
        hkv_item.setCheckState(0, Qt.Checked)

        hkv_line_item = QTreeWidgetItem(fp_item, ["HKV-Leitungen"])
        hkv_line_item.setFlags(hkv_line_item.flags() | Qt.ItemIsUserCheckable)
        hkv_line_item.setCheckState(0, Qt.Checked)

        ap_item = QTreeWidgetItem(fp_item, ["\u26a1 Anschlusspunkte"])
        ap_item.setFlags(ap_item.flags() | Qt.ItemIsUserCheckable)
        ap_item.setCheckState(0, Qt.Checked)

        kv_item = QTreeWidgetItem(fp_item, ["Kabelverbindungen"])
        kv_item.setFlags(kv_item.flags() | Qt.ItemIsUserCheckable)
        kv_item.setCheckState(0, Qt.Checked)

        self._fp_sub_items[fp_id] = {
            "hk": hk_item, "hkv": hkv_item, "hkv_line": hkv_line_item,
            "ap": ap_item, "kv": kv_item,
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
        parent_tree_item = self._tree_items.get(parent_fp_id)
        if parent_tree_item is None and self.floorplan_panels:
            first_fp = next(iter(self.floorplan_panels))
            parent_tree_item = self._tree_items.get(first_fp)
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
                for j in range(fp_item.childCount()):
                    child = fp_item.child(j)
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
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.elec_point_panels[point_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[point_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("ap") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, point_id, name or point_id)
        return panel

    def remove_elec_point_panel(self, point_id: str):
        self._remove_tree_item(point_id)
        self._element_floorplan.pop(point_id, None)
        panel = self.elec_point_panels.pop(point_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

    def get_elec_point_params(self, point_id: str) -> dict | None:
        panel = self.elec_point_panels.get(point_id)
        return panel.get_parameters() if panel else None

    # ──────────────────────────────────────────────────────────────── #
    #  Elektro: Kabelverbindungen                                       #
    # ──────────────────────────────────────────────────────────────── #

    def add_elec_cable_panel(self, cable_id: str,
                             fp_id: str | None = None,
                             name: str | None = None,
                             color: str | None = None) -> ElektroCablePanel:
        panel = ElektroCablePanel(cable_id, name=name, color=color)
        panel.delete_requested.connect(self.delete_elec_cable_requested)
        panel.duplicate_requested.connect(self.duplicate_elec_cable_requested)
        panel.name_changed.connect(self._update_tree_item_name)
        panel.visibility_changed.connect(
            lambda cid, c: self._sync_tree_checkbox(cid, c)
        )
        self._prop_layout.insertWidget(self._prop_layout.count() - 1, panel)
        panel.hide()
        self.elec_cable_panels[cable_id] = panel
        resolved = self._resolve_fp_id(fp_id)
        self._element_floorplan[cable_id] = resolved or ""
        parent_item = self._fp_sub_items.get(resolved or "", {}).get("kv") if resolved else None
        if parent_item:
            self._add_tree_item(parent_item, cable_id, name or cable_id)
        return panel

    def remove_elec_cable_panel(self, cable_id: str):
        self._remove_tree_item(cable_id)
        self._element_floorplan.pop(cable_id, None)
        panel = self.elec_cable_panels.pop(cable_id, None)
        if panel:
            self._prop_layout.removeWidget(panel)
            panel.deleteLater()
        self._show_placeholder_if_empty()

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
    #  Helpers                                                          #
    # ──────────────────────────────────────────────────────────────── #

    def _show_placeholder_if_empty(self):
        """Show the 'select an item' label when no panel is visible."""
        if not any(p.isVisible() for p in
                   list(self.floorplan_panels.values()) +
                   list(self.furniture_panels.values()) +
                   list(self.circuit_panels.values()) +
                   list(self.elec_point_panels.values()) +
                   list(self.elec_cable_panels.values()) +
                   list(self.hkv_panels.values()) +
                   list(self.hkv_line_panels.values())):
            self._empty_label.show()

    # ──────────────────────────────────────────────────────────────── #
    #  General heating params                                           #
    # ──────────────────────────────────────────────────────────────── #

    def get_heating_params(self) -> dict:
        return {
            "t_supply": self.sb_vorlauf.value(),
            "t_return": self.sb_ruecklauf.value(),
            "t_norm_outdoor": self.sb_norm_aussen.value(),
        }

    # ──────────────────────────────────────────────────────────────── #
    #  Serialization                                                    #
    # ──────────────────────────────────────────────────────────────── #

    def clear_all_panels(self):
        """Remove all object panels (circuits, elec, HKV, floorplans) from the tree + layout."""
        for fur_id in list(self.furniture_panels):
            self.remove_furniture_panel(fur_id)
        for fid in list(self.floorplan_panels):
            self.remove_floorplan_panel(fid)
        for cid in list(self.circuit_panels):
            self.remove_circuit_panel(cid)
        for pid in list(self.elec_point_panels):
            self.remove_elec_point_panel(pid)
        for cid in list(self.elec_cable_panels):
            self.remove_elec_cable_panel(cid)
        for hid in list(self.hkv_panels):
            self.remove_hkv_panel(hid)
        for lid in list(self.hkv_line_panels):
            self.remove_hkv_line_panel(lid)

    def to_dict(self) -> dict:
        return {
            "t_supply": self.sb_vorlauf.value(),
            "t_return": self.sb_ruecklauf.value(),
            "t_norm_outdoor": self.sb_norm_aussen.value(),
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
        }

    def from_dict(self, d: dict):
        self._loading = True

        self.sb_vorlauf.setValue(d.get("t_supply", 35.0))
        self.sb_ruecklauf.setValue(d.get("t_return", 30.0))
        self.sb_norm_aussen.setValue(d.get("t_norm_outdoor", -12.0))

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
        for cid, values in d.get("elec_cables", {}).items():
            panel = self.add_elec_cable_panel(
                cid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", cid),
                color=values.get("color", "#ff9800"),
            )
            panel.from_dict(values)
        for lid, values in d.get("hkv_lines", {}).items():
            panel = self.add_hkv_line_panel(
                lid, fp_id=values.get("floor_plan_id"),
                name=values.get("name", lid),
                color=values.get("color", "#e53935"),
            )
            panel.from_dict(values)

        self._loading = False
