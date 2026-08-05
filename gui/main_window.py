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
    QDialogButtonBox, QTabWidget, QPushButton, QHeaderView, QMenu,
    QApplication, QCheckBox, QSpinBox, QDoubleSpinBox, QInputDialog,
    QSplitter,
    QProgressDialog,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QPainter, QPageLayout,
    QPen, QBrush, QPolygonF, QPainterPath, QKeySequence, QImage, QShortcut,
)
from PySide6.QtCore import Qt, QSettings, QMarginsF, QRectF, QDateTime, QPointF, QTimer, QByteArray, QBuffer, QIODevice, QSize
from PySide6.QtPrintSupport import QPrinter

from gui.canvas_widget import CanvasWidget, COLORS, ToolMode
from gui.parameter_panel import ParameterPanel, SafeDoubleSpinBox, SafeComboBox, BUILTIN_SYMBOLS
from gui.pdf_export_dialog import PdfExportConfigDialog
from gui.elec_schema_window import ElecSchemaWindow, ApNode, CableEdge
from gui.schaltplan_window import SchaltplanWindow
from logic.schaltplan_generator import build_uv_hierarchy, get_uv_circuits
from logic.elec_schematic import sanitize_elec_schematic, infer_elec_schematic_from_legacy
from logic.svg_parser import parse_svg_dimensions
from logic.heating_calc import calc_circuit, calc_balancing, FLOOR_COVERINGS
from logic.kicad_export import export_project_to_kicad

_SETTINGS = QSettings("HRouting", "HRouting")
_LAST_PROJECT_KEY = "last_project_path"
_RECENT_KEY = "recent_projects"
_MAX_RECENT = 8
_MAX_UNDO = 80
_PARAMS_UI_STATE_KEY = "ui_state"

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
        self._elec_room_counter = 0
        self._elec_cable_counter = 0
        self._hkv_counter = 0
        self._hkv_line_counter = 0
        self._text_counter = 0
        self._floorplan_counter = 0
        self._furniture_counter = 0
        self._pdf_export_pages: list[dict] = []
        self._pdf_export_dialog = None
        self._elec_schema_window: ElecSchemaWindow | None = None
        self._elec_schema_ap_positions: dict[str, list[float]] = {}
        self._schaltplan_window: SchaltplanWindow | None = None
        self._elec_point_room_map: dict[str, str] = {}
        self._project_ui_state: dict = {}
        self._suspend_schema_refresh = False
        self._dirty = False
        self._copy_buffer: dict | None = None

        # Undo / Redo
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._undo_blocked = False
        self._last_snapshot: dict | None = None

        self._dirty_debounce_timer = QTimer(self)
        self._dirty_debounce_timer.setSingleShot(True)
        self._dirty_debounce_timer.setInterval(300)
        self._dirty_debounce_timer.timeout.connect(self._apply_debounced_dirty)

        self._build_ui()
        self._build_toolbar()
        self.canvas.set_helper_line_target_length_mm(self._helper_len_spin.value() * 1000.0)
        self._build_menubar()
        self._build_shortcuts()
        self._connect_signals()
        self._pdf_export_pages = self._default_pdf_export_pages()
        self._auto_load_last_project()

        # Capture the initial state as baseline for undo
        self._last_snapshot = self._capture_snapshot()

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
        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(self.canvas)
        self._main_splitter.addWidget(self.param_panel)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 0)
        self._main_splitter.setSizes([1100, 380])

        layout.addWidget(self._main_splitter, stretch=1)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(
            "Willkommen! SVG laden → Referenzlinie zeichnen → Heizkreis hinzufügen"
        )

    def _build_toolbar(self):
        tb = QToolBar("Werkzeuge")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        def _add_toolbar_group_separator():
            sep = QLabel("│")
            sep.setStyleSheet("color:#666; font-size:14px; padding:0 6px;")
            tb.addWidget(sep)

        file_lbl = QLabel("Datei")
        file_lbl.setStyleSheet("color:#9aa0a6; font-size:11px;")
        tb.addWidget(file_lbl)
        for text, tooltip, slot in [
            ("📄", "Neues Projekt", self._new_project),
            ("📂", "Grundriss laden", self._open_svg),
            ("💾", "Speichern", self._save_project),
            ("🖫", "Speichern unter…", self._save_project_as),
            ("📁", "Projekt öffnen…", self._open_project),
            ("📤", "SVG exportieren", self._export_svg),
            ("📊", "Projektübersicht", self._export_lengths),
            ("📄", "Als PDF exportieren", self._export_pdf),
        ]:
            act = QAction(text, self)
            act.setToolTip(tooltip)
            act.setStatusTip(tooltip)
            act.triggered.connect(slot)
            tb.addAction(act)
        _add_toolbar_group_separator()

        # Snap-angle dropdown
        snap_lbl = QLabel("Fang")
        snap_lbl.setStyleSheet("color:#9aa0a6; font-size:11px;")
        tb.addWidget(snap_lbl)
        lbl = QLabel("∡")
        lbl.setToolTip("Fangwinkel")
        tb.addWidget(lbl)
        self._snap_combo = SafeComboBox()
        self._snap_combo.addItem("Aus",   0)
        self._snap_combo.addItem("45°",  45)
        self._snap_combo.addItem("90°",  90)
        self._snap_combo.addItem("120°", 120)
        self._snap_combo.setCurrentIndex(2)   # default 90°
        self._snap_combo.currentIndexChanged.connect(self._on_snap_angle_changed)
        tb.addWidget(self._snap_combo)
        _add_toolbar_group_separator()

        # ── Grid controls ──────────────────────────────────────────────
        grid_lbl = QLabel("Raster")
        grid_lbl.setStyleSheet("color:#9aa0a6; font-size:11px;")
        tb.addWidget(grid_lbl)
        self._grid_cb = QCheckBox("◫")
        self._grid_cb.setToolTip("Raster ein/aus")
        self._grid_cb.setChecked(False)
        self._grid_cb.stateChanged.connect(self._on_grid_toggled)
        tb.addWidget(self._grid_cb)

        dist_lbl = QLabel("d")
        dist_lbl.setToolTip("Rasterabstand")
        tb.addWidget(dist_lbl)
        self._grid_spin = SafeDoubleSpinBox()
        self._grid_spin.setDecimals(2)
        self._grid_spin.setRange(0.01, 10.0)
        self._grid_spin.setSingleStep(0.01)
        self._grid_spin.setValue(0.10)
        self._grid_spin.setSuffix(" m")
        self._grid_spin.setFixedWidth(90)
        self._grid_spin.valueChanged.connect(self._on_grid_spacing_changed)
        tb.addWidget(self._grid_spin)

        self._grid_color_btn = QPushButton("▦")
        self._grid_color_btn.setToolTip("Rasterfarbe")
        self._grid_color_btn.setFixedWidth(28)
        self._grid_color_btn.clicked.connect(self._on_grid_color_pick)
        self._update_grid_color_btn(QColor(255, 255, 255, 60))
        tb.addWidget(self._grid_color_btn)

        self._bg_color_btn = QPushButton("◼")
        self._bg_color_btn.setToolTip("Hintergrundfarbe")
        self._bg_color_btn.setFixedWidth(28)
        self._bg_color_btn.clicked.connect(self._on_bg_color_pick)
        self._update_bg_color_btn(QColor("#2b2b2b"))
        tb.addWidget(self._bg_color_btn)
        _add_toolbar_group_separator()

        # ── Measurement tool ──
        measure_lbl = QLabel("Messen")
        measure_lbl.setStyleSheet("color:#9aa0a6; font-size:11px;")
        tb.addWidget(measure_lbl)
        self._measure_btn = QPushButton("📏")
        self._measure_btn.setToolTip("Abstand zwischen zwei Punkten messen")
        self._measure_btn.setCheckable(True)
        self._measure_btn.setFixedWidth(36)
        self._measure_btn.clicked.connect(self._on_measure_toggled)
        tb.addWidget(self._measure_btn)

        self._measure_color_btn = QPushButton()
        self._measure_color_btn.setToolTip("Messlinien-Farbe")
        self._measure_color_btn.setFixedWidth(28)
        self._measure_color_btn.setStyleSheet("background:#00e5ff;")
        self._measure_color_btn.clicked.connect(self._on_measure_color_pick)
        tb.addWidget(self._measure_color_btn)

        self._clear_measure_btn = QPushButton("✕")
        self._clear_measure_btn.setToolTip("Alle Messlinien löschen")
        self._clear_measure_btn.setFixedWidth(28)
        self._clear_measure_btn.clicked.connect(self._on_clear_measurements)
        tb.addWidget(self._clear_measure_btn)

        self._angle_measure_btn = QPushButton("∠")
        self._angle_measure_btn.setToolTip("Winkel über 3 Punkte messen")
        self._angle_measure_btn.setCheckable(True)
        self._angle_measure_btn.setFixedWidth(36)
        self._angle_measure_btn.clicked.connect(self._on_angle_measure_toggled)
        tb.addWidget(self._angle_measure_btn)

        self._clear_angle_btn = QPushButton("✕")
        self._clear_angle_btn.setToolTip("Alle Winkelmessungen löschen")
        self._clear_angle_btn.setFixedWidth(28)
        self._clear_angle_btn.clicked.connect(self._on_clear_angle_measurements)
        tb.addWidget(self._clear_angle_btn)

        tb.addSeparator()
        helper_lbl = QLabel("Hilfslinien")
        helper_lbl.setStyleSheet("color:#9aa0a6; font-size:11px;")
        tb.addWidget(helper_lbl)
        self._helper_draw_btn = QPushButton("📐")
        self._helper_draw_btn.setToolTip("Hilfslinie mit definierter Länge einzeichnen")
        self._helper_draw_btn.setCheckable(True)
        self._helper_draw_btn.setFixedWidth(36)
        self._helper_draw_btn.clicked.connect(self._on_helper_draw_toggled)
        tb.addWidget(self._helper_draw_btn)

        self._helper_edit_btn = QPushButton("✥")
        self._helper_edit_btn.setToolTip("Hilfslinien bearbeiten (ziehen/löschen)")
        self._helper_edit_btn.setCheckable(True)
        self._helper_edit_btn.setFixedWidth(36)
        self._helper_edit_btn.clicked.connect(self._on_helper_edit_toggled)
        tb.addWidget(self._helper_edit_btn)

        len_lbl = QLabel("L")
        len_lbl.setToolTip("Hilfslinien-Länge")
        tb.addWidget(len_lbl)
        self._helper_len_spin = SafeDoubleSpinBox()
        self._helper_len_spin.setDecimals(3)
        self._helper_len_spin.setRange(0.01, 200.0)
        self._helper_len_spin.setSingleStep(0.1)
        self._helper_len_spin.setValue(1.0)
        self._helper_len_spin.setSuffix(" m")
        self._helper_len_spin.setFixedWidth(90)
        self._helper_len_spin.valueChanged.connect(self._on_helper_length_changed)
        tb.addWidget(self._helper_len_spin)

        self._clear_helper_btn = QPushButton("✕")
        self._clear_helper_btn.setToolTip("Alle Hilfslinien löschen")
        self._clear_helper_btn.setFixedWidth(28)
        self._clear_helper_btn.clicked.connect(self._on_clear_helper_lines)
        tb.addWidget(self._clear_helper_btn)

        self._helper_color_btn = QPushButton()
        self._helper_color_btn.setToolTip("Hilfslinien-Farbe")
        self._helper_color_btn.setFixedWidth(28)
        self._helper_color_btn.setStyleSheet("background:#f8f32b;")
        self._helper_color_btn.clicked.connect(self._on_helper_color_pick)
        tb.addWidget(self._helper_color_btn)

        for widget in (
            helper_lbl,
            self._helper_draw_btn,
            self._helper_edit_btn,
            len_lbl,
            self._helper_len_spin,
            self._clear_helper_btn,
            self._helper_color_btn,
        ):
            widget.setVisible(False)
        _add_toolbar_group_separator()

        # ── Export-Rahmen tool ──
        export_lbl = QLabel("Export")
        export_lbl.setStyleSheet("color:#9aa0a6; font-size:11px;")
        tb.addWidget(export_lbl)
        self._export_frame_btn = QPushButton("⬚")
        self._export_frame_btn.setToolTip(
            "Export-Rahmen aufziehen – Linksklick ziehen, Rechtsklick löschen, ESC abbrechen"
        )
        self._export_frame_btn.setCheckable(True)
        self._export_frame_btn.setFixedWidth(36)
        self._export_frame_btn.clicked.connect(self._on_export_frame_toggled)
        tb.addWidget(self._export_frame_btn)

        self._clear_export_frame_btn = QPushButton("✕")
        self._clear_export_frame_btn.setToolTip("Export-Rahmen löschen")
        self._clear_export_frame_btn.setFixedWidth(28)
        self._clear_export_frame_btn.clicked.connect(self._on_clear_export_frame)
        tb.addWidget(self._clear_export_frame_btn)

    def _build_menubar(self):
        mb = self.menuBar()

        # ── Datei ──
        file_menu = mb.addMenu("&Datei")
        file_menu.addAction("📄 Neues Projekt", self._new_project)
        file_menu.addAction("📂 Projekt öffnen…", self._open_project)
        file_menu.addSeparator()
        save_action = file_menu.addAction("💾 Speichern", self._save_project)
        save_action.setShortcut(QKeySequence.Save)
        save_as_action = file_menu.addAction("💾 Speichern unter…", self._save_project_as)
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        file_menu.addSeparator()

        self._recent_menu = file_menu.addMenu("🕑 Letzte Projekte")
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        file_menu.addAction("Beenden", self.close)

        # ── Bearbeiten ──
        edit_menu = mb.addMenu("&Bearbeiten")
        self._undo_action = edit_menu.addAction("↩ Rückgängig")
        self._undo_action.setShortcut(QKeySequence.Undo)
        self._undo_action.setShortcutContext(Qt.ApplicationShortcut)
        self._undo_action.triggered.connect(self._undo)
        self._undo_action.setEnabled(False)

        self._redo_action = edit_menu.addAction("↪ Wiederherstellen")
        self._redo_action.setShortcuts([
            QKeySequence.Redo,
            QKeySequence("Ctrl+Shift+Z"),
        ])
        self._redo_action.setShortcutContext(Qt.ApplicationShortcut)
        self._redo_action.triggered.connect(self._redo)
        self._redo_action.setEnabled(False)

        edit_menu.addSeparator()
        edit_menu.addAction("🏷️ Anschlusspunkte durchnummerieren", 
                           self._numbering_elec_points)

        # ── Extras ──
        extras_menu = mb.addMenu("&Extras")
        extras_menu.addAction("🧠 Elektro-Strangschema…", self._open_elec_schema_window)
        extras_menu.addAction("📐 Schaltplan…", self._open_schaltplan_window)
        extras_menu.addSeparator()
        extras_menu.addAction("📤 KiCad exportieren…", self._export_to_kicad)

        # ── Hilfe ──
        help_menu = mb.addMenu("&Hilfe")
        help_menu.addAction("ℹ️ Über HRouting…", self._show_about)

    def _build_shortcuts(self):
        self._copy_shortcut = QShortcut(QKeySequence.Copy, self)
        self._copy_shortcut.activated.connect(self._copy_selected_object)

        self._paste_shortcut = QShortcut(QKeySequence.Paste, self)
        self._paste_shortcut.activated.connect(self._paste_copied_object)

        self._delete_shortcut = QShortcut(QKeySequence.Delete, self)
        self._delete_shortcut.activated.connect(self._delete_selected_object)

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

    def _record_mutation(self, *, debounced: bool = False):
        """Central mutation hook for dirty/undo/title updates.

        This keeps mutation bookkeeping in one place so GUI and non-GUI
        mutation paths can be aligned incrementally.
        """
        self._dirty = True
        self._update_title()
        if debounced:
            self._dirty_debounce_timer.start()
        else:
            self._push_undo()

    def _mark_dirty(self, *_args):
        self._record_mutation(debounced=False)

    def _mark_dirty_debounced(self, *_args):
        self._record_mutation(debounced=True)

    def _apply_debounced_dirty(self):
        self._record_mutation(debounced=False)

    def _flush_pending_dirty(self):
        if self._dirty_debounce_timer.isActive():
            self._dirty_debounce_timer.stop()
            self._apply_debounced_dirty()

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

    @staticmethod
    def _encode_qbytearray(value: QByteArray) -> str:
        if not isinstance(value, QByteArray):
            return ""
        encoded = value.toBase64().data()
        return encoded.decode("ascii") if encoded else ""

    @staticmethod
    def _decode_qbytearray(value: object) -> QByteArray:
        if not isinstance(value, str) or not value:
            return QByteArray()
        try:
            return QByteArray.fromBase64(value.encode("ascii"))
        except Exception:
            return QByteArray()

    def _capture_window_state(self, window: QWidget | None) -> dict:
        if window is None:
            return {}
        state = {
            "geometry": self._encode_qbytearray(window.saveGeometry()),
            "visible": bool(window.isVisible()),
            "maximized": bool(window.isMaximized()),
        }
        if hasattr(window, "view") and getattr(window, "view", None) is not None:
            try:
                state["zoom"] = float(window.view.transform().m11())
            except Exception:
                pass
        return state

    def _restore_window_state(self, window: QWidget | None, state: dict):
        if window is None or not isinstance(state, dict):
            return
        geometry = self._decode_qbytearray(state.get("geometry"))
        if not geometry.isEmpty():
            window.restoreGeometry(geometry)
        if bool(state.get("maximized", False)):
            window.showMaximized()
        # Minimized state is intentionally not restored.
        window.setWindowState(window.windowState() & ~Qt.WindowMinimized)

        zoom = state.get("zoom")
        if zoom is not None and hasattr(window, "view") and getattr(window, "view", None) is not None:
            try:
                zoom_value = float(zoom)
            except (TypeError, ValueError):
                zoom_value = 1.0
            zoom_value = max(0.1, min(5.0, zoom_value))
            try:
                window.view.resetTransform()
                window.view.scale(zoom_value, zoom_value)
                if hasattr(window, "_update_zoom_label"):
                    window._update_zoom_label()
            except Exception:
                pass

    def _collect_project_ui_state(self) -> dict:
        main_state = self._capture_window_state(self)
        main_state["splitter_sizes"] = [int(v) for v in self._main_splitter.sizes()]
        main_state["sidebar_width"] = int(self.param_panel.width())
        return {
            "main_window": main_state,
            "elec_schema_window": self._capture_window_state(self._elec_schema_window),
            "schaltplan_window": self._capture_window_state(self._schaltplan_window),
            "pdf_export_dialog": self._capture_window_state(self._pdf_export_dialog),
        }

    def _apply_project_ui_state(self, ui_state: dict):
        self._project_ui_state = dict(ui_state) if isinstance(ui_state, dict) else {}
        if not self._project_ui_state:
            return

        main_state = self._project_ui_state.get("main_window", {})
        if isinstance(main_state, dict):
            self._restore_window_state(self, main_state)
            splitter_sizes = main_state.get("splitter_sizes")
            if (
                isinstance(splitter_sizes, list)
                and len(splitter_sizes) == 2
                and all(isinstance(v, (int, float)) and v > 0 for v in splitter_sizes)
            ):
                self._main_splitter.setSizes([int(splitter_sizes[0]), int(splitter_sizes[1])])

        self._restore_window_state(
            self._elec_schema_window,
            self._project_ui_state.get("elec_schema_window", {}),
        )
        self._restore_window_state(
            self._schaltplan_window,
            self._project_ui_state.get("schaltplan_window", {}),
        )
        self._restore_window_state(
            self._pdf_export_dialog,
            self._project_ui_state.get("pdf_export_dialog", {}),
        )

    def closeEvent(self, event):
        if self._maybe_save():
            self._project_ui_state = self._collect_project_ui_state()
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
            "elec_schema_ap_positions": self._elec_schema_ap_positions,
            "pdf_export_pages": self._pdf_export_pages,
            "counters": {
                "circuit": self._circuit_counter,
                "elec_point": self._elec_point_counter,
                "elec_room": self._elec_room_counter,
                "elec_cable": self._elec_cable_counter,
                "hkv": self._hkv_counter,
                "hkv_line": self._hkv_line_counter,
                "text": self._text_counter,
                "floorplan": self._floorplan_counter,
                "furniture": self._furniture_counter,
            },
        })

    def _push_undo(self):
        """Save the previous state onto the undo stack (before the change)."""
        if self._undo_blocked:
            return
        current = self._capture_snapshot()
        # Avoid pushing a duplicate state (e.g. from multiple rapid signals)
        if self._last_snapshot is not None:
            # Quick identity check: if counters haven't changed, compare key fields
            if (current.get("counters") == self._last_snapshot.get("counters")
                    and current.get("canvas") == self._last_snapshot.get("canvas")
                    and current.get("params") == self._last_snapshot.get("params")
                    and current.get("elec_schema_ap_positions") == self._last_snapshot.get("elec_schema_ap_positions")):
                return  # nothing changed, skip
            self._undo_stack.append(self._last_snapshot)
            if len(self._undo_stack) > _MAX_UNDO:
                self._undo_stack.pop(0)
            self._redo_stack.clear()
        self._last_snapshot = current
        self._update_undo_actions()

    def _undo(self):
        if not self._undo_stack:
            return
        self._flush_pending_dirty()
        # Block undo pushes during and briefly after restore
        self._undo_blocked = True
        # Save current state to redo before restoring
        self._redo_stack.append(self._capture_snapshot())
        snap = self._undo_stack.pop()
        self._last_snapshot = snap
        self._restore_snapshot(snap)
        self._update_undo_actions()
        self.status.showMessage("↩ Rückgängig")
        # Keep blocked until next event-loop tick so deferred signals settle
        QTimer.singleShot(0, self._unblock_undo)

    def _redo(self):
        if not self._redo_stack:
            return
        self._flush_pending_dirty()
        # Block undo pushes during and briefly after restore
        self._undo_blocked = True
        # Save current state to undo before restoring
        self._undo_stack.append(self._capture_snapshot())
        snap = self._redo_stack.pop()
        self._last_snapshot = snap
        self._restore_snapshot(snap)
        self._update_undo_actions()
        self.status.showMessage("↪ Wiederherstellen")
        # Keep blocked until next event-loop tick so deferred signals settle
        QTimer.singleShot(0, self._unblock_undo)

    def _unblock_undo(self):
        """Re-enable undo recording after restore settles."""
        self._undo_blocked = False
        self._last_snapshot = self._capture_snapshot()

    def _update_undo_actions(self):
        if hasattr(self, '_undo_action'):
            self._undo_action.setEnabled(bool(self._undo_stack))
            self._redo_action.setEnabled(bool(self._redo_stack))

    def _restore_snapshot(self, snap: dict):
        """Restore canvas + param panel from a snapshot dict."""
        self._undo_blocked = True
        self._suspend_schema_refresh = True
        window_updates_enabled = self.updatesEnabled()
        canvas_updates_enabled = self.canvas.updatesEnabled()
        panel_updates_enabled = self.param_panel.updatesEnabled()
        canvas_signals_prev = self.canvas.blockSignals(True)
        panel_signals_prev = self.param_panel.blockSignals(True)
        self.setUpdatesEnabled(False)
        self.canvas.setUpdatesEnabled(False)
        self.param_panel.setUpdatesEnabled(False)
        # Keep current viewport during undo/redo (no jump in zoom/pan position)
        current_view_scale = self.canvas._scale
        current_view_offset = QPointF(self.canvas._offset)
        try:
            # Clear existing canvas data (geometry only, no widget destruction)
            self.canvas.clear_data()

            # Restore canvas geometry
            self.canvas.from_dict(snap["canvas"])

            # Restore param panels (incremental – reuses existing widgets)
            self.param_panel.update_panels_from_dict(snap["params"])

            # Restore counters
            c = snap.get("counters", {})
            self._circuit_counter = c.get("circuit", 0)
            self._elec_point_counter = c.get("elec_point", 0)
            self._elec_room_counter = c.get("elec_room", 0)
            self._elec_cable_counter = c.get("elec_cable", 0)
            self._hkv_counter = c.get("hkv", 0)
            self._hkv_line_counter = c.get("hkv_line", 0)
            self._text_counter = c.get("text", 0)
            self._floorplan_counter = c.get("floorplan", 0)
            self._furniture_counter = c.get("furniture", 0)
            self._pdf_export_pages = self._normalize_pdf_export_pages(
                snap.get("pdf_export_pages")
            )
            self._elec_schema_ap_positions = self._sanitize_elec_schema_ap_positions(
                snap.get("elec_schema_ap_positions", {})
            )

            # Reconnect panel signals + sync visual state
            self._reconnect_panels_after_restore()

            # Restore previous viewport after geometry/panel restore finished
            self.canvas._scale = current_view_scale
            self.canvas._offset = QPointF(current_view_offset)

            self._sync_toolbar_from_canvas()

            self._dirty = True
            self._update_title()
        finally:
            self.param_panel.blockSignals(panel_signals_prev)
            self.canvas.blockSignals(canvas_signals_prev)
            self.param_panel.setUpdatesEnabled(panel_updates_enabled)
            self.canvas.setUpdatesEnabled(canvas_updates_enabled)
            self.setUpdatesEnabled(window_updates_enabled)
            self.canvas.update()
            self._suspend_schema_refresh = False
            self._refresh_elec_schema_window()
            # _undo_blocked stays True – caller or QTimer will reset it
            self._refresh_schaltplan_window()

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
            if layer:
                p = panel.get_parameters()
                self.canvas.set_floor_plan_polygon_color(
                    fur_id, p.get("polygon_color", "#8d99ae")
                )
            if layer and layer.polygon and not panel._file_path:
                panel.set_polygon_source()

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
            panel.label_visibility_changed.connect(self._on_label_visibility_changed)
            panel.hydraulics_param_changed.connect(self._recalc_circuit_hydraulics)
            values = panel.get_parameters()
            self.canvas.set_polygon_name(cid, values["name"])
            self.canvas.set_color(cid, QColor(values["color"]))
            self.canvas._circuit_visible[cid] = values.get("visible", True)
            self.canvas.set_label_font_size(cid, values.get("label_size", 12.0))
            self.canvas.set_label_visible(cid, values.get("label_visible", True))
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
            panel.label_visibility_changed.connect(self._on_label_visibility_changed)
            panel.position_changed.connect(self._on_elec_point_position_changed)
            panel.height_changed.connect(self._on_elec_point_height_changed)
            panel.note_changed.connect(self._on_elec_point_note_changed)
            panel.smarthome_device_changed.connect(self._on_elec_point_smarthome_changed)
            panel.smarthome_device_color_changed.connect(self._on_elec_point_smarthome_color_changed)
            values = panel.get_parameters()
            self.canvas._label_map[pid] = values.get("name", pid)
            self.canvas._elec_visible[pid] = values.get("visible", True)
            self.canvas._elec_point_position[pid] = values.get("position", "Wand")
            self.canvas._elec_point_height[pid] = values.get("height_from_floor", 0.0)
            self.canvas._elec_point_notes[pid] = values.get("note", "")
            self.canvas._elec_point_smarthome_device[pid] = values.get("smarthome_device", "")
            self.canvas._elec_point_smarthome_device_color[pid] = values.get("smarthome_device_color", "")
            self.canvas.set_label_font_size(pid, values.get("label_size", 12.0))
            self.canvas.set_label_visible(pid, values.get("label_visible", True))
            self.canvas.set_color(pid, QColor(values.get("color", "#4fc3f7")))
            if values.get("icon_path"):
                self.canvas.set_elec_point_icon(pid, values["icon_path"])

        for rid, panel in self.param_panel.elec_room_panels.items():
            panel.draw_requested.connect(self._on_draw_elec_room)
            panel.edit_requested.connect(self._on_edit_elec_room)
            panel.name_changed.connect(self._on_elec_room_name_changed)
            panel.color_changed.connect(self._on_elec_room_color_changed)
            panel.visibility_changed.connect(self._on_elec_room_visibility_changed)
            panel.label_size_changed.connect(self._on_label_size_changed)
            panel.label_visibility_changed.connect(self._on_label_visibility_changed)
            values = panel.get_parameters()
            self.canvas._label_map[rid] = values.get("name", rid)
            self.canvas._elec_room_visible[rid] = values.get("visible", True)
            self.canvas.set_label_font_size(rid, values.get("label_size", 12.0))
            self.canvas.set_label_visible(rid, values.get("label_visible", True))
            self.canvas.set_color(rid, QColor(values.get("color", "#43aa8b")))

        for kid, panel in self.param_panel.elec_cable_panels.items():
            panel.draw_cable_requested.connect(self._on_draw_elec_cable)
            panel.edit_cable_requested.connect(self._on_edit_elec_cable)
            panel.name_changed.connect(self._on_elec_cable_name_changed)
            panel.color_changed.connect(self._on_elec_cable_color_changed)
            panel.type_changed.connect(self._on_elec_cable_type_changed)
            panel.comment_changed.connect(self._on_elec_cable_comment_changed)
            panel.visibility_changed.connect(self._on_elec_visibility_changed)
            panel.label_size_changed.connect(self._on_label_size_changed)
            panel.label_visibility_changed.connect(self._on_label_visibility_changed)
            panel.type_label_visibility_changed.connect(
                self._on_elec_cable_type_label_visibility_changed
            )
            values = panel.get_parameters()
            self.canvas._label_map[kid] = values.get("name", kid)
            self.canvas._elec_visible[kid] = values.get("visible", True)
            self.canvas._elec_cable_notes[kid] = values.get("comment", "")
            self.canvas.set_elec_cable_type_text(kid, values.get("type", ""))
            self.canvas.set_elec_cable_type_label_visible(
                kid,
                values.get("type_label_visible", False),
            )
            self.canvas.set_label_font_size(kid, values.get("label_size", 12.0))
            self.canvas.set_label_visible(kid, values.get("label_visible", True))
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
            panel.label_visibility_changed.connect(self._on_label_visibility_changed)
            values = panel.get_parameters()
            self.canvas._label_map[hid] = values.get("name", hid)
            self.canvas._hkv_visible[hid] = values.get("visible", True)
            self.canvas.set_label_font_size(hid, values.get("label_size", 12.0))
            self.canvas.set_label_visible(hid, values.get("label_visible", True))
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
            panel.label_visibility_changed.connect(self._on_label_visibility_changed)
            values = panel.get_parameters()
            self.canvas._label_map[lid] = values.get("name", lid)
            self.canvas._hkv_line_visible[lid] = values.get("visible", True)
            self.canvas.set_label_font_size(lid, values.get("label_size", 12.0))
            self.canvas.set_label_visible(lid, values.get("label_visible", True))
            self.canvas.set_color(lid, QColor(values.get("color", "#e53935")))
            length_px = self.canvas.get_hkv_line_length_px(lid)
            length_mm = length_px * self.canvas.get_mm_per_px()
            self.param_panel.set_hkv_line_length(lid, length_mm)
            self._update_hkv_line_labels(lid)

        for tid, panel in self.param_panel.text_panels.items():
            panel.place_requested.connect(self._on_place_text)
            panel.content_changed.connect(self._on_text_content_changed)
            panel.comment_changed.connect(self._on_text_comment_changed)
            panel.font_size_changed.connect(self._on_text_font_size_changed)
            panel.color_changed.connect(self._on_text_color_changed)
            panel.visibility_changed.connect(self._on_text_visibility_changed)
            panel.name_changed.connect(self._on_text_name_changed)
            values = panel.get_parameters()
            self.canvas._text_visible[tid] = values.get("visible", True)
            self.canvas._text_contents[tid] = values.get("content", "Text")
            self.canvas._text_font_sizes[tid] = values.get("font_size", 14.0)
            self.canvas._text_colors[tid] = values.get("color", "#ffffff")
            self.canvas._text_comments[tid] = values.get("comment", "")

        for cid in self.param_panel.circuit_panels:
            self._update_supply_hkv_label(cid)

        self._update_elec_point_room_assignments()

    def _connect_signals(self):
        if (
            getattr(self, "_signals_connected", False)
            and getattr(self, "_signals_canvas", None) is self.canvas
            and getattr(self, "_signals_panel", None) is self.param_panel
        ):
            return

        self.canvas.polygon_finished.connect(self._on_polygon_finished)
        self.canvas.elec_room_polygon_finished.connect(self._on_elec_room_polygon_finished)
        self.canvas.polygon_changed.connect(self._update_circuit_area)
        self.canvas.elec_room_polygon_changed.connect(self._on_elec_room_polygon_changed)
        self.canvas.floor_plan_polygon_finished.connect(
            self._on_floorplan_polygon_finished)
        self.canvas.mode_changed.connect(self._on_canvas_mode_changed)
        self.canvas.ref_line_set.connect(self._on_ref_line_drawn)
        self.canvas.start_point_moved.connect(self._on_start_point_moved)
        self.canvas.route_changed.connect(self._on_route_changed)
        self.canvas.supply_line_changed.connect(self._on_supply_line_changed)
        self.canvas.elec_point_placed.connect(self._on_elec_point_placed)
        self.canvas.elec_cable_changed.connect(self._on_elec_cable_changed)
        self.canvas.hkv_placed.connect(self._on_hkv_placed)
        self.canvas.hkv_line_changed.connect(self._on_hkv_line_changed)
        self.canvas.text_placed.connect(self._on_text_placed)
        self.canvas.object_clicked.connect(self._on_object_clicked)
        self.canvas.object_switched_from_edit.connect(self._on_object_switched_from_edit)
        self.canvas.object_double_clicked.connect(self._on_object_double_clicked)
        self.canvas.context_menu_requested.connect(self._show_canvas_context_menu)
        self.canvas.floor_plan_transform_updated.connect(
            self._on_floor_plan_transform_from_canvas)
        self.canvas.floor_plan_polygon_changed.connect(
            self._on_floor_plan_polygon_changed)

        # Treeview selection sync
        self.param_panel.item_selected.connect(self.canvas.set_selected_item)

        self.param_panel.delete_requested.connect(self._delete_circuit)
        self.param_panel.add_floorplan_requested.connect(self._add_floorplan)
        self.param_panel.delete_floorplan_requested.connect(self._delete_floorplan)
        self.param_panel.floorplan_file_browse.connect(self._browse_floorplan_file)
        self.param_panel.floorplan_polygon_draw.connect(
            self._on_floorplan_polygon_draw)
        self.param_panel.floorplan_polygon_color_changed.connect(
            self._on_floorplan_polygon_color_changed)
        self.param_panel.floorplan_ref_line.connect(self._on_floorplan_ref_line)
        self.param_panel.floorplan_ref_line_color_changed.connect(
            self._on_floorplan_ref_line_color_changed)
        self.param_panel.floorplan_ref_line_visibility_changed.connect(
            self._on_floorplan_ref_line_visibility_changed)
        self.param_panel.floorplan_helper_visibility_changed.connect(
            self._on_floorplan_helper_visibility_changed)
        self.param_panel.floorplan_helper_color_changed.connect(
            self._on_floorplan_helper_color_changed)
        self.param_panel.floorplan_helper_length_changed.connect(
            self._on_floorplan_helper_length_changed)
        self.param_panel.floorplan_helper_width_changed.connect(
            self._on_floorplan_helper_width_changed)
        self.param_panel.floorplan_helper_style_changed.connect(
            self._on_floorplan_helper_style_changed)
        self.param_panel.floorplan_helper_draw_requested.connect(
            self._on_floorplan_helper_draw_requested)
        self.param_panel.floorplan_helper_edit_requested.connect(
            self._on_floorplan_helper_edit_requested)
        self.param_panel.floorplan_helper_clear_requested.connect(
            self._on_floorplan_helper_clear_requested)
        self.param_panel.floorplan_ref_confirmed.connect(self._on_floorplan_ref_confirmed)
        self.param_panel.floorplan_transform_changed.connect(self._on_floorplan_transform)
        self.param_panel.floorplan_opacity_changed.connect(self._on_floorplan_opacity)
        self.param_panel.floorplan_visibility_changed.connect(self._on_floorplan_visibility)
        self.param_panel.floorplan_move_requested.connect(self._on_floorplan_move)
        self.param_panel.floorplan_rotate_requested.connect(self._on_floorplan_rotate)
        self.param_panel.floorplan_order_changed.connect(self._on_floorplan_order_changed)
        self.param_panel.add_circuit_requested.connect(self._add_circuit)
        self.param_panel.add_elec_point_requested.connect(self._add_elec_point)
        self.param_panel.add_elec_room_requested.connect(self._add_elec_room)
        self.param_panel.add_elec_cable_requested.connect(self._add_elec_cable)
        self.param_panel.delete_elec_point_requested.connect(self._delete_elec_point)
        self.param_panel.delete_elec_room_requested.connect(self._delete_elec_room)
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
        self.param_panel.add_text_requested.connect(self._add_text)
        self.param_panel.delete_text_requested.connect(self._delete_text)
        self.param_panel.heating_global_changed.connect(self._recalc_all_circuits)
        self.param_panel.bom_metadata_changed.connect(self._mark_dirty)

        # Dirty-tracking: jede inhaltliche Änderung markiert als unsaved
        self.canvas.polygon_finished.connect(self._mark_dirty)

        self._signals_connected = True
        self._signals_canvas = self.canvas
        self._signals_panel = self.param_panel
        self.canvas.polygon_changed.connect(self._mark_dirty)
        self.canvas.elec_room_polygon_finished.connect(self._mark_dirty)
        self.canvas.elec_room_polygon_changed.connect(self._mark_dirty)
        self.canvas.floor_plan_polygon_finished.connect(self._mark_dirty)
        self.canvas.route_changed.connect(self._mark_dirty)
        self.canvas.supply_line_changed.connect(self._mark_dirty)
        self.canvas.elec_point_placed.connect(self._mark_dirty)
        self.canvas.elec_cable_changed.connect(self._mark_dirty)
        self.canvas.hkv_placed.connect(self._mark_dirty)
        self.canvas.hkv_line_changed.connect(self._mark_dirty)
        self.canvas.text_placed.connect(self._mark_dirty)
        self.canvas.helper_lines_changed.connect(self._mark_dirty)
        self.canvas.ref_line_set.connect(self._mark_dirty)
        self.canvas.start_point_moved.connect(self._mark_dirty)
        self.canvas.label_moved.connect(self._mark_dirty)
        self.canvas.measure_changed.connect(self._mark_dirty)
        self.canvas.export_frame_drawn.connect(self._mark_dirty)
        self.canvas.multi_selection_changed.connect(self._on_multi_selection_changed)
        self.canvas.multi_objects_moved.connect(self._mark_dirty)
        self.canvas.multi_objects_moved.connect(self._update_elec_point_room_assignments)
        self.param_panel.heating_global_changed.connect(self._mark_dirty)
        self._main_splitter.splitterMoved.connect(self._mark_dirty_debounced)

        # Elektro-Strangschema live aktualisieren
        self.canvas.elec_point_placed.connect(self._refresh_elec_schema_window)
        self.canvas.elec_cable_changed.connect(self._refresh_elec_schema_window)

        # Schaltplan live aktualisieren
        self.canvas.elec_point_placed.connect(self._refresh_schaltplan_window)
        self.canvas.elec_cable_changed.connect(self._refresh_schaltplan_window)

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

    def _on_angle_measure_toggled(self, checked: bool):
        if checked:
            self.canvas.start_angle_measure()
            self._measure_btn.blockSignals(True)
            self._measure_btn.setChecked(False)
            self._measure_btn.blockSignals(False)
            self.status.showMessage(
                "∠ Winkel messen – 3x Linksklick (P1, Scheitel, P3), Rechtsklick: zurück/abbrechen"
            )
        else:
            from gui.canvas_widget import ToolMode
            if self.canvas._mode == ToolMode.MEASURE_ANGLE:
                self.canvas._mode = ToolMode.NONE
                self.canvas._angle_measure_p1 = None
                self.canvas._angle_measure_p2 = None
                self.canvas._angle_measure_p3 = None
                self.canvas.setCursor(Qt.ArrowCursor)
                self.canvas.mode_changed.emit()
                self.canvas.update()

    def _on_clear_angle_measurements(self):
        self.canvas.clear_angle_measurements()
        self.status.showMessage("Winkelmessungen gelöscht", 2000)

    def _on_helper_length_changed(self, meters: float):
        fp_id = self.param_panel.get_active_floorplan_id()
        self.canvas.set_helper_line_target_length_mm(meters * 1000.0, fp_id, resize_selected=True)
        panel = self.param_panel.floorplan_panels.get(fp_id or "")
        if panel:
            panel.sb_helper_length.blockSignals(True)
            panel.sb_helper_length.setValue(meters)
            panel.sb_helper_length.blockSignals(False)
        self._mark_dirty()

    def _on_helper_draw_toggled(self, checked: bool):
        if checked:
            fp_id = self.param_panel.get_active_floorplan_id()
            self.canvas.start_draw_helper_line(fp_id)
            self._helper_edit_btn.blockSignals(True)
            self._helper_edit_btn.setChecked(False)
            self._helper_edit_btn.blockSignals(False)
            self._measure_btn.blockSignals(True)
            self._measure_btn.setChecked(False)
            self._measure_btn.blockSignals(False)
            self._angle_measure_btn.blockSignals(True)
            self._angle_measure_btn.setChecked(False)
            self._angle_measure_btn.blockSignals(False)
            self.status.showMessage(
                "📐 Hilfslinie – Linksklick Start, Linksklick Richtung (Länge fix aus Feld), Rechtsklick: Abbruch"
            )
        else:
            from gui.canvas_widget import ToolMode
            if self.canvas._mode == ToolMode.DRAW_HELPER_LINE:
                self.canvas._mode = ToolMode.NONE
                self.canvas._helper_draw_start = None
                self.canvas._helper_draw_current = None
                self.canvas.setCursor(Qt.ArrowCursor)
                self.canvas.mode_changed.emit()
                self.canvas.update()

    def _on_helper_edit_toggled(self, checked: bool):
        if checked:
            fp_id = self.param_panel.get_active_floorplan_id()
            self.canvas.start_edit_helper_lines(fp_id)
            self._helper_draw_btn.blockSignals(True)
            self._helper_draw_btn.setChecked(False)
            self._helper_draw_btn.blockSignals(False)
            self.status.showMessage(
                "✥ Hilfslinien bearbeiten – Linksklick wählen/ziehen, Rechtsklick löschen, Entf löscht Auswahl"
            )
        else:
            from gui.canvas_widget import ToolMode
            if self.canvas._mode == ToolMode.EDIT_HELPER_LINE:
                self.canvas._mode = ToolMode.NONE
                self.canvas._helper_dragging_endpoint = None
                self.canvas._helper_dragging_whole_id = None
                self.canvas._helper_drag_start = None
                self.canvas._helper_drag_origin = []
                self.canvas.setCursor(Qt.ArrowCursor)
                self.canvas.mode_changed.emit()
                self.canvas.update()

    def _on_clear_helper_lines(self):
        fp_id = self.param_panel.get_active_floorplan_id()
        self.canvas.clear_helper_lines(fp_id)
        self.status.showMessage("Hilfslinien gelöscht", 2000)

    def _on_helper_color_pick(self):
        fp_id = self.param_panel.get_active_floorplan_id()
        color = QColorDialog.getColor(
            QColor(self.canvas.get_helper_line_color(fp_id)),
            self,
            "Hilfslinien-Farbe wählen",
        )
        if color.isValid():
            hex_color = color.name()
            self.canvas.set_helper_line_color(hex_color, fp_id)
            self._helper_color_btn.setStyleSheet(f"background:{hex_color};")
            panel = self.param_panel.floorplan_panels.get(fp_id or "")
            if panel:
                panel.btn_helper_color.setStyleSheet(f"background:{hex_color};")
            self._mark_dirty()

    def _on_measure_color_pick(self):
        """Open colour picker for measurement lines."""
        color = QColorDialog.getColor(
            QColor(self.canvas.get_measure_color()),
            self, "Messlinie-Farbe wählen"
        )
        if color.isValid():
            hex_color = color.name()
            self.canvas.set_measure_color(hex_color)
            self._measure_color_btn.setStyleSheet(f"background:{hex_color};")
            self._mark_dirty()

    def _on_export_frame_toggled(self, checked: bool):
        if checked:
            self.canvas.start_draw_export_frame()
            self.status.showMessage(
                "⬚ Export-Rahmen – Linksklick ziehen, Rechtsklick löschen, ESC abbrechen"
            )
        else:
            from gui.canvas_widget import ToolMode
            if self.canvas._mode == ToolMode.DRAW_EXPORT_FRAME:
                self.canvas._mode = ToolMode.NONE
                self.canvas._export_frame_start = None
                self.canvas._export_frame_current = None
                self.canvas.setCursor(Qt.ArrowCursor)
                self.canvas.mode_changed.emit()
                self.canvas.update()
            self.status.clearMessage()

    def _on_clear_export_frame(self):
        self.canvas.clear_export_frame()
        self.status.showMessage("Export-Rahmen gelöscht", 2000)

    def _on_canvas_mode_changed(self):
        from gui.canvas_widget import ToolMode
        if self.canvas._mode != ToolMode.MEASURE:
            self._measure_btn.blockSignals(True)
            self._measure_btn.setChecked(False)
            self._measure_btn.blockSignals(False)
        if self.canvas._mode != ToolMode.MEASURE_ANGLE:
            self._angle_measure_btn.blockSignals(True)
            self._angle_measure_btn.setChecked(False)
            self._angle_measure_btn.blockSignals(False)
        if self.canvas._mode != ToolMode.DRAW_HELPER_LINE:
            self._helper_draw_btn.blockSignals(True)
            self._helper_draw_btn.setChecked(False)
            self._helper_draw_btn.blockSignals(False)
        if self.canvas._mode != ToolMode.EDIT_HELPER_LINE:
            self._helper_edit_btn.blockSignals(True)
            self._helper_edit_btn.setChecked(False)
            self._helper_edit_btn.blockSignals(False)
        if self.canvas._mode != ToolMode.DRAW_EXPORT_FRAME:
            self._export_frame_btn.blockSignals(True)
            self._export_frame_btn.setChecked(False)
            self._export_frame_btn.blockSignals(False)

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

        # Measure color
        measure_color = QColor(c.get_measure_color())
        self._measure_color_btn.setStyleSheet(f"background:{measure_color.name()};")
        fp_id = self.param_panel.get_active_floorplan_id()
        c.set_active_helper_floor(fp_id)
        helper_color = QColor(c.get_helper_line_color(fp_id))
        self._helper_color_btn.setStyleSheet(f"background:{helper_color.name()};")
        self._helper_len_spin.blockSignals(True)
        self._helper_len_spin.setValue(max(0.01, c.get_helper_line_target_length_mm(fp_id) / 1000.0))
        self._helper_len_spin.blockSignals(False)

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

    def _delete_floorplan(self, fp_id: str):
        self.canvas.remove_floor_plan(fp_id)
        self.param_panel.remove_floorplan_panel(fp_id)
        self._mark_dirty()

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
        self.status.showMessage(
            f"{fur_id}: Bild laden (\U0001f4c2) \u2192 Referenzlinie zeichnen \u2192 Positionieren"
        )

    def _delete_furniture(self, fur_id: str):
        self.canvas.remove_floor_plan(fur_id)
        self.param_panel.remove_furniture_panel(fur_id)
        self._mark_dirty()
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
        layer = self.canvas._floor_plans.get(fp_id)
        if panel and layer and layer.ref_p1 and layer.ref_p2 and layer.mm_per_px > 0:
            panel.update_scale_label(layer.mm_per_px)
        # If this is the first floor plan, set legacy svg_path + fit window
        if not self._svg_path:
            self._svg_path = path
            self._fit_window_to_svg()
        self._mark_dirty()

    def _on_floorplan_polygon_draw(self, fp_id: str):
        panel = self.param_panel.furniture_panels.get(fp_id)
        if panel:
            p = panel.get_parameters()
            self.canvas.set_floor_plan_polygon_color(
                fp_id, p.get("polygon_color", "#8d99ae")
            )
        self.canvas.start_draw_floor_plan_polygon(fp_id)
        self.status.showMessage(
            f"{fp_id}: Einrichtungs-Polygon zeichnen  |  "
            "Linksklick = Punkt  |  Enter/Doppelklick = Fertig  |  ESC = Abbruch  |  Rechtsklick = Kontextmenü"
        )

    def _on_floorplan_polygon_finished(self, fp_id: str, points: list):
        panel = self.param_panel.furniture_panels.get(fp_id)
        if panel:
            panel.set_polygon_source()
            p = panel.get_parameters()
            self.canvas.set_floor_plan_polygon_color(
                fp_id, p.get("polygon_color", "#8d99ae")
            )
        self.status.showMessage(
            f"✅ Einrichtung {fp_id}: Polygon erstellt ({len(points)} Punkte)."
        )

    def _on_floorplan_polygon_color_changed(self, fp_id: str, color: str):
        self.canvas.set_floor_plan_polygon_color(fp_id, color)
        self._mark_dirty()

    def _on_floorplan_ref_line(self, fp_id: str):
        self.canvas.start_ref_line_for_floor(fp_id)

    def _on_floorplan_ref_line_color_changed(self, fp_id: str, color: str):
        """Update reference line color in canvas."""
        self.canvas.set_ref_line_color(fp_id, color)
        self._mark_dirty()

    def _on_floorplan_ref_line_visibility_changed(self, fp_id: str, visible: bool):
        """Update reference line visibility in canvas."""
        self.canvas.set_ref_line_visible(fp_id, visible)
        self._mark_dirty()

    def _on_floorplan_helper_visibility_changed(self, fp_id: str, visible: bool):
        self.canvas.set_helper_line_visible(fp_id, visible)
        self._sync_toolbar_from_canvas()
        self._mark_dirty()

    def _on_floorplan_helper_color_changed(self, fp_id: str, color: str):
        self.canvas.set_helper_line_color(color, fp_id)
        self._sync_toolbar_from_canvas()
        self._mark_dirty()

    def _on_floorplan_helper_length_changed(self, fp_id: str, length_mm: float):
        self.canvas.set_helper_line_target_length_mm(length_mm, fp_id, resize_selected=True)
        self._sync_toolbar_from_canvas()
        self._mark_dirty()

    def _on_floorplan_helper_width_changed(self, fp_id: str, width_px: float):
        self.canvas.set_helper_line_width(fp_id, width_px)
        self._mark_dirty()

    def _on_floorplan_helper_style_changed(self, fp_id: str, style_key: str):
        self.canvas.set_helper_line_style(fp_id, style_key)
        self._mark_dirty()

    def _on_floorplan_helper_draw_requested(self, fp_id: str):
        self.canvas.set_active_helper_floor(fp_id)
        self._on_helper_draw_toggled(True)

    def _on_floorplan_helper_edit_requested(self, fp_id: str):
        self.canvas.set_active_helper_floor(fp_id)
        self._on_helper_edit_toggled(True)

    def _on_floorplan_helper_clear_requested(self, fp_id: str):
        self.canvas.clear_helper_lines(fp_id)
        self.status.showMessage("Hilfslinien dieses Grundrisses gelöscht", 2000)
        self._mark_dirty()

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
        self.canvas.rescale_all_layer_ref_points(old_global, new_global, skip_fp_id=fp_id)
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
        self._sync_toolbar_from_canvas()

    def _on_floorplan_move(self, fp_id: str):
        self.canvas.set_active_helper_floor(fp_id)
        self.canvas.start_move_floor_plan(fp_id)
        self.status.showMessage(
            f"\u2725 Grundriss verschieben \u2013 Ziehen mit linker Maustaste, ESC zum Beenden")

    def _on_floorplan_rotate(self, fp_id: str):
        self.canvas.set_active_helper_floor(fp_id)
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

    def _on_floor_plan_polygon_changed(self, _fp_id: str):
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
            "Linksklick = Punkt  |  Enter/Doppelklick = Fertig  |  ESC = Abbruch  |  Rechtsklick = Kontextmenü"
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
        panel.label_visibility_changed.connect(self._on_label_visibility_changed)
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
            "Linksklick = Punkt  |  Enter/Doppelklick = Fertig  |  ESC = Abbruch  |  Rechtsklick = Kontextmenü"
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
            "Linksklick = Punkt  |  Enter/Doppelklick = Fertig  |  ESC = Abbruch  |  Rechtsklick = Kontextmenü"
        )

    def _on_edit_supply_requested(self, circuit_id: str):
        self.canvas.start_edit_supply_line(circuit_id)
        self.status.showMessage(
            f"Zuleitung bearbeiten: Linksklick=Verschieben, Doppelklick auf Punkt=Löschen, "
            f"Doppelklick auf Kante=Einfügen, Mitteltaste/ESC=Beenden, Rechtsklick=Kontextmenü."
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
        elif obj_type == "floor_polygon":
            self._on_edit_floor_polygon_requested(obj_id)
        elif obj_type == "polygon":
            self._on_edit_polygon_requested(obj_id)
        elif obj_type == "elec_room":
            self._on_edit_elec_room(obj_id)

    def _on_object_clicked(self, obj_type: str, obj_id: str):
        """Handle single-click on canvas object – select in sidebar."""
        self.param_panel.select_item(obj_id)

    def _show_canvas_context_menu(self, obj_type: str, obj_id: str,
                                  canvas_pt, global_pos):
        if obj_id and obj_type not in {"polygon", "elec_room", "helper_line"}:
            self.param_panel.select_item(obj_id)

        selected_id = self.param_panel.get_selected_item_id()
        selected_type = self._selected_object_type(selected_id) if selected_id else None
        context_type = obj_type or selected_type
        context_id = obj_id or selected_id

        menu = QMenu(self)

        placing_mode = self.canvas._mode in {
            ToolMode.PLACE_ELEC_POINT,
            ToolMode.PLACE_HKV,
            ToolMode.PLACE_TEXT,
        }
        if placing_mode:
            cancel_place_action = menu.addAction("✕ Platzierung abbrechen")
            cancel_place_action.triggered.connect(
                lambda checked=False: self.canvas.cancel_active_placement()
            )
            menu.addSeparator()

        # Multi-selection context menu
        if self.canvas._multi_selected:
            multi_count = len(self.canvas._multi_selected)
            move_multi_action = menu.addAction(f"✥ {multi_count} Objekte verschieben")
            move_multi_action.triggered.connect(self._show_move_multi_hint)
            menu.addSeparator()

        info_lines = self._context_info_lines(context_type, context_id)
        if info_lines:
            for line in info_lines:
                info_action = menu.addAction(line)
                info_action.setEnabled(False)
            menu.addSeparator()

        # ── Helper Line spezifische Menüeinträge ──
        if obj_type == "helper_line" and obj_id:
            hid = obj_id
            fid = self.canvas._helper_selected_floor_id
            
            edit_length_action = menu.addAction("✏️ Länge ändern…")
            edit_length_action.triggered.connect(
                lambda checked=False, hid_=hid, fid_=fid: self._context_edit_helper_line_length(hid_, fid_)
            )
            
            is_fixed = self.canvas._floor_helper_line_fixed.get(fid, {}).get(hid, False)
            fixed_text = "✓ Länge fixiert" if is_fixed else "☐ Länge fixieren"
            toggle_fixed_action = menu.addAction(fixed_text)
            toggle_fixed_action.triggered.connect(
                lambda checked=False, hid_=hid, fid_=fid: self._context_toggle_helper_line_fixed(hid_, fid_)
            )
            
            delete_action = menu.addAction("🗑️ Löschen")
            delete_action.triggered.connect(
                lambda checked=False, hid_=hid, fid_=fid: self._context_delete_helper_line(hid_, fid_)
            )
            
            menu.addSeparator()

        elif selected_id:
            copy_action = menu.addAction("📋 Kopieren")
            copy_action.triggered.connect(self._copy_selected_object)

            duplicate_action = menu.addAction("📄 Duplizieren")
            duplicate_action.triggered.connect(self._duplicate_selected_object_via_clipboard)

            delete_action = menu.addAction("🗑️ Löschen")
            delete_action.triggered.connect(self._delete_selected_object)

            if context_type in {"elec_point", "hkv", "text", "elec_cable"}:
                move_hint = menu.addAction("✥ Verschieben")
                move_hint.triggered.connect(
                    lambda checked=False, t=context_type: self._show_move_hint(t)
                )

            if selected_type in {"floorplan", "furniture"}:
                submenu = menu.addMenu("✥ Bearbeiten")
                move_action = submenu.addAction("Verschieben")
                move_action.triggered.connect(
                    lambda checked=False, item_id=selected_id: self._on_floorplan_move(item_id)
                )
                rotate_action = submenu.addAction("Drehen")
                rotate_action.triggered.connect(
                    lambda checked=False, item_id=selected_id: self._on_floorplan_rotate(item_id)
                )

            edit_action = self._context_edit_action(context_type, context_id)
            if edit_action is not None:
                menu.addSeparator()
                menu.addAction(edit_action)

            if context_type in {
                "elec_cable", "hkv_line", "supply_line", "route",
                "polygon", "elec_room", "floor_polygon",
            }:
                add_point_action = menu.addAction("➕ Punkt hinzufügen")
                add_point_action.triggered.connect(
                    lambda checked=False, t=context_type, i=context_id, p=QPointF(canvas_pt): self._context_insert_point(t, i, p)
                )
                delete_point_action = menu.addAction("➖ Punkt löschen")
                delete_point_action.triggered.connect(
                    lambda checked=False, t=context_type, i=context_id, p=QPointF(canvas_pt): self._context_delete_point(t, i, p)
                )

            if context_type == "elec_point" and context_id:
                menu.addSeparator()
                edit_device_action = menu.addAction("✏️ Unterputz-Gerät bearbeiten")
                edit_device_action.triggered.connect(
                    lambda checked=False, pid=context_id: self._context_edit_ap_device(pid)
                )
                edit_device_color_action = menu.addAction("🎨 Gerätefarbe bearbeiten")
                edit_device_color_action.triggered.connect(
                    lambda checked=False, pid=context_id: self._context_edit_ap_device_color(pid)
                )
                edit_note_action = menu.addAction("📝 Notiz bearbeiten")
                edit_note_action.triggered.connect(
                    lambda checked=False, pid=context_id: self._context_edit_ap_note(pid)
                )

        if not (obj_type == "helper_line"):
            menu.addSeparator()
            tools_menu = menu.addMenu("🧰 Werkzeuge")
            tools_menu.addAction("📏 Abstand messen", self._context_activate_measure)
            tools_menu.addAction("∠ Winkel messen", self._context_activate_angle_measure)
            tools_menu.addSeparator()
            tools_menu.addAction("📐 Hilfslinie zeichnen", self._context_activate_helper_draw)
            tools_menu.addAction("✥ Hilfslinien bearbeiten", self._context_activate_helper_edit)
            tools_menu.addAction("✕ Hilfslinien löschen", self._on_clear_helper_lines)
            tools_menu.addSeparator()
            tools_menu.addAction("⬚ Export-Rahmen setzen", self._context_activate_export_frame)
            tools_menu.addAction("✕ Export-Rahmen löschen", self._on_clear_export_frame)
        else:
            menu.addSeparator()
            tools_menu = menu.addMenu("🧰 Werkzeuge")
            tools_menu.addAction("✕ Hilfslinien löschen", self._on_clear_helper_lines)

        if self._copy_buffer:
            if selected_id:
                menu.addSeparator()
            paste_action = menu.addAction("📥 Einfügen")
            paste_action.triggered.connect(self._paste_copied_object)

        if menu.isEmpty():
            return

        pos = global_pos.toPoint() if hasattr(global_pos, "toPoint") else global_pos
        menu.exec(pos)

    def _context_activate_measure(self):
        self._measure_btn.setChecked(True)
        self._on_measure_toggled(True)

    def _context_activate_angle_measure(self):
        self._angle_measure_btn.setChecked(True)
        self._on_angle_measure_toggled(True)

    def _context_activate_helper_draw(self):
        self.canvas.set_active_helper_floor(self.param_panel.get_active_floorplan_id())
        self._helper_draw_btn.setChecked(True)
        self._on_helper_draw_toggled(True)

    def _context_activate_helper_edit(self):
        self.canvas.set_active_helper_floor(self.param_panel.get_active_floorplan_id())
        self._helper_edit_btn.setChecked(True)
        self._on_helper_edit_toggled(True)

    def _context_activate_export_frame(self):
        self._export_frame_btn.setChecked(True)
        self._on_export_frame_toggled(True)

    def _context_edit_helper_line_length(self, helper_id: str, floor_id: str | None):
        fid = floor_id or self.canvas._helper_selected_floor_id
        if not fid or not helper_id:
            return
        current_mm = self.canvas.get_helper_line_length_mm(fid, helper_id)
        current_m = max(0.001, float(current_mm) / 1000.0)
        value_m, ok = QInputDialog.getDouble(
            self,
            "Hilfslinie: Länge ändern",
            "Länge (m):",
            current_m,
            0.001,
            1000.0,
            3,
        )
        if not ok:
            return
        self.canvas.set_helper_line_length_mm(fid, helper_id, value_m * 1000.0)
        self._mark_dirty()

    def _context_toggle_helper_line_fixed(self, helper_id: str, floor_id: str | None):
        fid = floor_id or self.canvas._helper_selected_floor_id
        if not fid or not helper_id:
            return
        cur = self.canvas.is_helper_line_length_fixed(fid, helper_id)
        self.canvas.set_helper_line_length_fixed(fid, helper_id, not cur)
        self._mark_dirty()

    def _context_delete_helper_line(self, helper_id: str, floor_id: str | None):
        fid = floor_id or self.canvas._helper_selected_floor_id
        if not fid or not helper_id:
            return
        self.canvas.delete_helper_line(fid, helper_id)
        self._mark_dirty()

    def _context_info_lines(self, context_type: str | None, context_id: str | None) -> list[str]:
        if not context_type or not context_id:
            return []

        lines: list[str] = []
        if context_type == "elec_point":
            panel = self.param_panel.elec_point_panels.get(context_id)
            if not panel:
                return []
            params = panel.get_parameters()
            device = str(params.get("smarthome_device", "") or "").strip()
            device_color = str(params.get("smarthome_device_color", "") or "").strip()
            note = str(params.get("note", "") or "").strip()
            if device:
                lines.append(f"ℹ Unterputz-Gerät: {device}")
            if device_color:
                lines.append(f"🎨 Gerätefarbe: {device_color}")
            if note:
                lines.append(f"📝 Notiz: {note}")
            return lines

        if context_type == "elec_cable":
            panel = self.param_panel.elec_cable_panels.get(context_id)
            if not panel:
                return []
            params = panel.get_parameters()
            cable_type = str(params.get("type", "") or "").strip()
            note = str(params.get("comment", "") or "").strip()
            if cable_type:
                lines.append(f"ℹ Typ: {cable_type}")
            if note:
                lines.append(f"📝 Notiz: {note}")
            return lines

        return []

    def _context_edit_ap_device(self, point_id: str):
        panel = self.param_panel.elec_point_panels.get(point_id)
        if not panel:
            return
        current = panel.get_smarthome_device_text().strip()
        choices = [""]
        for p in self.param_panel.elec_point_panels.values():
            value = p.get_smarthome_device_text().strip()
            if value and value not in choices:
                choices.append(value)
        if current and current not in choices:
            choices.append(current)
        value, ok = QInputDialog.getItem(
            self,
            "Unterputz-Gerät bearbeiten",
            "Gerät:",
            choices,
            choices.index(current) if current in choices else 0,
            True,
        )
        if not ok:
            return
        panel.set_smarthome_device_text(value)
        self.canvas._elec_point_smarthome_device[point_id] = value
        self._mark_dirty_debounced()

    def _context_edit_ap_device_color(self, point_id: str):
        panel = self.param_panel.elec_point_panels.get(point_id)
        if not panel:
            return
        current = panel.get_smarthome_device_color_text().strip()
        choices = ["", "weiß", "schwarz"]
        for p in self.param_panel.elec_point_panels.values():
            value = p.get_smarthome_device_color_text().strip()
            if value and value not in choices:
                choices.append(value)
        if current and current not in choices:
            choices.append(current)
        value, ok = QInputDialog.getItem(
            self,
            "Gerätefarbe bearbeiten",
            "Farbe:",
            choices,
            choices.index(current) if current in choices else 0,
            True,
        )
        if not ok:
            return
        panel.set_smarthome_device_color_text(value)
        self.canvas._elec_point_smarthome_device_color[point_id] = value
        self._mark_dirty_debounced()

    def _context_edit_ap_note(self, point_id: str):
        panel = self.param_panel.elec_point_panels.get(point_id)
        if not panel:
            return
        current = panel.get_parameters().get("note", "")
        value, ok = QInputDialog.getMultiLineText(
            self,
            "AP-Notiz bearbeiten",
            "Notiz:",
            current,
        )
        if not ok:
            return
        panel.te_note.setPlainText(value)
        self.canvas._elec_point_notes[point_id] = value
        self._mark_dirty_debounced()

    def _context_edit_action(self, selected_type: str | None, selected_id: str):
        if not selected_type or not selected_id:
            return None
        if selected_type == "elec_cable":
            return QAction("✏️ Kabel bearbeiten", self, triggered=lambda: self._on_edit_elec_cable(selected_id))
        if selected_type == "hkv_line":
            return QAction("✂️ HKV-Leitung bearbeiten", self, triggered=lambda: self._on_edit_hkv_line(selected_id))
        if selected_type == "supply_line":
            return QAction("✂️ Zuleitung bearbeiten", self, triggered=lambda: self._on_edit_supply_requested(selected_id))
        if selected_type == "route":
            return QAction("✂️ Rohrverlauf bearbeiten", self, triggered=lambda: self._on_edit_route_requested(selected_id))
        if selected_type == "floor_polygon":
            return QAction("✏️ Einrichtungs-Polygon bearbeiten", self, triggered=lambda: self._on_edit_floor_polygon_requested(selected_id))
        if selected_type == "polygon":
            return QAction("✏️ Polygon bearbeiten", self, triggered=lambda: self._on_edit_polygon_requested(selected_id))
        if selected_type == "elec_room":
            return QAction("✏️ Raum-Polygon bearbeiten", self, triggered=lambda: self._on_edit_elec_room(selected_id))
        return None

    def _show_move_hint(self, obj_type: str):
        labels = {
            "elec_point": "Anschlusspunkt",
            "hkv": "HKV",
            "text": "Text",
            "elec_cable": "Kabel",
        }
        label = labels.get(obj_type, "Objekt")
        self.status.showMessage(
            f"{label} verschieben: Mit linker Maustaste im Zeichenfeld ziehen.",
            3500,
        )

    def _show_move_multi_hint(self):
        count = len(self.canvas._multi_selected)
        self.status.showMessage(
            f"Verschieben von {count} Objekten: Alt + Linke Maustaste halten + bewegen",
            3500,
        )

    def _on_multi_selection_changed(self, selected_set):
        count = len(selected_set)
        if count == 0:
            self.status.showMessage("")
        else:
            self.status.showMessage(
                f"{count} Objekt(e) selektiert. Alt+Drag zum Verschieben oder Rechtsklick für Menü"
            )

    def _context_insert_point(self, obj_type: str, obj_id: str, canvas_pt: QPointF):
        if not obj_type or not obj_id:
            return
        if self.canvas.context_insert_point(obj_type, obj_id, canvas_pt):
            self._mark_dirty()
            self.status.showMessage("Punkt hinzugefügt.", 2000)
        else:
            self.status.showMessage("Keine passende Kante für neuen Punkt gefunden.", 2500)

    def _context_delete_point(self, obj_type: str, obj_id: str, canvas_pt: QPointF):
        if not obj_type or not obj_id:
            return
        if self.canvas.context_delete_point(obj_type, obj_id, canvas_pt):
            self._mark_dirty()
            self.status.showMessage("Punkt gelöscht.", 2000)
        else:
            self.status.showMessage("Kein löschbarer Punkt an dieser Stelle.", 2500)

    def _duplicate_selected_object_via_clipboard(self):
        item_id = self.param_panel.get_selected_item_id()
        if not item_id:
            return
        self._copy_selected_object()
        self._paste_copied_object()

    def _on_object_switched_from_edit(self, obj_type: str, obj_id: str):
        """Show feedback when edit mode is exited by clicking another object."""
        labels = {
            "elec_point": "Anschlusspunkt",
            "elec_room": "Raum",
            "hkv": "HKV",
            "elec_cable": "Kabel",
            "hkv_line": "HKV-Leitung",
            "supply_line": "Zuleitung",
            "route": "Rohrverlauf",
            "floor_polygon": "Einrichtung",
            "polygon": "Polygon",
        }
        label = labels.get(obj_type, "Objekt")
        self.status.showMessage(
            f"Bearbeitungsmodus beendet — {label} '{obj_id}' ausgewählt.",
            3000,
        )

    def _on_edit_floor_polygon_requested(self, fp_id: str):
        self.canvas.start_edit_floor_plan_polygon(fp_id)
        self.status.showMessage(
            "Einrichtungs-Polygon bearbeiten: Linksklick zum Verschieben, "
            "Doppelklick auf Punkt zum Löschen, Doppelklick auf Kante zum Einfügen, "
            "Mitteltaste oder ESC zum Beenden, Rechtsklick=Kontextmenü."
        )

    def _on_edit_polygon_requested(self, circuit_id: str):
        self.canvas.start_edit_polygon(circuit_id)
        self.status.showMessage(
            f"Polygon bearbeiten: Linksklick zum Verschieben, Doppelklick auf Punkt zum Löschen, "
            f"Doppelklick auf Kante zum Einfügen, Mitteltaste oder ESC zum Beenden, Rechtsklick=Kontextmenü."
        )

    def _on_edit_route_requested(self, circuit_id: str):
        self.canvas.start_edit_route(circuit_id)
        self.status.showMessage(
            f"Rohrverlauf bearbeiten: Linksklick zum Verschieben, Doppelklick auf Punkt zum Löschen, "
            f"Doppelklick auf Kante zum Einfügen, Mitteltaste oder ESC zum Beenden, Rechtsklick=Kontextmenü."
        )

    def _on_circuit_name_changed(self, circuit_id: str, name: str):
        self.canvas.set_polygon_name(circuit_id, name)

    def _on_circuit_color_changed(self, circuit_id: str, color: str):
        self.canvas.set_color(circuit_id, QColor(color))

    def _on_label_size_changed(self, item_id: str, size: float):
        self.canvas.set_label_font_size(item_id, size)
        if item_id in self.param_panel.elec_point_panels or item_id in self.param_panel.elec_cable_panels:
            self._refresh_elec_schema_window()

    def _on_label_visibility_changed(self, item_id: str, visible: bool):
        self.canvas.set_label_visible(item_id, visible)
        if item_id in self.param_panel.elec_point_panels or item_id in self.param_panel.elec_cable_panels:
            self._refresh_elec_schema_window()

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

    def _compute_polygon_perimeter_mm(self, circuit_id: str) -> float | None:
        px_points = self.canvas.get_polygon_px(circuit_id)
        if len(px_points) < 3:
            return None
        perimeter_px = 0.0
        n = len(px_points)
        for i in range(n):
            x1, y1 = px_points[i]
            x2, y2 = px_points[(i + 1) % n]
            perimeter_px += math.hypot(x2 - x1, y2 - y1)
        scale = self.canvas.get_mm_per_px()
        return perimeter_px * scale

    def _update_circuit_area(self, circuit_id: str):
        area_mm2 = self._compute_polygon_area_mm2(circuit_id)
        if area_mm2 is not None:
            self.param_panel.set_circuit_area(circuit_id, area_mm2)
        perimeter_mm = self._compute_polygon_perimeter_mm(circuit_id)
        if perimeter_mm is not None:
            self.param_panel.set_circuit_perimeter(circuit_id, perimeter_mm)
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
        panel.label_visibility_changed.connect(self._on_label_visibility_changed)
        panel.position_changed.connect(self._on_elec_point_position_changed)
        panel.height_changed.connect(self._on_elec_point_height_changed)
        panel.note_changed.connect(self._on_elec_point_note_changed)
        panel.smarthome_device_changed.connect(self._on_elec_point_smarthome_changed)
        panel.smarthome_device_color_changed.connect(self._on_elec_point_smarthome_color_changed)
        panel.ap_type_changed.connect(self._on_elec_point_ap_type_changed)
        panel.uv_config_changed.connect(self._on_elec_point_uv_config_changed)
        panel.up_distribution_changed.connect(self._on_elec_point_up_distribution_changed)
        self._update_up_distribution_cable_choices_for_point(point_id)
        return panel

    def _update_up_distribution_cable_choices_for_point(self, point_id: str):
        panel = self.param_panel.elec_point_panels.get(point_id)
        if not panel:
            return
        connected: list[dict] = []
        for cable_id, cable_panel in self.param_panel.elec_cable_panels.items():
            start_ap_id, end_ap_id = self.canvas.get_cable_ap(cable_id)
            if point_id not in (start_ap_id, end_ap_id):
                continue
            cable_params = cable_panel.get_parameters()
            cable_name = str(cable_params.get("name", cable_id) or "").strip() or cable_id
            connected.append({
                "cable_id": cable_id,
                "name": cable_name,
            })
        panel.set_up_distribution_cable_choices(connected)

    def _update_up_distribution_cable_choices_all(self):
        for point_id in self.param_panel.elec_point_panels.keys():
            self._update_up_distribution_cable_choices_for_point(point_id)

    def _on_place_elec_point(self, point_id: str):
        params = self.param_panel.get_elec_point_params(point_id)
        if not params:
            return
        self.canvas._elec_point_notes[point_id] = params.get("note", "")
        self.canvas._elec_point_smarthome_device[point_id] = params.get("smarthome_device", "")
        self.canvas._elec_point_smarthome_device_color[point_id] = params.get("smarthome_device_color", "")
        self.canvas.set_color(point_id, QColor(params.get("color", "#4fc3f7")))
        self.canvas.start_place_elec_point(
            point_id, params["width"], params["height"])
        self.status.showMessage(
            f"{point_id}: Klicke auf den Plan um den Anschlusspunkt "
            "zu platzieren. ESC = Abbruch, Rechtsklick = Kontextmenü")

    def _on_elec_point_placed(self, point_id: str):
        self._update_elec_point_room_assignments()
        self._update_up_distribution_cable_choices_for_point(point_id)
        self._refresh_elec_schema_window()
        self.status.showMessage(
            f"✅ Anschlusspunkt {point_id} platziert.")

    def _on_elec_point_size_changed(self, point_id: str):
        params = self.param_panel.get_elec_point_params(point_id)
        if params:
            self.canvas.update_elec_point_size(
                point_id, params["width"], params["height"])
            self._refresh_elec_schema_window()

    def _on_elec_point_icon_changed(self, point_id: str, path: str):
        self.canvas.set_elec_point_icon(point_id, path)
        self._refresh_elec_schema_window()

    def _on_elec_point_color_changed(self, point_id: str, color: str):
        self.canvas.set_color(point_id, QColor(color))
        self._refresh_elec_schema_window()

    def _on_elec_point_name_changed(self, point_id: str, name: str):
        self.canvas._label_map[point_id] = name
        self.canvas.update()
        self._refresh_elec_schema_window()

    def _on_elec_point_position_changed(self, point_id: str, position: str):
        self.canvas._elec_point_position[point_id] = position
        self._mark_dirty()
        self._refresh_elec_schema_window()

    def _on_elec_point_height_changed(self, point_id: str, height: float):
        self.canvas._elec_point_height[point_id] = height
        self._mark_dirty()
        self._refresh_elec_schema_window()

    def _on_elec_point_note_changed(self, point_id: str, note: str):
        self.canvas._elec_point_notes[point_id] = note
        self._mark_dirty_debounced()
        self._refresh_elec_schema_window()

    def _on_elec_point_smarthome_changed(self, point_id: str, device: str):
        self.canvas._elec_point_smarthome_device[point_id] = device
        self._mark_dirty_debounced()
        self._refresh_elec_schema_window()

    def _on_elec_point_smarthome_color_changed(self, point_id: str, color: str):
        self.canvas._elec_point_smarthome_device_color[point_id] = color
        self._mark_dirty_debounced()
        self._refresh_elec_schema_window()

    def _on_elec_point_ap_type_changed(self, point_id: str, ap_type: str):
        self._mark_dirty()
        self._refresh_elec_schema_window()

    def _on_elec_point_uv_config_changed(self, point_id: str):
        self._mark_dirty()
        self._refresh_elec_schema_window()

    def _on_elec_point_up_distribution_changed(self, point_id: str):
        self._mark_dirty()
        self._refresh_elec_schema_window()

    def _on_elec_visibility_changed(self, item_id: str, visible: bool):
        self.canvas._elec_visible[item_id] = visible
        self.canvas.update()
        self._refresh_elec_schema_window()

    def _delete_elec_point(self, point_id: str):
        self.canvas.delete_elec_point(point_id)
        self.param_panel.remove_elec_point_panel(point_id)
        self._elec_point_room_map.pop(point_id, None)
        self._elec_schema_ap_positions.pop(point_id, None)
        self._update_up_distribution_cable_choices_all()
        self._update_elec_point_room_assignments()
        self._refresh_elec_schema_window()
        self.status.showMessage(f"🗑️ Anschlusspunkt {point_id} gelöscht.")

    def _add_elec_room(self, fp_id: str = ""):
        self._elec_room_counter += 1
        rid = f"R-{self._elec_room_counter}"
        self._create_elec_room_panel(rid, fp_id=fp_id or None, name=rid)
        self.status.showMessage(
            f"{rid}: Klicke 'Raum-Polygon zeichnen' im Panel."
        )

    def _create_elec_room_panel(self, room_id: str,
                                fp_id: str | None = None,
                                name: str | None = None):
        panel = self.param_panel.add_elec_room_panel(room_id, fp_id=fp_id, name=name)
        panel.draw_requested.connect(self._on_draw_elec_room)
        panel.edit_requested.connect(self._on_edit_elec_room)
        panel.name_changed.connect(self._on_elec_room_name_changed)
        panel.color_changed.connect(self._on_elec_room_color_changed)
        panel.visibility_changed.connect(self._on_elec_room_visibility_changed)
        panel.label_size_changed.connect(self._on_label_size_changed)
        panel.label_visibility_changed.connect(self._on_label_visibility_changed)
        return panel

    def _on_draw_elec_room(self, room_id: str):
        self.canvas.start_draw_elec_room(room_id)
        self.status.showMessage(
            f"{room_id}: Raum-Polygon zeichnen  |  "
            "Linksklick = Punkt  |  Enter/Doppelklick = Fertig  |  ESC = Abbruch  |  Rechtsklick = Kontextmenü"
        )

    def _on_edit_elec_room(self, room_id: str):
        self.canvas.start_edit_elec_room_polygon(room_id)
        self.status.showMessage(
            "Raum-Polygon bearbeiten: Linksklick zum Verschieben, Doppelklick auf Punkt zum Löschen, "
            "Doppelklick auf Kante zum Einfügen, Mitteltaste oder ESC zum Beenden, Rechtsklick=Kontextmenü."
        )

    def _on_elec_room_polygon_finished(self, room_id: str, points: list):
        self._update_elec_point_room_assignments()
        self.status.showMessage(f"✅ Raum {room_id} erstellt ({len(points)} Punkte).")

    def _on_elec_room_polygon_changed(self, room_id: str):
        self._update_elec_point_room_assignments()

    def _on_elec_room_name_changed(self, room_id: str, name: str):
        self.canvas._label_map[room_id] = name
        self.canvas.update()
        self._update_elec_point_room_assignments()

    def _on_elec_room_color_changed(self, room_id: str, color: str):
        self.canvas.set_color(room_id, QColor(color))

    def _on_elec_room_visibility_changed(self, room_id: str, visible: bool):
        self.canvas._elec_room_visible[room_id] = visible
        self.canvas.update()
        self._update_elec_point_room_assignments()

    def _delete_elec_room(self, room_id: str):
        self.canvas.delete_elec_room(room_id)
        self.param_panel.remove_elec_room_panel(room_id)
        self._update_elec_point_room_assignments()
        self.status.showMessage(f"🗑️ Raum {room_id} gelöscht.")

    def _update_elec_point_room_assignments(self):
        room_ids = list(self.param_panel.elec_room_panels.keys())
        self._elec_point_room_map.clear()
        if not room_ids:
            for pid, panel in self.param_panel.elec_point_panels.items():
                panel.set_room_name("")
                self.param_panel.set_elec_point_room_assignment(pid, "", "")
            self.param_panel.sort_elec_point_room_groups()
            self._refresh_elec_schema_window()
            return

        for pid, panel in self.param_panel.elec_point_panels.items():
            pt = self.canvas._elec_points.get(pid)
            if pt is None:
                panel.set_room_name("")
                self.param_panel.set_elec_point_room_assignment(pid, "", "")
                continue
            point_fp = self.param_panel._element_floorplan.get(pid, "")
            assigned_name = ""
            assigned_room_id = ""
            for rid in room_ids:
                if not self.canvas._elec_room_visible.get(rid, True):
                    continue
                if self.param_panel._element_floorplan.get(rid, "") != point_fp:
                    continue
                poly = self.canvas._elec_room_polygons.get(rid, [])
                if len(poly) < 3:
                    continue
                if self.canvas._point_in_polygon(pt, poly):
                    room_panel = self.param_panel.elec_room_panels.get(rid)
                    assigned_name = room_panel.get_parameters().get("name", rid) if room_panel else rid
                    assigned_room_id = rid
                    break
            panel.set_room_name(assigned_name)
            if assigned_room_id:
                self._elec_point_room_map[pid] = assigned_room_id
            self.param_panel.set_elec_point_room_assignment(pid, assigned_room_id, assigned_name)
        self.param_panel.sort_elec_point_room_groups()
        self._refresh_elec_schema_window()

    def _add_elec_cable(self, fp_id: str = ""):
        self._elec_cable_counter += 1
        cid = f"KV-{self._elec_cable_counter}"
        panel = self._create_elec_cable_panel(cid, fp_id=fp_id or None, name=None)
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
        panel.type_changed.connect(self._on_elec_cable_type_changed)
        panel.comment_changed.connect(self._on_elec_cable_comment_changed)
        panel.visibility_changed.connect(self._on_elec_visibility_changed)
        panel.label_size_changed.connect(self._on_label_size_changed)
        panel.label_visibility_changed.connect(self._on_label_visibility_changed)
        panel.type_label_visibility_changed.connect(
            self._on_elec_cable_type_label_visibility_changed
        )
        panel.stroke_width_changed.connect(self._on_elec_cable_stroke_width_changed)
        self._update_up_distribution_cable_choices_all()
        return panel

    def _on_draw_elec_cable(self, cable_id: str):
        panel = self.param_panel.elec_cable_panels.get(cable_id)
        if panel:
            self.canvas.set_color(cable_id, QColor(panel._color.name()))
            self.canvas._elec_cable_notes[cable_id] = panel.get_parameters().get("comment", "")
            self.canvas.set_elec_cable_stroke_width(
                cable_id, panel.get_parameters().get("stroke_width", 2.0))
        self.canvas.start_draw_elec_cable(cable_id)
        self.status.showMessage(
            f"{cable_id}: Kabel zeichnen  |  "
            "Linksklick = Punkt  |  Enter/Doppelklick = Fertig  |  ESC = Abbruch  |  Rechtsklick = Kontextmenü"
        )

    def _on_edit_elec_cable(self, cable_id: str):
        self.canvas.start_edit_elec_cable(cable_id)
        self.status.showMessage(
            f"Kabel bearbeiten: Linksklick=Verschieben, Doppelklick auf Punkt=Löschen, "
            f"Doppelklick auf Kante=Einfügen, Mitteltaste/ESC=Beenden, Rechtsklick=Kontextmenü."
        )

    def _on_elec_cable_changed(self, cable_id: str):
        length_px = self.canvas.get_elec_cable_length_px(cable_id)
        length_mm = length_px * self.canvas.get_mm_per_px()
        self.param_panel.set_cable_length(cable_id, length_mm)
        self._update_cable_ap_labels(cable_id)
        self.param_panel.update_all_elec_point_uv_cable_choices()
        self._update_up_distribution_cable_choices_all()
        self._refresh_elec_schema_window()
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
        self._update_up_distribution_cable_choices_all()
        self._refresh_elec_schema_window()

    def _on_elec_cable_color_changed(self, cable_id: str, color: str):
        self.canvas.set_color(cable_id, QColor(color))
        self._refresh_elec_schema_window()

    def _on_elec_cable_type_changed(self, cable_id: str, cable_type: str):
        self.canvas.set_elec_cable_type_text(cable_id, cable_type)
        self._mark_dirty_debounced()
        self._refresh_elec_schema_window()

    def _on_elec_cable_type_label_visibility_changed(self, cable_id: str, visible: bool):
        self.canvas.set_elec_cable_type_label_visible(cable_id, visible)
        self._mark_dirty_debounced()
        self._refresh_elec_schema_window()

    def _on_elec_cable_comment_changed(self, cable_id: str, comment: str):
        self.canvas._elec_cable_notes[cable_id] = comment
        self._mark_dirty_debounced()
        self._refresh_elec_schema_window()

    def _on_elec_cable_stroke_width_changed(self, cable_id: str, width: float):
        self.canvas.set_elec_cable_stroke_width(cable_id, width)
        self._mark_dirty_debounced()
        self._refresh_elec_schema_window()

    def _delete_elec_cable(self, cable_id: str):
        self.canvas.delete_elec_cable(cable_id)
        self.param_panel.remove_elec_cable_panel(cable_id)
        self.param_panel.update_all_elec_point_uv_cable_choices()
        self._update_up_distribution_cable_choices_all()
        self._refresh_elec_schema_window()
        self.status.showMessage(f"🗑️ Kabelverbindung {cable_id} gelöscht.")

    def _open_elec_schema_window(self):
        if self._elec_schema_window is None:
            self._elec_schema_window = ElecSchemaWindow(self)
            self._elec_schema_window.add_ap_requested.connect(self._on_schema_add_ap)
            self._elec_schema_window.add_cable_requested.connect(self._on_schema_add_cable)
            self._elec_schema_window.delete_ap_requested.connect(self._delete_elec_point)
            self._elec_schema_window.delete_cable_requested.connect(self._delete_elec_cable)
            self._elec_schema_window.ap_position_changed.connect(self._on_schema_ap_position_changed)
            self._elec_schema_window.ap_positions_changed.connect(self._on_schema_ap_positions_changed)
            self._elec_schema_window.edit_ap_requested.connect(self._on_schema_edit_ap)
            self._elec_schema_window.edit_cable_requested.connect(self._on_schema_edit_cable)
            self._elec_schema_window.duplicate_selection_requested.connect(self._on_schema_duplicate_selection)
            self._restore_window_state(
                self._elec_schema_window,
                self._project_ui_state.get("elec_schema_window", {}),
            )
        self._refresh_elec_schema_window()
        self._elec_schema_window.show()
        self._elec_schema_window.raise_()
        self._elec_schema_window.activateWindow()

    def _collect_elec_room_choices(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        for room_id, panel in self.param_panel.elec_room_panels.items():
            room_name = str(panel.get_parameters().get("name", room_id) or room_id).strip() or room_id
            choices.append((room_id, room_name))
        choices.sort(key=lambda value: value[1].lower())
        return choices

    def _resolve_schema_room_center(self, room_id: str) -> tuple[QPointF, str] | None:
        polygon = self.canvas._elec_room_polygons.get(room_id, [])
        if not polygon:
            return None

        area_twice = 0.0
        cx = 0.0
        cy = 0.0
        for idx, point in enumerate(polygon):
            nxt = polygon[(idx + 1) % len(polygon)]
            cross = point.x() * nxt.y() - nxt.x() * point.y()
            area_twice += cross
            cx += (point.x() + nxt.x()) * cross
            cy += (point.y() + nxt.y()) * cross

        if abs(area_twice) > 1e-9:
            centroid = QPointF(cx / (3.0 * area_twice), cy / (3.0 * area_twice))
        else:
            centroid = QPointF(
                sum(point.x() for point in polygon) / len(polygon),
                sum(point.y() for point in polygon) / len(polygon),
            )

        if not self.canvas._point_in_polygon(centroid, polygon):
            centroid = QPointF(
                sum(point.x() for point in polygon) / len(polygon),
                sum(point.y() for point in polygon) / len(polygon),
            )

        return centroid, str(self.param_panel._element_floorplan.get(room_id, "") or "")

    def _place_schema_elec_point(self, point_id: str, position: QPointF):
        panel = self.param_panel.elec_point_panels.get(point_id)
        if not panel:
            return
        params = panel.get_parameters()
        self.canvas._elec_point_notes[point_id] = params.get("note", "")
        self.canvas._elec_point_smarthome_device[point_id] = params.get("smarthome_device", "")
        self.canvas._elec_point_smarthome_device_color[point_id] = params.get("smarthome_device_color", "")
        self.canvas._elec_point_position[point_id] = params.get("position", "Wand")
        self.canvas._elec_point_height[point_id] = params.get("height_from_floor", 0.0)
        self.canvas._elec_visible[point_id] = bool(params.get("visible", True))
        self.canvas.set_label_visible(point_id, bool(params.get("label_visible", True)))
        self.canvas.set_label_font_size(point_id, float(params.get("label_size", 12.0) or 12.0))
        self.canvas.update_elec_point_size(point_id, params.get("width", 30.0), params.get("height", 30.0))
        self.canvas.set_elec_point_icon(point_id, str(params.get("icon_path", "") or ""))
        self.canvas._ensure_color(point_id)
        self.canvas.set_color(point_id, QColor(params.get("color", "#4fc3f7")))
        self.canvas._label_map[point_id] = str(params.get("name", point_id) or point_id)
        self.canvas._elec_points[point_id] = QPointF(position)
        self.canvas.update()
        self.canvas.elec_point_placed.emit(point_id)

    def _resolve_schema_cable_floorplan(self, start_ap_id: str, end_ap_id: str) -> str:
        for point_id in (start_ap_id, end_ap_id):
            if point_id:
                fp_id = str(self.param_panel._element_floorplan.get(point_id, "") or "")
                if fp_id:
                    return fp_id
        return ""

    def _rebuild_schema_cable_geometry(self, cable_id: str, start_ap_id: str, end_ap_id: str):
        existing = [QPointF(point) for point in self.canvas._elec_cables.get(cable_id, [])]
        start_point = QPointF(self.canvas._elec_points[start_ap_id]) if start_ap_id in self.canvas._elec_points else None
        end_point = QPointF(self.canvas._elec_points[end_ap_id]) if end_ap_id in self.canvas._elec_points else None

        if start_point and end_point:
            self.canvas._elec_cables[cable_id] = [start_point, end_point]
            return

        if start_point:
            fallback = existing[-1] if existing else QPointF(start_point.x() + 120.0, start_point.y() + 40.0)
            self.canvas._elec_cables[cable_id] = [start_point, QPointF(fallback)]
            return

        if end_point:
            fallback = existing[0] if existing else QPointF(end_point.x() - 120.0, end_point.y() - 40.0)
            self.canvas._elec_cables[cable_id] = [QPointF(fallback), end_point]
            return

        if cable_id not in self.canvas._elec_cables:
            self.canvas._elec_cables[cable_id] = []

    def _on_schema_add_ap(self, payload: dict):
        room_id = str(payload.get("room_id") or "").strip()
        room_target = self._resolve_schema_room_center(room_id) if room_id else None
        fp_id = room_target[1] if room_target else str(self.param_panel._element_floorplan.get(room_id, "") or "")
        self._add_elec_point(fp_id=fp_id)
        point_id = f"AP-{self._elec_point_counter}"
        panel = self.param_panel.elec_point_panels.get(point_id)
        if not panel:
            return

        name = (payload.get("name") or "").strip() or point_id
        color = (payload.get("color") or "").strip() or "#4fc3f7"
        symbol = (payload.get("symbol") or "").strip()
        ap_type = str(payload.get("ap_type") or "standard")

        panel.le_name.setText(name)
        panel.set_ap_type(ap_type)
        if panel.cmb_symbol.findText(symbol) >= 0:
            panel.cmb_symbol.setCurrentText(symbol)
        panel._icon_path = BUILTIN_SYMBOLS.get(symbol, "") if symbol else ""
        panel._color = QColor(color)
        panel._update_color_button()
        self._on_elec_point_color_changed(point_id, color)
        self.canvas.set_elec_point_icon(point_id, panel._icon_path or "")
        if room_target is not None:
            self._place_schema_elec_point(point_id, room_target[0])
        else:
            self._on_place_elec_point(point_id)
        self._refresh_elec_schema_window()

    def _on_schema_add_cable(self, payload: dict):
        start_ap_id = str(payload.get("start_ap_id") or "").strip()
        end_ap_id = str(payload.get("end_ap_id") or "").strip()
        needs_pick_mode = not start_ap_id or not end_ap_id
        fp_id = self._resolve_schema_cable_floorplan(start_ap_id, end_ap_id)
        self._add_elec_cable(fp_id=fp_id)
        cable_id = f"KV-{self._elec_cable_counter}"
        panel = self.param_panel.elec_cable_panels.get(cable_id)
        if not panel:
            return

        name = (payload.get("name") or "").strip() or cable_id
        cable_type = (payload.get("type") or "").strip() or "5x1,5"
        color = (payload.get("color") or "").strip() or "#ff9800"
        try:
            stroke_width = float(payload.get("stroke_width", 2.0))
        except (TypeError, ValueError):
            stroke_width = 2.0

        panel.le_name.setText(name)
        panel.set_type_text(cable_type)
        self._on_elec_cable_type_changed(cable_id, cable_type)
        panel.sb_stroke_width.setValue(stroke_width)
        panel._color = QColor(color)
        panel._update_color_button()
        self._on_elec_cable_color_changed(cable_id, color)
        self.canvas.set_elec_cable_stroke_width(cable_id, stroke_width)
        self.canvas._cable_start_ap[cable_id] = start_ap_id
        self.canvas._cable_end_ap[cable_id] = end_ap_id
        self.canvas._elec_visible[cable_id] = bool(panel.get_parameters().get("visible", True))
        self._rebuild_schema_cable_geometry(cable_id, start_ap_id, end_ap_id)
        self.canvas.update()
        self.canvas.elec_cable_changed.emit(cable_id)
        self._refresh_elec_schema_window()
        if needs_pick_mode and self._elec_schema_window is not None:
            self._elec_schema_window.start_cable_pick_mode(cable_id)

    def _refresh_elec_schema_window(self, *_args):
        if self._suspend_schema_refresh:
            return
        if self._elec_schema_window is not None:
            valid_ids = set(self.param_panel.elec_point_panels.keys())
            self._elec_schema_ap_positions = {
                pid: pos
                for pid, pos in self._elec_schema_ap_positions.items()
                if pid in valid_ids
            }
            ap_nodes, cable_edges = self._build_elec_schema_data()
            self._elec_schema_window.set_data(
                ap_nodes,
                cable_edges,
                manual_positions=self._elec_schema_ap_positions,
                room_choices=self._collect_elec_room_choices(),
            )
        self._refresh_schaltplan_window()

    def _open_schaltplan_window(self):
        if self._schaltplan_window is None:
            self._schaltplan_window = SchaltplanWindow(self)
            self._restore_window_state(
                self._schaltplan_window,
                self._project_ui_state.get("schaltplan_window", {}),
            )
        self._refresh_schaltplan_window()
        self._schaltplan_window.show()
        self._schaltplan_window.raise_()
        self._schaltplan_window.activateWindow()

    def _refresh_schaltplan_window(self, *_args):
        if self._schaltplan_window is None:
            return
        if self._suspend_schema_refresh:
            return
        ap_nodes, cable_edges = self._build_elec_schema_data()
        ap_nodes_dict = {n.point_id: n for n in ap_nodes}
        cable_edges_dict = {e.cable_id: e for e in cable_edges}
        room_map = self._collect_point_id_to_room_name()
        self._schaltplan_window.set_data(ap_nodes_dict, cable_edges_dict, room_map)

    def _on_schema_ap_position_changed(self, point_id: str, x: float, y: float):
        self._elec_schema_ap_positions[point_id] = [float(x), float(y)]
        self._mark_dirty()

    def _on_schema_ap_positions_changed(self, positions: dict):
        changed = False
        for point_id, pos in positions.items():
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                continue
            try:
                nx = float(pos[0])
                ny = float(pos[1])
            except (TypeError, ValueError):
                continue
            current = self._elec_schema_ap_positions.get(point_id)
            if current is not None and len(current) == 2:
                if abs(float(current[0]) - nx) <= 0.01 and abs(float(current[1]) - ny) <= 0.01:
                    continue
            self._elec_schema_ap_positions[point_id] = [nx, ny]
            changed = True
        if changed:
            self._record_mutation(debounced=False)

    def _on_schema_duplicate_selection(self, ap_ids: list[str], cable_ids: list[str]):
        selected_ap_ids = [pid for pid in ap_ids if pid in self.param_panel.elec_point_panels]
        selected_cable_ids = [cid for cid in cable_ids if cid in self.param_panel.elec_cable_panels]
        if not selected_ap_ids and not selected_cable_ids:
            return

        id_map: dict[str, str] = {}
        any_created = False

        for source_ap_id in selected_ap_ids:
            new_ap_id = self._duplicate_elec_point(source_ap_id)
            if not new_ap_id:
                continue
            id_map[source_ap_id] = new_ap_id
            source_pos = self._elec_schema_ap_positions.get(source_ap_id)
            if isinstance(source_pos, (list, tuple)) and len(source_pos) == 2:
                try:
                    self._elec_schema_ap_positions[new_ap_id] = [
                        float(source_pos[0]) + 20.0,
                        float(source_pos[1]) + 20.0,
                    ]
                except (TypeError, ValueError):
                    pass
            any_created = True

        for source_cable_id in selected_cable_ids:
            new_cable_id = self._duplicate_elec_cable(source_cable_id)
            if not new_cable_id:
                continue

            source_start = str(self.canvas._cable_start_ap.get(source_cable_id, "") or "").strip()
            source_end = str(self.canvas._cable_end_ap.get(source_cable_id, "") or "").strip()
            new_start = id_map.get(source_start, "")
            new_end = id_map.get(source_end, "")

            if new_start or new_end:
                self.canvas._cable_start_ap[new_cable_id] = new_start
                self.canvas._cable_end_ap[new_cable_id] = new_end
                self._rebuild_schema_cable_geometry(new_cable_id, new_start, new_end)

            any_created = True

        if not any_created:
            return

        self.canvas.update()
        self._refresh_elec_schema_window()
        self._mark_dirty()

    def _on_schema_edit_ap(self, point_id: str, payload: dict):
        """Übernimmt bearbeitete AP-Eigenschaften aus dem Schema-Fenster."""
        panel = self.param_panel.elec_point_panels.get(point_id)
        if not panel:
            return
        panel.blockSignals(True)
        try:
            name = (payload.get("name") or "").strip()
            panel.le_name.setText(name)
            symbol = (payload.get("symbol") or "").strip()
            if panel.cmb_symbol.findText(symbol) >= 0:
                panel.cmb_symbol.setCurrentText(symbol)
            icon_path = str(payload.get("icon_path") or "").strip()
            builtin_path = BUILTIN_SYMBOLS.get(symbol, "") if symbol else ""
            if icon_path and icon_path != builtin_path:
                panel._icon_path = icon_path
                panel.btn_icon.setText(Path(icon_path).name)
                idx = panel.cmb_symbol.findText("(kein Symbol)")
                if idx >= 0:
                    panel.cmb_symbol.blockSignals(True)
                    panel.cmb_symbol.setCurrentIndex(idx)
                    panel.cmb_symbol.blockSignals(False)
                self.canvas.set_elec_point_icon(point_id, icon_path)
            elif symbol and panel.cmb_symbol.findText(symbol) >= 0:
                panel._icon_path = builtin_path
                panel.btn_icon.setText("Eigenes Bild…")
                self.canvas.set_elec_point_icon(point_id, builtin_path)
            else:
                panel._icon_path = ""
                panel.btn_icon.setText("Eigenes Bild…")
                self.canvas.set_elec_point_icon(point_id, "")
            color = (payload.get("color") or "").strip()
            if color:
                panel._color = QColor(color)
                panel._update_color_button()
                self._on_elec_point_color_changed(point_id, color)
            try:
                panel.sb_width.setValue(float(payload.get("width", panel.sb_width.value() * 10.0)) / 10.0)
                panel.sb_height.setValue(float(payload.get("height", panel.sb_height.value() * 10.0)) / 10.0)
            except (TypeError, ValueError):
                pass
            panel.chk_visible.setChecked(bool(payload.get("visible", panel.chk_visible.isChecked())))
            panel.chk_label_visible.setChecked(bool(payload.get("label_visible", panel.chk_label_visible.isChecked())))
            try:
                panel.sb_label_size.setValue(float(payload.get("label_size", panel.sb_label_size.value())))
            except (TypeError, ValueError):
                pass
            ap_type = str(payload.get("ap_type") or "standard")
            panel.set_ap_type(ap_type)
            position = (payload.get("position") or "").strip()
            if position in {"Wand", "Decke", "Boden", "Freitext"}:
                panel.cmb_position.setCurrentText(position)
            elif position:
                idx = panel.cmb_position.findText("Freitext")
                if idx >= 0:
                    panel.cmb_position.setCurrentIndex(idx)
                panel.le_position_custom.setText(position)
            try:
                hff = float(payload.get("height_from_floor", 30))
                panel.sb_height_from_floor.setValue(hff)
            except (TypeError, ValueError):
                pass
            panel.set_smarthome_device_text(str(payload.get("smarthome_device") or ""))
            panel.set_smarthome_device_color_text(str(payload.get("smarthome_device_color") or ""))
            panel.te_note.setPlainText(str(payload.get("note") or ""))
            uv_config = payload.get("uv_config")
            if uv_config is not None:
                panel.set_uv_config(uv_config)
            up_config = payload.get("up_distribution_config")
            if up_config is not None:
                panel.set_up_distribution_config(up_config)
            hak_config = payload.get("hak_config")
            if hak_config is not None:
                panel._hak_config = dict(hak_config or {})
            zaehler_config = payload.get("zaehler_config")
            if zaehler_config is not None:
                panel._zaehler_config = dict(zaehler_config or {})
        finally:
            panel.blockSignals(False)
        self._mark_dirty()
        self._refresh_elec_schema_window()
        self._refresh_schaltplan_window()
        self._refresh_schaltplan_window()

    def _on_schema_edit_cable(self, cable_id: str, payload: dict):
        """Übernimmt bearbeitete Kabel-Eigenschaften aus dem Schema-Fenster."""
        panel = self.param_panel.elec_cable_panels.get(cable_id)
        if not panel:
            return
        panel.blockSignals(True)
        try:
            name = (payload.get("name") or "").strip()
            panel.le_name.setText(name)
            cable_type = (payload.get("type") or "").strip()
            panel.set_type_text(cable_type)
            color = (payload.get("color") or "").strip()
            if color:
                panel._color = QColor(color)
                panel._update_color_button()
                self._on_elec_cable_color_changed(cable_id, color)
            panel.chk_visible.setChecked(bool(payload.get("visible", panel.chk_visible.isChecked())))
            panel.chk_label_visible.setChecked(bool(payload.get("label_visible", panel.chk_label_visible.isChecked())))
            panel.chk_type_label_visible.setChecked(
                bool(payload.get("type_label_visible", panel.chk_type_label_visible.isChecked()))
            )
            try:
                panel.sb_label_size.setValue(float(payload.get("label_size", panel.sb_label_size.value())))
            except (TypeError, ValueError):
                pass
            try:
                stroke_width = float(payload.get("stroke_width", 2.0))
                panel.sb_stroke_width.setValue(stroke_width)
            except (TypeError, ValueError):
                pass
            panel.te_comment.setPlainText(str(payload.get("comment") or ""))
            start_ap = str(payload.get("start_ap_id") or "").strip()
            end_ap = str(payload.get("end_ap_id") or "").strip()
        finally:
            panel.blockSignals(False)
        if start_ap:
            self.canvas._cable_start_ap[cable_id] = start_ap
        elif "start_ap_id" in payload:  # explizit auf leer gesetzt
            self.canvas._cable_start_ap.pop(cable_id, None)
        if end_ap:
            self.canvas._cable_end_ap[cable_id] = end_ap
        elif "end_ap_id" in payload:
            self.canvas._cable_end_ap.pop(cable_id, None)
        self._rebuild_schema_cable_geometry(cable_id, start_ap, end_ap)
        fp_id = self._resolve_schema_cable_floorplan(start_ap, end_ap)
        if fp_id:
            self.param_panel._element_floorplan[cable_id] = fp_id
        self.canvas.update()
        self.canvas.elec_cable_changed.emit(cable_id)
        self._mark_dirty()

    def _build_elec_schema_data(self) -> tuple[list[ApNode], list[CableEdge]]:
        point_id_to_room = self._collect_point_id_to_room_name()

        connected_points: set[str] = set()
        for cable_id in self.param_panel.elec_cable_panels.keys():
            start_ap_id, end_ap_id = self.canvas.get_cable_ap(cable_id)
            if start_ap_id:
                connected_points.add(start_ap_id)
            if end_ap_id:
                connected_points.add(end_ap_id)

        ap_nodes: list[ApNode] = []
        for point_id, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            has_distributor = self._is_uv_type(params) or self._is_up_distribution_type(params)
            size_px = self.canvas._elec_point_size_px.get(point_id, (56.0, 56.0))
            ap_nodes.append(
                ApNode(
                    point_id=point_id,
                    name=str(params.get("name", point_id) or point_id),
                    room=point_id_to_room.get(point_id, "(ohne Raum)"),
                    ap_type=str(params.get("ap_type", "standard") or "standard"),
                    has_distributor_function=has_distributor,
                    is_connected=point_id in connected_points,
                    color=str(params.get("color", "#4fc3f7") or "#4fc3f7"),
                    icon_path=str(params.get("icon_path", "") or ""),
                    builtin_symbol=str(params.get("builtin_symbol", "") or ""),
                    width_px=float(size_px[0]),
                    height_px=float(size_px[1]),
                    width_mm=float(params.get("width", 30.0) or 30.0),
                    height_mm=float(params.get("height", 30.0) or 30.0),
                    visible=bool(params.get("visible", True)),
                    label_visible=bool(params.get("label_visible", True)),
                    label_size=float(params.get("label_size", 12.0) or 12.0),
                    position=str(params.get("position", "Wand") or "Wand"),
                    height_from_floor=float(params.get("height_from_floor", 0.0) or 0.0),
                    smarthome_device=str(params.get("smarthome_device", "") or ""),
                    smarthome_device_color=str(params.get("smarthome_device_color", "") or ""),
                    note=str(params.get("note", "") or ""),
                    uv_config=params.get("uv_config"),
                    up_distribution_config=params.get("up_distribution_config"),
                    hak_config=params.get("hak_config"),
                    zaehler_config=params.get("zaehler_config"),
                )
            )

        cable_edges: list[CableEdge] = []
        mm_per_px = self.canvas.get_mm_per_px()
        for cable_id, panel in self.param_panel.elec_cable_panels.items():
            params = panel.get_parameters()
            length_px = self.canvas.get_elec_cable_length_px(cable_id)
            start_ap_id, end_ap_id = self.canvas.get_cable_ap(cable_id)
            cable_edges.append(
                CableEdge(
                    cable_id=cable_id,
                    name=str(params.get("name", cable_id) or cable_id),
                    cable_type=str(params.get("type", "") or ""),
                    length_m=(length_px * mm_per_px) / 1000.0,
                    color=str(params.get("color", "#ff9800") or "#ff9800"),
                    stroke_width_px=float(params.get("stroke_width", 2.0) or 2.0),
                    start_ap_id=str(start_ap_id or ""),
                    end_ap_id=str(end_ap_id or ""),
                    visible=bool(params.get("visible", True)),
                    label_visible=bool(params.get("label_visible", True)),
                    type_label_visible=bool(params.get("type_label_visible", False)),
                    label_size=float(params.get("label_size", 12.0) or 12.0),
                    comment=str(params.get("comment", "") or ""),
                )
            )

        return ap_nodes, cable_edges

    @staticmethod
    def _sanitize_elec_schema_ap_positions(raw: dict) -> dict[str, list[float]]:
        if not isinstance(raw, dict):
            return {}
        sanitized: dict[str, list[float]] = {}
        for point_id, pos in raw.items():
            pid = str(point_id or "").strip()
            if not pid:
                continue
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                continue
            try:
                x = float(pos[0])
                y = float(pos[1])
            except (TypeError, ValueError):
                continue
            sanitized[pid] = [x, y]
        return sanitized

    def _selected_object_type(self, item_id: str) -> str | None:
        if item_id in self.param_panel.floorplan_panels:
            return "floorplan"
        if item_id in self.param_panel.furniture_panels:
            return "furniture"
        if item_id in self.param_panel.circuit_panels:
            return "circuit"
        if item_id in self.param_panel.elec_point_panels:
            return "elec_point"
        if item_id in self.param_panel.elec_room_panels:
            return "elec_room"
        if item_id in self.param_panel.elec_cable_panels:
            return "elec_cable"
        if item_id in self.param_panel.hkv_panels:
            return "hkv"
        if item_id in self.param_panel.hkv_line_panels:
            return "hkv_line"
        if item_id in self.param_panel.text_panels:
            return "text"
        return None

    def _delete_selected_object(self):
        item_id = self.param_panel.get_selected_item_id()
        if not item_id:
            return
        obj_type = self._selected_object_type(item_id)
        if not obj_type:
            return

        if obj_type in {"floorplan", "circuit"}:
            label = "Grundriss" if obj_type == "floorplan" else "Heizkreis"
            reply = QMessageBox.question(
                self,
                "Löschen bestätigen",
                f"{label} '{item_id}' wirklich löschen?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        deleted = False
        if obj_type == "floorplan":
            self._delete_floorplan(item_id)
            deleted = True
        elif obj_type == "furniture":
            self._delete_furniture(item_id)
            deleted = True
        elif obj_type == "circuit":
            self._delete_circuit(item_id)
            deleted = True
        elif obj_type == "elec_point":
            self._delete_elec_point(item_id)
            deleted = True
        elif obj_type == "elec_room":
            self._delete_elec_room(item_id)
            deleted = True
        elif obj_type == "elec_cable":
            self._delete_elec_cable(item_id)
            deleted = True
        elif obj_type == "hkv":
            self._delete_hkv(item_id)
            deleted = True
        elif obj_type == "hkv_line":
            self._delete_hkv_line(item_id)
            deleted = True
        elif obj_type == "text":
            self._delete_text(item_id)
            deleted = True

        if deleted:
            self._mark_dirty()

    def _numbering_elec_points(self):
        """Number all electrical connection points with the same name."""
        if not self.canvas._elec_points:
            self.status.showMessage("Keine Anschlusspunkte vorhanden.", 2000)
            return
        
        # Push undo state before making changes
        self._push_undo()
        
        # Group points by name
        from collections import defaultdict
        groups = defaultdict(list)
        
        for pid in self.canvas._elec_points:
            name = self.canvas._label_map.get(pid, pid)
            groups[name].append(pid)
        
        # Update labels: if group has > 1 point, add number to all
        changes_made = 0
        for name, point_ids in groups.items():
            if len(point_ids) > 1:
                # Sort for consistent numbering
                point_ids_sorted = sorted(point_ids)
                for idx, pid in enumerate(point_ids_sorted, start=1):
                    new_name = f"{name}{idx}"
                    self.canvas._label_map[pid] = new_name
                    # Update panel if exists
                    panel = self.param_panel.elec_point_panels.get(pid)
                    if panel:
                        panel.le_name.setText(new_name)
                        panel.le_name.editingFinished.emit()
                    changes_made += 1
        
        self.canvas.update()
        self._mark_dirty()
        
        if changes_made > 0:
            self.status.showMessage(
                f"✅ {changes_made} Anschlusspunkt(e) durchnummeriert."
            )
        else:
            self.status.showMessage("Alle Anschlusspunkte haben unterschiedliche Namen.", 2000)

    def _export_to_kicad(self):
        """Export current project to KiCad schematic (.kicad_sch) format."""
        if not self._project_path:
            QMessageBox.warning(
                self, "Projekt nicht gespeichert",
                "Bitte speichern Sie das Projekt zuerst bevor Sie es exportieren."
            )
            return
        
        # Suggest filename based on project name
        base_name = self._project_path.stem
        suggested_name = f"{base_name}_export.kicad_sch"
        
        export_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportieren zu KiCad",
            str(self._project_path.parent / suggested_name),
            "KiCad Schaltplan (*.kicad_sch);;Alle Dateien (*)"
        )
        
        if not export_path:
            return
        
        # Prepare project dict
        project_dict = {
            "canvas": copy.deepcopy(self.canvas._to_dict()),
            "params": copy.deepcopy(self.param_panel.to_dict()),
        }
        
        # Export
        success, message = export_project_to_kicad(project_dict, export_path)
        
        if success:
            QMessageBox.information(
                self, "Export erfolgreich",
                f"Projekt erfolgreich zu KiCad exportiert:\n{export_path}"
            )
            self.status.showMessage(message, 3000)
        else:
            QMessageBox.critical(
                self, "Export fehlgeschlagen",
                f"Fehler beim Exportieren:\n{message}"
            )

    def _copy_selected_object(self):
        item_id = self.param_panel.get_selected_item_id()
        if not item_id:
            self.status.showMessage("Kein Objekt ausgewählt.", 2000)
            return
        obj_type = self._selected_object_type(item_id)
        if not obj_type:
            self.status.showMessage("Dieser Eintrag kann nicht kopiert werden.", 2000)
            return
        self._copy_buffer = {"type": obj_type, "id": item_id}
        self.status.showMessage(f"📋 {item_id} kopiert.", 2000)

    def _paste_copied_object(self):
        if not self._copy_buffer:
            self.status.showMessage("Zwischenablage ist leer.", 2000)
            return
        obj_type = self._copy_buffer.get("type")
        source_id = self._copy_buffer.get("id")
        if not source_id:
            return

        new_id = None
        if obj_type == "elec_point":
            new_id = self._duplicate_elec_point(source_id)
        elif obj_type == "elec_cable":
            new_id = self._duplicate_elec_cable(source_id)
        elif obj_type == "circuit":
            new_id = self._duplicate_circuit(source_id)
        elif obj_type == "hkv":
            new_id = self._duplicate_hkv(source_id)
        elif obj_type == "hkv_line":
            new_id = self._duplicate_hkv_line(source_id)
        elif obj_type == "text":
            new_id = self._duplicate_text(source_id)
        elif obj_type == "floorplan":
            new_id = self._duplicate_floorplan(source_id)
        elif obj_type == "furniture":
            new_id = self._duplicate_furniture(source_id)
        elif obj_type == "elec_room":
            new_id = self._duplicate_elec_room(source_id)

        if new_id:
            self.param_panel.select_item(new_id)
            self._mark_dirty()

    def _duplicate_elec_room(self, source_id: str) -> str | None:
        src_panel = self.param_panel.elec_room_panels.get(source_id)
        if not src_panel:
            return None
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._elec_room_counter += 1
        new_id = f"R-{self._elec_room_counter}"
        panel = self._create_elec_room_panel(new_id, fp_id=src_fp_id, name=src.get('name', source_id))
        panel.from_dict(src)
        panel.le_name.setText(src.get('name', source_id))
        if source_id in self.canvas._elec_room_polygons:
            self.canvas._elec_room_polygons[new_id] = [QPointF(p.x() + 20, p.y() + 20)
                                                       for p in self.canvas._elec_room_polygons[source_id]]
            self.canvas._elec_room_visible[new_id] = self.canvas._elec_room_visible.get(source_id, True)
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        self.canvas.update()
        return new_id

    def _duplicate_circuit(self, source_id: str) -> str | None:
        src_panel = self.param_panel.circuit_panels.get(source_id)
        if not src_panel:
            return None
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._circuit_counter += 1
        new_id = f"HK-{self._circuit_counter}"
        panel = self._create_circuit_panel(new_id, fp_id=src_fp_id, name=src.get('name', source_id))
        panel.from_dict(src)
        panel.le_name.setText(src.get('name', source_id))
        if source_id in self.canvas._polygons:
            self.canvas._polygons[new_id] = [QPointF(p.x() + 20, p.y() + 20)
                                             for p in self.canvas._polygons[source_id]]
            self.canvas._start_points[new_id] = QPointF(
                self.canvas._start_points[source_id].x() + 20,
                self.canvas._start_points[source_id].y() + 20,
            ) if source_id in self.canvas._start_points else self.canvas._polygons[new_id][0]
        if source_id in self.canvas._manual_routes:
            self.canvas._manual_routes[new_id] = [QPointF(p.x() + 20, p.y() + 20)
                                                  for p in self.canvas._manual_routes[source_id]]
        if source_id in self.canvas._supply_lines:
            self.canvas._supply_lines[new_id] = [QPointF(p.x() + 20, p.y() + 20)
                                                 for p in self.canvas._supply_lines[source_id]]
        self.canvas._route_wall_dist_px[new_id] = self.canvas._route_wall_dist_px.get(source_id, 0.0)
        self.canvas._route_line_dist_px[new_id] = self.canvas._route_line_dist_px.get(source_id, 0.0)
        self.canvas._supply_hkv.pop(new_id, None)
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        self.canvas.update()
        return new_id

    def _duplicate_hkv(self, source_id: str) -> str | None:
        src_panel = self.param_panel.hkv_panels.get(source_id)
        if not src_panel:
            return None
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._hkv_counter += 1
        new_id = f"HKV-{self._hkv_counter}"
        panel = self._create_hkv_panel(new_id, fp_id=src_fp_id, name=src.get('name', source_id))
        panel.from_dict(src)
        panel.le_name.setText(src.get('name', source_id))
        if source_id in self.canvas._hkv_points:
            p = self.canvas._hkv_points[source_id]
            self.canvas._hkv_points[new_id] = QPointF(p.x() + 20, p.y() + 20)
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        self.canvas.update()
        return new_id

    def _duplicate_hkv_line(self, source_id: str) -> str | None:
        src_panel = self.param_panel.hkv_line_panels.get(source_id)
        if not src_panel:
            return None
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._hkv_line_counter += 1
        new_id = f"HL-{self._hkv_line_counter}"
        panel = self._create_hkv_line_panel(new_id, fp_id=src_fp_id, name=src.get('name', source_id))
        panel.from_dict(src)
        panel.le_name.setText(src.get('name', source_id))
        if source_id in self.canvas._hkv_lines:
            self.canvas._hkv_lines[new_id] = [QPointF(p.x() + 20, p.y() + 20)
                                              for p in self.canvas._hkv_lines[source_id]]
        self.canvas._hkv_line_start.pop(new_id, None)
        self.canvas._hkv_line_end.pop(new_id, None)
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        self.canvas.update()
        return new_id

    def _duplicate_text(self, source_id: str) -> str | None:
        src_panel = self.param_panel.text_panels.get(source_id)
        if not src_panel:
            return None
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._text_counter += 1
        new_id = f"Text-{self._text_counter}"
        panel = self._create_text_panel(new_id, fp_id=src_fp_id, name=src.get('name', source_id))
        panel.set_parameters(src)
        panel.le_name.setText(src.get('name', source_id))
        if source_id in self.canvas._text_annotations:
            p = self.canvas._text_annotations[source_id]
            self.canvas._text_annotations[new_id] = QPointF(p.x() + 20, p.y() + 20)
            self.canvas._text_contents[new_id] = self.canvas._text_contents.get(source_id, "")
            self.canvas._text_font_sizes[new_id] = self.canvas._text_font_sizes.get(source_id, 14.0)
            self.canvas._text_colors[new_id] = self.canvas._text_colors.get(source_id, "#ffffff")
            self.canvas._text_comments[new_id] = self.canvas._text_comments.get(source_id, "")
            self.canvas._text_visible[new_id] = self.canvas._text_visible.get(source_id, True)
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        self.canvas.update()
        return new_id

    def _copy_layer_values(self, source_id: str, new_id: str):
        src_layer = self.canvas._floor_plans.get(source_id)
        dst_layer = self.canvas._floor_plans.get(new_id)
        if not src_layer or not dst_layer:
            return
        dst_layer.size = tuple(src_layer.size)
        dst_layer.offset_x = src_layer.offset_x + 20
        dst_layer.offset_y = src_layer.offset_y + 20
        dst_layer.rotation = src_layer.rotation
        dst_layer.opacity = src_layer.opacity
        dst_layer.visible = src_layer.visible
        dst_layer.mm_per_px = src_layer.mm_per_px
        dst_layer.ref_length_mm = src_layer.ref_length_mm
        dst_layer.fixed_width_mm = src_layer.fixed_width_mm
        dst_layer.fixed_height_mm = src_layer.fixed_height_mm
        dst_layer.polygon_color = src_layer.polygon_color
        dst_layer.polygon = [QPointF(p.x(), p.y()) for p in src_layer.polygon]
        if src_layer.ref_p1:
            dst_layer.ref_p1 = QPointF(src_layer.ref_p1.x() + 20, src_layer.ref_p1.y() + 20)
        if src_layer.ref_p2:
            dst_layer.ref_p2 = QPointF(src_layer.ref_p2.x() + 20, src_layer.ref_p2.y() + 20)

    def _duplicate_floorplan(self, source_id: str) -> str | None:
        src_panel = self.param_panel.floorplan_panels.get(source_id)
        if not src_panel:
            return None
        self._floorplan_counter += 1
        new_id = f"grundriss-{self._floorplan_counter}"
        self.canvas.add_floor_plan(new_id)
        panel = self.param_panel.add_floorplan_panel(new_id, name=src_panel.get_parameters().get('name', source_id))
        panel.from_dict(src_panel.to_dict())
        panel.le_name.setText(src_panel.get_parameters().get('name', source_id))
        if src_panel.get_parameters().get("file_path"):
            self.canvas.load_floor_plan_image(new_id, src_panel.get_parameters().get("file_path"))
        self._copy_layer_values(source_id, new_id)
        self.canvas.update()
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        return new_id

    def _duplicate_furniture(self, source_id: str) -> str | None:
        src_panel = self.param_panel.furniture_panels.get(source_id)
        if not src_panel:
            return None
        parent_fp_id = self.param_panel._furniture_parent.get(source_id)
        if not parent_fp_id:
            return None
        self._furniture_counter += 1
        new_id = f"einr-{self._furniture_counter}"
        self.canvas.add_floor_plan(new_id)
        panel = self.param_panel.add_furniture_panel(new_id, parent_fp_id, name=src_panel.get_parameters().get('name', source_id))
        panel.from_dict(src_panel.to_dict())
        panel.le_name.setText(src_panel.get_parameters().get('name', source_id))
        if src_panel.get_parameters().get("file_path"):
            self.canvas.load_floor_plan_image(new_id, src_panel.get_parameters().get("file_path"))
        self._copy_layer_values(source_id, new_id)
        self.canvas.update()
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        return new_id

    def _duplicate_elec_point(self, source_id: str):
        src_panel = self.param_panel.elec_point_panels.get(source_id)
        if not src_panel:
            return
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._elec_point_counter += 1
        new_id = f"AP-{self._elec_point_counter}"
        panel = self._create_elec_point_panel(new_id, fp_id=src_fp_id, name=src.get('name', source_id))
        panel.sb_width.setValue(src.get("width", 30.0) / 10)
        panel.sb_height.setValue(src.get("height", 30.0) / 10)
        panel.chk_label_visible.setChecked(src.get("label_visible", True))
        panel.sb_label_size.setValue(src.get("label_size", 12.0))
        panel.set_ap_type(src.get("ap_type", "standard"))
        panel.set_uv_config(copy.deepcopy(src.get("uv_config") or {}))
        panel.set_up_distribution_config(copy.deepcopy(src.get("up_distribution_config") or {}))
        # Position und Höhe kopieren
        pos_idx = panel.cmb_position.findText(src.get("position", "Wand"))
        if pos_idx >= 0:
            panel.cmb_position.setCurrentIndex(pos_idx)
        panel.sb_height_from_floor.setValue(src.get("height_from_floor", 0.0))
        self.canvas._elec_point_position[new_id] = src.get("position", "Wand")
        self.canvas._elec_point_height[new_id] = src.get("height_from_floor", 0.0)
        self.canvas._elec_point_notes[new_id] = src.get("note", "")
        self.canvas._elec_point_smarthome_device[new_id] = src.get("smarthome_device", "")
        self.canvas._elec_point_smarthome_device_color[new_id] = src.get("smarthome_device_color", "")
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
        self.canvas.set_label_visible(new_id, src.get("label_visible", True))
        if source_id in self.canvas._elec_points:
            p = self.canvas._elec_points[source_id]
            self.canvas._elec_points[new_id] = QPointF(p.x() + 20, p.y() + 20)
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        self.canvas.update()
        return new_id

    def _duplicate_elec_cable(self, source_id: str):
        src_panel = self.param_panel.elec_cable_panels.get(source_id)
        if not src_panel:
            return
        src = src_panel.to_dict()
        src_fp_id = self.param_panel._element_floorplan.get(source_id)
        self._elec_cable_counter += 1
        new_id = f"KV-{self._elec_cable_counter}"
        panel = self._create_elec_cable_panel(new_id, fp_id=src_fp_id, name=src.get('name', source_id))
        panel.set_type_text(src.get("type", "5x1,5"))
        panel.te_comment.setPlainText(src.get("comment", ""))
        self.canvas._elec_cable_notes[new_id] = src.get("comment", "")
        panel.chk_type_label_visible.setChecked(src.get("type_label_visible", False))
        panel.sb_label_size.setValue(src.get("label_size", 12.0))
        c = src.get("color", "#ff9800")
        panel._color = QColor(c)
        panel._update_color_button()
        self.canvas._ensure_color(new_id)
        self.canvas.set_color(new_id, QColor(c))
        self.canvas.set_elec_cable_type_text(new_id, src.get("type", ""))
        self.canvas.set_elec_cable_type_label_visible(
            new_id,
            src.get("type_label_visible", False),
        )
        self.canvas.set_label_font_size(new_id, src.get("label_size", 12.0))
        self.canvas.set_label_visible(new_id, src.get("label_visible", True))
        if source_id in self.canvas._elec_cables:
            self.canvas._elec_cables[new_id] = [QPointF(p.x() + 20, p.y() + 20)
                                                for p in self.canvas._elec_cables[source_id]]
            self.canvas._cable_start_ap.pop(new_id, None)
            self.canvas._cable_end_ap.pop(new_id, None)
        self.status.showMessage(f"📋 {source_id} dupliziert → {new_id}")
        self.canvas.update()
        return new_id

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
        panel.label_visibility_changed.connect(self._on_label_visibility_changed)
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
            "zu platzieren. ESC = Abbruch, Rechtsklick = Kontextmenü")

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
        panel.label_visibility_changed.connect(self._on_label_visibility_changed)
        return panel

    def _on_draw_hkv_line(self, line_id: str):
        panel = self.param_panel.hkv_line_panels.get(line_id)
        if panel:
            self.canvas.set_color(line_id, QColor(panel._color.name()))
        self.canvas.start_draw_hkv_line(line_id)
        self.status.showMessage(
            f"{line_id}: HKV-Leitung zeichnen  |  "
            "Linksklick = Punkt  |  Enter/Doppelklick = Fertig  |  ESC = Abbruch  |  Rechtsklick = Kontextmenü")

    def _on_edit_hkv_line(self, line_id: str):
        self.canvas.start_edit_hkv_line(line_id)
        self.status.showMessage(
            "HKV-Leitung bearbeiten: Linksklick=Verschieben, "
            "Doppelklick auf Punkt=Löschen, Doppelklick auf Kante=Einfügen, "
            "Mitteltaste/ESC=Beenden, Rechtsklick=Kontextmenü.")

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

    # ── Beschriftungen (Text-Annotationen) ───────────────────────────── #

    def _add_text(self, fp_id: str = ""):
        self._text_counter += 1
        tid = f"Text-{self._text_counter}"
        self._create_text_panel(tid, fp_id=fp_id or None, name=tid)
        self.status.showMessage(
            f"{tid}: Klicke 'Platzieren' im Panel, dann auf den Plan klicken.")

    def _create_text_panel(self, text_id: str,
                           fp_id: str | None = None,
                           name: str | None = None):
        panel = self.param_panel.add_text_panel(text_id, fp_id=fp_id, name=name)
        panel.place_requested.connect(self._on_place_text)
        panel.content_changed.connect(self._on_text_content_changed)
        panel.comment_changed.connect(self._on_text_comment_changed)
        panel.font_size_changed.connect(self._on_text_font_size_changed)
        panel.color_changed.connect(self._on_text_color_changed)
        panel.visibility_changed.connect(self._on_text_visibility_changed)
        panel.name_changed.connect(self._on_text_name_changed)
        return panel

    def _on_place_text(self, text_id: str):
        panel = self.param_panel.text_panels.get(text_id)
        if not panel:
            return
        params = panel.get_parameters()
        self.canvas.start_place_text(
            text_id,
            params.get("content", "Text"),
            params.get("font_size", 14.0),
            params.get("color", "#ffffff"),
        )
        self.status.showMessage(
            f"{text_id}: Klicke auf den Plan um den Text zu platzieren. "
            "ESC = Abbruch, Rechtsklick = Kontextmenü")

    def _on_text_placed(self, text_id: str):
        self.status.showMessage(f"✅ Text {text_id} platziert.")

    def _on_text_content_changed(self, text_id: str, content: str):
        self.canvas.update_text_content(text_id, content)
        self._mark_dirty_debounced()

    def _on_text_comment_changed(self, text_id: str, comment: str):
        self.canvas.update_text_comment(text_id, comment)
        self._mark_dirty_debounced()

    def _on_text_font_size_changed(self, text_id: str, size: float):
        self.canvas.update_text_font_size(text_id, size)
        self._mark_dirty()

    def _on_text_color_changed(self, text_id: str, color: str):
        self.canvas.update_text_color(text_id, color)
        self._mark_dirty()

    def _on_text_visibility_changed(self, text_id: str, visible: bool):
        self.canvas.set_text_visible(text_id, visible)
        self._mark_dirty()

    def _on_text_name_changed(self, text_id: str, name: str):
        self._mark_dirty_debounced()

    def _delete_text(self, text_id: str):
        self.canvas.delete_text_annotation(text_id)
        self.param_panel.remove_text_panel(text_id)
        self.status.showMessage(f"🗑️ Text {text_id} gelöscht.")
        self._mark_dirty()

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

    @staticmethod
    def _normalize_symbol_key(text: str) -> str:
        return (text or "").strip().lower().replace("_", " ").replace("-", " ")

    def _migrate_legacy_ap_icon(self, ap_data: dict):
        """Migrate legacy AP icon fields to current builtin icons.

        Strategy:
        - Try matching by builtin symbol name.
        - If not possible, try matching by icon filename stem.
        - If still not possible and icon file is missing, clear icon assignment.
        """
        from gui.parameter_panel import BUILTIN_SYMBOLS

        builtin_symbol = str(ap_data.get("builtin_symbol", "") or "").strip()
        icon_path = str(ap_data.get("icon_path", "") or "").strip()

        available_labels = [k for k in BUILTIN_SYMBOLS.keys() if k != "(kein Symbol)"]
        label_by_norm = {
            self._normalize_symbol_key(label): label
            for label in available_labels
        }

        stem_by_norm: dict[str, str] = {}
        for label in available_labels:
            builtin_path = BUILTIN_SYMBOLS.get(label, "")
            if not builtin_path:
                continue
            stem_norm = self._normalize_symbol_key(Path(builtin_path).stem)
            if stem_norm and stem_norm not in stem_by_norm:
                stem_by_norm[stem_norm] = label

        match_label = ""

        norm_builtin = self._normalize_symbol_key(builtin_symbol)
        if norm_builtin in label_by_norm:
            match_label = label_by_norm[norm_builtin]
        elif norm_builtin in stem_by_norm:
            match_label = stem_by_norm[norm_builtin]

        if not match_label and icon_path:
            icon_name_norm = self._normalize_symbol_key(Path(icon_path).name)
            icon_stem_norm = self._normalize_symbol_key(Path(icon_path).stem)
            for key in (icon_name_norm, icon_stem_norm):
                if key in label_by_norm:
                    match_label = label_by_norm[key]
                    break
                if key in stem_by_norm:
                    match_label = stem_by_norm[key]
                    break

        if match_label:
            ap_data["builtin_symbol"] = match_label
            ap_data["icon_path"] = BUILTIN_SYMBOLS.get(match_label, "")
            return

        if icon_path and Path(icon_path).exists():
            ap_data["builtin_symbol"] = "(kein Symbol)"
            return

        ap_data["builtin_symbol"] = "(kein Symbol)"
        ap_data["icon_path"] = ""

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
        self._flush_pending_dirty()
        if not self._maybe_save():
            return

        # Reset state
        self._svg_path = None
        self._project_path = None
        self._circuit_counter = 0
        self._elec_point_counter = 0
        self._elec_room_counter = 0
        self._elec_cable_counter = 0
        self._hkv_counter = 0
        self._hkv_line_counter = 0
        self._floorplan_counter = 0
        self._furniture_counter = 0
        self._pdf_export_pages = self._default_pdf_export_pages()
        self._elec_schema_ap_positions = {}

        # Recreate canvas and panel
        old_canvas = self.canvas
        old_panel = self.param_panel

        self.canvas = CanvasWidget()
        self.param_panel = ParameterPanel()
        self._main_splitter.replaceWidget(0, self.canvas)
        self._main_splitter.replaceWidget(1, self.param_panel)
        self._main_splitter.setSizes([1100, 380])
        old_canvas.deleteLater()
        old_panel.deleteLater()

        self._project_ui_state = {}

        self._connect_signals()
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._last_snapshot = self._capture_snapshot()
        self._update_undo_actions()
        self._update_title()
        self.status.showMessage("📄 Neues Projekt erstellt.")

    # -- save ---------------------------------------------------------- #

    def _save_project(self):
        """Save to the current project path, or prompt if none set."""
        self._flush_pending_dirty()
        if not self._project_path:
            self._save_project_as()
            return
        self._write_project(self._project_path)

    def _save_project_as(self):
        self._flush_pending_dirty()
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
        # Ensure the new schematic model exists; infer from legacy data if missing.
        params["elec_schematic"] = sanitize_elec_schematic(
            params.get("elec_schematic") or infer_elec_schematic_from_legacy(params)
        )
        self._project_ui_state = self._collect_project_ui_state()
        params[_PARAMS_UI_STATE_KEY] = dict(self._project_ui_state)
        params["elec_schema_ap_positions"] = self._sanitize_elec_schema_ap_positions(
            self._elec_schema_ap_positions
        )

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
            "pdf_export_pages": self._pdf_export_pages,
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

            # Ensure project load always starts from a clean state
            self.canvas.clear_data()
            self.param_panel.clear_all_panels()
            self._svg_path = ""
            self._circuit_counter = 0
            self._elec_point_counter = 0
            self._elec_room_counter = 0
            self._elec_cable_counter = 0
            self._hkv_counter = 0
            self._hkv_line_counter = 0
            self._text_counter = 0
            self._floorplan_counter = 0
            self._furniture_counter = 0

            # --- resolve svg_path relative to project dir ---------------
            svg_rel = data.get("svg_path", "")
            svg_abs = self._to_absolute(svg_rel, project_dir)
            if svg_abs and Path(svg_abs).exists():
                self._svg_path = svg_abs
                self.canvas.load_svg(self._svg_path)
                self._fit_window_to_svg()

            canvas_data = data.get("canvas", {})
            self.canvas.from_dict(canvas_data)
            self._pdf_export_pages = self._normalize_pdf_export_pages(
                data.get("pdf_export_pages")
            )

            # --- resolve floorplan file paths + load images -------------
            params = data.get("params", {})
            # Backward compatible migration into the new schematic model.
            params["elec_schematic"] = sanitize_elec_schematic(
                params.get("elec_schematic") or infer_elec_schematic_from_legacy(params)
            )
            self._project_ui_state = dict(params.get(_PARAMS_UI_STATE_KEY, {})) if isinstance(params.get(_PARAMS_UI_STATE_KEY, {}), dict) else {}
            self._elec_schema_ap_positions = self._sanitize_elec_schema_ap_positions(
                params.get("elec_schema_ap_positions", {})
            )
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
                self._migrate_legacy_ap_icon(pdata)

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
                if layer:
                    p = panel.get_parameters()
                    self.canvas.set_floor_plan_polygon_color(
                        fur_id, p.get("polygon_color", "#8d99ae")
                    )
                if layer and layer.polygon and not panel._file_path:
                    panel.set_polygon_source()
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
                panel.label_visibility_changed.connect(self._on_label_visibility_changed)
                panel.hydraulics_param_changed.connect(self._recalc_circuit_hydraulics)
                values = panel.get_parameters()
                self.canvas.set_polygon_name(cid, values["name"])
                self.canvas.set_color(cid, QColor(values["color"]))
                self.canvas._circuit_visible[cid] = values.get("visible", True)
                self.canvas.set_label_font_size(cid, values.get("label_size", 12.0))
                self.canvas.set_label_visible(cid, values.get("label_visible", True))
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
                panel.label_visibility_changed.connect(self._on_label_visibility_changed)
                panel.position_changed.connect(self._on_elec_point_position_changed)
                panel.height_changed.connect(self._on_elec_point_height_changed)
                panel.ap_type_changed.connect(self._on_elec_point_ap_type_changed)
                panel.uv_config_changed.connect(self._on_elec_point_uv_config_changed)
                panel.up_distribution_changed.connect(self._on_elec_point_up_distribution_changed)
                values = panel.get_parameters()
                self.canvas._label_map[pid] = values.get("name", pid)
                self.canvas._elec_visible[pid] = values.get("visible", True)
                self.canvas._elec_point_position[pid] = values.get("position", "Wand")
                self.canvas._elec_point_height[pid] = values.get("height_from_floor", 0.0)
                self.canvas.set_label_font_size(pid, values.get("label_size", 12.0))
                self.canvas.set_label_visible(pid, values.get("label_visible", True))
                self.canvas.set_color(pid, QColor(values.get("color", "#4fc3f7")))
                if values.get("icon_path"):
                    self.canvas.set_elec_point_icon(pid, values["icon_path"])
                try:
                    num = int(pid.split("-")[1])
                    self._elec_point_counter = max(self._elec_point_counter, num)
                except (IndexError, ValueError):
                    pass

            for rid, panel in self.param_panel.elec_room_panels.items():
                panel.draw_requested.connect(self._on_draw_elec_room)
                panel.edit_requested.connect(self._on_edit_elec_room)
                panel.name_changed.connect(self._on_elec_room_name_changed)
                panel.color_changed.connect(self._on_elec_room_color_changed)
                panel.visibility_changed.connect(self._on_elec_room_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                panel.label_visibility_changed.connect(self._on_label_visibility_changed)
                values = panel.get_parameters()
                self.canvas._label_map[rid] = values.get("name", rid)
                self.canvas._elec_room_visible[rid] = values.get("visible", True)
                self.canvas.set_label_font_size(rid, values.get("label_size", 12.0))
                self.canvas.set_label_visible(rid, values.get("label_visible", True))
                self.canvas.set_color(rid, QColor(values.get("color", "#43aa8b")))
                try:
                    num = int(rid.split("-")[1])
                    self._elec_room_counter = max(self._elec_room_counter, num)
                except (IndexError, ValueError):
                    pass

            for kid, panel in self.param_panel.elec_cable_panels.items():
                panel.draw_cable_requested.connect(self._on_draw_elec_cable)
                panel.edit_cable_requested.connect(self._on_edit_elec_cable)
                panel.name_changed.connect(self._on_elec_cable_name_changed)
                panel.color_changed.connect(self._on_elec_cable_color_changed)
                panel.type_changed.connect(self._on_elec_cable_type_changed)
                panel.visibility_changed.connect(self._on_elec_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                panel.label_visibility_changed.connect(self._on_label_visibility_changed)
                panel.type_label_visibility_changed.connect(
                    self._on_elec_cable_type_label_visibility_changed
                )
                panel.stroke_width_changed.connect(self._on_elec_cable_stroke_width_changed)
                values = panel.get_parameters()
                self.canvas._label_map[kid] = values.get("name", kid)
                self.canvas._elec_visible[kid] = values.get("visible", True)
                self.canvas.set_elec_cable_type_text(kid, values.get("type", ""))
                self.canvas.set_elec_cable_type_label_visible(
                    kid,
                    values.get("type_label_visible", False),
                )
                self.canvas.set_label_font_size(kid, values.get("label_size", 12.0))
                self.canvas.set_label_visible(kid, values.get("label_visible", True))
                self.canvas.set_color(kid, QColor(values.get("color", "#ff9800")))
                self.canvas.set_elec_cable_stroke_width(kid, values.get("stroke_width", 2.0))
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

            self._update_up_distribution_cable_choices_all()

            # HKV panels
            for hid, panel in self.param_panel.hkv_panels.items():
                panel.place_requested.connect(self._on_place_hkv)
                panel.size_changed.connect(self._on_hkv_size_changed)
                panel.icon_changed.connect(self._on_hkv_icon_changed)
                panel.name_changed.connect(self._on_hkv_name_changed)
                panel.color_changed.connect(self._on_hkv_color_changed)
                panel.visibility_changed.connect(self._on_hkv_visibility_changed)
                panel.label_size_changed.connect(self._on_label_size_changed)
                panel.label_visibility_changed.connect(self._on_label_visibility_changed)
                values = panel.get_parameters()
                self.canvas._label_map[hid] = values.get("name", hid)
                self.canvas._hkv_visible[hid] = values.get("visible", True)
                self.canvas.set_label_font_size(hid, values.get("label_size", 12.0))
                self.canvas.set_label_visible(hid, values.get("label_visible", True))
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
                panel.label_visibility_changed.connect(self._on_label_visibility_changed)
                values = panel.get_parameters()
                self.canvas._label_map[lid] = values.get("name", lid)
                self.canvas._hkv_line_visible[lid] = values.get("visible", True)
                self.canvas.set_label_font_size(lid, values.get("label_size", 12.0))
                self.canvas.set_label_visible(lid, values.get("label_visible", True))
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

            self._update_elec_point_room_assignments()
            self._apply_project_ui_state(self._project_ui_state)

            # Text annotation panels
            for tid, panel in self.param_panel.text_panels.items():
                panel.place_requested.connect(self._on_place_text)
                panel.content_changed.connect(self._on_text_content_changed)
                panel.comment_changed.connect(self._on_text_comment_changed)
                panel.font_size_changed.connect(self._on_text_font_size_changed)
                panel.color_changed.connect(self._on_text_color_changed)
                panel.visibility_changed.connect(self._on_text_visibility_changed)
                panel.name_changed.connect(self._on_text_name_changed)
                values = panel.get_parameters()
                self.canvas._text_visible[tid] = values.get("visible", True)
                self.canvas._text_contents[tid] = values.get("content", "Text")
                self.canvas._text_font_sizes[tid] = values.get("font_size", 14.0)
                self.canvas._text_colors[tid] = values.get("color", "#ffffff")
                self.canvas._text_comments[tid] = values.get("comment", "")
                try:
                    num = int(tid.split("-")[1])
                    self._text_counter = max(self._text_counter, num)
                except (IndexError, ValueError):
                    pass

            # Remember as last project
            _SETTINGS.setValue(_LAST_PROJECT_KEY, str(filepath))
            self._add_to_recent(filepath)
            self._dirty = False
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._last_snapshot = self._capture_snapshot()
            self._update_undo_actions()
            self._update_title()
            self.status.showMessage(f"📂 Projekt geladen: {filepath.name}")
        except Exception as e:
            self.status.showMessage(f"⚠️ Fehler beim Laden: {e}")

    # ------------------------------------------------------------------ #
    #  Export                                                              #
    # ------------------------------------------------------------------ #

    def _floor_plan_display_name(self, floor_plan_id: str) -> str:
        panel = self.param_panel.floorplan_panels.get(floor_plan_id)
        if panel:
            name = (panel.le_name.text() or "").strip()
            if name:
                return name
        return floor_plan_id

    @staticmethod
    def _default_pdf_element_visibility() -> dict:
        return {
            "background": True,
            "furniture": True,
            "hk": True,
            "hkv": True,
            "hkv_line": True,
            "ap": True,
            "room": True,
            "kv": True,
            "text": True,
        }

    @staticmethod
    def _default_pdf_table_sections(ptype: str) -> list[str]:
        if ptype == "heating":
            return ["hk_lengths", "hk_hydraulics", "hk_hkv_lines"]
        if ptype == "elektro":
            return [
                "el_kabel", "el_ap_types", "el_ap_connections", "el_rooms",
                "el_ap_infos", "el_uv", "el_up_distribution", "el_bom", "el_uv_busbars",
                "schaltplan_uv", "schaltplan_stromkreise", "schaltplan_hierarchie",
            ]
        return []

    def _default_pdf_export_pages(self) -> list[dict]:
        pages: list[dict] = [
            {
                "id": "overview-all",
                "type": "plan",
                "title": "Gesamtübersicht – Alle Elemente",
                "enabled": True,
                "show_background": True,
                "show_heating": True,
                "show_elektro": True,
                "floor_plan_id": None,
                "source_rect": None,
            },
            {
                "id": "plan-heating",
                "type": "heating",
                "title": "Fußbodenheizung – Verlegeplan",
                "enabled": True,
                "show_background": True,
                "show_heating": True,
                "show_elektro": False,
                "element_visibility": {
                    **self._default_pdf_element_visibility(),
                    "ap": False,
                    "room": False,
                    "kv": False,
                },
                "table_sections": self._default_pdf_table_sections("heating"),
                "floor_plan_id": None,
                "source_rect": None,
            },
            {
                "id": "table-lengths",
                "type": "lengths",
                "title": "Heizkreise – Rohrlängen",
                "enabled": True,
            },
            {
                "id": "table-hydraulics",
                "type": "hydraulics",
                "title": "Hydraulische Übersicht & Abgleich",
                "enabled": True,
            },
            {
                "id": "page-elektro",
                "type": "elektro",
                "title": "Elektro – Übersicht",
                "enabled": True,
                "show_background": True,
                "show_heating": False,
                "show_elektro": True,
                "element_visibility": {
                    **self._default_pdf_element_visibility(),
                    "hk": False,
                    "hkv": False,
                    "hkv_line": False,
                },
                "table_sections": self._default_pdf_table_sections("elektro"),
                "floor_plan_id": None,
                "source_rect": None,
            },
        ]

        for fid in self.canvas._floor_plan_order:
            layer = self.canvas._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            pages.append({
                "id": f"floor-{fid}",
                "type": "plan",
                "title": f"Grundriss – {self._floor_plan_display_name(fid)}",
                "enabled": True,
                "show_background": True,
                "show_heating": True,
                "show_elektro": True,
                "element_visibility": self._default_pdf_element_visibility(),
                "floor_plan_id": fid,
                "source_rect": None,
            })
        return pages

    def _normalize_pdf_export_pages(self, pages: list[dict] | None) -> list[dict]:
        if not pages:
            return self._default_pdf_export_pages()

        normalized: list[dict] = []
        for index, src in enumerate(pages):
            if not isinstance(src, dict):
                continue
            ptype = str(src.get("type", "plan")).strip().lower()
            if ptype == "plan" and str(src.get("id", "")) == "plan-heating":
                ptype = "heating"
            if ptype not in ("plan", "heating", "lengths", "hydraulics", "elektro"):
                continue

            page = {
                "id": str(src.get("id") or f"page-{index + 1}"),
                "type": ptype,
                "title": str(src.get("title") or "Seite"),
                "enabled": bool(src.get("enabled", True)),
            }

            if ptype in ("plan", "heating", "elektro"):
                page["show_background"] = bool(src.get("show_background", True))
                page["show_heating"] = bool(src.get("show_heating", True))
                page["show_elektro"] = bool(src.get("show_elektro", True))
                vis_src = src.get("element_visibility")
                default_vis = self._default_pdf_element_visibility()
                if isinstance(vis_src, dict):
                    for key in list(default_vis.keys()):
                        default_vis[key] = bool(vis_src.get(key, default_vis[key]))
                # Enforce core groups for page type
                if ptype == "heating":
                    default_vis["hk"] = True
                    default_vis["hkv"] = True
                    default_vis["hkv_line"] = True
                elif ptype == "elektro":
                    default_vis["ap"] = True
                    default_vis["room"] = True
                    default_vis["kv"] = True
                page["element_visibility"] = default_vis

                if ptype in ("heating", "elektro"):
                    allowed = set(self._default_pdf_table_sections(ptype))
                    sections_src = src.get("table_sections")
                    if isinstance(sections_src, list):
                        sections = [str(v) for v in sections_src if str(v) in allowed]
                    else:
                        sections = self._default_pdf_table_sections(ptype)
                    page["table_sections"] = sections
                floor_plan_id = src.get("floor_plan_id")
                page["floor_plan_id"] = floor_plan_id if floor_plan_id else None

                rect = src.get("source_rect")
                if (isinstance(rect, (list, tuple)) and len(rect) == 4):
                    try:
                        rx, ry, rw, rh = [float(v) for v in rect]
                        if rw > 0 and rh > 0:
                            page["source_rect"] = [rx, ry, rw, rh]
                        else:
                            page["source_rect"] = None
                    except (TypeError, ValueError):
                        page["source_rect"] = None
                else:
                    page["source_rect"] = None

            normalized.append(page)

        if not normalized:
            return self._default_pdf_export_pages()
        return normalized

    def _current_floor_plans_for_export_dialog(self) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for fid in self.canvas._floor_plan_order:
            result.append((fid, self._floor_plan_display_name(fid)))
        return result

    @staticmethod
    def _page_source_rect(page: dict | None) -> QRectF | None:
        if not page:
            return None
        rect = page.get("source_rect")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            return None
        try:
            x, y, w, h = [float(v) for v in rect]
        except (TypeError, ValueError):
            return None
        if w <= 0 or h <= 0:
            return None
        return QRectF(x, y, w, h).normalized()

    def _effective_pdf_source_rect(self, page: dict | None) -> QRectF:
        """Determine the source rectangle for a PDF page.

        Priority:
        1. Explicit source_rect stored in the page configuration
        2. Canvas export frame (drawn by the user on the canvas)
        3. Default computed from visible floor plan bounds
        """
        default_rect = self._default_source_rect()

        # 1. Check page-level source_rect
        custom = self._page_source_rect(page)

        # 2. Fallback to canvas export frame
        if custom is None:
            canvas_frame = self.canvas.get_export_frame()
            if canvas_frame is not None:
                nr = QRectF(canvas_frame).normalized()
                if nr.width() > 0 and nr.height() > 0:
                    custom = nr

        if custom is None:
            return default_rect

        # Use the custom rect directly (it defines the user's desired crop)
        if custom.width() > 0 and custom.height() > 0:
            return custom

        return default_rect

    def _open_pdf_export_config_dialog(self, on_accept=None):
        if self._pdf_export_dialog is not None:
            self._pdf_export_dialog.raise_()
            self._pdf_export_dialog.activateWindow()
            return

        dialog = PdfExportConfigDialog(
            pages=self._normalize_pdf_export_pages(self._pdf_export_pages),
            floor_plans=self._current_floor_plans_for_export_dialog(),
            svg_size=self.canvas._svg_size,
            canvas=self.canvas,
            parent=self,
        )
        dialog.setModal(False)
        dialog.setWindowModality(Qt.NonModal)
        self._restore_window_state(dialog, self._project_ui_state.get("pdf_export_dialog", {}))
        self._pdf_export_dialog = dialog

        def _cleanup():
            if self._pdf_export_dialog is dialog:
                self._pdf_export_dialog = None
            dialog.deleteLater()

        def _accepted():
            pages = self._normalize_pdf_export_pages(dialog.get_pages())
            self._project_ui_state["pdf_export_dialog"] = self._capture_window_state(dialog)
            _cleanup()
            if on_accept:
                on_accept(pages)

        def _rejected():
            self._project_ui_state["pdf_export_dialog"] = self._capture_window_state(dialog)
            _cleanup()

        dialog.accepted.connect(_accepted)
        dialog.rejected.connect(_rejected)
        dialog.open()

    def _render_plan_to_painter(self, painter: QPainter,
                                target_rect: QRectF,
                                layer: str = "all",
                                floor_plan_id: str | None = None,
                                source_rect: QRectF | None = None,
                                show_background: bool | None = None,
                                show_heating: bool | None = None,
                                show_elektro: bool | None = None,
                                element_visibility: dict | None = None,
                                rasterize: bool = False):
        """Render the floor plan with overlays directly onto *painter*.

        *target_rect* – the rectangle within the painter to draw into.
        *layer* – ``"all"`` | ``"heating"`` | ``"elektro"``
        *floor_plan_id* – if given, render only this floor plan as background.
        """
        if rasterize:
            iw = max(1, int(round(target_rect.width())))
            ih = max(1, int(round(target_rect.height())))
            iw, ih, _ = self._clamp_raster_size(iw, ih)
            img = QImage(iw, ih, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            ip = QPainter(img)
            ip.setRenderHint(QPainter.Antialiasing)
            self._render_plan_to_painter(
                ip,
                QRectF(0, 0, iw, ih),
                layer=layer,
                floor_plan_id=floor_plan_id,
                source_rect=source_rect,
                show_background=show_background,
                show_heating=show_heating,
                show_elektro=show_elektro,
                element_visibility=element_visibility,
                rasterize=False,
            )
            ip.end()
            painter.drawImage(target_rect, img)
            return

        full_src = self._default_source_rect()
        if source_rect:
            src = QRectF(source_rect.normalized())
            if src.width() <= 0 or src.height() <= 0:
                src = full_src
        else:
            src = full_src
        if src.width() <= 0 or src.height() <= 0:
            return

        # Scale SVG to fill the target rect as much as possible while
        # preserving aspect ratio (fit to best dimension).
        sx = target_rect.width() / src.width()
        sy = target_rect.height() / src.height()
        scale = min(sx, sy)
        scaled_w = src.width() * scale
        scaled_h = src.height() * scale
        ox = target_rect.x() + (target_rect.width() - scaled_w) / 2
        oy = target_rect.y() + (target_rect.height() - scaled_h) / 2

        painter.save()
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        painter.translate(-src.x(), -src.y())

        # Helper: create a font that looks correct in the scaled SVG
        # coordinate system.  We use setPixelSize so the size is in SVG
        # pixels, not device points.
        def _svg_font(pixel_size: float) -> QFont:
            f = QFont("Arial")
            f.setPixelSize(max(4, int(pixel_size)))
            return f

        group_vis = element_visibility or {}

        def _group_visible(key: str, default: bool = True) -> bool:
            return bool(group_vis.get(key, default))

        # Background: floor plan layers
        ref_mpp = self.canvas._mm_per_px if self.canvas._mm_per_px > 0 else 1.0
        rendered_floor = False
        if show_background is None:
            show_background = True
        if show_background:
            fp_ids = [floor_plan_id] if floor_plan_id else self.canvas._floor_plan_order
            for fid in fp_ids:
                fp_layer = self.canvas._floor_plans.get(fid)
                if not fp_layer or not fp_layer.visible:
                    continue
                is_furniture = fid in self.param_panel.furniture_panels
                if is_furniture and not _group_visible("furniture"):
                    continue
                if (not is_furniture) and not _group_visible("background"):
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

        if show_heating is None:
            show_heating = layer in ("all", "heating")
        if show_elektro is None:
            show_elektro = layer in ("all", "elektro")

        # ── Heating elements ──────────────────────────────────────
        if show_heating:
            if _group_visible("hk"):
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
                    if not self.canvas._label_visible.get(cid, True):
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

            if _group_visible("hkv"):
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
                    if not self.canvas._label_visible.get(hid, True):
                        continue
                    label = self.canvas._label_map.get(hid, hid)
                    font_size = self.canvas._label_font_sizes.get(hid, 10.0)
                    painter.setFont(_svg_font(font_size))
                    painter.setPen(QPen(color))
                    painter.drawText(
                        QPointF(pos.x() - w / 4,
                                pos.y() + h / 2 + font_size + 2),
                        label)

            if _group_visible("hkv_line"):
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
                    if not self.canvas._label_visible.get(lid, True):
                        continue
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
            if _group_visible("room"):
                for rid, pts in self.canvas._elec_room_polygons.items():
                    if not self.canvas._elec_visible.get(rid, True):
                        continue
                    if len(pts) < 3:
                        continue
                    color = self.canvas._color_map.get(rid, QColor("#43aa8b"))
                    fill = QColor(color)
                    fill.setAlpha(35)
                    painter.setBrush(QBrush(fill))
                    painter.setPen(QPen(color, 2.0))
                    painter.drawPolygon(QPolygonF(pts))

            if _group_visible("ap"):
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

                    # Always draw border rect (like on canvas)
                    painter.setBrush(QBrush(fill))
                    painter.setPen(QPen(color, 2.0))
                    painter.drawRect(QRectF(x, y, ew, eh))

                    # Draw icon/SVG on top
                    svg_r = self.canvas._elec_point_svgs.get(pid)
                    icon_pm = self.canvas._elec_point_icons.get(pid)
                    if svg_r and svg_r.isValid():
                        svg_r.render(painter, QRectF(x, y, ew, eh))
                    elif icon_pm and not icon_pm.isNull():
                        painter.drawPixmap(QRectF(x, y, ew, eh),
                                           icon_pm,
                                           QRectF(icon_pm.rect()))

                    if not self.canvas._label_visible.get(pid, True):
                        continue

                    label = self.canvas._label_map.get(pid, pid)
                    font_size = self.canvas._label_font_sizes.get(pid, 10.0)
                    painter.setFont(_svg_font(font_size))
                    painter.setPen(QPen(color))
                    painter.drawText(
                        QPointF(pos.x() - ew / 4,
                                pos.y() + eh / 2 + font_size + 2),
                        label,
                    )

            if _group_visible("kv"):
                # Kabelverbindungen
                for kid, pts in self.canvas._elec_cables.items():
                    if not self.canvas._elec_visible.get(kid, True):
                        continue
                    if len(pts) < 2:
                        continue
                    color = self.canvas._color_map.get(kid, QColor("#ff9800"))
                    rounding = 8.0
                    qpath = self.canvas._smooth_polyline_path(pts, rounding)
                    sw = self.canvas._elec_cable_stroke_width.get(kid, 2.0)
                    painter.setPen(QPen(color, sw, Qt.SolidLine,
                                        Qt.RoundCap, Qt.RoundJoin))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawPath(qpath)

                    if not self.canvas._label_visible.get(kid, True):
                        continue

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

        if _group_visible("text"):
            for tid, pos in self.canvas._text_annotations.items():
                if not self.canvas._text_visible.get(tid, True):
                    continue
                content = self.canvas._text_contents.get(tid, "")
                if not content:
                    continue
                color = QColor(self.canvas._text_colors.get(tid, "#ffffff"))
                font_size = self.canvas._text_font_sizes.get(tid, 12.0)
                painter.setFont(_svg_font(font_size))
                painter.setPen(QPen(color))
                painter.drawText(pos, content)

        helper_font = _svg_font(10.0)
        helper_floor_ids = [floor_plan_id] if floor_plan_id else self.canvas._floor_plan_order
        for fid in helper_floor_ids:
            fp_layer = self.canvas._floor_plans.get(fid)
            if not fp_layer or not fp_layer.visible:
                continue
            if not self.canvas.get_helper_line_visible(fid):
                continue
            helper_pen = QPen(
                QColor(self.canvas.get_helper_line_color(fid)),
                self.canvas.get_helper_line_width(fid),
                self.canvas._helper_line_pen_style(self.canvas.get_helper_line_style(fid)),
            )
            visible_map = self.canvas._floor_helper_line_visible.get(fid, {})
            for hid, pts in self.canvas._floor_helper_lines.get(fid, {}).items():
                if not visible_map.get(hid, True) or len(pts) < 2:
                    continue
                p1, p2 = pts[0], pts[1]
                painter.setPen(helper_pen)
                painter.setBrush(QBrush(helper_pen.color()))
                painter.drawLine(p1, p2)
                painter.drawEllipse(p1, 2.5, 2.5)
                painter.drawEllipse(p2, 2.5, 2.5)
                px_len = ((p2.x() - p1.x()) ** 2 + (p2.y() - p1.y()) ** 2) ** 0.5
                mm_len = px_len * self.canvas._mm_per_px if self.canvas._mm_per_px > 0 else 0.0
                mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2 - 8.0)
                painter.setFont(helper_font)
                painter.drawText(mid, f"{mm_len / 1000:.3f} m")

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
            sw = self.canvas._elec_cable_stroke_width.get(kid, 2.0)
            if svg_d:
                lines.append(
                    f'  <path d="{svg_d}" fill="none" '
                    f'stroke="{color_str}" stroke-width="{sw}" '
                    f'stroke-linejoin="round" stroke-linecap="round"/>'
                )

        return lines

    def _default_source_rect(self) -> QRectF:
        floor_xs: list[float] = []
        floor_ys: list[float] = []
        overlay_xs: list[float] = []
        overlay_ys: list[float] = []

        def _add_floor_point(x: float, y: float):
            floor_xs.append(float(x))
            floor_ys.append(float(y))

        def _add_overlay_point(x: float, y: float):
            overlay_xs.append(float(x))
            overlay_ys.append(float(y))

        # Floor plan / furniture layers (with transform)
        ref_mpp = self.canvas._mm_per_px if self.canvas._mm_per_px > 0 else 1.0
        for fid in self.canvas._floor_plan_order:
            layer = self.canvas._floor_plans.get(fid)
            if not layer or not layer.visible:
                continue
            w, h = layer.size
            ls = layer.mm_per_px / ref_mpp if layer.mm_per_px > 0 else 1.0
            sw, sh = w * ls, h * ls
            cx = sw / 2 + layer.offset_x
            cy = sh / 2 + layer.offset_y
            rad = math.radians(layer.rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            for dx, dy in [(-sw / 2, -sh / 2), (sw / 2, -sh / 2), (sw / 2, sh / 2), (-sw / 2, sh / 2)]:
                rx = dx * cos_r - dy * sin_r
                ry = dx * sin_r + dy * cos_r
                _add_floor_point(cx + rx, cy + ry)

        # Polyline/polygon based overlays
        for collection in (
            self.canvas._polygons,
            self.canvas._manual_routes,
            self.canvas._supply_lines,
            self.canvas._elec_room_polygons,
            self.canvas._elec_cables,
            self.canvas._hkv_lines,
        ):
            for pts in collection.values():
                for p in pts:
                    _add_overlay_point(p.x(), p.y())

        # Point-based overlays
        for collection in (
            self.canvas._start_points,
            self.canvas._elec_points,
            self.canvas._hkv_points,
            self.canvas._text_annotations,
        ):
            for p in collection.values():
                _add_overlay_point(p.x(), p.y())

        def _rect_from_points(xs: list[float], ys: list[float], pad: float) -> QRectF | None:
            if not xs or not ys:
                return None
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            w = max(1.0, max_x - min_x)
            h = max(1.0, max_y - min_y)
            return QRectF(min_x - pad, min_y - pad, w + 2 * pad, h + 2 * pad)

        # Primary: fit to visible floor/furniture layers (stable for plan exports)
        floor_rect = _rect_from_points(floor_xs, floor_ys, pad=20.0)
        if floor_rect is not None:
            return floor_rect

        # Fallback: overlays only (projects without floor plan image)
        overlay_rect = _rect_from_points(overlay_xs, overlay_ys, pad=20.0)
        if overlay_rect is not None:
            return overlay_rect

        w, h = self.canvas._svg_size
        return QRectF(0.0, 0.0, max(1.0, float(w)), max(1.0, float(h)))

    @staticmethod
    def _clamp_raster_size(width_px: int,
                           height_px: int,
                           max_dim: int = 6000,
                           max_pixels: int = 20_000_000) -> tuple[int, int, bool]:
        """Clamp raster size to safe limits and return (w, h, was_clamped)."""
        w = max(1, int(width_px))
        h = max(1, int(height_px))

        s_dim = min(1.0, max_dim / max(w, h))
        s_pix = min(1.0, math.sqrt(max_pixels / max(1.0, float(w * h))))
        s = min(s_dim, s_pix)

        if s >= 0.9999:
            return w, h, False

        cw = max(1, int(round(w * s)))
        ch = max(1, int(round(h * s)))
        return cw, ch, True

    def _render_plan_to_image(self,
                              width_px: int,
                              height_px: int,
                              layer: str = "all",
                              source_rect: QRectF | None = None,
                              show_background: bool | None = None,
                              show_heating: bool | None = None,
                              show_elektro: bool | None = None,
                              element_visibility: dict | None = None,
                              floor_plan_id: str | None = None) -> QImage:
        """Rasterize current plan/crop to an in-memory image."""
        rw, rh, _ = self._clamp_raster_size(width_px, height_px)
        img = QImage(max(1, rw), max(1, rh),
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self._render_plan_to_painter(
            p,
            QRectF(0, 0, img.width(), img.height()),
            layer=layer,
            floor_plan_id=floor_plan_id,
            source_rect=source_rect or self._default_source_rect(),
            show_background=show_background,
            show_heating=show_heating,
            show_elektro=show_elektro,
            element_visibility=element_visibility,
        )
        p.end()
        return img

    def _write_plan_svg(self,
                        path: str,
                        source_rect: QRectF | None = None,
                        show_background: bool | None = None,
                        show_heating: bool | None = None,
                        show_elektro: bool | None = None,
                        floor_plan_id: str | None = None):
        """Write the complete plan as SVG image using current export frame crop."""
        import base64

        src = source_rect or self._default_source_rect()
        w = max(1, int(round(src.width())))
        h = max(1, int(round(src.height())))
        w, h, _ = self._clamp_raster_size(w, h)

        img = self._render_plan_to_image(
            w,
            h,
            layer="all",
            source_rect=src,
            show_background=show_background,
            show_heating=show_heating,
            show_elektro=show_elektro,
            floor_plan_id=floor_plan_id,
        )
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        png_b64 = base64.b64encode(bytes(ba)).decode("ascii")

        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
            f'  <image href="data:image/png;base64,{png_b64}" '
            f'x="0" y="0" width="{w}" height="{h}"/>',
        ]
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

    @staticmethod
    def _is_uv_type(params: dict) -> bool:
        return str(params.get("ap_type", "standard")).strip().lower() == "uv"

    @staticmethod
    def _is_up_distribution_type(params: dict) -> bool:
        return str(params.get("ap_type", "standard")).strip().lower() == "up_distribution"

    @staticmethod
    def _describe_ap_type(params: dict) -> str:
        if MainWindow._is_uv_type(params):
            return "Unterverteilung (UV)"
        if MainWindow._is_up_distribution_type(params):
            return "Verteilung in Unterputzdose"
        symbol = (params.get("builtin_symbol") or "").strip()
        icon_path = (params.get("icon_path") or "").strip()

        if symbol and symbol != "(kein Symbol)":
            return symbol
        if icon_path:
            return Path(icon_path).stem.replace("_", " ").replace("-", " ").strip() or "Eigenes Symbol"
        return "(kein Symbol)"

    def _collect_point_id_to_room_name(self) -> dict[str, str]:
        point_id_to_room_name: dict[str, str] = {}
        for pid in self.param_panel.elec_point_panels:
            room_name = "(ohne Raum)"
            point = self.canvas._elec_points.get(pid)
            point_fp_id = self.param_panel._element_floorplan.get(pid, "")
            if point is not None:
                for rid, poly in self.canvas._elec_room_polygons.items():
                    if len(poly) < 3:
                        continue
                    room_fp_id = self.param_panel._element_floorplan.get(rid, "")
                    if point_fp_id != room_fp_id:
                        continue
                    if self.canvas._point_in_polygon(point, poly):
                        room_panel = self.param_panel.elec_room_panels.get(rid)
                        room_name = room_panel.get_parameters().get("name", rid) if room_panel else rid
                        break
            point_id_to_room_name[pid] = room_name
        return point_id_to_room_name

    def _collect_uv_rows(self, point_id_to_room_name: dict[str, str] | None = None) -> list[dict]:
        point_id_to_room_name = point_id_to_room_name or self._collect_point_id_to_room_name()
        rows: list[dict] = []
        for pid, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            if not self._is_uv_type(params):
                continue
            uv_config = params.get("uv_config") or {}
            try:
                uv_rows = int(uv_config.get("rows", 0) or 0)
            except (TypeError, ValueError):
                uv_rows = 0
            try:
                uv_modules = int(uv_config.get("modules_per_row", 0) or 0)
            except (TypeError, ValueError):
                uv_modules = 0
            ap_name = (params.get("name") or pid).strip() or pid
            room_name = point_id_to_room_name.get(pid, "(ohne Raum)")
            slots = uv_config.get("slots", [])
            if not isinstance(slots, list):
                slots = []
            normalized_slots = sorted(
                [
                    {
                        "row": int(slot.get("row", 0) or 0),
                        "slot": int(slot.get("slot", 0) or 0),
                        "device_type": str(slot.get("device_type", "") or "").strip(),
                        "te_size": max(1, int(slot.get("te_size", 1) or 1)),
                        "spec": str(slot.get("spec", "") or "").strip(),
                        "label": str(slot.get("label", "") or "").strip(),
                        "assignment": str(slot.get("assignment", "") or "").strip(),
                        "manufacturer": str(slot.get("manufacturer", "") or "").strip(),
                        "article_number": str(slot.get("article_number", "") or "").strip(),
                        "note": str(slot.get("note", "") or "").strip(),
                    }
                    for slot in slots if isinstance(slot, dict)
                ],
                key=lambda slot: (slot["row"], slot["slot"]),
            )
            if not normalized_slots:
                rows.append({
                    "ap": ap_name,
                    "room": room_name,
                    "rows": uv_rows,
                    "modules_per_row": uv_modules,
                    "row": "",
                    "slot": "",
                    "device_type": "",
                    "te_size": 1,
                    "spec": "",
                    "label": "",
                    "assignment": "",
                    "note": "",
                })
                continue
            for slot in normalized_slots:
                rows.append({
                    "ap": ap_name,
                    "room": room_name,
                    "rows": uv_rows,
                    "modules_per_row": uv_modules,
                    "row": slot["row"],
                    "slot": slot["slot"],
                    "device_type": slot["device_type"],
                    "te_size": slot["te_size"],
                    "spec": slot["spec"],
                    "label": slot["label"],
                    "assignment": slot["assignment"],
                    "manufacturer": slot["manufacturer"],
                    "article_number": slot["article_number"],
                    "note": slot["note"],
                })
        return rows

    def _collect_uv_data(self, point_id_to_room_name: dict[str, str] | None = None) -> list[dict]:
        """Returns one dict per UV AP (not flattened), for visual PDF rendering."""
        point_id_to_room_name = point_id_to_room_name or self._collect_point_id_to_room_name()
        result: list[dict] = []
        for pid, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            if not self._is_uv_type(params):
                continue
            uv_config = params.get("uv_config") or {}
            try:
                uv_rows = int(uv_config.get("rows", 0) or 0)
            except (TypeError, ValueError):
                uv_rows = 0
            try:
                uv_modules = int(uv_config.get("modules_per_row", 0) or 0)
            except (TypeError, ValueError):
                uv_modules = 0
            ap_name = (params.get("name") or pid).strip() or pid
            room_name = point_id_to_room_name.get(pid, "(ohne Raum)")
            slots_raw = uv_config.get("slots", [])
            if not isinstance(slots_raw, list):
                slots_raw = []
            slots = sorted(
                [
                    {
                        "row": int(s.get("row", 0) or 0),
                        "slot": int(s.get("slot", 0) or 0),
                        "device_type": str(s.get("device_type", "") or "").strip(),
                        "te_size": max(1, int(s.get("te_size", 1) or 1)),
                        "spec": str(s.get("spec", "") or "").strip(),
                        "label": str(s.get("label", "") or "").strip(),
                        "assignment": str(s.get("assignment", "") or "").strip(),
                        "manufacturer": str(s.get("manufacturer", "") or "").strip(),
                        "article_number": str(s.get("article_number", "") or "").strip(),
                        "note": str(s.get("note", "") or "").strip(),
                    }
                    for s in slots_raw if isinstance(s, dict)
                ],
                key=lambda s: (s["row"], s["slot"]),
            )
            result.append({
                "ap_id": pid,
                "ap_name": ap_name,
                "room": room_name,
                "rows": uv_rows,
                "modules_per_row": uv_modules,
                "preset": str(uv_config.get("preset", "") or ""),
                "slots": slots,
                "busbars": [
                    {
                        "phase": str(b.get("phase", "") or "").strip(),
                        "color": str(b.get("color", "#888888") or "#888888"),
                        "te_start": max(1, int(b.get("te_start", 1) or 1)),
                        "te_end": max(1, int(b.get("te_end", 1) or 1)),
                    }
                    for b in (uv_config.get("busbars", []) or [])
                    if isinstance(b, dict) and str(b.get("phase", "") or "").strip()
                ],
            })
        return result

    def _collect_up_distribution_rows(self, point_id_to_room_name: dict[str, str] | None = None) -> list[dict]:
        point_id_to_room_name = point_id_to_room_name or self._collect_point_id_to_room_name()
        cable_id_to_name: dict[str, str] = {}
        for cable_id, cable_panel in self.param_panel.elec_cable_panels.items():
            cable_params = cable_panel.get_parameters()
            cable_name = str(cable_params.get("name", cable_id) or "").strip() or cable_id
            cable_id_to_name[cable_id] = cable_name

        rows: list[dict] = []
        for pid, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            if not self._is_up_distribution_type(params):
                continue
            config = params.get("up_distribution_config") or {}
            if not isinstance(config, dict):
                continue

            ap_name = (params.get("name") or pid).strip() or pid
            room_name = point_id_to_room_name.get(pid, "(ohne Raum)")
            incoming_id = str(config.get("incoming_cable_id", "") or "").strip()
            incoming_name = cable_id_to_name.get(incoming_id, incoming_id)
            outgoing_raw = config.get("outgoing_cable_ids", [])
            if not isinstance(outgoing_raw, list):
                outgoing_raw = []
            outgoing_ids: list[str] = []
            for cable_id in outgoing_raw:
                text = str(cable_id or "").strip()
                if text and text not in outgoing_ids:
                    outgoing_ids.append(text)
            outgoing_names = [cable_id_to_name.get(cable_id, cable_id) for cable_id in outgoing_ids]

            mappings_raw = config.get("mappings", [])
            if not isinstance(mappings_raw, list):
                mappings_raw = []
            mappings: list[dict] = []
            for mapping in mappings_raw:
                if not isinstance(mapping, dict):
                    continue
                to_cable_id = str(mapping.get("to_cable_id", "") or "").strip()
                mappings.append({
                    "from_conductor": str(mapping.get("from_conductor", "") or "").strip(),
                    "to_cable_id": to_cable_id,
                    "to_cable_name": cable_id_to_name.get(to_cable_id, to_cable_id),
                    "to_conductor": str(mapping.get("to_conductor", "") or "").strip(),
                    "note": str(mapping.get("note", "") or "").strip(),
                })

            distribution_note = str(config.get("note", "") or "").strip()
            if not mappings:
                rows.append({
                    "ap": ap_name,
                    "room": room_name,
                    "incoming_cable": incoming_name,
                    "incoming_cable_id": incoming_id,
                    "outgoing_cables": ", ".join(outgoing_names),
                    "from_conductor": "",
                    "to_cable": "",
                    "to_cable_id": "",
                    "to_conductor": "",
                    "mapping_note": "",
                    "distribution_note": distribution_note,
                })
                continue

            for mapping in mappings:
                rows.append({
                    "ap": ap_name,
                    "room": room_name,
                    "incoming_cable": incoming_name,
                    "incoming_cable_id": incoming_id,
                    "outgoing_cables": ", ".join(outgoing_names),
                    "from_conductor": mapping["from_conductor"],
                    "to_cable": mapping["to_cable_name"],
                    "to_cable_id": mapping["to_cable_id"],
                    "to_conductor": mapping["to_conductor"],
                    "mapping_note": mapping["note"],
                    "distribution_note": distribution_note,
                })
        return rows

    def _collect_ap_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for panel in self.param_panel.elec_point_panels.values():
            params = panel.get_parameters()
            type_name = self._describe_ap_type(params)
            counts[type_name] += 1
        return dict(sorted(counts.items(), key=lambda kv: kv[0].lower()))

    @staticmethod
    def _safe_te_count(te_start: int, te_end: int) -> int:
        if te_end < te_start:
            te_start, te_end = te_end, te_start
        return max(0, (te_end - te_start + 1))

    @staticmethod
    def _bom_catalog_key(item_type: str, key: str) -> str:
        return f"{str(item_type or '').strip()}|{str(key or '').strip()}"

    def _apply_bom_metadata_to_rows(self, rows_by_section: dict[str, list[dict]]) -> dict[str, list[dict]]:
        metadata = (
            self.param_panel.get_bom_metadata()
            if hasattr(self.param_panel, "get_bom_metadata")
            else {}
        )
        catalog = metadata.get("item_catalog", {}) if isinstance(metadata, dict) else {}
        custom_items = metadata.get("custom_items", []) if isinstance(metadata, dict) else []

        enriched: dict[str, list[dict]] = {
            key: [dict(row) for row in rows]
            for key, rows in rows_by_section.items()
        }
        enriched.setdefault("custom_bom_rows", [])

        for section_key, rows in enriched.items():
            for row in rows:
                row.setdefault("manufacturer", "")
                row.setdefault("article_number", "")
                item_type = str(row.get("item_type", "") or "")
                item_key = str(row.get("key", "") or "")
                catalog_key = self._bom_catalog_key(item_type, item_key)
                entry = catalog.get(catalog_key, {}) if isinstance(catalog, dict) else {}
                if isinstance(entry, dict):
                    manufacturer = str(entry.get("manufacturer", "") or "").strip()
                    article_number = str(entry.get("article_number", "") or "").strip()
                    description_override = str(entry.get("description_override", "") or "").strip()
                    if manufacturer:
                        row["manufacturer"] = manufacturer
                    if article_number:
                        row["article_number"] = article_number
                    if description_override:
                        row["description"] = description_override

        if isinstance(custom_items, list):
            for item in custom_items:
                if not isinstance(item, dict):
                    continue
                section_key = str(item.get("section_key", "custom_bom_rows") or "custom_bom_rows").strip()
                section_key = section_key or "custom_bom_rows"
                enriched.setdefault(section_key, [])
                enriched[section_key].append({
                    "category": str(item.get("category", "Manuell") or "Manuell").strip(),
                    "item_type": str(item.get("item_type", "custom") or "custom").strip(),
                    "key": str(item.get("key", item.get("custom_id", "")) or "").strip(),
                    "description": str(item.get("description", "") or "").strip(),
                    "unit": str(item.get("unit", "Stk") or "Stk").strip(),
                    "quantity": float(item.get("quantity", 0.0) or 0.0),
                    "manufacturer": str(item.get("manufacturer", "") or "").strip(),
                    "article_number": str(item.get("article_number", "") or "").strip(),
                    "note": str(item.get("note", "") or "").strip(),
                    "custom_id": str(item.get("custom_id", "") or "").strip(),
                })

        return enriched

    def _collect_bom_rows(
        self,
        hk_rows: list[dict],
        kv_rows: list[dict],
        ap_info_rows: list[dict],
        uv_data: list[dict],
        hl_rows: list[dict],
    ) -> dict:
        """Collect BOM rows + summaries for export/UI/PDF/MCP reuse."""
        hk_by_diameter: dict[float, float] = defaultdict(float)
        for row in hk_rows:
            diameter = float(row.get("diameter_mm", 0.0) or 0.0)
            hk_by_diameter[diameter] += float(row.get("total_m", 0.0) or 0.0)
        hk_bom_rows = [
            {
                "category": "Heizrohr",
                "item_type": "heating_pipe",
                "key": f"{diameter:.1f}",
                "description": f"Heizrohr {diameter:.1f} mm",
                "unit": "m",
                "quantity": length_m,
                "meta": {"diameter_mm": diameter},
            }
            for diameter, length_m in sorted(hk_by_diameter.items())
        ]

        cable_by_type: dict[str, float] = defaultdict(float)
        for row in kv_rows:
            cable_type = str(row.get("type", "") or "").strip() or "(unbekannt)"
            cable_by_type[cable_type] += float(row.get("length_m", 0.0) or 0.0)
        cable_bom_rows = [
            {
                "category": "Elektro-Kabel",
                "item_type": "elec_cable",
                "key": cable_type,
                "description": cable_type,
                "unit": "m",
                "quantity": length_m,
                "meta": {"cable_type": cable_type},
            }
            for cable_type, length_m in sorted(cable_by_type.items(), key=lambda kv: kv[0].lower())
        ]

        ap_by_type: dict[str, int] = defaultdict(int)
        for row in ap_info_rows:
            ap_type = str(row.get("type", "") or "").strip() or "(kein Typ)"
            ap_by_type[ap_type] += 1
        ap_bom_rows = [
            {
                "category": "Anschlusspunkte",
                "item_type": "ap",
                "key": ap_type,
                "description": ap_type,
                "unit": "Stk",
                "quantity": count,
                "meta": {"ap_type": ap_type},
            }
            for ap_type, count in sorted(ap_by_type.items(), key=lambda kv: kv[0].lower())
        ]

        hkv_line_by_type: dict[str, float] = defaultdict(float)
        for row in hl_rows:
            line_type = str(row.get("type", "") or "").strip() or "(unbekannt)"
            hkv_line_by_type[line_type] += float(row.get("length_m", 0.0) or 0.0)
        hkv_line_bom_rows = [
            {
                "category": "HKV-Leitungen",
                "item_type": "hkv_line",
                "key": line_type,
                "description": line_type,
                "unit": "m",
                "quantity": length_m,
                "meta": {"line_type": line_type},
            }
            for line_type, length_m in sorted(hkv_line_by_type.items(), key=lambda kv: kv[0].lower())
        ]

        uv_device_summary: dict[tuple[str, str], dict] = {}
        uv_busbar_summary: dict[tuple[str, int, int], dict] = {}
        for uv in uv_data:
            uv_name = str(uv.get("ap_name", "") or "").strip() or str(uv.get("ap_id", "") or "UV")
            room = str(uv.get("room", "") or "").strip()

            for slot in uv.get("slots", []) or []:
                device_type = str(slot.get("device_type", "") or "").strip() or "(leer)"
                te_size = max(1, int(slot.get("te_size", 1) or 1))
                manufacturer = str(slot.get("manufacturer", "") or "").strip()
                article_number = str(slot.get("article_number", "") or "").strip()
                key = (uv_name, device_type, manufacturer, article_number)
                if key not in uv_device_summary:
                    uv_device_summary[key] = {
                        "category": "UV-Geräte",
                        "item_type": "uv_device",
                        "key": device_type,
                        "uv": uv_name,
                        "room": room,
                        "description": device_type,
                        "unit": "Stk",
                        "quantity": 0,
                        "te_total": 0,
                        "manufacturer": manufacturer,
                        "article_number": article_number,
                    }
                uv_device_summary[key]["quantity"] += 1
                uv_device_summary[key]["te_total"] += te_size

            for busbar in uv.get("busbars", []) or []:
                phase = str(busbar.get("phase", "") or "").strip()
                if not phase:
                    continue
                te_start = max(1, int(busbar.get("te_start", 1) or 1))
                te_end = max(1, int(busbar.get("te_end", 1) or 1))
                if te_end < te_start:
                    te_start, te_end = te_end, te_start
                te_count = self._safe_te_count(te_start, te_end)
                key = (phase, te_start, te_end)
                if key not in uv_busbar_summary:
                    uv_busbar_summary[key] = {
                        "category": "UV-Phasenschienen",
                        "item_type": "uv_busbar",
                        "phase": phase,
                        "description": f"Phasenschiene {phase}",
                        "te_start": te_start,
                        "te_end": te_end,
                        "unit": "TE",
                        "quantity": 0,
                        "uv_count": 0,
                        "uv_names": set(),
                    }
                uv_busbar_summary[key]["quantity"] += te_count
                uv_busbar_summary[key]["uv_names"].add(uv_name)
                uv_busbar_summary[key]["uv_count"] = len(uv_busbar_summary[key]["uv_names"])

        uv_device_bom_rows = sorted(
            uv_device_summary.values(),
            key=lambda r: (
                str(r.get("room", "") or "").lower(),
                str(r.get("uv", "") or "").lower(),
                str(r.get("description", "") or "").lower(),
            ),
        )
        uv_busbar_bom_rows = []
        for row in sorted(
            uv_busbar_summary.values(),
            key=lambda r: (
                str(r.get("phase", "") or "").lower(),
                int(r.get("te_start", 0) or 0),
                int(r.get("te_end", 0) or 0),
            ),
        ):
            row = dict(row)
            row["uv_names"] = ", ".join(sorted(row.get("uv_names", set())))
            uv_busbar_bom_rows.append(row)

        rows_by_section = {
            "hk_bom_rows": hk_bom_rows,
            "cable_bom_rows": cable_bom_rows,
            "ap_bom_rows": ap_bom_rows,
            "hkv_line_bom_rows": hkv_line_bom_rows,
            "uv_device_bom_rows": uv_device_bom_rows,
            "uv_busbar_bom_rows": uv_busbar_bom_rows,
            "custom_bom_rows": [],
        }

        return self._apply_bom_metadata_to_rows(rows_by_section)

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
            perimeter_mm = self._compute_polygon_perimeter_mm(cid)
            perimeter_m = (perimeter_mm or 0.0) / 1000.0

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
                "perimeter_m": perimeter_m,
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
        point_id_to_room_name: dict[str, str] = {}
        for pid, _panel in self.param_panel.elec_point_panels.items():
            room_name = "(ohne Raum)"
            point = self.canvas._elec_points.get(pid)
            point_fp_id = self.param_panel._element_floorplan.get(pid, "")
            if point is not None:
                for rid, poly in self.canvas._elec_room_polygons.items():
                    if len(poly) < 3:
                        continue
                    room_fp_id = self.param_panel._element_floorplan.get(rid, "")
                    if point_fp_id != room_fp_id:
                        continue
                    if self.canvas._point_in_polygon(point, poly):
                        room_panel = self.param_panel.elec_room_panels.get(rid)
                        if room_panel:
                            room_name = room_panel.get_parameters().get("name", rid)
                        else:
                            room_name = rid
                        break
            point_id_to_room_name[pid] = room_name

        kv_rows: list[dict] = []
        for kid, panel in self.param_panel.elec_cable_panels.items():
            params = panel.get_parameters()
            length_px = self.canvas.get_elec_cable_length_px(kid)
            length_m = length_px * scale / 1000.0
            start_ap_id, end_ap_id = self.canvas.get_cable_ap(kid)
            start_name = ""
            start_height = 0.0
            start_position = ""
            start_device = ""
            start_device_color = ""
            start_note = ""
            if start_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(start_ap_id)
                start_name = (ap_p.get_parameters()["name"]
                              if ap_p else start_ap_id)
                start_height = self.canvas._elec_point_height.get(start_ap_id, 0.0)
                start_position = self.canvas._elec_point_position.get(start_ap_id, "")
                if ap_p:
                    ap_params = ap_p.get_parameters()
                    start_device = ap_params.get("smarthome_device", "")
                    start_device_color = ap_params.get("smarthome_device_color", "")
                    start_note = ap_params.get("note", "")
            end_name = ""
            end_height = 0.0
            end_position = ""
            end_device = ""
            end_device_color = ""
            end_note = ""
            if end_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(end_ap_id)
                end_name = (ap_p.get_parameters()["name"]
                            if ap_p else end_ap_id)
                end_height = self.canvas._elec_point_height.get(end_ap_id, 0.0)
                end_position = self.canvas._elec_point_position.get(end_ap_id, "")
                if ap_p:
                    ap_params = ap_p.get_parameters()
                    end_device = ap_params.get("smarthome_device", "")
                    end_device_color = ap_params.get("smarthome_device_color", "")
                    end_note = ap_params.get("note", "")
            
            # Höhen zu Kabellänge addieren (in cm, zu m umrechnen: cm / 100)
            total_height_cm = start_height + end_height
            length_m += total_height_cm / 100.0
            
            kv_rows.append({
                "name": params["name"],
                "type": params["type"],
                "comment": params.get("comment", ""),
                "length_m": length_m,
                "start_ap": start_name,
                "end_ap": end_name,
                "start_room": point_id_to_room_name.get(start_ap_id, "(ohne Raum)") if start_ap_id else "",
                "end_room": point_id_to_room_name.get(end_ap_id, "(ohne Raum)") if end_ap_id else "",
                "start_height_cm": start_height,
                "end_height_cm": end_height,
                "start_position": start_position,
                "end_position": end_position,
                "start_device": start_device,
                "start_device_color": start_device_color,
                "start_note": start_note,
                "end_device": end_device,
                "end_device_color": end_device_color,
                "end_note": end_note,
            })

        # Summe pro Kabel-Typ
        kv_sum: dict[str, float] = defaultdict(float)
        for r in kv_rows:
            kv_sum[r["type"]] += r["length_m"]

        # AP → Kabel Zuordnung
        ap_cables = self._build_ap_cable_map(kv_rows)
        room_ap_connections = self._build_room_ap_connection_map(kv_rows)
        ap_type_counts = self._collect_ap_type_counts()
        point_id_to_room_name = self._collect_point_id_to_room_name()

        ap_info_rows: list[dict] = []
        for pid, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            ap_info_rows.append({
                "name": (params.get("name") or pid).strip() or pid,
                "type": self._describe_ap_type(params),
                "room": point_id_to_room_name.get(pid, "(ohne Raum)"),
                "position": str(self.canvas._elec_point_position.get(pid, "") or "").strip(),
                "height_cm": float(self.canvas._elec_point_height.get(pid, 0.0) or 0.0),
                "device_color": str(params.get("smarthome_device_color", "") or "").strip(),
                "device": str(params.get("smarthome_device", "") or "").strip(),
                "note": str(params.get("note", "") or "").strip(),
            })

        ap_info_rows = sorted(
            ap_info_rows,
            key=lambda r: ((r.get("room") or "").lower(), (r.get("name") or "").lower()),
        )

        # AP-Liste (Detailinfos)
        ap_rows: list[dict] = []
        for pid, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            ap_rows.append({
                "name": (params.get("name") or pid).strip() or pid,
                "type": self._describe_ap_type(params),
                "room": point_id_to_room_name.get(pid, "(ohne Raum)"),
                "position": str(self.canvas._elec_point_position.get(pid, "") or "").strip(),
                "height_cm": float(self.canvas._elec_point_height.get(pid, 0.0) or 0.0),
                "device_color": str(params.get("smarthome_device_color", "") or "").strip(),
                "device": str(params.get("smarthome_device", "") or "").strip(),
                "note": str(params.get("note", "") or "").strip(),
            })

        ap_rows = sorted(
            ap_rows,
            key=lambda r: (
                (r.get("room") or "").lower(),
                (r.get("name") or "").lower(),
            ),
        )
        uv_rows = self._collect_uv_rows(point_id_to_room_name)
        up_distribution_rows = self._collect_up_distribution_rows(point_id_to_room_name)

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
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)
        dlg_layout = QVBoxLayout(dlg)

        tabs = QTabWidget()
        dlg_layout.addWidget(tabs)

        # -- Tab 1: Heizkreise Längen --
        hk_widget = QWidget()
        hk_layout = QVBoxLayout(hk_widget)

        hk_layout.addWidget(QLabel("<b>Heizkreise – Einzellängen</b>"))
        tbl_hk = QTableWidget(len(hk_rows), 8)
        tbl_hk.setHorizontalHeaderLabels(
            ["Name", "Durchm. (mm)", "Abstand (mm)",
             "Rohr (m)", "Zuleitung (m)", "Gesamt (m)", "Umfang (m)", "Fläche (m²)"])
        tbl_hk.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_hk.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, r in enumerate(hk_rows):
            tbl_hk.setItem(i, 0, QTableWidgetItem(r["name"]))
            tbl_hk.setItem(i, 1, QTableWidgetItem(f"{r['diameter_mm']:.1f}"))
            tbl_hk.setItem(i, 2, QTableWidgetItem(f"{r['spacing_mm']:.1f}"))
            for col, key in [(3, "route_m"), (4, "supply_m"), (5, "total_m"), (6, "perimeter_m"), (7, "area_m2")]:
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

        kv_layout.addWidget(QLabel("<b>Kabelliste – alle Kabel</b>"))
        tbl_kv = QTableWidget(len(kv_rows), 14)
        tbl_kv.setHorizontalHeaderLabels(
            [
                "Name", "Typ", "Kabel-Notiz", "Länge (m)",
                "Start-AP", "Start-Gerät", "Start-Farbe", "Start-Notiz",
                "End-AP", "End-Gerät", "End-Farbe", "End-Notiz",
                "Start-Raum", "End-Raum",
            ]
        )
        tbl_kv.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_kv.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, r in enumerate(kv_rows):
            tbl_kv.setItem(i, 0, QTableWidgetItem(r["name"]))
            tbl_kv.setItem(i, 1, QTableWidgetItem(r["type"]))
            tbl_kv.setItem(i, 2, QTableWidgetItem(r.get("comment", "")))
            item = QTableWidgetItem(f"{r['length_m']:.2f}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tbl_kv.setItem(i, 3, item)
            tbl_kv.setItem(i, 4, QTableWidgetItem(r.get("start_ap", "")))
            tbl_kv.setItem(i, 5, QTableWidgetItem(r.get("start_device", "")))
            tbl_kv.setItem(i, 6, QTableWidgetItem(r.get("start_device_color", "")))
            tbl_kv.setItem(i, 7, QTableWidgetItem(r.get("start_note", "")))
            tbl_kv.setItem(i, 8, QTableWidgetItem(r.get("end_ap", "")))
            tbl_kv.setItem(i, 9, QTableWidgetItem(r.get("end_device", "")))
            tbl_kv.setItem(i, 10, QTableWidgetItem(r.get("end_device_color", "")))
            tbl_kv.setItem(i, 11, QTableWidgetItem(r.get("end_note", "")))
            tbl_kv.setItem(i, 12, QTableWidgetItem(r.get("start_room", "")))
            tbl_kv.setItem(i, 13, QTableWidgetItem(r.get("end_room", "")))
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

        if ap_type_counts:
            kv_layout.addWidget(QLabel("<b>Anschlusspunkte – Anzahl pro Typ</b>"))
            sorted_ap_types = sorted(ap_type_counts.keys())
            tbl_ap_types = QTableWidget(len(sorted_ap_types), 2)
            tbl_ap_types.setHorizontalHeaderLabels(["Typ", "Anzahl"])
            tbl_ap_types.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tbl_ap_types.setEditTriggers(QTableWidget.NoEditTriggers)
            for i, t in enumerate(sorted_ap_types):
                tbl_ap_types.setItem(i, 0, QTableWidgetItem(t))
                item = QTableWidgetItem(str(ap_type_counts[t]))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl_ap_types.setItem(i, 1, item)
            kv_layout.addWidget(tbl_ap_types)

        if room_ap_connections:
            kv_layout.addWidget(QLabel(
                "<b>Räume – Details sind in separaten Raum-Reitern verfügbar</b>"))

        tabs.addTab(kv_widget, "🔌 Elektro")

        # -- Tab: AP-Liste --
        apl_widget = QWidget()
        apl_layout = QVBoxLayout(apl_widget)
        apl_layout.addWidget(QLabel("<b>Anschlusspunkte – Übersicht</b>"))

        tbl_ap_list = QTableWidget(len(ap_rows), 8)
        tbl_ap_list.setHorizontalHeaderLabels(
            ["Name", "Art", "Raum", "Position", "Höhe (cm)", "Gerätefarbe", "Unterputz-Gerät", "Notiz"]
        )
        tbl_ap_list.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_ap_list.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, r in enumerate(ap_rows):
            tbl_ap_list.setItem(i, 0, QTableWidgetItem(r.get("name", "")))
            tbl_ap_list.setItem(i, 1, QTableWidgetItem(r.get("type", "")))
            tbl_ap_list.setItem(i, 2, QTableWidgetItem(r.get("room", "")))
            tbl_ap_list.setItem(i, 3, QTableWidgetItem(r.get("position", "")))
            item = QTableWidgetItem(f"{r.get('height_cm', 0.0):.1f}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tbl_ap_list.setItem(i, 4, item)
            tbl_ap_list.setItem(i, 5, QTableWidgetItem(r.get("device_color", "")))
            tbl_ap_list.setItem(i, 6, QTableWidgetItem(r.get("device", "")))
            tbl_ap_list.setItem(i, 7, QTableWidgetItem(r.get("note", "")))
        apl_layout.addWidget(tbl_ap_list)

        tabs.addTab(apl_widget, "🔎 AP-Infos")

        if uv_rows:
            uv_widget = QWidget()
            uv_layout = QVBoxLayout(uv_widget)
            uv_layout.addWidget(QLabel("<b>Unterverteilungen – Belegung</b>"))
            tbl_uv = QTableWidget(len(uv_rows), 10)
            tbl_uv.setHorizontalHeaderLabels(
                ["UV", "Raum", "Raster", "Reihe", "TE", "Belegung", "Kennz.", "Bezeichnung", "Kabel/Stromkreis", "Notiz"]
            )
            tbl_uv.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tbl_uv.setEditTriggers(QTableWidget.NoEditTriggers)
            for i, r in enumerate(uv_rows):
                tbl_uv.setItem(i, 0, QTableWidgetItem(r.get("ap", "")))
                tbl_uv.setItem(i, 1, QTableWidgetItem(r.get("room", "")))
                tbl_uv.setItem(i, 2, QTableWidgetItem(f"{r.get('rows', 0)}x{r.get('modules_per_row', 0)}"))
                tbl_uv.setItem(i, 3, QTableWidgetItem(str(r.get("row", ""))))
                tbl_uv.setItem(i, 4, QTableWidgetItem(str(r.get("slot", ""))))
                tbl_uv.setItem(i, 5, QTableWidgetItem(r.get("device_type", "")))
                tbl_uv.setItem(i, 6, QTableWidgetItem(r.get("spec", "")))
                tbl_uv.setItem(i, 7, QTableWidgetItem(r.get("label", "")))
                tbl_uv.setItem(i, 8, QTableWidgetItem(r.get("assignment", "")))
                tbl_uv.setItem(i, 9, QTableWidgetItem(r.get("note", "")))
            uv_layout.addWidget(tbl_uv)
            tabs.addTab(uv_widget, "🧰 UV")

        if up_distribution_rows:
            up_widget = QWidget()
            up_layout = QVBoxLayout(up_widget)
            up_layout.addWidget(QLabel("<b>Verteilung in Unterputzdose – Aderzuordnung</b>"))
            tbl_up = QTableWidget(len(up_distribution_rows), 10)
            tbl_up.setHorizontalHeaderLabels(
                [
                    "AP", "Raum", "Zuleitung", "Abgänge",
                    "Ader (Zuleitung)", "Abgehendes Kabel", "Ader (Abgang)",
                    "Zuordnung-Notiz", "Verteilungs-Notiz", "Abgangs-ID",
                ]
            )
            tbl_up.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tbl_up.setEditTriggers(QTableWidget.NoEditTriggers)
            for i, r in enumerate(up_distribution_rows):
                tbl_up.setItem(i, 0, QTableWidgetItem(r.get("ap", "")))
                tbl_up.setItem(i, 1, QTableWidgetItem(r.get("room", "")))
                tbl_up.setItem(i, 2, QTableWidgetItem(r.get("incoming_cable", "")))
                tbl_up.setItem(i, 3, QTableWidgetItem(r.get("outgoing_cables", "")))
                tbl_up.setItem(i, 4, QTableWidgetItem(r.get("from_conductor", "")))
                tbl_up.setItem(i, 5, QTableWidgetItem(r.get("to_cable", "")))
                tbl_up.setItem(i, 6, QTableWidgetItem(r.get("to_conductor", "")))
                tbl_up.setItem(i, 7, QTableWidgetItem(r.get("mapping_note", "")))
                tbl_up.setItem(i, 8, QTableWidgetItem(r.get("distribution_note", "")))
                tbl_up.setItem(i, 9, QTableWidgetItem(r.get("to_cable_id", "")))
            up_layout.addWidget(tbl_up)
            tabs.addTab(up_widget, "🔀 Unterputzdose")

        bom_data = self._collect_bom_rows(
            hk_rows=hk_rows,
            kv_rows=kv_rows,
            ap_info_rows=ap_rows,
            uv_data=self._collect_uv_data(point_id_to_room_name),
            hl_rows=hl_rows,
        )

        bom_widget = QWidget()
        bom_layout = QVBoxLayout(bom_widget)
        bom_layout.addWidget(QLabel("<b>Stückliste – Zusammenfassung</b>"))

        bom_rows = []
        for section_key, section_name in [
            ("hk_bom_rows", "Heizrohr"),
            ("cable_bom_rows", "Elektro-Kabel"),
            ("ap_bom_rows", "Anschlusspunkte"),
            ("hkv_line_bom_rows", "HKV-Leitungen"),
            ("uv_device_bom_rows", "UV-Geräte"),
            ("uv_busbar_bom_rows", "UV-Phasenschienen"),
            ("custom_bom_rows", "Manuelle Positionen"),
        ]:
            for row in bom_data.get(section_key, []):
                bom_rows.append({
                    "section": section_name,
                    "description": row.get("description", ""),
                    "manufacturer": row.get("manufacturer", ""),
                    "article_number": row.get("article_number", ""),
                    "unit": row.get("unit", ""),
                    "quantity": row.get("quantity", 0),
                    "note": (
                        f"TE: {row.get('te_total', 0)}" if section_key == "uv_device_bom_rows"
                        else f"TE {row.get('te_start', '')}-{row.get('te_end', '')}; UV: {row.get('uv_names', '')}" if section_key == "uv_busbar_bom_rows"
                        else str(row.get("note", "") or "")
                    ),
                })

        tbl_bom = QTableWidget(len(bom_rows), 7)
        tbl_bom.setHorizontalHeaderLabels(["Bereich", "Artikel", "Hersteller", "Artikelnummer", "Einheit", "Menge", "Notiz"])
        tbl_bom.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_bom.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, row in enumerate(bom_rows):
            tbl_bom.setItem(i, 0, QTableWidgetItem(str(row.get("section", ""))))
            tbl_bom.setItem(i, 1, QTableWidgetItem(str(row.get("description", ""))))
            tbl_bom.setItem(i, 2, QTableWidgetItem(str(row.get("manufacturer", ""))))
            tbl_bom.setItem(i, 3, QTableWidgetItem(str(row.get("article_number", ""))))
            tbl_bom.setItem(i, 4, QTableWidgetItem(str(row.get("unit", ""))))
            item_qty = QTableWidgetItem(f"{float(row.get('quantity', 0.0) or 0.0):.2f}")
            item_qty.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tbl_bom.setItem(i, 5, item_qty)
            tbl_bom.setItem(i, 6, QTableWidgetItem(str(row.get("note", ""))))
        bom_layout.addWidget(tbl_bom)
        tabs.addTab(bom_widget, "📦 Stückliste")

        # -- Tab: Anschlusspunkte Verkabelung --
        ap_widget = QWidget()
        ap_layout = QVBoxLayout(ap_widget)
        ap_layout.addWidget(QLabel("<b>Anschlusspunkte – Kabelverbindungen</b>"))
        if ap_cables:
            for ap_name in sorted(ap_cables.keys()):
                cables = ap_cables[ap_name]
                ap_layout.addWidget(QLabel(f"<i>{ap_name}</i>"))
                tbl_ap = QTableWidget(len(cables), 8)
                tbl_ap.setHorizontalHeaderLabels(
                    ["Kabel", "Typ", "Anschluss", "Gerät", "Farbe", "AP-Notiz", "Kabel-Notiz", "Länge (m)"])
                tbl_ap.horizontalHeader().setSectionResizeMode(
                    QHeaderView.Stretch)
                tbl_ap.setEditTriggers(QTableWidget.NoEditTriggers)
                tbl_ap.setMaximumHeight(30 + len(cables) * 30)
                for i, c in enumerate(cables):
                    tbl_ap.setItem(i, 0, QTableWidgetItem(c["cable"]))
                    tbl_ap.setItem(i, 1, QTableWidgetItem(c["type"]))
                    tbl_ap.setItem(i, 2, QTableWidgetItem(c["role"]))
                    tbl_ap.setItem(i, 3, QTableWidgetItem(c.get("ap_device", "")))
                    tbl_ap.setItem(i, 4, QTableWidgetItem(c.get("ap_device_color", "")))
                    tbl_ap.setItem(i, 5, QTableWidgetItem(c.get("ap_note", "")))
                    tbl_ap.setItem(i, 6, QTableWidgetItem(c.get("cable_note", "")))
                    item = QTableWidgetItem(f"{c['length_m']:.2f}")
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    tbl_ap.setItem(i, 7, item)
                ap_layout.addWidget(tbl_ap)
        else:
            ap_layout.addWidget(QLabel("Keine AP-Kabelverbindungen vorhanden."))
        tabs.addTab(ap_widget, "🔗 AP-Verkabelung")

        # -- Zusätzliche Tabs: Elektro je Raum --
        ordered_room_names: list[str] = []
        for _, panel in self.param_panel.elec_room_panels.items():
            room_name = (panel.get_parameters().get("name") or "").strip()
            if room_name and room_name not in ordered_room_names:
                ordered_room_names.append(room_name)

        for row in room_ap_connections:
            room_name = row.get("room", "")
            if room_name and room_name not in ordered_room_names:
                ordered_room_names.append(room_name)

        for room_name in ordered_room_names:
            room_rows = [r for r in room_ap_connections if r.get("room", "") == room_name]

            room_widget = QWidget()
            room_layout = QVBoxLayout(room_widget)

            room_layout.addWidget(QLabel(f"<b>Raum: {room_name}</b>"))

            ap_names = sorted({r.get("ap", "") for r in room_rows if r.get("ap", "")})
            cable_count = len({r.get("cable", "") for r in room_rows if r.get("cable", "")})
            room_layout.addWidget(QLabel(
                f"AP im Bericht: {len(ap_names)}   |   Kabelverbindungen: {cable_count}"
            ))

            if room_rows:
                tbl_room = QTableWidget(len(room_rows), 9)
                tbl_room.setHorizontalHeaderLabels(
                    [
                        "AP", "Gerät", "Farbe", "AP-Notiz",
                        "Kabel", "Typ", "Kabel-Notiz", "Führt zu AP", "Länge (m)",
                    ]
                )
                tbl_room.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
                tbl_room.setEditTriggers(QTableWidget.NoEditTriggers)

                for i, row in enumerate(room_rows):
                    tbl_room.setItem(i, 0, QTableWidgetItem(row.get("ap", "")))
                    tbl_room.setItem(i, 1, QTableWidgetItem(row.get("ap_device", "")))
                    tbl_room.setItem(i, 2, QTableWidgetItem(row.get("ap_device_color", "")))
                    tbl_room.setItem(i, 3, QTableWidgetItem(row.get("ap_note", "")))
                    tbl_room.setItem(i, 4, QTableWidgetItem(row.get("cable", "")))
                    tbl_room.setItem(i, 5, QTableWidgetItem(row.get("type", "")))
                    tbl_room.setItem(i, 6, QTableWidgetItem(row.get("cable_note", "")))
                    tbl_room.setItem(i, 7, QTableWidgetItem(row.get("target_ap", "")))
                    item = QTableWidgetItem(f"{row.get('length_m', 0.0):.2f}")
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    tbl_room.setItem(i, 8, item)

                room_layout.addWidget(tbl_room)
            else:
                room_layout.addWidget(QLabel("Keine Kabelverbindungen für diesen Raum vorhanden."))

            tabs.addTab(room_widget, f"🏠 {room_name}")

        # -- Buttons --
        btn_box = QDialogButtonBox()
        btn_csv = QPushButton("💾 Als CSV exportieren")
        btn_csv.clicked.connect(
            lambda: self._save_lengths_csv(hk_rows, hk_sum, kv_rows, kv_sum,
                                           hkv_sum, ap_cables, hl_rows, hl_sum,
                                           ap_type_counts, room_ap_connections, uv_rows,
                                           up_distribution_rows, bom_data)
        )
        btn_box.addButton(btn_csv, QDialogButtonBox.ActionRole)
        btn_box.addButton(QDialogButtonBox.Close)
        btn_box.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn_box)

        dlg.exec()

    def _save_lengths_csv(self, hk_rows, hk_sum, kv_rows, kv_sum, hkv_sum,
                           ap_cables=None, hl_rows=None, hl_sum=None,
                           ap_type_counts=None, room_ap_connections=None,
                           uv_rows=None, up_distribution_rows=None,
                           bom_data=None):
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
                               "Rohr (m)", "Zuleitung (m)", "Gesamt (m)", "Umfang (m)", "Fläche (m²)"]))
        for r in hk_rows:
            lines.append(sep.join([
                r["name"],
                f"{r['diameter_mm']:.1f}",
                f"{r['spacing_mm']:.1f}",
                f"{r['route_m']:.2f}",
                f"{r['supply_m']:.2f}",
                f"{r['total_m']:.2f}",
                f"{r.get('perimeter_m', 0.0):.2f}",
                f"{r.get('area_m2', 0.0):.2f}",
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
        lines.append(sep.join(["Name", "Typ", "Start-AP", "Start-Position",
                               "Start-Höhe (cm)", "Start-Gerät", "Start-Farbe", "Start-Notiz",
                               "End-AP", "End-Position", "End-Höhe (cm)", "End-Gerät", "End-Farbe",
                               "End-Notiz", "Kabel-Notiz", "Länge (m)"]))
        for r in kv_rows:
            lines.append(sep.join([
                r["name"], r["type"],
                r.get("start_ap", ""), r.get("start_position", ""),
                f"{r.get('start_height_cm', 0.0):.1f}",
                r.get("start_device", ""), r.get("start_device_color", ""), r.get("start_note", ""),
                r.get("end_ap", ""), r.get("end_position", ""),
                f"{r.get('end_height_cm', 0.0):.1f}",
                r.get("end_device", ""), r.get("end_device_color", ""), r.get("end_note", ""),
                r.get("comment", ""),
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
            lines.append(sep.join(["AP", "Kabel", "Typ", "Anschluss", "Gerät", "Farbe", "AP-Notiz", "Kabel-Notiz",
                                   "Länge (m)"]))
            for ap_name in sorted(ap_cables.keys()):
                for c in ap_cables[ap_name]:
                    lines.append(sep.join([
                        ap_name, c["cable"], c["type"], c["role"],
                        c.get("ap_device", ""), c.get("ap_device_color", ""), c.get("ap_note", ""), c.get("cable_note", ""),
                        f"{c['length_m']:.2f}",
                    ]))
            lines.append("")

        # Raumzuordnung AP → Kabelziele
        if room_ap_connections:
            lines.append("Elektro - Räume mit AP und Kabelzielen")
            lines.append(sep.join([
                "Raum", "AP", "Gerät", "Farbe", "AP-Notiz", "Kabel", "Typ", "Kabel-Notiz", "Führt zu AP", "Länge (m)",
            ]))
            for row in room_ap_connections:
                lines.append(sep.join([
                    row.get("room", ""),
                    row.get("ap", ""),
                    row.get("ap_device", ""),
                    row.get("ap_device_color", ""),
                    row.get("ap_note", ""),
                    row.get("cable", ""),
                    row.get("type", ""),
                    row.get("cable_note", ""),
                    row.get("target_ap", ""),
                    f"{row.get('length_m', 0.0):.2f}",
                ]))
            lines.append("")

        # AP-Typen
        if ap_type_counts:
            lines.append("Elektro - Anschlusspunkte je Typ")
            lines.append(sep.join(["Typ", "Anzahl"]))
            for type_name in sorted(ap_type_counts.keys()):
                lines.append(sep.join([type_name, str(ap_type_counts[type_name])]))
            lines.append("")

        if uv_rows:
            lines.append("Elektro - Unterverteilungen")
            lines.append(sep.join([
                "UV", "Raum", "Raster", "Reihe", "TE", "Belegung",
                "Kennz.", "Bezeichnung", "Kabel/Stromkreis", "Notiz",
            ]))
            for row in uv_rows:
                lines.append(sep.join([
                    row.get("ap", ""),
                    row.get("room", ""),
                    f"{row.get('rows', 0)}x{row.get('modules_per_row', 0)}",
                    str(row.get("row", "")),
                    str(row.get("slot", "")),
                    row.get("device_type", ""),
                    row.get("spec", ""),
                    row.get("label", ""),
                    row.get("assignment", ""),
                    row.get("note", ""),
                ]))
            lines.append("")

        if up_distribution_rows:
            lines.append("Elektro - Verteilung in Unterputzdose")
            lines.append(sep.join([
                "AP", "Raum", "Zuleitung", "Abgänge",
                "Ader (Zuleitung)", "Abgehendes Kabel", "Abgehendes Kabel (ID)", "Ader (Abgang)",
                "Zuordnung-Notiz", "Verteilungs-Notiz",
            ]))
            for row in up_distribution_rows:
                lines.append(sep.join([
                    row.get("ap", ""),
                    row.get("room", ""),
                    row.get("incoming_cable", ""),
                    row.get("outgoing_cables", ""),
                    row.get("from_conductor", ""),
                    row.get("to_cable", ""),
                    row.get("to_cable_id", ""),
                    row.get("to_conductor", ""),
                    row.get("mapping_note", ""),
                    row.get("distribution_note", ""),
                ]))
            lines.append("")

        if bom_data:
            lines.append("Stückliste - Zusammenfassung")
            lines.append(sep.join(["Bereich", "Artikel", "Hersteller", "Artikelnummer", "Einheit", "Menge", "Notiz"]))
            for section_key, section_name in [
                ("hk_bom_rows", "Heizrohr"),
                ("cable_bom_rows", "Elektro-Kabel"),
                ("ap_bom_rows", "Anschlusspunkte"),
                ("hkv_line_bom_rows", "HKV-Leitungen"),
                ("uv_device_bom_rows", "UV-Geräte"),
                ("uv_busbar_bom_rows", "UV-Phasenschienen"),
                ("custom_bom_rows", "Manuelle Positionen"),
            ]:
                for row in bom_data.get(section_key, []):
                    note = ""
                    if section_key == "uv_device_bom_rows":
                        note = f"TE: {row.get('te_total', 0)}"
                    elif section_key == "uv_busbar_bom_rows":
                        note = f"TE {row.get('te_start', '')}-{row.get('te_end', '')}; UV: {row.get('uv_names', '')}"
                    else:
                        note = str(row.get("note", "") or "")

                    lines.append(sep.join([
                        section_name,
                        str(row.get("description", "")),
                        str(row.get("manufacturer", "")),
                        str(row.get("article_number", "")),
                        str(row.get("unit", "")),
                        f"{float(row.get('quantity', 0.0) or 0.0):.2f}",
                        note,
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
            for role, key, dev_key, color_key, note_key in [
                ("Start", "start_ap", "start_device", "start_device_color", "start_note"),
                ("Ende", "end_ap", "end_device", "end_device_color", "end_note"),
            ]:
                ap = r.get(key, "")
                if ap:
                    ap_map[ap].append({
                        "cable": r["name"], "type": r["type"],
                        "length_m": r["length_m"], "role": role,
                        "ap_device": r.get(dev_key, ""),
                        "ap_device_color": r.get(color_key, ""),
                        "ap_note": r.get(note_key, ""),
                        "cable_note": r.get("comment", ""),
                    })
        return dict(ap_map)

    def _build_room_ap_connection_map(self, kv_rows: list[dict]) -> list[dict]:
        """Build rows for report: Raum -> AP -> Kabel -> Ziel-AP.

        Returns list of rows with keys:
        room, ap, cable, type, role, target_ap, length_m,
        ap_device, ap_device_color, ap_note, cable_note
        """
        point_id_to_name: dict[str, str] = {}
        point_id_to_room_name: dict[str, str] = {}
        point_id_to_device: dict[str, str] = {}
        point_id_to_device_color: dict[str, str] = {}
        point_id_to_note: dict[str, str] = {}

        for pid, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            ap_name = (params.get("name") or pid).strip() or pid
            point_id_to_name[pid] = ap_name
            point_id_to_device[pid] = str(params.get("smarthome_device", "") or "").strip()
            point_id_to_device_color[pid] = str(params.get("smarthome_device_color", "") or "").strip()
            point_id_to_note[pid] = str(params.get("note", "") or "").strip()

            room_name = "(ohne Raum)"
            point = self.canvas._elec_points.get(pid)
            point_fp_id = self.param_panel._element_floorplan.get(pid, "")
            if point is not None:
                for rid, poly in self.canvas._elec_room_polygons.items():
                    if len(poly) < 3:
                        continue
                    room_fp_id = self.param_panel._element_floorplan.get(rid, "")
                    if point_fp_id != room_fp_id:
                        continue
                    if self.canvas._point_in_polygon(point, poly):
                        room_panel = self.param_panel.elec_room_panels.get(rid)
                        if room_panel:
                            room_name = room_panel.get_parameters().get("name", rid)
                        else:
                            room_name = rid
                        break
            point_id_to_room_name[pid] = room_name

        # cable name -> length/type (from kv rows) for robust display order
        cable_meta: dict[str, dict] = {
            r.get("name", ""): {
                "type": r.get("type", ""),
                "length_m": float(r.get("length_m", 0.0)),
                "comment": str(r.get("comment", "") or "").strip(),
            }
            for r in kv_rows
        }

        rows: list[dict] = []
        for cable_id, panel in self.param_panel.elec_cable_panels.items():
            cable_params = panel.get_parameters()
            cable_name = cable_params.get("name", cable_id)
            cable_type = cable_meta.get(cable_name, {}).get("type", cable_params.get("type", ""))
            cable_len = cable_meta.get(cable_name, {}).get("length_m", 0.0)
            cable_note = cable_meta.get(cable_name, {}).get("comment", str(cable_params.get("comment", "") or "").strip())

            start_id, end_id = self.canvas.get_cable_ap(cable_id)

            if start_id:
                rows.append({
                    "room": point_id_to_room_name.get(start_id, "(ohne Raum)"),
                    "ap": point_id_to_name.get(start_id, start_id),
                    "cable": cable_name,
                    "type": cable_type,
                    "role": "Start",
                    "target_ap": point_id_to_name.get(end_id, end_id) if end_id else "(offenes Ende)",
                    "length_m": cable_len,
                    "ap_device": point_id_to_device.get(start_id, ""),
                    "ap_device_color": point_id_to_device_color.get(start_id, ""),
                    "ap_note": point_id_to_note.get(start_id, ""),
                    "cable_note": cable_note,
                })
            if end_id:
                rows.append({
                    "room": point_id_to_room_name.get(end_id, "(ohne Raum)"),
                    "ap": point_id_to_name.get(end_id, end_id),
                    "cable": cable_name,
                    "type": cable_type,
                    "role": "Ende",
                    "target_ap": point_id_to_name.get(start_id, start_id) if start_id else "(offenes Ende)",
                    "length_m": cable_len,
                    "ap_device": point_id_to_device.get(end_id, ""),
                    "ap_device_color": point_id_to_device_color.get(end_id, ""),
                    "ap_note": point_id_to_note.get(end_id, ""),
                    "cable_note": cable_note,
                })

        return sorted(
            rows,
            key=lambda r: (
                (r.get("room") or "").lower(),
                (r.get("ap") or "").lower(),
                (r.get("cable") or "").lower(),
                (r.get("role") or "").lower(),
            ),
        )

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
            perimeter_mm = self._compute_polygon_perimeter_mm(cid)
            perimeter_m = (perimeter_mm or 0.0) / 1000.0
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
                "perimeter_m": perimeter_m,
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
            start_height = 0.0
            start_position = ""
            start_device = ""
            start_device_color = ""
            start_note = ""
            if start_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(start_ap_id)
                start_name = (ap_p.get_parameters()["name"]
                              if ap_p else start_ap_id)
                start_height = self.canvas._elec_point_height.get(start_ap_id, 0.0)
                start_position = self.canvas._elec_point_position.get(start_ap_id, "")
                if ap_p:
                    ap_params = ap_p.get_parameters()
                    start_device = ap_params.get("smarthome_device", "")
                    start_device_color = ap_params.get("smarthome_device_color", "")
                    start_note = ap_params.get("note", "")
            end_name = ""
            end_height = 0.0
            end_position = ""
            end_device = ""
            end_device_color = ""
            end_note = ""
            if end_ap_id:
                ap_p = self.param_panel.elec_point_panels.get(end_ap_id)
                end_name = (ap_p.get_parameters()["name"]
                            if ap_p else end_ap_id)
                end_height = self.canvas._elec_point_height.get(end_ap_id, 0.0)
                end_position = self.canvas._elec_point_position.get(end_ap_id, "")
                if ap_p:
                    ap_params = ap_p.get_parameters()
                    end_device = ap_params.get("smarthome_device", "")
                    end_device_color = ap_params.get("smarthome_device_color", "")
                    end_note = ap_params.get("note", "")
            
            # Höhen zu Kabellänge addieren (in cm, zu m umrechnen: cm / 100)
            total_height_cm = start_height + end_height
            length_m += total_height_cm / 100.0
            
            kv_rows.append({"name": params["name"], "type": params["type"],
                            "comment": params.get("comment", ""),
                            "length_m": length_m,
                            "start_ap": start_name, "end_ap": end_name,
                            "start_height_cm": start_height,
                            "end_height_cm": end_height,
                            "start_position": start_position,
                            "end_position": end_position,
                            "start_device": start_device,
                            "start_device_color": start_device_color,
                            "start_note": start_note,
                            "end_device": end_device,
                            "end_device_color": end_device_color,
                            "end_note": end_note})

        kv_sum: dict[str, float] = defaultdict(float)
        for r in kv_rows:
            kv_sum[r["type"]] += r["length_m"]

        ap_cables = self._build_ap_cable_map(kv_rows)
        room_ap_connections = self._build_room_ap_connection_map(kv_rows)
        ap_type_counts = self._collect_ap_type_counts()
        point_id_to_room_name = self._collect_point_id_to_room_name()

        ap_info_rows: list[dict] = []
        for pid, panel in self.param_panel.elec_point_panels.items():
            params = panel.get_parameters()
            ap_info_rows.append({
                "name": (params.get("name") or pid).strip() or pid,
                "type": self._describe_ap_type(params),
                "room": point_id_to_room_name.get(pid, "(ohne Raum)"),
                "position": str(self.canvas._elec_point_position.get(pid, "") or "").strip(),
                "height_cm": float(self.canvas._elec_point_height.get(pid, 0.0) or 0.0),
                "device_color": str(params.get("smarthome_device_color", "") or "").strip(),
                "device": str(params.get("smarthome_device", "") or "").strip(),
                "note": str(params.get("note", "") or "").strip(),
            })

        ap_info_rows = sorted(
            ap_info_rows,
            key=lambda r: ((r.get("room") or "").lower(), (r.get("name") or "").lower()),
        )
        uv_rows = self._collect_uv_rows(point_id_to_room_name)
        uv_data = self._collect_uv_data(point_id_to_room_name)
        up_distribution_rows = self._collect_up_distribution_rows(point_id_to_room_name)

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

        bom_data = self._collect_bom_rows(
            hk_rows=hk_rows,
            kv_rows=kv_rows,
            ap_info_rows=ap_info_rows,
            uv_data=uv_data,
            hl_rows=hl_rows,
        )

        return {
            "t_supply": t_supply, "t_return": t_return,
            "hk_rows": hk_rows, "hkv_sum": hkv_sum,
            "kv_rows": kv_rows, "kv_sum": kv_sum,
            "ap_cables": ap_cables,
            "room_ap_connections": room_ap_connections,
            "ap_type_counts": ap_type_counts,
            "ap_info_rows": ap_info_rows,
            "uv_rows": uv_rows,
            "uv_data": uv_data,
            "up_distribution_rows": up_distribution_rows,
            "hl_rows": hl_rows, "hl_sum": hl_sum,
            **bom_data,
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
        self._open_pdf_export_config_dialog(self._continue_export_pdf)

    def _continue_export_pdf(self, pages: list[dict]):
        if pages is None:
            return

        enabled_pages = [p for p in pages if p.get("enabled", True)]
        if not enabled_pages:
            QMessageBox.information(
                self,
                "PDF-Export",
                "Keine aktive Exportseite ausgewählt.",
            )
            return

        if pages != self._pdf_export_pages:
            self._pdf_export_pages = pages
            self._mark_dirty()

        path, _ = QFileDialog.getSaveFileName(
            self, "Als PDF exportieren", "projektbericht.pdf", "PDF (*.pdf)")
        if not path:
            return

        # ── Save current visibility state ──
        saved_vis = self._save_all_visibility()

        # ── Progress dialog ──
        progress = QProgressDialog(
            "PDF wird exportiert…", "Abbrechen", 0, len(enabled_pages), self
        )
        progress.setWindowTitle("PDF-Export")
        progress.setMinimumDuration(0)
        progress.setModal(True)
        progress.setValue(0)
        QApplication.processEvents()

        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        printer.setPageMargins(QMarginsF(12, 10, 12, 10),
                               QPageLayout.Unit.Millimeter)

        painter = QPainter()
        if not painter.begin(printer):
            self._restore_all_visibility(saved_vis)
            progress.close()
            QMessageBox.warning(self, "Fehler",
                                "PDF konnte nicht erstellt werden.")
            return

        cancelled = False
        try:
            dpi = printer.resolution()
            data = self._collect_export_data()

            # ── Pass 1: dry-run on QPicture to count pages ──────────────────
            # QPicture records paint commands but writes nothing to disk.
            from PySide6.QtGui import QPicture
            toc_entries: list[tuple[str, int]] = []
            try:
                _pic = QPicture()
                _dpa = QPainter()
                if _dpa.begin(_pic):
                    try:
                        # dry_run=True: new_page() only increments counter,
                        # never calls printer.newPage() on the real printer.
                        _dctx = _PdfContext(printer, _dpa, dpi, dry_run=True)
                        for _cidx, _cpage in enumerate(enabled_pages):
                            if _cidx > 0:
                                _dctx.new_page()
                            _dctx.record_toc(_cpage.get("title", f"Seite {_cidx + 1}"))
                            self._apply_page_visibility(_cpage)
                            self._render_pdf_export_page(_dctx, data, _cpage)
                    finally:
                        _dpa.end()
                    # Shift all page numbers by +1 (page 1 will be TOC)
                    toc_entries = [(t, pn + 1) for t, pn in _dctx._toc]
            except Exception:
                toc_entries = []
            finally:
                # Restore visibility after counting pass
                self._restore_all_visibility(saved_vis)

            # ── Pass 2: real render – TOC on page 1, content from page 2 ────
            ctx = _PdfContext(printer, painter, dpi)

            # Also write plan SVG next to PDF (all elements)
            svg_path = str(Path(path).with_suffix('.svg'))
            self._write_plan_svg(svg_path)

            # TOC as page 1 (no printer.newPage() before – first page is free)
            if toc_entries:
                ctx.draw_toc_on_first_page(toc_entries)

            for idx, page in enumerate(enabled_pages):
                if progress.wasCanceled():
                    cancelled = True
                    break

                page_title = page.get("title", f"Seite {idx + 1}")
                progress.setLabelText(f"Exportiere: {page_title}")
                QApplication.processEvents()

                self._apply_page_visibility(page)
                ctx.new_page()   # pages 2, 3, …
                self._render_pdf_export_page(ctx, data, page)

                progress.setValue(idx + 1)
                QApplication.processEvents()
        finally:
            painter.end()
            # ── Restore original visibility ──
            self._restore_all_visibility(saved_vis)
            self.canvas.update()
            progress.close()

        if cancelled:
            import os
            try:
                os.remove(path)
            except OSError:
                pass
            self.status.showMessage("PDF-Export abgebrochen.")
        else:
            self.status.showMessage(f"\U0001f4c4 PDF exportiert: {path}")

    # ── Visibility save / apply / restore for PDF export ──

    def _save_all_visibility(self) -> dict:
        """Snapshot all canvas-level per-item visibility dictionaries."""
        return {
            "circuit_visible": dict(self.canvas._circuit_visible),
            "hkv_visible": dict(self.canvas._hkv_visible),
            "hkv_line_visible": dict(self.canvas._hkv_line_visible),
            "elec_visible": dict(self.canvas._elec_visible),
            "text_visible": dict(self.canvas._text_visible),
            "label_visible": dict(self.canvas._label_visible),
            "floor_plan_visible": {
                fid: layer.visible
                for fid, layer in self.canvas._floor_plans.items()
            },
        }

    def _restore_all_visibility(self, saved: dict):
        """Restore all per-item visibility from a snapshot."""
        self.canvas._circuit_visible.update(saved.get("circuit_visible", {}))
        self.canvas._hkv_visible.update(saved.get("hkv_visible", {}))
        self.canvas._hkv_line_visible.update(saved.get("hkv_line_visible", {}))
        self.canvas._elec_visible.update(saved.get("elec_visible", {}))
        self.canvas._text_visible.update(saved.get("text_visible", {}))
        self.canvas._label_visible.update(saved.get("label_visible", {}))
        for fid, vis in saved.get("floor_plan_visible", {}).items():
            layer = self.canvas._floor_plans.get(fid)
            if layer:
                layer.visible = vis

    def _apply_page_visibility(self, page: dict):
        """Set canvas visibility according to the page's element_visibility config.

        This makes ALL items in the included groups visible, and hides all
        items in excluded groups, so the render shows exactly what the page
        configuration specifies.
        """
        elem_vis = page.get("element_visibility") or self._default_pdf_element_visibility()
        ptype = str(page.get("type", "plan")).strip().lower()

        show_bg = bool(elem_vis.get("background", True))
        show_furniture = bool(elem_vis.get("furniture", True))
        show_hk = bool(elem_vis.get("hk", True))
        show_hkv = bool(elem_vis.get("hkv", True))
        show_hkv_line = bool(elem_vis.get("hkv_line", True))
        show_ap = bool(elem_vis.get("ap", True))
        show_room = bool(elem_vis.get("room", True))
        show_kv = bool(elem_vis.get("kv", True))
        show_text = bool(elem_vis.get("text", True))

        # Floor plans / furniture layers
        for fid in self.canvas._floor_plan_order:
            layer = self.canvas._floor_plans.get(fid)
            if not layer:
                continue
            is_furniture = fid in self.param_panel.furniture_panels
            if is_furniture:
                layer.visible = show_furniture
            else:
                layer.visible = show_bg

        # Heating circuits (polygons, routes, supply lines)
        for cid in self.canvas._polygons:
            self.canvas._circuit_visible[cid] = show_hk
        for cid in self.canvas._manual_routes:
            if cid not in self.canvas._circuit_visible:
                self.canvas._circuit_visible[cid] = show_hk
            else:
                self.canvas._circuit_visible[cid] = show_hk
        for cid in self.canvas._supply_lines:
            if cid not in self.canvas._circuit_visible:
                self.canvas._circuit_visible[cid] = show_hk
            else:
                self.canvas._circuit_visible[cid] = show_hk

        # HKV
        for hid in self.canvas._hkv_points:
            self.canvas._hkv_visible[hid] = show_hkv

        # HKV lines
        for lid in self.canvas._hkv_lines:
            self.canvas._hkv_line_visible[lid] = show_hkv_line

        # Elektro: APs
        for pid in self.canvas._elec_points:
            self.canvas._elec_visible[pid] = show_ap

        # Elektro: rooms
        for rid in self.canvas._elec_room_polygons:
            self.canvas._elec_visible[rid] = show_room

        # Elektro: cables
        for kid in self.canvas._elec_cables:
            self.canvas._elec_visible[kid] = show_kv

        # Text annotations
        for tid in self.canvas._text_annotations:
            self.canvas._text_visible[tid] = show_text

        # Labels: always show in export
        for key in self.canvas._label_visible:
            self.canvas._label_visible[key] = True

    # ── Seite: Plan-Darstellung (generisch) ──

    def _render_pdf_export_page(self, ctx: '_PdfContext', data: dict, page: dict):
        ptype = str(page.get("type", "plan")).strip().lower()
        title = str(page.get("title") or "Seite").strip() or "Seite"
        elem_vis = page.get("element_visibility") or self._default_pdf_element_visibility()

        show_bg = bool(elem_vis.get("background", page.get("show_background", True)))
        show_heating_groups = any(bool(elem_vis.get(k, True)) for k in ("hk", "hkv", "hkv_line"))
        show_elektro_groups = any(bool(elem_vis.get(k, True)) for k in ("ap", "room", "kv"))

        if ptype == "lengths":
            self._pdf_lengths_page(ctx, data, title=title)
            return
        if ptype == "hydraulics":
            self._pdf_hydraulics_page(ctx, data, title=title)
            return
        if ptype == "heating":
            self._pdf_heating_page(
                ctx,
                data,
                title=title,
                floor_plan_id=page.get("floor_plan_id"),
                source_rect=self._effective_pdf_source_rect(page),
                show_background=show_bg,
                show_heating=show_heating_groups,
                show_elektro=show_elektro_groups,
                element_visibility=page.get("element_visibility"),
                table_sections=page.get("table_sections"),
            )
            return
        if ptype == "elektro":
            self._pdf_elektro_page(
                ctx,
                data,
                title=title,
                floor_plan_id=page.get("floor_plan_id"),
                source_rect=self._effective_pdf_source_rect(page),
                show_background=show_bg,
                show_heating=show_heating_groups,
                show_elektro=show_elektro_groups,
                element_visibility=page.get("element_visibility"),
                table_sections=page.get("table_sections"),
            )
            return

        self._pdf_plan_page(
            ctx,
            title,
            layer="all",
            floor_plan_id=page.get("floor_plan_id"),
            source_rect=self._effective_pdf_source_rect(page),
            show_background=show_bg,
            show_heating=show_heating_groups,
            show_elektro=show_elektro_groups,
            element_visibility=page.get("element_visibility"),
        )

    def _pdf_plan_page(self, ctx: '_PdfContext', title: str,
                       layer: str = "all",
                       floor_plan_id: str | None = None,
                       source_rect: QRectF | None = None,
                       show_background: bool | None = None,
                       show_heating: bool | None = None,
                       show_elektro: bool | None = None,
                       element_visibility: dict | None = None):
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

        self._render_plan_to_painter(
            ctx.painter,
            draw_rect,
            layer=layer,
            floor_plan_id=floor_plan_id,
            source_rect=source_rect or self._default_source_rect(),
            show_background=show_background,
            show_heating=show_heating,
            show_elektro=show_elektro,
            element_visibility=element_visibility,
            rasterize=True,
        )

    # ── Seite: Rohrlängen ──

    def _pdf_lengths_page(self, ctx: '_PdfContext', data: dict,
                          title: str = "Heizkreise – Rohrlängen"):
        page = ctx.page_rect()
        ctx.stamp(page)
        y = ctx.title(page, title,
                      f"Vorlauf {data['t_supply']:.1f} °C / "
                      f"Rücklauf {data['t_return']:.1f} °C")
        headers = ["Name", "Durchm. (mm)", "Abstand (mm)",
             "Rohr (m)", "Zuleitung (m)", "Gesamt (m)", "Umfang (m)", "Fläche (m²)"]
        rows = [[r["name"], f"{r['diameter_mm']:.1f}",
                 f"{r['spacing_mm']:.1f}",
                 f"{r['route_m']:.2f}", f"{r['supply_m']:.2f}",
              f"{r['total_m']:.2f}", f"{r.get('perimeter_m', 0.0):.2f}", f"{r.get('area_m2', 0.0):.2f}"]
                for r in data["hk_rows"]]
        ctx.draw_table(page, y, headers, rows)

    # ── Seite: Hydraulik & Abgleich ──

    def _pdf_hydraulics_page(self, ctx: '_PdfContext', data: dict,
                             title: str = "Hydraulische Übersicht & Abgleich"):
        page = ctx.page_rect()
        ctx.stamp(page)
        y = ctx.title(page, title,
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

    # ── Seite: Heizung (Plan + optional Tabellen) ──

    def _pdf_heating_page(self, ctx: '_PdfContext', data: dict,
                          title: str = "Fußbodenheizung – Verlegeplan",
                          floor_plan_id: str | None = None,
                          source_rect: QRectF | None = None,
                          show_background: bool | None = True,
                          show_heating: bool | None = True,
                          show_elektro: bool | None = False,
                          element_visibility: dict | None = None,
                          table_sections: list[str] | None = None):
        page = ctx.page_rect()
        ctx.stamp(page)

        sections = set(table_sections or ["hk_lengths", "hk_hydraulics", "hk_hkv_lines"])

        title_h = ctx.mm(8)
        ctx.painter.save()
        ctx.painter.setFont(QFont("Arial", 14, QFont.Bold))
        ctx.painter.drawText(
            QRectF(page.x(), page.y(), page.width(), title_h),
            Qt.AlignCenter,
            title,
        )
        ctx.painter.restore()

        plan_top = page.y() + title_h + ctx.mm(2)
        plan_h = page.height() - title_h - ctx.mm(6)
        plan_rect = QRectF(page.x(), plan_top, page.width(), plan_h)
        self._render_plan_to_painter(
            ctx.painter,
            plan_rect,
            layer="all",
            floor_plan_id=floor_plan_id,
            source_rect=source_rect or self._default_source_rect(),
            show_background=show_background,
            show_heating=show_heating,
            show_elektro=show_elektro,
            element_visibility=element_visibility,
            rasterize=True,
        )

        table_available = (
            ("hk_lengths" in sections and data.get("hk_rows"))
            or ("hk_hydraulics" in sections and data.get("hk_rows"))
            or ("hk_hkv_lines" in sections and data.get("hl_rows"))
        )
        if not table_available:
            return

        if "hk_lengths" in sections and data.get("hk_rows"):
            page = ctx.new_page(toc_title=f"{title} – Rohrlängen")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "Rohrlängen")
            headers = ["Name", "Durchm. (mm)", "Abstand (mm)", "Rohr (m)", "Zuleitung (m)", "Gesamt (m)"]
            rows = [
                [
                    r["name"],
                    f"{r['diameter_mm']:.1f}",
                    f"{r['spacing_mm']:.1f}",
                    f"{r['route_m']:.2f}",
                    f"{r['supply_m']:.2f}",
                    f"{r['total_m']:.2f}",
                ]
                for r in data["hk_rows"]
            ]
            ctx.draw_table(page, y_after, headers, rows)

        if "hk_hydraulics" in sections and data.get("hk_rows"):
            page = ctx.new_page(toc_title=f"{title} – Hydraulik & Abgleich")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "Hydraulik & Abgleich")
            headers = ["Name", "HKV", "Leistung (W)", "V̇ (l/min)", "Δp Rohr (mbar)"]
            rows = [
                [
                    r["name"],
                    r.get("distributor", ""),
                    f"{r.get('power_w', 0.0):.0f}",
                    f"{r.get('volume_flow_lmin', 0.0):.2f}",
                    f"{r.get('pressure_drop_mbar', 0.0):.1f}",
                ]
                for r in data["hk_rows"]
            ]
            ctx.draw_table(page, y_after, headers, rows)

        if "hk_hkv_lines" in sections and data.get("hl_rows"):
            page = ctx.new_page(toc_title=f"{title} – HKV-Leitungen")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "HKV-Leitungen")
            headers = ["Name", "Typ", "Start-HKV", "End-HKV", "Länge (m)"]
            rows = [
                [
                    r["name"], r["type"],
                    r.get("start_hkv", ""), r.get("end_hkv", ""),
                    f"{r.get('length_m', 0.0):.2f}",
                ]
                for r in data["hl_rows"]
            ]
            ctx.draw_table(page, y_after, headers, rows)

    # ── Seite: Elektro (Plan + Tabelle) ──

    def _pdf_elektro_page(self, ctx: '_PdfContext', data: dict,
                          title: str = "Elektro – Übersicht",
                          floor_plan_id: str | None = None,
                          source_rect: QRectF | None = None,
                          show_background: bool | None = True,
                          show_heating: bool | None = False,
                          show_elektro: bool | None = True,
                          element_visibility: dict | None = None,
                          table_sections: list[str] | None = None):
        """Elektro page: plan with only elektro elements, then table below."""
        page = ctx.page_rect()
        ctx.stamp(page)

        sections = set(table_sections or [
            "el_kabel", "el_ap_types", "el_ap_connections", "el_rooms",
            "el_ap_infos", "el_uv", "el_up_distribution", "el_bom", "el_uv_busbars",
            "schaltplan_uv", "schaltplan_stromkreise", "schaltplan_hierarchie",
        ])

        title_h = ctx.mm(8)
        ctx.painter.save()
        ctx.painter.setFont(QFont("Arial", 14, QFont.Bold))
        ctx.painter.drawText(
            QRectF(page.x(), page.y(), page.width(), title_h),
            Qt.AlignCenter,
            title,
        )
        ctx.painter.restore()

        # Upper half: plan image (elektro only)
        plan_top = page.y() + title_h + ctx.mm(2)
        plan_h = page.height() - title_h - ctx.mm(6)
        plan_rect = QRectF(page.x(), plan_top, page.width(), plan_h)
        self._render_plan_to_painter(
            ctx.painter,
            plan_rect,
            layer="all",
            floor_plan_id=floor_plan_id,
            source_rect=source_rect or self._default_source_rect(),
            show_background=show_background,
            show_heating=show_heating,
            show_elektro=show_elektro,
            element_visibility=element_visibility,
            rasterize=True,
        )

        table_available = (
            ("el_kabel" in sections and data.get("kv_rows"))
            or ("el_ap_types" in sections and data.get("ap_type_counts"))
            or ("el_ap_connections" in sections and data.get("ap_cables"))
            or ("el_rooms" in sections and data.get("room_ap_connections"))
            or ("el_ap_infos" in sections and data.get("ap_info_rows"))
            or ("el_uv" in sections and (data.get("uv_data") or data.get("uv_rows")))
            or ("el_up_distribution" in sections and data.get("up_distribution_rows"))
            or ("el_bom" in sections and data.get("cable_bom_rows"))
            or ("el_uv_busbars" in sections and data.get("uv_busbar_bom_rows"))
            or ("schaltplan_uv" in sections and data.get("uv_data"))
            or ("schaltplan_stromkreise" in sections and data.get("uv_data"))
            or ("schaltplan_hierarchie" in sections and self.param_panel.elec_point_panels)
        )
        if not table_available:
            return

        if "el_kabel" in sections and data.get("kv_rows"):
            page = ctx.new_page(toc_title=f"{title} – Leitungen")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "Leitungen")
            headers = [
                "Name", "Typ", "Kabel-Notiz",
                "Start-AP", "Start-Gerät", "Start-Farbe", "Start-Notiz", "Start-H. (cm)",
                "End-AP", "End-Gerät", "End-Farbe", "End-Notiz", "End-H. (cm)",
                "Länge (m)",
            ]
            rows = [[r["name"], r["type"],
                     r.get("comment", ""),
                     r.get("start_ap", ""), r.get("start_device", ""), r.get("start_device_color", ""), r.get("start_note", ""), f"{r.get('start_height_cm', 0.0):.1f}",
                     r.get("end_ap", ""), r.get("end_device", ""), r.get("end_device_color", ""), r.get("end_note", ""), f"{r.get('end_height_cm', 0.0):.1f}",
                     f"{r['length_m']:.2f}"]
                    for r in data["kv_rows"]]
            col_w = [1.0, 0.9, 1.5, 0.9, 0.9, 0.8, 1.2, 0.7, 0.9, 0.9, 0.8, 1.2, 0.7, 0.8]
            y_after = ctx.draw_table(page, y_after, headers, rows, col_widths=col_w)
            kv_sum = data.get("kv_sum", {})
            if kv_sum:
                y_after += ctx.mm(4)
                h2 = ["Leitungstyp", "Gesamtlänge (m)"]
                r2 = [[t, f"{kv_sum[t]:.2f}"] for t in sorted(kv_sum.keys())]
                ctx.draw_table(page, y_after, h2, r2)

        ap_type_counts = data.get("ap_type_counts", {})
        if "el_ap_types" in sections and ap_type_counts:
            page = ctx.new_page(toc_title=f"{title} – AP-Typen")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "Anschlusspunkt-Typen")
            h_types = ["Typ", "Anzahl"]
            r_types = [[t, str(ap_type_counts[t])] for t in sorted(ap_type_counts.keys())]
            ctx.draw_table(page, y_after, h_types, r_types)

        ap_cables = data.get("ap_cables", {})
        if "el_ap_connections" in sections and ap_cables:
            page = ctx.new_page(toc_title=f"{title} – AP-Anschlüsse")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "Anschlusspunkte – Kabelzuordnung")
            ap_headers = ["AP", "Kabel", "Typ", "Anschluss", "Gerät", "Farbe", "AP-Notiz", "Kabel-Notiz", "Länge (m)"]
            ap_rows = []
            for ap_name in sorted(ap_cables.keys()):
                for c in ap_cables[ap_name]:
                    ap_rows.append([
                        ap_name, c["cable"], c["type"], c["role"],
                        c.get("ap_device", ""), c.get("ap_device_color", ""),
                        c.get("ap_note", ""), c.get("cable_note", ""), f"{c['length_m']:.2f}",
                    ])
            ctx.draw_table(page, y_after, ap_headers, ap_rows)

        room_ap_connections = data.get("room_ap_connections", [])
        if "el_rooms" in sections and room_ap_connections:
            page = ctx.new_page(toc_title=f"{title} – Räume")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "AP-Zuordnung nach Räumen")
            room_headers = ["Raum", "AP", "Gerät", "Farbe", "AP-Notiz", "Kabel", "Typ", "Kabel-Notiz", "Führt zu AP", "Länge (m)"]
            room_rows = [
                [
                    r.get("room", ""), r.get("ap", ""),
                    r.get("ap_device", ""), r.get("ap_device_color", ""), r.get("ap_note", ""),
                    r.get("cable", ""), r.get("type", ""),
                    r.get("cable_note", ""), r.get("target_ap", ""),
                    f"{r.get('length_m', 0.0):.2f}",
                ]
                for r in room_ap_connections
            ]
            ctx.draw_table(page, y_after, room_headers, room_rows)

        ap_info_rows = data.get("ap_info_rows", [])
        if "el_ap_infos" in sections and ap_info_rows:
            page = ctx.new_page(toc_title=f"{title} – AP-Details")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "Anschlusspunkte – Details")
            headers = ["Name", "Art", "Raum", "Position", "Höhe (cm)", "Gerätefarbe", "Unterputz-Gerät", "Notiz"]
            rows = [
                [
                    r.get("name", ""), r.get("type", ""), r.get("room", ""),
                    r.get("position", ""), f"{r.get('height_cm', 0.0):.1f}",
                    r.get("device_color", ""), r.get("device", ""), r.get("note", ""),
                ]
                for r in ap_info_rows
            ]
            ctx.draw_table(page, y_after, headers, rows)

        uv_rows = data.get("uv_rows", [])
        uv_data = data.get("uv_data", [])
        if "el_uv" in sections and uv_data:
            for uv in uv_data:
                page = ctx.new_page(toc_title=f"{title} – UV: {uv.get('ap_name', '')}")
                y_after = ctx.title(page, f"{title} – UV: {uv.get('ap_name', '')}")
                y_after = ctx.draw_uv_schematic(page, y_after, uv)
        elif "el_uv" in sections and uv_rows:
            # Fallback: flat table for projects without uv_data
            y_after += ctx.mm(4)
            headers = ["UV", "Raum", "Raster", "Reihe", "TE", "Belegung", "Kennz.", "Bezeichnung", "Kabel/Stromkreis", "Notiz"]
            rows = [
                [
                    r.get("ap", ""),
                    r.get("room", ""),
                    f"{r.get('rows', 0)}x{r.get('modules_per_row', 0)}",
                    str(r.get("row", "")),
                    str(r.get("slot", "")),
                    r.get("device_type", ""),
                    r.get("spec", ""),
                    r.get("label", ""),
                    r.get("assignment", ""),
                    r.get("note", ""),
                ]
                for r in uv_rows
            ]
            y_after = ctx.draw_table(page, y_after, headers, rows)

        # Schaltplan-Seiten
        if any(k in sections for k in {"schaltplan_uv", "schaltplan_stromkreise", "schaltplan_hierarchie"}):
            sp_ap_nodes, sp_cable_edges = self._build_elec_schema_data()
            sp_ap_nodes_dict = {n.point_id: n for n in sp_ap_nodes}
            sp_cable_edges_dict = {e.cable_id: e for e in sp_cable_edges}
            sp_room_map = self._collect_point_id_to_room_name()

            if "schaltplan_uv" in sections:
                for uv in uv_data:
                    page = ctx.new_page(toc_title=f"Schaltplan UV: {uv.get('ap_name', '')}")
                    y_after = ctx.title(page, f"Schaltplan – UV: {uv.get('ap_name', '')}")
                    y_after = ctx.draw_uv_schematic(page, y_after, uv)

            if "schaltplan_stromkreise" in sections:
                for uv in uv_data:
                    uv_id = str(uv.get("ap_id", "") or "")
                    uv_name = str(uv.get("ap_name", uv_id) or uv_id)
                    circuits = get_uv_circuits(
                        uv_id,
                        sp_ap_nodes_dict,
                        sp_cable_edges_dict,
                        sp_room_map,
                    )
                    if not circuits:
                        continue
                    page = ctx.new_page(toc_title=f"Stromkreise: {uv_name}")
                    y_after = ctx.title(page, f"Schaltplan – Stromkreise: {uv_name}")
                    y_after = ctx.section_heading(page, y_after, "Stromkreiszuordnung")
                    headers = ["Reihe", "TE", "Gerät", "Kennz.", "Bezeichnung", "Kabel", "Verbraucher", "Raum", "Notiz"]
                    rows = [
                        [
                            str(c.get("row", "")),
                            str(c.get("slot", "")),
                            str(c.get("device_type", "")),
                            str(c.get("spec", "")),
                            str(c.get("label", "")),
                            str(c.get("cable_id", "")),
                            str(c.get("end_ap_name", "")),
                            str(c.get("end_ap_room", "")),
                            str(c.get("note", "")),
                        ]
                        for c in circuits
                    ]
                    ctx.draw_table(page, y_after, headers, rows)

            if "schaltplan_hierarchie" in sections:
                hierarchy = build_uv_hierarchy(sp_ap_nodes_dict, sp_cable_edges_dict)

                def _flatten_edges(nodes: list[dict], parent: dict | None = None, out: list[list[str]] | None = None) -> list[list[str]]:
                    out = out if out is not None else []
                    for n in nodes:
                        if parent is not None:
                            out.append([
                                str(parent.get("ap_type", "")).upper(),
                                str(parent.get("name", "")),
                                str(n.get("ap_type", "")).upper(),
                                str(n.get("name", "")),
                            ])
                        _flatten_edges(n.get("children", []), n, out)
                    return out

                rows = _flatten_edges(hierarchy)
                if rows:
                    page = ctx.new_page(toc_title="Schaltplan-Hierarchie")
                    y_after = ctx.title(page, "Schaltplan – Hierarchie")
                    y_after = ctx.section_heading(page, y_after, "Versorgungsstruktur")
                    headers = ["Typ Quelle", "Quelle", "Typ Ziel", "Ziel"]
                    ctx.draw_table(page, y_after, headers, rows)
                else:
                    page = ctx.new_page(toc_title="Schaltplan-Hierarchie")
                    y_after = ctx.title(page, "Schaltplan – Hierarchie")
                    y_after = ctx.section_heading(page, y_after, "Versorgungsstruktur")
                    ctx.painter.drawText(
                        QRectF(page.x(), y_after + ctx.mm(4), page.width(), ctx.mm(10)),
                        Qt.AlignLeft | Qt.AlignTop,
                        "Keine Hierarchie-Verbindungen vorhanden.",
                    )

        up_distribution_rows = data.get("up_distribution_rows", [])
        if "el_up_distribution" in sections and up_distribution_rows:
            page = ctx.new_page(toc_title=f"{title} – UP-Verteilungen")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "Unterputz-Verteilungen")
            headers = [
                "AP", "Raum", "Zuleitung", "Abgänge",
                "Ader (Zul.)", "Abgehendes Kabel", "Ader (Abg.)",
                "Zuordn.-Notiz", "Verteilungs-Notiz",
            ]
            rows = [
                [
                    r.get("ap", ""),
                    r.get("room", ""),
                    r.get("incoming_cable", ""),
                    r.get("outgoing_cables", ""),
                    r.get("from_conductor", ""),
                    r.get("to_cable", ""),
                    r.get("to_conductor", ""),
                    r.get("mapping_note", ""),
                    r.get("distribution_note", ""),
                ]
                for r in up_distribution_rows
            ]
            ctx.draw_table(page, y_after, headers, rows)

        if "el_bom" in sections:
            bom_rows = []
            for section_name, key in [
                ("Elektro-Kabel", "cable_bom_rows"),
                ("Anschlusspunkte", "ap_bom_rows"),
                ("HKV-Leitungen", "hkv_line_bom_rows"),
                ("UV-Geräte", "uv_device_bom_rows"),
                ("UV-Phasenschienen", "uv_busbar_bom_rows"),
                ("Manuelle Positionen", "custom_bom_rows"),
            ]:
                for row in data.get(key, []) or []:
                    note = ""
                    if key == "uv_device_bom_rows":
                        note = f"TE: {row.get('te_total', 0)}"
                    elif key == "uv_busbar_bom_rows":
                        note = f"TE {row.get('te_start', '')}-{row.get('te_end', '')}; UV: {row.get('uv_names', '')}"
                    else:
                        note = str(row.get("note", "") or "")
                    bom_rows.append([
                        section_name,
                        str(row.get("description", "")),
                        str(row.get("manufacturer", "")),
                        str(row.get("article_number", "")),
                        str(row.get("unit", "")),
                        f"{float(row.get('quantity', 0.0) or 0.0):.2f}",
                        note,
                    ])
            if bom_rows:
                page = ctx.new_page(toc_title=f"{title} – Stückliste")
                y_after = ctx.title(page, title)
                y_after = ctx.section_heading(page, y_after, "Stückliste")
                headers = ["Bereich", "Artikel", "Hersteller", "Artikelnummer", "Einheit", "Menge", "Notiz"]
                ctx.draw_table(page, y_after, headers, bom_rows)

        if "el_uv_busbars" in sections and data.get("uv_busbar_bom_rows"):
            page = ctx.new_page(toc_title=f"{title} – UV-Phasenschienen")
            y_after = ctx.title(page, title)
            y_after = ctx.section_heading(page, y_after, "UV-Phasenschienen")
            headers = ["Phase", "TE Start", "TE Ende", "Menge (TE)", "UV"]
            rows = [
                [
                    str(r.get("phase", "")),
                    str(r.get("te_start", "")),
                    str(r.get("te_end", "")),
                    f"{float(r.get('quantity', 0.0) or 0.0):.2f}",
                    str(r.get("uv_names", "")),
                ]
                for r in data.get("uv_busbar_bom_rows", [])
            ]
            ctx.draw_table(page, y_after, headers, rows)


# ====================================================================== #
#  PDF rendering helper                                                    #
# ====================================================================== #

class _PdfContext:
    """DPI-aware helper for painting onto a QPrinter (PDF output)."""

    def __init__(self, printer: QPrinter, painter: QPainter, dpi: int,
                 dry_run: bool = False):
        self.printer = printer
        self.painter = painter
        self.dpi = dpi
        self._dry_run = dry_run
        # QPainter on a QPrinter maps (0,0) to the top-left of the
        # printable area (inside margins).  pageRect gives size only;
        # we must NOT use its x/y offset, otherwise margins are doubled.
        pr = printer.pageRect(QPrinter.Unit.DevicePixel)
        self._pr = QRectF(0, 0, pr.width(), pr.height())
        self._page_num: int = 1
        self._toc: list[tuple[str, int]] = []  # (title, page_number)

    def mm(self, millimeters: float) -> float:
        return millimeters * self.dpi / 25.4

    def page_rect(self) -> QRectF:
        return QRectF(self._pr)

    # ── page helpers ────────────────────────────────────────────── #

    def new_page(self, toc_title: str = "") -> QRectF:
        """Start a new printer page, increment counter, optionally record TOC.
        Returns the new page rect. Also calls stamp()."""
        if not self._dry_run:
            self.printer.newPage()
        self._page_num += 1
        if toc_title:
            self.record_toc(toc_title)
        p = self.page_rect()
        if not self._dry_run:
            self.stamp(p)
        return p

    def record_toc(self, title: str):
        """Record current page number in the TOC list."""
        self._toc.append((title, self._page_num))

    def section_heading(self, page: QRectF, y: float, text: str) -> float:
        """Draw a bold section heading above a table. Returns y after heading."""
        h = self.mm(6)
        self.painter.save()
        self.painter.setFont(QFont("Arial", 10, QFont.Bold))
        self.painter.setPen(Qt.black)
        self.painter.fillRect(
            QRectF(page.x(), y, page.width(), h),
            QBrush(QColor("#e8edf5")),
        )
        from PySide6.QtGui import QPen as _QPen
        self.painter.setPen(_QPen(QColor("#aaaaaa"), max(1, self.mm(0.2))))
        self.painter.drawRect(QRectF(page.x(), y, page.width(), h))
        self.painter.setPen(Qt.black)
        self.painter.drawText(
            QRectF(page.x() + self.mm(2), y, page.width() - self.mm(4), h),
            Qt.AlignVCenter | Qt.AlignLeft,
            text,
        )
        self.painter.restore()
        return y + h + self.mm(2)

    def draw_toc(self):
        """Render the table of contents as the last page (legacy / fallback)."""
        if not self._toc:
            return
        self.printer.newPage()
        self._page_num += 1
        page = self.page_rect()
        self.stamp(page)
        self._draw_toc_on_current_page(self._toc, page)

    def draw_toc_on_first_page(self, toc_entries: list[tuple[str, int]]):
        """Render TOC on the very first printer page (call before any content)."""
        page = self.page_rect()
        self.stamp(page)
        self._draw_toc_on_current_page(toc_entries, page)

    def _draw_toc_on_current_page(self, entries: list[tuple[str, int]], page: QRectF):
        """Render TOC entries on the given page rect."""
        from PySide6.QtGui import QPen, QBrush
        y = self.title(page, "Inhaltsverzeichnis")
        row_h = self.mm(6)
        dot_col_w = page.width() - self.mm(20)
        page_col_w = self.mm(20)
        self.painter.save()
        self.painter.setFont(QFont("Arial", 9))
        for title_text, page_no in entries:
            if y + row_h > page.bottom() - self.mm(10):
                break  # single-page TOC
            self.painter.setPen(Qt.black)
            self.painter.drawText(
                QRectF(page.x(), y, dot_col_w, row_h),
                Qt.AlignVCenter | Qt.AlignLeft,
                title_text,
            )
            self.painter.drawText(
                QRectF(page.x() + dot_col_w, y, page_col_w - self.mm(2), row_h),
                Qt.AlignVCenter | Qt.AlignRight,
                str(page_no),
            )
            fm = self.painter.fontMetrics()
            title_w = fm.horizontalAdvance(title_text) + self.mm(2)
            pn_w = fm.horizontalAdvance(str(page_no)) + self.mm(4)
            dots_x0 = page.x() + title_w
            dots_x1 = page.x() + dot_col_w + page_col_w - pn_w
            dot_gap = self.mm(2.5)
            px = dots_x0
            self.painter.setPen(QColor("#aaaaaa"))
            while px + dot_gap < dots_x1:
                cy = y + row_h / 2 + self.mm(0.5)
                self.painter.drawPoint(int(px), int(cy))
                px += dot_gap
            y += row_h + self.mm(1)
        self.painter.restore()

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
        # page number centred at bottom
        pw = self.mm(30)
        self.painter.drawText(
            QRectF(rect.x() + (rect.width() - pw) / 2,
                   rect.bottom() - th, pw, th),
            Qt.AlignHCenter | Qt.AlignBottom,
            f"Seite {self._page_num}",
        )
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
        min_row_h = self.mm(5.5)
        cell_pad = self.mm(1.2)
        x0 = page.x()
        top_margin = self.mm(6)
        bottom_margin = self.mm(6)

        self.painter.setFont(QFont("Arial", 7))
        fm = self.painter.fontMetrics()

        def _cell_height(text: str, col_w: float) -> float:
            inner_w = max(1, int(col_w - 2 * cell_pad))
            br = fm.boundingRect(0, 0, inner_w, 100000,
                                  Qt.TextWordWrap | Qt.AlignLeft, str(text))
            return br.height() + 2 * cell_pad

        def _row_height(row_cells: list) -> float:
            h = min_row_h
            for j, cell in enumerate(row_cells):
                if cell:
                    h = max(h, _cell_height(str(cell),
                                            widths[j] if j < len(widths) else self.mm(20)))
            return h

        def _page_bottom() -> float:
            return page.bottom() - bottom_margin

        def _new_page():
            if not self._dry_run:
                self.printer.newPage()
            self._page_num += 1
            new_page = self.page_rect()
            if not self._dry_run:
                self.stamp(new_page)
            return new_page

        def _draw_header(y_pos: float):
            self.painter.setFont(QFont("Arial", 7, QFont.Bold))
            cx = x0
            for j, h in enumerate(headers):
                r = QRectF(cx, y_pos, widths[j], header_h)
                self.painter.fillRect(r, QBrush(QColor("#e0e0e0")))
                self.painter.drawRect(r)
                self.painter.drawText(
                    r.adjusted(cell_pad, 0, -cell_pad, 0),
                    Qt.AlignCenter | Qt.TextWordWrap, h)
                cx += widths[j]

        y = y_start
        if y + header_h > _page_bottom():
            page = _new_page()
            x0 = page.x()
            y = page.y() + top_margin

        self.painter.save()
        self.painter.setPen(QPen(Qt.black, max(1, self.mm(0.15))))

        # Header
        _draw_header(y)
        y += header_h

        # Data rows
        self.painter.setFont(QFont("Arial", 7))
        for ri, row in enumerate(rows):
            rh = _row_height(row)
            if y + rh > _page_bottom():
                page = _new_page()
                x0 = page.x()
                y = page.y() + top_margin
                _draw_header(y)
                y += header_h
                self.painter.setFont(QFont("Arial", 7))

            if ri % 2 == 1:
                cx = x0
                for j in range(n_cols):
                    self.painter.fillRect(
                        QRectF(cx, y, widths[j], rh),
                        QBrush(QColor("#f5f5f5")))
                    cx += widths[j]
            cx = x0
            for j, cell in enumerate(row):
                r = QRectF(cx, y, widths[j], rh)
                self.painter.drawRect(r)
                align = ((Qt.AlignRight | Qt.AlignTop)
                         if j >= 2
                         else (Qt.AlignLeft | Qt.AlignTop))
                self.painter.drawText(
                    r.adjusted(cell_pad, cell_pad, -cell_pad, -cell_pad),
                    align | Qt.TextWordWrap, str(cell))
                cx += widths[j]
            y += rh

        self.painter.restore()
        return y

    # ── UV visual schematic ─────────────────────────────────────── #

    # Colours/abbreviations mirrored from UvRailWidget in parameter_panel.py
    _UV_COLORS: dict[str, str] = {
        "":                    "#aaaaaa",
        "Reserve":             "#888888",
        "Hauptschalter":       "#c0392b",
        "LS":                  "#1553b5",
        "LS 3-polig":          "#0d3d8a",
        "FI":                  "#b85d10",
        "FI 4-polig":          "#8a3a00",
        "FI/LS":               "#6b22bf",
        "\u00dcberspannungsschutz": "#9b0000",
        "Motorschutz":         "#1a7a3a",
        "Sch\u00fctz":              "#007070",
        "Zeitschalter":        "#5a5a00",
        "Klemme":              "#9a7000",
        "Steckdose UV":        "#2c6e49",
        "Freitext":            "#444444",
    }
    _UV_SHORT: dict[str, str] = {
        "":                    "",
        "Reserve":             "Res",
        "Hauptschalter":       "HS",
        "LS":                  "LS",
        "LS 3-polig":          "LS3",
        "FI":                  "FI",
        "FI 4-polig":          "FI4",
        "FI/LS":               "FI/L",
        "\u00dcberspannungsschutz": "\u00dcSS",
        "Motorschutz":         "MOT",
        "Sch\u00fctz":              "SCH",
        "Zeitschalter":        "Zeit",
        "Klemme":              "KL",
        "Steckdose UV":        "SD",
        "Freitext":            "...",
    }

    def draw_uv_schematic(self, page: QRectF, y_start: float,
                          uv: dict) -> float:
        """Draw one UV as a visual DIN-rail schematic + compact assignment table.

        Returns the y position after the last drawn element.
        """
        from PySide6.QtGui import QPen, QBrush, QFont
        from PySide6.QtCore import Qt

        rows = int(uv.get("rows", 0) or 0)
        mpr = int(uv.get("modules_per_row", 0) or 0)
        slots_list: list[dict] = uv.get("slots", [])
        busbars_list: list[dict] = list(uv.get("busbars", []) or [])
        ap_name = str(uv.get("ap_name", "") or "")
        room = str(uv.get("room", "") or "")
        preset = str(uv.get("preset", "") or "")

        if rows < 1 or mpr < 1:
            return y_start

        bottom_margin = self.mm(6)

        def _page_bottom() -> float:
            return page.bottom() - bottom_margin

        def _new_page() -> QRectF:
            if not self._dry_run:
                self.printer.newPage()
            self._page_num += 1
            p = self.page_rect()
            if not self._dry_run:
                self.stamp(p)
            return p

        # slot lookup
        slot_map: dict[tuple[int, int], dict] = {}
        for s in slots_list:
            try:
                slot_map[(int(s["row"]), int(s["slot"]))] = s
            except (KeyError, TypeError, ValueError):
                pass

        # layout sizes
        avail_w = page.width()
        slot_w = min(self.mm(15), avail_w / mpr)
        slot_h = self.mm(13)
        rail_h = self.mm(2.5)
        te_num_h = self.mm(3.5)
        row_band_h = te_num_h + slot_h + rail_h + self.mm(1.5)
        left_margin = self.mm(8)
        header_h = self.mm(7)
        gap_after_header = self.mm(2)
        gap_between_rails = self.mm(4)

        occupied = [
            s for s in slots_list
            if str(s.get("device_type", "") or "").strip()
        ]
        tbl_row_h = self.mm(5)
        tbl_header_h = self.mm(7)

        cur_page = page
        y = y_start + self.mm(3)

        if y + self.mm(20) > _page_bottom():
            cur_page = _new_page()
            y = cur_page.y() + self.mm(4)

        x0 = cur_page.x()

        self.painter.save()

        # ── section header ──────────────────────────────────────── #
        subtitle_parts = [f"Raum: {room}", f"{rows}\u00d7{mpr} TE"]
        if preset:
            subtitle_parts.append(preset)
        subtitle = "  |  ".join(subtitle_parts)

        self.painter.fillRect(
            QRectF(x0, y, avail_w, header_h),
            QBrush(QColor("#d0d8e8")),
        )
        self.painter.setPen(QPen(QColor("#555555"), max(1, self.mm(0.2))))
        self.painter.drawRect(QRectF(x0, y, avail_w, header_h))
        self.painter.setPen(Qt.black)
        self.painter.setFont(QFont("Arial", 10, QFont.Bold))
        self.painter.drawText(
            QRectF(x0 + self.mm(2), y, avail_w * 0.5, header_h),
            Qt.AlignVCenter | Qt.AlignLeft, ap_name,
        )
        self.painter.setFont(QFont("Arial", 7))
        self.painter.drawText(
            QRectF(x0 + self.mm(2), y, avail_w - self.mm(4), header_h),
            Qt.AlignVCenter | Qt.AlignRight, subtitle,
        )
        y += header_h + gap_after_header

        # ── DIN-rail rows ───────────────────────────────────────── #
        font_te = QFont("Arial", 5)
        font_type = QFont("Arial", 6, QFont.Bold)
        font_lbl = QFont("Arial", 5)
        te_global_offset = 0

        for row_idx in range(rows):
            row_no = row_idx + 1

            if y + row_band_h > _page_bottom():
                cur_page = _new_page()
                y = cur_page.y() + self.mm(4)
                x0 = cur_page.x()

            # row label
            self.painter.setFont(QFont("Arial", 7, QFont.Bold))
            self.painter.setPen(QColor("#555555"))
            self.painter.drawText(
                QRectF(x0, y + te_num_h, left_margin - self.mm(1), slot_h + rail_h),
                Qt.AlignVCenter | Qt.AlignRight, f"R{row_no}",
            )

            rail_x0 = x0 + left_margin

            # TE numbers (continuous)
            self.painter.setFont(font_te)
            self.painter.setPen(QColor("#888888"))
            for te in range(1, mpr + 1):
                tx = rail_x0 + (te - 1) * slot_w
                self.painter.drawText(
                    QRectF(tx, y, slot_w, te_num_h),
                    Qt.AlignHCenter | Qt.AlignVCenter,
                    str(te_global_offset + te),
                )

            # slots
            te = 1
            while te <= mpr:
                sx = rail_x0 + (te - 1) * slot_w
                slot_data = slot_map.get((row_no, te))
                ts = max(1, int((slot_data or {}).get("te_size", 1) or 1))
                ts = min(ts, mpr - te + 1)
                sw = ts * slot_w
                sy = y + te_num_h
                device_type = str((slot_data or {}).get("device_type", "") or "").strip()
                color_hex = self._UV_COLORS.get(device_type, self._UV_COLORS[""])

                if slot_data and device_type:
                    self.painter.setBrush(QBrush(QColor(color_hex)))
                    self.painter.setPen(QPen(QColor("#222222"), max(1, self.mm(0.2))))
                    self.painter.drawRoundedRect(
                        QRectF(sx + self.mm(0.3), sy, sw - self.mm(0.6), slot_h),
                        self.mm(0.8), self.mm(0.8),
                    )
                    short = self._UV_SHORT.get(device_type, device_type[:4])
                    self.painter.setFont(font_type)
                    self.painter.setPen(QColor("#ffffff"))
                    self.painter.drawText(
                        QRectF(sx + self.mm(0.3), sy + self.mm(0.5),
                               sw - self.mm(0.6), slot_h * 0.38),
                        Qt.AlignHCenter | Qt.AlignVCenter, short,
                    )
                    spec = str(slot_data.get("spec", "") or "").strip()
                    if spec:
                        self.painter.setFont(QFont("Arial", 5))
                        self.painter.setPen(QColor("#ffe08a"))
                        self.painter.drawText(
                            QRectF(sx + self.mm(0.3), sy + slot_h * 0.4,
                                   sw - self.mm(0.6), slot_h * 0.28),
                            Qt.AlignHCenter | Qt.AlignVCenter, spec,
                        )
                    label = str(slot_data.get("label", "") or "").strip()
                    if label:
                        self.painter.setFont(font_lbl)
                        self.painter.setPen(QColor("#eeeeee"))
                        self.painter.drawText(
                            QRectF(sx + self.mm(0.4),
                                   sy + slot_h * (0.68 if spec else 0.5),
                                   sw - self.mm(0.8),
                                   slot_h * 0.30),
                            Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, label,
                        )
                else:
                    self.painter.setBrush(QBrush(QColor("#e8e8e8")))
                    self.painter.setPen(QPen(QColor("#cccccc"), max(1, self.mm(0.15))))
                    self.painter.drawRect(
                        QRectF(sx + self.mm(0.3), sy, sw - self.mm(0.6), slot_h)
                    )

                te += ts

            # DIN rail bar
            rail_y = y + te_num_h + slot_h
            self.painter.setBrush(QBrush(QColor("#999999")))
            self.painter.setPen(QPen(QColor("#777777"), max(1, self.mm(0.15))))
            self.painter.drawRect(QRectF(rail_x0, rail_y, mpr * slot_w, rail_h))
            # notch marks
            self.painter.setPen(QPen(QColor("#bbbbbb"), max(1, self.mm(0.15))))
            for te in range(0, mpr + 1, 2):
                nx = rail_x0 + te * slot_w
                self.painter.drawLine(
                    int(nx), int(rail_y + self.mm(0.5)),
                    int(nx), int(rail_y + rail_h - self.mm(0.5)),
                )

            # Busbar phase bands (thin colored strip between slot bottom and DIN rail)
            if busbars_list:
                bb_strip_h = self.mm(2.2)
                bb_y = rail_y - bb_strip_h
                row_te_start_g = te_global_offset + 1  # global TE of first slot in row
                row_te_end_g = te_global_offset + mpr
                _3p_colors = {"L1": "#e53935", "L2": "#43a047", "L3": "#1e88e5"}
                for bb in busbars_list:
                    bb_te_s = int(bb.get("te_start", 1) or 1)
                    bb_te_e = int(bb.get("te_end", 1) or 1)
                    vis_s = max(bb_te_s, row_te_start_g)
                    vis_e = min(bb_te_e, row_te_end_g)
                    if vis_s > vis_e:
                        continue
                    bb_col = str(bb.get("color", "#888888") or "#888888")
                    bb_phase = str(bb.get("phase", "") or "")
                    if bb_phase == "L1/L2/L3":
                        # Dreiphasige Sammelschiene: jede TE einzeln einfärben
                        for te_g in range(vis_s, vis_e + 1):
                            local_te = te_g - row_te_start_g  # 0-based within row
                            te_bx = rail_x0 + local_te * slot_w
                            ph = ("L1", "L2", "L3")[(te_g - bb_te_s) % 3]
                            self.painter.fillRect(
                                QRectF(te_bx, bb_y, slot_w, bb_strip_h),
                                QBrush(QColor(_3p_colors[ph])),
                            )
                            self.painter.setFont(QFont("Arial", 3, QFont.Bold))
                            self.painter.setPen(QColor("#ffffff"))
                            self.painter.drawText(
                                QRectF(te_bx + self.mm(0.2), bb_y,
                                       slot_w - self.mm(0.4), bb_strip_h),
                                Qt.AlignHCenter | Qt.AlignVCenter, ph,
                            )
                    elif bb_phase == "3~N":
                        # Vierphasige Sammelschiene mit N: L1→L2→L3→N zyklisch
                        _3pn_colors = {"L1": "#e53935", "L2": "#43a047", "L3": "#1e88e5", "N": "#1565c0"}
                        for te_g in range(vis_s, vis_e + 1):
                            local_te = te_g - row_te_start_g  # 0-based within row
                            te_bx = rail_x0 + local_te * slot_w
                            ph = ("L1", "L2", "L3", "N")[(te_g - bb_te_s) % 4]
                            self.painter.fillRect(
                                QRectF(te_bx, bb_y, slot_w, bb_strip_h),
                                QBrush(QColor(_3pn_colors[ph])),
                            )
                            self.painter.setFont(QFont("Arial", 3, QFont.Bold))
                            self.painter.setPen(QColor("#ffffff"))
                            self.painter.drawText(
                                QRectF(te_bx + self.mm(0.2), bb_y,
                                       slot_w - self.mm(0.4), bb_strip_h),
                                Qt.AlignHCenter | Qt.AlignVCenter, ph,
                            )
                    elif bb_phase == "3~N4":
                        # 3~N4: L1, L2, L3, N (einmalig an TE 4), dann L1/L2/L3 zyklisch
                        _3pn4_colors = {"L1": "#e53935", "L2": "#43a047", "L3": "#1e88e5", "N": "#1565c0"}

                        def _ph_3n4(idx: int) -> str:
                            if idx < 3:
                                return ("L1", "L2", "L3")[idx]
                            elif idx == 3:
                                return "N"
                            else:
                                return ("L1", "L2", "L3")[(idx - 4) % 3]

                        for te_g in range(vis_s, vis_e + 1):
                            local_te = te_g - row_te_start_g  # 0-based within row
                            te_bx = rail_x0 + local_te * slot_w
                            ph = _ph_3n4(te_g - bb_te_s)
                            self.painter.fillRect(
                                QRectF(te_bx, bb_y, slot_w, bb_strip_h),
                                QBrush(QColor(_3pn4_colors[ph])),
                            )
                            self.painter.setFont(QFont("Arial", 3, QFont.Bold))
                            self.painter.setPen(QColor("#ffffff"))
                            self.painter.drawText(
                                QRectF(te_bx + self.mm(0.2), bb_y,
                                       slot_w - self.mm(0.4), bb_strip_h),
                                Qt.AlignHCenter | Qt.AlignVCenter, ph,
                            )
                    else:
                        local_s = vis_s - row_te_start_g  # 0-based within row
                        local_e = vis_e - row_te_start_g  # 0-based within row
                        bx = rail_x0 + local_s * slot_w
                        bw_b = (local_e - local_s + 1) * slot_w
                        self.painter.fillRect(
                            QRectF(bx, bb_y, bw_b, bb_strip_h),
                            QBrush(QColor(bb_col)),
                        )
                        if bb_phase:
                            self.painter.setFont(QFont("Arial", 4, QFont.Bold))
                            self.painter.setPen(QColor("#ffffff"))
                            self.painter.drawText(
                                QRectF(bx + self.mm(0.5), bb_y, bw_b - self.mm(1), bb_strip_h),
                                Qt.AlignVCenter | Qt.AlignLeft, bb_phase,
                            )

            te_global_offset += mpr
            y += row_band_h + gap_between_rails

        # ── compact assignment table ─────────────────────────────── #
        if occupied:
            y += self.mm(2)
            if y + tbl_header_h + tbl_row_h > _page_bottom():
                cur_page = _new_page()
                y = cur_page.y() + self.mm(4)
                x0 = cur_page.x()

            tbl_headers = ["TE", "Typ", "Kennz.", "Bezeichnung", "Kabel/Stromkreis", "Notiz"]
            col_w_rel = [0.6, 1.2, 1.4, 2.0, 2.0, 1.5]
            total_rel = sum(col_w_rel)
            col_ws = [r / total_rel * avail_w for r in col_w_rel]

            self.painter.setFont(QFont("Arial", 7, QFont.Bold))
            self.painter.setPen(QPen(Qt.black, max(1, self.mm(0.15))))
            cx = x0
            for hi, hdr in enumerate(tbl_headers):
                r2 = QRectF(cx, y, col_ws[hi], tbl_header_h)
                self.painter.fillRect(r2, QBrush(QColor("#e0e0e0")))
                self.painter.drawRect(r2)
                self.painter.drawText(
                    r2.adjusted(self.mm(1), 0, -self.mm(1), 0),
                    Qt.AlignCenter, hdr,
                )
                cx += col_ws[hi]
            y += tbl_header_h

            self.painter.setFont(QFont("Arial", 7))
            for ri, s in enumerate(
                sorted(occupied, key=lambda x: (x.get("row", 0), x.get("slot", 0)))
            ):
                if y + tbl_row_h > _page_bottom():
                    cur_page = _new_page()
                    y = cur_page.y() + self.mm(4)
                    x0 = cur_page.x()
                    self.painter.setFont(QFont("Arial", 7, QFont.Bold))
                    cx2 = x0
                    for hi2, hdr2 in enumerate(tbl_headers):
                        r3 = QRectF(cx2, y, col_ws[hi2], tbl_header_h)
                        self.painter.fillRect(r3, QBrush(QColor("#e0e0e0")))
                        self.painter.drawRect(r3)
                        self.painter.drawText(
                            r3.adjusted(self.mm(1), 0, -self.mm(1), 0),
                            Qt.AlignCenter, hdr2,
                        )
                        cx2 += col_ws[hi2]
                    y += tbl_header_h
                    self.painter.setFont(QFont("Arial", 7))

                if ri % 2 == 1:
                    self.painter.fillRect(
                        QRectF(x0, y, avail_w, tbl_row_h),
                        QBrush(QColor("#f5f5f5")),
                    )

                row_no_s = int(s.get("row", 0) or 0)
                slot_no_s = int(s.get("slot", 0) or 0)
                te_global_num = (row_no_s - 1) * mpr + slot_no_s
                ts_s = max(1, int(s.get("te_size", 1) or 1))
                te_label = (str(te_global_num) if ts_s == 1
                            else f"{te_global_num}\u2013{te_global_num + ts_s - 1}")

                cells = [
                    te_label,
                    str(s.get("device_type", "") or ""),
                    str(s.get("spec", "") or ""),
                    str(s.get("label", "") or ""),
                    str(s.get("assignment", "") or ""),
                    str(s.get("note", "") or ""),
                ]
                self.painter.setPen(QPen(Qt.black, max(1, self.mm(0.15))))
                cx = x0
                for ci, cell in enumerate(cells):
                    r4 = QRectF(cx, y, col_ws[ci], tbl_row_h)
                    self.painter.drawRect(r4)
                    self.painter.drawText(
                        r4.adjusted(self.mm(1), 0, -self.mm(1), 0),
                        Qt.AlignVCenter | Qt.AlignLeft, cell,
                    )
                    cx += col_ws[ci]
                y += tbl_row_h

        # ── Busbar legend ────────────────────────────────────────── #
        if busbars_list:
            y += self.mm(3)
            leg_h = self.mm(5)
            leg_col_phase = avail_w * 0.20
            leg_col_range = avail_w * 0.25
            leg_col_rest = avail_w - leg_col_phase - leg_col_range

            if y + self.mm(7) + len(busbars_list) * leg_h > _page_bottom():
                cur_page = _new_page()
                y = cur_page.y() + self.mm(4)
                x0 = cur_page.x()

            # Legend header
            self.painter.setFont(QFont("Arial", 7, QFont.Bold))
            self.painter.setPen(QPen(Qt.black, max(1, self.mm(0.15))))
            hdr_rect = QRectF(x0, y, avail_w, self.mm(6))
            self.painter.fillRect(hdr_rect, QBrush(QColor("#e8eaf6")))
            self.painter.drawRect(hdr_rect)
            self.painter.drawText(
                hdr_rect.adjusted(self.mm(2), 0, 0, 0),
                Qt.AlignVCenter | Qt.AlignLeft, "Phasenschienen",
            )
            y += self.mm(6)

            for bb in busbars_list:
                bb_phase = str(bb.get("phase", "") or "")
                bb_col = str(bb.get("color", "#888888") or "#888888")
                bb_te_s = int(bb.get("te_start", 1) or 1)
                bb_te_e = int(bb.get("te_end", 1) or 1)
                row_rect = QRectF(x0, y, avail_w, leg_h)
                self.painter.setPen(QPen(Qt.black, max(1, self.mm(0.15))))
                self.painter.drawRect(row_rect)
                # Color swatch (or 3-phase gradient)
                swatch = QRectF(x0 + self.mm(1), y + self.mm(0.8), self.mm(4), leg_h - self.mm(1.6))
                if bb_phase == "L1/L2/L3":
                    sw = swatch.width() / 3
                    for ci, ph_col in enumerate(("#e53935", "#43a047", "#1e88e5")):
                        self.painter.fillRect(
                            QRectF(swatch.x() + ci * sw, swatch.y(), sw, swatch.height()),
                            QBrush(QColor(ph_col)),
                        )
                    self.painter.drawRect(swatch)
                else:
                    self.painter.fillRect(swatch, QBrush(QColor(bb_col)))
                    self.painter.drawRect(swatch)
                # Phase label
                self.painter.setFont(QFont("Arial", 7, QFont.Bold))
                self.painter.setPen(Qt.black)
                self.painter.drawText(
                    QRectF(x0 + self.mm(6), y, leg_col_phase, leg_h),
                    Qt.AlignVCenter | Qt.AlignLeft, bb_phase,
                )
                # TE range
                te_range = f"TE {bb_te_s}\u2013{bb_te_e}"
                self.painter.setFont(QFont("Arial", 7))
                self.painter.drawText(
                    QRectF(x0 + self.mm(6) + leg_col_phase, y, leg_col_range, leg_h),
                    Qt.AlignVCenter | Qt.AlignLeft, te_range,
                )
                y += leg_h

        self.painter.restore()
        return y + self.mm(4)
