from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QByteArray
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.parameter_panel import BUILTIN_SYMBOLS, UvConfigDialog, UpDistributionDialog
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
    start_ap_id: str
    end_ap_id: str
    visible: bool = True
    label_visible: bool = True
    type_label_visible: bool = False
    label_size: float = 12.0
    comment: str = ""


class _AddApDialog(QDialog):
    def __init__(self, room_choices: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("AP hinzufügen")
        self.resize(420, 320)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.le_name = QLineEdit()
        self.le_name.setPlaceholderText("z. B. Steckdose Küche")
        form.addRow("Name:", self.le_name)

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
        self.setWindowTitle("Kabel hinzufügen")
        self.resize(420, 340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.le_name = QLineEdit()
        self.le_name.setPlaceholderText("z. B. Zuleitung Küche")
        form.addRow("Name:", self.le_name)

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

        sorted_aps = [(pid, n.name or pid) for pid, n in all_ap_nodes.items()]
        sorted_aps.sort(key=lambda value: value[1].lower())
        self.cmb_start_ap = QComboBox()
        self.cmb_end_ap = QComboBox()
        self.cmb_start_ap.addItem("(keiner)", "")
        self.cmb_end_ap.addItem("(keiner)", "")
        for ap_id, ap_name in sorted_aps:
            label_text = ap_name or ap_id
            self.cmb_start_ap.addItem(label_text, ap_id)
            self.cmb_end_ap.addItem(label_text, ap_id)
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
            "start_ap_id": str(self.cmb_start_ap.currentData() or ""),
            "end_ap_id": str(self.cmb_end_ap.currentData() or ""),
        }


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
        self.le_name.setPlaceholderText("z. B. Zuleitung K\u00fcche")
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

        self.sb_label_size = QDoubleSpinBox()
        self.sb_label_size.setRange(0.1, 999999.0)
        self.sb_label_size.setSingleStep(1.0)
        self.sb_label_size.setSuffix(" pt")
        self.sb_label_size.setValue(float(edge.label_size or 12.0))
        form.addRow("Schriftgröße:", self.sb_label_size)

        self.lbl_length = QLabel(f"{edge.length_m:.2f} m")
        form.addRow("Länge:", self.lbl_length)

        sorted_aps = [("" , "(keiner)")] + sorted(
            [(pid, n.name or pid) for pid, n in all_ap_nodes.items()],
            key=lambda v: v[1].lower(),
        )
        self.cmb_start_ap = QComboBox()
        self.cmb_end_ap = QComboBox()
        for ap_id, ap_name in sorted_aps:
            label_text = (ap_name or ap_id) if ap_id else "(keiner)"
            self.cmb_start_ap.addItem(label_text, ap_id)
            self.cmb_end_ap.addItem(label_text, ap_id)
        for cmb, target in [(self.cmb_start_ap, edge.start_ap_id), (self.cmb_end_ap, edge.end_ap_id)]:
            idx = cmb.findData((target or "").strip())
            if idx >= 0:
                cmb.setCurrentIndex(idx)
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
            "start_ap_id": str(self.cmb_start_ap.currentData() or ""),
            "end_ap_id": str(self.cmb_end_ap.currentData() or ""),
            "comment": self.te_comment.toPlainText(),
        }


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

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        self.btn_add_ap = QPushButton("➕ AP")
        self.btn_add_cable = QPushButton("➕ Kabel")
        self.btn_del_ap = QPushButton("🗑 AP")
        self.btn_del_cable = QPushButton("🗑 Kabel")
        self.btn_refresh = QPushButton("🔄 Aktualisieren")
        
        self.btn_zoom_in = QPushButton("＋")
        self.btn_zoom_out = QPushButton("－")
        self.btn_zoom_reset = QPushButton("100%")
        self.btn_fit = QPushButton("Auf Inhalt einpassen")
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setMinimumWidth(52)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_mode = QLabel("")
        self.lbl_mode.setVisible(False)
        self.lbl_mode.setStyleSheet("color:#ffd54f; font-weight:bold;")

        top.addWidget(self.btn_add_ap)
        top.addWidget(self.btn_add_cable)
        top.addWidget(self.btn_del_ap)
        top.addWidget(self.btn_del_cable)
        top.addStretch(1)
        top.addWidget(self.btn_zoom_out)
        top.addWidget(self.btn_zoom_in)
        top.addWidget(self.btn_zoom_reset)
        top.addWidget(self.lbl_zoom)
        top.addWidget(self.lbl_mode)
        top.addWidget(self.btn_fit)
        top.addWidget(self.btn_refresh)
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

        self.lbl_hint = QLabel("Anzeige: AP-Name, Raum, Verteilerfunktion, Anschlussstatus | Kabel: Name, Typ, Länge")
        root.addWidget(self.lbl_hint)

        self.setCentralWidget(central)

        self.btn_add_ap.clicked.connect(self._open_add_ap_dialog)
        self.btn_add_cable.clicked.connect(self._open_add_cable_dialog)
        self.btn_del_ap.clicked.connect(self._open_delete_ap_dialog)
        self.btn_del_cable.clicked.connect(self._open_delete_cable_dialog)
        self.btn_refresh.clicked.connect(self._render)
        
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        self.btn_zoom_reset.clicked.connect(self._zoom_reset)
        self.btn_fit.clicked.connect(self._fit_to_content)
        self._update_zoom_label()
        self._update_mode_indicator()

    def set_data(
        self,
        ap_nodes: list[ApNode],
        cable_edges: list[CableEdge],
        manual_positions: dict[str, tuple[float, float]] | None = None,
        room_choices: list[tuple[str, str]] | None = None,
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
        if manual_positions is not None:
            sanitized: dict[str, tuple[float, float]] = {}
            for point_id, pos in manual_positions.items():
                if point_id not in self._ap_nodes:
                    continue
                if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                    continue
                try:
                    sanitized[point_id] = (float(pos[0]), float(pos[1]))
                except (TypeError, ValueError):
                    continue
            self._manual_positions = sanitized
        self._render()

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
                "Anzeige: AP-Name, Raum, Verteilerfunktion, Anschlussstatus | Kabel: Name, Typ, Länge"
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
        self.scene.clear()
        if not self._ap_nodes and not self._cable_edges:
            self.scene.addSimpleText("Keine APs/Kabel vorhanden.")
            self._is_rendering = False
            return

        positions = self._compute_layout_positions()
        self._draw_cables(positions)
        self._draw_nodes(positions)

        bounds = self.scene.itemsBoundingRect().adjusted(-50, -50, 50, 50)
        self.scene.setSceneRect(bounds)
        self._is_rendering = False
        self._apply_selection_visuals()

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

    def _fit_to_content(self):
        rect = self.scene.itemsBoundingRect()
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
        """Hierarchisches Layout mit UV als Ursprung.
        Ebene 0 ist die Unterverteilung (ap_type == "uv").
        Die Hierarchie wird über den Graph-Abstand zur UV aufgebaut,
        und die Reihenfolge innerhalb der Ebene über Verbindungsanzahl bestimmt.
        """
        positions: dict[str, tuple[float, float]] = {}

        nodes = set(self._ap_nodes.keys())
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self._cable_edges.values():
            a = edge.start_ap_id.strip()
            b = edge.end_ap_id.strip()
            if a and b and a in nodes and b in nodes and a != b:
                adjacency[a].add(b)
                adjacency[b].add(a)

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

        level_gap_y = 260.0
        node_gap_x = 220.0
        component_gap_x = 260.0
        isolated_cols = 4

        base_x = 0.0
        for component in sorted(components, key=lambda c: len(c), reverse=True):
            if len(component) == 1 and len(adjacency[component[0]]) == 0:
                node_id = component[0]
                positions[node_id] = (base_x, 0.0)
                base_x += component_gap_x
                continue

            uv_candidates = [nid for nid in component if self._ap_nodes[nid].ap_type == "uv"]
            if uv_candidates:
                root = max(uv_candidates, key=lambda nid: len(adjacency[nid]))
            else:
                root = max(component, key=lambda nid: len(adjacency[nid]))

            levels: dict[str, int] = {root: 0}
            queue = deque([root])
            while queue:
                cur = queue.popleft()
                for nxt in adjacency[cur]:
                    if nxt not in levels:
                        levels[nxt] = levels[cur] + 1
                        queue.append(nxt)

            for node_id in component:
                if node_id not in levels:
                    levels[node_id] = 0

            level_nodes: dict[int, list[str]] = defaultdict(list)
            for node_id in component:
                level_nodes[levels[node_id]].append(node_id)

            max_level = max(level_nodes.keys()) if level_nodes else 0
            for level in range(max_level + 1):
                level_nodes[level].sort(
                    key=lambda nid: (
                        -len(adjacency[nid]),
                        str(self._ap_nodes[nid].name or nid).lower(),
                    )
                )

            def _ordered_neighbor_positions(target_level: int) -> dict[str, int]:
                return {node_id: idx for idx, node_id in enumerate(level_nodes[target_level])}

            for _ in range(8):
                for level in range(1, max_level + 1):
                    prev_order = _ordered_neighbor_positions(level - 1)

                    def _down_key(nid: str):
                        neigh = [prev_order[n] for n in adjacency[nid] if levels[n] == level - 1]
                        if neigh:
                            return (sum(neigh) / len(neigh), -len(adjacency[nid]))
                        return (1e9, -len(adjacency[nid]))

                    level_nodes[level].sort(key=_down_key)

                for level in range(max_level - 1, -1, -1):
                    next_order = _ordered_neighbor_positions(level + 1)

                    def _up_key(nid: str):
                        neigh = [next_order[n] for n in adjacency[nid] if levels[n] == level + 1]
                        if neigh:
                            return (sum(neigh) / len(neigh), -len(adjacency[nid]))
                        return (1e9, -len(adjacency[nid]))

                    level_nodes[level].sort(key=_up_key)

            max_count = max((len(v) for v in level_nodes.values()), default=1)
            component_width = max_count * node_gap_x
            component_left = base_x - component_width / 2.0

            for level in range(max_level + 1):
                group = level_nodes[level]
                group_width = max(1, len(group)) * node_gap_x
                x0 = component_left + (component_width - group_width) / 2.0
                y = level * level_gap_y
                for idx, node_id in enumerate(group):
                    positions[node_id] = (x0 + idx * node_gap_x, y)

            base_x += component_width + component_gap_x

        isolated = [
            node_id
            for node_id in sorted(self._ap_nodes.keys())
            if len(adjacency[node_id]) == 0
        ]
        if isolated:
            max_y = max((pos[1] for pos in positions.values()), default=0.0)
            x0 = -((isolated_cols - 1) * node_gap_x) / 2.0
            y0 = max_y + level_gap_y
            for idx, node_id in enumerate(isolated):
                if node_id in self._manual_positions:
                    continue
                col = idx % max(1, isolated_cols)
                row = idx // max(1, isolated_cols)
                positions[node_id] = (x0 + col * node_gap_x, y0 + row * 150.0)

        for point_id, pos in self._manual_positions.items():
            if point_id in positions:
                positions[point_id] = (float(pos[0]), float(pos[1]))

        return positions

    def _draw_cables(self, positions: dict[str, tuple[float, float]]):
        pair_lane_index: dict[tuple[str, str], int] = defaultdict(int)
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
                key_a, key_b = sorted([start or edge.cable_id, end or edge.cable_id])
                pair_key = (key_a, key_b)
                lane = pair_lane_index[pair_key]
                pair_lane_index[pair_key] += 1

                lane_step = 22.0
                lane_offset = (lane - (pair_lane_index[pair_key] - 1) / 2.0) * lane_step
                path = QPainterPath()
                path.moveTo(x1, y1 + lane_offset)
                path.lineTo(x2, y2 + lane_offset)

                label_x = (x1 + x2) / 2.0 + 6.0
                label_y = (y1 + y2) / 2.0 + lane_offset - 18.0
            elif len(points) == 1:
                x1, y1 = points[0]
                x2, y2 = x1 + 180.0, y1 + 40.0
                path = QPainterPath()
                path.moveTo(x1, y1)
                path.lineTo(x2, y2)

                label_x = (x1 + x2) / 2.0 + 6.0
                label_y = (y1 + y2) / 2.0 - 18.0
            else:
                continue

            start_pos = QPointF(x1, y1 + lane_offset) if len(points) == 2 else QPointF(x1, y1)
            end_pos = QPointF(x2, y2 + lane_offset) if len(points) == 2 else QPointF(x2, y2)
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
            pen_color = QColor(edge.color)
            if is_selected:
                pen_color = pen_color.lighter(165)
            pen_width = max(0.5, float(edge.stroke_width_px)) + (1.8 if is_selected else 0.0)
            pen = QPen(pen_color, pen_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            item.setPen(pen)
            item.setZValue(10.0)
            self.scene.addItem(item)

            label = (
                f"{edge.name or edge.cable_id} | {edge.cable_type or '-'} | "
                f"{edge.length_m:.2f} m"
            )
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

            w = max(56.0, float(node.width_px))
            h = max(56.0, float(node.height_px))
            rect = QRectF(-w / 2, -h / 2, w, h)

            color = QColor(node.color)
            fill = QColor(color)
            fill.setAlpha(60)

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
            base.setBrush(QBrush(fill))
            base.setPen(QPen(color, 3.0 if node.point_id in self._selected_ap_ids else 2.0))
            base.setZValue(20.0)
            self.scene.addItem(base)

            self._draw_symbol(base, rect, node)

            distributor = "Ja" if node.has_distributor_function else "Nein"
            connected = "angeschlossen" if node.is_connected else "nicht angeschlossen"
            label = (
                f"{node.name or node.point_id}\n"
                f"Raum: {node.room or '(ohne Raum)'}\n"
                f"Verteilerfunktion: {distributor}\n"
                f"Status: {connected}"
            )
            text = QGraphicsSimpleTextItem(label, base)
            font = text.font()
            font.setPointSizeF(self._uniform_font_pt)
            text.setFont(font)
            text.setBrush(QBrush(QColor("#ffffff")))
            text.setPos(w / 2 + 8.0, -h / 2)
            text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

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

        if point_id not in self._selected_ap_ids:
            return False

        self._group_drag_anchor_id = point_id
        self._group_drag_active = True
        self._group_drag_orig_positions = {
            pid: QPointF(self._ap_items[pid].pos())
            for pid in self._selected_ap_ids
            if pid in self._ap_items
        }
        return True

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
        ap_ids = self._collect_ap_ids_for_group_move()

        if not ap_ids:
            self._cable_drag_state = None
            return

        self._cable_drag_state = {
            "cable_id": self._active_cable_id,
            "start_scene": QPointF(scene_pos),
            "ap_ids": ap_ids,
            "orig_positions": {
                point_id: QPointF(self._ap_items[point_id].pos())
                for point_id in ap_ids
                if point_id in self._ap_items
            },
        }

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
        ap_ids = self._collect_ap_ids_for_group_move()
        if not ap_ids:
            self._view_group_drag_state = None
            return False
        self._view_group_drag_state = {
            "start_scene": QPointF(scene_pos),
            "orig_positions": {
                point_id: QPointF(self._ap_items[point_id].pos())
                for point_id in ap_ids
                if point_id in self._ap_items
            },
        }
        return True

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
        self._selected_ap_ids = {pid for pid in ap_ids if pid in self._ap_nodes}
        self._selected_cable_ids = {cid for cid in cable_ids if cid in self._cable_edges}
        if active_cable and active_cable in self._selected_cable_ids:
            self._active_cable_id = active_cable
        else:
            self._active_cable_id = next(iter(self._selected_cable_ids), None)
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
