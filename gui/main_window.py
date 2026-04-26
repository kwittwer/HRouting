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
import json
import math
import os
import shutil
from pathlib import Path

from collections import defaultdict

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QToolBar,
    QFileDialog, QMessageBox, QStatusBar, QColorDialog,
    QComboBox, QLabel, QDialog, QTableWidget, QTableWidgetItem,
    QDialogButtonBox, QTabWidget, QPushButton, QHeaderView,
    QApplication, QCheckBox, QSpinBox, QDoubleSpinBox,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QPainter, QPageLayout,
    QPen, QBrush, QPolygonF, QPainterPath, QKeySequence,
)
from PySide6.QtCore import Qt, QSettings, QMarginsF, QRectF, QDateTime, QPointF
from PySide6.QtPrintSupport import QPrinter

from gui.canvas_widget import CanvasWidget, COLORS
from gui.parameter_panel import ParameterPanel
from logic.svg_parser import parse_svg_dimensions
from logic.heating_calc import calc_circuit, calc_balancing, FLOOR_COVERINGS

_SETTINGS = QSettings("HRouting", "HRouting")
_LAST_PROJECT_KEY = "last_project_path"
_RECENT_KEY = "recent_projects"
_MAX_RECENT = 8
_MAX_UNDO = 80

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from main import VERSION
        self._version = VERSION
        self.setWindowTitle(f"HRouting v{VERSION} – Fußbodenheizung und Kabel Planer")
        self.resize(1400, 900)

        self._svg_path: str | None = None
        self._project_path: Path | None = None
        self._circuit_counter = 0
        self._elec_point_counter = 0
        self._elec_cable_counter = 0
        self._hkv_counter = 0
        self._hkv_line_counter = 0
        self._floorplan_counter = 0
        self._furniture_counter = 0
        self._dirty = False

        # Undo / Redo
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._undo_blocked = False

        self._build_ui()
        self._build_toolbar()
        self._build_menubar()
        self._connect_signals()
        self._auto_load_last_project()

    # ------------------------------------------------------------------ #
    #  UI                                                                  #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.canvas      = CanvasWidget()
        self.param_panel = ParameterPanel()

        layout.addWidget(self.canvas,      stretch=1)
        layout.addWidget(self.param_panel, stretch=0)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(
            "Willkommen! SVG laden → Referenzlinie zeichnen → Heizkreis hinzufügen"
        )

    def _build_toolbar(self):
        tb = QToolBar("Werkzeuge")
        tb.setMovable(False)
        self.addToolBar(tb)

        for label, slot in [
            ("📄 Neues Projekt",          self._new_project),
            ("📂 Grundriss laden",         self._open_svg),
            ("💾 Speichern",              self._save_project),
            ("💾 Speichern unter…",       self._save_project_as),
            ("📂 Projekt öffnen…",        self._open_project),
            ("📤 SVG exportieren",        self._export_svg),
            ("📊 Projektübersicht",       self._export_lengths),
            ("📄 Als PDF exportieren",    self._export_pdf),
        ]:
            act = QAction(label, self)
            act.triggered.connect(slot)
            tb.addAction(act)
            tb.addSeparator()

        # Snap-angle dropdown
        tb.addSeparator()
        lbl = QLabel("  Fangwinkel: ")
        tb.addWidget(lbl)
        self._snap_combo = QComboBox()
        self._snap_combo.addItem("Aus",   0)
        self._snap_combo.addItem("45°",  45)
        self._snap_combo.addItem("90°",  90)
        self._snap_combo.addItem("120°", 120)
        self._snap_combo.setCurrentIndex(2)   # default 90°
        self._snap_combo.currentIndexChanged.connect(self._on_snap_angle_changed)
        tb.addWidget(self._snap_combo)

        # ── Grid controls ──────────────────────────────────────────────
        tb.addSeparator()
        self._grid_cb = QCheckBox(" Raster")
        self._grid_cb.setChecked(False)
        self._grid_cb.stateChanged.connect(self._on_grid_toggled)
        tb.addWidget(self._grid_cb)

        tb.addWidget(QLabel("  Abstand: "))
        self._grid_spin = QDoubleSpinBox()
        self._grid_spin.setDecimals(2)
        self._grid_spin.setRange(0.01, 10.0)
        self._grid_spin.setSingleStep(0.01)
        self._grid_spin.setValue(0.10)
        self._grid_spin.setSuffix(" m")
        self._grid_spin.setFixedWidth(120)
        self._grid_spin.valueChanged.connect(self._on_grid_spacing_changed)
        tb.addWidget(self._grid_spin)

        self._grid_color_btn = QPushButton("Rasterfarbe")
        self._grid_color_btn.setFixedWidth(90)
        self._grid_color_btn.clicked.connect(self._on_grid_color_pick)
        self._update_grid_color_btn(QColor(255, 255, 255, 60))
        tb.addWidget(self._grid_color_btn)

        self._bg_color_btn = QPushButton("Hintergrund")
        self._bg_color_btn.setFixedWidth(100)
        self._bg_color_btn.clicked.connect(self._on_bg_color_pick)
        self._update_bg_color_btn(QColor("#2b2b2b"))
        tb.addWidget(self._bg_color_btn)

        # ── Measurement tool ──
        tb.addSeparator()
        self._measure_btn = QPushButton("📏 Messen")
        self._measure_btn.setToolTip("Abstand zwischen zwei Punkten messen")
        self._measure_btn.setCheckable(True)
        self._measure_btn.setFixedWidth(80)
        self._measure_btn.clicked.connect(self._on_measure_toggled)
        tb.addWidget(self._measure_btn)

        self._clear_measure_btn = QPushButton("✕")
        self._clear_measure_btn.setToolTip("Alle Messlinien löschen")
        self._clear_measure_btn.setFixedWidth(28)
        self._clear_measure_btn.clicked.connect(self._on_clear_measurements)
        tb.addWidget(self._clear_measure_btn)

    def _build_menubar(self):
        mb = self.menuBar()

        # ── Datei ──
        file_menu = mb.addMenu("&Datei")
        file_menu.addAction("📄 Neues Projekt", self._new_project)
        file_menu.addAction("📂 Projekt öffnen…", self._open_project)
        file_menu.addSeparator()
        file_menu.addAction("💾 Speichern", self._save_project)
        file_menu.addAction("💾 Speichern unter…", self._save_project_as)
        file_menu.addSeparator()

        self._recent_menu = file_menu.addMenu("🕑 Letzte Projekte")
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        file_menu.addAction("Beenden", self.close)

        # ── Bearbeiten ──
        edit_menu = mb.addMenu("&Bearbeiten")
        self._undo_action = edit_menu.addAction("↩ Rückgängig")
        self._undo_action.setShortcut(QKeySequence.Undo)
        self._undo_action.triggered.connect(self._undo)
        self._undo_action.setEnabled(False)

        self._redo_action = edit_menu.addAction("↪ Wiederherstellen")
        self._redo_action.setShortcut(QKeySequence.Redo)
        self._redo_action.triggered.connect(self._redo)
        self._redo_action.setEnabled(False)

        # ── Hilfe ──
        help_menu = mb.addMenu("&Hilfe")
        help_menu.addAction("ℹ️ Über HRouting…", self._show_about)

    # -- Recent Projects ----------------------------------------------- #

    def _rebuild_recent_menu(self):
        self._recent_menu.clear()
        recent = _SETTINGS.value(_RECENT_KEY, [])
        if isinstance(recent, str):
            recent = [recent] if recent else []
        for path_str in recent:
            p = Path(path_str)
            if p.exists():
                act = self._recent_menu.addAction(p.name)
                act.setToolTip(str(p))
                act.triggered.connect(lambda checked, fp=p: self._open_recent(fp))
        if self._recent_menu.isEmpty():
            act = self._recent_menu.addAction("(keine)")
            act.setEnabled(False)

    def _add_to_recent(self, filepath: Path):
        recent = _SETTINGS.value(_RECENT_KEY, [])
        if isinstance(recent, str):
            recent = [recent] if recent else []
        s = str(filepath)
        if s in recent:
            recent.remove(s)
        recent.insert(0, s)
        recent = recent[:_MAX_RECENT]
        _SETTINGS.setValue(_RECENT_KEY, recent)
        self._rebuild_recent_menu()

    def _open_recent(self, filepath: Path):
        if not filepath.exists():
            QMessageBox.warning(self, "Datei nicht gefunden",
                                f"Die Datei existiert nicht mehr:\n{filepath}")
            return
        if not self._maybe_save():
            return
        self._project_path = filepath
        self._load_project(filepath)

    # -- About dialog -------------------------------------------------- #

    def _show_about(self):
        QMessageBox.about(
            self, "Über HRouting",
            f"<h2>HRouting v{self._version}</h2>"
            f"<p>Fußbodenheizung und Kabel Planer</p>"
            f"<p>Copyright © 2026 Konrad-Fabian Wittwer</p>"
            f"<p>Lizenz: GNU General Public License v3 (GPL-3.0)</p>"
            f"<hr>"
            f"<p>Erstellt mit Python 3 und PySide6 (Qt for Python).</p>"
            f"<p>Berechnungen basieren vereinfacht auf DIN EN 1264.</p>",
        )

    # -- Unsaved-changes guard ----------------------------------------- #

    def _mark_dirty(self, *_args):
        self._dirty = True
        self._push_undo()
        self._update_title()

    def _maybe_save(self) -> bool:
        """Ask the user to save if there are unsaved changes.
        Returns True if the caller may proceed, False to cancel."""
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self, "Ungespeicherte Änderungen",
            "Es gibt ungespeicherte Änderungen.\nMöchten Sie vorher speichern?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Save:
            self._save_project()
            return True
        if reply == QMessageBox.Discard:
            return True
        return False   # Cancel

    def closeEvent(self, event):
        if self._maybe_save():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------ #
    #  Undo / Redo                                                        #
    # ------------------------------------------------------------------ #

    def _capture_snapshot(self) -> dict:
        """Return a deep-copied snapshot of the current project state."""
        return copy.deepcopy({
            "canvas": self.canvas.to_dict(),
            "params": self.param_panel.to_dict(),
            "counters": {
                "circuit": self._circuit_counter,
                "elec_point": self._elec_point_counter,
                "elec_cable": self._elec_cable_counter,
                "hkv": self._hkv_counter,
                "hkv_line": self._hkv_line_counter,
                "floorplan": self._floorplan_counter,
                "furniture": self._furniture_counter,
            },
        })

    def _push_undo(self):
        """Save the current state onto the undo stack."""
        if self._undo_blocked:
            return
        snap = self._capture_snapshot()
        self._undo_stack.append(snap)
        if len(self._undo_stack) > _MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_actions()

    def _undo(self):
        if not self._undo_stack:
            return
        # Save current state to redo before restoring
        self._redo_stack.append(self._capture_snapshot())
        snap = self._undo_stack.pop()
        self._restore_snapshot(snap)
        self._update_undo_actions()
        self.status.showMessage("↩ Rückgängig")

    def _redo(self):
        if not self._redo_stack:
            return
        # Save current state to undo before restoring
        self._undo_stack.append(self._capture_snapshot())
        snap = self._redo_stack.pop()
        self._restore_snapshot(snap)
        self._update_undo_actions()
        self.status.showMessage("↪ Wiederherstellen")

    def _update_undo_actions(self):
        if hasattr(self, '_undo_action'):
            self._undo_action.setEnabled(bool(self._undo_stack))
            self._redo_action.setEnabled(bool(self._redo_stack))

    def _restore_snapshot(self, snap: dict):
        """Restore canvas + param panel from a snapshot dict."""
        self._undo_blocked = True
        try:
            # Clear existing data
            self.canvas.clear_data()
            self.param_panel.clear_all_panels()

            # Restore canvas geometry
            self.canvas.from_dict(snap["canvas"])

            # Restore param panels
            self.param_panel.from_dict(snap["params"])

            # Restore counters
            c = snap.get("counters", {})
            self._circuit_counter = c.get("circuit", 0)
            self._elec_point_counter = c.get("elec_point", 0)
            self._elec_cable_counter = c.get("elec_cable", 0)
            self._hkv_counter = c.get("hkv", 0)
            self._hkv_line_counter = c.get("hkv_line", 0)
            self._floorplan_counter = c.get("floorplan", 0)
            self._furniture_counter = c.get("furniture", 0)

            # Reconnect panel signals + sync visual state
            self._reconnect_panels_after_restore()
            self._sync_toolbar_from_canvas()

            self._dirty = True
            self._update_title()
            self.canvas.update()
        finally:
            self._undo_blocked = False

    def _reconnect_panels_after_restore(self):
        """Reconnect per-object panel signals after an undo/redo restore."""
        # Floorplan panels: restore images + scale labels
        for fid, panel in self.param_panel.floorplan_panels.items():
            fp = panel._file_path
            if fp and os.path.exists(fp):
                self.canvas.load_floor_plan_image(fid, fp)
            layer = self.canvas._floor_plans.get(fid)
            if layer and layer.mm_per_px != 1.0:
                panel.update_scale_label(layer.mm_per_px)

        # Furniture panels: restore images + scale labels
        for fur_id, panel in self.param_panel.furniture_panels.items():
            fp = panel._file_path
            if fp and os.path.exists(fp):
                self.canvas.load_floor_plan_image(fur_id, fp)
            layer = self.canvas._floor_plans.get(fur_id)
            if layer and layer.mm_per_px != 1.0:
                panel.update_scale_label(layer.mm_per_px)

        for cid, panel in self.param_panel.circuit_panels.items():
            panel.draw_route_requested.connect(self._start_manual_route)
            panel.edit_polygon_requested.connect(self._on_edit_polygon_requested)
            panel.edit_route_requested.connect(self._on_edit_route_requested)
            panel.draw_supply_requested.connect(self._start_supply_line)
            panel.edit_supply_requested.connect(self._on_edit_supply_requested)
            panel.name_changed.connect(self._on_circuit_name_changed)
            panel.color_changed.connect(self._on_circuit_color_changed)
            panel.spacing_changed.connect(self._on_spacing_changed)
            panel.wall_dist_changed.connect(self._on_wall_dist_changed)
            panel.visibility_changed.connect(self._on_visibility_changed)
            panel.label_size_changed.connect(self._on_label_size_changed)
            panel.hydraulics_param_changed.connect(self._recalc_circuit_hydraulics)
            values = panel.get_parameters()
            self.canvas.set_polygon_name(cid, values["name"])
            self.canvas.set_color(cid, QColor(values["color"]))
            self.canvas._circuit_visible[cid] = values.get("visible", True)
            self.canvas.set_label_font_size(cid, values.get("label_size", 12.0))
            self._update_circuit_area(cid)
            route_mm = self.canvas.get_manual_route_length_px(cid) * self.canvas.get_mm_per_px()
            self.param_panel.set_circuit_length(cid, route_mm)
            supply_mm = self.canvas.get_supply_line_length_px(cid) * self.canvas.get_mm_per_px()
            self.param_panel.set_supply_length(cid, supply_mm)
            self.param_panel.set_total_length(cid, route_mm, supply_mm)
            self._recalc_circuit_hydraulics(cid)

        for pid, panel in self.param_panel.elec_point_panels.items():
            panel.place_requested.connect(self._on_place_elec_point)
            panel.size_changed.connect(self._on_elec_point_size_changed)
            panel.icon_changed.connect(self._on_elec_point_icon_changed)
            panel.name_changed.connect(self._on_elec_point_name_changed)
            panel.color_changed.connect(self._on_elec_point_color_changed)
            panel.visibility_changed.connect(self._on_elec_visibility_changed)
            panel.label_size_changed.connect(self._on_label_size_changed)
            values = panel.get_parameters()
            self.canvas._label_map[pid] = values.get("name", pid)
            self.canvas._elec_visible[pid] = values.get("visible", True)
            self.canvas.set_label_font_size(pid, values.get("label_size", 12.0))
            self.canvas.set_color(pid, QColor(values.get("color", "#4fc3f7")))
            if values.get("icon_path"):
                self.canvas.set_elec_point_icon(pid, values["icon_path"])

        for kid, panel in self.param_panel.elec_cable_panels.items():
            panel.draw_cable_requested.connect(self._on_draw_elec_cable)
            panel.edit_cable_requested.connect(self._on_edit_elec_cable)
            panel.name_changed.connect(self._on_elec_cable_name_changed)
            panel.color_changed.connect(self._on_elec_cable_color_changed)
            panel.visibility_changed.connect(self._on_elec_visibility_changed)
            panel.label_size_changed.connect(self._on_label_size_changed)
            values = panel.get_parameters()
            self.canvas._label_map[kid] = values.get("name", kid)
            self.canvas._elec_visible[kid] = values.get("visible", True)
            self.canvas.set_label_font_size(kid, values.get("label_size", 12.0))
            self.canvas.set_color(kid, QColor(values.get("color", "#ff9800")))
            length_px = self.canvas.get_elec_cable_length_px(kid)
            length_mm = length_px * self.canvas.get_mm_per_px()
            self.param_panel.set_cable_length(kid, length_mm)
            self._update_cable_ap_labels(kid)

        for hid, panel in self.param_panel.hkv_panels.items():
            panel.place_requested.connect(self._on_place_hkv)
            panel.size_changed.connect(self._on_hkv_size_changed)
            panel.icon_changed.connect(self._on_hkv_icon_changed)
            panel.name_changed.connect(self._on_hkv_name_changed)
            panel.color_changed.connect(self._on_hkv_color_changed)
            panel.visibility_changed.connect(self._on_hkv_visibility_changed)
            panel.label_size_changed.connect(self._on_label_size_changed)
            values = panel.get_parameters()
            self.canvas._label_map[hid] = values.get("name", hid)
            self.canvas._hkv_visible[hid] = values.get("visible", True)
            self.canvas.set_label_font_size(hid, values.get("label_size", 12.0))
            self.canvas.set_color(hid, QColor(values.get("color", "#e53935")))
            if values.get("icon_path"):
                self.canvas.set_hkv_icon(hid, values["icon_path"])

        for lid, panel in self.param_panel.hkv_line_panels.items():
            panel.draw_line_requested.connect(self._on_draw_hkv_line)
            panel.edit_line_requested.connect(self._on_edit_hkv_line)
            panel.name_changed.connect(self._on_hkv_line_name_changed)
            panel.color_changed.connect(self._on_hkv_line_color_changed)
            panel.visibility_changed.connect(self._on_hkv_line_visibility_changed)
            panel.label_size_changed.connect(self._on_label_size_changed)
            values = panel.get_parameters()
            self.canvas._label_map[lid] = values.get("name", lid)
            self.canvas._hkv_line_visible[lid] = values.get("visible", True)
            self.canvas.set_label_font_size(lid, values.get("label_size", 12.0))
            self.canvas.set_color(lid, QColor(values.get("color", "#e53935")))
            length_px = self.canvas.get_hkv_line_length_px(lid)
            length_mm = length_px * self.canvas.get_mm_per_px()
            self.param_panel.set_hkv_line_length(lid, length_mm)
            self._update_hkv_line_labels(lid)

        for cid in self.param_panel.circuit_panels:
            self._update_supply_hkv_label(cid)

    def _connect_signals(self):
        self.canvas.polygon_finished.connect(self._on_polygon_finished)
        self.canvas.mode_changed.connect(self._on_canvas_mode_changed)
        self.canvas.ref_line_set.connect(self._on_ref_line_drawn)
        self.canvas.start_point_moved.connect(self._on_start_point_moved)
        self.canvas.route_changed.connect(self._on_route_changed)
        self.canvas.supply_line_changed.connect(self._on_supply_line_changed)
        self.canvas.elec_point_placed.connect(self._on_elec_point_placed)
        self.canvas.elec_cable_changed.connect(self._on_elec_cable_changed)
        self.canvas.hkv_placed.connect(self._on_hkv_placed)
        self.canvas.hkv_line_changed.connect(self._on_hkv_line_changed)
        self.canvas.object_double_clicked.connect(self._on_object_double_clicked)
        self.canvas.floor_plan_transform_updated.connect(
            self._on_floor_plan_transform_from_canvas)

        self.param_panel.delete_requested.connect(self._delete_circuit)
        self.param_panel.add_floorplan_requested.connect(self._add_floorplan)
        self.param_panel.delete_floorplan_requested.connect(self._delete_floorplan)
        self.param_panel.floorplan_file_browse.connect(self._browse_floorplan_file)
        self.param_panel.floorplan_ref_line.connect(self._on_floorplan_ref_line)
        self.param_panel.floorplan_ref_confirmed.connect(self._on_floorplan_ref_confirmed)
        self.param_panel.floorplan_transform_changed.connect(self._on_floorplan_transform)
        self.param_panel.floorplan_opacity_changed.connect(self._on_floorplan_opacity)
        self.param_panel.floorplan_visibility_changed.connect(self._on_floorplan_visibility)
        self.param_panel.floorplan_move_requested.connect(self._on_floorplan_move)
        self.param_panel.floorplan_rotate_requested.connect(self._on_floorplan_rotate)
        self.param_panel.floorplan_order_changed.connect(self._on_floorplan_order_changed)
        self.param_panel.add_circuit_requested.connect(self._add_circuit)
        self.param_panel.add_elec_point_requested.connect(self._add_elec_point)
        self.param_panel.add_elec_cable_requested.connect(self._add_elec_cable)
        self.param_panel.delete_elec_point_requested.connect(self._delete_elec_point)
        self.param_panel.delete_elec_cable_requested.connect(self._delete_elec_cable)
        self.param_panel.duplicate_elec_point_requested.connect(self._duplicate_elec_point)
        self.param_panel.duplicate_elec_cable_requested.connect(self._duplicate_elec_cable)
        self.param_panel.add_hkv_requested.connect(self._add_hkv)
        self.param_panel.add_hkv_line_requested.connect(self._add_hkv_line)
        self.param_panel.delete_hkv_requested.connect(self._delete_hkv)
        self.param_panel.delete_hkv_line_requested.connect(self._delete_hkv_line)
        self.param_panel.add_furniture_requested.connect(self._add_furniture)
        self.param_panel.delete_furniture_requested.connect(self._delete_furniture)
        self.param_panel.furniture_size_changed.connect(self._on_furniture_size_changed)
        self.param_panel.heating_global_changed.connect(self._recalc_all_circuits)

        # Dirty-tracking: jede inhaltliche Änderung markiert als unsaved
        self.canvas.polygon_finished.connect(self._mark_dirty)
        self.canvas.route_changed.connect(self._mark_dirty)
        self.canvas.supply_line_changed.connect(self._mark_dirty)
        self.canvas.elec_point_placed.connect(self._mark_dirty)
        self.canvas.elec_cable_changed.connect(self._mark_dirty)
        self.canvas.hkv_placed.connect(self._mark_dirty)
        self.canvas.hkv_line_changed.connect(self._mark_dirty)
        self.canvas.ref_line_set.connect(self._mark_dirty)
        self.canvas.start_point_moved.connect(self._mark_dirty)
        self.param_panel.heating_global_changed.connect(self._mark_dirty)

    # ------------------------------------------------------------------ #
    #  Slots                                                               #
    # ------------------------------------------------------------------ #

    def _on_snap_angle_changed(self, index: int):
        angle = self._snap_combo.itemData(index)
        self.canvas._snap_angle = float(angle)

    # -- Grid callbacks ------------------------------------------------ #
    def _on_grid_toggled(self, state):
        self.canvas._grid_visible = bool(state)
        self.canvas.update()

    def _on_grid_spacing_changed(self, value: float):
        self.canvas._grid_spacing_mm = value * 1000.0
        self.canvas.update()

    def _on_grid_color_pick(self):
        cur = self.canvas._grid_color
        col = QColorDialog.getColor(
            cur, self, "Rasterfarbe wählen",
            QColorDialog.ShowAlphaChannel,
        )
        if col.isValid():
            self.canvas._grid_color = col
            self._update_grid_color_btn(col)
            self.canvas.update()

    def _update_grid_color_btn(self, color: QColor):
        r, g, b, a = color.red(), color.green(), color.blue(), color.alpha()
        self._grid_color_btn.setStyleSheet(
            f"background-color: rgba({r},{g},{b},{a}); border: 1px solid #888;"
        )

    def _on_bg_color_pick(self):
        cur = self.canvas._bg_color
        col = QColorDialog.getColor(cur, self, "Hintergrundfarbe wählen")
        if col.isValid():
            self.canvas._bg_color = col
            self._update_bg_color_btn(col)
            self.canvas.update()

    def _update_bg_color_btn(self, color: QColor):
        r, g, b = color.red(), color.green(), color.blue()
        # choose light/dark text for readability
        text_col = "#fff" if (r * 0.299 + g * 0.587 + b * 0.114) < 128 else "#000"
        self._bg_color_btn.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); color: {text_col}; border: 1px solid #888;"
        )

    def _on_measure_toggled(self, checked: bool):
        if checked:
            self.canvas.start_measure()
            self.status.showMessage(
                "📏 Messen – Linksklick: Punkt setzen, "
                "Rechtsklick: abbrechen, ESC: beenden")
        else:
            from gui.canvas_widget import ToolMode
            if self.canvas._mode == ToolMode.MEASURE:
                self.canvas._mode = ToolMode.NONE
                self.canvas._measure_p1 = None
                self.canvas._measure_p2 = None
                self.canvas.setCursor(Qt.ArrowCursor)
                self.canvas.update()
            self.status.clearMessage()

    def _on_clear_measurements(self):
        self.canvas.clear_measurements()
        self.status.showMessage("Messlinien gelöscht", 2000)

    def _on_canvas_mode_changed(self):
        from gui.canvas_widget import ToolMode
        if self.canvas._mode != ToolMode.MEASURE:
            self._measure_btn.blockSignals(True)
            self._measure_btn.setChecked(False)
            self._measure_btn.blockSignals(False)

    def _sync_toolbar_from_canvas(self):
        """Synchronise toolbar widgets with the current canvas state."""
        c = self.canvas
        # Grid
        self._grid_cb.blockSignals(True)
        self._grid_cb.setChecked(c._grid_visible)
        self._grid_cb.blockSignals(False)

        self._grid_spin.blockSignals(True)
        self._grid_spin.setValue(c._grid_spacing_mm / 1000.0)
        self._grid_spin.blockSignals(False)

        self._update_grid_color_btn(c._grid_color)
        self._update_bg_color_btn(c._bg_color)

        # Snap angle
        angle = c._snap_angle
        idx = self._snap_combo.findData(int(angle))
        if idx >= 0:
            self._snap_combo.blockSignals(True)
            self._snap_combo.setCurrentIndex(idx)
            self._snap_combo.blockSignals(False)

    def _on_visibility_changed(self, circuit_id: str, visible: bool):
        self.canvas._circuit_visible[circuit_id] = visible
        self.canvas.update()

    def _open_svg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Grundriss öffnen", "",
            "Bilder (*.svg *.png *.jpg *.jpeg *.bmp);;SVG (*.svg);;Rasterbilder (*.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return
        # Create a new floor plan layer for this image
        self._floorplan_counter += 1
        fp_id = f"grundriss-{self._floorplan_counter}"
        name = Path(path).stem
        self.canvas.add_floor_plan(fp_id, filepath=path)
        panel = self.param_panel.add_floorplan_panel(fp_id, name=name)
        panel.set_file_path(path)
        self._svg_path = path
        self._fit_window_to_svg()
        self._mark_dirty()
        self._push_undo()
        self.status.showMessage(
            f"Grundriss geladen: {Path(path).name}  |  "
            "Jetzt Referenzlinie im Grundriss-Panel zeichnen!"
        )

    def _fit_window_to_svg(self):
        """Resize the main window so the canvas matches the SVG width."""
        svg_w, svg_h = self.canvas._svg_size
        if svg_w <= 0 or svg_h <= 0:
            return

        # Determine available screen geometry
        screen = self.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        avail = screen.availableGeometry()

        # Panel width + some margin for frame/borders
        panel_w = self.param_panel.width()
        chrome_w = self.width() - self.canvas.width()  # toolbar, borders, etc.
        chrome_h = self.height() - self.canvas.height()  # toolbar, statusbar

        desired_w = int(svg_w) + chrome_w
        desired_h = int(svg_h) + chrome_h

        # Clamp to available screen size (leave a small margin)
        max_w = int(avail.width() * 0.95)
        max_h = int(avail.height() * 0.95)
        new_w = min(desired_w, max_w)
        new_h = min(desired_h, max_h)

        self.resize(new_w, new_h)
        # Re-center on screen if needed
        geo = self.geometry()
        if geo.right() > avail.right() or geo.bottom() > avail.bottom():
            self.move(
                max(avail.left(), (avail.width() - new_w) // 2 + avail.left()),
                max(avail.top(), (avail.height() - new_h) // 2 + avail.top()),
            )

    def _on_ref_line_drawn(self):
        """Referenzlinie wurde gezeichnet – Nutzer zur Längeneingabe auffordern."""
        self.status.showMessage(
            "✏️ Referenzlinie gezeichnet!  "
            "Jetzt im Panel rechts die reale Länge eingeben und '✔ Anwenden' klicken."
        )

    def _on_ref_length_confirmed(self, length_mm: float):
        """Länge wurde im Panel eingegeben und bestätigt."""
        p1 = self.canvas._ref_p1
        p2 = self.canvas._ref_p2

        if p1 is None or p2 is None:
            self.status.showMessage(
                "⚠️ Bitte zuerst eine Referenzlinie zeichnen (Schritt ①)!"
            )
            return

        px_len = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        if px_len < 1.0:
            self.status.showMessage(
                "⚠️ Referenzlinie zu kurz – bitte neu zeichnen."
            )
            return

        mm_per_px = length_mm / px_len
        self.canvas.set_mm_per_px(mm_per_px)
        self.status.showMessage(
            f"✅ Maßstab gesetzt: {mm_per_px / 1000:.6f} m/px  "
            f"({length_mm / 1000:.3f} m ÷ {px_len:.1f} px)"
        )

    # ── Floor plan management ─────────────────────────────────────

    def _add_floorplan(self):
        """Add a new empty floor plan layer."""
        self._floorplan_counter += 1
        fp_id = f"grundriss-{self._floorplan_counter}"
        name = f"Grundriss {self._floorplan_counter}"
        self.canvas.add_floor_plan(fp_id)
        panel = self.param_panel.add_floorplan_panel(fp_id, name=name)
        self._connect_floorplan_panel_signals(panel)
        self._mark_dirty()
        self._push_undo()

    def _delete_floorplan(self, fp_id: str):
        self.canvas.remove_floor_plan(fp_id)
        self.param_panel.remove_floorplan_panel(fp_id)
        self._mark_dirty()
        self._push_undo()

    def _add_furniture(self, parent_fp_id: str):
        """Add a new furniture element under a floor plan."""
        self._furniture_counter += 1
        fur_id = f"einr-{self._furniture_counter}"
        name = f"Einrichtung {self._furniture_counter}"
        self.canvas.add_floor_plan(fur_id)
        # Insert furniture right after the last existing furniture of the same parent
        order = self.canvas._floor_plan_order
        if parent_fp_id in order:
            order.remove(fur_id)
            parent_idx = order.index(parent_fp_id)
            insert_idx = parent_idx + 1
            for i in range(parent_idx + 1, len(order)):
                sibling = order[i]
                if self.param_panel._furniture_parent.get(sibling) == parent_fp_id:
                    insert_idx = i + 1
                elif sibling in self.param_panel.floorplan_panels:
                    break
            order.insert(insert_idx, fur_id)
        self.param_panel.add_furniture_panel(fur_id, parent_fp_id, name=name)
        self._mark_dirty()
        self._push_undo()
        self.status.showMessage(
            f"{fur_id}: Bild laden (\U0001f4c2) \u2192 Referenzlinie zeichnen \u2192 Positionieren"
        )

    def _delete_furniture(self, fur_id: str):
        self.canvas.remove_floor_plan(fur_id)
        self.param_panel.remove_furniture_panel(fur_id)
        self._mark_dirty()
        self._push_undo()
        self.status.showMessage(f"\U0001f5d1\ufe0f Einrichtungselement {fur_id} gel\u00f6scht.")

    def _on_furniture_size_changed(self, fur_id: str):
        """Feste Abmessungen einer Einrichtung wurden im Panel geändert."""
        panel = self.param_panel.furniture_panels.get(fur_id)
        if not panel:
            return
        p = panel.get_parameters()
        self.canvas.set_floor_plan_size_mm(
            fur_id,
            p.get("fixed_width_mm", 0.0),
            p.get("fixed_height_mm", 0.0),
        )
        self._mark_dirty()

    def _browse_floorplan_file(self, fp_id: str):
        path, _ = QFileDialog.getOpenFileName(
            self, "Bild für Grundriss wählen", "",
            "Bilder (*.svg *.png *.jpg *.jpeg *.bmp);;SVG (*.svg);;Rasterbilder (*.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return
        panel = self.param_panel.floorplan_panels.get(fp_id)
        if panel is None:
            panel = self.param_panel.furniture_panels.get(fp_id)
        if panel:
            panel.set_file_path(path)
        self.canvas.load_floor_plan_image(fp_id, path)
        # If this is the first floor plan, set legacy svg_path + fit window
        if not self._svg_path:
            self._svg_path = path
            self._fit_window_to_svg()
        self._mark_dirty()
        self._push_undo()

    def _on_floorplan_ref_line(self, fp_id: str):
        self.canvas.start_ref_line_for_floor(fp_id)

    def _on_floorplan_ref_confirmed(self, fp_id: str, length_mm: float):
        layer = self.canvas._floor_plans.get(fp_id)
        if not layer:
            return
        p1 = layer.ref_p1 or self.canvas._ref_p1
        p2 = layer.ref_p2 or self.canvas._ref_p2
        if p1 is None or p2 is None:
            self.status.showMessage(
                "⚠️ Bitte zuerst eine Referenzlinie zeichnen (Schritt ①)!"
            )
            return
        px_len = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        if px_len < 1.0:
            self.status.showMessage(
                "⚠️ Referenzlinie zu kurz – bitte neu zeichnen."
            )
            return

        # Compute old/new render scale so ref points can be rescaled
        old_global = self.canvas._mm_per_px if self.canvas._mm_per_px > 0 else 1.0
        old_ls = layer.mm_per_px / old_global if layer.mm_per_px > 0 else 1.0

        mm_per_px = length_mm / px_len
        layer.mm_per_px = mm_per_px
        layer.ref_length_mm = length_mm
        # Use the first calibrated floor plan as global reference scale
        if self.canvas._mm_per_px == 1.0 or (
                self.canvas._floor_plan_order
                and self.canvas._floor_plan_order[0] == fp_id):
            self.canvas.set_mm_per_px(mm_per_px)

        new_global = self.canvas._mm_per_px if self.canvas._mm_per_px > 0 else 1.0
        new_ls = mm_per_px / new_global if mm_per_px > 0 else 1.0

        # Rescale ref points so they stay on the correct image position
        self.canvas.rescale_layer_ref_points(fp_id, old_ls, new_ls)

        # Trigger repaint so all layers rescale to match
        self.canvas.update()
        panel = self.param_panel.floorplan_panels.get(fp_id)
        if panel:
            panel.update_scale_label(mm_per_px)
        self.status.showMessage(
            f"✅ Maßstab für {panel.le_name.text() if panel else fp_id}: "
            f"{mm_per_px / 1000:.6f} m/px"
        )
        self._mark_dirty()

    def _on_floorplan_transform(self, fp_id: str):
        panel = self.param_panel.floorplan_panels.get(fp_id)
        if not panel:
            return
        p = panel.get_parameters()
        self.canvas.set_floor_plan_transform(
            fp_id, p["offset_x"], p["offset_y"], p["rotation"]
        )
        self._mark_dirty()

    def _on_floorplan_opacity(self, fp_id: str, opacity: float):
        self.canvas.set_floor_plan_opacity(fp_id, opacity)
        self._mark_dirty()

    def _on_floorplan_visibility(self, fp_id: str, visible: bool):
        self.canvas.set_floor_plan_visible(fp_id, visible)

    def _on_floorplan_move(self, fp_id: str):
        self.canvas.start_move_floor_plan(fp_id)
        self.status.showMessage(
            f"\u2725 Grundriss verschieben \u2013 Ziehen mit linker Maustaste, ESC zum Beenden")

    def _on_floorplan_rotate(self, fp_id: str):
        self.canvas.start_rotate_floor_plan(fp_id)
        self.status.showMessage(
            f"\u21bb Grundriss drehen \u2013 Ziehen mit linker Maustaste, ESC zum Beenden")

    def _on_floor_plan_transform_from_canvas(self, fp_id: str,
                                              offset_x: float,
                                              offset_y: float,
                                              rotation: float):
        """Canvas finished a mouse-based move/rotate \u2013 sync spinboxes."""
        panel = self.param_panel.floorplan_panels.get(fp_id)
        if panel:
            panel.set_transform_silent(offset_x, offset_y, rotation)
        self._mark_dirty()

    def _on_floorplan_order_changed(self):
        """Tree order of floor plans changed – sync canvas render order."""
        order = self.param_panel.get_full_render_order()
        self.canvas.set_floor_plan_order(order)
        self.canvas.update()
        self._mark_dirty()

    def _connect_floorplan_panel_signals(self, panel):
        """Connect per-panel signals that need main_window slots (for load_project)."""
        pass  # All signals route through ParameterPanel aggregate signals

    def _add_circuit(self, fp_id: str = ""):
        if not self._svg_path and not self.canvas._floor_plans:
            QMessageBox.warning(self, "Kein Grundriss",
                                "Bitte zuerst einen Grundriss hinzufügen.")
            return
        if self.canvas.get_mm_per_px() == 1.0:
            reply = QMessageBox.question(
                self, "Kein Maßstab",
                "Es wurde noch kein Maßstab gesetzt.\nTrotzdem fortfahren?",
            )
            if reply != QMessageBox.Yes:
                return

        self._circuit_counter += 1
        cid = f"HK-{self._circuit_counter}"
        color = COLORS[(self._circuit_counter - 1) % len(COLORS)]
        self._create_circuit_panel(cid, fp_id=fp_id or None, name=cid, color=color)
        self.canvas.start_drawing(cid)
        self.status.showMessage(
            f"{cid}: Polygon zeichnen  |  "
            "Linksklick = Punkt  |  Rechtsklick = Fertig  |  ESC = Abbruch"
        )

    def _on_polygon_finished(self, circuit_id: str, points: list):
        self.status.showMessage(
            f"✅ Polygon {circuit_id} fertig.  |  "
            "Startpunkt (◆) verschieben oder 'Rohrverlauf zeichnen' drücken."
        )
        self._update_circuit_area(circuit_id)

    def _create_circuit_panel(self, circuit_id: str,
                              fp_id: str | None = None,
                              name: str | None = None,
                              color: str | None = None):
        panel = self.param_panel.add_circuit_panel(
            circuit_id, fp_id=fp_id, name=name, color=color
        )
        panel.draw_route_requested.connect(self._start_manual_route)
        panel.edit_polygon_requested.connect(self._on_edit_polygon_requested)
        panel.edit_route_requested.connect(self._on_edit_route_requested)
        panel.draw_supply_requested.connect(self._start_supply_line)
        panel.edit_supply_requested.connect(self._on_edit_supply_requested)
        panel.name_changed.connect(self._on_circuit_name_changed)
        panel.color_changed.connect(self._on_circuit_color_changed)
        panel.spacing_changed.connect(self._on_spacing_changed)
        panel.wall_dist_changed.connect(self._on_wall_dist_changed)
        panel.visibility_changed.connect(self._on_visibility_changed)
        panel.label_size_changed.connect(self._on_label_size_changed)
        panel.hydraulics_param_changed.connect(self._recalc_circuit_hydraulics)
        return panel

    def _start_manual_route(self, circuit_id: str):
        params = self.param_panel.get_circuit_params(circuit_id)
        if not params:
            return
        px_points = self.canvas.get_polygon_px(circuit_id)
        if len(px_points) < 3:
            QMessageBox.warning(
                self, "Kein Polygon",
                f"Bitte zuerst ein Polygon für {circuit_id} zeichnen."
            )
            return
        self.canvas.start_route_drawing(
            circuit_id,
            wall_distance_mm=params["wall_dist"],
            line_distance_mm=params["spacing"],
        )
        self.status.showMessage(
            f"{circuit_id}: Manuellen Rohrverlauf zeichnen  |  "
            "Linksklick = Punkt  |  Rechtsklick = Fertig  |  ESC = Abbruch"
        )

    def _on_route_changed(self, circuit_id: str):
        length_px = self.canvas.get_manual_route_length_px(circuit_id)
        length_mm = length_px * self.canvas.get_mm_per_px()
        self.param_panel.set_circuit_length(circuit_id, length_mm)
        self._update_total_length(circuit_id)
        self.status.showMessage(
            f"✅ {circuit_id}: Manueller Rohrverlauf aktualisiert "
            f"({length_mm / 1000:.2f} m)"
        )

    def _on_supply_line_changed(self, circuit_id: str):
        supply_px = self.canvas.get_supply_line_length_px(circuit_id)
        supply_mm = supply_px * self.canvas.get_mm_per_px()
        self.param_panel.set_supply_length(circuit_id, supply_mm)
        self._update_total_length(circuit_id)
        self._recalc_circuit_hydraulics(circuit_id)
        self._update_supply_hkv_label(circuit_id)
        self.status.showMessage(
            f"✅ {circuit_id}: Zuleitung aktualisiert ({supply_mm / 1000:.2f} m)")

    def _update_total_length(self, circuit_id: str):
        scale = self.canvas.get_mm_per_px()
        route_mm = self.canvas.get_manual_route_length_px(circuit_id) * scale
        supply_mm = self.canvas.get_supply_line_length_px(circuit_id) * scale
        self.param_panel.set_total_length(circuit_id, route_mm, supply_mm)

    def _start_supply_line(self, circuit_id: str):
        sp = self.canvas.get_start_point_px(circuit_id)
        if not sp:
            QMessageBox.warning(
                self, "Kein Startpunkt",
                f"Bitte zuerst ein Polygon für {circuit_id} zeichnen."
            )
            return
        self.canvas.start_draw_supply_line(circuit_id)
        self.status.showMessage(
            f"{circuit_id}: Zuleitung zeichnen (ab Punkt S)  |  "
            "Linksklick = Punkt  |  Rechtsklick = Fertig  |  ESC = Abbruch"
        )

    def _on_edit_supply_requested(self, circuit_id: str):
        self.canvas.start_edit_supply_line(circuit_id)
        self.status.showMessage(
            f"Zuleitung bearbeiten: Links=Verschieben, Rechts auf Punkt=Löschen, "
            f"Rechts auf Kante=Einfügen, Mitteltaste/ESC=Beenden."
        )

    def _on_spacing_changed(self, circuit_id: str):
        params = self.param_panel.get_circuit_params(circuit_id)
        if not params:
            return
        scale = self.canvas.get_mm_per_px()
        line_dist_px = max(0.0, params["spacing"] / scale)
        self.canvas._route_line_dist_px[circuit_id] = line_dist_px
        length_px = self.canvas.get_manual_route_length_px(circuit_id)
        length_mm = length_px * self.canvas.get_mm_per_px()
        self.param_panel.set_circuit_length(circuit_id, length_mm)
        self._update_total_length(circuit_id)
        self._recalc_circuit_hydraulics(circuit_id)
        self.canvas.update()

    def _on_wall_dist_changed(self, circuit_id: str):
        params = self.param_panel.get_circuit_params(circuit_id)
        if not params:
            return
        scale = self.canvas.get_mm_per_px()
        wall_dist_px = max(0.0, params["wall_dist"] / scale)
        self.canvas._route_wall_dist_px[circuit_id] = wall_dist_px
        self.canvas.update()

    def _on_object_double_clicked(self, obj_type: str, obj_id: str):
        """Dispatch double-click on a canvas object to the matching edit mode."""
        # Select the item in the tree first
        self.param_panel.select_item(obj_id)

        if obj_type == "elec_point":
            # AP has no special edit mode – just select it
            self.status.showMessage(f"Anschlusspunkt '{obj_id}' ausgewählt.")
        elif obj_type == "hkv":
            self.status.showMessage(f"HKV '{obj_id}' ausgewählt.")
        elif obj_type == "elec_cable":
            self._on_edit_elec_cable(obj_id)
        elif obj_type == "hkv_line":
            self._on_edit_hkv_line(obj_id)
        elif obj_type == "supply_line":
            self._on_edit_supply_requested(obj_id)
        elif obj_type == "route":
            self._on_edit_route_requested(obj_id)
        elif obj_type == "polygon":
            self._on_edit_polygon_requested(obj_id)

    def _on_edit_polygon_requested(self, circuit_id: str):
        self.canvas.start_edit_polygon(circuit_id)
        self.status.showMessage(
            f"Polygon bearbeiten: Linksklick zum Verschieben, Rechtsklick auf Punkt zum Löschen, "
            f"Rechtsklick auf Kante zum Einfügen, Mitteltaste oder ESC zum Beenden."
        )

    def _on_edit_route_requested(self, circuit_id: str):
        self.canvas.start_edit_route(circuit_id)
        self.status.showMessage(
            f"Rohrverlauf bearbeiten: Linksklick zum Verschieben, Rechtsklick auf Punkt zum Löschen, "
            f"Rechtsklick auf Kante zum Einfügen, Mitteltaste oder ESC zum Beenden."
        )

    def _on_circuit_name_changed(self, circuit_id: str, name: str):
        self.canvas.set_polygon_name(circuit_id, name)

    def _on_circuit_color_changed(self, circuit_id: str, color: str):
        self.canvas.set_color(circuit_id, QColor(color))

    def _on_label_size_changed(self, item_id: str, size: float):
        self.canvas.set_label_font_size(item_id, size)

    def _compute_polygon_area_mm2(self, circuit_id: str) -> float | None:
        px_points = self.canvas.get_polygon_px(circuit_id)
        if len(px_points) < 3:
            return None
        area_px = 0.0
        n = len(px_points)
        for i in range(n):
            x1, y1 = px_points[i]
            x2, y2 = px_points[(i + 1) % n]
            area_px += x1 * y2 - x2 * y1
        area_px = abs(area_px) / 2.0
        scale = self.canvas.get_mm_per_px()
        return area_px * scale * scale

    def _update_circuit_area(self, circuit_id: str):
        area_mm2 = self._compute_polygon_area_mm2(circuit_id)
        if area_mm2 is not None:
            self.param_panel.set_circuit_area(circuit_id, area_mm2)
        self._recalc_circuit_hydraulics(circuit_id)

    # ------------------------------------------------------------------ #
    #  Hydraulik-Berechnung (live)                                         #
    # ------------------------------------------------------------------ #

    def _recalc_circuit_hydraulics(self, circuit_id: str):
        """Recalculate and display hydraulic values for one circuit."""
        panel = self.param_panel.circuit_panels.get(circuit_id)
        if not panel:
            return
        params = panel.get_parameters()
        heat = self.param_panel.get_heating_params()
        scale = self.canvas.get_mm_per_px()

        # Fläche
        area_mm2 = self._compute_polygon_area_mm2(circuit_id)
        area_m2 = (area_mm2 or 0.0) / 1_000_000.0

        # Rohrlänge
        route_m = self.canvas.get_manual_route_length_px(circuit_id) * scale / 1000.0
        supply_m = self.canvas.get_supply_line_length_px(circuit_id) * scale / 1000.0
        total_m = route_m + supply_m

        spacing_cm = params["spacing"] / 10.0
        floor_name = params.get("floor_covering", "Fliesen / Keramik")
        r_lambda_b = FLOOR_COVERINGS.get(floor_name, 0.01)
        room_temp = params.get("room_temp", 20.0)
        diameter_mm = params.get("diameter", 16.0)

        hc = calc_circuit(
            t_supply=heat["t_supply"],
            t_return=heat["t_return"],
            t_room=room_temp,
            spacing_cm=spacing_cm,
            r_lambda_b=r_lambda_b,
            area_m2=area_m2,
            pipe_length_m=route_m,
            outer_diameter_mm=diameter_mm,
            total_pipe_length_m=total_m,
        )
        panel.set_hydraulics(
            hc["power_w"], hc["volume_flow_lmin"],
            hc["pressure_drop_mbar"], hc["q_wm2"],
        )

    def _recalc_all_circuits(self):
        """Recalculate hydraulics for every circuit (e.g. after global temp change)."""
        for cid in self.param_panel.circuit_panels:
            self._recalc_circuit_hydraulics(cid)

    def _on_start_point_moved(self, circuit_id: str, pos_px: tuple):
        self.status.showMessage(
            f"📍 Startpunkt {circuit_id} verschoben."
        )

    def _delete_circuit(self, circuit_id: str):
        self.canvas.delete_circuit(circuit_id)
        self.param_panel.remove_circuit_panel(circuit_id)
        self.status.showMessage(f"🗑️ Heizkreis {circuit_id} gelöscht.")

    # ── Elektro ──────────────────────────────────────────────────────── #

    def _add_elec_point(self, fp_id: str = ""):
        self._elec_point_counter += 1
        pid = f"AP-{self._elec_point_counter}"
        panel = self._create_elec_point_panel(pid, fp_id=fp_id or None, name=pid)
        self.status.showMessage(
            f"{pid}: Klicke 'Platzieren' im Panel, dann auf den Plan klicken."
        )

    def _create_elec_point_panel(self, point_id: str,
                                  fp_id: str | None = None,
                                  name: str | None = None):
        panel = self.param_panel.add_elec_point_panel(point_id, fp_id=fp_id, name=name)
        panel.place_requested.connect(self._on_place_elec_point)
        panel.size_changed.connect(self._on_elec_point_size_changed)
        panel.icon_changed.connect(self._on_elec_point_icon_changed)
        panel.name_changed.connect(self._on_elec_point_name_changed)
        panel.color_changed.connect(self._on_elec_point_color_changed)
        panel.visibility_changed.connect(self._on_elec_visibility_changed)
        panel.label_size_changed.connect(self._on_label_size_changed)
        return panel

    def _on_place_elec_point(self, point_id: str):
        params = self.param_panel.get_elec_point_params(point_id)
        if not params:
            return
        self.canvas.set_color(point_id, QColor(params.get("color", "#4fc3f7")))
        self.canvas.start_place_elec_point(
            point_id, params["width"], params["height"])
        self.status.showMessage(
            f"{point_id}: Klicke auf den Plan um den Anschlusspunkt "
            "zu platzieren. ESC = Abbruch"
        )

    def _on_elec_point_placed(self, point_id: str):
        self.status.showMessage(
            f"✅ Anschlusspunkt {point_id} platziert.")

    def _on_elec_point_size_changed(self, point_id: str):
        params = self.param_panel.get_elec_point_params(point_id)
        if params:
            self.canvas.update_elec_point_size(
                point_id, params["width"], params["height"])

    def _on_elec_point_icon_changed(self, point_id: str, path: str):
        self.canvas.set_elec_point_icon(point_id, path)

    def _on_elec_point_color_changed(self, point_id: str, color: str):
        self.canvas.set_color(point_id, QColor(color))

    def _on_elec_point_name_changed(self, point_id: str, name: str):
        self.canvas._label_map[point_id] = name
        self.canvas.update()

    def _on_elec_visibility_changed(self, item_id: str, visible: bool):
        self.canvas._elec_visible[item_id] = visible
        self.canvas.update()

    def _delete_elec_point(self, point_id: str):
        self.canvas.delete_elec_point(point_id)
        self.param_panel.remove_elec_point_panel(point_id)
        self.status.showMessage(f"🗑️ Anschlusspunkt {point_id} gelöscht.")

    def _add_elec_cable(self, fp_id: str = ""):
        self._elec_cable_counter += 1
        cid = f"KV-{self._elec_cable_counter}"
        panel = self._create_elec_cable_panel(cid, fp_id=fp_id or None, name=cid)
        self.status.showMessage(
            f"{cid}: Klicke 'Kabel zeichnen' im Panel."
        )

    def _create_elec_cable_panel(self, cable_id: str,
                                  fp_id: str | None = None,
                                  name: str | None = None):
        panel = self.param_panel.add_elec_cable_panel(cable_id, fp_id=fp_id, name=name)
        panel.draw_cable_requested.connect(self._on_draw_elec_cable)
        panel.edit_cable_requested.connect(self._on_edit_elec_cable)
        panel.name_changed.connect(self._on_elec_cable_name_changed)
        panel.color_changed.connect(self._on_elec_cable_color_changed)
        panel.visibility_changed.connect(self._on_elec_visibility_changed)
        panel.label_size_changed.connect(self._on_label_size_changed)
        return panel

    def _on_draw_elec_cable(self, cable_id: str):
        panel = self.param_panel.elec_cable_panels.get(cable_id)
        if panel:
            self.canvas.set_color(cable_id, QColor(panel._color.name()))
        self.canvas.start_draw_elec_cable(cable_id)
        self.status.showMessage(
            f"{cable_id}: Kabel zeichnen  |  "
            "Linksklick = Punkt  |  Rechtsklick = Fertig  |  ESC = Abbruch"
        )

    def _on_edit_elec_cable(self, cable_id: str):
        self.canvas.start_edit_elec_cable(cable_id)
        self.status.showMessage(
            f"Kabel bearbeiten: Links=Verschieben, Rechts auf Punkt=Löschen, "
            f"Rechts auf Kante=Einfügen, Mitteltaste/ESC=Beenden."
        )

    def _on_elec_cable_changed(self, cable_id: str):
        length_px = self.canvas.get_elec_cable_length_px(cable_id)
        length_mm = length_px * self.canvas.get_mm_per_px()
        self.param_panel.set_cable_length(cable_id, length_mm)
        self._update_cable_ap_labels(cable_id)
        self.status.showMessage(
            f"✅ {cable_id}: Kabel aktualisiert ({length_mm / 1000:.2f} m)")

    def _update_cable_ap_labels(self, cable_id: str):
        """Read AP connections from canvas and display them on the cable panel."""
        panel = self.param_panel.elec_cable_panels.get(cable_id)
        if not panel:
            return
        start_ap_id, end_ap_id = self.canvas.get_cable_ap(cable_id)
        # Resolve AP names
        start_name = ""
        if start_ap_id:
            ap_panel = self.param_panel.elec_point_panels.get(start_ap_id)
            start_name = (ap_panel.get_parameters()["name"]
                          if ap_panel else start_ap_id)
        end_name = ""
        if end_ap_id:
            ap_panel = self.param_panel.elec_point_panels.get(end_ap_id)
            end_name = (ap_panel.get_parameters()["name"]
                        if ap_panel else end_ap_id)
        panel.set_start_ap(start_name)
        panel.set_end_ap(end_name)

    def _on_elec_cable_name_changed(self, cable_id: str, name: str):
        self.canvas._label_map[cable_id] = name
        self.canvas.update()

    def _on_elec_cable_color_changed(self, cable_id: str, color: str):
        self.canvas.set_color(cable_id, QColor(color))

    def _delete_elec_cable(self, cable_id: str):
        self.canvas.delete_elec_cable(cable_id)
        self.param_panel.remove_elec_cable_panel(cable_id)
        self.status.showMessage(f"🗑️ Kabelverbindung {cable_id} gelöscht.")

    def _duplicate_elec_point(self, source_id: str):
        src_panel = self.param_panel.elec_point_panels.get(source_id)
        if not src_panel:
            return
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._elec_point_counter += 1
        new_id = f"AP-{self._elec_point_counter}"
        panel = self._create_elec_point_panel(new_id, fp_id=src_fp_id, name=f"{src.get('name', source_id)} (Kopie)")
        panel.sb_width.setValue(src.get("width", 30.0) / 10)
        panel.sb_height.setValue(src.get("height", 30.0) / 10)
        panel.sb_label_size.setValue(src.get("label_size", 12.0))
        c = src.get("color", "#4fc3f7")
        panel._color = QColor(c)
        panel._update_color_button()
        icon_path = src.get("icon_path", "")
        builtin = src.get("builtin_symbol", "(kein Symbol)")
        if builtin and builtin != "(kein Symbol)":
            idx = panel.cmb_symbol.findText(builtin)
            if idx >= 0:
                panel.cmb_symbol.setCurrentIndex(idx)
        elif icon_path:
            panel._icon_path = icon_path
            panel.btn_icon.setText(icon_path.split("/")[-1].split("\\")[-1])
            self.canvas.set_elec_point_icon(new_id, icon_path)
        self.canvas._ensure_color(new_id)
        self.canvas.set_color(new_id, QColor(c))
        self.canvas.set_label_font_size(new_id, src.get("label_size", 12.0))
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")

    def _duplicate_elec_cable(self, source_id: str):
        src_panel = self.param_panel.elec_cable_panels.get(source_id)
        if not src_panel:
            return
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._elec_cable_counter += 1
        new_id = f"KV-{self._elec_cable_counter}"
        panel = self._create_elec_cable_panel(new_id, fp_id=src_fp_id, name=f"{src.get('name', source_id)} (Kopie)")
        panel.le_type.setText(src.get("type", "5x1,5"))
        panel.te_comment.setPlainText(src.get("comment", ""))
        panel.sb_label_size.setValue(src.get("label_size", 12.0))
        c = src.get("color", "#ff9800")
        panel._color = QColor(c)
        panel._update_color_button()
        self.canvas._ensure_color(new_id)
        self.canvas.set_color(new_id, QColor(c))
        self.canvas.set_label_font_size(new_id, src.get("label_size", 12.0))
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")

    # ── HKV (Heizkreisverteiler) ─────────────────────────────────────── #

    def _add_hkv(self, fp_id: str = ""):
        self._hkv_counter += 1
        hid = f"HKV-{self._hkv_counter}"
        panel = self._create_hkv_panel(hid, fp_id=fp_id or None, name=hid)
        self.param_panel.update_all_hkv_choices()
        self.status.showMessage(
            f"{hid}: Klicke 'Platzieren' im Panel, dann auf den Plan klicken.")

    def _create_hkv_panel(self, hkv_id: str,
                          fp_id: str | None = None,
                          name: str | None = None):
        panel = self.param_panel.add_hkv_panel(hkv_id, fp_id=fp_id, name=name)
        panel.place_requested.connect(self._on_place_hkv)
        panel.size_changed.connect(self._on_hkv_size_changed)
        panel.icon_changed.connect(self._on_hkv_icon_changed)
        panel.name_changed.connect(self._on_hkv_name_changed)
        panel.color_changed.connect(self._on_hkv_color_changed)
        panel.visibility_changed.connect(self._on_hkv_visibility_changed)
        panel.label_size_changed.connect(self._on_label_size_changed)
        return panel

    def _on_place_hkv(self, hkv_id: str):
        params = self.param_panel.get_hkv_params(hkv_id)
        if not params:
            return
        self.canvas.set_color(hkv_id, QColor(params.get("color", "#e53935")))
        self.canvas.start_place_hkv(
            hkv_id, params["width"], params["height"])
        self.status.showMessage(
            f"{hkv_id}: Klicke auf den Plan um den Heizkreisverteiler "
            "zu platzieren. ESC = Abbruch")

    def _on_hkv_placed(self, hkv_id: str):
        self.status.showMessage(
            f"✅ Heizkreisverteiler {hkv_id} platziert.")

    def _on_hkv_size_changed(self, hkv_id: str):
        params = self.param_panel.get_hkv_params(hkv_id)
        if params:
            self.canvas.update_hkv_size(
                hkv_id, params["width"], params["height"])

    def _on_hkv_icon_changed(self, hkv_id: str, path: str):
        self.canvas.set_hkv_icon(hkv_id, path)

    def _on_hkv_name_changed(self, hkv_id: str, name: str):
        self.canvas._label_map[hkv_id] = name
        self.canvas.update()
        self.param_panel.update_all_hkv_choices()

    def _on_hkv_color_changed(self, hkv_id: str, color: str):
        self.canvas.set_color(hkv_id, QColor(color))

    def _on_hkv_visibility_changed(self, hkv_id: str, visible: bool):
        self.canvas._hkv_visible[hkv_id] = visible
        self.canvas.update()

    def _delete_hkv(self, hkv_id: str):
        self.canvas.delete_hkv(hkv_id)
        self.param_panel.remove_hkv_panel(hkv_id)
        self.status.showMessage(f"🗑️ Heizkreisverteiler {hkv_id} gelöscht.")

    # ── HKV-Leitungen ────────────────────────────────────────────────── #

    def _add_hkv_line(self, fp_id: str = ""):
        self._hkv_line_counter += 1
        lid = f"HL-{self._hkv_line_counter}"
        panel = self._create_hkv_line_panel(lid, fp_id=fp_id or None, name=lid)
        self.status.showMessage(
            f"{lid}: Klicke 'Zeichnen' im Panel, dann auf den Plan klicken.")

    def _create_hkv_line_panel(self, line_id: str,
                               fp_id: str | None = None,
                               name: str | None = None):
        panel = self.param_panel.add_hkv_line_panel(line_id, fp_id=fp_id, name=name)
        panel.draw_line_requested.connect(self._on_draw_hkv_line)
        panel.edit_line_requested.connect(self._on_edit_hkv_line)
        panel.name_changed.connect(self._on_hkv_line_name_changed)
        panel.color_changed.connect(self._on_hkv_line_color_changed)
        panel.visibility_changed.connect(self._on_hkv_line_visibility_changed)
        panel.label_size_changed.connect(self._on_label_size_changed)
        return panel

    def _on_draw_hkv_line(self, line_id: str):
        panel = self.param_panel.hkv_line_panels.get(line_id)
        if panel:
            self.canvas.set_color(line_id, QColor(panel._color.name()))
        self.canvas.start_draw_hkv_line(line_id)
        self.status.showMessage(
            f"{line_id}: HKV-Leitung zeichnen  |  "
            "Linksklick = Punkt  |  Rechtsklick = Fertig  |  ESC = Abbruch")

    def _on_edit_hkv_line(self, line_id: str):
        self.canvas.start_edit_hkv_line(line_id)
        self.status.showMessage(
            "HKV-Leitung bearbeiten: Links=Verschieben, "
            "Rechts auf Punkt=Löschen, Rechts auf Kante=Einfügen, "
            "Mitteltaste/ESC=Beenden.")

    def _on_hkv_line_changed(self, line_id: str):
        length_px = self.canvas.get_hkv_line_length_px(line_id)
        length_mm = length_px * self.canvas.get_mm_per_px()
        self.param_panel.set_hkv_line_length(line_id, length_mm)
        self._update_hkv_line_labels(line_id)
        self.status.showMessage(
            f"✅ {line_id}: HKV-Leitung aktualisiert ({length_mm / 1000:.2f} m)")

    def _update_hkv_line_labels(self, line_id: str):
        """Read HKV connections from canvas and display on the line panel."""
        panel = self.param_panel.hkv_line_panels.get(line_id)
        if not panel:
            return
        start_id, end_id = self.canvas.get_hkv_line_ap(line_id)
        start_name = ""
        if start_id:
            hkv_panel = self.param_panel.hkv_panels.get(start_id)
            start_name = (hkv_panel.get_parameters()["name"]
                          if hkv_panel else start_id)
        end_name = ""
        if end_id:
            hkv_panel = self.param_panel.hkv_panels.get(end_id)
            end_name = (hkv_panel.get_parameters()["name"]
                        if hkv_panel else end_id)
        panel.set_start_hkv(start_name)
        panel.set_end_hkv(end_name)

    def _on_hkv_line_name_changed(self, line_id: str, name: str):
        self.canvas._label_map[line_id] = name
        self.canvas.update()

    def _on_hkv_line_color_changed(self, line_id: str, color: str):
        self.canvas.set_color(line_id, QColor(color))

    def _on_hkv_line_visibility_changed(self, line_id: str, visible: bool):
        self.canvas._hkv_line_visible[line_id] = visible
        self.canvas.update()

    def _delete_hkv_line(self, line_id: str):
        self.canvas.delete_hkv_line(line_id)
        self.param_panel.remove_hkv_line_panel(line_id)
        self.status.showMessage(f"🗑️ HKV-Leitung {line_id} gelöscht.")

    def _update_supply_hkv_label(self, circuit_id: str):
        """After supply line changes, show HKV name on the circuit panel."""
        hkv_id = self.canvas.get_supply_hkv(circuit_id)
        panel = self.param_panel.circuit_panels.get(circuit_id)
        if panel and hkv_id:
            hkv_panel = self.param_panel.hkv_panels.get(hkv_id)
            hkv_name = (hkv_panel.get_parameters()["name"]
                        if hkv_panel else hkv_id)
            panel.cb_distributor.setCurrentText(hkv_name)

    # ------------------------------------------------------------------ #
    #  Speichern / Laden                                                   #
    # ------------------------------------------------------------------ #

    def _update_title(self):
        base = f"HRouting v{self._version} – Fußbodenheizung und Kabel Planer"
        dirty = " *" if self._dirty else ""
        if self._project_path:
            self.setWindowTitle(f"{base}  –  {self._project_path.name}{dirty}")
        else:
            self.setWindowTitle(f"{base}{dirty}")

    # -- helpers for relative <-> absolute path conversion -------------- #

    @staticmethod
    def _to_relative(abs_path: str | None, project_dir: Path) -> str:
        """Convert an absolute path to a path relative to *project_dir*.
        Returns empty string when *abs_path* is falsy."""
        if not abs_path:
            return ""
        try:
            return str(Path(abs_path).relative_to(project_dir))
        except ValueError:
            return abs_path          # keep as-is if not under project_dir

    @staticmethod
    def _to_absolute(rel_path: str | None, project_dir: Path) -> str:
        """Resolve a (possibly relative) path against *project_dir*."""
        if not rel_path:
            return ""
        p = Path(rel_path)
        if p.is_absolute():
            return str(p)
        return str((project_dir / p).resolve())

    def _copy_to_images_folder(self, abs_path: str, project_dir: Path) -> str:
        """Copy *abs_path* into <project_dir>/images/ and return the
        relative path 'images/<filename>' (POSIX-style, forward slashes).
        If a file with the same name already exists at the destination but
        has *different* content, a numbered suffix is inserted to avoid
        overwriting it."""
        if not abs_path:
            return ""
        src = Path(abs_path).resolve()
        if not src.exists():
            # File missing – just make it relative if already under project_dir
            try:
                return src.relative_to(project_dir).as_posix()
            except ValueError:
                return abs_path
        images_dir = project_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        dest = images_dir / src.name
        # If source is already in the images dir, nothing to do
        if src == dest.resolve():
            return dest.relative_to(project_dir).as_posix()
        # Avoid overwriting a *different* file that happens to share the name
        if dest.exists() and dest.resolve() != src:
            stem, suffix = src.stem, src.suffix
            counter = 1
            while dest.exists():
                dest = images_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        shutil.copy2(str(src), str(dest))
        return dest.relative_to(project_dir).as_posix()

    # -- new project --------------------------------------------------- #

    def _new_project(self):
        """Reset everything to a blank state."""
        if not self._maybe_save():
            return

        # Reset state
        self._svg_path = None
        self._project_path = None
        self._circuit_counter = 0
        self._elec_point_counter = 0
        self._elec_cable_counter = 0
        self._hkv_counter = 0
        self._hkv_line_counter = 0
        self._floorplan_counter = 0
        self._furniture_counter = 0

        # Recreate canvas and panel
        old_canvas = self.canvas
        old_panel = self.param_panel

        self.canvas = CanvasWidget()
        self.param_panel = ParameterPanel()

        layout = self.centralWidget().layout()
        layout.replaceWidget(old_canvas, self.canvas)
        layout.replaceWidget(old_panel, self.param_panel)
        old_canvas.deleteLater()
        old_panel.deleteLater()

        self._connect_signals()
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_actions()
        self._update_title()
        self.status.showMessage("📄 Neues Projekt erstellt.")

    # -- save ---------------------------------------------------------- #

    def _save_project(self):
        """Save to the current project path, or prompt if none set."""
        if not self._project_path:
            self._save_project_as()
            return
        self._write_project(self._project_path)

    def _save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Projekt speichern unter…", "",
            "HRouting Projekt (*.hrp);;JSON (*.json)"
        )
        if not path:
            return
        self._project_path = Path(path)
        self._write_project(self._project_path)

    def _write_project(self, filepath: Path):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        project_dir = filepath.parent

        from gui.parameter_panel import BUILTIN_SYMBOLS
        _builtin_paths = set(BUILTIN_SYMBOLS.values())

        # ── 1. Alle Bilder in images/ kopieren und Panels aktualisieren ──

        # Grundrisse
        for fid, panel in self.param_panel.floorplan_panels.items():
            abs_fp = panel._file_path or ""
            if abs_fp:
                rel = self._copy_to_images_folder(abs_fp, project_dir)
                new_abs = str((project_dir / rel).resolve())
                panel.set_file_path(new_abs)
                layer = self.canvas._floor_plans.get(fid)
                if layer:
                    layer.file_path = new_abs

        # Einrichtungsgegenstände
        for fur_id, panel in self.param_panel.furniture_panels.items():
            layer = self.canvas._floor_plans.get(fur_id)
            abs_fp = panel._file_path or (layer.file_path if layer else "") or ""
            if abs_fp:
                rel = self._copy_to_images_folder(abs_fp, project_dir)
                new_abs = str((project_dir / rel).resolve())
                panel.set_file_path(new_abs)
                if layer:
                    layer.file_path = new_abs

        # Eigene Icons für Elektro-Anschlusspunkte
        for pid, panel in self.param_panel.elec_point_panels.items():
            abs_icon = panel._icon_path or ""
            if abs_icon and abs_icon not in _builtin_paths:
                rel = self._copy_to_images_folder(abs_icon, project_dir)
                panel._icon_path = str((project_dir / rel).resolve())

        # Eigene Icons für HKV
        for hid, panel in self.param_panel.hkv_panels.items():
            abs_icon = panel._icon_path or ""
            if abs_icon and abs_icon not in _builtin_paths:
                rel = self._copy_to_images_folder(abs_icon, project_dir)
                panel._icon_path = str((project_dir / rel).resolve())

        # Legacy svg_path
        if self._svg_path:
            rel_svg_copy = self._copy_to_images_folder(self._svg_path, project_dir)
            self._svg_path = str((project_dir / rel_svg_copy).resolve())

        # ── 2. JSON bauen – alle Pfade relativ zur Projektdatei ──────────

        params = self.param_panel.to_dict()

        # Grundrisse: absoluten Pfad → relativ
        for fid, fp_data in params.get("floorplans", {}).items():
            abs_fp = fp_data.get("file_path", "")
            if abs_fp:
                try:
                    fp_data["file_path"] = Path(abs_fp).relative_to(project_dir).as_posix()
                except ValueError:
                    fp_data["file_path"] = abs_fp

        # Einrichtungsgegenstände: absoluten Pfad → relativ
        for fur_id, fur_data in params.get("furniture", {}).items():
            abs_fp = fur_data.get("file_path", "")
            if abs_fp:
                try:
                    fur_data["file_path"] = Path(abs_fp).relative_to(project_dir).as_posix()
                except ValueError:
                    fur_data["file_path"] = abs_fp

        # Icons: absoluten Pfad → relativ
        for pid, pdata in params.get("elec_points", {}).items():
            abs_icon = pdata.get("icon_path", "")
            if abs_icon and abs_icon not in _builtin_paths:
                try:
                    pdata["icon_path"] = Path(abs_icon).relative_to(project_dir).as_posix()
                except ValueError:
                    pdata["icon_path"] = abs_icon

        # HKV-Icons: absoluten Pfad → relativ
        for hid, hdata in params.get("hkv_points", {}).items():
            abs_icon = hdata.get("icon_path", "")
            if abs_icon and abs_icon not in _builtin_paths:
                try:
                    hdata["icon_path"] = Path(abs_icon).relative_to(project_dir).as_posix()
                except ValueError:
                    hdata["icon_path"] = abs_icon

        # Legacy svg_path relativ
        rel_svg = ""
        if self._svg_path:
            try:
                rel_svg = Path(self._svg_path).relative_to(project_dir).as_posix()
            except ValueError:
                rel_svg = self._svg_path

        data = {
            "svg_path": rel_svg,
            "canvas":   self.canvas.to_dict(),
            "params":   params,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # Remember as last project
        _SETTINGS.setValue(_LAST_PROJECT_KEY, str(filepath))
        self._add_to_recent(filepath)
        self._dirty = False
        self._update_title()
        self.status.showMessage(f"💾 Gespeichert: {filepath}")

    # -- open ---------------------------------------------------------- #

    def _open_project(self):
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Projekt öffnen…", "",
            "HRouting Projekt (*.hrp);;JSON (*.json);;Alle Dateien (*)"
        )
        if not path:
            return
        self._project_path = Path(path)
        self._load_project(self._project_path)

    def _auto_load_last_project(self):
        last = _SETTINGS.value(_LAST_PROJECT_KEY, "")
        if last and Path(last).exists():
            self._project_path = Path(last)
            self._load_project(self._project_path)

    def _load_project(self, filepath: Path):
        if not filepath.exists():
            return
        try:
            project_dir = filepath.parent
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # --- resolve svg_path relative to project dir ---------------
            svg_rel = data.get("svg_path", "")
            svg_abs = self._to_absolute(svg_rel, project_dir)
            if svg_abs and Path(svg_abs).exists():
                self._svg_path = svg_abs
                self.canvas.load_svg(self._svg_path)
                self._fit_window_to_svg()

            canvas_data = data.get("canvas", {})
            self.canvas.from_dict(canvas_data)

            # --- resolve floorplan file paths + load images -------------
            params = data.get("params", {})
            for fid, fp_data in params.get("floorplans", {}).items():
                rel_fp = fp_data.get("file_path", "")
                if rel_fp:
                    abs_fp = self._to_absolute(rel_fp, project_dir)
                    fp_data["file_path"] = abs_fp or ""
                    if abs_fp and Path(abs_fp).exists():
                        self.canvas.load_floor_plan_image(fid, abs_fp)

            # --- resolve furniture file paths + load images -----------
            for fur_id, fur_data in params.get("furniture", {}).items():
                rel_fp = fur_data.get("file_path", "")
                if rel_fp:
                    abs_fp = self._to_absolute(rel_fp, project_dir)
                    fur_data["file_path"] = abs_fp or ""
                    if abs_fp and Path(abs_fp).exists():
                        self.canvas.load_floor_plan_image(fur_id, abs_fp)

            # --- sync toolbar widgets with restored canvas state -------
            self._sync_toolbar_from_canvas()

            # --- resolve icon paths before handing to param_panel ------
            for pid, pdata in params.get("elec_points", {}).items():
                rel_icon = pdata.get("icon_path", "")
                if rel_icon:
                    pdata["icon_path"] = self._to_absolute(rel_icon, project_dir)

            for hid, hdata in params.get("hkv_points", {}).items():
                rel_icon = hdata.get("icon_path", "")
                if rel_icon:
                    hdata["icon_path"] = self._to_absolute(rel_icon, project_dir)

            self.param_panel.from_dict(params)

            # Floorplan panels: update counter + load images
            for fid, panel in self.param_panel.floorplan_panels.items():
                # If this panel has a file_path but no canvas layer, create one
                if fid not in self.canvas._floor_plans and panel._file_path:
                    self.canvas.add_floor_plan(fid, filepath=panel._file_path)
                layer = self.canvas._floor_plans.get(fid)
                if layer and layer.mm_per_px != 1.0:
                    panel.update_scale_label(layer.mm_per_px)
                try:
                    num = int(fid.split("-")[1])
                    self._floorplan_counter = max(self._floorplan_counter, num)
                except (IndexError, ValueError):
                    pass
                # Set svg_path legacy from first floor with a file
                if not self._svg_path and panel._file_path:
                    self._svg_path = panel._file_path
            # Legacy project: if old svg_path was loaded, assign to grundriss-1
            if self._svg_path and "grundriss-1" in self.param_panel.floorplan_panels:
                fp = self.param_panel.floorplan_panels["grundriss-1"]
                if not fp._file_path:
                    fp.set_file_path(self._svg_path)
                    if "grundriss-1" not in self.canvas._floor_plans:
                        self.canvas.add_floor_plan("grundriss-1",
                                                    filepath=self._svg_path)

            # Furniture panels: update counter + ensure canvas layer
            for fur_id, panel in self.param_panel.furniture_panels.items():
                if fur_id not in self.canvas._floor_plans and panel._file_path:
                    self.canvas.add_floor_plan(fur_id, filepath=panel._file_path)
                layer = self.canvas._floor_plans.get(fur_id)
                if layer and layer.mm_per_px != 1.0:
                    panel.update_scale_label(layer.mm_per_px)
                # Feste Abmessungen aus Panel-Daten auf Canvas-Layer anwenden
                p = panel.get_parameters()
                w_mm = p.get("fixed_width_mm", 0.0)
                h_mm = p.get("fixed_height_mm", 0.0)
                if layer and (w_mm > 0 or h_mm > 0):
                    layer.fixed_width_mm = w_mm
                    layer.fixed_height_mm = h_mm
                try:
                    num = int(fur_id.split("-")[1])
                    self._furniture_counter = max(self._furniture_counter, num)
                except (IndexError, ValueError):
                    pass

            for cid, panel in self.param_panel.circuit_panels.items():
                panel.draw_route_requested.connect(self._start_manual_route)
                panel.edit_polygon_requested.connect(self._on_edit_polygon_requested)
                panel.edit_route_requested.connect(self._on_edit_route_requested)
                panel.draw_supply_requested.connect(self._start_supply_line)
                panel.edit_supply_requested.connect(self._on_edit_supply_requested)
                panel.name_changed.connect(self._on_circuit_name_changed)
                panel.color_changed.connect(self._on_circuit_color_changed)
                panel.spacing_changed.connect(self._on_spacing_changed)
                panel.wall_dist_changed.connect(self._on_wall_dist_changed)
                panel.visibility_changed.connect(self._on_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                panel.hydraulics_param_changed.connect(self._recalc_circuit_hydraulics)
                values = panel.get_parameters()
                self.canvas.set_polygon_name(cid, values["name"])
                self.canvas.set_color(cid, QColor(values["color"]))
                self.canvas._circuit_visible[cid] = values.get("visible", True)
                self.canvas.set_label_font_size(cid, values.get("label_size", 12.0))
                self._update_circuit_area(cid)
                # Update lengths
                route_mm = self.canvas.get_manual_route_length_px(cid) * self.canvas.get_mm_per_px()
                self.param_panel.set_circuit_length(cid, route_mm)
                supply_mm = self.canvas.get_supply_line_length_px(cid) * self.canvas.get_mm_per_px()
                self.param_panel.set_supply_length(cid, supply_mm)
                self.param_panel.set_total_length(cid, route_mm, supply_mm)
                self._recalc_circuit_hydraulics(cid)
                try:
                    num = int(cid.split("-")[1])
                    self._circuit_counter = max(self._circuit_counter, num)
                except (IndexError, ValueError):
                    pass

            # Elektro panels
            for pid, panel in self.param_panel.elec_point_panels.items():
                panel.place_requested.connect(self._on_place_elec_point)
                panel.size_changed.connect(self._on_elec_point_size_changed)
                panel.icon_changed.connect(self._on_elec_point_icon_changed)
                panel.name_changed.connect(self._on_elec_point_name_changed)
                panel.color_changed.connect(self._on_elec_point_color_changed)
                panel.visibility_changed.connect(self._on_elec_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                values = panel.get_parameters()
                self.canvas._label_map[pid] = values.get("name", pid)
                self.canvas._elec_visible[pid] = values.get("visible", True)
                self.canvas.set_label_font_size(pid, values.get("label_size", 12.0))
                self.canvas.set_color(pid, QColor(values.get("color", "#4fc3f7")))
                if values.get("icon_path"):
                    self.canvas.set_elec_point_icon(pid, values["icon_path"])
                try:
                    num = int(pid.split("-")[1])
                    self._elec_point_counter = max(self._elec_point_counter, num)
                except (IndexError, ValueError):
                    pass

            for kid, panel in self.param_panel.elec_cable_panels.items():
                panel.draw_cable_requested.connect(self._on_draw_elec_cable)
                panel.edit_cable_requested.connect(self._on_edit_elec_cable)
                panel.name_changed.connect(self._on_elec_cable_name_changed)
                panel.color_changed.connect(self._on_elec_cable_color_changed)
                panel.visibility_changed.connect(self._on_elec_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                values = panel.get_parameters()
                self.canvas._label_map[kid] = values.get("name", kid)
                self.canvas._elec_visible[kid] = values.get("visible", True)
                self.canvas.set_label_font_size(kid, values.get("label_size", 12.0))
                self.canvas.set_color(kid, QColor(values.get("color", "#ff9800")))
                # Update cable length + AP labels
                length_px = self.canvas.get_elec_cable_length_px(kid)
                length_mm = length_px * self.canvas.get_mm_per_px()
                self.param_panel.set_cable_length(kid, length_mm)
                self._update_cable_ap_labels(kid)
                try:
                    num = int(kid.split("-")[1])
                    self._elec_cable_counter = max(self._elec_cable_counter, num)
                except (IndexError, ValueError):
                    pass

            # HKV panels
            for hid, panel in self.param_panel.hkv_panels.items():
                panel.place_requested.connect(self._on_place_hkv)
                panel.size_changed.connect(self._on_hkv_size_changed)
                panel.icon_changed.connect(self._on_hkv_icon_changed)
                panel.name_changed.connect(self._on_hkv_name_changed)
                panel.color_changed.connect(self._on_hkv_color_changed)
                panel.visibility_changed.connect(self._on_hkv_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                values = panel.get_parameters()
                self.canvas._label_map[hid] = values.get("name", hid)
                self.canvas._hkv_visible[hid] = values.get("visible", True)
                self.canvas.set_label_font_size(hid, values.get("label_size", 12.0))
                self.canvas.set_color(hid, QColor(values.get("color", "#e53935")))
                if values.get("icon_path"):
                    self.canvas.set_hkv_icon(hid, values["icon_path"])
                try:
                    num = int(hid.split("-")[1])
                    self._hkv_counter = max(self._hkv_counter, num)
                except (IndexError, ValueError):
                    pass

            # HKV line panels
            for lid, panel in self.param_panel.hkv_line_panels.items():
                panel.draw_line_requested.connect(self._on_draw_hkv_line)
                panel.edit_line_requested.connect(self._on_edit_hkv_line)
                panel.name_changed.connect(self._on_hkv_line_name_changed)
                panel.color_changed.connect(self._on_hkv_line_color_changed)
                panel.visibility_changed.connect(self._on_hkv_line_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                values = panel.get_parameters()
                self.canvas._label_map[lid] = values.get("name", lid)
                self.canvas._hkv_line_visible[lid] = values.get("visible", True)
                self.canvas.set_label_font_size(lid, values.get("label_size", 12.0))
                self.canvas.set_color(lid, QColor(values.get("color", "#e53935")))
                # Update length + HKV labels
                length_px = self.canvas.get_hkv_line_length_px(lid)
                length_mm = length_px * self.canvas.get_mm_per_px()
                self.param_panel.set_hkv_line_length(lid, length_mm)
                self._update_hkv_line_labels(lid)
                try:
                    num = int(lid.split("-")[1])
                    self._hkv_line_counter = max(self._hkv_line_counter, num)
                except (IndexError, ValueError):
                    pass

            # Update supply line HKV labels
            for cid in self.param_panel.circuit_panels:
                self._update_supply_hkv_label(cid)

            # Remember as last project
            _SETTINGS.setValue(_LAST_PROJECT_KEY, str(filepath))
            self._add_to_recent(filepath)
            self._dirty = False
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._update_undo_actions()
            self._update_title()
            self.status.showMessage(f"📂 Projekt geladen: {filepath.name}")
        except Exception as e:
            self.status.showMessage(f"⚠️ Fehler beim Laden: {e}")

    # ------------------------------------------------------------------ #
    #  Export                                                              #
    # ------------------------------------------------------------------ #

    def _render_plan_to_painter(self, painter: QPainter,
                                target_rect: QRectF,
                                layer: str = "all",
                                floor_plan_id: str | None = None):
        """Render the floor plan with overlays directly onto *painter*.

        *target_rect* – the rectangle within the painter to draw into.
        *layer* – ``"all"`` | ``"heating"`` | ``"elektro"``
        *floor_plan_id* – if given, render only this floor plan as background.
        """
        svg_w, svg_h = self.canvas._svg_size
        if svg_w <= 0 or svg_h <= 0:
            return

        # Scale SVG to fill the target rect as much as possible while
        # preserving aspect ratio (fit to best dimension).
        sx = target_rect.width() / svg_w
        sy = target_rect.height() / svg_h
        scale = min(sx, sy)
        scaled_w = svg_w * scale
        scaled_h = svg_h * scale
        ox = target_rect.x() + (target_rect.width() - scaled_w) / 2
        oy = target_rect.y() + (target_rect.height() - scaled_h) / 2

        painter.save()
        painter.translate(ox, oy)
        painter.scale(scale, scale)

        # Helper: create a font that looks correct in the scaled SVG
        # coordinate system.  We use setPixelSize so the size is in SVG
        # pixels, not device points.
        def _svg_font(pixel_size: float) -> QFont:
            f = QFont("Arial")
            f.setPixelSize(max(4, int(pixel_size)))
            return f

        # Background: floor plan layers
        ref_mpp = self.canvas._mm_per_px if self.canvas._mm_per_px > 0 else 1.0
        rendered_floor = False
        fp_ids = [floor_plan_id] if floor_plan_id else self.canvas._floor_plan_order
        for fid in fp_ids:
            fp_layer = self.canvas._floor_plans.get(fid)
            if not fp_layer or not fp_layer.visible:
                continue
            rendered_floor = True
            painter.save()
            w, h = fp_layer.size
            ls = fp_layer.mm_per_px / ref_mpp if fp_layer.mm_per_px > 0 else 1.0
            sw, sh = w * ls, h * ls
            cx_fp = sw / 2 + fp_layer.offset_x
            cy_fp = sh / 2 + fp_layer.offset_y
            painter.translate(cx_fp, cy_fp)
            painter.rotate(fp_layer.rotation)
            painter.translate(-sw / 2, -sh / 2)
            painter.setOpacity(fp_layer.opacity)
            if fp_layer.renderer:
                fp_layer.renderer.render(painter, QRectF(0, 0, sw, sh))
            elif fp_layer.pixmap:
                painter.drawPixmap(QRectF(0, 0, sw, sh), fp_layer.pixmap,
                                   QRectF(fp_layer.pixmap.rect()))
            painter.restore()

        # Legacy single background (only if no floor plan layers rendered)
        if not rendered_floor:
            if self.canvas._svg_renderer and self.canvas._svg_renderer.isValid():
                self.canvas._svg_renderer.render(
                    painter, QRectF(0, 0, svg_w, svg_h)
                )
            elif self.canvas._bg_pixmap:
                painter.drawPixmap(QRectF(0, 0, svg_w, svg_h),
                                   self.canvas._bg_pixmap,
                                   QRectF(self.canvas._bg_pixmap.rect()))

        show_heating = layer in ("all", "heating")
        show_elektro = layer in ("all", "elektro")

        # ── Heating elements ──────────────────────────────────────
        if show_heating:
            # Polygons
            for cid, pts in self.canvas._polygons.items():
                if not self.canvas._circuit_visible.get(cid, True):
                    continue
                if len(pts) < 3:
                    continue
                color = self.canvas._color_map.get(cid, QColor("#ff0000"))
                fill = QColor(color)
                fill.setAlpha(35)
                painter.setBrush(QBrush(fill))
                painter.setPen(QPen(color, 2.0))
                poly = QPolygonF(pts)
                painter.drawPolygon(poly)

            # Manual routes
            for cid, pts in self.canvas._manual_routes.items():
                if not self.canvas._circuit_visible.get(cid, True):
                    continue
                if len(pts) < 2:
                    continue
                color = self.canvas._color_map.get(cid, QColor("#ff0000"))
                line_dist = self.canvas._route_line_dist_px.get(cid, 0.0)
                offset = line_dist / 2.0
                line1 = self.canvas._offset_route_points(pts, offset)
                line2 = self.canvas._offset_route_points(pts, -offset)
                combined = list(line1) + list(reversed(line2))
                if len(combined) < 2:
                    continue
                qpath = self.canvas._smooth_polyline_path(combined, offset)
                painter.setPen(QPen(color, 2.0, Qt.SolidLine,
                                    Qt.RoundCap, Qt.RoundJoin))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(qpath)

            # Supply lines
            for cid, pts in self.canvas._supply_lines.items():
                if not self.canvas._circuit_visible.get(cid, True):
                    continue
                if len(pts) < 2:
                    continue
                color = self.canvas._color_map.get(cid, QColor("#ff0000"))
                line_dist = self.canvas._route_line_dist_px.get(cid, 0.0)
                offset = line_dist / 2.0
                line1 = self.canvas._offset_route_points(pts, offset)
                line2 = self.canvas._offset_route_points(pts, -offset)
                combined = list(line1) + list(reversed(line2))
                if len(combined) < 2:
                    continue
                qpath = self.canvas._smooth_polyline_path(combined, offset)
                pen = QPen(color, 2.0, Qt.DashDotLine,
                           Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(qpath)

            # Labels for circuits
            for cid in self.canvas._polygons:
                if not self.canvas._circuit_visible.get(cid, True):
                    continue
                label = self.canvas._label_map.get(cid, cid)
                pts = self.canvas._polygons.get(cid, [])
                if len(pts) < 3:
                    continue
                cx = sum(p.x() for p in pts) / len(pts)
                cy = sum(p.y() for p in pts) / len(pts)
                color = self.canvas._color_map.get(cid, QColor("#ffffff"))
                font_size = self.canvas._label_font_sizes.get(cid, 12.0)
                painter.setFont(_svg_font(font_size))
                painter.setPen(QPen(color))
                painter.drawText(QPointF(cx, cy), label)

            # Heizkreisverteiler
            for hid, pos in self.canvas._hkv_points.items():
                if not self.canvas._hkv_visible.get(hid, True):
                    continue
                w, h = self.canvas._hkv_size_px.get(hid, (30, 30))
                x = pos.x() - w / 2
                y = pos.y() - h / 2
                color = self.canvas._color_map.get(hid, QColor("#e53935"))
                fill = QColor(color)
                fill.setAlpha(60)
                svg_r = self.canvas._hkv_svgs.get(hid)
                icon_pm = self.canvas._hkv_icons.get(hid)
                if svg_r and svg_r.isValid():
                    svg_r.render(painter, QRectF(x, y, w, h))
                elif icon_pm and not icon_pm.isNull():
                    painter.drawPixmap(QRectF(x, y, w, h),
                                       icon_pm,
                                       QRectF(icon_pm.rect()))
                else:
                    painter.setBrush(QBrush(fill))
                    painter.setPen(QPen(color, 2.0))
                    painter.drawRoundedRect(QRectF(x, y, w, h), 4.0, 4.0)
                label = self.canvas._label_map.get(hid, hid)
                font_size = self.canvas._label_font_sizes.get(hid, 10.0)
                painter.setFont(_svg_font(font_size))
                painter.setPen(QPen(color))
                painter.drawText(
                    QPointF(pos.x() - w / 4,
                            pos.y() + h / 2 + font_size + 2),
                    label)

            # HKV Verbindungsleitungen
            for lid, pts in self.canvas._hkv_lines.items():
                if not self.canvas._hkv_line_visible.get(lid, True):
                    continue
                if len(pts) < 2:
                    continue
                color = self.canvas._color_map.get(lid, QColor("#e53935"))
                offset = 3.0
                line1 = self.canvas._offset_route_points(pts, offset)
                line2 = self.canvas._offset_route_points(pts, -offset)
                pen = QPen(color, 2.0, Qt.SolidLine,
                           Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                for line in (line1, line2):
                    if len(line) > 1:
                        path = QPainterPath()
                        path.moveTo(line[0])
                        for p in line[1:]:
                            path.lineTo(p)
                        painter.drawPath(path)
                if line1 and line2:
                    painter.drawLine(line1[-1], line2[-1])
                    painter.drawLine(line1[0], line2[0])
                label = self.canvas._label_map.get(lid, lid)
                if len(pts) >= 2:
                    mi = len(pts) // 2
                    mid = pts[mi]
                    font_size = self.canvas._label_font_sizes.get(lid, 10.0)
                    painter.setFont(_svg_font(font_size))
                    painter.setPen(QPen(color))
                    painter.drawText(QPointF(mid.x() + 4, mid.y() - 4), label)

        # ── Elektro elements ──────────────────────────────────────
        if show_elektro:
            # Anschlusspunkte
            for pid, pos in self.canvas._elec_points.items():
                if not self.canvas._elec_visible.get(pid, True):
                    continue
                ew, eh = self.canvas._elec_point_size_px.get(pid, (30, 30))
                x = pos.x() - ew / 2
                y = pos.y() - eh / 2
                color = self.canvas._color_map.get(pid, QColor("#4fc3f7"))
                fill = QColor(color)
                fill.setAlpha(60)

                # Try SVG renderer first, then pixmap, then plain rect
                svg_r = self.canvas._elec_point_svgs.get(pid)
                icon_pm = self.canvas._elec_point_icons.get(pid)
                if svg_r and svg_r.isValid():
                    svg_r.render(painter, QRectF(x, y, ew, eh))
                elif icon_pm and not icon_pm.isNull():
                    painter.drawPixmap(QRectF(x, y, ew, eh),
                                       icon_pm,
                                       QRectF(icon_pm.rect()))
                else:
                    painter.setBrush(QBrush(fill))
                    painter.setPen(QPen(color, 2.0))
                    painter.drawRect(QRectF(x, y, ew, eh))

                label = self.canvas._label_map.get(pid, pid)
                font_size = self.canvas._label_font_sizes.get(pid, 10.0)
                painter.setFont(_svg_font(font_size))
                painter.setPen(QPen(color))
                painter.drawText(
                    QPointF(pos.x() - ew / 4,
                            pos.y() + eh / 2 + font_size + 2),
                    label,
                )

            # Kabelverbindungen
            for kid, pts in self.canvas._elec_cables.items():
                if not self.canvas._elec_visible.get(kid, True):
                    continue
                if len(pts) < 2:
                    continue
                color = self.canvas._color_map.get(kid, QColor("#ff9800"))
                rounding = 8.0
                qpath = self.canvas._smooth_polyline_path(pts, rounding)
                painter.setPen(QPen(color, 2.0, Qt.SolidLine,
                                    Qt.RoundCap, Qt.RoundJoin))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(qpath)

                label = self.canvas._label_map.get(kid, kid)
                if len(pts) >= 2:
                    mid_idx = len(pts) // 2
                    mid = pts[mid_idx]
                    font_size = self.canvas._label_font_sizes.get(
                        kid, 10.0)
                    painter.setFont(_svg_font(font_size))
                    painter.setPen(QPen(color))
                    painter.drawText(
                        QPointF(mid.x() + 4, mid.y() - 4), label
                    )

        painter.restore()

    @staticmethod
    def _qpainterpath_to_svg_d(qpath) -> str:
        """Convert a QPainterPath to an SVG path 'd' attribute string."""
        parts: list[str] = []
        for i in range(qpath.elementCount()):
            el = qpath.elementAt(i)
            t = el.type
            if t == QPainterPath.ElementType.MoveToElement:
                parts.append(f"M {el.x:.2f},{el.y:.2f}")
            elif t == QPainterPath.ElementType.LineToElement:
                parts.append(f"L {el.x:.2f},{el.y:.2f}")
            elif t == QPainterPath.ElementType.CurveToElement:
                c1x, c1y = el.x, el.y
                el2 = qpath.elementAt(i + 1)
                el3 = qpath.elementAt(i + 2)
                parts.append(
                    f"C {c1x:.2f},{c1y:.2f} "
                    f"{el2.x:.2f},{el2.y:.2f} "
                    f"{el3.x:.2f},{el3.y:.2f}"
                )
            elif t == QPainterPath.ElementType.CurveToDataElement:
                pass  # handled above as part of CurveTo
        return " ".join(parts)

    def _generate_plan_svg_elements(self) -> list[str]:
        """Generate SVG path elements for circuits, routes, supply lines, and elektro."""
        lines: list[str] = []
        _exported_polys: set[str] = set()
        _exported_routes: set[str] = set()

        for cid in list(self.canvas._polygons.keys()) + list(self.canvas._manual_routes.keys()):
            if not self.canvas._circuit_visible.get(cid, True):
                continue
            color = self.canvas._color_map.get(cid)
            color_str = color.name() if color else "#ff0000"

            poly_pts = self.canvas._polygons.get(cid, [])
            if len(poly_pts) >= 3 and cid not in _exported_polys:
                _exported_polys.add(cid)
                poly_d = "M " + " L ".join(
                    f"{p.x():.2f},{p.y():.2f}" for p in poly_pts
                ) + " Z"
                lines.append(
                    f'  <path d="{poly_d}" fill="{color_str}" '
                    f'fill-opacity="0.14" stroke="{color_str}" stroke-width="2"/>'
                )

            pts = self.canvas._manual_routes.get(cid, [])
            if len(pts) < 2 or cid in _exported_routes:
                continue
            _exported_routes.add(cid)
            line_dist = self.canvas._route_line_dist_px.get(cid, 0.0)
            offset = line_dist / 2.0
            line1 = self.canvas._offset_route_points(pts, offset)
            line2 = self.canvas._offset_route_points(pts, -offset)
            combined = list(line1) + list(reversed(line2))
            if len(combined) < 2:
                continue
            qpath = self.canvas._smooth_polyline_path(combined, offset)
            svg_d = self._qpainterpath_to_svg_d(qpath)
            if svg_d:
                lines.append(
                    f'  <path d="{svg_d}" fill="none" '
                    f'stroke="{color_str}" stroke-width="2" '
                    f'stroke-linejoin="round" stroke-linecap="round"/>'
                )

        # Anschlussleitungen
        for cid, pts in self.canvas._supply_lines.items():
            if not self.canvas._circuit_visible.get(cid, True):
                continue
            if len(pts) < 2:
                continue
            color = self.canvas._color_map.get(cid)
            color_str = color.name() if color else "#ff0000"
            line_dist = self.canvas._route_line_dist_px.get(cid, 0.0)
            offset = line_dist / 2.0
            line1 = self.canvas._offset_route_points(pts, offset)
            line2 = self.canvas._offset_route_points(pts, -offset)
            combined = list(line1) + list(reversed(line2))
            if len(combined) < 2:
                continue
            qpath = self.canvas._smooth_polyline_path(combined, offset)
            svg_d = self._qpainterpath_to_svg_d(qpath)
            if svg_d:
                lines.append(
                    f'  <path d="{svg_d}" fill="none" '
                    f'stroke="{color_str}" stroke-width="2" '
                    f'stroke-dasharray="8,4,2,4" '
                    f'stroke-linejoin="round" stroke-linecap="round"/>'
                )

        # Elektro: Anschlusspunkte
        for pid, pos in self.canvas._elec_points.items():
            if not self.canvas._elec_visible.get(pid, True):
                continue
            ew, eh = self.canvas._elec_point_size_px.get(pid, (30, 30))
            x = pos.x() - ew / 2
            y = pos.y() - eh / 2
            lines.append(
                f'  <rect x="{x:.2f}" y="{y:.2f}" '
                f'width="{ew:.2f}" height="{eh:.2f}" '
                f'fill="#4fc3f7" fill-opacity="0.25" '
                f'stroke="#4fc3f7" stroke-width="2"/>'
            )
            label = self.canvas._label_map.get(pid, pid)
            lines.append(
                f'  <text x="{pos.x():.2f}" '
                f'y="{pos.y() + eh / 2 + 12:.2f}" '
                f'fill="#4fc3f7" font-size="10" '
                f'text-anchor="middle">{label}</text>'
            )

        # Elektro: Kabelverbindungen
        for kid, pts in self.canvas._elec_cables.items():
            if not self.canvas._elec_visible.get(kid, True):
                continue
            if len(pts) < 2:
                continue
            color = self.canvas._color_map.get(kid)
            color_str = color.name() if color else "#ff9800"
            rounding = 8.0
            qpath = self.canvas._smooth_polyline_path(pts, rounding)
            svg_d = self._qpainterpath_to_svg_d(qpath)
            if svg_d:
                lines.append(
                    f'  <path d="{svg_d}" fill="none" '
                    f'stroke="{color_str}" stroke-width="2" '
                    f'stroke-linejoin="round" stroke-linecap="round"/>'
                )

        return lines

    def _write_plan_svg(self, path: str):
        """Write the complete plan (background + circuits + elektro) as SVG."""
        w, h = self.canvas._svg_size
        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        ]
        if self._svg_path:
            ext = Path(self._svg_path).suffix.lower()
            if ext == ".svg":
                href = Path(self._svg_path).as_uri()
            else:
                import base64
                mime = {"png": "image/png", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg", "bmp": "image/bmp"}
                mt = mime.get(ext.lstrip("."), "image/png")
                raw = Path(self._svg_path).read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                href = f"data:{mt};base64,{b64}"
            lines.append(
                f'  <image href="{href}" '
                f'x="0" y="0" width="{w}" height="{h}"/>'
            )
        lines.extend(self._generate_plan_svg_elements())
        lines.append("</svg>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _export_svg(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Als SVG exportieren", "heizplan.svg", "SVG (*.svg)"
        )
        if not path:
            return
        self._write_plan_svg(path)
        self.status.showMessage(f"\ud83d\udce4 SVG exportiert: {path}")

    # ------------------------------------------------------------------ #
    #  Längen-Export                                                       #
    # ------------------------------------------------------------------ #

    def _export_lengths(self):
        """Show a dialog with length tables, hydraulic overview and optional CSV export."""
        scale = self.canvas.get_mm_per_px()
        heat_params = self.param_panel.get_heating_params()
        t_supply = heat_params["t_supply"]
        t_return = heat_params["t_return"]

        # ── Heizkreise sammeln ──
        hk_rows: list[dict] = []
        for cid, panel in self.param_panel.circuit_panels.items():
            params = panel.get_parameters()
            route_px = self.canvas.get_manual_route_length_px(cid)
            route_m = route_px * scale / 1000.0
            supply_px = self.canvas.get_supply_line_length_px(cid)
            supply_m = supply_px * scale / 1000.0
            total_m = route_m + supply_m

            # Fläche berechnen
            area_mm2 = self._compute_polygon_area_mm2(cid)
            area_m2 = (area_mm2 or 0.0) / 1_000_000.0

            # Heizungstechnische Berechnung
            spacing_cm = params["spacing"] / 10.0  # mm → cm
            floor_name = params.get("floor_covering", "Fliesen / Keramik")
            r_lambda_b = FLOOR_COVERINGS.get(floor_name, 0.01)
            room_temp = params.get("room_temp", 20.0)
            diameter_mm = params.get("diameter", 16.0)

            hc = calc_circuit(
                t_supply=t_supply,
                t_return=t_return,
                t_room=room_temp,
                spacing_cm=spacing_cm,
                r_lambda_b=r_lambda_b,
                area_m2=area_m2,
                pipe_length_m=route_m,
                outer_diameter_mm=diameter_mm,
                total_pipe_length_m=total_m,
            )

            hk_rows.append({
                "name": params["name"],
                "diameter_mm": diameter_mm,
                "spacing_mm": params["spacing"],
                "route_m": route_m,
                "supply_m": supply_m,
                "total_m": total_m,
                "area_m2": area_m2,
                "room_temp": room_temp,
                "floor_covering": floor_name,
                "distributor": params.get("distributor", ""),
                "power_w": hc["power_w"],
                "q_wm2": hc["q_wm2"],
                "volume_flow_lmin": hc["volume_flow_lmin"],
                "pressure_drop_mbar": hc["pressure_drop_mbar"],
            })

        # Summe pro Rohrdurchmesser (Gesamtlänge inkl. Zuleitung)
        hk_sum: dict[float, float] = defaultdict(float)
        for r in hk_rows:
            hk_sum[r["diameter_mm"]] += r["total_m"]

        # Summe pro Heizkreisverteiler
        hkv_sum: dict[str, dict] = defaultdict(lambda: {"volume_flow": 0.0, "power": 0.0})
        for r in hk_rows:
            dist = r["distributor"]
            if dist:
                hkv_sum[dist]["volume_flow"] += r["volume_flow_lmin"]
                hkv_sum[dist]["power"] += r["power_w"]

        # Hydraulischer Abgleich
        hk_rows = calc_balancing(hk_rows)

        # ── Elektro-Kabel sammeln ──
        kv_rows: list[dict] = []
        for kid, panel in self.param_panel.elec_cable_panels.items():
            params = panel.get_parameters()
            length_px = self.canvas.get_elec_cable_length_px(kid)
            length_m = length_px * scale / 1000.0
            start_ap_id, end_ap_id = self.canvas.get_cable_ap(kid)
            start_name = ""
            if start_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(start_ap_id)
                start_name = (ap_p.get_parameters()["name"]
                              if ap_p else start_ap_id)
            end_name = ""
            if end_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(end_ap_id)
                end_name = (ap_p.get_parameters()["name"]
                            if ap_p else end_ap_id)
            kv_rows.append({
                "name": params["name"],
                "type": params["type"],
                "length_m": length_m,
                "start_ap": start_name,
                "end_ap": end_name,
            })

        # Summe pro Kabel-Typ
        kv_sum: dict[str, float] = defaultdict(float)
        for r in kv_rows:
            kv_sum[r["type"]] += r["length_m"]

        # AP → Kabel Zuordnung
        ap_cables = self._build_ap_cable_map(kv_rows)

        # ── HKV-Leitungen sammeln ──
        hl_rows: list[dict] = []
        for lid, panel in self.param_panel.hkv_line_panels.items():
            params = panel.get_parameters()
            length_px = self.canvas.get_hkv_line_length_px(lid)
            length_m = length_px * scale / 1000.0
            start_hkv_id, end_hkv_id = self.canvas.get_hkv_line_ap(lid)
            start_name = ""
            if start_hkv_id:
                hp = self.param_panel.hkv_panels.get(start_hkv_id)
                start_name = (hp.get_parameters()["name"]
                              if hp else start_hkv_id)
            end_name = ""
            if end_hkv_id:
                hp = self.param_panel.hkv_panels.get(end_hkv_id)
                end_name = (hp.get_parameters()["name"]
                            if hp else end_hkv_id)
            hl_rows.append({
                "name": params["name"],
                "type": params["type"],
                "length_m": length_m,
                "start_hkv": start_name,
                "end_hkv": end_name,
            })

        hl_sum: dict[str, float] = defaultdict(float)
        for r in hl_rows:
            hl_sum[r["type"]] += r["length_m"]

        # ── Dialog aufbauen ──
        dlg = QDialog(self)
        dlg.setWindowTitle("📊 Längenübersicht")
        dlg.resize(900, 620)
        dlg_layout = QVBoxLayout(dlg)

        tabs = QTabWidget()
        dlg_layout.addWidget(tabs)

        # -- Tab 1: Heizkreise Längen --
        hk_widget = QWidget()
        hk_layout = QVBoxLayout(hk_widget)

        hk_layout.addWidget(QLabel("<b>Heizkreise – Einzellängen</b>"))
        tbl_hk = QTableWidget(len(hk_rows), 6)
        tbl_hk.setHorizontalHeaderLabels(
            ["Name", "Durchm. (mm)", "Abstand (mm)",
             "Rohr (m)", "Zuleitung (m)", "Gesamt (m)"])
        tbl_hk.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_hk.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, r in enumerate(hk_rows):
            tbl_hk.setItem(i, 0, QTableWidgetItem(r["name"]))
            tbl_hk.setItem(i, 1, QTableWidgetItem(f"{r['diameter_mm']:.1f}"))
            tbl_hk.setItem(i, 2, QTableWidgetItem(f"{r['spacing_mm']:.1f}"))
            for col, key in [(3, "route_m"), (4, "supply_m"), (5, "total_m")]:
                item = QTableWidgetItem(f"{r[key]:.2f}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl_hk.setItem(i, col, item)
        hk_layout.addWidget(tbl_hk)

        hk_layout.addWidget(QLabel("<b>Summe pro Rohrdurchmesser</b>"))
        sorted_diams = sorted(hk_sum.keys())
        tbl_hk_sum = QTableWidget(len(sorted_diams), 2)
        tbl_hk_sum.setHorizontalHeaderLabels(
            ["Rohrdurchmesser (mm)", "Gesamtlänge (m)"])
        tbl_hk_sum.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_hk_sum.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, d in enumerate(sorted_diams):
            tbl_hk_sum.setItem(i, 0, QTableWidgetItem(f"{d:.1f}"))
            item = QTableWidgetItem(f"{hk_sum[d]:.2f}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tbl_hk_sum.setItem(i, 1, item)
        hk_layout.addWidget(tbl_hk_sum)

        # HKV-Leitungen (im Heizungsteil)
        if hl_rows:
            hk_layout.addWidget(QLabel(
                "<b>HKV-Leitungen – Einzellängen</b>"))
            tbl_hl = QTableWidget(len(hl_rows), 5)
            tbl_hl.setHorizontalHeaderLabels(
                ["Name", "Typ", "Start-HKV", "End-HKV", "Länge (m)"])
            tbl_hl.horizontalHeader().setSectionResizeMode(
                QHeaderView.Stretch)
            tbl_hl.setEditTriggers(QTableWidget.NoEditTriggers)
            for i, r in enumerate(hl_rows):
                tbl_hl.setItem(i, 0, QTableWidgetItem(r["name"]))
                tbl_hl.setItem(i, 1, QTableWidgetItem(r["type"]))
                tbl_hl.setItem(i, 2, QTableWidgetItem(
                    r.get("start_hkv", "")))
                tbl_hl.setItem(i, 3, QTableWidgetItem(
                    r.get("end_hkv", "")))
                item = QTableWidgetItem(f"{r['length_m']:.2f}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl_hl.setItem(i, 4, item)
            hk_layout.addWidget(tbl_hl)

            if hl_sum:
                hk_layout.addWidget(QLabel(
                    "<b>HKV-Leitungen – Summe pro Typ</b>"))
                sorted_hl_types = sorted(hl_sum.keys())
                tbl_hl_sum = QTableWidget(len(sorted_hl_types), 2)
                tbl_hl_sum.setHorizontalHeaderLabels(
                    ["Leitungstyp", "Gesamtlänge (m)"])
                tbl_hl_sum.horizontalHeader().setSectionResizeMode(
                    QHeaderView.Stretch)
                tbl_hl_sum.setEditTriggers(QTableWidget.NoEditTriggers)
                for i, t in enumerate(sorted_hl_types):
                    tbl_hl_sum.setItem(i, 0, QTableWidgetItem(t))
                    item = QTableWidgetItem(f"{hl_sum[t]:.2f}")
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    tbl_hl_sum.setItem(i, 1, item)
                hk_layout.addWidget(tbl_hl_sum)

        tabs.addTab(hk_widget, "🔥 Heizkreise – Längen")

        # -- Tab 2: Heizkreise Hydraulik --
        hy_widget = QWidget()
        hy_layout = QVBoxLayout(hy_widget)

        hy_layout.addWidget(QLabel(
            f"<b>Hydraulische Übersicht &amp; Abgleich</b>  "
            f"(Vorlauf {t_supply:.1f} °C / Rücklauf {t_return:.1f} °C)"
        ))
        tbl_hy = QTableWidget(len(hk_rows), 13)
        tbl_hy.setHorizontalHeaderLabels([
            "Name", "HKV", "Raumtemp.\n(°C)", "Belag",
            "Fläche\n(m²)", "q\n(W/m²)",
            "Leistung\n(W)", "Volumen-\nstrom\n(l/min)", "Δp Rohr\n(mbar)",
            "Δp max\n(mbar)", "Δp Ventil\n(mbar)", "Kv\n(m³/h)",
            "Soll-V̇\n(l/min)",
        ])
        tbl_hy.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_hy.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, r in enumerate(hk_rows):
            tbl_hy.setItem(i, 0, QTableWidgetItem(r["name"]))
            tbl_hy.setItem(i, 1, QTableWidgetItem(r["distributor"]))
            item = QTableWidgetItem(f"{r['room_temp']:.1f}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tbl_hy.setItem(i, 2, item)
            tbl_hy.setItem(i, 3, QTableWidgetItem(r["floor_covering"]))
            for col, key, fmt in [
                (4, "area_m2", ".2f"),
                (5, "q_wm2", ".1f"),
                (6, "power_w", ".0f"),
                (7, "volume_flow_lmin", ".2f"),
                (8, "pressure_drop_mbar", ".1f"),
                (9, "dp_max_mbar", ".1f"),
                (10, "dp_valve_mbar", ".1f"),
                (11, "kv_value", ".3f"),
                (12, "volume_flow_lmin", ".2f"),
            ]:
                item = QTableWidgetItem(f"{r[key]:{fmt}}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl_hy.setItem(i, col, item)
        hy_layout.addWidget(tbl_hy)

        # Summe pro Heizkreisverteiler
        if hkv_sum:
            hy_layout.addWidget(QLabel("<b>Summe pro Heizkreisverteiler</b>"))
            sorted_hkv = sorted(hkv_sum.keys())
            tbl_hkv = QTableWidget(len(sorted_hkv), 3)
            tbl_hkv.setHorizontalHeaderLabels([
                "Heizkreisverteiler", "Volumenstrom (l/min)", "Leistung (W)",
            ])
            tbl_hkv.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tbl_hkv.setEditTriggers(QTableWidget.NoEditTriggers)
            for i, name in enumerate(sorted_hkv):
                tbl_hkv.setItem(i, 0, QTableWidgetItem(name))
                item = QTableWidgetItem(f"{hkv_sum[name]['volume_flow']:.2f}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl_hkv.setItem(i, 1, item)
                item = QTableWidgetItem(f"{hkv_sum[name]['power']:.0f}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl_hkv.setItem(i, 2, item)
            hy_layout.addWidget(tbl_hkv)

        tabs.addTab(hy_widget, "🌡 Heizkreise – Hydraulik")

        # -- Tab 2: Elektro --
        kv_widget = QWidget()
        kv_layout = QVBoxLayout(kv_widget)

        kv_layout.addWidget(QLabel("<b>Kabelverbindungen – Einzellängen</b>"))
        tbl_kv = QTableWidget(len(kv_rows), 5)
        tbl_kv.setHorizontalHeaderLabels(
            ["Name", "Typ", "Start-AP", "End-AP", "Länge (m)"])
        tbl_kv.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_kv.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, r in enumerate(kv_rows):
            tbl_kv.setItem(i, 0, QTableWidgetItem(r["name"]))
            tbl_kv.setItem(i, 1, QTableWidgetItem(r["type"]))
            tbl_kv.setItem(i, 2, QTableWidgetItem(r.get("start_ap", "")))
            tbl_kv.setItem(i, 3, QTableWidgetItem(r.get("end_ap", "")))
            item = QTableWidgetItem(f"{r['length_m']:.2f}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tbl_kv.setItem(i, 4, item)
        kv_layout.addWidget(tbl_kv)

        kv_layout.addWidget(QLabel("<b>Summe pro Leitungstyp</b>"))
        sorted_types = sorted(kv_sum.keys())
        tbl_kv_sum = QTableWidget(len(sorted_types), 2)
        tbl_kv_sum.setHorizontalHeaderLabels(
            ["Leitungstyp", "Gesamtlänge (m)"])
        tbl_kv_sum.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_kv_sum.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, t in enumerate(sorted_types):
            tbl_kv_sum.setItem(i, 0, QTableWidgetItem(t))
            item = QTableWidgetItem(f"{kv_sum[t]:.2f}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tbl_kv_sum.setItem(i, 1, item)
        kv_layout.addWidget(tbl_kv_sum)

        # AP-Anschlüsse
        if ap_cables:
            kv_layout.addWidget(QLabel(
                "<b>Anschlusspunkte – Kabelverbindungen</b>"))
            for ap_name in sorted(ap_cables.keys()):
                cables = ap_cables[ap_name]
                kv_layout.addWidget(QLabel(f"<i>{ap_name}</i>"))
                tbl_ap = QTableWidget(len(cables), 4)
                tbl_ap.setHorizontalHeaderLabels(
                    ["Kabel", "Typ", "Anschluss", "Länge (m)"])
                tbl_ap.horizontalHeader().setSectionResizeMode(
                    QHeaderView.Stretch)
                tbl_ap.setEditTriggers(QTableWidget.NoEditTriggers)
                tbl_ap.setMaximumHeight(30 + len(cables) * 30)
                for i, c in enumerate(cables):
                    tbl_ap.setItem(i, 0, QTableWidgetItem(c["cable"]))
                    tbl_ap.setItem(i, 1, QTableWidgetItem(c["type"]))
                    tbl_ap.setItem(i, 2, QTableWidgetItem(c["role"]))
                    item = QTableWidgetItem(f"{c['length_m']:.2f}")
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    tbl_ap.setItem(i, 3, item)
                kv_layout.addWidget(tbl_ap)

        tabs.addTab(kv_widget, "🔌 Elektro")

        # -- Buttons --
        btn_box = QDialogButtonBox()
        btn_csv = QPushButton("💾 Als CSV exportieren")
        btn_csv.clicked.connect(
            lambda: self._save_lengths_csv(hk_rows, hk_sum, kv_rows, kv_sum,
                                           hkv_sum, ap_cables, hl_rows, hl_sum)
        )
        btn_box.addButton(btn_csv, QDialogButtonBox.ActionRole)
        btn_box.addButton(QDialogButtonBox.Close)
        btn_box.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn_box)

        dlg.exec()

    def _save_lengths_csv(self, hk_rows, hk_sum, kv_rows, kv_sum, hkv_sum,
                           ap_cables=None, hl_rows=None, hl_sum=None):
        path, _ = QFileDialog.getSaveFileName(
            self, "Längen als CSV speichern", "laengen.csv",
            "CSV (*.csv)")
        if not path:
            return
        sep = ";"
        lines: list[str] = []

        # Heizkreise Einzellängen
        lines.append("Heizkreise - Einzellängen")
        lines.append(sep.join(["Name", "Rohrdurchmesser (mm)",
                               "Verlegeabstand (mm)",
                               "Rohr (m)", "Zuleitung (m)", "Gesamt (m)"]))
        for r in hk_rows:
            lines.append(sep.join([
                r["name"],
                f"{r['diameter_mm']:.1f}",
                f"{r['spacing_mm']:.1f}",
                f"{r['route_m']:.2f}",
                f"{r['supply_m']:.2f}",
                f"{r['total_m']:.2f}",
            ]))
        lines.append("")

        # Heizkreise Summe pro Durchmesser
        lines.append("Heizkreise - Summe pro Rohrdurchmesser")
        lines.append(sep.join(["Rohrdurchmesser (mm)", "Gesamtlänge (m)"]))
        for d in sorted(hk_sum.keys()):
            lines.append(sep.join([f"{d:.1f}", f"{hk_sum[d]:.2f}"]))
        lines.append("")

        # Hydraulische Übersicht & Abgleich
        lines.append("Heizkreise - Hydraulische Übersicht & Abgleich")
        lines.append(sep.join([
            "Name", "HKV", "Raumtemp. (°C)", "Fußbodenbelag",
            "Fläche (m²)", "q (W/m²)",
            "Leistung (W)", "Volumenstrom (l/min)", "Δp Rohr (mbar)",
            "Δp max (mbar)", "Δp Ventil (mbar)", "Kv (m³/h)",
            "Soll-Durchfluss (l/min)",
        ]))
        for r in hk_rows:
            lines.append(sep.join([
                r["name"],
                r.get("distributor", ""),
                f"{r['room_temp']:.1f}",
                r.get("floor_covering", ""),
                f"{r['area_m2']:.2f}",
                f"{r['q_wm2']:.1f}",
                f"{r['power_w']:.0f}",
                f"{r['volume_flow_lmin']:.2f}",
                f"{r['pressure_drop_mbar']:.1f}",
                f"{r.get('dp_max_mbar', 0.0):.1f}",
                f"{r.get('dp_valve_mbar', 0.0):.1f}",
                f"{r.get('kv_value', 0.0):.3f}",
                f"{r['volume_flow_lmin']:.2f}",
            ]))
        lines.append("")

        # Summe pro Heizkreisverteiler
        if hkv_sum:
            lines.append("Summe pro Heizkreisverteiler")
            lines.append(sep.join([
                "Heizkreisverteiler", "Volumenstrom (l/min)", "Leistung (W)",
            ]))
            for name in sorted(hkv_sum.keys()):
                lines.append(sep.join([
                    name,
                    f"{hkv_sum[name]['volume_flow']:.2f}",
                    f"{hkv_sum[name]['power']:.0f}",
                ]))
            lines.append("")

        # Elektro Einzellängen
        lines.append("Elektro - Kabelverbindungen")
        lines.append(sep.join(["Name", "Typ", "Start-AP", "End-AP",
                               "Länge (m)"]))
        for r in kv_rows:
            lines.append(sep.join([
                r["name"], r["type"],
                r.get("start_ap", ""), r.get("end_ap", ""),
                f"{r['length_m']:.2f}",
            ]))
        lines.append("")

        # Elektro Summe pro Typ
        lines.append("Elektro - Summe pro Leitungstyp")
        lines.append(sep.join(["Leitungstyp", "Gesamtlänge (m)"]))
        for t in sorted(kv_sum.keys()):
            lines.append(sep.join([t, f"{kv_sum[t]:.2f}"]))
        lines.append("")

        # AP-Anschlüsse
        if ap_cables:
            lines.append("Elektro - Anschlusspunkte")
            lines.append(sep.join(["AP", "Kabel", "Typ", "Anschluss",
                                   "Länge (m)"]))
            for ap_name in sorted(ap_cables.keys()):
                for c in ap_cables[ap_name]:
                    lines.append(sep.join([
                        ap_name, c["cable"], c["type"], c["role"],
                        f"{c['length_m']:.2f}",
                    ]))
            lines.append("")

        # HKV-Leitungen
        if hl_rows:
            lines.append("HKV-Leitungen - Einzellängen")
            lines.append(sep.join(["Name", "Typ", "Start-HKV", "End-HKV",
                                   "Länge (m)"]))
            for r in hl_rows:
                lines.append(sep.join([
                    r["name"], r["type"],
                    r.get("start_hkv", ""), r.get("end_hkv", ""),
                    f"{r['length_m']:.2f}",
                ]))
            lines.append("")

            if hl_sum:
                lines.append("HKV-Leitungen - Summe pro Leitungstyp")
                lines.append(sep.join(["Leitungstyp", "Gesamtlänge (m)"]))
                for t in sorted(hl_sum.keys()):
                    lines.append(sep.join([t, f"{hl_sum[t]:.2f}"]))
                lines.append("")

        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(lines))
        self.status.showMessage(f"\u2705 L\u00e4ngen exportiert: {path}")

    # ------------------------------------------------------------------ #
    #  PDF-Export                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_ap_cable_map(kv_rows: list[dict]) -> dict[str, list[dict]]:
        """Build {ap_name: [{cable_name, type, length_m, role}, ...]}."""
        ap_map: dict[str, list[dict]] = defaultdict(list)
        for r in kv_rows:
            for role, key in [("Start", "start_ap"), ("Ende", "end_ap")]:
                ap = r.get(key, "")
                if ap:
                    ap_map[ap].append({
                        "cable": r["name"], "type": r["type"],
                        "length_m": r["length_m"], "role": role,
                    })
        return dict(ap_map)

    def _collect_export_data(self) -> dict:
        """Collect circuit + elektro data for export."""
        scale = self.canvas.get_mm_per_px()
        heat = self.param_panel.get_heating_params()
        t_supply = heat["t_supply"]
        t_return = heat["t_return"]

        hk_rows = []
        for cid, panel in self.param_panel.circuit_panels.items():
            params = panel.get_parameters()
            route_m = self.canvas.get_manual_route_length_px(cid) * scale / 1000.0
            supply_m = self.canvas.get_supply_line_length_px(cid) * scale / 1000.0
            total_m = route_m + supply_m
            area_mm2 = self._compute_polygon_area_mm2(cid)
            area_m2 = (area_mm2 or 0.0) / 1_000_000.0
            spacing_cm = params["spacing"] / 10.0
            floor_name = params.get("floor_covering", "Fliesen / Keramik")
            r_lambda_b = FLOOR_COVERINGS.get(floor_name, 0.01)
            room_temp = params.get("room_temp", 20.0)
            diameter_mm = params.get("diameter", 16.0)
            hc = calc_circuit(
                t_supply=t_supply, t_return=t_return, t_room=room_temp,
                spacing_cm=spacing_cm, r_lambda_b=r_lambda_b,
                area_m2=area_m2, pipe_length_m=route_m,
                outer_diameter_mm=diameter_mm, total_pipe_length_m=total_m,
            )
            hk_rows.append({
                "name": params["name"], "diameter_mm": diameter_mm,
                "spacing_mm": params["spacing"],
                "route_m": route_m, "supply_m": supply_m, "total_m": total_m,
                "area_m2": area_m2, "room_temp": room_temp,
                "floor_covering": floor_name,
                "distributor": params.get("distributor", ""), **hc,
            })
        hk_rows = calc_balancing(hk_rows)

        hkv_sum: dict[str, dict] = defaultdict(lambda: {"volume_flow": 0.0, "power": 0.0})
        for r in hk_rows:
            dist = r.get("distributor", "")
            if dist:
                hkv_sum[dist]["volume_flow"] += r["volume_flow_lmin"]
                hkv_sum[dist]["power"] += r["power_w"]

        kv_rows = []
        for kid, panel in self.param_panel.elec_cable_panels.items():
            params = panel.get_parameters()
            length_m = self.canvas.get_elec_cable_length_px(kid) * scale / 1000.0
            start_ap_id, end_ap_id = self.canvas.get_cable_ap(kid)
            start_name = ""
            if start_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(start_ap_id)
                start_name = (ap_p.get_parameters()["name"]
                              if ap_p else start_ap_id)
            end_name = ""
            if end_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(end_ap_id)
                end_name = (ap_p.get_parameters()["name"]
                            if ap_p else end_ap_id)
            kv_rows.append({"name": params["name"], "type": params["type"],
                            "length_m": length_m,
                            "start_ap": start_name, "end_ap": end_name})

        kv_sum: dict[str, float] = defaultdict(float)
        for r in kv_rows:
            kv_sum[r["type"]] += r["length_m"]

        ap_cables = self._build_ap_cable_map(kv_rows)

        # ── HKV-Leitungen sammeln ──
        hl_rows: list[dict] = []
        for lid, panel in self.param_panel.hkv_line_panels.items():
            params = panel.get_parameters()
            length_m = self.canvas.get_hkv_line_length_px(lid) * scale / 1000.0
            start_hkv_id, end_hkv_id = self.canvas.get_hkv_line_ap(lid)
            start_name = ""
            if start_hkv_id:
                hp = self.param_panel.hkv_panels.get(start_hkv_id)
                start_name = (hp.get_parameters()["name"]
                              if hp else start_hkv_id)
            end_name = ""
            if end_hkv_id:
                hp = self.param_panel.hkv_panels.get(end_hkv_id)
                end_name = (hp.get_parameters()["name"]
                            if hp else end_hkv_id)
            hl_rows.append({"name": params["name"], "type": params["type"],
                            "length_m": length_m,
                            "start_hkv": start_name, "end_hkv": end_name})

        hl_sum: dict[str, float] = defaultdict(float)
        for r in hl_rows:
            hl_sum[r["type"]] += r["length_m"]

        return {
            "t_supply": t_supply, "t_return": t_return,
            "hk_rows": hk_rows, "hkv_sum": hkv_sum,
            "kv_rows": kv_rows, "kv_sum": kv_sum,
            "ap_cables": ap_cables,
            "hl_rows": hl_rows, "hl_sum": hl_sum,
        }

    # ── PDF-Export ──

    def _export_pdf(self):
        """Export project as multi-page A4-landscape PDF.

        Page layout:
        1. Übersicht – Grundriss mit ALLEN Elementen
        2. Heizung – Grundriss nur mit Heizkreisen
        3. Rohrlängen – Tabelle
        4. Hydraulik & Abgleich – Tabelle
        5. Elektro – Grundriss nur mit Elektro-Elementen + Tabelle
        6+. Pro Grundriss – Einzelne Seite mit Heizung + Elektro
        """
        path, _ = QFileDialog.getSaveFileName(
            self, "Als PDF exportieren", "projektbericht.pdf", "PDF (*.pdf)")
        if not path:
            return

        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        printer.setPageMargins(QMarginsF(12, 10, 12, 10),
                               QPageLayout.Unit.Millimeter)

        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.warning(self, "Fehler",
                                "PDF konnte nicht erstellt werden.")
            return

        try:
            dpi = printer.resolution()
            ctx = _PdfContext(printer, painter, dpi)
            data = self._collect_export_data()

            # Also write plan SVG next to PDF (all elements)
            svg_path = str(Path(path).with_suffix('.svg'))
            self._write_plan_svg(svg_path)

            # Page 1 – Übersicht (alle Elemente)
            self._pdf_plan_page(ctx, "Gesamt\u00fcbersicht \u2013 Alle Elemente",
                                layer="all")

            # Page 2 – Heizung Plan
            printer.newPage()
            self._pdf_plan_page(
                ctx,
                "Fu\u00dfbodenheizung \u2013 Verlegeplan",
                layer="heating",
            )

            # Page 3 – Rohrlängen
            printer.newPage()
            self._pdf_lengths_page(ctx, data)

            # Page 4 – Hydraulik & Abgleich
            printer.newPage()
            self._pdf_hydraulics_page(ctx, data)

            # Page 5 – Elektro (Plan + Tabelle)
            has_elec = (data["kv_rows"]
                        or self.canvas._elec_points
                        or self.canvas._elec_cables)
            if has_elec:
                printer.newPage()
                self._pdf_elektro_page(ctx, data)

            # Per-floor-plan pages – each floor plan on its own page
            for fid in self.canvas._floor_plan_order:
                fp_layer = self.canvas._floor_plans.get(fid)
                if not fp_layer or not fp_layer.visible:
                    continue
                # Determine floor plan name from panel or fallback
                fp_name = fid
                fp_panel = self.param_panel.floorplan_panels.get(fid)
                if fp_panel:
                    fp_name = fp_panel.le_name.text() or fid
                printer.newPage()
                self._pdf_plan_page(
                    ctx,
                    f"Grundriss \u2013 {fp_name}",
                    layer="all",
                    floor_plan_id=fid,
                )
        finally:
            painter.end()

        self.status.showMessage(f"\U0001f4c4 PDF exportiert: {path}")

    # ── Seite: Plan-Darstellung (generisch) ──

    def _pdf_plan_page(self, ctx: '_PdfContext', title: str,
                       layer: str = "all",
                       floor_plan_id: str | None = None):
        """Render a full-page plan image with a title."""
        page = ctx.page_rect()
        ctx.stamp(page)

        ctx.painter.save()
        ctx.painter.setFont(QFont("Arial", 14, QFont.Bold))
        title_h = ctx.mm(8)
        ctx.painter.drawText(
            QRectF(page.x(), page.y(), page.width(), title_h),
            Qt.AlignCenter, title,
        )
        ctx.painter.restore()

        draw_rect = QRectF(
            page.x(), page.y() + title_h + ctx.mm(2),
            page.width(),
            page.height() - title_h - ctx.mm(6),
        )

        self._render_plan_to_painter(ctx.painter, draw_rect, layer=layer,
                                     floor_plan_id=floor_plan_id)

    # ── Seite: Rohrlängen ──

    def _pdf_lengths_page(self, ctx: '_PdfContext', data: dict):
        page = ctx.page_rect()
        ctx.stamp(page)
        y = ctx.title(page, "Heizkreise \u2013 Rohrlängen",
                      f"Vorlauf {data['t_supply']:.1f} °C / "
                      f"Rücklauf {data['t_return']:.1f} °C")
        headers = ["Name", "Durchm. (mm)", "Abstand (mm)",
                   "Rohr (m)", "Zuleitung (m)", "Gesamt (m)"]
        rows = [[r["name"], f"{r['diameter_mm']:.1f}",
                 f"{r['spacing_mm']:.1f}",
                 f"{r['route_m']:.2f}", f"{r['supply_m']:.2f}",
                 f"{r['total_m']:.2f}"]
                for r in data["hk_rows"]]
        ctx.draw_table(page, y, headers, rows)

    # ── Seite: Hydraulik & Abgleich ──

    def _pdf_hydraulics_page(self, ctx: '_PdfContext', data: dict):
        page = ctx.page_rect()
        ctx.stamp(page)
        y = ctx.title(page, "Hydraulische Übersicht & Abgleich",
                      f"Vorlauf {data['t_supply']:.1f} °C / "
                      f"Rücklauf {data['t_return']:.1f} °C")
        headers = ["Name", "HKV", "Raum\n(°C)", "Belag",
                   "Fläche\n(m²)", "q\n(W/m²)",
                   "Leistung\n(W)", "V̇\n(l/min)",
                   "Δp Rohr\n(mbar)", "Δp max\n(mbar)",
                   "Δp Ventil\n(mbar)", "Kv\n(m³/h)",
                   "Soll-V̇\n(l/min)"]
        rows = []
        for r in data["hk_rows"]:
            rows.append([
                r["name"], r["distributor"],
                f"{r['room_temp']:.1f}", r["floor_covering"],
                f"{r['area_m2']:.2f}", f"{r['q_wm2']:.1f}",
                f"{r['power_w']:.0f}", f"{r['volume_flow_lmin']:.2f}",
                f"{r['pressure_drop_mbar']:.1f}",
                f"{r.get('dp_max_mbar',0):.1f}",
                f"{r.get('dp_valve_mbar',0):.1f}",
                f"{r.get('kv_value',0):.3f}",
                f"{r['volume_flow_lmin']:.2f}",
            ])
        col_w = [1.3, 0.9, 0.6, 1.3, 0.7, 0.6, 0.8, 0.7, 0.8, 0.8, 0.8, 0.7, 0.7]
        y_after = ctx.draw_table(page, y, headers, rows, col_widths=col_w)

        # HKV Summe
        hkv = data["hkv_sum"]
        if hkv:
            y_after += ctx.mm(4)
            ctx.painter.save()
            ctx.painter.setFont(QFont("Arial", 9, QFont.Bold))
            ctx.painter.drawText(
                int(page.x()), int(y_after + ctx.mm(3)),
                "Summe pro Heizkreisverteiler:")
            ctx.painter.restore()
            y_after += ctx.mm(5)
            h2 = ["Heizkreisverteiler", "Volumenstrom (l/min)",
                  "Leistung (W)"]
            r2 = [[n, f"{hkv[n]['volume_flow']:.2f}",
                   f"{hkv[n]['power']:.0f}"]
                  for n in sorted(hkv.keys())]
            y_after = ctx.draw_table(page, y_after, h2, r2)

        # HKV-Leitungen
        hl_rows = data.get("hl_rows", [])
        if hl_rows:
            y_after += ctx.mm(4)
            ctx.painter.save()
            ctx.painter.setFont(QFont("Arial", 9, QFont.Bold))
            ctx.painter.drawText(
                int(page.x()), int(y_after + ctx.mm(3)),
                "HKV-Leitungen:")
            ctx.painter.restore()
            y_after += ctx.mm(5)
            hl_headers = ["Name", "Typ", "Start-HKV", "End-HKV",
                          "Länge (m)"]
            hl_data = [[r["name"], r["type"],
                        r.get("start_hkv", ""), r.get("end_hkv", ""),
                        f"{r['length_m']:.2f}"]
                       for r in hl_rows]
            y_after = ctx.draw_table(page, y_after, hl_headers, hl_data)

            hl_sum = data.get("hl_sum", {})
            if hl_sum:
                y_after += ctx.mm(3)
                ctx.painter.save()
                ctx.painter.setFont(QFont("Arial", 8, QFont.Bold))
                ctx.painter.drawText(
                    int(page.x()), int(y_after + ctx.mm(3)),
                    "Summe pro Leitungstyp:")
                ctx.painter.restore()
                y_after += ctx.mm(4)
                hl_s_h = ["Leitungstyp", "Gesamtlänge (m)"]
                hl_s_r = [[t, f"{hl_sum[t]:.2f}"]
                          for t in sorted(hl_sum.keys())]
                ctx.draw_table(page, y_after, hl_s_h, hl_s_r)

    # ── Seite: Elektro (Plan + Tabelle) ──

    def _pdf_elektro_page(self, ctx: '_PdfContext', data: dict):
        """Elektro page: plan with only elektro elements, then table below."""
        page = ctx.page_rect()
        ctx.stamp(page)

        title_h = ctx.mm(8)
        ctx.painter.save()
        ctx.painter.setFont(QFont("Arial", 14, QFont.Bold))
        ctx.painter.drawText(
            QRectF(page.x(), page.y(), page.width(), title_h),
            Qt.AlignCenter,
            "Elektro \u2013 \u00dcbersicht",
        )
        ctx.painter.restore()

        # Upper half: plan image (elektro only)
        plan_top = page.y() + title_h + ctx.mm(2)
        plan_h = page.height() * 0.48
        plan_rect = QRectF(page.x(), plan_top, page.width(), plan_h)
        self._render_plan_to_painter(ctx.painter, plan_rect, layer="elektro")

        # Lower half: table
        table_y = plan_top + plan_h + ctx.mm(4)

        if data["kv_rows"]:
            ctx.painter.save()
            ctx.painter.setFont(QFont("Arial", 10, QFont.Bold))
            ctx.painter.drawText(
                QRectF(page.x(), table_y, page.width(), ctx.mm(5)),
                Qt.AlignLeft | Qt.AlignVCenter,
                "Kabelverbindungen",
            )
            ctx.painter.restore()
            table_y += ctx.mm(6)

            headers = ["Name", "Typ", "Start-AP", "End-AP", "L\u00e4nge (m)"]
            rows = [[r["name"], r["type"],
                     r.get("start_ap", ""), r.get("end_ap", ""),
                     f"{r['length_m']:.2f}"]
                    for r in data["kv_rows"]]
            y_after = ctx.draw_table(page, table_y, headers, rows)

            kv_sum = data["kv_sum"]
            if kv_sum:
                y_after += ctx.mm(4)
                ctx.painter.save()
                ctx.painter.setFont(QFont("Arial", 9, QFont.Bold))
                ctx.painter.drawText(
                    int(page.x()), int(y_after + ctx.mm(3)),
                    "Summe pro Leitungstyp:")
                ctx.painter.restore()
                y_after += ctx.mm(5)
                h2 = ["Leitungstyp", "Gesamtl\u00e4nge (m)"]
                r2 = [[t, f"{kv_sum[t]:.2f}"]
                      for t in sorted(kv_sum.keys())]
                y_after = ctx.draw_table(page, y_after, h2, r2)

        # AP connection summary
        ap_cables = data.get("ap_cables", {})
        if ap_cables:
            y_after = (y_after or table_y) + ctx.mm(4)
            ctx.painter.save()
            ctx.painter.setFont(QFont("Arial", 9, QFont.Bold))
            ctx.painter.drawText(
                int(page.x()), int(y_after + ctx.mm(3)),
                "Anschlusspunkte \u2013 Kabelverbindungen:")
            ctx.painter.restore()
            y_after += ctx.mm(5)
            ap_headers = ["AP", "Kabel", "Typ", "Anschluss",
                          "L\u00e4nge (m)"]
            ap_rows = []
            for ap_name in sorted(ap_cables.keys()):
                for c in ap_cables[ap_name]:
                    ap_rows.append([ap_name, c["cable"], c["type"],
                                    c["role"], f"{c['length_m']:.2f}"])
            ctx.draw_table(page, y_after, ap_headers, ap_rows)


# ====================================================================== #
#  PDF rendering helper                                                    #
# ====================================================================== #

class _PdfContext:
    """DPI-aware helper for painting onto a QPrinter (PDF output)."""

    def __init__(self, printer: QPrinter, painter: QPainter, dpi: int):
        self.printer = printer
        self.painter = painter
        self.dpi = dpi
        # QPainter on a QPrinter maps (0,0) to the top-left of the
        # printable area (inside margins).  pageRect gives size only;
        # we must NOT use its x/y offset, otherwise margins are doubled.
        pr = printer.pageRect(QPrinter.Unit.DevicePixel)
        self._pr = QRectF(0, 0, pr.width(), pr.height())

    def mm(self, millimeters: float) -> float:
        return millimeters * self.dpi / 25.4

    def page_rect(self) -> QRectF:
        return QRectF(self._pr)

    def stamp(self, rect: QRectF):
        from main import VERSION
        now = QDateTime.currentDateTime().toString("dd.MM.yyyy  HH:mm")
        self.painter.save()
        self.painter.setFont(QFont("Arial", 7))
        self.painter.setPen(Qt.darkGray)
        txt = f"HRouting v{VERSION}  \u2013  {now}"
        tw, th = self.mm(80), self.mm(4)
        self.painter.drawText(
            QRectF(rect.right() - tw, rect.bottom() - th, tw, th),
            Qt.AlignRight | Qt.AlignBottom, txt)
        self.painter.restore()

    def title(self, page: QRectF, text: str, subtitle: str = "") -> float:
        self.painter.save()
        h = self.mm(7)
        self.painter.setFont(QFont("Arial", 13, QFont.Bold))
        self.painter.drawText(
            QRectF(page.x(), page.y(), page.width(), h),
            Qt.AlignLeft | Qt.AlignVCenter, text)
        y = page.y() + h
        if subtitle:
            sh = self.mm(5)
            self.painter.setFont(QFont("Arial", 9))
            self.painter.drawText(
                QRectF(page.x(), y, page.width(), sh),
                Qt.AlignLeft | Qt.AlignVCenter, subtitle)
            y += sh
        self.painter.restore()
        return y + self.mm(2)

    def draw_table(self, page: QRectF, y_start: float,
                   headers: list[str], rows: list[list[str]],
                   col_widths: list[float] | None = None) -> float:
        from PySide6.QtGui import QPen, QBrush
        n_cols = len(headers)
        table_w = page.width()
        if col_widths:
            total_w = sum(col_widths)
            widths = [w / total_w * table_w for w in col_widths]
        else:
            widths = [table_w / n_cols] * n_cols

        header_h = self.mm(8)
        row_h = self.mm(5.5)
        x0 = page.x()

        self.painter.save()
        self.painter.setPen(QPen(Qt.black, max(1, self.mm(0.15))))

        # Header
        self.painter.setFont(QFont("Arial", 7, QFont.Bold))
        cx = x0
        for j, h in enumerate(headers):
            r = QRectF(cx, y_start, widths[j], header_h)
            self.painter.fillRect(r, QBrush(QColor("#e0e0e0")))
            self.painter.drawRect(r)
            self.painter.drawText(
                r.adjusted(self.mm(1), 0, -self.mm(1), 0),
                Qt.AlignCenter | Qt.TextWordWrap, h)
            cx += widths[j]
        y = y_start + header_h

        # Data rows
        self.painter.setFont(QFont("Arial", 7))
        for ri, row in enumerate(rows):
            if ri % 2 == 1:
                cx = x0
                for j in range(n_cols):
                    self.painter.fillRect(
                        QRectF(cx, y, widths[j], row_h),
                        QBrush(QColor("#f5f5f5")))
                    cx += widths[j]
            cx = x0
            for j, cell in enumerate(row):
                r = QRectF(cx, y, widths[j], row_h)
                self.painter.drawRect(r)
                align = ((Qt.AlignRight | Qt.AlignVCenter)
                         if j >= 2
                         else (Qt.AlignLeft | Qt.AlignVCenter))
                self.painter.drawText(
                    r.adjusted(self.mm(1), 0, -self.mm(1), 0),
                    align, cell)
                cx += widths[j]
            y += row_h

        self.painter.restore()
        return y