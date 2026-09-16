from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QByteArray, QSignalBlocker
from PySide6.QtGui import QColor, QPen, QBrush, QPainterPath, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.parameter_panel import BUILTIN_SYMBOLS, UvConfigDialog, UpDistributionDialog
from model.schema import format_auto_cable_name, format_elec_point_choice_label
from storage.asset_data_uri import is_data_uri, is_svg_asset_ref, parse_data_uri


@dataclass
class ApNode:
    point_id: str
    name: str
    room: str
    ap_type: str
    has_distributor_function: bool
    is_connected: bool
    color: str
    icon_path: str
    builtin_symbol: str
    width_px: float
    height_px: float
    room_id: str = ""
    width_mm: float = 30.0
    height_mm: float = 30.0
    visible: bool = True
    label_visible: bool = True
    label_size: float = 12.0
    position: str = "Wand"
    height_from_floor: float = 0.0
    smarthome_device: str = ""
    smarthome_device_color: str = ""
    note: str = ""
    uv_config: dict | None = None
    up_distribution_config: dict | None = None
    hak_config: dict | None = None
    zaehler_config: dict | None = None


@dataclass
class CableEdge:
    cable_id: str
    name: str
    cable_type: str
    length_m: float
    color: str
    stroke_width_px: float
    line_style: str = "solid"
    start_ap_id: str = ""
    end_ap_id: str = ""
    visible: bool = True
    label_visible: bool = True
    type_label_visible: bool = False
    label_size: float = 12.0
    comment: str = ""


def _line_style_to_pen_style(style_key: str):
    style = str(style_key or "solid").strip().lower()
    if style == "dash":
        return Qt.PenStyle.DashLine
    if style == "dot":
        return Qt.PenStyle.DotLine
    if style == "dashdot":
        return Qt.PenStyle.DashDotLine
    return Qt.PenStyle.SolidLine


class _AddApDialog(QDialog):
    def __init__(self, room_choices: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("AP hinzufügen")
        self.resize(420, 320)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.le_name = QLineEdit()
        self.le_name.setPlaceholderText("z. B. Steckdose Küche")
        form.addRow("Name (automatisch):", self.le_name)

        self.cmb_symbol = QComboBox()
        for label in BUILTIN_SYMBOLS.keys():
            self.cmb_symbol.addItem(label)
        if self.cmb_symbol.findText("Steckdose") >= 0:
            self.cmb_symbol.setCurrentText("Steckdose")
        form.addRow("Symbol:", self.cmb_symbol)

        self.cmb_color = QComboBox()
        self.cmb_color.setEditable(True)
        self.cmb_color.addItems(["#4fc3f7", "#ff9800", "#43aa8b", "#e53935"])
        self.cmb_color.setCurrentText("#4fc3f7")
        form.addRow("Farbe:", self.cmb_color)

        self.cmb_ap_type = QComboBox()
        self.cmb_ap_type.addItem("Standard", "standard")
        self.cmb_ap_type.addItem("Unterverteilung (UV)", "uv")
        self.cmb_ap_type.addItem("Verteilung in Unterputzdose", "up_distribution")
        self.cmb_ap_type.addItem("Hausanschlusskasten (HAK)", "hak")
        self.cmb_ap_type.addItem("Stromzähler", "zaehler")
        form.addRow("AP-Typ:", self.cmb_ap_type)

        self.cmb_room = QComboBox()
        self.cmb_room.addItem("(kein Raum)", "")
        for room_id, room_name in room_choices:
            label = room_name or room_id
            self.cmb_room.addItem(label, room_id)
        form.addRow("Raum:", self.cmb_room)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_payload(self) -> dict:
        return {
            "name": self.le_name.text().strip(),
            "symbol": self.cmb_symbol.currentText().strip(),
            "color": self.cmb_color.currentText().strip() or "#4fc3f7",
            "ap_type": str(self.cmb_ap_type.currentData() or "standard"),
            "room_id": str(self.cmb_room.currentData() or ""),
        }


class _AddCableDialog(QDialog):
    def __init__(self, all_ap_nodes: dict[str, ApNode], parent=None):
        super().__init__(parent)
        self._all_ap_nodes = dict(all_ap_nodes)
        self.setWindowTitle("Kabel hinzufügen")
        self.resize(420, 340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.le_name = QLineEdit()
        self.le_name.setReadOnly(True)
        self.le_name.setToolTip("Wird automatisch aus Start-AP und End-AP erzeugt.")
        form.addRow("Name (automatisch):", self.le_name)

        self.le_type = QLineEdit("5x1,5")
        form.addRow("Kabeltyp:", self.le_type)

        self.cmb_color = QComboBox()
        self.cmb_color.setEditable(True)
        self.cmb_color.addItems(["#ff9800", "#4fc3f7", "#43aa8b", "#e53935"])
        self.cmb_color.setCurrentText("#ff9800")
        form.addRow("Farbe:", self.cmb_color)

        self.sb_stroke = QDoubleSpinBox()
        self.sb_stroke.setRange(0.5, 10.0)
        self.sb_stroke.setSingleStep(0.5)
        self.sb_stroke.setDecimals(1)
        self.sb_stroke.setValue(2.0)
        self.sb_stroke.setSuffix(" px")
        form.addRow("Linienstärke:", self.sb_stroke)

        self.cmb_line_style = QComboBox()
        self.cmb_line_style.addItem("Durchgezogen", "solid")
        self.cmb_line_style.addItem("Gestrichelt", "dash")
        self.cmb_line_style.addItem("Gepunktet", "dot")
        self.cmb_line_style.addItem("Strich-Punkt", "dashdot")
        self.cmb_line_style.setCurrentIndex(0)
        form.addRow("Linientyp:", self.cmb_line_style)

        sorted_aps = [
            (pid, format_elec_point_choice_label(pid, n.name or ""))
            for pid, n in all_ap_nodes.items()
        ]
        sorted_aps.sort(key=lambda value: value[1].lower())
        self.cmb_start_ap = QComboBox()
        self.cmb_end_ap = QComboBox()
        self.cmb_start_ap.addItem("(keiner)", "")
        self.cmb_end_ap.addItem("(keiner)", "")
        for ap_id, label_text in sorted_aps:
            self.cmb_start_ap.addItem(label_text, ap_id)
            self.cmb_end_ap.addItem(label_text, ap_id)
        self.cmb_start_ap.currentIndexChanged.connect(lambda _idx: self._update_auto_name())
        self.cmb_end_ap.currentIndexChanged.connect(lambda _idx: self._update_auto_name())
        self._update_auto_name()
        form.addRow("Start-AP:", self.cmb_start_ap)
        form.addRow("End-AP:", self.cmb_end_ap)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_payload(self) -> dict:
        return {
            "name": self.le_name.text().strip(),
            "type": self.le_type.text().strip() or "5x1,5",
            "color": self.cmb_color.currentText().strip() or "#ff9800",
            "stroke_width": float(self.sb_stroke.value()),
            "line_style": str(self.cmb_line_style.currentData() or "solid"),
            "start_ap_id": str(self.cmb_start_ap.currentData() or ""),
            "end_ap_id": str(self.cmb_end_ap.currentData() or ""),
        }

    def _ap_name(self, ap_id: str) -> str:
        ap_id = str(ap_id or "").strip()
        node = self._all_ap_nodes.get(ap_id)
        return str(node.name if node is not None else ap_id or "")

    def _update_auto_name(self) -> None:
        start_id = str(self.cmb_start_ap.currentData() or "")
        end_id = str(self.cmb_end_ap.currentData() or "")
        self.le_name.setText(format_auto_cable_name(self._ap_name(start_id), self._ap_name(end_id)))


class _EditApDialog(QDialog):
    """Dialog zum Bearbeiten aller AP-Eigenschaften."""
    def __init__(
        self,
        node: "ApNode",
        uv_cable_choices: list[str] | None = None,
        up_cable_choices: list[tuple[str, str]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"AP bearbeiten – {node.name or node.point_id}")
        self.resize(480, 680)
        self._node = node
        self._uv_cable_choices = list(uv_cable_choices or [])
        self._up_cable_choices = list(up_cable_choices or [])
        self._uv_config = dict(node.uv_config or {})
        self._up_distribution_config = dict(node.up_distribution_config or {})
        self._hak_config = dict(node.hak_config or {})
        self._zaehler_config = dict(node.zaehler_config or {})
        self._icon_path = str(node.icon_path or "")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(bool(node.visible))
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(bool(node.label_visible))
        form.addRow(self.chk_label_visible)

        self.le_name = QLineEdit(node.name or "")
        self.le_name.setPlaceholderText("z. B. Steckdose Küche")
        form.addRow("Name:", self.le_name)

        self.lbl_room = QLabel(node.room or "(ohne Raum)")
        form.addRow("Raum:", self.lbl_room)

        self.cmb_symbol = QComboBox()
        for lbl in BUILTIN_SYMBOLS.keys():
            self.cmb_symbol.addItem(lbl)
        if self.cmb_symbol.findText(node.builtin_symbol or "") >= 0:
            self.cmb_symbol.setCurrentText(node.builtin_symbol or "")
        self.cmb_symbol.currentTextChanged.connect(self._on_symbol_selected)
        form.addRow("Symbol:", self.cmb_symbol)

        self.btn_icon = QPushButton("Eigenes Bild…")
        self.btn_icon.clicked.connect(self._load_icon)
        if self._is_custom_icon_selected():
            self.btn_icon.setText(Path(self._icon_path).name)
        form.addRow("Eigenes Bild:", self.btn_icon)

        self.cmb_color = QComboBox()
        self.cmb_color.setEditable(True)
        self.cmb_color.addItems(["#4fc3f7", "#ff9800", "#43aa8b", "#e53935"])
        self.cmb_color.setCurrentText(node.color or "#4fc3f7")
        form.addRow("Farbe:", self.cmb_color)

        self.sb_width = QDoubleSpinBox()
        self.sb_width.setRange(0.1, 999999.0)
        self.sb_width.setSingleStep(0.5)
        self.sb_width.setSuffix(" cm")
        self.sb_width.setValue(max(0.1, float(node.width_mm) / 10.0))
        form.addRow("Breite:", self.sb_width)

        self.sb_height_size = QDoubleSpinBox()
        self.sb_height_size.setRange(0.1, 999999.0)
        self.sb_height_size.setSingleStep(0.5)
        self.sb_height_size.setSuffix(" cm")
        self.sb_height_size.setValue(max(0.1, float(node.height_mm) / 10.0))
        form.addRow("Höhe:", self.sb_height_size)

        self.cmb_ap_type = QComboBox()
        self.cmb_ap_type.addItem("Standard", "standard")
        self.cmb_ap_type.addItem("Unterverteilung (UV)", "uv")
        self.cmb_ap_type.addItem("Verteilung in Unterputzdose", "up_distribution")
        self.cmb_ap_type.addItem("Hausanschlusskasten (HAK)", "hak")
        self.cmb_ap_type.addItem("Stromzähler", "zaehler")
        type_map = {"standard": 0, "uv": 1, "up_distribution": 2, "hak": 3, "zaehler": 4}
        self.cmb_ap_type.setCurrentIndex(type_map.get(node.ap_type or "standard", 0))
        self.cmb_ap_type.currentIndexChanged.connect(self._on_ap_type_changed)
        form.addRow("AP-Typ:", self.cmb_ap_type)

        self.btn_uv_config = QPushButton("🗂️ UV planen…")
        self.btn_uv_config.clicked.connect(self._open_uv_dialog)
        form.addRow(self.btn_uv_config)

        self.btn_up_config = QPushButton("Verteilung in Unterputzdose…")
        self.btn_up_config.clicked.connect(self._open_up_dialog)
        form.addRow(self.btn_up_config)

        self.btn_hak_config = QPushButton("🏠 HAK konfigurieren…")
        self.btn_hak_config.clicked.connect(self._open_hak_dialog)
        form.addRow(self.btn_hak_config)

        self.btn_zaehler_config = QPushButton("🔢 Zähler konfigurieren…")
        self.btn_zaehler_config.clicked.connect(self._open_zaehler_dialog)
        form.addRow(self.btn_zaehler_config)

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.setValue(float(node.label_size or 12.0))
        form.addRow("Schriftgröße:", self.sb_label_size)

        self.cmb_position = QComboBox()
        self.cmb_position.addItems(["Wand", "Decke", "Boden", "Freitext"])
        self.cmb_position.currentTextChanged.connect(self._on_position_changed)
        form.addRow("Position:", self.cmb_position)

        self.le_position_custom = QLineEdit()
        self.le_position_custom.setPlaceholderText("Z. B. Trockenbau, Fenster...")
        form.addRow("Freitext:", self.le_position_custom)

        self._set_position_value(node.position or "Wand")

        self.sb_height_from_floor = QDoubleSpinBox()
        self.sb_height_from_floor.setRange(0.0, 999.9)
        self.sb_height_from_floor.setSingleStep(1.0)
        self.sb_height_from_floor.setDecimals(1)
        self.sb_height_from_floor.setSuffix(" cm")
        self.sb_height_from_floor.setValue(float(node.height_from_floor or 0.0))
        form.addRow("Höhe v. Boden:", self.sb_height_from_floor)

        self.cmb_smarthome = QComboBox()
        self.cmb_smarthome.setEditable(True)
        self.cmb_smarthome.addItems(["", "Shelly", "Sonoff ZBMINIR2"])
        self.cmb_smarthome.setCurrentText(node.smarthome_device or "")
        form.addRow("Unterputz-Gerät:", self.cmb_smarthome)

        self.cmb_smarthome_color = QComboBox()
        self.cmb_smarthome_color.setEditable(True)
        self.cmb_smarthome_color.addItems(["", "weiß", "schwarz"])
        self.cmb_smarthome_color.setCurrentText(node.smarthome_device_color or "")
        form.addRow("Gerätefarbe:", self.cmb_smarthome_color)

        self.te_note = QTextEdit()
        self.te_note.setMaximumHeight(80)
        self.te_note.setPlainText(node.note or "")
        form.addRow("Notiz:", self.te_note)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_config_buttons_visibility()

    def _is_custom_icon_selected(self) -> bool:
        builtin_path = BUILTIN_SYMBOLS.get(self.cmb_symbol.currentText() or "", "")
        return bool(self._icon_path and self._icon_path != builtin_path)

    def _on_symbol_selected(self, label: str):
        path = BUILTIN_SYMBOLS.get(label, "")
        if path:
            self._icon_path = path
            self.btn_icon.setText("Eigenes Bild…")
        elif label == "(kein Symbol)":
            self._icon_path = ""
            self.btn_icon.setText("Eigenes Bild…")

    def _load_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Symbol laden",
            "",
            "Bilder (*.png *.jpg *.svg *.bmp)",
        )
        if not path:
            return
        self._icon_path = path
        idx = self.cmb_symbol.findText("(kein Symbol)")
        if idx >= 0:
            self.cmb_symbol.blockSignals(True)
            self.cmb_symbol.setCurrentIndex(idx)
            self.cmb_symbol.blockSignals(False)
        self.btn_icon.setText(Path(path).name)

    def _set_position_value(self, value: str):
        text = (value or "").strip() or "Wand"
        idx = self.cmb_position.findText(text)
        if idx >= 0:
            self.cmb_position.setCurrentIndex(idx)
            self.le_position_custom.clear()
            self.le_position_custom.setEnabled(text == "Freitext")
            return
        idx = self.cmb_position.findText("Freitext")
        if idx >= 0:
            self.cmb_position.setCurrentIndex(idx)
        self.le_position_custom.setEnabled(True)
        self.le_position_custom.setText(text)

    def _on_position_changed(self, value: str):
        is_custom = value == "Freitext"
        self.le_position_custom.setEnabled(is_custom)
        if not is_custom:
            self.le_position_custom.clear()

    def _get_position_value(self) -> str:
        value = self.cmb_position.currentText().strip() or "Wand"
        if value == "Freitext":
            return self.le_position_custom.text().strip() or "Freitext"
        return value

    def _update_config_buttons_visibility(self):
        ap_type = str(self.cmb_ap_type.currentData() or "standard")
        self.btn_uv_config.setVisible(ap_type == "uv")
        self.btn_up_config.setVisible(ap_type == "up_distribution")
        self.btn_hak_config.setVisible(ap_type == "hak")
        self.btn_zaehler_config.setVisible(ap_type == "zaehler")

    def _on_ap_type_changed(self):
        self._update_config_buttons_visibility()

    def _open_uv_dialog(self):
        dlg = UvConfigDialog(
            config=self._uv_config,
            cable_choices=self._uv_cable_choices,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._uv_config = dlg.get_config()

    def _open_up_dialog(self):
        dlg = UpDistributionDialog(
            config=self._up_distribution_config,
            cable_choices=self._up_cable_choices,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._up_distribution_config = dlg.get_config()

    def _open_hak_dialog(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("HAK konfigurieren")
        dlg.resize(340, 160)
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        le_voltage = QLineEdit(str(self._hak_config.get("incoming_voltage", "400V") or "400V"))
        le_fuse = QLineEdit(str(self._hak_config.get("main_fuse_a", "63") or "63"))
        form.addRow("Spannung:", le_voltage)
        form.addRow("Hauptsicherung (A):", le_fuse)
        layout.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        layout.addWidget(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._hak_config = {
                "incoming_voltage": le_voltage.text().strip() or "400V",
                "main_fuse_a": le_fuse.text().strip() or "63",
            }

    def _open_zaehler_dialog(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Zähler konfigurieren")
        dlg.resize(340, 160)
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        le_meter_id = QLineEdit(str(self._zaehler_config.get("meter_id", "") or ""))
        cmb_phases = QComboBox()
        cmb_phases.addItems(["3-phasig", "1-phasig"])
        cur_phases = str(self._zaehler_config.get("phases", "3-phasig") or "3-phasig")
        idx = cmb_phases.findText(cur_phases)
        if idx >= 0:
            cmb_phases.setCurrentIndex(idx)
        form.addRow("Zählernummer:", le_meter_id)
        form.addRow("Phasen:", cmb_phases)
        layout.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        layout.addWidget(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._zaehler_config = {
                "meter_id": le_meter_id.text().strip(),
                "phases": cmb_phases.currentText(),
            }

    def get_payload(self) -> dict:
        ap_type = str(self.cmb_ap_type.currentData() or "standard")
        return {
            "name": self.le_name.text().strip(),
            "symbol": self.cmb_symbol.currentText().strip(),
            "icon_path": self._icon_path,
            "color": self.cmb_color.currentText().strip() or "#4fc3f7",
            "width": float(self.sb_width.value() * 10.0),
            "height": float(self.sb_height_size.value() * 10.0),
            "visible": self.chk_visible.isChecked(),
            "label_visible": self.chk_label_visible.isChecked(),
            "label_size": float(self.sb_label_size.value()),
            "ap_type": ap_type,
            "position": self._get_position_value(),
            "height_from_floor": float(self.sb_height_from_floor.value()),
            "smarthome_device": self.cmb_smarthome.currentText().strip(),
            "smarthome_device_color": self.cmb_smarthome_color.currentText().strip(),
            "note": self.te_note.toPlainText(),
            "uv_config": self._uv_config if ap_type == "uv" else {},
            "up_distribution_config": self._up_distribution_config if ap_type == "up_distribution" else {},
            "hak_config": self._hak_config if ap_type == "hak" else {},
            "zaehler_config": self._zaehler_config if ap_type == "zaehler" else {},
        }



class _EditCableDialog(QDialog):
    """Dialog zum Bearbeiten aller Kabel-Eigenschaften."""
    def __init__(self, edge: "CableEdge", all_ap_nodes: "dict[str, ApNode]", parent=None):
        super().__init__(parent)
        self._all_ap_nodes = dict(all_ap_nodes)
        self.setWindowTitle(f"Kabel bearbeiten \u2013 {edge.name or edge.cable_id}")
        self.resize(460, 460)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.chk_visible = QCheckBox("Sichtbar")
        self.chk_visible.setChecked(bool(edge.visible))
        form.addRow(self.chk_visible)

        self.chk_label_visible = QCheckBox("Beschriftung")
        self.chk_label_visible.setChecked(bool(edge.label_visible))
        form.addRow(self.chk_label_visible)

        self.chk_type_label_visible = QCheckBox("Kabeltyp im Plan")
        self.chk_type_label_visible.setChecked(bool(edge.type_label_visible))
        form.addRow(self.chk_type_label_visible)

        self.le_name = QLineEdit(edge.name or "")
        self.le_name.setReadOnly(True)
        self.le_name.setToolTip("Wird automatisch aus Start-AP und End-AP erzeugt.")
        form.addRow("Name:", self.le_name)

        self.le_type = QLineEdit(edge.cable_type or "5x1,5")
        form.addRow("Kabeltyp:", self.le_type)

        self.cmb_color = QComboBox()
        self.cmb_color.setEditable(True)
        self.cmb_color.addItems(["#ff9800", "#4fc3f7", "#43aa8b", "#e53935"])
        self.cmb_color.setCurrentText(edge.color or "#ff9800")
        form.addRow("Farbe:", self.cmb_color)

        self.sb_stroke = QDoubleSpinBox()
        self.sb_stroke.setRange(0.5, 10.0)
        self.sb_stroke.setSingleStep(0.5)
        self.sb_stroke.setDecimals(1)
        self.sb_stroke.setValue(max(0.5, float(edge.stroke_width_px)))
        self.sb_stroke.setSuffix(" px")
        form.addRow("Linienst\u00e4rke:", self.sb_stroke)

        self.cmb_line_style = QComboBox()
        self.cmb_line_style.addItem("Durchgezogen", "solid")
        self.cmb_line_style.addItem("Gestrichelt", "dash")
        self.cmb_line_style.addItem("Gepunktet", "dot")
        self.cmb_line_style.addItem("Strich-Punkt", "dashdot")
        style_idx = self.cmb_line_style.findData(str(edge.line_style or "solid"))
        self.cmb_line_style.setCurrentIndex(style_idx if style_idx >= 0 else 0)
        form.addRow("Linientyp:", self.cmb_line_style)

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.setValue(float(edge.label_size or 12.0))
        form.addRow("Schriftgröße:", self.sb_label_size)

        self.lbl_length = QLabel(f"{edge.length_m:.2f} m")
        form.addRow("Länge:", self.lbl_length)

        sorted_aps = [("", "(keiner)")] + sorted(
            [
                (pid, format_elec_point_choice_label(pid, n.name or ""))
                for pid, n in all_ap_nodes.items()
            ],
            key=lambda v: v[1].lower(),
        )
        self.cmb_start_ap = QComboBox()
        self.cmb_end_ap = QComboBox()
        for ap_id, label_text in sorted_aps:
            label_text = label_text if ap_id else "(keiner)"
            self.cmb_start_ap.addItem(label_text, ap_id)
            self.cmb_end_ap.addItem(label_text, ap_id)
        for cmb, target in [(self.cmb_start_ap, edge.start_ap_id), (self.cmb_end_ap, edge.end_ap_id)]:
            idx = cmb.findData((target or "").strip())
            if idx >= 0:
                cmb.setCurrentIndex(idx)
        self.cmb_start_ap.currentIndexChanged.connect(lambda _idx: self._update_auto_name())
        self.cmb_end_ap.currentIndexChanged.connect(lambda _idx: self._update_auto_name())
        self._update_auto_name()
        form.addRow("Start-AP:", self.cmb_start_ap)
        form.addRow("End-AP:", self.cmb_end_ap)

        self.te_comment = QTextEdit()
        self.te_comment.setMaximumHeight(80)
        self.te_comment.setPlainText(edge.comment or "")
        form.addRow("Kommentar:", self.te_comment)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_payload(self) -> dict:
        return {
            "name": self.le_name.text().strip(),
            "type": self.le_type.text().strip() or "5x1,5",
            "color": self.cmb_color.currentText().strip() or "#ff9800",
            "visible": self.chk_visible.isChecked(),
            "label_visible": self.chk_label_visible.isChecked(),
            "type_label_visible": self.chk_type_label_visible.isChecked(),
            "label_size": float(self.sb_label_size.value()),
            "stroke_width": float(self.sb_stroke.value()),
            "line_style": str(self.cmb_line_style.currentData() or "solid"),
            "start_ap_id": str(self.cmb_start_ap.currentData() or ""),
            "end_ap_id": str(self.cmb_end_ap.currentData() or ""),
            "comment": self.te_comment.toPlainText(),
        }

    def _ap_name(self, ap_id: str) -> str:
        ap_id = str(ap_id or "").strip()
        node = self._all_ap_nodes.get(ap_id)
        return str(node.name if node is not None else ap_id or "")

    def _update_auto_name(self) -> None:
        start_id = str(self.cmb_start_ap.currentData() or "")
        end_id = str(self.cmb_end_ap.currentData() or "")
        self.le_name.setText(format_auto_cable_name(self._ap_name(start_id), self._ap_name(end_id)))


class _DeleteSelectDialog(QDialog):
    def __init__(self, title: str, label: str, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 160)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.cmb = QComboBox()
        for item_id, item_label in items:
            self.cmb.addItem(item_label or item_id, item_id)
        form.addRow(label, self.cmb)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_id(self) -> str:
        return str(self.cmb.currentData() or "")


class _ZoomGraphicsView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom_callback = None
        self._mouse_press_callback = None
        self._mouse_move_callback = None
        self._mouse_release_callback = None
        self._panning = False
        self._pan_start = None
        self._hbar_start = 0
        self._vbar_start = 0

    def set_zoom_callback(self, callback):
        self._zoom_callback = callback

    def set_mouse_callbacks(self, press_callback=None, move_callback=None, release_callback=None):
        self._mouse_press_callback = press_callback
        self._mouse_move_callback = move_callback
        self._mouse_release_callback = release_callback

    def wheelEvent(self, event):
        if self._zoom_callback is None:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = 1.12 if delta > 0 else (1.0 / 1.12)
        handled = bool(self._zoom_callback(factor))
        if handled:
            event.accept()
            return
        event.ignore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self._hbar_start = self.horizontalScrollBar().value()
            self._vbar_start = self.verticalScrollBar().value()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._mouse_press_callback is not None:
            handled = bool(self._mouse_press_callback(event))
            if handled:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.position() - self._pan_start
            self.horizontalScrollBar().setValue(int(self._hbar_start - delta.x()))
            self.verticalScrollBar().setValue(int(self._vbar_start - delta.y()))
            event.accept()
            return
        if self._mouse_move_callback is not None:
            handled = bool(self._mouse_move_callback(event))
            if handled:
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        if self._mouse_release_callback is not None:
            handled = bool(self._mouse_release_callback(event))
            if handled:
                event.accept()
                return
        super().mouseReleaseEvent(event)


class _ApNodeItem(QGraphicsRectItem):
    def __init__(
        self,
        point_id: str,
        rect: QRectF,
        moved_callback,
        dblclick_callback,
        press_callback,
        position_change_callback,
        parent=None,
    ):
        super().__init__(rect, parent)
        self._point_id = point_id
        self._moved_callback = moved_callback
        self._dblclick_callback = dblclick_callback
        self._press_callback = press_callback
        self._position_change_callback = position_change_callback
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, True)

    def mousePressEvent(self, event):
        if self._press_callback is not None:
            allow_default = bool(self._press_callback(self._point_id, event))
            if not allow_default:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._moved_callback is None:
            return
        center_scene = self.mapToScene(self.rect().center())
        self._moved_callback(self._point_id, float(center_scene.x()), float(center_scene.y()))

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if self._dblclick_callback is not None:
            self._dblclick_callback(self._point_id)

    def itemChange(self, change, value):
        if (
            change == QGraphicsRectItem.GraphicsItemChange.ItemPositionChange
            and self._position_change_callback is not None
            and isinstance(value, QPointF)
        ):
            return self._position_change_callback(self._point_id, value)
        return super().itemChange(change, value)


class _CablePathItem(QGraphicsPathItem):
    """Anklickbares Kabel-Item mit Doppelklick-Support."""
    def __init__(
        self,
        cable_id: str,
        path: QPainterPath,
        dblclick_callback,
        press_callback,
        move_callback,
        release_callback,
        parent=None,
    ):
        super().__init__(path, parent)
        self._cable_id = cable_id
        self._dblclick_callback = dblclick_callback
        self._press_callback = press_callback
        self._move_callback = move_callback
        self._release_callback = release_callback
        # breiterer unsichtbarer Bereich für einfacheres Anklicken
        self.setAcceptHoverEvents(True)

    def mousePressEvent(self, event):
        if self._press_callback is not None:
            self._press_callback(self._cable_id, event)
            if event.isAccepted():
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._move_callback is not None:
            self._move_callback(self._cable_id, event)
            if event.isAccepted():
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._release_callback is not None:
            self._release_callback(self._cable_id, event)
            if event.isAccepted():
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if self._dblclick_callback is not None:
            self._dblclick_callback(self._cable_id)


class _CableEndpointHandle(QGraphicsEllipseItem):
    def __init__(
        self,
        cable_id: str,
        endpoint: str,
        rect: QRectF,
        press_callback,
        move_callback,
        release_callback,
        parent=None,
    ):
        super().__init__(rect, parent)
        self._cable_id = cable_id
        self._endpoint = endpoint
        self._press_callback = press_callback
        self._move_callback = move_callback
        self._release_callback = release_callback
        self.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptHoverEvents(True)

    def mousePressEvent(self, event):
        if self._press_callback is not None:
            self._press_callback(self._cable_id, self._endpoint, event)
            if event.isAccepted():
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._move_callback is not None:
            self._move_callback(self._cable_id, event)
            if event.isAccepted():
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._release_callback is not None:
            self._release_callback(self._cable_id, event)
            if event.isAccepted():
                return
        super().mouseReleaseEvent(event)

    def hoverEnterEvent(self, event):
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)


class ElecSchemaWindow(QMainWindow):
    _remote_mermaid_available: bool | None = None

    add_ap_requested = Signal(dict)
    add_cable_requested = Signal(dict)
    delete_ap_requested = Signal(str)
    delete_cable_requested = Signal(str)
    ap_position_changed = Signal(str, float, float)
    ap_positions_changed = Signal(dict)
    edit_ap_requested = Signal(str, dict)   # point_id, payload
    edit_cable_requested = Signal(str, dict)  # cable_id, payload
    duplicate_selection_requested = Signal(list, list)  # ap_ids, cable_ids

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Elektro-Strangschema")
        self.resize(1200, 800)

        self._ap_nodes: dict[str, ApNode] = {}
        self._cable_edges: dict[str, CableEdge] = {}
        self._uniform_font_pt = 10.0
        self._zoom_step = 1.2
        self._zoom_min = 0.2
        self._zoom_max = 5.0
        self._manual_positions: dict[str, tuple[float, float]] = {}
        self._room_choices: list[tuple[str, str]] = []
        self._ap_scene_positions: dict[str, QPointF] = {}
        self._cable_endpoints_scene: dict[str, tuple[QPointF | None, QPointF | None]] = {}
        self._ap_items: dict[str, _ApNodeItem] = {}
        self._cable_items: dict[str, _CablePathItem] = {}
        self._cable_handle_items: dict[str, list[_CableEndpointHandle]] = {}
        self._selected_ap_ids: set[str] = set()
        self._selected_cable_ids: set[str] = set()
        self._active_cable_id: str | None = None
        self._selection_origin: QPointF | None = None
        self._selection_rect_item: QGraphicsRectItem | None = None
        self._selection_origin_ap_ids: set[str] = set()
        self._selection_origin_cable_ids: set[str] = set()
        self._selection_mode: str = "replace"
        self._group_drag_anchor_id: str | None = None
        self._group_drag_orig_positions: dict[str, QPointF] = {}
        self._group_drag_active = False
        self._applying_group_drag = False
        self._view_group_drag_state: dict | None = None
        self._cable_drag_state: dict | None = None
        self._cable_rewire_state: dict | None = None
        self._cable_pick_state: dict | None = None
        self._copied_selection: dict[str, list[str]] | None = None
        self._rewire_preview_item: QGraphicsPathItem | None = None
        self._rewire_endpoint_tolerance_px = 14.0
        self._rewire_drop_tolerance_px = 56.0
        self._handle_radius = 6.5
        self._is_rendering = False
        self._selected_root_ap_id = ""
        self._tree_edge_ids: set[str] = set()
        self._cross_edge_ids: set[str] = set()
        self._compact_view_enabled = True
        self._show_all_cable_labels = False
        self._layout_mode = "radial"
        self._show_room_zones = True
        self._room_zone_defs: list[dict] = []
        self._room_styles: dict[str, dict[str, str]] = {}
        self._room_shape_mode = "ellipse"
        self._use_room_colors = True
        self._tight_packing_enabled = True

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        self.btn_add_ap = QPushButton("➕ AP")
        self.btn_add_cable = QPushButton("➕ Kabel")
        self.btn_del_ap = QPushButton("🗑 AP")
        self.btn_del_cable = QPushButton("🗑 Kabel")
        
        self.btn_zoom_in = QPushButton("＋")
        self.btn_zoom_out = QPushButton("－")
        self.btn_zoom_reset = QPushButton("100%")
        self.btn_fit = QPushButton("Auf Inhalt einpassen")
        self.lbl_root = QLabel("Root:")
        self.cmb_root_ap = QComboBox()
        self.cmb_root_ap.setMinimumWidth(240)
        self.chk_rooms = QCheckBox("Räume")
        self.chk_rooms.setChecked(True)
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setMinimumWidth(52)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_mode = QLabel("")
        self.lbl_mode.setVisible(False)
        self.lbl_mode.setStyleSheet("color:#ffd54f; font-weight:bold;")
        self.lbl_render_status = QLabel("")
        self.lbl_render_status.setVisible(False)

        top.addWidget(self.btn_add_ap)
        top.addWidget(self.btn_add_cable)
        top.addWidget(self.btn_del_ap)
        top.addWidget(self.btn_del_cable)
        top.addSpacing(8)
        top.addWidget(self.lbl_root)
        top.addWidget(self.cmb_root_ap)
        top.addWidget(self.chk_rooms)
        top.addStretch(1)
        top.addWidget(self.btn_zoom_out)
        top.addWidget(self.btn_zoom_in)
        top.addWidget(self.btn_zoom_reset)
        top.addWidget(self.lbl_zoom)
        top.addWidget(self.lbl_mode)
        top.addWidget(self.btn_fit)
        root.addLayout(top)

        self.scene = QGraphicsScene(self)
        self.view = _ZoomGraphicsView(self)
        self.view.setScene(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.set_zoom_callback(self._apply_zoom_factor)
        self.view.set_mouse_callbacks(
            self._on_view_mouse_press,
            self._on_view_mouse_move,
            self._on_view_mouse_release,
        )
        root.addWidget(self.view, 1)

        self.lbl_source = QLabel("Quelle")
        self.lbl_source.setVisible(False)
        root.addWidget(self.lbl_source)
        self.te_mermaid_source = QPlainTextEdit(self)
        self.te_mermaid_source.setReadOnly(True)
        self.te_mermaid_source.setMaximumBlockCount(2000)
        self.te_mermaid_source.setPlaceholderText("Mermaid-Definition")
        self.te_mermaid_source.setMinimumHeight(170)
        self.te_mermaid_source.setVisible(False)
        root.addWidget(self.te_mermaid_source)

        self.lbl_hint = QLabel(
            "Anzeige: state-artige AP-Karten mit Attributen; Kabel zeigen die Verbindungen."
        )
        root.addWidget(self.lbl_render_status)
        root.addWidget(self.lbl_hint)

        self.setCentralWidget(central)

        self.btn_add_ap.clicked.connect(self._open_add_ap_dialog)
        self.btn_add_cable.clicked.connect(self._open_add_cable_dialog)
        self.btn_del_ap.clicked.connect(self._open_delete_ap_dialog)
        self.btn_del_cable.clicked.connect(self._open_delete_cable_dialog)
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        self.btn_zoom_reset.clicked.connect(self._zoom_reset)
        self.btn_fit.clicked.connect(self._fit_to_content)
        self.cmb_root_ap.currentIndexChanged.connect(self._on_root_changed)
        self.chk_rooms.toggled.connect(self._on_room_zones_toggled)
        self._update_zoom_label()
        self._update_mode_indicator()

    def set_data(
        self,
        ap_nodes: list[ApNode],
        cable_edges: list[CableEdge],
        manual_positions: dict[str, tuple[float, float]] | None = None,
        room_choices: list[tuple[str, str]] | None = None,
        room_styles: dict[str, dict[str, str]] | None = None,
    ):
        self._ap_nodes = {n.point_id: n for n in ap_nodes}
        self._cable_edges = {e.cable_id: e for e in cable_edges}
        self._selected_ap_ids &= set(self._ap_nodes.keys())
        self._selected_cable_ids &= set(self._cable_edges.keys())
        if self._active_cable_id and self._active_cable_id not in self._selected_cable_ids:
            self._active_cable_id = next(iter(self._selected_cable_ids), None)
        if self._cable_pick_state is not None:
            pick_cable_id = str(self._cable_pick_state.get("cable_id", "") or "")
            if pick_cable_id not in self._cable_edges:
                self._cancel_cable_pick_mode()
        self._room_choices = list(room_choices or [])
        self._room_styles = dict(room_styles or {})
        self._manual_positions = {}
        self._refresh_root_choices()
        self._render()

    def _refresh_root_choices(self):
        previous = self._selected_root_ap_id if self._selected_root_ap_id in self._ap_nodes else ""
        labels = [
            (point_id, format_elec_point_choice_label(point_id, node.name or ""))
            for point_id, node in self._ap_nodes.items()
        ]
        labels.sort(key=lambda value: value[1].lower())

        blocker = QSignalBlocker(self.cmb_root_ap)
        self.cmb_root_ap.clear()
        self.cmb_root_ap.addItem("Automatisch (UV/zentral)", "")
        for point_id, text in labels:
            self.cmb_root_ap.addItem(text, point_id)

        if not previous:
            previous = self._guess_root_ap_id(set(self._ap_nodes.keys()))
        index = self.cmb_root_ap.findData(previous)
        if index < 0:
            index = 0
            previous = ""
        self.cmb_root_ap.setCurrentIndex(index)
        self._selected_root_ap_id = str(previous or "")
        del blocker

    def _on_root_changed(self, _index: int):
        self._selected_root_ap_id = str(self.cmb_root_ap.currentData() or "")
        self._render()

    def _on_layout_mode_changed(self, _index: int):
        self._layout_mode = str(self.cmb_layout_mode.currentData() or "radial")
        self._render()

    def _on_room_shape_changed(self, _index: int):
        self._room_shape_mode = str(self.cmb_room_shape.currentData() or "ellipse")
        self._render()

    def _on_room_zones_toggled(self, checked: bool):
        self._show_room_zones = bool(checked)
        self._render()

    def _on_room_colors_toggled(self, checked: bool):
        self._use_room_colors = bool(checked)
        self._render()

    def _on_compact_view_toggled(self, checked: bool):
        self._compact_view_enabled = bool(checked)
        self._render()

    def _on_show_cable_labels_toggled(self, checked: bool):
        self._show_all_cable_labels = bool(checked)
        self._render()

    def _on_tight_packing_toggled(self, checked: bool):
        self._tight_packing_enabled = bool(checked)
        self._render()

    @staticmethod
    def _safe_mermaid_id(raw_id: str) -> str:
        text = "".join(ch if ch.isalnum() else "_" for ch in str(raw_id or ""))
        text = text.strip("_") or "node"
        if text[0].isdigit():
            text = f"N_{text}"
        return text

    @staticmethod
    def _escape_mermaid_value(value: str) -> str:
        text = str(value or "")
        text = text.replace("\\", "\\\\")
        text = text.replace('"', '\\"')
        text = text.replace("\r", "")
        text = text.replace("\n", " ")
        text = text.replace(";", ",")
        text = text.replace("|", "/")
        return text

    @classmethod
    def _sanitize_mermaid_relation_label(cls, value: str) -> str:
        text = cls._escape_mermaid_value(value)
        text = text.replace(":", "_")
        return text.strip() or "Verbindung"

    @staticmethod
    def _ap_type_label(ap_type: str) -> str:
        mapping = {
            "standard": "Standard",
            "uv": "UV",
            "up_distribution": "UP-Verteilung",
            "hak": "HAK",
            "zaehler": "Zähler",
        }
        return mapping.get(str(ap_type or "").strip(), str(ap_type or "Standard").strip() or "Standard")

    def _state_node_title(self, node: ApNode) -> str:
        name = node.name or node.point_id
        if self._compact_view_enabled:
            name = self._compact_text(name, max_chars=28)
        return f"{name} ({node.point_id})"

    def _state_node_lines(self, node: ApNode) -> list[str]:
        lines = [
            f"Typ: {self._ap_type_label(node.ap_type)}",
            f"Raum: {node.room or '(ohne Raum)'}",
            f"Status: {'angeschlossen' if node.is_connected else 'offen'}",
        ]
        if not self._compact_view_enabled:
            lines.extend(
                [
                    f"Position: {node.position or '-'}",
                    f"Höhe: {node.height_from_floor:.0f} cm",
                    f"Verteiler: {'Ja' if node.has_distributor_function else 'Nein'}",
                ]
            )
            if node.smarthome_device:
                lines.append(f"UP-Gerät: {node.smarthome_device}")
            if node.note:
                lines.append(f"Notiz: {self._compact_text(node.note, max_chars=34)}")
        return lines

    def _ap_attribute_lines(self, node: ApNode) -> list[str]:
        lines = [
            f'id: {self._escape_mermaid_value(node.point_id)}',
            f'name: {self._escape_mermaid_value(node.name or node.point_id)}',
            f'type: {self._escape_mermaid_value(node.ap_type)}',
            f'connected: {"yes" if node.is_connected else "no"}',
        ]
        if self._show_room_zones:
            lines.append(f'room: {self._escape_mermaid_value(node.room or "(ohne Raum)")}')
        if self._use_room_colors:
            lines.extend(
                [
                    f'position: {self._escape_mermaid_value(node.position or "")}',
                    f'height_mm: {node.height_from_floor:.0f}',
                    f'smarthome: {self._escape_mermaid_value(node.smarthome_device or "-")}',
                ]
            )
            if not self._compact_view_enabled:
                lines.extend(
                    [
                        f'color: {self._escape_mermaid_value(node.color)}',
                        f'symbol: {self._escape_mermaid_value(node.builtin_symbol or "")}',
                        f'note: {self._escape_mermaid_value(node.note or "-")}',
                    ]
                )
        return lines

    def _build_mermaid_source(self) -> str:
        diagram = self._diagram_type or "class"
        ap_ids = sorted(self._ap_nodes.keys(), key=lambda pid: str(self._ap_nodes[pid].name or pid).lower())
        id_map = {point_id: self._safe_mermaid_id(point_id) for point_id in ap_ids}

        if diagram == "er":
            lines = ["erDiagram"]
            for point_id in ap_ids:
                node = self._ap_nodes[point_id]
                lines.append(f"    {id_map[point_id]} {{")
                for attr in self._ap_attribute_lines(node):
                    lines.append(f"        string {self._escape_mermaid_value(attr)}")
                lines.append("    }")
            for edge in self._cable_edges.values():
                start = str(edge.start_ap_id or "").strip()
                end = str(edge.end_ap_id or "").strip()
                if start in id_map and end in id_map:
                    label = self._sanitize_mermaid_relation_label(edge.name or edge.cable_id)
                    lines.append(f'    {id_map[start]} }}o--o{{ {id_map[end]} : {label}')
            return "\n".join(lines)

        lines = ["classDiagram", "    direction LR"]
        for point_id in ap_ids:
            node = self._ap_nodes[point_id]
            title = self._escape_mermaid_value(f"{node.name or point_id} ({point_id})")
            lines.append(f'    class {id_map[point_id]}["{title}"] {{')
            for attr in self._ap_attribute_lines(node):
                name, _, value = attr.partition(":")
                lines.append(
                    f'        {self._escape_mermaid_value(name.strip())}: {self._escape_mermaid_value(value.strip())}'
                )
            lines.append("    }")
        for edge in self._cable_edges.values():
            start = str(edge.start_ap_id or "").strip()
            end = str(edge.end_ap_id or "").strip()
            if start in id_map and end in id_map:
                label = self._sanitize_mermaid_relation_label(edge.name or edge.cable_id)
                lines.append(f'    {id_map[start]} --> {id_map[end]} : {label}')
        return "\n".join(lines)

    def _render_mermaid_svg_local(self, mermaid_source: str) -> tuple[bytes | None, str]:
        npx_path = shutil.which("npx")
        if not npx_path:
            return None, "Lokaler Renderer nicht verfügbar (npx fehlt)."

        try:
            with tempfile.TemporaryDirectory(prefix="hrouting_mermaid_") as temp_dir:
                temp_path = Path(temp_dir)
                source_path = temp_path / "diagram.mmd"
                output_path = temp_path / "diagram.svg"
                source_path.write_text(mermaid_source, encoding="utf-8")
                result = subprocess.run(
                    [
                        npx_path,
                        "-y",
                        "@mermaid-js/mermaid-cli",
                        "-q",
                        "-i",
                        str(source_path),
                        "-o",
                        str(output_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
                if result.returncode != 0:
                    stderr_text = (result.stderr or result.stdout or "").strip()
                    detail = stderr_text.splitlines()[0] if stderr_text else f"Exit-Code {result.returncode}"
                    return None, f"Lokales Rendering fehlgeschlagen: {detail}"
                if not output_path.exists():
                    return None, "Lokales Rendering fehlgeschlagen: SVG wurde nicht erzeugt."
                return output_path.read_bytes(), "Lokal mit Mermaid CLI gerendert."
        except subprocess.TimeoutExpired:
            return None, "Lokales Rendering abgebrochen: Timeout nach 90s."
        except OSError as exc:
            return None, f"Lokales Rendering fehlgeschlagen: {exc}"

    def _request_mermaid_svg(self, mermaid_source: str) -> bytes | None:
        remote_status = ""
        if self.__class__._remote_mermaid_available is not False:
            payload = json.dumps({"diagram_source": mermaid_source}).encode("utf-8")
            req = urllib.request.Request(
                "https://kroki.io/mermaid/svg",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 HRouting/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    self.__class__._remote_mermaid_available = True
                    self._mermaid_status = "Online mit Mermaid gerendert."
                    return response.read()
            except urllib.error.HTTPError as exc:
                self.__class__._remote_mermaid_available = False
                remote_status = f"Online-Rendering fehlgeschlagen: HTTP {exc.code}."
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                self.__class__._remote_mermaid_available = False
                remote_status = f"Online-Rendering fehlgeschlagen: {exc}."
        else:
            remote_status = "Online-Rendering uebersprungen: Endpoint zuvor fehlgeschlagen."

        svg_bytes, local_status = self._render_mermaid_svg_local(mermaid_source)
        if svg_bytes:
            self._mermaid_status = f"{remote_status} {local_status}".strip()
            return svg_bytes
        self._mermaid_status = f"{remote_status} {local_status}".strip()
        return None

    def _display_svg_bytes(self, svg_bytes: bytes):
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        if not renderer.isValid():
            return False
        size = renderer.defaultSize()
        width = int(size.width()) if size.isValid() and size.width() > 0 else 1400
        height = int(size.height()) if size.isValid() and size.height() > 0 else 900
        width = max(320, min(width, 2800))
        height = max(240, min(height, 2800))
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            renderer.render(painter)
        finally:
            painter.end()
        self.scene.clear()
        item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(item)
        self.scene.setSceneRect(QRectF(0.0, 0.0, float(width), float(height)))
        return True

    def _guess_root_ap_id(self, node_ids: set[str]) -> str:
        if not node_ids:
            return ""
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self._cable_edges.values():
            a = edge.start_ap_id.strip()
            b = edge.end_ap_id.strip()
            if a in node_ids and b in node_ids and a and b and a != b:
                adjacency[a].add(b)
                adjacency[b].add(a)
        uv_candidates = [nid for nid in node_ids if self._ap_nodes[nid].ap_type == "uv"]
        if uv_candidates:
            return max(uv_candidates, key=lambda nid: len(adjacency[nid]))
        return max(
            sorted(node_ids),
            key=lambda nid: (
                len(adjacency[nid]),
                -len(str(self._ap_nodes[nid].name or nid)),
            ),
        )

    def _open_add_ap_dialog(self):
        dlg = _AddApDialog(self._room_choices, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.add_ap_requested.emit(dlg.get_payload())

    def _open_add_cable_dialog(self):
        dlg = _AddCableDialog(self._ap_nodes, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.add_cable_requested.emit(dlg.get_payload())

    def _find_context_ids_at(self, scene_pos: QPointF) -> tuple[set[str], set[str]]:
        item = self.scene.itemAt(scene_pos, self.view.transform())
        while item is not None:
            if isinstance(item, _ApNodeItem):
                point_id = str(getattr(item, "_point_id", "") or "")
                return ({point_id} if point_id else set(), set())
            if isinstance(item, _CablePathItem):
                cable_id = str(getattr(item, "_cable_id", "") or "")
                return (set(), {cable_id} if cable_id else set())
            if isinstance(item, _CableEndpointHandle):
                cable_id = str(getattr(item, "_cable_id", "") or "")
                return (set(), {cable_id} if cable_id else set())
            item = item.parentItem()
        return set(), set()

    def _context_target_ids(self, clicked_ap_ids: set[str], clicked_cable_ids: set[str]) -> tuple[list[str], list[str]]:
        if self._selected_ap_ids or self._selected_cable_ids:
            ap_ids = sorted(self._selected_ap_ids)
            cable_ids = sorted(self._selected_cable_ids)
        else:
            ap_ids = sorted(clicked_ap_ids)
            cable_ids = sorted(clicked_cable_ids)
        return ap_ids, cable_ids

    def _delete_ids(self, ap_ids: list[str], cable_ids: list[str]):
        if self._cable_pick_state is not None:
            self._cancel_cable_pick_mode()
        for cable_id in cable_ids:
            if cable_id in self._cable_edges:
                self.delete_cable_requested.emit(cable_id)
        for point_id in ap_ids:
            if point_id in self._ap_nodes:
                self.delete_ap_requested.emit(point_id)

    def _copy_ids(self, ap_ids: list[str], cable_ids: list[str]):
        if not ap_ids and not cable_ids:
            self._copied_selection = None
            return
        self._copied_selection = {
            "ap_ids": list(ap_ids),
            "cable_ids": list(cable_ids),
        }

    def _paste_copied_ids(self):
        payload = self._copied_selection
        if not payload:
            return
        ap_ids = [pid for pid in payload.get("ap_ids", []) if pid in self._ap_nodes]
        cable_ids = [cid for cid in payload.get("cable_ids", []) if cid in self._cable_edges]
        if not ap_ids and not cable_ids:
            return
        self.duplicate_selection_requested.emit(ap_ids, cable_ids)

    def _open_context_menu(self, scene_pos: QPointF, global_pos):
        clicked_ap_ids, clicked_cable_ids = self._find_context_ids_at(scene_pos)
        if (
            not self._selected_ap_ids
            and not self._selected_cable_ids
            and (clicked_ap_ids or clicked_cable_ids)
        ):
            active_cable = next(iter(clicked_cable_ids), None)
            self._set_selection(set(clicked_ap_ids), set(clicked_cable_ids), active_cable)

        target_ap_ids, target_cable_ids = self._context_target_ids(clicked_ap_ids, clicked_cable_ids)

        menu = QMenu(self)
        add_ap_action = menu.addAction("➕ AP hinzufügen")
        add_cable_action = menu.addAction("➕ Kabel hinzufügen")
        menu.addSeparator()
        delete_action = menu.addAction("🗑 Löschen")
        copy_action = menu.addAction("📋 Kopieren")
        paste_action = menu.addAction("📥 Einfügen")

        can_apply_target = bool(target_ap_ids or target_cable_ids)
        delete_action.setEnabled(can_apply_target)
        copy_action.setEnabled(can_apply_target)
        paste_action.setEnabled(self._copied_selection is not None)

        chosen = menu.exec(global_pos.toPoint() if hasattr(global_pos, "toPoint") else global_pos)
        if chosen is None:
            return
        if chosen == add_ap_action:
            self._open_add_ap_dialog()
            return
        if chosen == add_cable_action:
            self._open_add_cable_dialog()
            return
        if chosen == delete_action:
            self._delete_ids(target_ap_ids, target_cable_ids)
            return
        if chosen == copy_action:
            self._copy_ids(target_ap_ids, target_cable_ids)
            return
        if chosen == paste_action:
            self._paste_copied_ids()
            return

    def start_cable_pick_mode(self, cable_id: str):
        edge = self._cable_edges.get(cable_id)
        if edge is None:
            return

        start_ap_id = str(edge.start_ap_id or "").strip()
        end_ap_id = str(edge.end_ap_id or "").strip()
        if start_ap_id and end_ap_id:
            self._cancel_cable_pick_mode()
            return

        if not start_ap_id and not end_ap_id:
            pending = ["start", "end"]
        elif not start_ap_id:
            pending = ["start"]
        else:
            pending = ["end"]

        self._cable_pick_state = {
            "cable_id": cable_id,
            "pending": pending,
        }
        self._set_selection(set(), {cable_id}, cable_id)
        self._update_mode_indicator()

    def _cancel_cable_pick_mode(self):
        self._cable_pick_state = None
        self._update_mode_indicator()

    def _update_mode_indicator(self):
        state = self._cable_pick_state
        if not state:
            self.lbl_mode.clear()
            self.lbl_mode.setVisible(False)
            self.view.unsetCursor()
            self.lbl_hint.setText(
                "Anzeige: state-artige AP-Karten mit Attributen; Kabel: Name, Typ, Länge"
            )
            self._apply_selection_visuals()
            return

        cable_id = str(state.get("cable_id", "") or "")
        pending = list(state.get("pending", []))
        if pending:
            next_label = "Start-AP" if pending[0] == "start" else "End-AP"
        else:
            next_label = "AP"

        self.lbl_mode.setText(f"🧲 Kabel-Ziehmodus: {cable_id} – wähle {next_label} (ESC = Abbrechen)")
        self.lbl_mode.setVisible(True)
        self.view.setCursor(Qt.CursorShape.CrossCursor)
        self.lbl_hint.setText(
            "Kabel-Ziehmodus aktiv: Klick auf AP setzt fehlenden Anschluss. ESC bricht ab."
        )
        self._apply_selection_visuals()

    def _cable_pick_locked_ap_id(self) -> str | None:
        state = self._cable_pick_state
        if not state:
            return None
        cable_id = str(state.get("cable_id", "") or "")
        edge = self._cable_edges.get(cable_id)
        if edge is None:
            return None
        pending = list(state.get("pending", []))
        if not pending:
            return None
        if pending[0] == "start":
            ap_id = str(edge.end_ap_id or "").strip()
        else:
            ap_id = str(edge.start_ap_id or "").strip()
        return ap_id or None

    def _apply_cable_pick_click(self, point_id: str):
        state = self._cable_pick_state
        if not state:
            return

        cable_id = str(state.get("cable_id", "") or "")
        edge = self._cable_edges.get(cable_id)
        if edge is None:
            self._cancel_cable_pick_mode()
            return

        pending = list(state.get("pending", []))
        if not pending:
            self._cancel_cable_pick_mode()
            return

        endpoint = pending.pop(0)
        start_ap_id = str(edge.start_ap_id or "").strip()
        end_ap_id = str(edge.end_ap_id or "").strip()
        if endpoint == "start":
            start_ap_id = point_id
            edge.start_ap_id = point_id
        else:
            end_ap_id = point_id
            edge.end_ap_id = point_id

        payload = {
            "name": edge.name,
            "type": edge.cable_type,
            "color": edge.color,
            "visible": edge.visible,
            "label_visible": edge.label_visible,
            "type_label_visible": edge.type_label_visible,
            "label_size": edge.label_size,
            "stroke_width": edge.stroke_width_px,
            "line_style": edge.line_style,
            "start_ap_id": start_ap_id,
            "end_ap_id": end_ap_id,
            "comment": edge.comment,
        }
        self.edit_cable_requested.emit(cable_id, payload)

        if pending:
            self._cable_pick_state = {
                "cable_id": cable_id,
                "pending": pending,
            }
        else:
            self._cable_pick_state = None
        self._update_mode_indicator()

    def _open_delete_ap_dialog(self):
        items = sorted(
            [(pid, n.name or pid) for pid, n in self._ap_nodes.items()],
            key=lambda v: v[1].lower(),
        )
        if not items:
            QMessageBox.information(self, "AP löschen", "Keine Anschlusspunkte vorhanden.")
            return
        dlg = _DeleteSelectDialog("AP löschen", "Anschlusspunkt:", items, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        point_id = dlg.selected_id()
        if not point_id:
            return
        reply = QMessageBox.question(
            self,
            "Löschen bestätigen",
            f"Anschlusspunkt '{point_id}' wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_ap_requested.emit(point_id)

    def _open_delete_cable_dialog(self):
        items = sorted(
            [(cid, e.name or cid) for cid, e in self._cable_edges.items()],
            key=lambda v: v[1].lower(),
        )
        if not items:
            QMessageBox.information(self, "Kabel löschen", "Keine Kabel vorhanden.")
            return
        dlg = _DeleteSelectDialog("Kabel löschen", "Kabel:", items, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cable_id = dlg.selected_id()
        if not cable_id:
            return
        reply = QMessageBox.question(
            self,
            "Löschen bestätigen",
            f"Kabel '{cable_id}' wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_cable_requested.emit(cable_id)

    def _render(self):
        self._is_rendering = True
        self._clear_selection_rect()
        self._clear_rewire_preview()
        self._cable_rewire_state = None
        self._ap_scene_positions.clear()
        self._cable_endpoints_scene.clear()
        self._ap_items.clear()
        self._cable_items.clear()
        self._cable_handle_items.clear()
        self._room_zone_defs = []
        self.scene.clear()
        if not self._ap_nodes and not self._cable_edges:
            self.scene.addSimpleText("Keine APs/Kabel vorhanden.")
            self._is_rendering = False
            return

        positions = self._compute_layout_positions()
        if self._show_room_zones:
            self._draw_room_zones()
        self._draw_cables(positions)
        self._draw_nodes(positions)

        bounds = self._content_bounds(include_room_zones=False).adjusted(-40, -40, 40, 40)
        if bounds.isNull() or bounds.width() <= 0 or bounds.height() <= 0:
            bounds = self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        self.scene.setSceneRect(bounds)
        self.view.centerOn(bounds.center())
        self._is_rendering = False

    def _zoom_in(self):
        self._apply_zoom_factor(self._zoom_step)

    def _zoom_out(self):
        factor = 1.0 / self._zoom_step
        self._apply_zoom_factor(factor)

    def _zoom_reset(self):
        self.view.resetTransform()
        self._update_zoom_label()

    def _apply_zoom_factor(self, factor: float) -> bool:
        if factor <= 0:
            return False
        current = float(self.view.transform().m11())
        target = current * factor
        if target < self._zoom_min:
            factor = self._zoom_min / current if current > 0 else 1.0
        elif target > self._zoom_max:
            factor = self._zoom_max / current if current > 0 else 1.0

        if abs(factor - 1.0) < 1e-9:
            return False
        self.view.scale(factor, factor)
        self._update_zoom_label()
        return True

    def _content_bounds(self, include_room_zones: bool = False) -> QRectF:
        rect = QRectF()
        for item in self.scene.items():
            if not include_room_zones and str(item.data(0) or "").startswith("room-zone"):
                continue
            item_rect = item.sceneBoundingRect()
            if item_rect.isNull() or item_rect.width() <= 0 or item_rect.height() <= 0:
                continue
            rect = item_rect if rect.isNull() else rect.united(item_rect)
        return rect

    def _fit_to_content(self):
        rect = self._content_bounds(include_room_zones=False)
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        padded = rect.adjusted(-40, -40, 40, 40)
        self.view.fitInView(padded, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_zoom_label()

    def _update_zoom_label(self):
        zoom = float(self.view.transform().m11())
        percent = int(round(zoom * 100.0))
        self.lbl_zoom.setText(f"{percent}%")

    def _compute_layout_positions(self) -> dict[str, tuple[float, float]]:
        """Berechnet ein baumartiges Layout mit separaten Nebenverbindungen."""
        positions: dict[str, tuple[float, float]] = {}

        nodes = set(self._ap_nodes.keys())
        adjacency: dict[str, set[str]] = defaultdict(set)
        edge_lookup: dict[frozenset[str], list[str]] = defaultdict(list)
        for edge in self._cable_edges.values():
            a = edge.start_ap_id.strip()
            b = edge.end_ap_id.strip()
            if a and b and a in nodes and b in nodes and a != b:
                adjacency[a].add(b)
                adjacency[b].add(a)
                edge_lookup[frozenset((a, b))].append(edge.cable_id)

        unvisited = set(nodes)
        components: list[list[str]] = []
        while unvisited:
            seed = next(iter(unvisited))
            queue = deque([seed])
            unvisited.remove(seed)
            component: list[str] = []
            while queue:
                cur = queue.popleft()
                component.append(cur)
                for nxt in adjacency[cur]:
                    if nxt in unvisited:
                        unvisited.remove(nxt)
                        queue.append(nxt)
            components.append(component)

        self._tree_edge_ids.clear()
        self._cross_edge_ids.clear()

        max_node_h = max((max(56.0, float(node.height_px)) for node in self._ap_nodes.values()), default=56.0)
        if self._tight_packing_enabled:
            level_gap_y = max(124.0, max_node_h + 56.0)
            base_gap_x = 18.0
            component_gap_x = 96.0
        else:
            level_gap_y = max(152.0, max_node_h + 84.0)
            base_gap_x = 28.0
            component_gap_x = 180.0

        def _node_span_x(node_id: str) -> float:
            box_w, _box_h = self._node_collision_size(self._ap_nodes[node_id])
            return box_w + 24.0

        def _node_sort_key(node_id: str) -> tuple[int, str]:
            return (-len(adjacency[node_id]), str(self._ap_nodes[node_id].name or node_id).lower())

        selected_root_component = None
        if self._selected_root_ap_id:
            for component in components:
                if self._selected_root_ap_id in component:
                    selected_root_component = set(component)
                    break

        sorted_components = sorted(
            components,
            key=lambda c: (
                0 if selected_root_component is not None and any(node_id in selected_root_component for node_id in c) else 1,
                -len(c),
            ),
        )
        base_x = 0.0
        primary_component_radius = 0.0
        deferred_radial_components: list[tuple[list[str], dict[str, tuple[float, float]], float, float]] = []
        for component_index, component in enumerate(sorted_components):
            component_set = set(component)
            if self._selected_root_ap_id and self._selected_root_ap_id in component_set:
                root = self._selected_root_ap_id
            else:
                root = self._guess_root_ap_id(component_set)

            levels: dict[str, int] = {root: 0}
            parents: dict[str, str] = {root: ""}
            tree_adj: dict[str, set[str]] = defaultdict(set)
            queue = deque([root])
            visited = {root}
            while queue:
                cur = queue.popleft()
                neighbors = sorted(adjacency[cur], key=_node_sort_key)
                for nxt in neighbors:
                    if nxt not in visited:
                        visited.add(nxt)
                        levels[nxt] = levels[cur] + 1
                        parents[nxt] = cur
                        tree_adj[cur].add(nxt)
                        tree_adj[nxt].add(cur)
                        ids = sorted(edge_lookup.get(frozenset((cur, nxt)), []))
                        if ids:
                            self._tree_edge_ids.add(ids[0])
                        queue.append(nxt)

            for node_id in component:
                if node_id not in levels:
                    levels[node_id] = 0

            level_nodes: dict[int, list[str]] = defaultdict(list)
            for node_id in component:
                level_nodes[levels[node_id]].append(node_id)

            max_level = max(level_nodes.keys()) if level_nodes else 0
            for level in range(max_level + 1):
                level_nodes[level].sort(key=_node_sort_key)

            def _ordered_neighbor_positions(target_level: int) -> dict[str, int]:
                return {node_id: idx for idx, node_id in enumerate(level_nodes[target_level])}

            for _ in range(8):
                for level in range(1, max_level + 1):
                    prev_order = _ordered_neighbor_positions(level - 1)

                    def _down_key(nid: str):
                        neigh = [prev_order[n] for n in tree_adj[nid] if levels[n] == level - 1]
                        if neigh:
                            return (sum(neigh) / len(neigh), -len(adjacency[nid]))
                        return (1e9, -len(adjacency[nid]))

                    level_nodes[level].sort(key=_down_key)

                for level in range(max_level - 1, -1, -1):
                    next_order = _ordered_neighbor_positions(level + 1)

                    def _up_key(nid: str):
                        neigh = [next_order[n] for n in tree_adj[nid] if levels[n] == level + 1]
                        if neigh:
                            return (sum(neigh) / len(neigh), -len(adjacency[nid]))
                        return (1e9, -len(adjacency[nid]))

                    level_nodes[level].sort(key=_up_key)

            if self._layout_mode == "radial":
                level_index: dict[str, int] = {}
                for level in range(max_level + 1):
                    for idx, node_id in enumerate(level_nodes[level]):
                        level_index[node_id] = idx

                children: dict[str, list[str]] = defaultdict(list)
                for node_id, parent_id in parents.items():
                    if parent_id:
                        children[parent_id].append(node_id)
                for parent_id in children.keys():
                    children[parent_id].sort(key=lambda nid: level_index.get(nid, 0))

                leaf_weight_cache: dict[str, float] = {}

                def _leaf_weight(node_id: str) -> float:
                    cached = leaf_weight_cache.get(node_id)
                    if cached is not None:
                        return cached
                    kids = children.get(node_id, [])
                    if not kids:
                        leaf_weight_cache[node_id] = 1.0
                        return 1.0
                    value = sum(_leaf_weight(child_id) for child_id in kids)
                    leaf_weight_cache[node_id] = max(1.0, value)
                    return leaf_weight_cache[node_id]

                node_angle: dict[str, float] = {}
                node_span: dict[str, tuple[float, float]] = {}

                def _assign_angles(node_id: str, start_angle: float, end_angle: float):
                    mid = (start_angle + end_angle) / 2.0
                    node_angle[node_id] = mid
                    node_span[node_id] = (start_angle, end_angle)
                    kids = children.get(node_id, [])
                    if not kids:
                        return
                    total = sum(_leaf_weight(child_id) for child_id in kids)
                    cursor = start_angle
                    for child_id in kids:
                        span = (end_angle - start_angle) * (_leaf_weight(child_id) / max(1e-9, total))
                        _assign_angles(child_id, cursor, cursor + span)
                        cursor += span

                node_angle[root] = -math.pi / 2.0
                node_span[root] = (-math.pi, math.pi)

                root_children = children.get(root, [])
                if root_children:
                    branch_gap = min(0.24, (2.0 * math.pi) / max(14.0, len(root_children) * 3.0))
                    total_gap = branch_gap * len(root_children)
                    usable_span = max(math.pi * 1.45, (2.0 * math.pi) - total_gap)
                    total_weight = sum(_leaf_weight(child_id) for child_id in root_children)
                    cursor = -math.pi / 2.0 - usable_span / 2.0
                    for child_id in root_children:
                        share = usable_span * (_leaf_weight(child_id) / max(1e-9, total_weight))
                        _assign_angles(child_id, cursor, cursor + share)
                        cursor += share + branch_gap

                radii: dict[int, float] = {0: 0.0}
                max_node_w = max(
                    (self._node_collision_size(self._ap_nodes[node_id])[0] for node_id in component),
                    default=180.0,
                )
                radial_min_step = max(132.0, max_node_h + (32.0 if self._tight_packing_enabled else 54.0))
                for level in range(1, max_level + 1):
                    nodes_on_level = level_nodes[level]
                    if not nodes_on_level:
                        radii[level] = radii[level - 1] + radial_min_step
                        continue
                    required_radius = radii[level - 1] + radial_min_step
                    for node_id in nodes_on_level:
                        start_a, end_a = node_span.get(node_id, (0.0, 0.0))
                        angular_span = max(0.22, end_a - start_a)
                        local_need = (max_node_w * 0.72) / max(0.22, angular_span)
                        required_radius = max(required_radius, local_need)
                    radii[level] = required_radius

                center_x = 0.0
                center_y = 0.0
                positions[root] = (center_x, center_y)
                for node_id in component:
                    if node_id == root:
                        continue
                    angle = node_angle.get(node_id, 0.0)
                    radius = radii.get(levels[node_id], 0.0)
                    positions[node_id] = (
                        center_x + radius * math.cos(angle),
                        center_y + radius * math.sin(angle),
                    )

                for parent_id, child_ids in children.items():
                    if not child_ids:
                        continue
                    if len(child_ids) == 1:
                        continue
                    px, py = positions[parent_id]
                    start_a, end_a = node_span.get(parent_id, (node_angle.get(parent_id, 0.0), node_angle.get(parent_id, 0.0)))
                    child_radius = radii.get(levels[child_ids[0]], radii.get(levels[parent_id] + 1, radial_min_step))
                    margin = min(0.18, (end_a - start_a) * 0.12)
                    usable_start = start_a + margin
                    usable_end = end_a - margin
                    if usable_end <= usable_start:
                        usable_start = start_a
                        usable_end = end_a
                    for idx, child_id in enumerate(child_ids):
                        t = (idx + 1) / (len(child_ids) + 1)
                        angle = usable_start + (usable_end - usable_start) * t
                        positions[child_id] = (
                            px + (child_radius - radii.get(levels[parent_id], 0.0)) * math.cos(angle),
                            py + (child_radius - radii.get(levels[parent_id], 0.0)) * math.sin(angle),
                        )

                self._resolve_component_overlaps(
                    component,
                    positions,
                    root,
                    center=(center_x, center_y),
                    levels=levels,
                    radial_level_min_radius=radii,
                )

                min_x = float("inf")
                max_x = float("-inf")
                for node_id in component:
                    px, _py = positions[node_id]
                    node_w, _node_h = self._node_collision_size(self._ap_nodes[node_id])
                    half_w = node_w / 2.0
                    min_x = min(min_x, px - half_w)
                    max_x = max(max_x, px + half_w)

                min_y = float("inf")
                max_y = float("-inf")
                for node_id in component:
                    px, py = positions[node_id]
                    node_w, node_h = self._node_collision_size(self._ap_nodes[node_id])
                    half_w = node_w / 2.0
                    half_h = node_h / 2.0
                    min_x = min(min_x, px - half_w)
                    max_x = max(max_x, px + half_w)
                    min_y = min(min_y, py - half_h)
                    max_y = max(max_y, py + half_h)

                component_radius = max(radii.values(), default=0.0) + 140.0
                estimated_width = max(240.0, component_radius * 2.0)
                component_width = max(estimated_width, (max_x - min_x) + 80.0)
                component_height = max(220.0, (max_y - min_y) + 80.0)

                local_positions = {node_id: positions[node_id] for node_id in component}
                if component_index == 0:
                    primary_component_radius = max(component_radius, 0.5 * max(component_width, component_height))
                else:
                    deferred_radial_components.append((list(component), local_positions, component_width, component_height))
            else:
                level_index: dict[str, int] = {}
                for level in range(max_level + 1):
                    for idx, node_id in enumerate(level_nodes[level]):
                        level_index[node_id] = idx

                children: dict[str, list[str]] = defaultdict(list)
                for node_id, parent_id in parents.items():
                    if parent_id:
                        children[parent_id].append(node_id)
                for parent_id, child_ids in children.items():
                    child_ids.sort(key=lambda nid: level_index.get(nid, 0))

                subtree_widths: dict[str, float] = {}

                def _subtree_width(node_id: str) -> float:
                    cached = subtree_widths.get(node_id)
                    if cached is not None:
                        return cached
                    self_width = _node_span_x(node_id)
                    child_ids = children.get(node_id, [])
                    if not child_ids:
                        subtree_widths[node_id] = self_width
                        return self_width
                    total_child_width = sum(_subtree_width(child_id) for child_id in child_ids)
                    total_child_width += max(0, len(child_ids) - 1) * base_gap_x
                    subtree_widths[node_id] = max(self_width, total_child_width)
                    return subtree_widths[node_id]

                def _assign_tree(node_id: str, left_x: float):
                    width = _subtree_width(node_id)
                    depth = levels.get(node_id, 0)
                    center_x = left_x + width / 2.0
                    positions[node_id] = (center_x, depth * level_gap_y)
                    child_ids = children.get(node_id, [])
                    if not child_ids:
                        return
                    total_child_width = sum(_subtree_width(child_id) for child_id in child_ids)
                    total_child_width += max(0, len(child_ids) - 1) * base_gap_x
                    child_left = center_x - total_child_width / 2.0
                    for child_id in child_ids:
                        _assign_tree(child_id, child_left)
                        child_left += _subtree_width(child_id) + base_gap_x

                component_width = max(220.0, _subtree_width(root))
                _assign_tree(root, base_x - component_width / 2.0)

                self._resolve_component_overlaps(component, positions, root)

                min_x = float("inf")
                max_x = float("-inf")
                for node_id in component:
                    px, _py = positions[node_id]
                    node_w, _node_h = self._node_collision_size(self._ap_nodes[node_id])
                    half_w = node_w / 2.0
                    min_x = min(min_x, px - half_w)
                    max_x = max(max_x, px + half_w)
                component_width = max(component_width, (max_x - min_x) + 80.0)

            for edge in self._cable_edges.values():
                a = edge.start_ap_id.strip()
                b = edge.end_ap_id.strip()
                if edge.cable_id in self._tree_edge_ids:
                    continue
                if a in component_set and b in component_set and a and b and a != b:
                    self._cross_edge_ids.add(edge.cable_id)

            if self._layout_mode != "radial":
                base_x += component_width + component_gap_x

        if self._layout_mode == "radial" and deferred_radial_components:
            max_cell_width = max(width for _component, _positions, width, _height in deferred_radial_components)
            max_cell_height = max(height for _component, _positions, _width, height in deferred_radial_components)
            cell_width = max_cell_width + 70.0
            cell_height = max_cell_height + 60.0
            cols = max(1, min(4, int(math.ceil(math.sqrt(len(deferred_radial_components))))))
            grid_origin_y = primary_component_radius + cell_height * 0.75

            for idx, (component, local_positions, _width, _height) in enumerate(deferred_radial_components):
                row = idx // cols
                col = idx % cols
                target_x = (col - (cols - 1) / 2.0) * cell_width
                target_y = grid_origin_y + row * cell_height

                min_x = float("inf")
                min_y = float("inf")
                max_x = float("-inf")
                max_y = float("-inf")
                for node_id in component:
                    px, py = local_positions[node_id]
                    node_w, node_h = self._node_collision_size(self._ap_nodes[node_id])
                    min_x = min(min_x, px - node_w / 2.0)
                    min_y = min(min_y, py - node_h / 2.0)
                    max_x = max(max_x, px + node_w / 2.0)
                    max_y = max(max_y, py + node_h / 2.0)
                center_local_x = (min_x + max_x) / 2.0
                center_local_y = (min_y + max_y) / 2.0

                for node_id in component:
                    px, py = local_positions[node_id]
                    positions[node_id] = (px - center_local_x + target_x, py - center_local_y + target_y)

        self._room_zone_defs = self._build_room_zone_defs(positions)

        return positions

    @staticmethod
    def _alternating_lane_slot(index: int) -> int:
        if index <= 0:
            return 0
        step = (index + 1) // 2
        return step if index % 2 == 1 else -step

    @staticmethod
    def _compact_text(value: str, max_chars: int = 22) -> str:
        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max(1, max_chars - 3)] + "..."

    def _node_collision_size(self, node: ApNode) -> tuple[float, float]:
        title = self._state_node_title(node)
        lines = self._state_node_lines(node)
        icon_size = 28.0
        title_w = len(title) * 7.2 + 30.0 + icon_size
        lines_w = max((len(line) for line in lines), default=10) * 6.6 + 28.0
        width = max(180.0, title_w, lines_w)
        header_h = 38.0
        line_h = 17.0 if self._compact_view_enabled else 18.0
        height = header_h + 10.0 + len(lines) * line_h + 12.0
        return width, max(92.0, height)

    def _node_collision_radius(self, node_id: str) -> float:
        node = self._ap_nodes[node_id]
        box_w, box_h = self._node_collision_size(node)
        return max(30.0, 0.5 * math.hypot(box_w, box_h))

    def _resolve_component_overlaps(
        self,
        component: list[str],
        positions: dict[str, tuple[float, float]],
        root_id: str,
        center: tuple[float, float] | None = None,
        levels: dict[str, int] | None = None,
        radial_level_min_radius: dict[int, float] | None = None,
    ):
        if len(component) <= 1:
            return

        ids = [node_id for node_id in component if node_id in positions]
        if len(ids) <= 1:
            return

        radii = {node_id: self._node_collision_radius(node_id) for node_id in ids}

        for _ in range(140):
            delta: dict[str, list[float]] = {node_id: [0.0, 0.0] for node_id in ids}
            moved = 0.0
            for left_idx in range(len(ids)):
                left_id = ids[left_idx]
                x1, y1 = positions[left_id]
                r1 = radii[left_id]
                for right_idx in range(left_idx + 1, len(ids)):
                    right_id = ids[right_idx]
                    x2, y2 = positions[right_id]
                    r2 = radii[right_id]
                    dx = x2 - x1
                    dy = y2 - y1
                    dist = math.hypot(dx, dy)
                    required = r1 + r2 + 18.0
                    if dist >= required:
                        continue

                    if dist <= 1e-9:
                        seed = (left_idx * 1103515245 + right_idx * 12345) % 360
                        angle = math.radians(float(seed))
                        ux = math.cos(angle)
                        uy = math.sin(angle)
                    else:
                        ux = dx / dist
                        uy = dy / dist

                    push = (required - dist) * 0.54
                    left_weight = 0.5
                    right_weight = 0.5
                    if left_id == root_id and right_id != root_id:
                        left_weight = 0.12
                        right_weight = 0.88
                    elif right_id == root_id and left_id != root_id:
                        left_weight = 0.88
                        right_weight = 0.12

                    delta[left_id][0] -= ux * push * left_weight
                    delta[left_id][1] -= uy * push * left_weight
                    delta[right_id][0] += ux * push * right_weight
                    delta[right_id][1] += uy * push * right_weight
                    moved += push

            for node_id in ids:
                x, y = positions[node_id]
                dx, dy = delta[node_id]
                positions[node_id] = (x + dx, y + dy)

            if center is not None and levels is not None and radial_level_min_radius is not None:
                cx, cy = center
                for node_id in ids:
                    if node_id == root_id:
                        positions[node_id] = (cx, cy)
                        continue
                    level = int(levels.get(node_id, 0))
                    min_radius = float(radial_level_min_radius.get(level, 0.0))
                    x, y = positions[node_id]
                    vx = x - cx
                    vy = y - cy
                    dist = math.hypot(vx, vy)
                    if dist < max(1.0, min_radius):
                        if dist <= 1e-9:
                            angle = ((sum(ord(ch) for ch in node_id) % 360) / 180.0) * math.pi
                            vx = math.cos(angle)
                            vy = math.sin(angle)
                            dist = 1.0
                        scale = min_radius / max(1e-9, dist)
                        positions[node_id] = (cx + vx * scale, cy + vy * scale)

            if moved < 0.2:
                break

    @staticmethod
    def _normalize_room_name(room_name: str) -> str:
        text = str(room_name or "").strip()
        if not text or text == "(ohne Raum)":
            return ""
        return text

    def _room_group_key(self, node: ApNode) -> str:
        room_id = str(node.room_id or "").strip()
        if room_id:
            return room_id
        return self._normalize_room_name(node.room)

    def _room_style_for_key(self, room_key: str, fallback_name: str) -> dict[str, str]:
        if room_key in self._room_styles:
            return self._room_styles[room_key]
        for candidate in self._room_styles.values():
            if str(candidate.get("name", "")).strip() == str(fallback_name or "").strip():
                return candidate
        return {"name": str(fallback_name or room_key), "color": ""}

    def _arrange_room_nodes(
        self,
        node_ids: list[str],
        positions: dict[str, tuple[float, float]],
        root_id: str,
    ):
        if not node_ids:
            return

        anchor_x = sum(positions[node_id][0] for node_id in node_ids) / max(1, len(node_ids))
        anchor_y = sum(positions[node_id][1] for node_id in node_ids) / max(1, len(node_ids))

        max_r = max((self._node_collision_radius(node_id) for node_id in node_ids), default=40.0)
        root_in_room = root_id in node_ids
        outer_nodes = [node_id for node_id in node_ids if node_id != root_id] if root_in_room else list(node_ids)
        if not outer_nodes:
            positions[root_id] = (anchor_x, anchor_y)
            return

        target_count = len(outer_nodes)
        rx = max(110.0, (target_count * (max_r * 1.8)) / (2.0 * math.pi) + 20.0)
        ry = max(80.0, rx * 0.68)

        outer_nodes.sort(
            key=lambda node_id: math.atan2(
                positions[node_id][1] - anchor_y,
                positions[node_id][0] - anchor_x,
            )
        )

        if root_in_room:
            positions[root_id] = (anchor_x, anchor_y)

        for idx, node_id in enumerate(outer_nodes):
            angle = -math.pi / 2.0 + (2.0 * math.pi * idx / max(1, target_count))
            positions[node_id] = (
                anchor_x + rx * math.cos(angle),
                anchor_y + ry * math.sin(angle),
            )

    def _arrange_component_by_rooms(
        self,
        component: list[str],
        positions: dict[str, tuple[float, float]],
        root_id: str,
    ):
        grouped: dict[str, list[str]] = defaultdict(list)
        for node_id in component:
            room_key = self._room_group_key(self._ap_nodes[node_id])
            if room_key:
                grouped[room_key].append(node_id)

        for room_nodes in grouped.values():
            if len(room_nodes) <= 1:
                continue
            self._arrange_room_nodes(room_nodes, positions, root_id)

    def _separate_room_groups(
        self,
        component: list[str],
        positions: dict[str, tuple[float, float]],
        root_id: str,
    ):
        room_groups: dict[str, list[str]] = defaultdict(list)
        for node_id in component:
            room_key = self._room_group_key(self._ap_nodes[node_id])
            if room_key:
                room_groups[room_key].append(node_id)
        if len(room_groups) <= 1:
            return

        gap_pad = 110.0 if not self._tight_packing_enabled else 48.0
        for _ in range(80):
            centers: dict[str, tuple[float, float, float, float]] = {}
            for room_key, node_ids in room_groups.items():
                min_x = float("inf")
                min_y = float("inf")
                max_x = float("-inf")
                max_y = float("-inf")
                for node_id in node_ids:
                    x, y = positions[node_id]
                    box_w, box_h = self._node_collision_size(self._ap_nodes[node_id])
                    min_x = min(min_x, x - box_w / 2.0)
                    min_y = min(min_y, y - box_h / 2.0)
                    max_x = max(max_x, x + box_w / 2.0)
                    max_y = max(max_y, y + box_h / 2.0)
                centers[room_key] = (
                    (min_x + max_x) / 2.0,
                    (min_y + max_y) / 2.0,
                    (max_x - min_x) / 2.0 + gap_pad,
                    (max_y - min_y) / 2.0 + gap_pad * 0.75,
                )

            deltas: dict[str, list[float]] = {room_key: [0.0, 0.0] for room_key in room_groups}
            moved = 0.0
            room_keys = list(room_groups.keys())
            for left_idx in range(len(room_keys)):
                left_key = room_keys[left_idx]
                lx, ly, lrx, lry = centers[left_key]
                for right_idx in range(left_idx + 1, len(room_keys)):
                    right_key = room_keys[right_idx]
                    rx, ry, rrx, rry = centers[right_key]
                    dx = rx - lx
                    dy = ry - ly
                    norm = math.hypot(dx, dy)
                    ux = dx / norm if norm > 1e-9 else 1.0
                    uy = dy / norm if norm > 1e-9 else 0.0
                    overlap_y = (lry + rry) - abs(dy)
                    overlap_x = (lrx + rrx) - abs(dx)
                    if overlap_x <= 0.0 or overlap_y <= 0.0:
                        continue
                    push = min(overlap_x, overlap_y) * 0.35
                    deltas[left_key][0] -= ux * push
                    deltas[left_key][1] -= uy * push
                    deltas[right_key][0] += ux * push
                    deltas[right_key][1] += uy * push
                    moved += push

            for room_key, node_ids in room_groups.items():
                dx, dy = deltas[room_key]
                if abs(dx) <= 1e-9 and abs(dy) <= 1e-9:
                    continue
                contains_root = root_id in node_ids
                weight = 0.35 if contains_root else 1.0
                for node_id in node_ids:
                    if node_id == root_id:
                        continue
                    x, y = positions[node_id]
                    positions[node_id] = (x + dx * weight, y + dy * weight)
            if moved < 0.5:
                break

    def _build_room_zone_defs(self, positions: dict[str, tuple[float, float]]) -> list[dict]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for node_id, node in self._ap_nodes.items():
            if node_id not in positions:
                continue
            room_key = self._room_group_key(node)
            if not room_key:
                continue
            grouped[room_key].append(node_id)

        zones: list[dict] = []
        for room_key, node_ids in grouped.items():
            room_style = self._room_style_for_key(room_key, self._ap_nodes[node_ids[0]].room)
            room_name = str(room_style.get("name", self._ap_nodes[node_ids[0]].room or room_key) or room_key)
            min_x = float("inf")
            min_y = float("inf")
            max_x = float("-inf")
            max_y = float("-inf")
            for node_id in node_ids:
                x, y = positions[node_id]
                box_w, box_h = self._node_collision_size(self._ap_nodes[node_id])
                half_w = box_w / 2.0
                half_h = box_h / 2.0
                min_x = min(min_x, x - half_w)
                min_y = min(min_y, y - half_h)
                max_x = max(max_x, x + half_w)
                max_y = max(max_y, y + half_h)

            pad_x = 42.0 if not self._tight_packing_enabled else 24.0
            pad_y = 30.0 if not self._tight_packing_enabled else 18.0
            rx = max(54.0, (max_x - min_x) / 2.0 + pad_x)
            ry = max(42.0, (max_y - min_y) / 2.0 + pad_y)
            if self._room_shape_mode == "circle":
                radius = max(rx, ry)
                rx = radius
                ry = radius
            zones.append(
                {
                    "room_key": room_key,
                    "room": room_name,
                    "cx": (min_x + max_x) / 2.0,
                    "cy": (min_y + max_y) / 2.0,
                    "rx": rx,
                    "ry": ry,
                    "color": str(room_style.get("color", "") or ""),
                    "count": len(node_ids),
                }
            )
        return zones

    def _draw_room_zones(self):
        for idx, zone in enumerate(self._room_zone_defs):
            cx = float(zone["cx"])
            cy = float(zone["cy"])
            rx = float(zone["rx"])
            ry = float(zone["ry"])
            room_name = str(zone["room"])
            rect = QRectF(cx - rx, cy - ry, 2.0 * rx, 2.0 * ry)

            room_color = str(zone.get("color", "") or "")
            if self._use_room_colors and room_color:
                stroke = QColor(room_color)
                if not stroke.isValid():
                    stroke = QColor("#8ecae6")
                fill = QColor(stroke)
                fill.setAlpha(14)
            else:
                hue = (sum(ord(ch) for ch in room_name) + idx * 37) % 360
                stroke = QColor.fromHsv(hue, 120, 235, 200)
                fill = QColor.fromHsv(hue, 70, 140, 14)

            ellipse = QGraphicsEllipseItem(rect)
            ellipse.setPen(QPen(stroke, 1.1, Qt.PenStyle.DashLine))
            ellipse.setBrush(QBrush(fill))
            ellipse.setZValue(-30.0)
            ellipse.setData(0, "room-zone")
            self.scene.addItem(ellipse)

            if self._compact_view_enabled:
                continue

            title = QGraphicsSimpleTextItem(room_name)
            f = title.font()
            f.setPointSizeF(max(9.0, self._uniform_font_pt))
            title.setFont(f)
            title.setBrush(QBrush(stroke))
            title.setPos(cx - rx + 12.0, cy - ry + 8.0)
            title.setZValue(-20.0)
            title.setData(0, "room-zone-label")
            self.scene.addItem(title)

    def _draw_cables(self, positions: dict[str, tuple[float, float]]):
        pair_lane_index: dict[tuple[str, str], int] = defaultdict(int)
        incident_edges: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
        for edge in self._cable_edges.values():
            start = edge.start_ap_id.strip()
            end = edge.end_ap_id.strip()
            if not start or not end or start not in positions or end not in positions or start == end:
                continue
            x1, y1 = positions[start]
            x2, y2 = positions[end]
            incident_edges[start].append((edge.cable_id, x2, y2))
            incident_edges[end].append((edge.cable_id, x1, y1))

        fan_offsets: dict[str, dict[str, float]] = defaultdict(dict)
        fan_step = 16.0
        for point_id, links in incident_edges.items():
            ordered = sorted(links, key=lambda item: (item[2], item[1], item[0]))
            half = (len(ordered) - 1) / 2.0
            for idx, (cable_id, _ox, _oy) in enumerate(ordered):
                fan_offsets[point_id][cable_id] = (idx - half) * fan_step

        tree_lane_index: dict[tuple[int, int], int] = defaultdict(int)
        for edge in self._cable_edges.values():
            points: list[tuple[float, float]] = []
            start = edge.start_ap_id.strip()
            end = edge.end_ap_id.strip()
            if start and start in positions:
                points.append(positions[start])
            if end and end in positions:
                points.append(positions[end])

            if len(points) == 2:
                (x1, y1), (x2, y2) = points
                is_cross_edge = edge.cable_id in self._cross_edge_ids
                key_a, key_b = sorted([start or edge.cable_id, end or edge.cable_id])
                pair_key = (key_a, key_b)
                lane = pair_lane_index[pair_key]
                pair_lane_index[pair_key] += 1

                lane_step = 14.0 if is_cross_edge else 18.0
                lane_offset = (lane - (pair_lane_index[pair_key] - 1) / 2.0) * lane_step
                path = QPainterPath()
                if self._layout_mode == "radial":
                    dx = x2 - x1
                    dy = y2 - y1
                    length = math.hypot(dx, dy)
                    nx = -dy / length if length > 1e-9 else 0.0
                    ny = dx / length if length > 1e-9 else 0.0
                    start_x = x1 + nx * lane_offset * 0.65
                    start_y = y1 + ny * lane_offset * 0.65
                    end_x = x2 + nx * lane_offset * 0.65
                    end_y = y2 + ny * lane_offset * 0.65
                    if is_cross_edge:
                        ctrl_x = (start_x + end_x) / 2.0 + nx * 44.0
                        ctrl_y = (start_y + end_y) / 2.0 + ny * 44.0
                        path.moveTo(start_x, start_y)
                        path.quadTo(ctrl_x, ctrl_y, end_x, end_y)
                        label_x = ctrl_x + 6.0
                        label_y = ctrl_y - 12.0
                    else:
                        path.moveTo(start_x, start_y)
                        path.lineTo(end_x, end_y)
                        label_x = (start_x + end_x) / 2.0 + 6.0
                        label_y = (start_y + end_y) / 2.0 - 12.0
                    start_pos = QPointF(start_x, start_y)
                    end_pos = QPointF(end_x, end_y)
                elif is_cross_edge:
                    ctrl_x = (x1 + x2) / 2.0
                    ctrl_y = min(y1, y2) - 90.0 - abs(lane_offset)
                    path.moveTo(x1, y1)
                    path.quadTo(ctrl_x, ctrl_y, x2, y2)
                    label_x = ctrl_x + 6.0
                    label_y = (ctrl_y + (y1 + y2) / 2.0) / 2.0 - 8.0
                    start_pos = QPointF(x1, y1)
                    end_pos = QPointF(x2, y2)
                else:
                    start_fan = fan_offsets.get(start, {}).get(edge.cable_id, 0.0)
                    end_fan = fan_offsets.get(end, {}).get(edge.cable_id, 0.0)
                    y_dir = 1.0 if y2 >= y1 else -1.0
                    stem = 26.0
                    inner_start_y = y1 + y_dir * stem
                    inner_end_y = y2 - y_dir * stem
                    if (y_dir > 0 and inner_start_y > inner_end_y) or (y_dir < 0 and inner_start_y < inner_end_y):
                        mid_y = (y1 + y2) / 2.0
                        inner_start_y = mid_y
                        inner_end_y = mid_y

                    low_band = int(round(min(y1, y2) / 80.0))
                    high_band = int(round(max(y1, y2) / 80.0))
                    lane_key = (low_band, high_band)
                    lane_i = tree_lane_index[lane_key]
                    tree_lane_index[lane_key] += 1
                    lane_slot = self._alternating_lane_slot(lane_i)
                    lane_x = (x1 + x2) / 2.0 + lane_slot * 28.0 + lane_offset * 0.45

                    start_stub_y = y1 + y_dir * 12.0
                    end_stub_y = y2 - y_dir * 12.0
                    path.moveTo(x1, y1)
                    path.lineTo(x1 + start_fan, start_stub_y)
                    path.lineTo(x1 + start_fan, inner_start_y)
                    path.lineTo(lane_x, inner_start_y)
                    path.lineTo(lane_x, inner_end_y)
                    path.lineTo(x2 + end_fan, inner_end_y)
                    path.lineTo(x2 + end_fan, end_stub_y)
                    path.lineTo(x2, y2)
                    label_x = lane_x + 6.0
                    label_y = (inner_start_y + inner_end_y) / 2.0 - 12.0
                    start_pos = QPointF(x1, y1)
                    end_pos = QPointF(x2, y2)
            elif len(points) == 1:
                x1, y1 = points[0]
                x2, y2 = x1 + 180.0, y1 + 40.0
                path = QPainterPath()
                path.moveTo(x1, y1)
                path.lineTo(x2, y2)

                label_x = (x1 + x2) / 2.0 + 6.0
                label_y = (y1 + y2) / 2.0 - 18.0
                lane_offset = 0.0
                start_pos = QPointF(x1, y1)
                end_pos = QPointF(x2, y2)
            else:
                continue

            self._cable_endpoints_scene[edge.cable_id] = (start_pos, end_pos)

            item = _CablePathItem(
                edge.cable_id,
                path,
                self._on_cable_dblclick,
                self._on_cable_mouse_press,
                self._on_cable_mouse_move,
                self._on_cable_mouse_release,
            )
            self._cable_items[edge.cable_id] = item
            is_selected = edge.cable_id in self._selected_cable_ids
            is_cross_edge = edge.cable_id in self._cross_edge_ids
            pen_color = QColor(edge.color)
            if is_cross_edge:
                pen_color.setAlpha(150)
            if is_selected:
                pen_color = pen_color.lighter(165)
            base_width = max(0.5, float(edge.stroke_width_px))
            if is_cross_edge:
                base_width = max(0.8, base_width * 0.85)
            pen_width = base_width + (1.8 if is_selected else 0.0)
            pen = QPen(pen_color, pen_width)
            pen.setStyle(Qt.PenStyle.DashLine if is_cross_edge else _line_style_to_pen_style(edge.line_style))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            item.setPen(pen)
            item.setZValue(6.0 if is_cross_edge else 10.0)
            self.scene.addItem(item)

            label = (
                f"{edge.name or edge.cable_id} | {edge.cable_type or '-'} | "
                f"{edge.length_m:.2f} m"
            )
            show_label = self._show_all_cable_labels or is_selected
            if show_label:
                text = QGraphicsSimpleTextItem(label)
                font = text.font()
                font.setPointSizeF(self._uniform_font_pt)
                text.setFont(font)
                text.setBrush(QBrush(QColor("#ffffff")))
                text.setPos(label_x, label_y)
                self.scene.addItem(text)

            if len(points) == 1:
                open_tag = QGraphicsSimpleTextItem("(offen)")
                f = open_tag.font()
                f.setPointSizeF(self._uniform_font_pt)
                open_tag.setFont(f)
                open_tag.setBrush(QBrush(QColor("#ffcc00")))
                open_tag.setPos(x2 + 8.0, y2 - 8.0)
                self.scene.addItem(open_tag)

            start_handle = self._draw_cable_end_handle(edge.cable_id, "start", start_pos)
            end_handle = self._draw_cable_end_handle(edge.cable_id, "end", end_pos)
            self._cable_handle_items[edge.cable_id] = [start_handle, end_handle]

    def _draw_cable_end_handle(self, cable_id: str, endpoint: str, pos: QPointF):
        radius = float(self._handle_radius)
        handle = _CableEndpointHandle(
            cable_id,
            endpoint,
            QRectF(-radius, -radius, radius * 2.0, radius * 2.0),
            self._on_cable_endpoint_handle_press,
            self._on_cable_mouse_move,
            self._on_cable_mouse_release,
        )
        handle.setPos(pos)
        is_selected = cable_id == self._active_cable_id
        fill = QColor("#ffd54f" if is_selected else "#b0bec5")
        fill.setAlpha(220)
        handle.setBrush(QBrush(fill))
        pen_color = QColor("#2b2b2b" if is_selected else "#455a64")
        handle.setPen(QPen(pen_color, 1.6 if is_selected else 1.2))
        handle.setZValue(45.0 if is_selected else 35.0)
        if is_selected:
            handle.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        else:
            handle.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            handle.setOpacity(0.65)
        self.scene.addItem(handle)
        return handle

    def _screen_px_to_scene(self, px: float) -> float:
        scale = float(self.view.transform().m11())
        if scale <= 1e-9:
            return float(px)
        return float(px) / scale

    @staticmethod
    def _distance_scene(a: QPointF, b: QPointF) -> float:
        dx = float(a.x() - b.x())
        dy = float(a.y() - b.y())
        return math.hypot(dx, dy)

    def _draw_nodes(self, positions: dict[str, tuple[float, float]]):
        for node in self._ap_nodes.values():
            if node.point_id not in positions:
                continue
            x, y = positions[node.point_id]
            self._ap_scene_positions[node.point_id] = QPointF(float(x), float(y))

            w, h = self._node_collision_size(node)
            rect = QRectF(-w / 2, -h / 2, w, h)

            color = QColor(node.color)
            fill = QColor(color)
            fill.setAlpha(48)

            base = _ApNodeItem(
                node.point_id,
                rect,
                self._on_ap_node_moved,
                self._on_ap_node_dblclick,
                self._on_ap_node_mouse_press,
                self._on_ap_node_position_change,
            )
            self._ap_items[node.point_id] = base
            base.setPos(x, y)
            base.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, False)
            base.setBrush(QBrush(fill))
            base.setPen(QPen(color, 3.0 if node.point_id in self._selected_ap_ids else 2.0))
            base.setZValue(20.0)
            self.scene.addItem(base)

            title = QGraphicsSimpleTextItem(self._state_node_title(node), base)
            title_font = title.font()
            title_font.setPointSizeF(max(self._uniform_font_pt, 10.0))
            title_font.setBold(True)
            title.setFont(title_font)
            title.setBrush(QBrush(QColor("#ffffff")))
            title.setPos(rect.left() + 12.0, rect.top() + 8.0)
            title.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            divider_y = rect.top() + 34.0
            divider_path = QPainterPath()
            divider_path.moveTo(rect.left() + 8.0, divider_y)
            divider_path.lineTo(rect.right() - 8.0, divider_y)
            divider = QGraphicsPathItem(divider_path, base)
            divider.setPen(QPen(QColor("#d6e4f0"), 1.2))
            divider.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            icon_rect = QRectF(rect.right() - 34.0, rect.top() + 6.0, 24.0, 24.0)
            self._draw_symbol(base, icon_rect, node)

            attr_text = QGraphicsSimpleTextItem("\n".join(self._state_node_lines(node)), base)
            attr_font = attr_text.font()
            attr_font.setPointSizeF(max(8.5, self._uniform_font_pt - 0.5))
            attr_text.setFont(attr_font)
            attr_text.setBrush(QBrush(QColor("#eef6ff")))
            attr_text.setPos(rect.left() + 12.0, divider_y + 7.0)
            attr_text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _draw_symbol(self, parent_item: QGraphicsRectItem, rect: QRectF, node: ApNode):
        icon_path = (node.icon_path or "").strip()
        if not icon_path:
            icon_path = BUILTIN_SYMBOLS.get(node.builtin_symbol or "", "")

        if not icon_path:
            return

        if is_svg_asset_ref(icon_path):
            if is_data_uri(icon_path):
                parsed = parse_data_uri(icon_path)
                if parsed is None:
                    return
                renderer = QSvgRenderer(QByteArray(parsed[1]))
            else:
                renderer = QSvgRenderer(icon_path)
            if renderer.isValid():
                img = QPixmap(int(rect.width()), int(rect.height()))
                img.fill(Qt.GlobalColor.transparent)
                painter = None
                try:
                    painter = QPainter(img)
                    renderer.render(painter, QRectF(0, 0, rect.width(), rect.height()))
                finally:
                    if painter is not None:
                        painter.end()
                item = QGraphicsPixmapItem(img, parent_item)
                item.setPos(rect.left(), rect.top())
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                return

        pix = QPixmap()
        if is_data_uri(icon_path):
            parsed = parse_data_uri(icon_path)
            if parsed is None or not pix.loadFromData(parsed[1]):
                return
        else:
            pix = QPixmap(icon_path)
        if pix.isNull():
            return
        scaled = pix.scaled(
            int(rect.width()),
            int(rect.height()),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        item = QGraphicsPixmapItem(scaled, parent_item)
        item.setPos(rect.center().x() - scaled.width() / 2, rect.center().y() - scaled.height() / 2)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _on_ap_node_moved(self, point_id: str, x: float, y: float):
        if self._is_rendering:
            return

        if self._group_drag_active and self._group_drag_anchor_id == point_id:
            changed_positions: dict[str, list[float]] = {}
            for selected_id in self._group_drag_orig_positions:
                item = self._ap_items.get(selected_id)
                if item is None:
                    continue
                pos = item.pos()
                current = self._manual_positions.get(selected_id)
                nx = float(pos.x())
                ny = float(pos.y())
                if current is None or abs(current[0] - nx) > 0.01 or abs(current[1] - ny) > 0.01:
                    self._manual_positions[selected_id] = (nx, ny)
                    changed_positions[selected_id] = [nx, ny]

            self._group_drag_active = False
            self._group_drag_anchor_id = None
            self._group_drag_orig_positions.clear()

            self._emit_position_changes(changed_positions)
            self._render()
            return

        self._manual_positions[point_id] = (float(x), float(y))
        self.ap_position_changed.emit(point_id, float(x), float(y))
        self._render()

    def _emit_position_changes(self, changed_positions: dict[str, list[float]]):
        if len(changed_positions) == 1:
            pid, coords = next(iter(changed_positions.items()))
            self.ap_position_changed.emit(pid, float(coords[0]), float(coords[1]))
        elif changed_positions:
            self.ap_positions_changed.emit(changed_positions)

    def _on_ap_node_mouse_press(self, point_id: str, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return True

        if self._cable_pick_state is not None:
            self._apply_cable_pick_click(point_id)
            return False

        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            ap_ids = set(self._selected_ap_ids)
            if point_id in ap_ids:
                ap_ids.remove(point_id)
            else:
                ap_ids.add(point_id)
            self._set_selection(ap_ids, set(self._selected_cable_ids), self._active_cable_id)
            return False
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            ap_ids = set(self._selected_ap_ids)
            ap_ids.add(point_id)
            self._set_selection(ap_ids, set(self._selected_cable_ids), self._active_cable_id)
        else:
            if point_id in self._selected_ap_ids:
                self._set_selection(set(self._selected_ap_ids), set(self._selected_cable_ids), self._active_cable_id)
            else:
                self._set_selection({point_id}, set(), None)
        return False

    def _on_ap_node_position_change(self, point_id: str, target_pos: QPointF) -> QPointF:
        if (
            not self._group_drag_active
            or self._group_drag_anchor_id != point_id
            or self._applying_group_drag
        ):
            return target_pos

        anchor_origin = self._group_drag_orig_positions.get(point_id)
        if anchor_origin is None:
            return target_pos

        dx = float(target_pos.x() - anchor_origin.x())
        dy = float(target_pos.y() - anchor_origin.y())
        if abs(dx) <= 1e-9 and abs(dy) <= 1e-9:
            return target_pos

        self._applying_group_drag = True
        try:
            for pid, origin in self._group_drag_orig_positions.items():
                if pid == point_id:
                    continue
                item = self._ap_items.get(pid)
                if item is None:
                    continue
                item.setPos(origin.x() + dx, origin.y() + dy)
        finally:
            self._applying_group_drag = False
        return target_pos

    def _clear_rewire_preview(self):
        if self._rewire_preview_item is not None:
            try:
                self.scene.removeItem(self._rewire_preview_item)
            except RuntimeError:
                pass
            self._rewire_preview_item = None

    def _nearest_ap_for_drop(self, scene_pos: QPointF, tolerance_px: float | None = None) -> str | None:
        best_id = None
        best_dist = self._screen_px_to_scene(
            float(tolerance_px if tolerance_px is not None else self._rewire_drop_tolerance_px)
        )
        for point_id, center in self._ap_scene_positions.items():
            dist = self._distance_scene(center, scene_pos)
            if dist < best_dist:
                best_dist = dist
                best_id = point_id
        return best_id

    def _start_rewire_for_endpoint(self, cable_id: str, endpoint_kind: str, scene_pos: QPointF):
        edge = self._cable_edges.get(cable_id)
        endpoints = self._cable_endpoints_scene.get(cable_id)
        if edge is None or endpoints is None:
            return False
        start_pos, end_pos = endpoints
        if start_pos is None or end_pos is None:
            return False

        if endpoint_kind == "start":
            anchor = end_pos
        else:
            anchor = start_pos

        self._cable_rewire_state = {
            "cable_id": cable_id,
            "endpoint": endpoint_kind,
            "anchor": QPointF(anchor),
            "orig_start": edge.start_ap_id,
            "orig_end": edge.end_ap_id,
        }
        self._clear_rewire_preview()
        self._rewire_preview_item = QGraphicsPathItem()
        preview_pen = QPen(QColor("#ffd54f"), 2.0)
        preview_pen.setStyle(Qt.PenStyle.DashLine)
        self._rewire_preview_item.setPen(preview_pen)
        self.scene.addItem(self._rewire_preview_item)

        path = QPainterPath()
        path.moveTo(anchor)
        path.lineTo(scene_pos)
        self._rewire_preview_item.setPath(path)
        return True

    def _on_cable_endpoint_handle_press(self, cable_id: str, endpoint_kind: str, event):
        if self._cable_pick_state is not None:
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._active_cable_id != cable_id:
            self._set_selection(set(self._selected_ap_ids), {cable_id}, cable_id)
            self._clear_rewire_preview()
            self._cable_rewire_state = None
            event.accept()
            return
        if self._start_rewire_for_endpoint(cable_id, endpoint_kind, event.scenePos()):
            event.accept()

    def _on_cable_mouse_press(self, cable_id: str, event):
        if self._cable_pick_state is not None:
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            cable_ids = set(self._selected_cable_ids)
            if cable_id in cable_ids:
                cable_ids.remove(cable_id)
            else:
                cable_ids.add(cable_id)
            active_cable = cable_id if cable_id in cable_ids else None
            self._set_selection(set(self._selected_ap_ids), cable_ids, active_cable)
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            cable_ids = set(self._selected_cable_ids)
            cable_ids.add(cable_id)
            self._set_selection(set(self._selected_ap_ids), cable_ids, cable_id)
        else:
            if cable_id in self._selected_cable_ids:
                self._set_selection(set(self._selected_ap_ids), set(self._selected_cable_ids), self._active_cable_id)
            else:
                self._set_selection(set(), {cable_id}, cable_id)

        self._clear_rewire_preview()
        self._cable_rewire_state = None
        if not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
            self._start_cable_group_drag(event.scenePos())
        event.accept()

    def _on_cable_mouse_move(self, cable_id: str, event):
        drag_state = self._cable_drag_state
        if drag_state and drag_state.get("cable_id") == cable_id:
            self._update_cable_group_drag(event.scenePos())
            event.accept()
            return

        state = self._cable_rewire_state
        if not state or state.get("cable_id") != cable_id:
            return
        if self._rewire_preview_item is None:
            return
        anchor = state.get("anchor")
        if anchor is None:
            return
        scene_pos = event.scenePos()
        path = QPainterPath()
        path.moveTo(anchor)
        path.lineTo(scene_pos)
        self._rewire_preview_item.setPath(path)
        event.accept()

    def _finalize_cable_rewire(self, cable_id: str, scene_pos: QPointF):
        state = self._cable_rewire_state
        if not state or state.get("cable_id") != cable_id:
            return False
        target_ap = self._nearest_ap_for_drop(scene_pos)
        payload = None
        if target_ap:
            edge = self._cable_edges.get(cable_id)
            if edge is not None:
                payload = {
                    "name": edge.name,
                    "type": edge.cable_type,
                    "color": edge.color,
                    "visible": edge.visible,
                    "label_visible": edge.label_visible,
                    "type_label_visible": edge.type_label_visible,
                    "label_size": edge.label_size,
                    "stroke_width": edge.stroke_width_px,
                    "line_style": edge.line_style,
                    "start_ap_id": edge.start_ap_id,
                    "end_ap_id": edge.end_ap_id,
                    "comment": edge.comment,
                }
                if state.get("endpoint") == "start":
                    payload["start_ap_id"] = target_ap
                else:
                    payload["end_ap_id"] = target_ap
        self._clear_rewire_preview()
        self._cable_rewire_state = None
        if payload is not None:
            self.edit_cable_requested.emit(cable_id, payload)
        return True

    def _on_cable_mouse_release(self, cable_id: str, event):
        drag_state = self._cable_drag_state
        if drag_state and drag_state.get("cable_id") == cable_id:
            self._finalize_cable_group_drag()
            event.accept()
            return

        state = self._cable_rewire_state
        if not state or state.get("cable_id") != cable_id:
            return
        self._finalize_cable_rewire(cable_id, event.scenePos())
        event.accept()

    def _start_cable_group_drag(self, scene_pos: QPointF):
        self._cable_drag_state = None

    def _collect_ap_ids_for_group_move(self) -> set[str]:
        ap_ids = set(self._selected_ap_ids)
        for selected_cable_id in self._selected_cable_ids:
            edge = self._cable_edges.get(selected_cable_id)
            if edge is None:
                continue
            if edge.start_ap_id in self._ap_items:
                ap_ids.add(edge.start_ap_id)
            if edge.end_ap_id in self._ap_items:
                ap_ids.add(edge.end_ap_id)
        return ap_ids

    def _current_selection_bounds(self) -> QRectF | None:
        rect: QRectF | None = None
        for point_id in self._selected_ap_ids:
            item = self._ap_items.get(point_id)
            if item is None:
                continue
            item_rect = item.sceneBoundingRect()
            rect = QRectF(item_rect) if rect is None else rect.united(item_rect)
        for cable_id in self._selected_cable_ids:
            item = self._cable_items.get(cable_id)
            if item is None:
                continue
            item_rect = item.sceneBoundingRect()
            rect = QRectF(item_rect) if rect is None else rect.united(item_rect)
        return rect

    def _start_view_group_drag(self, scene_pos: QPointF):
        self._view_group_drag_state = None
        return False

    def _update_view_group_drag(self, scene_pos: QPointF):
        state = self._view_group_drag_state
        if not state:
            return
        start_scene = state.get("start_scene")
        if not isinstance(start_scene, QPointF):
            return

        dx = float(scene_pos.x() - start_scene.x())
        dy = float(scene_pos.y() - start_scene.y())
        orig_positions = state.get("orig_positions", {})
        for point_id, origin in orig_positions.items():
            item = self._ap_items.get(point_id)
            if item is None:
                continue
            item.setPos(origin.x() + dx, origin.y() + dy)

    def _finalize_view_group_drag(self):
        state = self._view_group_drag_state
        self._view_group_drag_state = None
        if not state:
            return
        changed_positions: dict[str, list[float]] = {}
        orig_positions = state.get("orig_positions", {})
        for point_id, origin in orig_positions.items():
            item = self._ap_items.get(point_id)
            if item is None:
                continue
            pos = item.pos()
            nx = float(pos.x())
            ny = float(pos.y())
            if abs(nx - origin.x()) <= 0.01 and abs(ny - origin.y()) <= 0.01:
                continue
            self._manual_positions[point_id] = (nx, ny)
            changed_positions[point_id] = [nx, ny]
        self._emit_position_changes(changed_positions)
        self._render()

    def _update_cable_group_drag(self, scene_pos: QPointF):
        state = self._cable_drag_state
        if not state:
            return
        start_scene = state.get("start_scene")
        if not isinstance(start_scene, QPointF):
            return

        dx = float(scene_pos.x() - start_scene.x())
        dy = float(scene_pos.y() - start_scene.y())
        orig_positions = state.get("orig_positions", {})
        for point_id, origin in orig_positions.items():
            item = self._ap_items.get(point_id)
            if item is None:
                continue
            item.setPos(origin.x() + dx, origin.y() + dy)

    def _finalize_cable_group_drag(self):
        state = self._cable_drag_state
        self._cable_drag_state = None
        if not state:
            return

        changed_positions: dict[str, list[float]] = {}
        orig_positions = state.get("orig_positions", {})
        for point_id, origin in orig_positions.items():
            item = self._ap_items.get(point_id)
            if item is None:
                continue
            pos = item.pos()
            nx = float(pos.x())
            ny = float(pos.y())
            if abs(nx - origin.x()) <= 0.01 and abs(ny - origin.y()) <= 0.01:
                continue
            self._manual_positions[point_id] = (nx, ny)
            changed_positions[point_id] = [nx, ny]

        self._emit_position_changes(changed_positions)
        self._render()

    def _on_view_mouse_move(self, event) -> bool:
        if self._view_group_drag_state is not None:
            scene_pos = self.view.mapToScene(event.position().toPoint())
            self._update_view_group_drag(scene_pos)
            return True

        if self._selection_origin is not None:
            scene_pos = self.view.mapToScene(event.position().toPoint())
            self._update_selection_rect(scene_pos)
            self._apply_rect_selection_preview()
            return True

        state = self._cable_rewire_state
        if not state:
            return False
        anchor = state.get("anchor")
        if anchor is None:
            return False
        if self._rewire_preview_item is None:
            return False
        scene_pos = self.view.mapToScene(event.position().toPoint())
        path = QPainterPath()
        path.moveTo(anchor)
        path.lineTo(scene_pos)
        self._rewire_preview_item.setPath(path)
        return True

    def _on_view_mouse_release(self, event) -> bool:
        if self._view_group_drag_state is not None and event.button() == Qt.MouseButton.LeftButton:
            self._finalize_view_group_drag()
            return True

        if self._selection_origin is not None and event.button() == Qt.MouseButton.LeftButton:
            self._apply_rect_selection_preview()
            self._selection_origin = None
            self._clear_selection_rect()
            return True

        state = self._cable_rewire_state
        if not state:
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        cable_id = str(state.get("cable_id") or "")
        if not cable_id:
            return False
        scene_pos = self.view.mapToScene(event.position().toPoint())
        return self._finalize_cable_rewire(cable_id, scene_pos)

    def _on_view_mouse_press(self, event) -> bool:
        if event.button() == Qt.MouseButton.RightButton:
            scene_pos = self.view.mapToScene(event.position().toPoint())
            self._open_context_menu(scene_pos, event.globalPosition())
            return True

        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._cable_pick_state is not None:
            return False
        if self._cable_rewire_state is not None:
            return False

        scene_pos = self.view.mapToScene(event.position().toPoint())
        mods = event.modifiers()
        selection_count = len(self._selected_ap_ids) + len(self._selected_cable_ids)
        selection_bounds = self._current_selection_bounds()
        if (
            selection_count > 1
            and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
            and selection_bounds is not None
            and selection_bounds.contains(scene_pos)
        ):
            return self._start_view_group_drag(scene_pos)

        item = self.scene.itemAt(scene_pos, self.view.transform())
        if item is not None:
            return False

        self._selection_origin = scene_pos
        self._selection_origin_ap_ids = set(self._selected_ap_ids)
        self._selection_origin_cable_ids = set(self._selected_cable_ids)
        if mods & Qt.KeyboardModifier.ControlModifier:
            self._selection_mode = "toggle"
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self._selection_mode = "add"
        else:
            self._selection_mode = "replace"
        self._update_selection_rect(scene_pos)
        return True

    def _clear_selection_rect(self):
        if self._selection_rect_item is not None:
            try:
                self.scene.removeItem(self._selection_rect_item)
            except RuntimeError:
                pass
            self._selection_rect_item = None

    def _update_selection_rect(self, scene_pos: QPointF):
        if self._selection_origin is None:
            return
        rect = QRectF(self._selection_origin, scene_pos).normalized()
        if self._selection_rect_item is None:
            self._selection_rect_item = QGraphicsRectItem()
            self._selection_rect_item.setPen(QPen(QColor("#80deea"), 1.2, Qt.PenStyle.DashLine))
            fill = QColor("#80deea")
            fill.setAlpha(40)
            self._selection_rect_item.setBrush(QBrush(fill))
            self._selection_rect_item.setZValue(1000.0)
            self.scene.addItem(self._selection_rect_item)
        self._selection_rect_item.setRect(rect)

    def _collect_ids_in_rect(self, rect: QRectF) -> tuple[set[str], set[str]]:
        if rect.width() < 1.0 and rect.height() < 1.0:
            return set(), set()

        hit_aps = {
            point_id
            for point_id, item in self._ap_items.items()
            if item.sceneBoundingRect().intersects(rect)
        }
        hit_cables = {
            cable_id
            for cable_id, item in self._cable_items.items()
            if item.sceneBoundingRect().intersects(rect)
        }
        return hit_aps, hit_cables

    def _apply_rect_selection_preview(self):
        if self._selection_rect_item is None:
            return
        rect = self._selection_rect_item.rect().normalized()
        hit_aps, hit_cables = self._collect_ids_in_rect(rect)

        if self._selection_mode == "toggle":
            ap_ids = set(self._selection_origin_ap_ids) ^ hit_aps
            cable_ids = set(self._selection_origin_cable_ids) ^ hit_cables
        elif self._selection_mode == "add":
            ap_ids = set(self._selection_origin_ap_ids) | hit_aps
            cable_ids = set(self._selection_origin_cable_ids) | hit_cables
        else:
            ap_ids = hit_aps
            cable_ids = hit_cables

        active_cable = self._active_cable_id if self._active_cable_id in cable_ids else None
        if active_cable is None and cable_ids:
            active_cable = next(iter(cable_ids))
        self._set_selection(ap_ids, cable_ids, active_cable)

    def _set_selection(
        self,
        ap_ids: set[str],
        cable_ids: set[str],
        active_cable: str | None,
    ):
        prev_ap_ids = set(self._selected_ap_ids)
        prev_cable_ids = set(self._selected_cable_ids)
        prev_active_cable_id = self._active_cable_id
        self._selected_ap_ids = {pid for pid in ap_ids if pid in self._ap_nodes}
        self._selected_cable_ids = {cid for cid in cable_ids if cid in self._cable_edges}
        if active_cable and active_cable in self._selected_cable_ids:
            self._active_cable_id = active_cable
        else:
            self._active_cable_id = next(iter(self._selected_cable_ids), None)
        if (
            not self._is_rendering
            and (
                prev_ap_ids != self._selected_ap_ids
                or prev_cable_ids != self._selected_cable_ids
                or prev_active_cable_id != self._active_cable_id
            )
        ):
            self._render()
            return
        self._apply_selection_visuals()

    def _apply_selection_visuals(self):
        pick_state = self._cable_pick_state
        locked_ap_id = self._cable_pick_locked_ap_id()
        for point_id, item in self._ap_items.items():
            node = self._ap_nodes.get(point_id)
            if node is None:
                continue
            color = QColor(node.color)
            fill = QColor(color)
            is_selected = point_id in self._selected_ap_ids
            if pick_state is not None:
                if point_id == locked_ap_id:
                    locked_fill = QColor("#43aa8b")
                    locked_fill.setAlpha(85)
                    item.setBrush(QBrush(locked_fill))
                    item.setPen(QPen(QColor("#8cffc1"), 3.2))
                else:
                    fill.setAlpha(90)
                    item.setBrush(QBrush(fill))
                    item.setPen(QPen(QColor("#ffd54f"), 3.0 if is_selected else 2.6))
            else:
                fill.setAlpha(100 if is_selected else 60)
                item.setBrush(QBrush(fill))
                item.setPen(QPen(color, 3.0 if is_selected else 2.0))

        for cable_id, item in self._cable_items.items():
            edge = self._cable_edges.get(cable_id)
            if edge is None:
                continue
            is_selected = cable_id in self._selected_cable_ids
            pen_color = QColor(edge.color)
            if is_selected:
                pen_color = pen_color.lighter(165)
            pen_width = max(0.5, float(edge.stroke_width_px)) + (1.8 if is_selected else 0.0)
            pen = QPen(pen_color, pen_width)
            pen.setStyle(_line_style_to_pen_style(edge.line_style))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            item.setPen(pen)

            handles = self._cable_handle_items.get(cable_id, [])
            for handle in handles:
                is_active = cable_id == self._active_cable_id
                fill = QColor("#ffd54f" if is_active else "#b0bec5")
                fill.setAlpha(220)
                handle.setBrush(QBrush(fill))
                pen_color = QColor("#2b2b2b" if is_active else "#455a64")
                handle.setPen(QPen(pen_color, 1.6 if is_active else 1.2))
                handle.setZValue(45.0 if is_active else 35.0)
                if is_active:
                    handle.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                    handle.setOpacity(1.0)
                else:
                    handle.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    handle.setOpacity(0.65)

    def _on_ap_node_dblclick(self, point_id: str):
        node = self._ap_nodes.get(point_id)
        if node is None:
            return
        uv_cable_choices = sorted(
            {
                (edge.name or edge.cable_id).strip() or edge.cable_id
                for edge in self._cable_edges.values()
            },
            key=str.lower,
        )
        up_cable_choices = sorted(
            [
                (edge.cable_id, (edge.name or edge.cable_id).strip() or edge.cable_id)
                for edge in self._cable_edges.values()
                if point_id in {edge.start_ap_id.strip(), edge.end_ap_id.strip()}
            ],
            key=lambda value: value[1].lower(),
        )
        dlg = _EditApDialog(node, uv_cable_choices, up_cable_choices, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.edit_ap_requested.emit(point_id, dlg.get_payload())

    def _on_cable_dblclick(self, cable_id: str):
        if self._cable_pick_state is not None:
            return
        edge = self._cable_edges.get(cable_id)
        if edge is None:
            return
        dlg = _EditCableDialog(edge, self._ap_nodes, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.edit_cable_requested.emit(cable_id, dlg.get_payload())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._cable_pick_state is not None:
            self._cancel_cable_pick_mode()
            event.accept()
            return
        super().keyPressEvent(event)
