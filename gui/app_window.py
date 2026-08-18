"""Neues Hauptfenster von HRouting.

Aufbau:

* zentrales Widget: die Zeichenfläche (eine Instanz für alle Workspaces)
* Workspace-Tabs oberhalb der Zeichenfläche
* alle weiteren Panels sind andockbare, frei verschiebbare QDockWidgets
* Fenster- und Docklayout wird global in QSettings gespeichert
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QDateTime, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QInputDialog,
    QMenu,
    QToolBar,
    QCheckBox,
    QDoubleSpinBox,
    QPushButton,
    QComboBox,
    QTabBar,
    QVBoxLayout,
    QWidget,
    QColorDialog,
)

from model.document import Document
from model.field_access import get_field
from model.elements import (
    AngleMeasurement,
    AnnotationCircle,
    AnnotationEllipse,
    AnnotationLine,
    AnnotationPolygon,
    AnnotationPolyline,
    AnnotationRectangle,
    Circuit,
    DistanceMeasurement,
    ElecCable,
    ElecPoint,
    ElecRoom,
    FloorPlan,
    Hkv,
    HkvLine,
    TextAnnotation,
)
from model.schema import schema_for
from storage.asset_data_uri import is_data_uri
from storage.hrp_io import load_document, repair_and_save_hrp, save_document
from logic.kicad_import import (
    KiCadBusCableCandidate,
    KiCadCableCandidate,
    KiCadSheetPinRef,
    KiCadScanResult,
    build_kicad_bus_cable_key,
    build_import_preview,
    scan_kicad_project,
    suggest_ap_matches,
)
from logic.hrp_import import import_selected_elements, iter_import_candidates
from .elec_schema_window import ApNode, CableEdge, ElecSchemaWindow
from .hrp_import_dialog import HrpImportDialog
from .kicad_import_dialog import KiCadImportDialog
from .pdf_export_dialog import PdfExportConfigDialog
from .schaltplan_window import SchaltplanWindow
from logic.schaltplan_generator import build_uv_hierarchy, get_uv_circuits

from . import layout_store
from .canvas_widget import CanvasWidget, ToolMode
from .docks import LogDock, NavigatorDock, PropertiesDock, ToolsDock
from .docks import ProjectOverviewDock
from .workspaces import (
    DEFAULT_WORKSPACE_ID,
    DockId,
    WORKSPACES,
    WorkspaceDefinition,
    workspace as workspace_by_id,
)

_MAX_UNDO_STEPS = 80
_UNDO_GROUP_IDLE_MS = 250
_LAST_PROJECT_KEY = "last_project_path"
_RECENT_KEY = "recent_projects"
_LAST_PDF_EXPORT_KEY = "last_pdf_export_path"
_LAST_SVG_EXPORT_KEY = "last_svg_export_path"
_MAX_RECENT = 8

_FILE_FILTER = "HRouting-Projekt (*.hrp);;Alle Dateien (*)"
_IMAGE_FILTER = "Bilder (*.png *.jpg *.jpeg *.svg);;Alle Dateien (*)"
_HELPER_NAV_ID_PREFIX = "NAV-HLP::"


def _parse_helper_nav_id(nav_id: str) -> tuple[str, str] | None:
    if not nav_id.startswith(_HELPER_NAV_ID_PREFIX):
        return None
    payload = nav_id[len(_HELPER_NAV_ID_PREFIX):]
    floor_id, sep, helper_id = payload.partition("::")
    if not sep or not floor_id or not helper_id:
        return None
    return floor_id, helper_id


def _parse_measurement_nav_id(nav_id: str) -> tuple[str, int] | None:
    """Gibt ``(prefix, list_index)`` zurück, wenn *nav_id* eine Messung ist.

    Messungs-IDs folgen dem Schema ``MSRD-N`` (Distanz) oder ``MSRA-N``
    (Winkel), wobei N ≥ 1.  Der zurückgegebene Index ist 0-basiert.
    """
    for prefix in ("MSRD", "MSRA"):
        if nav_id.startswith(prefix + "-"):
            rest = nav_id[len(prefix) + 1:]
            if rest.isdigit():
                n = int(rest)
                if n >= 1:
                    return prefix, n - 1
    return None


class AppWindow(QMainWindow):
    """Dock-basiertes Hauptfenster mit Workspace-Tabs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("HRouting")
        self.setDockNestingEnabled(True)
        self.setObjectName("hrouting_main")

        self._document = Document()
        self._project_path: Path | None = None
        self._dirty = False
        self._workspace: WorkspaceDefinition = workspace_by_id(DEFAULT_WORKSPACE_ID)
        self._copy_buffer: dict | None = None
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._undo_group_open = False
        self._last_document_snapshot: dict | None = None
        self._restoring_snapshot = False
        self._undo_group_timer = QTimer(self)
        self._undo_group_timer.setSingleShot(True)
        self._undo_group_timer.timeout.connect(self._finish_undo_group)
        self._undo_action: QAction | None = None
        self._redo_action: QAction | None = None
        self._recent_menu: QMenu | None = None
        self._grid_toolbar: QToolBar | None = None
        self._elec_schema_window: ElecSchemaWindow | None = None
        self._schaltplan_window: SchaltplanWindow | None = None
        self._elec_schema_ap_positions: dict[str, list[float]] = {}
        self._pdf_export_pages: list[dict] = []
        self._pdf_export_meta: dict[str, str] = {}
        self._annotation_live_value_cache: dict[str, tuple] = {}
        self._pending_annotation_refresh_id: str = ""
        self._annotation_live_refresh_timer = QTimer(self)
        self._annotation_live_refresh_timer.setSingleShot(True)
        self._annotation_live_refresh_timer.setInterval(33)
        self._annotation_live_refresh_timer.timeout.connect(
            self._flush_annotation_live_refresh
        )

        self._build_central()
        self._build_docks()
        self._build_menus()
        self._connect_signals()

        self._restore_layout()
        self._apply_workspace(layout_store.last_workspace(DEFAULT_WORKSPACE_ID))
        self._set_document(self._document)
        QTimer.singleShot(0, self._auto_load_last_project)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _build_central(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabBar(central)
        self._tabs.setExpanding(False)
        self._tabs.setDrawBase(False)
        self._tabs.setDocumentMode(True)
        for definition in WORKSPACES:
            index = self._tabs.addTab(definition.label)
            self._tabs.setTabData(index, definition.id)
        layout.addWidget(self._tabs)

        self.canvas = CanvasWidget(central)
        layout.addWidget(self.canvas, 1)

        self.setCentralWidget(central)
        self._build_grid_toolbar()
        self.statusBar().showMessage("Bereit")

    def _build_docks(self) -> None:
        self.navigator = NavigatorDock(self)
        self.properties = PropertiesDock(self)
        self.tools = ToolsDock(self)
        self.log = LogDock(self)
        self.overview_general = ProjectOverviewDock(
            self,
            title="Projektübersicht: Allgemein",
            object_name="dock_overview_general",
            visible_tabs=("Allgemein",),
        )
        self.overview_heating = ProjectOverviewDock(
            self,
            title="Projektübersicht: Heizung",
            object_name="dock_overview",
            visible_tabs=("Heizung",),
        )
        self.overview_electro = ProjectOverviewDock(
            self,
            title="Projektübersicht: Elektro",
            object_name="dock_overview_electro",
            visible_tabs=("Elektro",),
        )
        # Backward compatibility for tests/extensions that still use `window.overview`.
        self.overview = self.overview_heating

        self.addDockWidget(Qt.LeftDockWidgetArea, self.tools)
        self.addDockWidget(Qt.RightDockWidgetArea, self.navigator)
        self.addDockWidget(Qt.RightDockWidgetArea, self.properties)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.overview_general)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.overview_heating)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.overview_electro)
        self.log.hide()
        self.overview_general.hide()
        self.overview_heating.hide()
        self.overview_electro.hide()

        self._docks = {
            DockId.NAVIGATOR: self.navigator,
            DockId.PROPERTIES: self.properties,
            DockId.TOOLS: self.tools,
            DockId.LOG: self.log,
            DockId.OVERVIEW_GENERAL: self.overview_general,
            DockId.OVERVIEW_HEATING: self.overview_heating,
            DockId.OVERVIEW_ELECTRO: self.overview_electro,
        }

        for dock in self._docks.values():
            self._configure_floating_dock_window_hints(dock)

    def _configure_floating_dock_window_hints(self, dock) -> None:
        """Erlaubt Min/Max auf frei schwebenden Docks (v. a. unter Windows)."""
        dock.setWindowFlag(Qt.Tool, False)
        dock.setWindowFlag(Qt.Window, True)
        dock.setWindowFlag(Qt.CustomizeWindowHint, True)
        dock.setWindowFlag(Qt.WindowTitleHint, True)
        dock.setWindowFlag(Qt.WindowCloseButtonHint, True)
        dock.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        dock.setWindowFlag(Qt.WindowMaximizeButtonHint, True)

    def _on_dock_top_level_changed(self, floating: bool) -> None:
        if not floating:
            return
        dock = self.sender()
        if dock is None:
            return
        self._configure_floating_dock_window_hints(dock)
        dock.show()

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&Datei")
        self._add_action(file_menu, "Neues Projekt", self._new_project, QKeySequence.New)
        self._add_action(file_menu, "Projekt öffnen…", self._open_project, QKeySequence.Open)
        self._recent_menu = file_menu.addMenu("🕑 Letzte Projekte")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        self._add_action(file_menu, "Speichern", self._save_project, QKeySequence.Save)
        self._add_action(
            file_menu, "Speichern unter…", self._save_project_as, QKeySequence.SaveAs
        )
        file_menu.addSeparator()
        self._add_action(file_menu, "Projekt reparieren & bereinigen…", self._repair_project)
        self._add_action(
            file_menu,
            "Grundriss-Skalierungen aus Referenzlinien synchronisieren",
            self._sync_floorplan_scales_from_references,
        )
        file_menu.addSeparator()
        self._add_action(file_menu, "Beenden", self.close, QKeySequence.Quit)

        edit_menu = bar.addMenu("&Bearbeiten")
        self._undo_action = self._add_action(edit_menu, "Rückgängig", self._undo, QKeySequence.Undo)
        self._redo_action = self._add_action(edit_menu, "Wiederherstellen", self._redo, QKeySequence.Redo)
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Ausschneiden", self._cut_selected, QKeySequence.Cut)
        self._add_action(edit_menu, "Kopieren", self._copy_selected, QKeySequence.Copy)
        self._add_action(edit_menu, "Einfügen", self._paste_copied, QKeySequence.Paste)
        self._add_action(edit_menu, "Duplizieren", self._duplicate_selected, QKeySequence("Ctrl+D"))
        self._add_action(edit_menu, "Löschen", self._delete_selected, QKeySequence.Delete)
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Anschlusspunkte durchnummerieren", self._renumber_elec_points)

        add_menu = bar.addMenu("&Einfügen")
        self._add_action(add_menu, "Grundriss hinzufügen…", self._add_floorplan)
        self._add_action(add_menu, "Einrichtung hinzufügen…", self._add_furniture)
        add_menu.addSeparator()
        self._add_action(add_menu, "Heizkreis hinzufügen", self._add_circuit)
        self._add_action(add_menu, "Elektro-Punkt hinzufügen", self._add_elec_point)
        self._add_action(add_menu, "Elektro-Raum hinzufügen", self._add_elec_room)
        self._add_action(add_menu, "Elektro-Kabel hinzufügen", self._add_elec_cable)
        self._add_action(add_menu, "HKV hinzufügen", self._add_hkv)
        self._add_action(add_menu, "HKV-Leitung hinzufügen", self._add_hkv_line)
        self._add_action(add_menu, "Text hinzufügen", self._add_text)

        view_menu = bar.addMenu("&Ansicht")
        for dock_id, dock in self._docks.items():
            action = dock.toggleViewAction()
            action.setObjectName(f"toggle_{dock_id}")
            view_menu.addAction(action)
        view_menu.addSeparator()
        self._add_action(view_menu, "Elektro-Strangschema…", self._open_elec_schema_window)
        self._add_action(view_menu, "Schaltplan…", self._open_schaltplan_window)
        view_menu.addSeparator()
        self._add_action(view_menu, "Layout zurücksetzen", self._reset_layout)

        self.export_menu = bar.addMenu("&Export")
        self._add_action(self.export_menu, "PDF exportieren…", self._export_pdf)
        self._add_action(self.export_menu, "SVG exportieren…", self._export_svg)
        self._add_action(self.export_menu, "Längen & Stückliste…", self._export_lengths)

        import_menu = bar.addMenu("&Import")
        self._add_action(import_menu, "Aus HRP importieren…", self._import_hrp_elements)
        self._add_action(import_menu, "KiCad-Kabel aus Schaltplan…", self._import_kicad_cables)

        help_menu = bar.addMenu("&Hilfe")
        self._add_action(help_menu, "Über HRouting…", self._show_about)

    def _build_grid_toolbar(self) -> None:
        self._grid_toolbar = QToolBar("Raster", self)
        self._grid_toolbar.setObjectName("grid_toolbar")
        self._grid_toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self._grid_toolbar)

        self._grid_cb = QCheckBox("Raster", self._grid_toolbar)
        self._grid_cb.toggled.connect(self._on_grid_toggled)
        self._grid_toolbar.addWidget(self._grid_cb)

        self._grid_spin = QDoubleSpinBox(self._grid_toolbar)
        self._grid_spin.setRange(0.01, 1000.0)
        self._grid_spin.setDecimals(2)
        self._grid_spin.setSingleStep(0.05)
        self._grid_spin.setSuffix(" m")
        self._grid_spin.setToolTip("Rasterabstand")
        self._grid_spin.valueChanged.connect(self._on_grid_spacing_changed)
        self._grid_toolbar.addWidget(self._grid_spin)

        self._grid_color_btn = QPushButton("▦", self._grid_toolbar)
        self._grid_color_btn.setToolTip("Rasterfarbe")
        self._grid_color_btn.setFixedWidth(28)
        self._grid_color_btn.clicked.connect(self._on_grid_color_pick)
        self._grid_toolbar.addWidget(self._grid_color_btn)

        self._snap_combo = QComboBox(self._grid_toolbar)
        self._snap_combo.setToolTip("Winkelsnap")
        for label, angle in [("Aus", 0), ("15°", 15), ("30°", 30), ("45°", 45), ("90°", 90)]:
            self._snap_combo.addItem(label, angle)
        self._snap_combo.currentIndexChanged.connect(self._on_snap_angle_changed)
        self._grid_toolbar.addWidget(self._snap_combo)
        self._sync_grid_toolbar_from_canvas()

    def _sync_grid_toolbar_from_canvas(self) -> None:
        if self._grid_toolbar is None:
            return
        self._grid_cb.blockSignals(True)
        self._grid_cb.setChecked(self.canvas.grid_visible())
        self._grid_cb.blockSignals(False)

        self._grid_spin.blockSignals(True)
        self._grid_spin.setValue(self.canvas.grid_spacing_mm() / 1000.0)
        self._grid_spin.blockSignals(False)

        self._update_grid_color_btn(self.canvas.grid_color())

        angle = int(self.canvas.snap_angle())
        idx = self._snap_combo.findData(angle)
        if idx >= 0:
            self._snap_combo.blockSignals(True)
            self._snap_combo.setCurrentIndex(idx)
            self._snap_combo.blockSignals(False)

    def _add_action(self, menu, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _connect_signals(self) -> None:
        self._tabs.currentChanged.connect(self._on_tab_changed)
        for dock in self._docks.values():
            dock.topLevelChanged.connect(self._on_dock_top_level_changed)
        self.navigator.element_selected.connect(self._on_element_selected)
        self.navigator.selection_changed.connect(self._on_navigator_selection_changed)
        self.navigator.floorplan_activated.connect(self._on_floorplan_activated)
        self.navigator.visibility_changed.connect(self._on_visibility_changed)
        self.navigator.context_requested.connect(self._on_navigator_context)
        self.navigator.reassign_floorplan.connect(self._on_reassign_floorplan)
        self.navigator.floorplan_order_changed.connect(self._on_navigator_floorplan_order_changed)
        self.tools.tool_activated.connect(self._on_tool_activated)
        self.canvas.object_clicked.connect(self._on_canvas_object_clicked)
        self.canvas.object_double_clicked.connect(self._on_canvas_object_double_clicked)
        self.canvas.context_menu_requested.connect(self._on_canvas_context_requested)
        self.canvas.document_data_changed.connect(self._on_document_data_changed)
        self.canvas.polygon_finished.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_room_polygon_finished.connect(self._on_canvas_mutation_signal)
        self.canvas.start_point_moved.connect(self._on_canvas_mutation_signal)
        self.canvas.route_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.polygon_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_room_polygon_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_point_placed.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_cable_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.hkv_placed.connect(self._on_canvas_mutation_signal)
        self.canvas.hkv_line_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.text_placed.connect(self._on_canvas_mutation_signal)
        self.canvas.floor_plan_transform_updated.connect(self._on_floor_plan_transform_updated)
        self.canvas.floor_plan_polygon_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.export_frame_drawn.connect(self._on_canvas_mutation_signal)
        self.canvas.helper_lines_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.helper_lines_changed.connect(self._on_helper_lines_changed)
        self.canvas.label_moved.connect(self._on_canvas_mutation_signal)
        self.canvas.measure_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.measure_changed.connect(self._on_measure_changed)
        self.canvas.annotation_shape_changed.connect(self._on_annotation_shape_changed)
        self.canvas.multi_objects_moved.connect(self._on_canvas_mutation_signal)
        self.canvas.will_move_multi_objects.connect(self._push_undo)
        self.canvas.ref_line_set.connect(self._on_ref_line_set)
        self.canvas.route_changed.connect(self._on_route_changed)
        self.canvas.supply_line_changed.connect(self._on_supply_line_changed)
        self.canvas.hkv_line_changed.connect(self._on_hkv_line_changed)
        self.properties.field_changed.connect(self._on_property_changed)
        self.properties.batch_field_changed.connect(self._on_batch_property_changed)
        self.properties.action_triggered.connect(self._on_property_action)
        self.properties.setting_changed.connect(self._on_global_setting_changed)
        self.properties.pre_change.connect(self._push_undo)

    def _update_grid_color_btn(self, color: QColor) -> None:
        r, g, b, a = color.red(), color.green(), color.blue(), color.alpha()
        self._grid_color_btn.setStyleSheet(
            f"background-color: rgba({r},{g},{b},{a}); border: 1px solid #888;"
        )

    def _on_grid_toggled(self, checked: bool) -> None:
        self.canvas.set_grid_visible(checked)
        self._mark_dirty()

    def _on_grid_spacing_changed(self, value: float) -> None:
        self.canvas.set_grid_spacing_mm(value * 1000.0)
        self._mark_dirty()

    def _on_grid_color_pick(self) -> None:
        color = QColorDialog.getColor(
            self.canvas.grid_color(),
            self,
            "Rasterfarbe wählen",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self.canvas.set_grid_color(color)
            self._update_grid_color_btn(color)
            self._mark_dirty()

    def _on_snap_angle_changed(self, index: int) -> None:
        angle = self._snap_combo.itemData(index)
        self.canvas.set_snap_angle(float(angle or 0))
        self._mark_dirty()

    def _on_property_changed(self, element_id: str, key: str, _value) -> None:
        """Ein Feld im Eigenschaften-Dock wurde geändert."""
        effects = self._apply_property_side_effects(element_id, key)
        self._document.element_changed.emit(element_id)
        if effects.get("refresh_navigator"):
            self.navigator.set_document(self._document)
        self.canvas.update()
        self._refresh_schema_windows()
        self._mark_dirty()

    def _on_batch_property_changed(self, element_ids: list[str], key: str, _value) -> None:
        defer_updates = len(element_ids) >= 25
        touched = 0
        refresh_navigator = False
        for element_id in element_ids:
            if self._document.get(element_id) is None:
                continue
            effects = self._apply_property_side_effects(element_id, key, defer_updates=defer_updates)
            refresh_navigator = refresh_navigator or bool(effects.get("refresh_navigator"))
            self._document.element_changed.emit(element_id)
            touched += 1
        if touched:
            if refresh_navigator:
                self.navigator.set_document(self._document)
            self.canvas.update()
            self._refresh_schema_windows()
            self._mark_dirty()

    def _apply_property_side_effects(
        self,
        element_id: str,
        key: str,
        defer_updates: bool = False,
    ) -> dict[str, bool]:
        """Sorgt dafür, dass Änderungen sofort im Canvas sichtbar werden."""
        effects = {"refresh_navigator": False}
        element = self._document.get(element_id)
        if element is None:
            return effects

        if key == "color" and element_id not in self._document.floorplans:
            color_value = str(element.data.get("color") or "").strip()
            if color_value:
                self.canvas.set_color(element_id, QColor(color_value))

        if key == "name":
            name = str(element.data.get("name") or "").strip()
            self.canvas._label_map[element_id] = name if name else element_id
            if not defer_updates:
                self.canvas.update()
                self.navigator.set_document(self._document)
            else:
                effects["refresh_navigator"] = True

        if key in ("builtin_symbol", "icon_path") and element_id in self._document.elements.get("elec_points", {}):
            from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415
            if key == "builtin_symbol":
                symbol = str(element.data.get("builtin_symbol") or "").strip()
                icon_path = str(BUILTIN_SYMBOLS.get(symbol, "") or "")
                element.data["icon_path"] = icon_path
            else:
                icon_path = str(element.data.get("icon_path") or "").strip()
                if not icon_path:
                    symbol = str(element.data.get("builtin_symbol") or "").strip()
                    icon_path = str(BUILTIN_SYMBOLS.get(symbol, "") or "")
            self.canvas.set_elec_point_icon(element_id, icon_path)

        if key in ("start_ap", "end_ap") and element_id in self._document.elements.get("elec_cables", {}):
            cable = self._document.elements["elec_cables"].get(element_id)
            if cable is not None:
                start_ap_id = str(cable.start_ap or cable.geom.get("cable_start_ap") or "").strip()
                end_ap_id = str(cable.end_ap or cable.geom.get("cable_end_ap") or "").strip()
                if start_ap_id and start_ap_id not in self._document.elements["elec_points"]:
                    start_ap_id = ""
                if end_ap_id and end_ap_id not in self._document.elements["elec_points"]:
                    end_ap_id = ""
                cable.start_ap = start_ap_id
                cable.end_ap = end_ap_id
                cable.geom["cable_start_ap"] = start_ap_id
                cable.geom["cable_end_ap"] = end_ap_id
                self._rebuild_schema_cable_geometry(cable, start_ap_id, end_ap_id)
                if not defer_updates:
                    self.canvas.update()

        if key == "visible":
            self.canvas.set_element_visible(element_id, bool(element.visible))
        elif key in ("width", "height") and element_id in self.canvas._elec_points:
            self.canvas.update_elec_point_size(
                element_id, float(element.data.get("width", 30.0)),
                float(element.data.get("height", 30.0))
            )
        elif key == "file_path" and (
            element_id in self._document.floorplans or element_id in self._document.furniture
        ):
            path = (element.data.get("file_path") or "").strip()
            if path:
                if is_data_uri(path):
                    self.canvas.load_floor_plan_image(element_id, path)
                else:
                    resolved = Path(path)
                    if not resolved.is_absolute() and self._project_path is not None:
                        resolved = (self._project_path.parent / resolved).resolve()
                    if resolved.exists():
                        self.canvas.load_floor_plan_image(element_id, str(resolved))
        elif element_id in self._document.floorplans:
            if key == "opacity":
                self.canvas.set_floor_plan_opacity(
                    element_id, float(element.data.get("opacity", 1.0))
                )
            elif key in ("offset_x", "offset_y", "rotation"):
                self.canvas.set_floor_plan_transform(
                    element_id,
                    float(element.data.get("offset_x", 0.0)),
                    float(element.data.get("offset_y", 0.0)),
                    float(element.data.get("rotation", 0.0)),
                )
            elif key == "polygon_color":
                self.canvas.set_floor_plan_polygon_color(
                    element_id, str(element.data.get("polygon_color", "#8d99ae"))
                )
            elif key == "ref_line_visible":
                self.canvas.set_ref_line_visible(
                    element_id, bool(element.data.get("ref_line_visible", True))
                )
            elif key == "ref_line_color":
                self.canvas.set_ref_line_color(
                    element_id, str(element.data.get("ref_line_color", "#ffdd00"))
                )
            elif key == "ref_length_mm":
                layer = self.canvas._floor_plans.get(element_id)
                if layer is not None:
                    layer.ref_length_mm = float(element.data.get("ref_length_mm", 0.0) or 0.0)
                # Neuberechnung nur beim expliziten 'Aktualisieren'-Button, nicht automatisch
        return effects

    def _on_global_setting_changed(self, _key: str, _value) -> None:
        self.properties.refresh_current()
        self._mark_dirty()

    def _on_document_data_changed(self, element_id: str) -> None:
        """Der Canvas hat Projektdaten geändert – Projekt gilt als bearbeitet."""
        self._record_canvas_change()
        self.properties.refresh_element(element_id)
        self._refresh_schema_windows()
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _on_canvas_mutation_signal(self, *_args) -> None:
        """Erfasst Canvas-Mutationen, die nicht über DocumentMapView laufen."""
        self._record_canvas_change()
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _on_annotation_shape_changed(self, element_id: str) -> None:
        """Aktualisiert Eigenschaften nach Annotation-Resize/Drag via Canvas."""
        if not element_id:
            return
        self._record_canvas_change()
        if self._document is not None:
            element = self._document.get(element_id)
            if isinstance(element, (AnnotationRectangle, AnnotationCircle, AnnotationEllipse)):
                schema = schema_for(element)
                if schema is not None:
                    signature_items: list[tuple[str, object]] = []
                    for key in ("size_unit", "width_value", "height_value", "corner_radius_value"):
                        spec = next((field for field in schema.fields if field.key == key), None)
                        if spec is None:
                            continue
                        value = get_field(element, spec)
                        if isinstance(value, float):
                            value = round(value, 3)
                        signature_items.append((key, value))
                    signature = tuple(signature_items)
                    if signature and self._annotation_live_value_cache.get(element_id) == signature:
                        if not self._dirty:
                            self._dirty = True
                            self._update_title()
                        return
                    if signature:
                        self._annotation_live_value_cache[element_id] = signature
        self._pending_annotation_refresh_id = element_id
        self._annotation_live_refresh_timer.start()
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _flush_annotation_live_refresh(self) -> None:
        element_id = self._pending_annotation_refresh_id
        self._pending_annotation_refresh_id = ""
        if not element_id or self._document is None:
            return
        self._document.element_changed.emit(element_id)
        self.properties.refresh_element(element_id)
        self._refresh_schema_windows()
        self.canvas.update()

    def _on_measure_changed(self, *_args) -> None:
        """Synchronisiert Messungen in Document-Elemente und aktualisiert den Navigator."""
        if self._document is None or self._restoring_snapshot:
            return
        self._sync_measurements_to_elements()

    def _on_helper_lines_changed(self, *_args) -> None:
        """Hilfslinien geändert – Navigator neu aufbauen und Properties aktualisieren."""
        if self._document is None or self._restoring_snapshot:
            return
        self._document.structure_changed.emit()
        # If a helper line editor is currently visible, refresh it.
        cur_id = self.properties._current_id
        if cur_id:
            helper_ref = _parse_helper_nav_id(cur_id)
            if helper_ref is not None:
                floor_id, helper_id = helper_ref
                self.properties.refresh_helper(floor_id, helper_id)

    def _on_floor_plan_transform_updated(self, fp_id: str, _ox: float, _oy: float, _rot: float) -> None:
        """Synchronisiert Property-Ansicht und Undo bei Drag-Transformationen.

        Keine Neuskalierung hier – die Skalierung soll stabil bleiben.
        _recompute_floorplan_scale_from_reference() darf nur beim expliziten
        'Aktualisieren'-Button aufgerufen werden, nicht bei jedem Drag-Event.
        """
        self._record_canvas_change()
        self.properties.refresh_element(fp_id)
        self._refresh_schema_windows()
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _on_ref_line_set(self) -> None:
        """Nach Referenzlinie: Nutzer zur Längeneingabe auffordern."""
        self.statusBar().showMessage(
            "✏️ Referenzlinie gezeichnet!  "
            "Jetzt die reale Länge eingeben und 'Aktualisieren' klicken."
        )

    def _recompute_floorplan_scale_from_reference(self, fp_id: str) -> bool:
        """Berechnet ``mm_per_px`` aus Referenzlinie und Referenzlänge.

        Wichtig: Die Referenzlänge muss aus Bildpixeln (nicht Canvas-Welt)
        berechnet werden. Nur so bleibt das Ergebnis unabhängig von einer
        bereits gesetzten Layer-Skalierung und damit bei wiederholtem
        "Aktualisieren" idempotent.
        """
        layer = self.canvas._floor_plans.get(fp_id)
        floor = self._document.floorplans.get(fp_id)
        if layer is None or floor is None:
            return False
        if layer.ref_p1 is None or layer.ref_p2 is None:
            return False

        ref_length_mm = float(layer.ref_length_mm or floor.data.get("ref_length_mm", 0.0) or 0.0)
        if ref_length_mm <= 0.0:
            return False

        old_global_mpp = float(self.canvas._mm_per_px or 1.0)
        old_mpp = float(layer.mm_per_px or 1.0)
        native_size = tuple(layer.size)
        old_render_size = self.canvas._layer_render_size_for_scale(
            layer,
            old_global_mpp,
            layer_mm_per_px=old_mpp,
            native_size=native_size,
        )

        p1_img = self.canvas._world_to_layer_image_point(
            layer,
            layer.ref_p1,
            old_render_size,
            native_size,
        )
        p2_img = self.canvas._world_to_layer_image_point(
            layer,
            layer.ref_p2,
            old_render_size,
            native_size,
        )
        px_len_img = math.hypot(p2_img.x() - p1_img.x(), p2_img.y() - p1_img.y())
        if px_len_img <= 1e-9:
            return False

        new_mpp = ref_length_mm / px_len_img

        if abs(new_mpp - old_mpp) > 1e-9:
            self.canvas.rescale_layer_ref_points(fp_id, old_mpp, new_mpp)

        layer.mm_per_px = new_mpp
        floor.layer["mm_per_px"] = new_mpp
        floor.layer["ref_length_mm"] = ref_length_mm
        floor.data["mm_per_px"] = new_mpp
        floor.data["ref_length_mm"] = ref_length_mm

        self.canvas.update()
        return True

    def _append_undo_snapshot(self, snapshot: dict) -> None:
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > _MAX_UNDO_STEPS:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_state()

    def _record_canvas_change(self) -> None:
        """Beginnt eine Undo-Gruppe für eine Canvas-Änderung.

        ``document_data_changed`` kann bei jedem Mausbewegungs-Event feuern.
        Deshalb wird nur der Zustand vor dem ersten Event gespeichert; ein
        kurzer Idle-Timer schließt die Gruppe nach dem Ende des Drags.
        """
        if self._restoring_snapshot or self._undo_group_open:
            if self._undo_group_open:
                self._undo_group_timer.start(_UNDO_GROUP_IDLE_MS)
            return
        if self._last_document_snapshot is None:
            self._last_document_snapshot = self._document.snapshot()
        self._append_undo_snapshot(copy.deepcopy(self._last_document_snapshot))
        self._undo_group_open = True
        self._undo_group_timer.start(_UNDO_GROUP_IDLE_MS)

    def _finish_undo_group(self) -> None:
        """Schließt die aktuelle Änderungsgruppe und aktualisiert die Baseline."""
        self._undo_group_open = False
        if not self._restoring_snapshot:
            self._last_document_snapshot = self._document.snapshot()

    def _on_route_changed(self, circuit_id: str) -> None:
        route_px = self.canvas.get_manual_route_length_px(circuit_id)
        route_mm = route_px * self.canvas.get_mm_per_px()
        self.properties.refresh_element(circuit_id)
        self.statusBar().showMessage(
            f"✅ {circuit_id}: Manuellen Rohrverlauf aktualisiert ({route_mm / 1000:.2f} m)",
            2500,
        )

    def _on_supply_line_changed(self, circuit_id: str) -> None:
        supply_px = self.canvas.get_supply_line_length_px(circuit_id)
        supply_mm = supply_px * self.canvas.get_mm_per_px()
        self.properties.refresh_element(circuit_id)
        self.statusBar().showMessage(
            f"✅ {circuit_id}: Zuleitung aktualisiert ({supply_mm / 1000:.2f} m)",
            2500,
        )

    def _on_hkv_line_changed(self, line_id: str) -> None:
        length_px = self.canvas.get_hkv_line_length_px(line_id)
        length_mm = length_px * self.canvas.get_mm_per_px()
        self.properties.refresh_element(line_id)
        self.statusBar().showMessage(
            f"✅ {line_id}: HKV-Leitung aktualisiert ({length_mm / 1000:.2f} m)",
            2500,
        )

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------
    def _push_undo(self) -> None:
        """Nimmt einen Snapshot des aktuellen Zustands auf den Undo-Stack."""
        self._undo_group_timer.stop()
        self._finish_undo_group()
        snapshot = self._document.snapshot()
        self._append_undo_snapshot(snapshot)
        # Der explizite Aufrufer mutiert unmittelbar nach diesem Aufruf. Die
        # Gruppe verhindert, dass das nachfolgende Canvas-Signal doppelt zählt.
        self._last_document_snapshot = snapshot
        self._undo_group_open = True
        self._undo_group_timer.start(_UNDO_GROUP_IDLE_MS)

    def _undo(self) -> None:
        if not self._undo_stack:
            self.statusBar().showMessage("Nichts zum Rückgängigmachen", 2000)
            return
        self._undo_group_timer.stop()
        self._undo_group_open = False
        self._redo_stack.append(self._document.snapshot())
        snapshot = self._undo_stack.pop()
        self._resync_after_restore(snapshot)
        self._update_undo_redo_state()
        self._dirty = True
        self._update_title()
        remaining = len(self._undo_stack)
        self.statusBar().showMessage(f"Rückgängig  ({remaining} verbleibend)", 2500)
        self.log.info(f"Undo (Stack: {remaining})")

    def _redo(self) -> None:
        if not self._redo_stack:
            self.statusBar().showMessage("Nichts zum Wiederherstellen", 2000)
            return
        self._undo_group_timer.stop()
        self._undo_group_open = False
        self._undo_stack.append(self._document.snapshot())
        snapshot = self._redo_stack.pop()
        self._resync_after_restore(snapshot)
        self._update_undo_redo_state()
        self._dirty = True
        self._update_title()
        remaining = len(self._redo_stack)
        self.statusBar().showMessage(f"Wiederhergestellt  ({remaining} verbleibend)", 2500)
        self.log.info(f"Redo (Stack: {remaining})")

    def _resync_after_restore(self, snapshot: dict) -> None:
        """Synchronisiert Canvas und alle Docks nach einem Undo/Redo-Restore.

        ``Document.restore`` ersetzt alle Felder des Dokuments in-place.
        Danach müssen Canvas-Views neu gebunden und Editoren verworfen werden.
        Der aktuell angezeigte Ausschnitt bleibt dabei unverändert; Zoom und
        Pan sind Ansichtszustand und sollen nicht mit der Projekthistorie
        zurückspringen.
        """
        current_scale = float(self.canvas._scale)
        current_offset = QPointF(self.canvas._offset)
        # Anzeigeeinstellungen sichern: diese sind kein Teil der Projekthistorie
        # und sollen beim Rückgängigmachen / Wiederherstellen nicht zurückspringen.
        current_view = {
            "bg_color": self.canvas._bg_color.name(),
            "grid_visible": bool(self.canvas.grid_visible()),
            "grid_spacing_mm": float(self.canvas.grid_spacing_mm()),
            "grid_color": [
                self.canvas.grid_color().red(),
                self.canvas.grid_color().green(),
                self.canvas.grid_color().blue(),
                self.canvas.grid_color().alpha(),
            ],
            "snap_angle": float(self.canvas.snap_angle()),
        }
        self._restoring_snapshot = True
        try:
            self._document.restore(snapshot)
            # Canvas: erst rohe Ansichtsdaten übertragen, dann Views neu binden.
            raw_canvas = snapshot.get("canvas", {})
            self.canvas.from_dict(raw_canvas)
            self.canvas.set_document(self._document)
            self.canvas._scale = current_scale
            self.canvas._offset = QPointF(current_offset)
            self._document.view["view_scale"] = current_scale
            self._document.view["view_offset"] = [
                current_offset.x(), current_offset.y()
            ]
            # Anzeigeeinstellungen zurückschreiben und im Canvas aktualisieren
            self._document.view.update(current_view)
            if "bg_color" in current_view:
                self.canvas._bg_color = self.canvas._bg_color.__class__(current_view["bg_color"])
            if "grid_visible" in current_view:
                self.canvas._grid_visible = bool(current_view["grid_visible"])
            if "grid_spacing_mm" in current_view:
                self.canvas._grid_spacing_mm = float(current_view["grid_spacing_mm"])
            if "grid_color" in current_view:
                gc = current_view["grid_color"]
                if isinstance(gc, (list, tuple)) and len(gc) == 4:
                    from PySide6.QtGui import QColor as _QColor  # noqa: PLC0415
                    self.canvas._grid_color = _QColor(gc[0], gc[1], gc[2], gc[3])
                else:
                    from PySide6.QtGui import QColor as _QColor  # noqa: PLC0415
                    self.canvas._grid_color = _QColor(gc)
            if "snap_angle" in current_view:
                self.canvas._snap_angle = float(current_view["snap_angle"])
            self._sync_grid_toolbar_from_canvas()
            self.canvas.update()
            self._load_floor_plan_images(self._document)
            # Eigenschaften-Dock: alle Editor-Caches invalidieren.
            current_id = self.properties._current_id
            self.properties.set_document(self._document)
            if current_id and self._document.get(current_id):
                self.properties.show_element(current_id)
        finally:
            self._restoring_snapshot = False
            self._undo_group_open = False
            self._sync_measurements_to_elements()
            self._last_document_snapshot = self._document.snapshot()

    def _update_undo_redo_state(self) -> None:
        """Passt den aktivierten Zustand und die Menü-Beschriftung an."""
        undo_count = len(self._undo_stack)
        redo_count = len(self._redo_stack)
        if self._undo_action is not None:
            self._undo_action.setEnabled(undo_count > 0)
            label = f"Rückgängig ({undo_count})" if undo_count else "Rückgängig"
            self._undo_action.setText(label)
        if self._redo_action is not None:
            self._redo_action.setEnabled(redo_count > 0)
            label = f"Wiederherstellen ({redo_count})" if redo_count else "Wiederherstellen"
            self._redo_action.setText(label)

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------
    def _on_tab_changed(self, index: int) -> None:
        workspace_id = self._tabs.tabData(index)
        if workspace_id:
            self._apply_workspace(str(workspace_id))

    def _apply_workspace(self, workspace_id: str) -> None:
        previous = self._workspace
        if previous is not None and previous.id != workspace_id:
            layout_store.save_workspace_state(self, previous.id)

        definition = workspace_by_id(workspace_id)
        self._workspace = definition

        for index in range(self._tabs.count()):
            if self._tabs.tabData(index) == definition.id:
                if self._tabs.currentIndex() != index:
                    self._tabs.blockSignals(True)
                    self._tabs.setCurrentIndex(index)
                    self._tabs.blockSignals(False)
                break

        # Werkzeuge
        self.tools.set_tools(definition.tools)
        self.canvas.set_tool_mode(ToolMode.NONE)

        # Selektionsfilter: nur Elemente des Workspaces sind anklickbar
        self.canvas.set_selectable_layers(definition.selectable_layers)
        self.navigator.set_selectable_layers(definition.selectable_layers)

        # Docks des Workspaces einblenden
        if not layout_store.restore_workspace_state(self, definition.id):
            for dock_id, dock in self._docks.items():
                dock.setVisible(dock_id in definition.default_docks)

        layout_store.save_last_workspace(definition.id)
        self.statusBar().showMessage(f"Arbeitsbereich: {definition.label}", 3000)

    def _on_tool_activated(self, tool_id: str) -> None:
        from .tool_registry import TOOLS_BY_ID  # lokal, um Zyklen zu vermeiden

        tool = TOOLS_BY_ID.get(tool_id)
        if tool is None:
            return

        # Werkzeuge mit Zielobjekt direkt starten, statt nur den Modus zu setzen.
        if tool_id == "fp.move":
            target_id = self._selected_floor_like_id()
            if not target_id:
                self.statusBar().showMessage("Kein Grundriss ausgewählt", 2500)
                return
            self.canvas.start_move_floor_plan(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "fp.rotate":
            target_id = self._selected_floorplan_id()
            if not target_id:
                self.statusBar().showMessage("Kein Grundriss ausgewählt", 2500)
                return
            self.canvas.start_rotate_floor_plan(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "fp.ref_line":
            target_id = self._selected_floorplan_id()
            if not target_id:
                self.statusBar().showMessage("Kein Grundriss ausgewählt", 2500)
                return
            self._push_undo()
            self.canvas.start_ref_line_for_floor(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "fp.polygon":
            target_id = self._selected_floorplan_id()
            if not target_id:
                self.statusBar().showMessage("Kein Grundriss ausgewählt", 2500)
                return
            self.canvas.start_draw_floor_plan_polygon(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "fp.edit_polygon":
            target_id = self._selected_floorplan_id()
            if not target_id:
                self.statusBar().showMessage("Kein Grundriss ausgewählt", 2500)
                return
            self.canvas.start_edit_floor_plan_polygon(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "furn.polygon":
            target_id = self._selected_floor_like_id()
            if not target_id:
                self.statusBar().showMessage("Kein Einrichtungselement ausgewählt", 2500)
                return
            self.canvas.start_draw_floor_plan_polygon(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "furn.edit_polygon":
            target_id = self._selected_floor_like_id()
            if not target_id:
                self.statusBar().showMessage("Kein Einrichtungselement ausgewählt", 2500)
                return
            self.canvas.start_edit_floor_plan_polygon(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "ann.text":
            self._add_text()
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id in {"ann.line", "ann.rectangle", "ann.polyline", "ann.circle", "ann.ellipse", "ann.polygon"}:
            self._add_annotation_shape(tool_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "ann.helper":
            fp_id = self._active_floorplan_id() or ""
            layer = self.canvas._floor_plans.get(fp_id) if fp_id else None
            if layer is None or not bool(getattr(layer, "visible", True)):
                for candidate_id in self.canvas._floor_plan_order:
                    candidate = self.canvas._floor_plans.get(candidate_id)
                    if candidate is not None and bool(getattr(candidate, "visible", True)):
                        fp_id = candidate_id
                        break
            self.canvas.start_draw_helper_line(fp_id or None)
            resolved_fp_id = self.canvas._helper_active_floor_id or fp_id
            self.properties.show_helper_draw_settings(resolved_fp_id or "", self.canvas)

            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return

        # ── Elektro-Werkzeuge ────────────────────────────────────────────────
        if tool_id == "ap.place":
            self._add_elec_point()
            return
        if tool_id == "er.polygon":
            self._add_elec_room()
            return
        if tool_id == "ek.draw":
            selected_id = self._current_selection_id()
            point = self._document.elements.get("elec_points", {}).get(selected_id)
            if point is not None:
                self._add_elec_cable_from_ap(selected_id)
            else:
                self._add_elec_cable()
            return
        if tool_id == "ek.edit":
            selected_id = self._current_selection_id()
            if self._document.elements.get("elec_cables", {}).get(selected_id):
                self.canvas.start_edit_elec_cable(selected_id)
                self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            else:
                self.statusBar().showMessage("Bitte erst ein Kabel auswählen", 2500)
            return

        # ── Heizungs-Werkzeuge ───────────────────────────────────────────────
        if tool_id == "hk.polygon":
            self._add_circuit()
            return
        if tool_id in ("hk.edit_polygon", "hk.route", "hk.edit_route",
                       "hk.supply", "hk.edit_supply"):
            selected_id = self._current_selection_id()
            circuit = self._document.elements.get("circuits", {}).get(selected_id)
            if circuit is None:
                self.statusBar().showMessage("Bitte erst einen Heizkreis auswählen", 2500)
                return
            if tool_id == "hk.edit_polygon":
                self.canvas.start_edit_polygon(selected_id)
            elif tool_id == "hk.route":
                self.canvas.start_route_drawing(
                    selected_id,
                    float(circuit.data.get("wall_dist", 200.0)),
                    float(circuit.data.get("spacing", 150.0)),
                )
            elif tool_id == "hk.edit_route":
                self.canvas.start_edit_route(selected_id)
            elif tool_id == "hk.supply":
                self.canvas.start_draw_supply_line(selected_id)
            elif tool_id == "hk.edit_supply":
                self.canvas.start_edit_supply_line(selected_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "hkv.place":
            self._add_hkv()
            return
        if tool_id == "hkv.line":
            self._add_hkv_line()
            return
        if tool_id == "hkv.edit_line":
            selected_id = self._current_selection_id()
            if self._document.elements.get("hkv_lines", {}).get(selected_id):
                self.canvas.start_edit_hkv_line(selected_id)
                self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            else:
                self.statusBar().showMessage("Bitte erst eine HKV-Leitung auswählen", 2500)
            return

        # ── Einrichtung ──────────────────────────────────────────────────────
        if tool_id == "furn.move":
            target_id = self._selected_floor_like_id()
            if not target_id:
                self.statusBar().showMessage("Kein Einrichtungselement ausgewählt", 2500)
                return
            self.canvas.start_move_floor_plan(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return

        mode = getattr(ToolMode, tool.tool_mode, ToolMode.NONE)
        self.canvas.set_tool_mode(mode)
        self.statusBar().showMessage(tool.tooltip or tool.label, 3000)

    # ------------------------------------------------------------------
    # Dokument
    # ------------------------------------------------------------------
    def _set_document(self, document: Document) -> None:
        self._document = document
        self._annotation_live_value_cache.clear()
        self._pending_annotation_refresh_id = ""
        self._annotation_live_refresh_timer.stop()
        # Neues Dokument = frische Undo/Redo-Stacks.
        self._undo_group_timer.stop()
        self._undo_group_open = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_redo_state()

        self.navigator.set_document(document)
        self.properties.set_document(document)
        self.overview_general.set_document(document)
        self.overview_heating.set_document(document)
        self.overview_electro.set_document(document)

        # Globale Ansichtsdaten (Zoom, Raster, Grundriss-Transformationen,
        # Hilfslinien, Messungen) in den Canvas übertragen …
        raw = document.to_dict()
        self.canvas.from_dict(raw.get("canvas", {}))
        # … und danach die Elementdaten an das Dokument binden. Ab hier ist
        # das Dokument die einzige Datenquelle: Zeichnen im Canvas verändert
        # unmittelbar das Projekt.
        self.canvas.set_document(document)
        self._sync_measurements_to_elements()

        self._load_floor_plan_images(document)
        self._reload_elec_points_to_canvas(document)
        self._pdf_export_pages = self._normalize_pdf_export_pages(document.pdf_export_pages)
        self._pdf_export_meta = self._normalize_pdf_export_meta(
            getattr(document, "pdf_export_meta", {}),
            self._pdf_export_pages,
        )
        self._sync_grid_toolbar_from_canvas()
        self._refresh_schema_windows()
        self._update_title()
        self._last_document_snapshot = self._document.snapshot()

    def _on_property_action(self, element_id: str, action_id: str) -> None:
        """Führt eine Schaltflächen-Aktion aus dem Eigenschaften-Dock aus."""
        element = self._document.get(element_id)
        if element is None:
            return

        handlers = {
            "draw_polygon": self._action_draw_polygon,
            "edit_polygon": self._action_edit_polygon,
            "remove_polygon": self._action_remove_polygon,
            "draw_route": self._action_draw_route,
            "edit_route": lambda eid: self.canvas.start_edit_route(eid),
            "draw_supply": lambda eid: self.canvas.start_draw_supply_line(eid),
            "edit_supply": lambda eid: self.canvas.start_edit_supply_line(eid),
            "draw_cable": lambda eid: self.canvas.start_draw_elec_cable(eid),
            "edit_cable": lambda eid: self.canvas.start_edit_elec_cable(eid),
            "draw_line": self._action_draw_line,
            "edit_line": self._action_edit_line,
            "draw_rectangle": lambda eid: self._action_draw_annotation(eid, "annotation_rectangle"),
            "edit_rectangle": lambda eid: self._action_edit_annotation(eid, "annotation_rectangle"),
            "draw_polyline": lambda eid: self._action_draw_annotation(eid, "annotation_polyline"),
            "edit_polyline": lambda eid: self._action_edit_annotation(eid, "annotation_polyline"),
            "draw_circle": lambda eid: self._action_draw_annotation(eid, "annotation_circle"),
            "edit_circle": lambda eid: self._action_edit_annotation(eid, "annotation_circle"),
            "draw_ellipse": lambda eid: self._action_draw_annotation(eid, "annotation_ellipse"),
            "edit_ellipse": lambda eid: self._action_edit_annotation(eid, "annotation_ellipse"),
            "draw_annotation_polygon": lambda eid: self._action_draw_annotation(eid, "annotation_polygon"),
            "edit_annotation_polygon": lambda eid: self._action_edit_annotation(eid, "annotation_polygon"),
            "place": self._action_place,
            "draw_ref_line": lambda eid: self.canvas.start_ref_line_for_floor(eid),
            "move": self._action_move,
            "rotate": self._action_rotate,
            "choose_image": self._action_choose_image,
            "recompute_scale": self._action_recompute_scale,
            "duplicate": lambda _eid: self._duplicate_selected(),
            "configure_uv": self._action_configure_uv,
            "configure_up": self._action_configure_up,
            "configure_hak": self._action_configure_hak,
            "configure_zaehler": self._action_configure_zaehler,
            "delete": self._action_delete,
        }

        handler = handlers.get(action_id)
        if handler is None:
            self.statusBar().showMessage("Aktion noch nicht verfügbar", 3000)
            return

        # Vor Geometrie- und Konfigurations-Aktionen Undo-Snapshot aufnehmen.
        _UNDO_BEFORE = {
            "draw_polygon", "edit_polygon", "remove_polygon", "draw_route", "edit_route",
            "draw_supply", "edit_supply", "draw_cable", "edit_cable",
            "draw_line", "edit_line", "place", "draw_ref_line",
            "draw_rectangle", "edit_rectangle", "draw_polyline", "edit_polyline",
            "draw_circle", "edit_circle", "draw_ellipse", "edit_ellipse",
            "draw_annotation_polygon", "edit_annotation_polygon",
            "configure_uv", "configure_up", "configure_hak", "configure_zaehler",
            "move", "rotate", "choose_image", "recompute_scale",
        }
        if action_id in _UNDO_BEFORE:
            self._push_undo()

        try:
            handler(element_id)
        except Exception as exc:  # noqa: BLE001 - Aktion darf die UI nicht abbrechen
            self.log.error(f"Aktion '{action_id}' fehlgeschlagen: {exc}")

    # ------------------------------------------------------------------
    # Konfigurationsdialoge für spezielle Anschlusspunkt-Typen
    # ------------------------------------------------------------------
    def _cable_names(self) -> list[str]:
        """Anzeigenamen aller Kabel – für die Zuordnung in der Unterverteilung."""
        names: list[str] = []
        for cable_id, cable in self._document.elements["elec_cables"].items():
            names.append(cable.name or cable_id)
        return sorted(set(names))

    def _cable_id_name_pairs(self) -> list[tuple[str, str]]:
        """(id, name)-Paare aller Kabel – für die Unterputz-Verteilung."""
        pairs = [
            (cable_id, cable.name or cable_id)
            for cable_id, cable in self._document.elements["elec_cables"].items()
        ]
        return sorted(pairs, key=lambda entry: entry[1].lower())

    def _store_config(self, element_id: str, key: str, config: dict) -> None:
        element = self._document.get(element_id)
        if element is None:
            return
        element.data[key] = config
        self._document.element_changed.emit(element_id)
        self.properties.refresh_element(element_id)
        self._mark_dirty()

    def _action_configure_uv(self, element_id: str) -> None:
        from gui.parameter_panel import UvConfigDialog  # noqa: PLC0415

        element = self._document.get(element_id)
        if element is None:
            return
        dialog = UvConfigDialog(
            config=element.data.get("uv_config") or {},
            cable_choices=self._cable_names(),
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            self._store_config(element_id, "uv_config", dialog.get_config())
            self.log.info(f"Unterverteilung aktualisiert: {element_id}")

    def _action_configure_up(self, element_id: str) -> None:
        from gui.parameter_panel import UpDistributionDialog  # noqa: PLC0415

        element = self._document.get(element_id)
        if element is None:
            return
        dialog = UpDistributionDialog(
            config=element.data.get("up_distribution_config") or {},
            cable_choices=self._cable_id_name_pairs(),
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            self._store_config(
                element_id, "up_distribution_config", dialog.get_config()
            )
            self.log.info(f"Unterputz-Verteilung aktualisiert: {element_id}")

    def _action_configure_hak(self, element_id: str) -> None:
        from gui.properties.config_dialogs import HakConfigDialog  # noqa: PLC0415

        element = self._document.get(element_id)
        if element is None:
            return
        dialog = HakConfigDialog(element.data.get("hak_config") or {}, self)
        if dialog.exec() == QDialog.Accepted:
            self._store_config(element_id, "hak_config", dialog.get_config())

    def _action_configure_zaehler(self, element_id: str) -> None:
        from gui.properties.config_dialogs import ZaehlerConfigDialog  # noqa: PLC0415

        element = self._document.get(element_id)
        if element is None:
            return
        dialog = ZaehlerConfigDialog(element.data.get("zaehler_config") or {}, self)
        if dialog.exec() == QDialog.Accepted:
            self._store_config(element_id, "zaehler_config", dialog.get_config())

    def _action_draw_route(self, element_id: str) -> None:
        element = self._document.get(element_id)
        if element is None:
            return
        wall_dist_mm = float(element.data.get("wall_dist", 200.0))
        spacing_mm = float(element.data.get("spacing", 150.0))
        self.canvas.start_route_drawing(element_id, wall_dist_mm, spacing_mm)

    def _action_draw_polygon(self, element_id: str) -> None:
        if element_id in self._document.elements["elec_rooms"]:
            self.canvas.start_draw_elec_room(element_id)
        elif element_id in self._document.floorplans or element_id in self._document.furniture:
            self.canvas.start_draw_floor_plan_polygon(element_id)
        else:
            self.canvas.start_drawing(element_id)

    def _action_draw_line(self, element_id: str) -> None:
        element = self._document.get(element_id)
        if isinstance(element, AnnotationLine):
            self._action_draw_annotation(element_id, "annotation_line")
            return
        self.canvas.start_draw_hkv_line(element_id)

    def _action_edit_line(self, element_id: str) -> None:
        element = self._document.get(element_id)
        if isinstance(element, AnnotationLine):
            self._action_edit_annotation(element_id, "annotation_line")
            return
        self.canvas.start_edit_hkv_line(element_id)

    def _action_draw_annotation(self, element_id: str, kind: str) -> None:
        element = self._document.get(element_id)
        if element is None:
            return
        fill_color = str(element.data.get("fill_color", "#ffffff") or "#ffffff")
        corner_radius = float(element.data.get("corner_radius", 0.0) or 0.0)
        self.canvas.start_place_annotation_shape(
            kind,
            element_id,
            color=str(element.data.get("color", "#00e5ff") or "#00e5ff"),
            line_style=str(element.data.get("line_style", "solid") or "solid"),
            stroke_width=float(element.data.get("stroke_width", 2.0) or 2.0),
            fill_color=fill_color,
            corner_radius=corner_radius,
        )

    def _action_edit_annotation(self, element_id: str, kind: str) -> None:
        if self._document.get(element_id) is None:
            return
        self.canvas.start_edit_annotation_shape(kind, element_id)

    def _action_edit_polygon(self, element_id: str) -> None:
        if element_id in self._document.elements["circuits"]:
            self.canvas.start_edit_polygon(element_id)
        elif element_id in self._document.elements["elec_rooms"]:
            self.canvas.start_edit_elec_room_polygon(element_id)
        elif element_id in self._document.floorplans or element_id in self._document.furniture:
            self.canvas.start_edit_floor_plan_polygon(element_id)

    def _action_remove_polygon(self, element_id: str) -> None:
        floor = self._document.floorplans.get(element_id) or self._document.furniture.get(element_id)
        if floor is None:
            return
        if not floor.layer.get("polygon"):
            return
        floor.layer["polygon"] = []
        self._document.element_changed.emit(element_id)
        self.properties.refresh_element(element_id)
        self.canvas.update()
        self._mark_dirty()

    def _action_place(self, element_id: str) -> None:
        element = self._document.get(element_id)
        if element is None:
            return
        if element_id in self._document.elements["elec_points"]:
            self.canvas.start_place_elec_point(
                element_id,
                float(element.data.get("width", 30.0)),
                float(element.data.get("height", 30.0)),
            )
        elif element_id in self._document.elements["hkv_points"]:
            self.canvas.start_place_hkv(
                element_id,
                float(element.data.get("width", 50.0)),
                float(element.data.get("height", 50.0)),
            )
        elif element_id in self._document.elements["text_annotations"]:
            self.canvas.start_place_text(element_id, element.data.get("content", "Text"))

    def _action_move(self, element_id: str) -> None:
        if element_id in self._document.floorplans or element_id in self._document.furniture:
            self.canvas.start_move_floor_plan(element_id)

    def _action_rotate(self, element_id: str) -> None:
        if element_id in self._document.floorplans:
            self.canvas.start_rotate_floor_plan(element_id)

    def _action_choose_image(self, element_id: str) -> None:
        element = self._document.get(element_id)
        if element is None:
            return
        current = (element.data.get("file_path") or "").strip()
        image_path, _ = QFileDialog.getOpenFileName(
            self,
            "Bild wählen",
            current,
            _IMAGE_FILTER,
        )
        if not image_path:
            return
        element.data["file_path"] = image_path
        self._apply_property_side_effects(element_id, "file_path")
        self._document.element_changed.emit(element_id)
        self.properties.refresh_element(element_id)
        self._mark_dirty()

    def _action_recompute_scale(self, element_id: str) -> None:
        if element_id not in self._document.floorplans:
            return
        if self._recompute_floorplan_scale_from_reference(element_id):
            self._document.element_changed.emit(element_id)
            self.properties.refresh_element(element_id)
            self._refresh_schema_windows()
            self._mark_dirty()
            self.statusBar().showMessage(f"Maßstab neu berechnet: {element_id}", 2500)
        else:
            self.statusBar().showMessage(
                "Maßstab konnte nicht neu berechnet werden (Referenzlinie/Länge fehlt)",
                3000,
            )

    def _action_delete(self, element_id: str) -> None:
        self._delete_element_with_confirm(element_id)

    def _cut_selected(self) -> None:
        element_id = self._current_selection_id()
        if not element_id:
            self.statusBar().showMessage("Kein Element ausgewählt", 2000)
            return
        self._copy_selected()
        if self._copy_buffer is not None:
            self._delete_selected()

    def _copy_selected(self) -> None:
        element_id = self._current_selection_id()
        if not element_id:
            self.statusBar().showMessage("Kein Element ausgewählt", 2000)
            return
        element = self._document.get(element_id)
        if element is None:
            self.statusBar().showMessage("Dieses Element kann nicht kopiert werden", 2500)
            return
        self._copy_buffer = {
            "id": element_id,
            "type": type(element).__name__,
        }
        self.statusBar().showMessage(f"Kopiert: {element_id}", 2000)

    def _paste_copied(self) -> None:
        if not self._copy_buffer:
            self.statusBar().showMessage("Zwischenablage ist leer", 2000)
            return
        source_id = str(self._copy_buffer.get("id") or "")
        if not source_id:
            return
        new_id = self._duplicate_element(source_id)
        if not new_id:
            self.statusBar().showMessage("Einfügen nicht möglich", 2500)
            return
        self.navigator.select(new_id)
        self.properties.show_element(new_id)
        self.canvas.set_selected_item(new_id)
        self.statusBar().showMessage(f"Eingefügt: {new_id}", 2500)

    def _duplicate_selected(self) -> None:
        element_id = self._current_selection_id()
        if not element_id:
            self.statusBar().showMessage("Kein Element ausgewählt", 2000)
            return
        new_id = self._duplicate_element(element_id)
        if not new_id:
            self.statusBar().showMessage("Duplizieren nicht möglich", 2500)
            return
        self.navigator.select(new_id)
        self.properties.show_element(new_id)
        self.canvas.set_selected_item(new_id)
        self.statusBar().showMessage(f"Dupliziert: {element_id} -> {new_id}", 2500)

    def _delete_selected(self) -> None:
        selected_ids = self.navigator.selected_ids()
        if len(selected_ids) > 1:
            to_delete: list[str] = []
            dist_indices: list[int] = []
            angle_indices: list[int] = []
            for element_id in selected_ids:
                meas_ref = _parse_measurement_nav_id(element_id)
                if meas_ref is not None:
                    prefix, idx = meas_ref
                    if prefix == "MSRD":
                        dist_indices.append(idx)
                    else:
                        angle_indices.append(idx)
                    continue
                if self._document.get(element_id) is not None:
                    to_delete.append(element_id)

            if not to_delete and not dist_indices and not angle_indices:
                self.statusBar().showMessage("Keine löschbaren Elemente ausgewählt", 2000)
                return

            self._push_undo()
            deleted = 0
            for element_id in to_delete:
                element = self._document.get(element_id)
                if element is None:
                    continue
                self._cleanup_references_before_delete(element_id)
                self._document.remove(element_id)
                self.properties.forget_element(element_id)
                deleted += 1

            # Messungen leben primär in Canvas-Laufzeitlisten; in absteigender
            # Reihenfolge löschen, damit Indizes stabil bleiben.
            for idx in sorted(set(dist_indices), reverse=True):
                if self.canvas.delete_measurement_at(idx):
                    deleted += 1
            for idx in sorted(set(angle_indices), reverse=True):
                if self.canvas.delete_angle_measurement_at(idx):
                    deleted += 1

            for element_id in selected_ids:
                self.properties.forget_element(element_id)

            if deleted:
                self._emit_structure_changed()
                self.canvas.update()
                self._mark_dirty()
                self.statusBar().showMessage(f"{deleted} Elemente gelöscht", 2500)
            return

        element_id = self._current_selection_id()
        if not element_id:
            self.statusBar().showMessage("Kein Element ausgewählt", 2000)
            return
        helper_ref = _parse_helper_nav_id(element_id)
        if helper_ref is not None:
            floor_id, helper_id = helper_ref
            answer = QMessageBox.question(
                self,
                "Hilfslinie löschen",
                f"Hilfslinie '{helper_id}' wirklich löschen?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            self._push_undo()
            self.canvas.delete_helper_line(floor_id, helper_id)
            self.navigator.rebuild()
            self._mark_dirty()
            self.statusBar().showMessage(f"Hilfslinie gelöscht: {helper_id}", 2500)
            return
        # Distanz- oder Winkelmessungen werden im Canvas (nicht im Dokument) gelöscht.
        meas_ref = _parse_measurement_nav_id(element_id)
        if meas_ref is not None:
            prefix, _idx = meas_ref
            kind_label = "Distanzmessung" if prefix == "MSRD" else "Winkelmessung"
            answer = QMessageBox.question(
                self,
                f"{kind_label} löschen",
                f"{kind_label} '{element_id}' wirklich löschen?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            if self._delete_element(element_id):
                # measure_changed wurde bereits von der canvas-Methode emittiert;
                # _on_measure_changed synchronisiert die Elements und triggert den Navigator.
                self.statusBar().showMessage(f"{kind_label} gelöscht: {element_id}", 2500)
            return
        self._delete_element_with_confirm(element_id)

    def _current_selection_id(self) -> str:
        current_id = (self.properties._current_id or "").strip()
        if current_id:
            return current_id
        selected_id = self.canvas._selected_item_id
        return selected_id if selected_id else ""

    def _delete_element_with_confirm(self, element_id: str) -> None:
        element = self._document.get(element_id)
        name = (element.name if element else "") or element_id
        answer = QMessageBox.question(
            self,
            "Element löschen",
            f"'{name}' wirklich löschen?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._delete_element(element_id)

    def _delete_element(self, element_id: str) -> bool:
        meas_ref = _parse_measurement_nav_id(element_id)
        if meas_ref is not None:
            prefix, idx = meas_ref
            self._push_undo()
            if prefix == "MSRD":
                deleted = self.canvas.delete_measurement_at(idx)
            else:
                deleted = self.canvas.delete_angle_measurement_at(idx)
            if not deleted:
                return False

            # measure_changed synchronisiert die Document-Elemente bereits,
            # deshalb hier nur UI/Dirty-State nachziehen.
            self.properties.forget_element(element_id)
            self._emit_structure_changed()
            self.canvas.update()
            self._mark_dirty()
            self.log.info(f"Gelöscht: {element_id}")
            return True

        element = self._document.get(element_id)
        if element is None:
            return False

        self._push_undo()
        self._cleanup_references_before_delete(element_id)
        self._document.remove(element_id)

        # Wenn ein Grundriss entfernt wurde, verwaiste Auswahl bereinigen.
        if element_id == self._document.active_floorplan_id:
            self._document.active_floorplan_id = self._active_floorplan_id()

        self.properties.forget_element(element_id)
        self._emit_structure_changed()
        self.canvas.update()
        self._mark_dirty()
        self.log.info(f"Gelöscht: {element_id}")
        return True

    def _delete_text(self, text_id: str) -> bool:
        """Kompatibler Löschpfad für Text-Annotationen (ohne Dialog)."""
        return self._delete_element(text_id)

    def _cleanup_references_before_delete(self, element_id: str) -> None:
        document = self._document
        element = document.get(element_id)
        if element is None:
            return

        # Kaskadierendes Löschen eines Grundrisses inkl. untergeordneter Elemente.
        if element_id in document.floorplans:
            dependent_ids: list[str] = []
            for candidate in document.all_elements():
                if candidate.id == element_id:
                    continue
                if candidate.floor_plan_id == element_id:
                    dependent_ids.append(candidate.id)
            for dependent_id in dependent_ids:
                self._cleanup_references_before_delete(dependent_id)
                document.remove(dependent_id)
                self.properties.forget_element(dependent_id)

        # Elektropunkt entfernen: Kabelenden lösen.
        if element_id in document.elements["elec_points"]:
            for cable in document.elements["elec_cables"].values():
                changed = False
                if cable.start_ap == element_id:
                    cable.start_ap = ""
                    cable.geom["cable_start_ap"] = ""
                    changed = True
                if cable.end_ap == element_id:
                    cable.end_ap = ""
                    cable.geom["cable_end_ap"] = ""
                    changed = True
                if changed:
                    document.element_changed.emit(cable.id)

        # HKV entfernen: Heizkreiszuordnung und Leitungsknoten lösen.
        if element_id in document.elements["hkv_points"]:
            removed_hkv_name = (element.name or "").strip()
            for circuit in document.elements["circuits"].values():
                changed = False
                if circuit.geom.get("supply_hkv") == element_id:
                    circuit.geom["supply_hkv"] = ""
                    changed = True
                if removed_hkv_name and (circuit.distributor or "").strip() == removed_hkv_name:
                    circuit.distributor = ""
                    changed = True
                if changed:
                    document.element_changed.emit(circuit.id)

            for line in document.elements["hkv_lines"].values():
                changed = False
                if line.start_hkv == element_id:
                    line.start_hkv = ""
                    line.geom["hkv_line_start"] = ""
                    changed = True
                if line.end_hkv == element_id:
                    line.end_hkv = ""
                    line.geom["hkv_line_end"] = ""
                    changed = True
                if changed:
                    document.element_changed.emit(line.id)

        # Kabel entfernen: Referenzen in AP-Konfigurationen bereinigen.
        if element_id in document.elements["elec_cables"]:
            for point in document.elements["elec_points"].values():
                changed = False
                uv = point.data.get("uv_config") or {}
                if isinstance(uv, dict):
                    slots = uv.get("slots") or []
                    for slot in slots:
                        if isinstance(slot, dict) and (slot.get("cable") or "") == element_id:
                            slot["cable"] = ""
                            changed = True

                up = point.data.get("up_distribution_config") or {}
                if isinstance(up, dict):
                    if str(up.get("incoming_cable_id", "") or "").strip() == element_id:
                        up["incoming_cable_id"] = ""
                        changed = True

                    outgoing_ids = up.get("outgoing_cable_ids") or []
                    if isinstance(outgoing_ids, list):
                        filtered = [
                            str(cable_id or "").strip()
                            for cable_id in outgoing_ids
                            if str(cable_id or "").strip() and str(cable_id or "").strip() != element_id
                        ]
                        if filtered != outgoing_ids:
                            up["outgoing_cable_ids"] = filtered
                            changed = True

                    mappings = up.get("mappings") or []
                    for mapping in mappings:
                        if not isinstance(mapping, dict):
                            continue
                        if str(mapping.get("cable_id", "") or "").strip() == element_id:
                            mapping["cable_id"] = ""
                            changed = True
                        if str(mapping.get("to_cable_id", "") or "").strip() == element_id:
                            mapping["to_cable_id"] = ""
                            changed = True
                if changed:
                    document.element_changed.emit(point.id)

    def _duplicate_element(self, source_id: str, *, record_undo: bool = True) -> str | None:
        source = self._document.get(source_id)
        if source is None:
            return None

        if record_undo:
            self._push_undo()
        cls = type(source)
        new_id = self._document.new_id(cls)
        data = copy.deepcopy(source.data)
        geom = copy.deepcopy(source.geom)

        if cls.ID_FIELD:
            data[cls.ID_FIELD] = new_id
        data.pop("uid", None)

        # Referenzen bei Duplikaten lösen, damit kein implizites Linking entsteht.
        if isinstance(source, Circuit):
            data["distributor"] = ""
            geom["supply_hkv"] = ""
        if isinstance(source, ElecCable):
            data["start_ap"] = ""
            data["end_ap"] = ""
            geom["cable_start_ap"] = ""
            geom["cable_end_ap"] = ""
        if isinstance(source, HkvLine):
            data["start_hkv"] = ""
            data["end_hkv"] = ""
            geom["hkv_line_start"] = ""
            geom["hkv_line_end"] = ""

        self._offset_geometry_for_duplicate(type(source), geom)

        if isinstance(source, FloorPlan):
            layer = copy.deepcopy(source.layer)
            layer["fp_id"] = new_id
            layer["offset_x"] = float(layer.get("offset_x", 0.0)) + 20.0
            layer["offset_y"] = float(layer.get("offset_y", 0.0)) + 20.0
            layer["ref_line"] = self._offset_points(layer.get("ref_line"))
            layer["polygon"] = self._offset_points(layer.get("polygon"))
            clone = cls(new_id, data=data, geom=geom, layer=layer)
            self._document.add(clone)
            if isinstance(clone, FloorPlan):
                self._document.floorplan_order.append(new_id)
                if clone.file_path:
                    self.canvas.load_floor_plan_image(new_id, str(clone.file_path))
        else:
            clone = cls(new_id, data=data, geom=geom)
            self._document.add(clone)

        self.canvas.register_element(new_id)
        self._document.element_changed.emit(new_id)
        self._emit_structure_changed()
        self.canvas.update()
        self._mark_dirty()
        return new_id

    @staticmethod
    def _offset_point(point_like):
        if (
            isinstance(point_like, (list, tuple))
            and len(point_like) == 2
            and isinstance(point_like[0], (int, float))
            and isinstance(point_like[1], (int, float))
        ):
            return [float(point_like[0]) + 20.0, float(point_like[1]) + 20.0]
        return point_like

    @classmethod
    def _offset_points(cls, maybe_points):
        if not isinstance(maybe_points, list):
            return maybe_points
        out = []
        for entry in maybe_points:
            if (
                isinstance(entry, (list, tuple))
                and len(entry) == 2
                and isinstance(entry[0], (int, float))
                and isinstance(entry[1], (int, float))
            ):
                out.append([float(entry[0]) + 20.0, float(entry[1]) + 20.0])
            else:
                out.append(entry)
        return out

    @classmethod
    def _offset_geometry_for_duplicate(cls, element_cls: type, geom: dict) -> None:
        offset_keys = {
            "polygons",
            "start_points",
            "manual_routes",
            "supply_lines",
            "elec_points",
            "elec_rooms",
            "elec_room_polygons",
            "elec_cables",
            "hkv_points",
            "hkv_lines",
            "label_positions",
        }
        single_point_keys = {"start_points", "elec_points", "hkv_points", "label_positions"}
        for key in offset_keys:
            if key in geom:
                value = geom[key]
                if key in single_point_keys:
                    geom[key] = cls._offset_point(value)
                else:
                    geom[key] = cls._offset_points(value)

        text_entry = geom.get("text_annotations")
        if isinstance(text_entry, dict) and "pos" in text_entry:
            text_entry["pos"] = cls._offset_point(text_entry["pos"])

    def _renumber_elec_points(self) -> None:
        points = list(self._document.elements["elec_points"].values())
        if not points:
            self.statusBar().showMessage("Keine Anschlusspunkte vorhanden", 2500)
            return

        self._push_undo()
        groups: dict[str, list[ElecPoint]] = {}
        for point in points:
            base = (point.name or point.id).strip() or point.id
            groups.setdefault(base, []).append(point)

        changed = 0
        for base_name, grouped in groups.items():
            if len(grouped) <= 1:
                continue
            grouped.sort(key=lambda p: p.id)
            for index, point in enumerate(grouped, start=1):
                new_name = f"{base_name}{index}"
                if point.name != new_name:
                    point.name = new_name
                    self._document.element_changed.emit(point.id)
                    changed += 1

        if changed == 0:
            self.statusBar().showMessage("Keine Umnummerierung nötig", 2500)
            return

        self.canvas.update()
        self._mark_dirty()
        self.statusBar().showMessage(f"{changed} Anschlusspunkte umnummeriert", 3000)

    def _workspace_context_action_specs(self, element_id: str, kind: str) -> list[tuple[str, str, bool]]:
        specs: list[tuple[str, str, bool]] = []
        element = self._document.get(element_id) if element_id else None

        if kind == "navigator_root":
            specs.append(("add_floorplan_empty", "Neuen Grundriss einfügen", True))

        if kind == "floorplan" and element_id in self._document.floorplans:
            specs.append(("activate", "Als aktiv setzen", True))

        if element is not None:
            if isinstance(element, ElecPoint):
                specs.append(("draw_cable_from_ap", "Kabel ziehen", True))

            schema = schema_for(element)
            same_workspace = getattr(type(element), "LAYER", None) is self._workspace.layer
            if schema is not None and same_workspace:
                values = {spec.key: spec.default for spec in schema.fields}
                for spec in schema.fields:
                    values[spec.key] = element.data.get(spec.key, values[spec.key])
                    if element_id in self._document.floorplans or element_id in self._document.furniture:
                        values[spec.key] = getattr(element, "layer", {}).get(spec.key, values[spec.key])
                for action in schema.actions:
                    if action.id in {"delete", "duplicate"}:
                        continue
                    if not action.is_visible_for(values):
                        continue
                    specs.append((action.id, action.label, action.is_enabled_for(element)))

        return specs

    def _generic_context_action_specs(self, element_id: str) -> list[tuple[str, str, bool]]:
        has_element = self._document.get(element_id) is not None if element_id else False
        is_helper = _parse_helper_nav_id(element_id) is not None if element_id else False
        is_measurement = _parse_measurement_nav_id(element_id) is not None if element_id else False
        # Messungen können nicht kopiert / dupliziert werden – ihre IDs sind
        # positionsbasiert und an die Canvas-Listen gebunden.
        can_copy_cut_dup = has_element and not is_measurement
        return [
            ("undo", "Rückgängig", bool(self._undo_stack)),
            ("redo", "Wiederherstellen", bool(self._redo_stack)),
            ("cut", "Ausschneiden", can_copy_cut_dup),
            ("copy", "Kopieren", can_copy_cut_dup),
            ("paste", "Einfügen", self._copy_buffer is not None),
            ("duplicate", "Duplizieren", can_copy_cut_dup),
            ("delete", "Löschen", has_element or is_helper or is_measurement),
        ]

    def _run_context_action(self, action_id: str, element_id: str, kind: str) -> None:
        selected_ids = self.navigator.selected_ids()
        is_batch_selection = len(selected_ids) > 1 and element_id in selected_ids

        if element_id and not (is_batch_selection and action_id == "delete"):
            self.navigator.select(element_id)
            self.canvas.set_selected_item(element_id)
            if self._document.get(element_id) is not None:
                self.properties.show_element(element_id)

        if action_id == "activate":
            self._on_floorplan_activated(element_id)
            return
        if action_id == "add_floorplan_empty":
            self._add_empty_floorplan()
            return
        if action_id == "undo":
            self._undo()
            return
        if action_id == "redo":
            self._redo()
            return
        if action_id == "cut":
            self._cut_selected()
            return
        if action_id == "copy":
            self._copy_selected()
            return
        if action_id == "paste":
            self._paste_copied()
            return
        if action_id == "duplicate":
            self._duplicate_selected()
            return
        if action_id == "delete":
            self._delete_selected()
            return
        if action_id == "draw_cable_from_ap" and element_id:
            self._add_elec_cable_from_ap(element_id)
            return
        if element_id:
            self._on_property_action(element_id, action_id)

    def _open_context_menu(self, element_id: str, kind: str, global_pos) -> None:
        from PySide6.QtCore import QPoint  # noqa: PLC0415

        menu = QMenu(self)
        action_map: dict[QAction, str] = {}

        selected_ids = self.navigator.selected_ids()
        batch_ids = selected_ids if len(selected_ids) > 1 and element_id in selected_ids else []

        if batch_ids:
            batch_label = f"{len(batch_ids)} Elemente löschen"
            delete_action = menu.addAction(batch_label)
            delete_action.setEnabled(True)
            action_map[delete_action] = "delete"

            menu.addSeparator()
            undo_action = menu.addAction("Rückgängig")
            undo_action.setEnabled(bool(self._undo_stack))
            action_map[undo_action] = "undo"
            redo_action = menu.addAction("Wiederherstellen")
            redo_action.setEnabled(bool(self._redo_stack))
            action_map[redo_action] = "redo"

            pos_for_exec = global_pos.toPoint() if hasattr(global_pos, "toPoint") else QPoint(global_pos)
            chosen = menu.exec(pos_for_exec)
            if chosen is None:
                return
            action_id = action_map.get(chosen)
            if action_id:
                self._run_context_action(action_id, element_id, kind)
            return

        for action_id, label, enabled in self._workspace_context_action_specs(element_id, kind):
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action_map[action] = action_id

        if action_map:
            menu.addSeparator()

        for action_id, label, enabled in self._generic_context_action_specs(element_id):
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action_map[action] = action_id

        # QMenu.exec erwartet QPoint (int), nicht QPointF
        pos_for_exec = global_pos.toPoint() if hasattr(global_pos, "toPoint") else QPoint(global_pos)
        chosen = menu.exec(pos_for_exec)
        if chosen is None:
            return
        action_id = action_map.get(chosen)
        if action_id:
            self._run_context_action(action_id, element_id, kind)

    def _on_navigator_context(self, element_id: str, kind: str, global_pos) -> None:
        self._open_context_menu(element_id, kind, global_pos)

    def _on_navigator_floorplan_order_changed(self, order: list[str]) -> None:
        if self._document is None:
            return
        cleaned: list[str] = []
        seen: set[str] = set()
        for fp_id in order:
            if fp_id in self._document.floorplans and fp_id not in seen:
                cleaned.append(fp_id)
                seen.add(fp_id)
        for fp_id in self._document.floorplan_order:
            if fp_id in self._document.floorplans and fp_id not in seen:
                cleaned.append(fp_id)
                seen.add(fp_id)
        old_order = list(self._document.floorplan_order)
        old_floor_order = [fid for fid in old_order if fid in self._document.floorplans]
        if cleaned == old_floor_order:
            return

        # Reorder only top-level floor plans and keep dependent layers in order.
        furniture_by_floor: dict[str, list[str]] = {}
        loose_layer_ids: list[str] = []
        for layer_id in old_order:
            furniture = self._document.furniture.get(layer_id)
            if furniture is None:
                continue
            parent_id = str(furniture.floor_plan_id or "").strip()
            if parent_id in self._document.floorplans:
                furniture_by_floor.setdefault(parent_id, []).append(layer_id)
            else:
                loose_layer_ids.append(layer_id)

        new_order: list[str] = []
        for fp_id in cleaned:
            new_order.append(fp_id)
            new_order.extend(furniture_by_floor.get(fp_id, []))

        for layer_id in old_order:
            if layer_id in self._document.floorplans:
                continue
            if layer_id not in new_order:
                new_order.append(layer_id)
        for layer_id in loose_layer_ids:
            if layer_id not in new_order:
                new_order.append(layer_id)

        self._document.floorplan_order = new_order
        self.canvas.set_floor_plan_order(new_order)
        self.canvas.update()
        self._emit_structure_changed()
        self._mark_dirty()

    def _on_canvas_context_requested(self, obj_type: str, obj_id: str, _canvas_pt, global_pos) -> None:
        if obj_type == "helper_line" and obj_id:
            helper_floor = self.canvas._helper_selected_floor_id or self._active_floorplan_id()
            obj_id = f"{_HELPER_NAV_ID_PREFIX}{helper_floor}::{obj_id}"
            kind = "helper_line"
            self._open_context_menu(obj_id, kind, global_pos)
            return
        kind = "floorplan" if obj_id in self._document.floorplans else "element"
        self._open_context_menu(obj_id, kind, global_pos)

    def _sync_canvas_to_document(self) -> None:
        """Überträgt die noch nicht gebundenen Canvas-Daten ins Dokument.

        Elementdaten liegen dank :meth:`CanvasWidget.set_document` bereits im
        Dokument. Globale Ansichtsdaten und die Grundriss-Layer werden weiterhin
        im Canvas geführt und hier vor dem Speichern zurückgeschrieben.
        """
        document = self._document
        if document is None:
            return

        document.pdf_export_pages = copy.deepcopy(
            self._normalize_pdf_export_pages(self._pdf_export_pages)
        )
        document.pdf_export_meta = copy.deepcopy(
            self._normalize_pdf_export_meta(self._pdf_export_meta, document.pdf_export_pages)
        )

        canvas_state = self.canvas.to_dict()
        bound_keys = self._bound_canvas_keys()

        for key, value in canvas_state.items():
            if key in bound_keys or key == "floor_plans":
                continue
            document.view[key] = value

        for entry in canvas_state.get("floor_plans", []):
            fp_id = entry.get("fp_id")
            if not fp_id:
                continue
            floor = document.floorplans.get(fp_id) or document.furniture.get(fp_id)
            if floor is not None:
                floor.layer.update(entry)

        # Persistente Vermessung aktuell aus Canvas-Laufzeitdaten zurückschreiben.
        # Diese Daten liegen im Canvas in spezialisierten Strukturen und laufen
        # deshalb nicht vollständig über die gebundenen Map-Views.
        for key in (
            "distance_measurements",
            "distance_label_positions",
            "angle_measurements",
            "angle_label_positions",
        ):
            document.view[key] = canvas_state.get(key, {})

    def _sync_measurements_to_elements(self) -> None:
        """Spiegelt Canvas-Messlisten in echte Document-Elements.

        Die Canvas-Laufzeitlisten ``_measure_lines`` / ``_angle_measurements``
        sind die einzige Quelle der Wahrheit für interaktiv gezeichnete
        Messungen.  Diese Methode erstellt oder aktualisiert zugehörige
        ``DistanceMeasurement``- / ``AngleMeasurement``-Elemente im Dokument
        (IDs ``MSRD-{idx+1}`` / ``MSRA-{idx+1}``).  Stale Elemente werden
        entfernt, damit der Navigator stets mit den Canvas-Listen übereinstimmt.
        """
        document = self._document
        if document is None:
            return

        fp_id = self._active_floorplan_id()

        # --- Distanzmessungen ---
        live_dist_ids: set[str] = set()
        for idx, (p1, p2, _mm) in enumerate(self.canvas._measure_lines):
            eid = f"MSRD-{idx + 1}"
            live_dist_ids.add(eid)
            el = document.elements.get("distance_measurements", {}).get(eid)
            if el is None:
                el = DistanceMeasurement.create(eid, floor_plan_id=fp_id, visible=True)
                document.add(el)
            # Geometrie aktualisieren
            el.geom["distance_measurements"] = [[p1.x(), p1.y()], [p2.x(), p2.y()]]
            lp_list = self.canvas._measure_label_positions
            if idx < len(lp_list):
                el.geom["distance_label_positions"] = list(lp_list[idx])
            else:
                el.geom.pop("distance_label_positions", None)

        # Veraltete Distanzmessungen entfernen
        stale_dist = [
            eid for eid in list(document.elements.get("distance_measurements", {}))
            if eid not in live_dist_ids
        ]
        for eid in stale_dist:
            document.remove(eid)

        # --- Winkelmessungen ---
        live_angle_ids: set[str] = set()
        for idx, (p1, p2, p3, _deg) in enumerate(self.canvas._angle_measurements):
            eid = f"MSRA-{idx + 1}"
            live_angle_ids.add(eid)
            el = document.elements.get("angle_measurements", {}).get(eid)
            if el is None:
                el = AngleMeasurement.create(eid, floor_plan_id=fp_id, visible=True)
                document.add(el)
            el.geom["angle_measurements"] = [
                [p1.x(), p1.y()], [p2.x(), p2.y()], [p3.x(), p3.y()]
            ]
            lp_list = self.canvas._angle_measure_label_positions
            if idx < len(lp_list):
                el.geom["angle_label_positions"] = list(lp_list[idx])
            else:
                el.geom.pop("angle_label_positions", None)

        # Veraltete Winkelmessungen entfernen
        stale_angle = [
            eid for eid in list(document.elements.get("angle_measurements", {}))
            if eid not in live_angle_ids
        ]
        for eid in stale_angle:
            document.remove(eid)

        document.structure_changed.emit()

    @staticmethod
    def _bound_canvas_keys() -> set[str]:
        """canvas-Schlüssel, die bereits über Views im Dokument liegen."""
        from model.canvas_binding import BINDINGS, BOUND_VIEW_KEYS  # noqa: PLC0415

        keys = {binding.geom_key for binding in BINDINGS if binding.geom_key}
        return keys | set(BOUND_VIEW_KEYS)

    def _load_floor_plan_images(self, document: Document) -> None:
        """Lädt die Grundriss-/Einrichtungsbilder in den Canvas.

        ``canvas.from_dict`` überträgt nur die Geometrie; Bilder müssen mit
        gegen das Projektverzeichnis aufgelösten Pfaden nachgeladen werden.
        """
        base_dir = self._project_path.parent if self._project_path else None
        for fp_id, floor in {**document.floorplans, **document.furniture}.items():
            file_path = (floor.file_path or "").strip()
            if not file_path:
                continue
            if is_data_uri(file_path):
                self.canvas.load_floor_plan_image(fp_id, file_path)
                continue
            resolved = Path(file_path)
            if not resolved.is_absolute() and base_dir is not None:
                resolved = (base_dir / resolved).resolve()
            if resolved.exists():
                self.canvas.load_floor_plan_image(fp_id, str(resolved))
            else:
                self.log.warning(f"Bild nicht gefunden für {fp_id}: {file_path}")
        self.canvas.update()

    def _reload_elec_points_to_canvas(self, document: Document) -> None:
        """Setzt Farbe, Größe und Icon für alle im Dokument vorhandenen APs.

        ``canvas.from_dict`` und ``set_document`` übertragen nur Geometrie.
        Icons und Farben müssen danach manuell synchronisiert werden.
        """
        from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415

        mm_per_px = max(self.canvas._mm_per_px, 1e-9)
        for pid, point in document.elements.get("elec_points", {}).items():
            # Farbe
            color = str(point.data.get("color") or "#4fc3f7").strip()
            self.canvas.set_color(pid, QColor(color))

            # Größe
            try:
                w = float(point.data.get("width") or 30.0)
                h = float(point.data.get("height") or 30.0)
            except (TypeError, ValueError):
                w, h = 30.0, 30.0
            self.canvas._elec_point_size_px[pid] = (w / mm_per_px, h / mm_per_px)

            # Icon
            icon_path = str(point.data.get("icon_path") or "").strip()
            if not icon_path:
                symbol = str(point.data.get("builtin_symbol") or "").strip()
                icon_path = str(BUILTIN_SYMBOLS.get(symbol, "") or "")
            self.canvas.set_elec_point_icon(pid, icon_path)

        self.canvas.update()

    def _on_element_selected(self, element_id: str) -> None:
        self._select_element_everywhere(element_id, update_navigator=False)

    def _on_navigator_selection_changed(self, element_ids: list[str]) -> None:
        if len(element_ids) <= 1:
            return
        self.properties.show_elements(element_ids)
        first_id = element_ids[0]
        self._sync_active_floorplan_for_selection(first_id)
        self.canvas.set_selected_item(first_id)

    def _on_canvas_object_clicked(self, obj_type: str, obj_id: str) -> None:
        if not obj_id:
            return
        if obj_type == "helper_line":
            helper_floor = self.canvas._helper_selected_floor_id or self._active_floorplan_id()
            obj_id = f"{_HELPER_NAV_ID_PREFIX}{helper_floor}::{obj_id}"
        self._select_element_everywhere(obj_id, update_navigator=True)

    def _on_canvas_object_double_clicked(self, obj_type: str, obj_id: str) -> None:
        if not obj_id:
            return
        if obj_type.startswith("annotation_"):
            self._select_element_everywhere(obj_id, update_navigator=True)
            self.canvas.start_edit_annotation_shape(obj_type, obj_id)
            self.statusBar().showMessage(f"{obj_id} bearbeiten", 2500)
            return
        self._on_canvas_object_clicked(obj_type, obj_id)

    def _select_element_everywhere(self, element_id: str, *, update_navigator: bool) -> None:
        if not element_id:
            return
        helper_ref = _parse_helper_nav_id(element_id)
        if helper_ref is not None:
            floor_id, helper_id = helper_ref
            if floor_id and floor_id in self._document.floorplans:
                if self._document.active_floorplan_id != floor_id:
                    self._document.active_floorplan_id = floor_id
                self.canvas.set_active_helper_floor(floor_id)
            if update_navigator:
                self.navigator.select(element_id)
            self.canvas.set_selected_item(element_id)
            self.properties.show_helper_line(floor_id, helper_id, self.canvas,
                                             nav_id=element_id)
            return
        self._sync_active_floorplan_for_selection(element_id)
        if update_navigator:
            self.navigator.select(element_id)
        self.canvas.set_selected_item(element_id)
        self.properties.show_element(element_id)
        annotation_kind = self._annotation_kind_for_element(element_id)
        if annotation_kind:
            self.canvas.start_edit_annotation_shape(annotation_kind, element_id)
        elif self.canvas.tool_mode() == ToolMode.EDIT_ANNOTATION:
            self.canvas.set_tool_mode(ToolMode.NONE)

    def _annotation_kind_for_element(self, element_id: str) -> str:
        if element_id in self._document.elements.get("annotation_lines", {}):
            return "annotation_line"
        if element_id in self._document.elements.get("annotation_rectangles", {}):
            return "annotation_rectangle"
        if element_id in self._document.elements.get("annotation_polylines", {}):
            return "annotation_polyline"
        if element_id in self._document.elements.get("annotation_circles", {}):
            return "annotation_circle"
        if element_id in self._document.elements.get("annotation_ellipses", {}):
            return "annotation_ellipse"
        if element_id in self._document.elements.get("annotation_polygons", {}):
            return "annotation_polygon"
        return ""

    def _sync_active_floorplan_for_selection(self, element_id: str) -> None:
        if self._document is None:
            return

        target_floorplan = ""
        if element_id in self._document.floorplans:
            target_floorplan = element_id
        else:
            element = self._document.get(element_id)
            if element is not None:
                target_floorplan = str(element.floor_plan_id or "").strip()

        if target_floorplan and target_floorplan in self._document.floorplans:
            if self._document.active_floorplan_id != target_floorplan:
                self._document.active_floorplan_id = target_floorplan
            self.canvas.set_active_helper_floor(target_floorplan)

    def _on_floorplan_activated(self, fp_id: str) -> None:
        self._document.active_floorplan_id = fp_id
        self.canvas.set_active_helper_floor(fp_id)
        self.navigator.select(fp_id)
        self.properties.show_element(fp_id)
        self.canvas.set_selected_item(fp_id)
        self.log.info(f"Aktiver Grundriss: {fp_id}")

    def _selected_floorplan_id(self) -> str:
        selected = self._current_selection_id()
        if selected and selected in self._document.floorplans:
            return selected
        return self._active_floorplan_id()

    def _selected_floor_like_id(self) -> str:
        selected = self._current_selection_id()
        if selected and (
            selected in self._document.floorplans or selected in self._document.furniture
        ):
            return selected
        return self._active_floorplan_id()

    def _on_visibility_changed(self, element_id: str, visible: bool) -> None:
        helper_ref = _parse_helper_nav_id(element_id)
        if helper_ref is not None:
            floor_id, helper_id = helper_ref
            self.canvas.set_helper_line_item_visible(floor_id, helper_id, visible)
            self._dirty = True
            self._update_title()
            return
        if not self._document.set_visible(element_id, visible):
            return
        self._apply_canvas_visibility(element_id, visible)
        self._dirty = True
        self._update_title()

    def _on_reassign_floorplan(self, element_id: str, new_fp_id: str) -> None:
        """Ordnet ein Element per Drag-and-Drop einem anderen Grundriss zu."""
        document = self._document
        if document is None or new_fp_id not in document.floorplans:
            return

        # Hilfslinien: physisch im Canvas verschieben (canvas-seitig gespeichert)
        helper_ref = _parse_helper_nav_id(element_id)
        if helper_ref is not None:
            old_fp_id, helper_id = helper_ref
            if old_fp_id == new_fp_id:
                return
            self._push_undo()
            self.canvas.move_helper_line(old_fp_id, helper_id, new_fp_id)
            self._mark_dirty()
            fp_name = (document.floorplans.get(new_fp_id) or object()).name or new_fp_id
            self.statusBar().showMessage(f"Hilfslinie {helper_id} → {fp_name}", 2500)
            return

        # Alle anderen Elemente (inkl. Messungen): floor_plan_id ändern
        element = document.get(element_id)
        if element is None or element.floor_plan_id == new_fp_id:
            return
        self._push_undo()
        element.floor_plan_id = new_fp_id
        self._emit_structure_changed()
        self._mark_dirty()
        fp_name = (document.floorplans.get(new_fp_id) or object()).name or new_fp_id
        label = element.name or element_id
        self.statusBar().showMessage(f"{label} → {fp_name}", 2500)

    def _apply_canvas_visibility(self, element_id: str, visible: bool) -> None:
        """Spiegelt die Sichtbarkeit in den Canvas.

        Element- und Grundrissdaten liegen dank der Dokumentbindung bereits
        im selben Speicher; der Aufruf sorgt zusätzlich dafür, dass abhängige
        Darstellungen (Referenzlinie, Hilfslinien) mitgeschaltet und der
        Canvas neu gezeichnet wird.
        """
        self.canvas.set_element_visible(element_id, visible)

    def _active_floorplan_id(self) -> str:
        fp_id = self._document.active_floorplan_id
        if fp_id:
            return fp_id
        if self._document.floorplan_order:
            return self._document.floorplan_order[0]
        return ""

    def _require_floorplan(self) -> str:
        fp_id = self._active_floorplan_id()
        if fp_id:
            return fp_id
        QMessageBox.information(self, "Kein Grundriss", "Bitte zuerst einen Grundriss hinzufügen.")
        return ""

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_title()

    def _emit_structure_changed(self) -> None:
        self._document.structure_changed.emit()
        self._refresh_schema_windows()

    def _add_floorplan(self) -> None:
        image_path, _ = QFileDialog.getOpenFileName(self, "Grundrissbild wählen", "", _IMAGE_FILTER)
        fp_id = self._document.new_id(FloorPlan)
        default_name = f"Grundriss {len(self._document.floorplans) + 1}"
        name, ok = QInputDialog.getText(self, "Grundriss", "Name:", text=default_name)
        if not ok:
            return
        name = (name or "").strip() or default_name
        self._push_undo()

        layer = {
            "fp_id": fp_id,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
            "opacity": 1.0,
            "visible": True,
            "mm_per_px": 1.0,
            "ref_length_mm": 5000.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
            "polygon_color": "#8d99ae",
            "polygon": [],
        }
        data = {
            "name": name,
            "visible": True,
            "file_path": image_path or "",
            "polygon_color": "#8d99ae",
            "ref_line_visible": True,
            "ref_line_color": "#ffdd00",
            "opacity": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
            "ref_length_mm": 5000.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
        }
        floor = FloorPlan(fp_id, data=data, layer=layer)
        self._document.add(floor)
        self._document.floorplan_order.append(fp_id)
        self._document.active_floorplan_id = fp_id

        self.canvas.add_floor_plan(fp_id, image_path or "")
        self.canvas.set_floor_plan_visible(fp_id, True)
        self.canvas.set_active_helper_floor(fp_id)

        self._emit_structure_changed()
        self._mark_dirty()
        self.log.success(f"Grundriss hinzugefügt: {fp_id}")

    def _add_empty_floorplan(self) -> None:
        fp_id = self._document.new_id(FloorPlan)
        default_name = f"Grundriss {len(self._document.floorplans) + 1}"
        self._push_undo()

        layer = {
            "fp_id": fp_id,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
            "opacity": 1.0,
            "visible": True,
            "mm_per_px": 1.0,
            "ref_length_mm": 5000.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
            "polygon_color": "#8d99ae",
            "polygon": [],
        }
        data = {
            "name": default_name,
            "visible": True,
            "file_path": "",
            "polygon_color": "#8d99ae",
            "ref_line_visible": True,
            "ref_line_color": "#ffdd00",
            "opacity": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
            "ref_length_mm": 5000.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
        }
        floor = FloorPlan(fp_id, data=data, layer=layer)
        self._document.add(floor)
        self._document.floorplan_order.append(fp_id)
        self._document.active_floorplan_id = fp_id

        self.canvas.add_floor_plan(fp_id)
        self.canvas.set_floor_plan_visible(fp_id, True)
        self.canvas.set_active_helper_floor(fp_id)

        self._emit_structure_changed()
        self._mark_dirty()
        self.log.success(f"Leerer Grundriss hinzugefügt: {fp_id}")

    def _add_furniture(self) -> None:
        from model.elements import Furniture  # noqa: PLC0415

        name, ok = QInputDialog.getText(self, "Einrichtung", "Name:", text="Möbel")
        if not ok:
            return
        name = (name or "").strip() or "Möbel"
        fp_id = self._active_floorplan_id()
        self._push_undo()
        eid = self._document.new_id(Furniture)
        layer = {
            "fp_id": eid,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
            "opacity": 1.0,
            "visible": True,
            "mm_per_px": self.canvas.get_mm_per_px() or 1.0,
            "ref_length_mm": 1000.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
            "polygon_color": "#8d99ae",
            "polygon": [],
        }
        data = {
            "name": name,
            "visible": True,
            "file_path": "",
            "polygon_color": "#8d99ae",
            "opacity": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
            "floor_plan_id": fp_id,
        }
        furniture = Furniture(eid, data=data, layer=layer)
        self._document.add(furniture)
        self._document.floorplan_order.append(eid)
        self.canvas.add_floor_plan(eid)
        self.canvas.set_floor_plan_visible(eid, True)
        self._emit_structure_changed()
        self.navigator.select(eid)
        self.canvas.start_draw_floor_plan_polygon(eid)
        self._mark_dirty()
        self.log.success(f"Einrichtung hinzugefügt: {eid}")

    def _add_circuit(self) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        self._push_undo()
        cid = self._document.new_id(Circuit)
        suffix = cid.rsplit("-", 1)[-1]
        circuit = Circuit.create(
            cid,
            floor_plan_id=fp_id,
            name=f"Heizkreis {suffix}",
            color="#2a9d8f",
            diameter=16.0,
            spacing=150.0,
            wall_dist=200.0,
            visible=True,
            label_visible=True,
            label_size=12.0,
            room_temp=20.0,
            floor_covering="Fliesen / Keramik",
            distributor="",
        )
        self._document.add(circuit)
        self.canvas.register_element(cid)
        self._emit_structure_changed()
        self.navigator.select(cid)
        self.canvas.start_drawing(cid)
        self._mark_dirty()

    def _add_elec_point(self) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        self._push_undo()
        pid = self._document.new_id(ElecPoint)
        point = ElecPoint.create(
            pid,
            floor_plan_id=fp_id,
            name=f"Anschlusspunkt {pid.rsplit('-', 1)[-1]}",
            color="#4fc3f7",
            width=30.0,
            height=30.0,
            icon_path="",
            builtin_symbol="Steckdose",
            visible=True,
            label_visible=True,
            label_size=12.0,
            position="Wand",
            height_from_floor=30.0,
            smarthome_device="",
            smarthome_device_color="",
            note="",
        )
        self._document.add(point)
        self._emit_structure_changed()
        self.navigator.select(pid)
        self.canvas.start_place_elec_point(pid, 30.0, 30.0)
        self._mark_dirty()

    def _add_elec_room(self) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        self._push_undo()
        rid = self._document.new_id(ElecRoom)
        room = ElecRoom.create(
            rid,
            floor_plan_id=fp_id,
            name=f"Raum {rid.rsplit('-', 1)[-1]}",
            color="#43aa8b",
            visible=True,
            label_visible=True,
            label_size=12.0,
        )
        self._document.add(room)
        self.canvas.register_element(rid)
        self._emit_structure_changed()
        self.navigator.select(rid)
        self.canvas.start_draw_elec_room(rid)
        self._mark_dirty()

    def _add_text(self) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        self._push_undo()
        tid = self._document.new_id(TextAnnotation)
        text = TextAnnotation.create(
            tid,
            floor_plan_id=fp_id,
            name=f"Text {tid.rsplit('-', 1)[-1]}",
            visible=True,
        )
        self._document.add(text)
        self.canvas.register_element(tid)
        self._emit_structure_changed()
        self.navigator.select(tid)
        self.canvas.start_place_text(tid, "Text")
        self._mark_dirty()

    def _add_annotation_shape(self, tool_id: str) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        shape_map = {
            "ann.line": (AnnotationLine, "annotation_line", "Linie"),
            "ann.rectangle": (AnnotationRectangle, "annotation_rectangle", "Rechteck"),
            "ann.polyline": (AnnotationPolyline, "annotation_polyline", "Polylinie"),
            "ann.circle": (AnnotationCircle, "annotation_circle", "Kreis"),
            "ann.ellipse": (AnnotationEllipse, "annotation_ellipse", "Ellipse"),
            "ann.polygon": (AnnotationPolygon, "annotation_polygon", "Polygon"),
        }
        model_cls, kind, label = shape_map[tool_id]
        self._push_undo()
        aid = self._document.new_id(model_cls)
        fields: dict[str, object] = {
            "floor_plan_id": fp_id,
            "visible": True,
            "color": "#00e5ff",
            "line_style": "solid",
            "stroke_width": 2.0,
        }
        if kind in {"annotation_rectangle", "annotation_circle", "annotation_ellipse", "annotation_polygon"}:
            fields["fill_color"] = "#ffffff"
        if kind == "annotation_rectangle":
            fields["corner_radius"] = 0.0
        element = model_cls.create(aid, **fields)
        self._document.add(element)
        self.canvas.register_element(aid)
        self._emit_structure_changed()
        self.navigator.select(aid)
        self.canvas.start_place_annotation_shape(
            kind,
            aid,
            color="#00e5ff",
            line_style="solid",
            stroke_width=2.0,
            fill_color="#ffffff",
            corner_radius=0.0,
        )
        self._mark_dirty()

    def _add_elec_cable(self) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        self._push_undo()
        eid = self._document.new_id(ElecCable)
        cable = ElecCable.create(
            eid,
            floor_plan_id=fp_id,
            name=f"Kabel {eid.rsplit('-', 1)[-1]}",
            color="#ffb300",
            visible=True,
            label_visible=True,
            label_size=12.0,
            type="",
            comment="",
            start_ap="",
            end_ap="",
        )
        self._document.add(cable)
        self.canvas.register_element(eid)
        self._emit_structure_changed()
        self.navigator.select(eid)
        self.canvas.start_draw_elec_cable(eid)
        self.statusBar().showMessage(
            "Kabel zeichnen: Linksklick Punkte setzen, Rechtsklick abschließen.",
            5000,
        )
        self.log.info(
            f"Kabel {eid} im Zeichenmodus: Linksklick Punkte, Rechtsklick Abschluss"
        )
        self._mark_dirty()

    def _add_elec_cable_from_ap(self, ap_id: str) -> None:
        point = self._document.elements["elec_points"].get(ap_id)
        if point is None:
            self.statusBar().showMessage("Anschlusspunkt nicht gefunden", 2500)
            return

        fp_id = str(point.floor_plan_id or "").strip()
        if not fp_id:
            fp_id = self._active_floorplan_id() or self._require_floorplan()
        if not fp_id:
            return

        self._push_undo()
        eid = self._document.new_id(ElecCable)
        cable = ElecCable.create(
            eid,
            floor_plan_id=fp_id,
            name=f"Kabel {eid.rsplit('-', 1)[-1]}",
            color="#ffb300",
            visible=True,
            label_visible=True,
            label_size=12.0,
            type="",
            comment="",
            start_ap=ap_id,
            end_ap="",
        )
        self._document.add(cable)
        self.canvas.register_element(eid)
        self._emit_structure_changed()
        self.navigator.select(eid)
        self.canvas.start_draw_elec_cable_from_ap(eid, ap_id)
        self.statusBar().showMessage(
            "Kabel ziehen: Start am AP gesetzt, Linksklick Punkte setzen, Rechtsklick abschließen.",
            5000,
        )
        self.log.info(f"Kabel {eid} ab AP {ap_id} im Zeichenmodus gestartet")
        self._mark_dirty()

    def _add_hkv(self) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        self._push_undo()
        hid = self._document.new_id(Hkv)
        hkv = Hkv.create(
            hid,
            floor_plan_id=fp_id,
            name=f"HKV {hid.rsplit('-', 1)[-1]}",
            color="#e91e63",
            visible=True,
            label_visible=True,
            label_size=12.0,
            width=50.0,
            height=50.0,
            icon_path="",
        )
        self._document.add(hkv)
        self.canvas.register_element(hid)
        self._emit_structure_changed()
        self.navigator.select(hid)
        self.canvas.start_place_hkv(hid, 50.0, 50.0)
        self._mark_dirty()

    def _add_hkv_line(self) -> None:
        fp_id = self._require_floorplan()
        if not fp_id:
            return
        self._push_undo()
        lid = self._document.new_id(HkvLine)
        line = HkvLine.create(
            lid,
            floor_plan_id=fp_id,
            name=f"HKV-Leitung {lid.rsplit('-', 1)[-1]}",
            color="#9c27b0",
            visible=True,
            label_visible=True,
            label_size=12.0,
            start_hkv="",
            end_hkv="",
        )
        self._document.add(line)
        self.canvas.register_element(lid)
        self._emit_structure_changed()
        self.navigator.select(lid)
        self.canvas.start_draw_hkv_line(lid)
        self._mark_dirty()


    def _new_project(self) -> None:
        if not self._confirm_discard():
            return
        self._project_path = None
        self._dirty = False
        self._set_document(Document())
        self.log.info("Neues Projekt angelegt")

    def _open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Projekt öffnen", "", _FILE_FILTER)
        if not path:
            return
        self.open_project_file(Path(path))

    def open_project_file(self, path: Path) -> bool:
        """Öffnet ein Projekt ohne Dialog (z. B. per Kommandozeile)."""
        try:
            document = load_document(path)
        except Exception as exc:  # noqa: BLE001 - Nutzerfeedback statt Absturz
            QMessageBox.critical(self, "Fehler", f"Projekt konnte nicht geladen werden:\n{exc}")
            self.log.error(f"Laden fehlgeschlagen: {exc}")
            return False
        self._project_path = Path(path)
        self._dirty = False
        self._set_document(document)
        self._remember_project_path(self._project_path)
        self.log.success(f"Projekt geladen: {path}")
        return True

    def _save_project(self) -> bool:
        if self._project_path is None:
            return self._save_project_as()
        self._sync_canvas_to_document()
        try:
            save_document(self._document, self._project_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{exc}")
            self.log.error(f"Speichern fehlgeschlagen: {exc}")
            return False
        self._dirty = False
        self._remember_project_path(self._project_path)
        self._update_title()
        self.log.success(f"Gespeichert: {self._project_path}")
        return True

    def _import_hrp_elements(self) -> None:
        start_dir = str(self._project_path.parent) if self._project_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "HRP-Quelle wählen",
            start_dir,
            _FILE_FILTER,
        )
        if not path:
            return

        source_path = Path(path)
        if self._project_path is not None:
            try:
                if source_path.resolve() == self._project_path.resolve():
                    QMessageBox.information(self, "HRP-Import", "Die aktuell geöffnete Datei kann nicht in sich selbst importiert werden.")
                    return
            except OSError:
                pass

        try:
            source_document = load_document(source_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "HRP-Import", f"Quelle konnte nicht geladen werden:\n{exc}")
            return

        candidates = iter_import_candidates(source_document)
        if not candidates:
            QMessageBox.information(self, "HRP-Import", "Die gewählte HRP enthält keine importierbaren Elemente.")
            return

        dialog = HrpImportDialog(
            source_document,
            candidates,
            source_label=source_path.name,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_keys = dialog.selected_keys()
        if not selected_keys:
            QMessageBox.information(self, "HRP-Import", "Keine Elemente ausgewählt.")
            return

        self._push_undo()
        result = import_selected_elements(source_document, self._document, selected_keys)
        self._load_floor_plan_images(self._document)
        self._reload_elec_points_to_canvas(self._document)
        self.canvas._rebuild_label_map()
        self.canvas.update()
        self._emit_structure_changed()
        self._mark_dirty()

        self.statusBar().showMessage(
            (
                f"HRP-Import: direkt {len(result.selected_keys)}, "
                f"automatisch {len(result.auto_included_keys)}, "
                f"importiert {len(result.imported_keys)}"
            ),
            5000,
        )
        QMessageBox.information(
            self,
            "HRP-Import",
            (
                f"Direkt ausgewählt: {len(result.selected_keys)}\n"
                f"Automatisch ergänzt: {len(result.auto_included_keys)}\n"
                f"Importiert gesamt: {len(result.imported_keys)}"
            ),
        )

    def _save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Projekt speichern", "", _FILE_FILTER)
        if not path:
            return False
        self._project_path = Path(path)
        return self._save_project()

    def _repair_project(self) -> bool:
        if self._project_path is None:
            if not self._save_project_as():
                return False
        elif self._dirty and not self._save_project():
            return False

        assert self._project_path is not None
        try:
            _repaired, changes, backup_path, _written = repair_and_save_hrp(
                self._project_path,
                output_path=self._project_path,
                backup=True,
                aggressive=True,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Fehler", f"Reparatur fehlgeschlagen:\n{exc}")
            self.log.error(f"Reparatur fehlgeschlagen: {exc}")
            return False

        if not self.open_project_file(self._project_path):
            return False

        backup_text = str(backup_path) if backup_path is not None else "(kein Backup)"
        QMessageBox.information(
            self,
            "Reparatur abgeschlossen",
            (
                f"Projekt wurde repariert und neu geladen.\n\n"
                f"Backup: {backup_text}\n"
                f"Aenderungen: {len(changes)}"
            ),
        )
        self.log.success(
            f"Reparatur abgeschlossen ({len(changes)} Aenderungen, Backup: {backup_text})"
        )
        return True

    def _sync_floorplan_scales_from_references(self, *, show_feedback: bool = True) -> tuple[int, int]:
        """Synchronisiert alle Grundriss-Skalierungen aus Referenzlinien.

        Returns:
            Tuple aus (valid_floor_count, changed_floor_count).
        """

        def _ref_implied_mpp(fp_id: str) -> float | None:
            layer = self.canvas._floor_plans.get(fp_id)
            if layer is None or layer.ref_p1 is None or layer.ref_p2 is None:
                return None
            ref_len = float(layer.ref_length_mm or 0.0)
            if ref_len <= 0.0:
                return None
            px_len = math.hypot(layer.ref_p2.x() - layer.ref_p1.x(), layer.ref_p2.y() - layer.ref_p1.y())
            if px_len <= 1e-9:
                return None
            return ref_len / px_len

        ordered_ids: list[str] = []
        seen: set[str] = set()
        for fid in self._document.floorplan_order:
            if fid in self._document.floorplans and fid not in seen:
                ordered_ids.append(fid)
                seen.add(fid)
        for fid in self._document.floorplans.keys():
            if fid not in seen:
                ordered_ids.append(fid)

        would_change = False
        for fid in ordered_ids:
            layer = self.canvas._floor_plans.get(fid)
            implied = _ref_implied_mpp(fid)
            if layer is None or implied is None:
                continue
            current = float(layer.mm_per_px or 0.0)
            if current <= 0.0 or abs(implied - current) > 1e-9:
                would_change = True
                break

        if would_change:
            self._push_undo()

        valid_count = 0
        changed_count = 0
        for fid in ordered_ids:
            layer = self.canvas._floor_plans.get(fid)
            if layer is None:
                continue
            before = float(layer.mm_per_px or 0.0)
            if not self._recompute_floorplan_scale_from_reference(fid):
                continue
            valid_count += 1
            after = float(layer.mm_per_px or 0.0)
            if abs(after - before) > 1e-9:
                changed_count += 1
                self._document.element_changed.emit(fid)

        if changed_count > 0:
            self._mark_dirty()
            self._refresh_schema_windows()

        if show_feedback:
            if valid_count == 0:
                QMessageBox.information(
                    self,
                    "Skalierung synchronisieren",
                    "Keine gültigen Referenzlinien mit Referenzlänge gefunden.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Skalierung synchronisieren",
                    (
                        "Grundriss-Skalierungen wurden aus Referenzlinien geprüft.\n\n"
                        f"Gültige Referenzen: {valid_count}\n"
                        f"Aktualisierte Skalierungen: {changed_count}"
                    ),
                )

        return valid_count, changed_count

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Ungespeicherte Änderungen",
            "Das Projekt wurde geändert. Vor dem Fortfahren speichern?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if answer == QMessageBox.Save:
            return self._save_project()
        return answer == QMessageBox.Discard

    # ------------------------------------------------------------------
    # Recent Projects / Last Project
    # ------------------------------------------------------------------
    @staticmethod
    def _settings() -> QSettings:
        return layout_store.settings()

    def _recent_projects(self) -> list[str]:
        recent = self._settings().value(_RECENT_KEY, [])
        if isinstance(recent, str):
            return [recent] if recent else []
        if isinstance(recent, list):
            return [str(p) for p in recent if str(p).strip()]
        return []

    def _rebuild_recent_menu(self) -> None:
        if self._recent_menu is None:
            return
        self._recent_menu.clear()
        found = False
        for path_str in self._recent_projects():
            p = Path(path_str)
            if not p.exists():
                continue
            act = self._recent_menu.addAction(p.name)
            act.setToolTip(str(p))
            act.triggered.connect(lambda _checked=False, fp=p: self._open_recent(fp))
            found = True
        if not found:
            act = self._recent_menu.addAction("(keine)")
            act.setEnabled(False)

    def _add_to_recent(self, filepath: Path) -> None:
        s = str(filepath)
        recent = [p for p in self._recent_projects() if p != s]
        recent.insert(0, s)
        self._settings().setValue(_RECENT_KEY, recent[:_MAX_RECENT])
        self._rebuild_recent_menu()

    def _remember_project_path(self, filepath: Path) -> None:
        self._settings().setValue(_LAST_PROJECT_KEY, str(filepath))
        self._add_to_recent(filepath)

    def _default_export_path(self, settings_key: str, default_name: str) -> str:
        raw = str(self._settings().value(settings_key, "") or "").strip()
        if raw:
            last_path = Path(raw)
            parent = last_path.parent
            if parent.exists():
                return str(last_path)
        if self._project_path is not None and self._project_path.parent.exists():
            return str(self._project_path.parent / default_name)
        return default_name

    def _default_pdf_export_path(self) -> str:
        return self._default_export_path(_LAST_PDF_EXPORT_KEY, "projektbericht.pdf")

    def _default_svg_export_path(self) -> str:
        return self._default_export_path(_LAST_SVG_EXPORT_KEY, "heizplan.svg")

    def _remember_export_path(self, settings_key: str, filepath: Path) -> None:
        self._settings().setValue(settings_key, str(filepath))

    def _remember_pdf_export_path(self, filepath: Path) -> None:
        self._remember_export_path(_LAST_PDF_EXPORT_KEY, filepath)

    def _remember_svg_export_path(self, filepath: Path) -> None:
        self._remember_export_path(_LAST_SVG_EXPORT_KEY, filepath)

    def _open_recent(self, filepath: Path) -> None:
        if not filepath.exists():
            QMessageBox.warning(self, "Datei fehlt", f"Projekt nicht gefunden:\n{filepath}")
            # Remove stale entry from recent list.
            filtered = [p for p in self._recent_projects() if p != str(filepath)]
            self._settings().setValue(_RECENT_KEY, filtered)
            self._rebuild_recent_menu()
            return
        if not self._confirm_discard():
            return
        self.open_project_file(filepath)

    def _auto_load_last_project(self) -> None:
        if self._project_path is not None:
            return
        raw = str(self._settings().value(_LAST_PROJECT_KEY, "") or "").strip()
        if not raw:
            return
        path = Path(raw)
        if not path.exists() or path.suffix.lower() not in (".hrp", ".json"):
            return
        self.open_project_file(path)

    def _update_title(self) -> None:
        name = self._project_path.name if self._project_path else "Unbenannt"
        marker = "*" if self._dirty else ""
        self.setWindowTitle(f"HRouting – {name}{marker}")

    # ------------------------------------------------------------------
    # Die eigentlichen _undo/_redo-Implementierungen befinden sich im
    # Abschnitt "Undo / Redo" weiter oben. Diese Stubs werden durch die
    # Methoden in diesem Block ersetzt, sobald das UI gebaut ist.
    # (Keine leeren Stubs mehr nötig – Methoden sind bereits definiert.)

    def _not_implemented(self) -> None:
        self.statusBar().showMessage("Noch nicht portiert", 3000)

    # ------------------------------------------------------------------
    # Spezialfenster (Elektro-Strangschema / Schaltplan)
    # ------------------------------------------------------------------
    def _collect_room_choices(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        for room_id, room in self._document.elements["elec_rooms"].items():
            choices.append((room_id, room.name or room_id))
        return sorted(choices, key=lambda entry: entry[1].lower())

    def _collect_floorplan_choices(self) -> list[tuple[str, str]]:
        names_by_id: dict[str, str] = {}
        for fp_id, floorplan in self._document.floorplans.items():
            names_by_id[str(fp_id)] = str(floorplan.name or fp_id)

        # Fallback for migrated/legacy projects where floorplan references exist
        # on elements but no explicit floorplan objects are present.
        for point in self._document.elements["elec_points"].values():
            fp_id = str(point.floor_plan_id or "").strip()
            if fp_id and fp_id not in names_by_id:
                names_by_id[fp_id] = fp_id
        for room in self._document.elements["elec_rooms"].values():
            fp_id = str(room.floor_plan_id or "").strip()
            if fp_id and fp_id not in names_by_id:
                names_by_id[fp_id] = fp_id

        choices = list(names_by_id.items())
        return sorted(choices, key=lambda entry: entry[1].lower())

    def _collect_room_choices_by_floorplan(self) -> dict[str, list[tuple[str, str]]]:
        out: dict[str, list[tuple[str, str]]] = {}
        for room_id, room in self._document.elements["elec_rooms"].items():
            floor_plan_id = str(room.floor_plan_id or "")
            out.setdefault(floor_plan_id, []).append((room_id, str(room.name or room_id)))
        for room_list in out.values():
            room_list.sort(key=lambda entry: entry[1].lower())
        return out

    @staticmethod
    def _poly_contains(point: list[float], polygon: list[list[float]]) -> bool:
        if len(polygon) < 3:
            return False
        x = float(point[0])
        y = float(point[1])
        inside = False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            xi, yi = float(polygon[i][0]), float(polygon[i][1])
            xj, yj = float(polygon[j][0]), float(polygon[j][1])
            intersects = (yi > y) != (yj > y)
            if intersects:
                denom = (yj - yi) if abs(yj - yi) > 1e-12 else 1e-12
                x_hit = (xj - xi) * (y - yi) / denom + xi
                if x < x_hit:
                    inside = not inside
            j = i
        return inside

    def _collect_point_id_to_room_name(self) -> dict[str, str]:
        room_polys: dict[str, list[list[float]]] = {}
        for rid, room in self._document.elements["elec_rooms"].items():
            poly = room.geom.get("elec_rooms") or room.geom.get("elec_room_polygons")
            if isinstance(poly, list):
                room_polys[rid] = poly

        out: dict[str, str] = {}
        for pid, point in self._document.elements["elec_points"].items():
            p = point.geom.get("elec_points")
            room_name = "(ohne Raum)"
            if isinstance(p, list) and len(p) == 2:
                point_fp = point.floor_plan_id or ""
                for rid, room in self._document.elements["elec_rooms"].items():
                    if room.floor_plan_id != point_fp:
                        continue
                    poly = room_polys.get(rid)
                    if not poly:
                        continue
                    if self._poly_contains([float(p[0]), float(p[1])], poly):
                        room_name = room.name or rid
                        break
            out[pid] = room_name
        return out

    def _build_schema_data(self) -> tuple[list[ApNode], list[CableEdge], dict[str, str]]:
        room_map = self._collect_point_id_to_room_name()
        cables = self._document.elements["elec_cables"]
        points = self._document.elements["elec_points"]

        connected_points: set[str] = set()
        for cable in cables.values():
            s = (cable.start_ap or cable.geom.get("cable_start_ap") or "").strip()
            e = (cable.end_ap or cable.geom.get("cable_end_ap") or "").strip()
            if s:
                connected_points.add(s)
            if e:
                connected_points.add(e)

        ap_nodes: list[ApNode] = []
        for point_id, point in points.items():
            size = point.geom.get("elec_point_size_px") or [point.width or 30.0, point.height or 30.0]
            width_px = float(size[0]) if isinstance(size, (list, tuple)) and len(size) == 2 else float(point.width or 30.0)
            height_px = float(size[1]) if isinstance(size, (list, tuple)) and len(size) == 2 else float(point.height or 30.0)
            ap_type = str(point.data.get("ap_type", "standard") or "standard")
            node = ApNode(
                point_id=point_id,
                name=point.name or point_id,
                room=room_map.get(point_id, "(ohne Raum)"),
                ap_type=ap_type,
                has_distributor_function=ap_type in {"uv", "up_distribution", "hak", "zaehler"},
                is_connected=point_id in connected_points,
                color=str(point.color or "#4fc3f7"),
                icon_path=str(point.data.get("icon_path", "") or ""),
                builtin_symbol=str(point.data.get("builtin_symbol", "") or ""),
                width_px=width_px,
                height_px=height_px,
                width_mm=float(point.data.get("width", 30.0) or 30.0),
                height_mm=float(point.data.get("height", 30.0) or 30.0),
                visible=bool(point.visible),
                label_visible=bool(point.label_visible),
                label_size=float(point.label_size or 12.0),
                position=str(point.data.get("position", "Wand") or "Wand"),
                height_from_floor=float(point.data.get("height_from_floor", 0.0) or 0.0),
                smarthome_device=str(point.data.get("smarthome_device", "") or ""),
                smarthome_device_color=str(point.data.get("smarthome_device_color", "") or ""),
                note=str(point.data.get("note", "") or ""),
                uv_config=copy.deepcopy(point.data.get("uv_config") or {}),
                up_distribution_config=copy.deepcopy(point.data.get("up_distribution_config") or {}),
                hak_config=copy.deepcopy(point.data.get("hak_config") or {}),
                zaehler_config=copy.deepcopy(point.data.get("zaehler_config") or {}),
            )
            ap_nodes.append(node)

        cable_edges: list[CableEdge] = []
        for cable_id, cable in cables.items():
            pts = cable.geom.get("elec_cables") or []
            length_px = 0.0
            if isinstance(pts, list) and len(pts) >= 2:
                for i in range(len(pts) - 1):
                    dx = float(pts[i + 1][0]) - float(pts[i][0])
                    dy = float(pts[i + 1][1]) - float(pts[i][1])
                    length_px += math.hypot(dx, dy)
            mm_per_px = 1.0
            fp = self._document.floorplans.get(cable.floor_plan_id or "")
            if fp is not None and float(fp.mm_per_px) > 0:
                mm_per_px = float(fp.mm_per_px)
            length_m = (length_px * mm_per_px) / 1000.0

            edge = CableEdge(
                cable_id=cable_id,
                name=cable.name or cable_id,
                cable_type=str(cable.data.get("type", "") or ""),
                length_m=length_m,
                color=str(cable.color or "#ff9800"),
                stroke_width_px=float(cable.geom.get("elec_cable_stroke_width", 2.0) or 2.0),
                start_ap_id=str(cable.start_ap or cable.geom.get("cable_start_ap") or ""),
                end_ap_id=str(cable.end_ap or cable.geom.get("cable_end_ap") or ""),
                visible=bool(cable.visible),
                label_visible=bool(cable.label_visible),
                type_label_visible=bool(cable.geom.get("elec_cable_type_label_visible", False)),
                label_size=float(cable.label_size or 12.0),
                comment=str(cable.data.get("comment", "") or ""),
            )
            cable_edges.append(edge)

        return ap_nodes, cable_edges, room_map

    def _resolve_schema_room_center(self, room_id: str) -> tuple[list[float], str] | None:
        room = self._document.elements["elec_rooms"].get(room_id)
        if room is None:
            return None
        polygon = room.geom.get("elec_rooms") or room.geom.get("elec_room_polygons") or []
        if not isinstance(polygon, list) or len(polygon) < 3:
            return None

        points: list[list[float]] = []
        for entry in polygon:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                continue
            try:
                points.append([float(entry[0]), float(entry[1])])
            except (TypeError, ValueError):
                continue
        if len(points) < 3:
            return None

        area_twice = 0.0
        cx = 0.0
        cy = 0.0
        for idx, point in enumerate(points):
            nxt = points[(idx + 1) % len(points)]
            cross = point[0] * nxt[1] - nxt[0] * point[1]
            area_twice += cross
            cx += (point[0] + nxt[0]) * cross
            cy += (point[1] + nxt[1]) * cross

        if abs(area_twice) > 1e-9:
            center = [cx / (3.0 * area_twice), cy / (3.0 * area_twice)]
        else:
            center = [
                sum(point[0] for point in points) / len(points),
                sum(point[1] for point in points) / len(points),
            ]
        if not self._poly_contains(center, points):
            center = [
                sum(point[0] for point in points) / len(points),
                sum(point[1] for point in points) / len(points),
            ]
        return center, str(room.floor_plan_id or "")

    def _resolve_schema_cable_floorplan(self, start_ap_id: str, end_ap_id: str) -> str:
        for point_id in (start_ap_id, end_ap_id):
            if not point_id:
                continue
            point = self._document.elements["elec_points"].get(point_id)
            if point is not None and point.floor_plan_id:
                return str(point.floor_plan_id)
        return self._active_floorplan_id()

    def _point_position(self, point_id: str) -> list[float] | None:
        point = self._document.elements["elec_points"].get(point_id)
        if point is None:
            return None
        pos = point.geom.get("elec_points")
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return None
        try:
            return [float(pos[0]), float(pos[1])]
        except (TypeError, ValueError):
            return None

    def _rebuild_schema_cable_geometry(self, cable: ElecCable, start_ap_id: str, end_ap_id: str) -> None:
        existing = cable.geom.get("elec_cables") or []
        existing_points: list[list[float]] = []
        if isinstance(existing, list):
            for entry in existing:
                if isinstance(entry, (list, tuple)) and len(entry) == 2:
                    try:
                        existing_points.append([float(entry[0]), float(entry[1])])
                    except (TypeError, ValueError):
                        pass

        start_pos = self._point_position(start_ap_id) if start_ap_id else None
        end_pos = self._point_position(end_ap_id) if end_ap_id else None

        if start_pos and end_pos:
            cable.geom["elec_cables"] = [start_pos, end_pos]
            return
        if start_pos:
            fallback = existing_points[-1] if existing_points else [start_pos[0] + 120.0, start_pos[1] + 40.0]
            cable.geom["elec_cables"] = [start_pos, fallback]
            return
        if end_pos:
            fallback = existing_points[0] if existing_points else [end_pos[0] - 120.0, end_pos[1] - 40.0]
            cable.geom["elec_cables"] = [fallback, end_pos]
            return
        if existing_points:
            cable.geom["elec_cables"] = existing_points
            return
        cable.geom["elec_cables"] = []

    def _suggest_schema_point_position(self, fp_id: str) -> list[float]:
        xs: list[float] = []
        ys: list[float] = []
        for point in self._document.elements["elec_points"].values():
            if (point.floor_plan_id or "") != fp_id:
                continue
            pos = point.geom.get("elec_points")
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                continue
            try:
                xs.append(float(pos[0]))
                ys.append(float(pos[1]))
            except (TypeError, ValueError):
                continue
        if not xs:
            return [120.0, 120.0]
        return [max(xs) + 60.0, max(ys) + 30.0]

    def _on_schema_add_ap(self, payload: dict) -> None:
        fp_id = self._active_floorplan_id()
        room_id = str(payload.get("room_id") or "").strip()
        room_target = self._resolve_schema_room_center(room_id) if room_id else None
        if room_target is not None:
            center, room_fp_id = room_target
            if room_fp_id:
                fp_id = room_fp_id
            position = center
        else:
            position = self._suggest_schema_point_position(fp_id)

        if not fp_id:
            fp_id = self._require_floorplan()
        if not fp_id:
            return

        from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415

        self._push_undo()
        point_id = self._document.new_id(ElecPoint)
        name = (payload.get("name") or "").strip() or point_id
        color = str(payload.get("color") or "#4fc3f7")
        symbol = str(payload.get("symbol") or "Steckdose")
        ap_type = str(payload.get("ap_type") or "standard")
        icon_path = str(BUILTIN_SYMBOLS.get(symbol, "") or "")

        point = ElecPoint.create(
            point_id,
            floor_plan_id=fp_id,
            name=name,
            color=color,
            width=30.0,
            height=30.0,
            icon_path=icon_path,
            builtin_symbol=symbol,
            visible=True,
            label_visible=True,
            label_size=12.0,
            position="Wand",
            height_from_floor=30.0,
            smarthome_device="",
            smarthome_device_color="",
            note="",
            ap_type=ap_type,
            uv_config={},
            up_distribution_config={},
            hak_config={},
            zaehler_config={},
        )
        point.geom["elec_points"] = [float(position[0]), float(position[1])]
        point.geom["elec_point_size_px"] = [30.0, 30.0]
        point.geom["elec_visible"] = True

        self._document.add(point)
        self.canvas.register_element(point_id, True)
        self.canvas.set_elec_point_icon(point_id, icon_path)
        self.canvas.set_color(point_id, color)

        self._emit_structure_changed()
        self.navigator.select(point_id)
        self.properties.show_element(point_id)
        self.canvas.set_selected_item(point_id)
        self.canvas.update()
        self._mark_dirty()

    def _on_schema_add_cable(self, payload: dict) -> None:
        start_ap_id = str(payload.get("start_ap_id") or "").strip()
        end_ap_id = str(payload.get("end_ap_id") or "").strip()
        fp_id = self._resolve_schema_cable_floorplan(start_ap_id, end_ap_id)
        if not fp_id:
            fp_id = self._require_floorplan()
        if not fp_id:
            return

        self._push_undo()
        cable_id = self._document.new_id(ElecCable)
        name = (payload.get("name") or "").strip() or cable_id
        cable_type = (payload.get("type") or "").strip() or "5x1,5"
        color = str(payload.get("color") or "#ff9800")
        try:
            stroke_width = float(payload.get("stroke_width", 2.0))
        except (TypeError, ValueError):
            stroke_width = 2.0

        cable = ElecCable.create(
            cable_id,
            floor_plan_id=fp_id,
            name=name,
            color=color,
            visible=True,
            label_visible=True,
            label_size=12.0,
            type=cable_type,
            comment="",
            start_ap=start_ap_id,
            end_ap=end_ap_id,
        )
        cable.geom["elec_cable_stroke_width"] = stroke_width
        cable.geom["elec_cable_type_text"] = cable_type
        cable.geom["elec_cable_type_label_visible"] = False
        cable.geom["cable_start_ap"] = start_ap_id
        cable.geom["cable_end_ap"] = end_ap_id
        cable.geom["elec_visible"] = True
        self._rebuild_schema_cable_geometry(cable, start_ap_id, end_ap_id)

        self._document.add(cable)
        self.canvas.register_element(cable_id, True)
        self.canvas.set_color(cable_id, color)
        self.canvas.set_elec_cable_stroke_width(cable_id, stroke_width)
        self.canvas.set_elec_cable_type_text(cable_id, cable_type)

        self._emit_structure_changed()
        self.navigator.select(cable_id)
        self.properties.show_element(cable_id)
        self.canvas.set_selected_item(cable_id)
        self.canvas.update()
        self._mark_dirty()

        if self._elec_schema_window is not None and (not start_ap_id or not end_ap_id):
            self._elec_schema_window.start_cable_pick_mode(cable_id)

    def _on_schema_ap_position_changed(self, point_id: str, x: float, y: float) -> None:
        self._elec_schema_ap_positions[point_id] = [float(x), float(y)]
        self._mark_dirty()

    def _on_schema_ap_positions_changed(self, positions: dict) -> None:
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
            self._mark_dirty()

    def _on_schema_edit_ap(self, point_id: str, payload: dict) -> None:
        point = self._document.elements["elec_points"].get(point_id)
        if point is None:
            return
        from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415

        self._push_undo()
        point.name = str(payload.get("name") or point.name or point_id)
        symbol = str(payload.get("symbol") or point.builtin_symbol or "")
        point.builtin_symbol = symbol
        icon_path = str(payload.get("icon_path") or "").strip()
        if not icon_path:
            icon_path = str(BUILTIN_SYMBOLS.get(symbol, "") or "")
        point.icon_path = icon_path
        point.color = str(payload.get("color") or point.color or "#4fc3f7")
        try:
            point.width = float(payload.get("width", point.width or 30.0))
            point.height = float(payload.get("height", point.height or 30.0))
        except (TypeError, ValueError):
            pass
        point.visible = bool(payload.get("visible", point.visible))
        point.label_visible = bool(payload.get("label_visible", point.label_visible))
        try:
            point.label_size = float(payload.get("label_size", point.label_size or 12.0))
        except (TypeError, ValueError):
            pass
        point.ap_type = str(payload.get("ap_type") or point.ap_type or "standard")
        point.position = str(payload.get("position") or point.position or "Wand")
        try:
            point.height_from_floor = float(payload.get("height_from_floor", point.height_from_floor or 0.0))
        except (TypeError, ValueError):
            pass
        point.smarthome_device = str(payload.get("smarthome_device") or point.smarthome_device or "")
        point.smarthome_device_color = str(payload.get("smarthome_device_color") or point.smarthome_device_color or "")
        point.note = str(payload.get("note") or point.note or "")

        point.data["uv_config"] = copy.deepcopy(payload.get("uv_config") or {})
        point.data["up_distribution_config"] = copy.deepcopy(payload.get("up_distribution_config") or {})
        point.data["hak_config"] = copy.deepcopy(payload.get("hak_config") or {})
        point.data["zaehler_config"] = copy.deepcopy(payload.get("zaehler_config") or {})

        self.canvas.set_element_visible(point_id, bool(point.visible))
        self.canvas.set_elec_point_icon(point_id, icon_path)
        self.canvas.set_color(point_id, str(point.color))
        self.canvas.update_elec_point_size(point_id, float(point.width), float(point.height))
        point.geom["elec_point_position"] = str(point.position)
        point.geom["elec_point_height"] = float(point.height_from_floor)
        point.geom["elec_point_notes"] = str(point.note)
        point.geom["elec_point_smarthome_device"] = str(point.smarthome_device)
        point.geom["elec_point_smarthome_device_color"] = str(point.smarthome_device_color)
        point.geom["elec_visible"] = bool(point.visible)

        self._document.element_changed.emit(point_id)
        self.properties.refresh_element(point_id)
        self.canvas.update()
        self._mark_dirty()

    def _on_schema_edit_cable(self, cable_id: str, payload: dict) -> None:
        cable = self._document.elements["elec_cables"].get(cable_id)
        if cable is None:
            return

        self._push_undo()
        cable.name = str(payload.get("name") or cable.name or cable_id)
        cable.cable_type = str(payload.get("type") or cable.cable_type or "")
        cable.color = str(payload.get("color") or cable.color or "#ff9800")
        cable.visible = bool(payload.get("visible", cable.visible))
        cable.label_visible = bool(payload.get("label_visible", cable.label_visible))
        try:
            cable.label_size = float(payload.get("label_size", cable.label_size or 12.0))
        except (TypeError, ValueError):
            pass
        cable.comment = str(payload.get("comment") or cable.comment or "")
        try:
            stroke_width = float(payload.get("stroke_width", cable.geom.get("elec_cable_stroke_width", 2.0) or 2.0))
        except (TypeError, ValueError):
            stroke_width = 2.0

        start_ap = str(payload.get("start_ap_id") or "").strip()
        end_ap = str(payload.get("end_ap_id") or "").strip()
        cable.start_ap = start_ap
        cable.end_ap = end_ap
        cable.geom["cable_start_ap"] = start_ap
        cable.geom["cable_end_ap"] = end_ap
        cable.geom["elec_cable_stroke_width"] = stroke_width
        cable.geom["elec_cable_type_text"] = str(cable.cable_type)
        cable.geom["elec_cable_type_label_visible"] = bool(payload.get("type_label_visible", False))
        cable.geom["elec_cable_notes"] = str(cable.comment)
        cable.geom["elec_visible"] = bool(cable.visible)
        self._rebuild_schema_cable_geometry(cable, start_ap, end_ap)

        fp_id = self._resolve_schema_cable_floorplan(start_ap, end_ap)
        if fp_id:
            cable.floor_plan_id = fp_id

        self.canvas.set_element_visible(cable_id, bool(cable.visible))
        self.canvas.set_color(cable_id, str(cable.color))
        self.canvas.set_elec_cable_stroke_width(cable_id, stroke_width)
        self.canvas.set_elec_cable_type_text(cable_id, str(cable.cable_type))
        self.canvas.set_elec_cable_type_label_visible(
            cable_id,
            bool(cable.geom.get("elec_cable_type_label_visible", False)),
        )

        self._document.element_changed.emit(cable_id)
        self.properties.refresh_element(cable_id)
        self.canvas.update()
        self._mark_dirty()

    def _on_schema_duplicate_selection(self, ap_ids: list[str], cable_ids: list[str]) -> None:
        selected_ap_ids = [pid for pid in ap_ids if pid in self._document.elements["elec_points"]]
        selected_cable_ids = [cid for cid in cable_ids if cid in self._document.elements["elec_cables"]]
        if not selected_ap_ids and not selected_cable_ids:
            return

        self._push_undo()

        id_map: dict[str, str] = {}
        new_ids: list[str] = []

        for source_ap_id in selected_ap_ids:
            new_ap_id = self._duplicate_element(source_ap_id, record_undo=False)
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
            new_ids.append(new_ap_id)

        for source_cable_id in selected_cable_ids:
            new_cable_id = self._duplicate_element(source_cable_id, record_undo=False)
            if not new_cable_id:
                continue
            new_cable = self._document.elements["elec_cables"].get(new_cable_id)
            source_cable = self._document.elements["elec_cables"].get(source_cable_id)
            if new_cable is None or source_cable is None:
                continue

            source_start = str(source_cable.start_ap or "").strip()
            source_end = str(source_cable.end_ap or "").strip()
            new_start = id_map.get(source_start, "")
            new_end = id_map.get(source_end, "")
            if new_start or new_end:
                new_cable.start_ap = new_start
                new_cable.end_ap = new_end
                new_cable.geom["cable_start_ap"] = new_start
                new_cable.geom["cable_end_ap"] = new_end
                self._rebuild_schema_cable_geometry(new_cable, new_start, new_end)
                self._document.element_changed.emit(new_cable_id)
            new_ids.append(new_cable_id)

        if new_ids:
            self.navigator.select(new_ids[-1])
            self.properties.show_element(new_ids[-1])
            self.canvas.set_selected_item(new_ids[-1])
            self.canvas.update()
            self._mark_dirty()

    def _open_elec_schema_window(self) -> None:
        if self._elec_schema_window is None:
            self._elec_schema_window = ElecSchemaWindow(self)
            self._elec_schema_window.add_ap_requested.connect(self._on_schema_add_ap)
            self._elec_schema_window.add_cable_requested.connect(self._on_schema_add_cable)
            self._elec_schema_window.delete_ap_requested.connect(self._delete_element)
            self._elec_schema_window.delete_cable_requested.connect(self._delete_element)
            self._elec_schema_window.ap_position_changed.connect(self._on_schema_ap_position_changed)
            self._elec_schema_window.ap_positions_changed.connect(self._on_schema_ap_positions_changed)
            self._elec_schema_window.edit_ap_requested.connect(self._on_schema_edit_ap)
            self._elec_schema_window.edit_cable_requested.connect(self._on_schema_edit_cable)
            self._elec_schema_window.duplicate_selection_requested.connect(
                self._on_schema_duplicate_selection
            )
        self._refresh_schema_windows()
        self._elec_schema_window.show()
        self._elec_schema_window.raise_()
        self._elec_schema_window.activateWindow()

    def _open_schaltplan_window(self) -> None:
        if self._schaltplan_window is None:
            self._schaltplan_window = SchaltplanWindow(self)
        self._refresh_schema_windows()
        self._schaltplan_window.show()
        self._schaltplan_window.raise_()
        self._schaltplan_window.activateWindow()

    def _refresh_schema_windows(self) -> None:
        if self._elec_schema_window is None and self._schaltplan_window is None:
            return
        ap_nodes, cable_edges, room_map = self._build_schema_data()
        if self._elec_schema_window is not None:
            # Keep only manual positions of currently existing APs.
            valid = {node.point_id for node in ap_nodes}
            self._elec_schema_ap_positions = {
                pid: pos for pid, pos in self._elec_schema_ap_positions.items() if pid in valid
            }
            self._elec_schema_window.set_data(
                ap_nodes,
                cable_edges,
                manual_positions=self._elec_schema_ap_positions,
                room_choices=self._collect_room_choices(),
            )
        if self._schaltplan_window is not None:
            self._schaltplan_window.set_data(
                {node.point_id: node for node in ap_nodes},
                {edge.cable_id: edge for edge in cable_edges},
                room_map,
            )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _collect_project_dict(self) -> dict:
        """Erstellt das legacy-kompatible canvas+params-Dict aus dem Dokument.

        Synchronisiert den Canvas zuerst, damit auch Geometrieänderungen
        (Polygon-Editierung etc.) im Ergebnis enthalten sind.
        """
        self._sync_canvas_to_document()
        return self._document.to_dict()

    def _export_svg(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Als SVG exportieren", self._default_svg_export_path(), "SVG (*.svg)"
        )
        if not path:
            return
        self._remember_svg_export_path(Path(path))
        self._write_plan_svg(path)
        self.log.success(f"SVG exportiert: {path}")
        self.statusBar().showMessage(f"SVG exportiert: {path}", 4000)

    def _write_plan_svg(self, path: str, source_rect=None) -> None:
        """Rendert den aktuellen Plan als PNG-in-SVG."""
        import base64  # noqa: PLC0415

        from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: PLC0415

        img = self.canvas.render_for_export(source_rect, output_w=2480, output_h=1754)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        png_b64 = base64.b64encode(bytes(ba)).decode("ascii")
        w = img.width()
        h = img.height()
        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
            f' width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
            f'  <image href="data:image/png;base64,{png_b64}"'
            f' x="0" y="0" width="{w}" height="{h}"/>',
            "</svg>",
        ]
        import pathlib  # noqa: PLC0415
        pathlib.Path(path).write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _default_pdf_element_visibility() -> dict[str, bool]:
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
        if ptype == "heating_circuit":
            return ["hk_lengths", "hk_hydraulics"]
        if ptype == "elektro":
            return [
                "el_kabel",
                "el_ap_types",
                "el_ap_connections",
                "el_rooms",
                "el_ap_infos",
                "el_uv",
                "el_up_distribution",
                "el_bom",
                "el_uv_busbars",
                "schaltplan_uv",
                "schaltplan_stromkreise",
                "schaltplan_hierarchie",
            ]
        if ptype == "elektro_room":
            return ["el_ap_infos", "el_kabel"]
        return []

    def _default_pdf_export_pages(self) -> list[dict]:
        return [
            {
                "id": "overview-all",
                "type": "plan",
                "title": "Gesamtübersicht – Alle Elemente",
                "enabled": True,
                "show_background": True,
                "show_heating": True,
                "show_elektro": True,
                "element_visibility": self._default_pdf_element_visibility(),
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
                "title": "Hydraulische Übersicht",
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

    def _hrouting_program_version(self) -> str:
        try:
            from main import VERSION  # noqa: PLC0415

            return str(VERSION)
        except Exception:
            return ""

    def _default_pdf_export_meta(self, pages: list[dict] | None = None) -> dict[str, str]:
        use_pages = pages if pages is not None else self._normalize_pdf_export_pages(self._pdf_export_pages)
        enabled = [p for p in use_pages if p.get("enabled", True)]
        project_name = self._project_path.stem if self._project_path else ""
        return {
            "project": str(project_name),
            "author": "",
            "date": QDateTime.currentDateTime().toString("dd.MM.yyyy"),
            "planning_status": "entwurf",
            "page_count": str(len(enabled) + 1),
            "hrouting_version": self._hrouting_program_version(),
            "notes": "",
        }

    def _normalize_pdf_export_meta(self, meta: dict | None, pages: list[dict] | None = None) -> dict[str, str]:
        defaults = self._default_pdf_export_meta(pages)
        src = meta if isinstance(meta, dict) else {}
        out = {
            "project": str(src.get("project", defaults["project"])).strip(),
            "author": str(src.get("author", defaults["author"])).strip(),
            "date": str(src.get("date", defaults["date"])).strip() or defaults["date"],
            "planning_status": str(src.get("planning_status", defaults["planning_status"])).strip().lower() or "entwurf",
            "page_count": str(src.get("page_count", defaults["page_count"])).strip() or defaults["page_count"],
            "hrouting_version": str(src.get("hrouting_version", defaults["hrouting_version"])).strip() or defaults["hrouting_version"],
            "notes": str(src.get("notes", defaults["notes"])).strip(),
        }
        if out["planning_status"] not in {"entwurf", "review", "final"}:
            out["planning_status"] = "entwurf"
        return out

    def _normalize_pdf_export_pages(self, pages: list[dict] | None) -> list[dict]:
        if not pages:
            return self._default_pdf_export_pages()
        normalized: list[dict] = []
        for index, src in enumerate(pages):
            if not isinstance(src, dict):
                continue
            ptype = str(src.get("type", "plan")).strip().lower()
            if ptype not in (
                "plan",
                "heating",
                "heating_circuit",
                "lengths",
                "hydraulics",
                "elektro",
                "elektro_room",
            ):
                continue
            page = {
                "id": str(src.get("id") or f"page-{index + 1}"),
                "type": ptype,
                "title": str(src.get("title") or "Seite"),
                "enabled": bool(src.get("enabled", True)),
            }
            if ptype in ("plan", "heating", "heating_circuit", "elektro", "elektro_room"):
                page["show_background"] = bool(src.get("show_background", True))
                page["show_heating"] = bool(src.get("show_heating", True))
                page["show_elektro"] = bool(src.get("show_elektro", True))
                vis = self._default_pdf_element_visibility()
                vis_src = src.get("element_visibility")
                if isinstance(vis_src, dict):
                    for key in vis:
                        vis[key] = bool(vis_src.get(key, vis[key]))
                if ptype == "elektro_room":
                    vis["hk"] = False
                    vis["hkv"] = False
                    vis["hkv_line"] = False
                    vis["ap"] = True
                    vis["room"] = True
                    vis["kv"] = True
                if ptype == "heating_circuit":
                    vis["hk"] = True
                    vis["hkv"] = True
                    vis["hkv_line"] = True
                    vis["ap"] = False
                    vis["room"] = False
                    vis["kv"] = False
                page["element_visibility"] = vis
                page["table_sections"] = list(src.get("table_sections") or self._default_pdf_table_sections(ptype))
                if ptype == "elektro_room":
                    room_ids_src = src.get("room_ids")
                    if isinstance(room_ids_src, list):
                        seen: set[str] = set()
                        room_ids: list[str] = []
                        for room_id in room_ids_src:
                            key = str(room_id or "").strip()
                            if key and key not in seen:
                                seen.add(key)
                                room_ids.append(key)
                        page["room_ids"] = room_ids
                    else:
                        page["room_ids"] = []
                if ptype == "heating_circuit":
                    circuit_ids_src = src.get("circuit_ids")
                    if isinstance(circuit_ids_src, list):
                        seen = set()
                        circuit_ids: list[str] = []
                        for circuit_id in circuit_ids_src:
                            key = str(circuit_id or "").strip()
                            if key and key not in seen:
                                seen.add(key)
                                circuit_ids.append(key)
                        page["circuit_ids"] = circuit_ids
                    else:
                        page["circuit_ids"] = []
                page["floor_plan_id"] = src.get("floor_plan_id") or None
                rect = src.get("source_rect")
                if isinstance(rect, (list, tuple)) and len(rect) == 4:
                    try:
                        x, y, w, h = [float(v) for v in rect]
                        page["source_rect"] = [x, y, w, h] if w > 0 and h > 0 else None
                    except (TypeError, ValueError):
                        page["source_rect"] = None
                else:
                    page["source_rect"] = None
            normalized.append(page)
        return normalized or self._default_pdf_export_pages()

    def _current_floor_plans_for_export_dialog(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for fid in self.canvas._floor_plan_order:
            floor = self._document.floorplans.get(fid)
            out.append((fid, (floor.name if floor else "") or fid))
        return out

    def _current_elec_rooms_for_export_dialog(self) -> list[tuple[str, str]]:
        return self._collect_room_choices()

    def _current_heating_circuits_for_export_dialog(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        for circuit_id, circuit in self._document.elements["circuits"].items():
            choices.append((circuit_id, str(circuit.name or circuit_id)))
        return sorted(choices, key=lambda entry: entry[1].lower())

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
        custom = self._page_source_rect(page)
        if custom is not None:
            return custom
        frame = self.canvas.get_export_frame()
        if frame is not None:
            nr = QRectF(frame).normalized()
            if nr.width() > 0 and nr.height() > 0:
                return nr
        return self.canvas.get_default_source_rect()

    def _open_pdf_export_config_dialog(self) -> tuple[list[dict], dict[str, str]] | None:
        pages = self._normalize_pdf_export_pages(self._pdf_export_pages)
        dialog = PdfExportConfigDialog(
            pages=pages,
            floor_plans=self._current_floor_plans_for_export_dialog(),
            elec_rooms=self._current_elec_rooms_for_export_dialog(),
            heating_circuits=self._current_heating_circuits_for_export_dialog(),
            svg_size=self.canvas._svg_size,
            export_meta=self._normalize_pdf_export_meta(self._pdf_export_meta, pages),
            hrouting_version=self._hrouting_program_version(),
            canvas=self.canvas,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return None
        out_pages = self._normalize_pdf_export_pages(dialog.get_pages())
        out_meta = self._normalize_pdf_export_meta(dialog.get_export_meta(), out_pages)
        return out_pages, out_meta

    def _save_all_visibility(self) -> dict:
        return {
            "circuit_visible": dict(self.canvas._circuit_visible),
            "hkv_visible": dict(self.canvas._hkv_visible),
            "hkv_line_visible": dict(self.canvas._hkv_line_visible),
            "elec_visible": dict(self.canvas._elec_visible),
            "elec_room_visible": dict(self.canvas._elec_room_visible),
            "text_visible": dict(self.canvas._text_visible),
            "label_visible": dict(self.canvas._label_visible),
            "floor_plan_visible": {fid: layer.visible for fid, layer in self.canvas._floor_plans.items()},
        }

    def _restore_all_visibility(self, saved: dict) -> None:
        self.canvas._circuit_visible = dict(saved.get("circuit_visible", {}))
        self.canvas._hkv_visible = dict(saved.get("hkv_visible", {}))
        self.canvas._hkv_line_visible = dict(saved.get("hkv_line_visible", {}))
        self.canvas._elec_visible = dict(saved.get("elec_visible", {}))
        self.canvas._elec_room_visible = dict(saved.get("elec_room_visible", {}))
        self.canvas._text_visible = dict(saved.get("text_visible", {}))
        self.canvas._label_visible = dict(saved.get("label_visible", {}))
        for fid, vis in saved.get("floor_plan_visible", {}).items():
            layer = self.canvas._floor_plans.get(fid)
            if layer is not None:
                layer.visible = bool(vis)

    def _apply_page_visibility(self, page: dict) -> None:
        vis = page.get("element_visibility") or self._default_pdf_element_visibility()
        selected_floor_plan_id = str(page.get("floor_plan_id") or "").strip() or None

        def _matches_floor_plan_id(value: object) -> bool:
            if selected_floor_plan_id is None:
                return True
            return str(value or "").strip() == selected_floor_plan_id

        for fid in self.canvas._floor_plan_order:
            layer = self.canvas._floor_plans.get(fid)
            if layer is None:
                continue
            layer.visible = bool(vis.get("background", True)) and _matches_floor_plan_id(fid)

        show_hk = bool(vis.get("hk", True))
        for cid in list(self.canvas._polygons) + list(self.canvas._manual_routes) + list(self.canvas._supply_lines):
            circuit = self._document.elements["circuits"].get(cid)
            self.canvas._circuit_visible[cid] = show_hk and _matches_floor_plan_id(
                getattr(circuit, "floor_plan_id", "") if circuit is not None else ""
            )

        show_hkv = bool(vis.get("hkv", True))
        for hid in self.canvas._hkv_points:
            hkv = self._document.elements["hkv_points"].get(hid)
            self.canvas._hkv_visible[hid] = show_hkv and _matches_floor_plan_id(
                getattr(hkv, "floor_plan_id", "") if hkv is not None else ""
            )

        show_hkv_line = bool(vis.get("hkv_line", True))
        for lid in self.canvas._hkv_lines:
            line = self._document.elements["hkv_lines"].get(lid)
            line_floor_plan_id = str(getattr(line, "floor_plan_id", "") or "") if line is not None else ""
            if not line_floor_plan_id and line is not None:
                start_hkv = self._document.elements["hkv_points"].get(str(line.start_hkv or ""))
                end_hkv = self._document.elements["hkv_points"].get(str(line.end_hkv or ""))
                line_floor_plan_id = str(
                    getattr(start_hkv, "floor_plan_id", "")
                    or getattr(end_hkv, "floor_plan_id", "")
                    or ""
                )
            self.canvas._hkv_line_visible[lid] = show_hkv_line and _matches_floor_plan_id(line_floor_plan_id)

        show_ap = bool(vis.get("ap", True))
        for pid in self.canvas._elec_points:
            point = self._document.elements["elec_points"].get(pid)
            self.canvas._elec_visible[pid] = show_ap and _matches_floor_plan_id(
                getattr(point, "floor_plan_id", "") if point is not None else ""
            )

        show_room = bool(vis.get("room", True))
        for rid in self.canvas._elec_room_polygons:
            room = self._document.elements["elec_rooms"].get(rid)
            self.canvas._elec_room_visible[rid] = show_room and _matches_floor_plan_id(
                getattr(room, "floor_plan_id", "") if room is not None else ""
            )

        show_kv = bool(vis.get("kv", True))
        for kid in self.canvas._elec_cables:
            cable = self._document.elements["elec_cables"].get(kid)
            cable_floor_plan_id = str(getattr(cable, "floor_plan_id", "") or "") if cable is not None else ""
            if not cable_floor_plan_id and cable is not None:
                cable_floor_plan_id = self._resolve_schema_cable_floorplan(
                    str(cable.start_ap or ""),
                    str(cable.end_ap or ""),
                )
            self.canvas._elec_visible[kid] = show_kv and _matches_floor_plan_id(cable_floor_plan_id)

        show_text = bool(vis.get("text", True))
        for tid in self.canvas._text_annotations:
            text = self._document.elements["text_annotations"].get(tid)
            self.canvas._text_visible[tid] = show_text and _matches_floor_plan_id(
                getattr(text, "floor_plan_id", "") if text is not None else ""
            )

    def _collect_pdf_electro_rows(self) -> tuple[list[list[str]], list[list[str]]]:
        from model.computed import cable_length_details  # noqa: PLC0415

        ap_rows: list[list[str]] = []
        for pid, point in self._document.elements["elec_points"].items():
            ap_rows.append([
                str(point.name or pid),
                str(point.builtin_symbol or ""),
                str(point.position or ""),
                f"{float(point.height_from_floor or 0.0):.1f} cm",
                str(point.note or ""),
            ])
        ap_rows.sort(key=lambda row: row[0].lower())

        cable_rows: list[list[str]] = []
        for cid, cable in self._document.elements["elec_cables"].items():
            start_id, end_id = self.canvas.get_cable_ap(cid)
            start_name = self._document.elements["elec_points"].get(start_id).name if start_id in self._document.elements["elec_points"] else (start_id or "")
            end_name = self._document.elements["elec_points"].get(end_id).name if end_id in self._document.elements["elec_points"] else (end_id or "")
            length_m = float(cable_length_details(self._document, cable)["length_m"])
            cable_rows.append([
                str(cable.name or cid),
                str(cable.cable_type or ""),
                str(start_name or ""),
                str(end_name or ""),
                f"{length_m:.2f} m",
            ])
        cable_rows.sort(key=lambda row: row[0].lower())
        return ap_rows, cable_rows

    def _collect_pdf_elektro_room_rows(
        self,
        room_ids: list[str],
    ) -> tuple[set[str], set[str], list[list[str]], list[list[str]]]:
        from model.computed import cable_length_details  # noqa: PLC0415

        selected_room_ids = {str(room_id).strip() for room_id in room_ids if str(room_id).strip()}
        if not selected_room_ids:
            return set(), set(), [], []

        selected_ap_ids: set[str] = set()
        for point_id, point in self._document.elements["elec_points"].items():
            room_id = self._resolve_existing_ap_room_id(point)
            if room_id in selected_room_ids:
                selected_ap_ids.add(point_id)

        selected_cable_ids: set[str] = set()
        cable_rows: list[list[str]] = []
        for cable_id, cable in self._document.elements["elec_cables"].items():
            start_id = str(cable.start_ap or cable.geom.get("cable_start_ap") or "")
            end_id = str(cable.end_ap or cable.geom.get("cable_end_ap") or "")
            if start_id not in selected_ap_ids and end_id not in selected_ap_ids:
                continue
            selected_cable_ids.add(cable_id)
            length_m = float(cable_length_details(self._document, cable)["length_m"])
            start_name = self._document.elements["elec_points"].get(start_id).name if start_id in self._document.elements["elec_points"] else (start_id or "")
            end_name = self._document.elements["elec_points"].get(end_id).name if end_id in self._document.elements["elec_points"] else (end_id or "")
            cable_rows.append([
                str(cable.name or cable_id),
                str(cable.cable_type or ""),
                str(start_name or ""),
                str(end_name or ""),
                f"{length_m:.2f} m",
            ])

        ap_rows: list[list[str]] = []
        for point_id, point in self._document.elements["elec_points"].items():
            if point_id not in selected_ap_ids:
                continue
            ap_rows.append([
                str(point.name or point_id),
                self._describe_ap_type(point),
                str(point.position or ""),
                f"{float(point.height_from_floor or 0.0):.1f} cm",
                str(point.note or ""),
            ])

        ap_rows.sort(key=lambda row: row[0].lower())
        cable_rows.sort(key=lambda row: row[0].lower())
        return selected_ap_ids, selected_cable_ids, ap_rows, cable_rows

    def _collect_pdf_heating_circuit_rows(
        self,
        circuit_ids: list[str],
    ) -> tuple[set[str], list[list[str]], list[list[str]]]:
        selected_circuit_ids = {str(circuit_id).strip() for circuit_id in circuit_ids if str(circuit_id).strip()}
        if not selected_circuit_ids:
            return set(), [], []

        hk_rows, _t_supply, _t_return = self._collect_length_overview_rows()
        rows_by_id = {str(row.get("id", "")): row for row in hk_rows}

        detail_rows: list[list[str]] = []
        metric_rows: list[list[str]] = []
        for circuit_id in sorted(selected_circuit_ids):
            circuit = self._document.elements["circuits"].get(circuit_id)
            if circuit is None:
                continue
            detail_rows.append(
                [
                    circuit_id,
                    str(circuit.name or circuit_id),
                    f"{float(circuit.data.get('room_temp', 20.0) or 20.0):.1f} °C",
                    str(circuit.data.get("floor_covering", "") or ""),
                    f"{float(circuit.data.get('diameter', 16.0) or 16.0):.1f} mm",
                    f"{float(circuit.data.get('spacing', 150.0) or 150.0):.0f} mm",
                    f"{float(circuit.data.get('wall_dist', 200.0) or 200.0):.0f} mm",
                ]
            )

            row = rows_by_id.get(circuit_id, {})
            metric_rows.append(
                [
                    circuit_id,
                    str(circuit.name or circuit_id),
                    f"{float(row.get('area_m2', 0.0)):.2f} m²",
                    f"{float(row.get('route_m', 0.0)):.2f} m",
                    f"{float(row.get('supply_m', 0.0)):.2f} m",
                    f"{float(row.get('total_m', 0.0)):.2f} m",
                    f"{float(row.get('power_w', 0.0)):.0f} W",
                    f"{float(row.get('pressure_drop_mbar', 0.0)):.0f} mbar",
                ]
            )

        detail_rows.sort(key=lambda row: row[1].lower())
        metric_rows.sort(key=lambda row: row[1].lower())
        return {row[0] for row in detail_rows}, detail_rows, metric_rows

    def _room_focus_source_rect(self, room_ids: list[str], fallback: QRectF) -> QRectF:
        selected_room_ids = {str(room_id).strip() for room_id in room_ids if str(room_id).strip()}
        if not selected_room_ids:
            return fallback

        min_x: float | None = None
        min_y: float | None = None
        max_x: float | None = None
        max_y: float | None = None

        for room_id in selected_room_ids:
            room = self._document.elements["elec_rooms"].get(room_id)
            if room is None:
                continue
            polygon = room.geom.get("elec_rooms") or room.geom.get("elec_room_polygons") or []
            if not isinstance(polygon, list):
                continue
            for entry in polygon:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    continue
                try:
                    x = float(entry[0])
                    y = float(entry[1])
                except (TypeError, ValueError):
                    continue
                min_x = x if min_x is None else min(min_x, x)
                min_y = y if min_y is None else min(min_y, y)
                max_x = x if max_x is None else max(max_x, x)
                max_y = y if max_y is None else max(max_y, y)

        if min_x is None or min_y is None or max_x is None or max_y is None:
            return fallback

        width = max_x - min_x
        height = max_y - min_y
        if width <= 1e-6 or height <= 1e-6:
            return fallback

        pad = max(40.0, min(width, height) * 0.15)
        rect = QRectF(min_x - pad, min_y - pad, width + 2 * pad, height + 2 * pad)
        return rect.normalized()

    def _heating_circuit_focus_source_rect(self, circuit_ids: list[str], fallback: QRectF) -> QRectF:
        selected_circuit_ids = {str(circuit_id).strip() for circuit_id in circuit_ids if str(circuit_id).strip()}
        if not selected_circuit_ids:
            return fallback

        min_x: float | None = None
        min_y: float | None = None
        max_x: float | None = None
        max_y: float | None = None

        def _collect_points(value) -> list[tuple[float, float]]:
            out: list[tuple[float, float]] = []
            if not isinstance(value, list):
                return out
            for entry in value:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    continue
                try:
                    x = float(entry[0])
                    y = float(entry[1])
                except (TypeError, ValueError):
                    continue
                out.append((x, y))
            return out

        for circuit_id in selected_circuit_ids:
            circuit = self._document.elements["circuits"].get(circuit_id)
            candidate_lists = [
                self.canvas._polygons.get(circuit_id, []),
                self.canvas._manual_routes.get(circuit_id, []),
                self.canvas._supply_lines.get(circuit_id, []),
            ]
            if circuit is not None:
                candidate_lists.extend(
                    [
                        circuit.geom.get("polygons", []),
                        circuit.geom.get("manual_routes", []),
                        circuit.geom.get("supply_lines", []),
                    ]
                )

            for points in candidate_lists:
                for x, y in _collect_points(points):
                    min_x = x if min_x is None else min(min_x, x)
                    min_y = y if min_y is None else min(min_y, y)
                    max_x = x if max_x is None else max(max_x, x)
                    max_y = y if max_y is None else max(max_y, y)

        if min_x is None or min_y is None or max_x is None or max_y is None:
            return fallback

        width = max_x - min_x
        height = max_y - min_y
        if width <= 1e-6 or height <= 1e-6:
            return fallback

        # Keep padding compact so selected circuits appear large in the screenshot.
        pad = max(20.0, min(width, height) * 0.08)
        rect = QRectF(min_x - pad, min_y - pad, width + 2 * pad, height + 2 * pad)
        return rect.normalized()

    @staticmethod
    def _is_uv_type(point: ElecPoint) -> bool:
        return str(point.ap_type or "standard").strip().lower() == "uv"

    @staticmethod
    def _is_up_distribution_type(point: ElecPoint) -> bool:
        return str(point.ap_type or "standard").strip().lower() == "up_distribution"

    def _describe_ap_type(self, point: ElecPoint) -> str:
        if self._is_uv_type(point):
            return "Unterverteilung (UV)"
        if self._is_up_distribution_type(point):
            return "Verteilung in Unterputzdose"
        symbol = str(point.builtin_symbol or "").strip()
        icon_path = str(point.icon_path or "").strip()
        if symbol and symbol != "(kein Symbol)":
            return symbol
        if icon_path:
            label = Path(icon_path).stem.replace("_", " ").replace("-", " ").strip()
            return label or "Eigenes Symbol"
        return "(kein Symbol)"

    def _collect_uv_rows(self, point_id_to_room_name: dict[str, str] | None = None) -> list[dict]:
        point_id_to_room_name = point_id_to_room_name or self._collect_point_id_to_room_name()
        rows: list[dict] = []
        for pid, point in self._document.elements["elec_points"].items():
            if not self._is_uv_type(point):
                continue
            uv_config = point.data.get("uv_config") or {}
            if not isinstance(uv_config, dict):
                uv_config = {}
            try:
                uv_rows = int(uv_config.get("rows", 0) or 0)
            except (TypeError, ValueError):
                uv_rows = 0
            try:
                uv_modules = int(uv_config.get("modules_per_row", 0) or 0)
            except (TypeError, ValueError):
                uv_modules = 0
            ap_name = str(point.name or pid)
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
                    for slot in slots
                    if isinstance(slot, dict)
                ],
                key=lambda slot: (slot["row"], slot["slot"]),
            )
            if not normalized_slots:
                rows.append(
                    {
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
                    }
                )
                continue
            for slot in normalized_slots:
                rows.append(
                    {
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
                    }
                )
        return rows

    def _collect_uv_data(self, point_id_to_room_name: dict[str, str] | None = None) -> list[dict]:
        point_id_to_room_name = point_id_to_room_name or self._collect_point_id_to_room_name()
        result: list[dict] = []
        for pid, point in self._document.elements["elec_points"].items():
            if not self._is_uv_type(point):
                continue
            uv_config = point.data.get("uv_config") or {}
            if not isinstance(uv_config, dict):
                uv_config = {}
            try:
                uv_rows = int(uv_config.get("rows", 0) or 0)
            except (TypeError, ValueError):
                uv_rows = 0
            try:
                uv_modules = int(uv_config.get("modules_per_row", 0) or 0)
            except (TypeError, ValueError):
                uv_modules = 0
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
                    for s in slots_raw
                    if isinstance(s, dict)
                ],
                key=lambda s: (s["row"], s["slot"]),
            )
            busbars_raw = uv_config.get("busbars", [])
            if not isinstance(busbars_raw, list):
                busbars_raw = []
            result.append(
                {
                    "ap_id": pid,
                    "ap_name": str(point.name or pid),
                    "room": point_id_to_room_name.get(pid, "(ohne Raum)"),
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
                        for b in busbars_raw
                        if isinstance(b, dict) and str(b.get("phase", "") or "").strip()
                    ],
                }
            )
        return result

    def _collect_up_distribution_rows(self, point_id_to_room_name: dict[str, str] | None = None) -> list[dict]:
        point_id_to_room_name = point_id_to_room_name or self._collect_point_id_to_room_name()
        cable_id_to_name = {
            cable_id: str(cable.name or cable_id)
            for cable_id, cable in self._document.elements["elec_cables"].items()
        }
        rows: list[dict] = []
        for pid, point in self._document.elements["elec_points"].items():
            if not self._is_up_distribution_type(point):
                continue
            config = point.data.get("up_distribution_config") or {}
            if not isinstance(config, dict):
                continue
            ap_name = str(point.name or pid)
            room_name = point_id_to_room_name.get(pid, "(ohne Raum)")
            incoming_id = str(config.get("incoming_cable_id", "") or "").strip()
            incoming_name = cable_id_to_name.get(incoming_id, incoming_id)
            outgoing_raw = config.get("outgoing_cable_ids", [])
            if not isinstance(outgoing_raw, list):
                outgoing_raw = []
            outgoing_ids = []
            for cable_id in outgoing_raw:
                text = str(cable_id or "").strip()
                if text and text not in outgoing_ids:
                    outgoing_ids.append(text)
            outgoing_names = [cable_id_to_name.get(cable_id, cable_id) for cable_id in outgoing_ids]
            mappings_raw = config.get("mappings", [])
            if not isinstance(mappings_raw, list):
                mappings_raw = []
            mappings = []
            for mapping in mappings_raw:
                if not isinstance(mapping, dict):
                    continue
                to_cable_id = str(mapping.get("to_cable_id", "") or "").strip()
                mappings.append(
                    {
                        "from_conductor": str(mapping.get("from_conductor", "") or "").strip(),
                        "to_cable_id": to_cable_id,
                        "to_cable_name": cable_id_to_name.get(to_cable_id, to_cable_id),
                        "to_conductor": str(mapping.get("to_conductor", "") or "").strip(),
                        "note": str(mapping.get("note", "") or "").strip(),
                    }
                )
            distribution_note = str(config.get("note", "") or "").strip()
            if not mappings:
                rows.append(
                    {
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
                    }
                )
                continue
            for mapping in mappings:
                rows.append(
                    {
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
                    }
                )
        return rows

    def _collect_ap_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for point in self._document.elements["elec_points"].values():
            counts[self._describe_ap_type(point)] += 1
        return dict(sorted(counts.items(), key=lambda kv: kv[0].lower()))

    @staticmethod
    def _build_ap_cable_map(kv_rows: list[dict]) -> dict[str, list[dict]]:
        ap_map: dict[str, list[dict]] = defaultdict(list)
        for row in kv_rows:
            for role, key, dev_key, color_key, note_key in [
                ("Start", "start_ap", "start_device", "start_device_color", "start_note"),
                ("Ende", "end_ap", "end_device", "end_device_color", "end_note"),
            ]:
                ap_name = row.get(key, "")
                if ap_name:
                    ap_map[ap_name].append(
                        {
                            "cable": row.get("name", ""),
                            "type": row.get("type", ""),
                            "length_m": float(row.get("length_m", 0.0) or 0.0),
                            "role": role,
                            "ap_device": row.get(dev_key, ""),
                            "ap_device_color": row.get(color_key, ""),
                            "ap_note": row.get(note_key, ""),
                            "cable_note": row.get("comment", ""),
                        }
                    )
        return dict(ap_map)

    def _build_room_ap_connection_map(self, kv_rows: list[dict]) -> list[dict]:
        point_id_to_room_name = self._collect_point_id_to_room_name()
        point_id_to_name = {
            pid: str(point.name or pid)
            for pid, point in self._document.elements["elec_points"].items()
        }
        point_id_to_device = {
            pid: str(point.smarthome_device or "")
            for pid, point in self._document.elements["elec_points"].items()
        }
        point_id_to_device_color = {
            pid: str(point.smarthome_device_color or "")
            for pid, point in self._document.elements["elec_points"].items()
        }
        point_id_to_note = {
            pid: str(point.note or "")
            for pid, point in self._document.elements["elec_points"].items()
        }
        cable_meta = {
            row.get("name", ""): {
                "type": row.get("type", ""),
                "length_m": float(row.get("length_m", 0.0) or 0.0),
                "comment": str(row.get("comment", "") or ""),
            }
            for row in kv_rows
        }

        rows: list[dict] = []
        for cable_id, cable in self._document.elements["elec_cables"].items():
            cable_name = str(cable.name or cable_id)
            cable_type = cable_meta.get(cable_name, {}).get("type", str(cable.cable_type or ""))
            cable_len = float(cable_meta.get(cable_name, {}).get("length_m", 0.0))
            cable_note = cable_meta.get(cable_name, {}).get("comment", str(cable.comment or ""))
            start_id = str(cable.start_ap or cable.geom.get("cable_start_ap") or "")
            end_id = str(cable.end_ap or cable.geom.get("cable_end_ap") or "")

            if start_id:
                rows.append(
                    {
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
                    }
                )
            if end_id:
                rows.append(
                    {
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
                    }
                )

        return sorted(
            rows,
            key=lambda row: (
                str(row.get("room", "")).lower(),
                str(row.get("ap", "")).lower(),
                str(row.get("cable", "")).lower(),
                str(row.get("role", "")).lower(),
            ),
        )

    @staticmethod
    def _safe_te_count(te_start: int, te_end: int) -> int:
        if te_end < te_start:
            te_start, te_end = te_end, te_start
        return max(0, (te_end - te_start + 1))

    def _collect_bom_rows(
        self,
        hk_rows: list[dict],
        kv_rows: list[dict],
        ap_info_rows: list[dict],
        uv_data: list[dict],
        hl_rows: list[dict],
    ) -> dict:
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
                "manufacturer": "",
                "article_number": "",
                "note": "",
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
                "manufacturer": "",
                "article_number": "",
                "note": "",
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
                "manufacturer": "",
                "article_number": "",
                "note": "",
            }
            for line_type, length_m in sorted(hkv_line_by_type.items(), key=lambda kv: kv[0].lower())
        ]

        uv_device_summary: dict[tuple[str, str, str, str], dict] = {}
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
                        "note": "",
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
                        "uv_names": set(),
                        "manufacturer": "",
                        "article_number": "",
                    }
                uv_busbar_summary[key]["quantity"] += te_count
                uv_busbar_summary[key]["uv_names"].add(uv_name)

        uv_device_bom_rows = sorted(
            uv_device_summary.values(),
            key=lambda row: (
                str(row.get("room", "")).lower(),
                str(row.get("uv", "")).lower(),
                str(row.get("description", "")).lower(),
            ),
        )
        uv_busbar_bom_rows = []
        for row in sorted(
            uv_busbar_summary.values(),
            key=lambda item: (
                str(item.get("phase", "")).lower(),
                int(item.get("te_start", 0) or 0),
                int(item.get("te_end", 0) or 0),
            ),
        ):
            enriched = dict(row)
            enriched["uv_names"] = ", ".join(sorted(row.get("uv_names", set())))
            uv_busbar_bom_rows.append(enriched)

        return {
            "cable_bom_rows": cable_bom_rows,
            "ap_bom_rows": ap_bom_rows,
            "hkv_line_bom_rows": hkv_line_bom_rows,
            "uv_device_bom_rows": uv_device_bom_rows,
            "uv_busbar_bom_rows": uv_busbar_bom_rows,
            "custom_bom_rows": [],
        }

    def _collect_export_data(self) -> dict:
        from model.computed import cable_length_details  # noqa: PLC0415

        hk_rows, t_supply, t_return = self._collect_length_overview_rows()

        hkv_sum: dict[str, dict] = defaultdict(lambda: {"volume_flow": 0.0, "power": 0.0})
        for row in hk_rows:
            dist = str(row.get("distributor", "") or "")
            if dist:
                hkv_sum[dist]["volume_flow"] += float(row.get("volume_flow_lmin", 0.0) or 0.0)
                hkv_sum[dist]["power"] += float(row.get("power_w", 0.0) or 0.0)

        kv_rows: list[dict] = []
        kv_sum: dict[str, float] = defaultdict(float)
        for cable_id, cable in self._document.elements["elec_cables"].items():
            length_m = float(cable_length_details(self._document, cable)["length_m"])

            start_id = str(cable.start_ap or cable.geom.get("cable_start_ap") or "")
            end_id = str(cable.end_ap or cable.geom.get("cable_end_ap") or "")
            start_point = self._document.elements["elec_points"].get(start_id)
            end_point = self._document.elements["elec_points"].get(end_id)

            start_height = float(start_point.height_from_floor if start_point else 0.0)
            end_height = float(end_point.height_from_floor if end_point else 0.0)

            row = {
                "name": str(cable.name or cable_id),
                "type": str(cable.cable_type or ""),
                "comment": str(cable.comment or ""),
                "length_m": length_m,
                "start_ap": str(start_point.name or start_id) if start_id else "",
                "end_ap": str(end_point.name or end_id) if end_id else "",
                "start_height_cm": start_height,
                "end_height_cm": end_height,
                "start_position": str(start_point.position or "") if start_point else "",
                "end_position": str(end_point.position or "") if end_point else "",
                "start_device": str(start_point.smarthome_device or "") if start_point else "",
                "start_device_color": str(start_point.smarthome_device_color or "") if start_point else "",
                "start_note": str(start_point.note or "") if start_point else "",
                "end_device": str(end_point.smarthome_device or "") if end_point else "",
                "end_device_color": str(end_point.smarthome_device_color or "") if end_point else "",
                "end_note": str(end_point.note or "") if end_point else "",
            }
            kv_rows.append(row)
            kv_sum[row["type"]] += length_m

        ap_cables = self._build_ap_cable_map(kv_rows)
        room_ap_connections = self._build_room_ap_connection_map(kv_rows)
        ap_type_counts = self._collect_ap_type_counts()
        point_id_to_room_name = self._collect_point_id_to_room_name()

        ap_info_rows: list[dict] = []
        for pid, point in self._document.elements["elec_points"].items():
            ap_info_rows.append(
                {
                    "name": str(point.name or pid),
                    "type": self._describe_ap_type(point),
                    "room": point_id_to_room_name.get(pid, "(ohne Raum)"),
                    "position": str(point.position or "").strip(),
                    "height_cm": float(point.height_from_floor or 0.0),
                    "device_color": str(point.smarthome_device_color or "").strip(),
                    "device": str(point.smarthome_device or "").strip(),
                    "note": str(point.note or "").strip(),
                }
            )
        ap_info_rows.sort(key=lambda row: (str(row.get("room", "")).lower(), str(row.get("name", "")).lower()))

        uv_rows = self._collect_uv_rows(point_id_to_room_name)
        uv_data = self._collect_uv_data(point_id_to_room_name)
        up_distribution_rows = self._collect_up_distribution_rows(point_id_to_room_name)

        hl_rows: list[dict] = []
        hl_sum: dict[str, float] = defaultdict(float)
        for line_id, line in self._document.elements["hkv_lines"].items():
            mm_per_px = max(float(self.canvas.get_mm_per_px()), 1e-9)
            fp = self._document.floorplans.get(line.floor_plan_id or "")
            if fp is not None and float(fp.mm_per_px) > 0:
                mm_per_px = float(fp.mm_per_px)
            length_m = self.canvas.get_hkv_line_length_px(line_id) * mm_per_px / 1000.0
            start_id = str(line.start_hkv or line.geom.get("hkv_line_start") or "")
            end_id = str(line.end_hkv or line.geom.get("hkv_line_end") or "")
            start_hkv = self._document.elements["hkv_points"].get(start_id)
            end_hkv = self._document.elements["hkv_points"].get(end_id)
            row = {
                "name": str(line.name or line_id),
                "type": str(line.data.get("type", "") or ""),
                "length_m": length_m,
                "start_hkv": str(start_hkv.name or start_id) if start_id else "",
                "end_hkv": str(end_hkv.name or end_id) if end_id else "",
            }
            hl_rows.append(row)
            hl_sum[row["type"]] += length_m

        bom_data = self._collect_bom_rows(
            hk_rows=hk_rows,
            kv_rows=kv_rows,
            ap_info_rows=ap_info_rows,
            uv_data=uv_data,
            hl_rows=hl_rows,
        )

        return {
            "t_supply": t_supply,
            "t_return": t_return,
            "hk_rows": hk_rows,
            "hkv_sum": hkv_sum,
            "kv_rows": kv_rows,
            "kv_sum": kv_sum,
            "ap_cables": ap_cables,
            "room_ap_connections": room_ap_connections,
            "ap_type_counts": ap_type_counts,
            "ap_info_rows": ap_info_rows,
            "uv_rows": uv_rows,
            "uv_data": uv_data,
            "up_distribution_rows": up_distribution_rows,
            "hl_rows": hl_rows,
            "hl_sum": hl_sum,
            **bom_data,
        }

    def _draw_pdf_title(self, painter, page_rect: QRectF, title: str) -> tuple[QRectF, QRectF]:
        from PySide6.QtGui import QFont  # noqa: PLC0415

        title_font = QFont("Arial", 14, QFont.Bold)
        painter.setFont(title_font)
        title_h = max(36.0, page_rect.height() * 0.06)
        title_rect = QRectF(page_rect.x(), page_rect.y(), page_rect.width(), title_h)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, title)
        footer_reserved_h = self._pdf_footer_reserved_height(page_rect)
        content_top = title_rect.bottom() + 8.0
        content_bottom = page_rect.bottom() - footer_reserved_h
        content_rect = QRectF(
            page_rect.x(),
            content_top,
            page_rect.width(),
            max(1.0, content_bottom - content_top),
        )
        return title_rect, content_rect

    def _pdf_footer_reserved_height(self, page_rect: QRectF) -> float:
        footer_h = max(14.0, page_rect.height() * 0.022)
        return footer_h + max(18.0, page_rect.height() * 0.02)

    def _pdf_prepare_footer(self) -> None:
        self._pdf_footer_date = QDateTime.currentDateTime().toString("dd.MM.yyyy")
        self._pdf_footer_page_no = 1
        self._pdf_counting_only = False

    def _draw_pdf_footer(self, painter, writer) -> None:
        if bool(getattr(self, "_pdf_counting_only", False)):
            return
        from PySide6.QtGui import QFont  # noqa: PLC0415

        page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
        page_no = int(getattr(self, "_pdf_footer_page_no", 1))
        date_text = str(getattr(self, "_pdf_footer_date", ""))
        footer_font = QFont("Arial", 9)
        footer_h = max(14.0, page_rect.height() * 0.022)

        painter.save()
        painter.setFont(footer_font)
        painter.setPen(Qt.darkGray)
        painter.drawText(
            QRectF(page_rect.x(), page_rect.bottom() - footer_h, page_rect.width() * 0.4, footer_h),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"Datum: {date_text}",
        )
        painter.drawText(
            QRectF(page_rect.x() + page_rect.width() * 0.3, page_rect.bottom() - footer_h, page_rect.width() * 0.4, footer_h),
            Qt.AlignHCenter | Qt.AlignVCenter,
            f"Seite {page_no}",
        )
        painter.restore()

    def _pdf_new_page(self, painter, writer) -> None:
        if not bool(getattr(self, "_pdf_counting_only", False)):
            self._draw_pdf_footer(painter, writer)
            writer.newPage()
        self._pdf_footer_page_no = int(getattr(self, "_pdf_footer_page_no", 1)) + 1

    def _pdf_finalize_footer(self, painter, writer) -> None:
        self._draw_pdf_footer(painter, writer)

    def _draw_pdf_cover_page(self, painter, writer, meta: dict[str, str]) -> None:
        from PySide6.QtGui import QFont  # noqa: PLC0415

        page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
        footer_reserved_h = self._pdf_footer_reserved_height(page_rect)
        title_font = QFont("Arial", 22, QFont.Bold)
        label_font = QFont("Arial", 11, QFont.Bold)
        value_font = QFont("Arial", 11)
        notes_font = QFont("Arial", 10)

        y = page_rect.y() + max(36.0, page_rect.height() * 0.08)
        painter.save()
        painter.setPen(Qt.black)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(page_rect.x(), y, page_rect.width(), 42.0),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "HRouting Projektbericht",
        )
        y += 62.0

        line_h = max(24.0, page_rect.height() * 0.042)
        left_x = page_rect.x() + page_rect.width() * 0.12
        label_w = page_rect.width() * 0.22
        value_w = page_rect.width() * 0.56

        fields = [
            ("Projekt", str(meta.get("project", ""))),
            ("Author", str(meta.get("author", ""))),
            ("Datum", str(meta.get("date", ""))),
            ("Planungsstand", str(meta.get("planning_status", ""))),
            ("Seitenanzahl", str(meta.get("page_count", ""))),
            ("HRouting Programmversion", str(meta.get("hrouting_version", ""))),
        ]

        for label, value in fields:
            painter.setFont(label_font)
            painter.drawText(QRectF(left_x, y, label_w, line_h), Qt.AlignLeft | Qt.AlignVCenter, f"{label}:")
            painter.setFont(value_font)
            painter.drawText(QRectF(left_x + label_w, y, value_w, line_h), Qt.AlignLeft | Qt.AlignVCenter, value)
            y += line_h

        y += 10.0
        painter.setFont(label_font)
        painter.drawText(QRectF(left_x, y, page_rect.width() * 0.78, line_h), Qt.AlignLeft | Qt.AlignTop, "Notizen:")
        y += line_h
        painter.setFont(notes_font)
        notes_rect = QRectF(
            left_x,
            y,
            page_rect.width() * 0.78,
            max(80.0, page_rect.bottom() - y - footer_reserved_h - 12.0),
        )
        painter.drawText(notes_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, str(meta.get("notes", "")))
        painter.restore()

    def _estimate_pdf_total_pages(
        self,
        writer,
        enabled_pages: list[dict],
        hk_rows: list[dict],
        t_supply: float,
        t_return: float,
        ap_rows: list[list[str]],
        cable_rows: list[list[str]],
        export_data: dict,
    ) -> int:
        from PySide6.QtGui import QPainter, QPicture  # noqa: PLC0415

        saved_vis = self._save_all_visibility()
        self._pdf_prepare_footer()
        self._pdf_counting_only = True
        dummy = QPicture()
        painter = QPainter()
        total = max(1, len(enabled_pages))
        try:
            if not painter.begin(dummy):
                return max(1, len(enabled_pages)) + 1
            for idx, page in enumerate(enabled_pages):
                if idx > 0:
                    self._pdf_new_page(painter, writer)
                self._apply_page_visibility(page)
                self._render_pdf_export_page(
                    painter,
                    writer,
                    page,
                    hk_rows,
                    t_supply,
                    t_return,
                    ap_rows,
                    cable_rows,
                    export_data,
                )
            total = int(getattr(self, "_pdf_footer_page_no", 1))
        finally:
            try:
                if painter.isActive():
                    painter.end()
            finally:
                self._restore_all_visibility(saved_vis)
                self._pdf_counting_only = False
                self.canvas.update()
        # +1 for generated title page
        return max(2, total + 1)

    def _draw_pdf_table(
        self,
        painter,
        writer,
        title: str,
        headers: list[str],
        rows: list[list[str]],
        col_widths: list[float] | None = None,
    ) -> None:
        from PySide6.QtGui import QBrush, QFont, QPen  # noqa: PLC0415

        page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
        title_rect, content_rect = self._draw_pdf_title(painter, page_rect, title)
        _ = title_rect
        side_margin = max(24.0, content_rect.width() * 0.02)
        table_rect = QRectF(
            content_rect.x() + side_margin,
            content_rect.y(),
            max(1.0, content_rect.width() - 2 * side_margin),
            content_rect.height(),
        )
        n_cols = max(1, len(headers))
        body_size = 9 if n_cols <= 6 else (8 if n_cols <= 8 else 7)
        body_font = QFont("Arial", body_size)
        painter.setFont(body_font)

        if col_widths and len(col_widths) == n_cols and sum(col_widths) > 0:
            total_w = float(sum(col_widths))
            widths = [table_rect.width() * (w / total_w) for w in col_widths]
        else:
            widths = [table_rect.width() / n_cols] * n_cols

        cell_pad = 4.0
        fm = painter.fontMetrics()
        header_h = max(16.0, fm.lineSpacing() + 2 * cell_pad)
        min_row_h = max(14.0, fm.lineSpacing() + 2 * cell_pad)
        cell_pad = 4.0
        y = table_rect.y() + 6.0
        bottom_margin = 6.0
        wide_table = n_cols >= 8

        def draw_header(_y: float):
            x = table_rect.x()
            painter.save()
            painter.setFont(QFont("Arial", body_size, QFont.Bold))
            painter.setPen(QPen(Qt.black, 1.0))
            for idx, header in enumerate(headers):
                cell = QRectF(x, _y, widths[idx], header_h)
                painter.fillRect(cell, QBrush(QColor("#e0e0e0")))
                painter.drawRect(cell)
                if wide_table:
                    inner_w = max(1, int(widths[idx] - 2 * cell_pad))
                    header_text = fm.elidedText(str(header), Qt.ElideRight, inner_w)
                    flags = Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine
                else:
                    header_text = header
                    flags = Qt.AlignCenter | Qt.TextWordWrap
                painter.drawText(
                    cell.adjusted(cell_pad, cell_pad, -cell_pad, -cell_pad),
                    flags,
                    header_text,
                )
                x += widths[idx]
            painter.restore()

        def row_height(values: list[str]) -> float:
            if wide_table:
                return min_row_h
            height = min_row_h
            for idx, value in enumerate(values):
                inner_w = max(1, int(widths[idx] - 2 * cell_pad))
                br = fm.boundingRect(0, 0, inner_w, 100000, Qt.TextWordWrap | Qt.AlignLeft, str(value))
                height = max(height, br.height() + 2 * cell_pad)
            return height

        draw_header(y)
        y += header_h
        for row_index, row in enumerate(rows):
            data_row = [str(row[idx]) if idx < len(row) else "" for idx in range(n_cols)]
            rh = row_height(data_row)
            if y + rh > table_rect.bottom() - bottom_margin:
                self._pdf_new_page(painter, writer)
                page_rect2 = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
                _, content_rect2 = self._draw_pdf_title(painter, page_rect2, f"{title} (Fortsetzung)")
                table_rect = QRectF(
                    content_rect2.x() + side_margin,
                    content_rect2.y(),
                    max(1.0, content_rect2.width() - 2 * side_margin),
                    content_rect2.height(),
                )
                y = table_rect.y() + 6.0
                if col_widths and len(col_widths) == n_cols and sum(col_widths) > 0:
                    total_w = float(sum(col_widths))
                    widths = [table_rect.width() * (w / total_w) for w in col_widths]
                else:
                    widths = [table_rect.width() / n_cols] * n_cols
                painter.setFont(body_font)
                draw_header(y)
                y += header_h

            x = table_rect.x()
            if row_index % 2 == 1:
                painter.fillRect(QRectF(table_rect.x(), y, table_rect.width(), rh), QBrush(QColor("#f5f5f5")))
            for idx, value in enumerate(data_row):
                cell = QRectF(x, y, widths[idx], rh)
                painter.drawRect(cell)
                if wide_table:
                    inner_w = max(1, int(widths[idx] - 2 * cell_pad))
                    value_text = fm.elidedText(value, Qt.ElideRight, inner_w)
                    align = (Qt.AlignRight | Qt.AlignVCenter) if idx >= 2 else (Qt.AlignLeft | Qt.AlignVCenter)
                    flags = align | Qt.TextSingleLine
                else:
                    value_text = value
                    align = (Qt.AlignRight | Qt.AlignTop) if idx >= 2 else (Qt.AlignLeft | Qt.AlignTop)
                    flags = align | Qt.TextWordWrap
                painter.drawText(
                    cell.adjusted(cell_pad, cell_pad, -cell_pad, -cell_pad),
                    flags,
                    value_text,
                )
                x += widths[idx]
            y += rh

    def _render_pdf_export_page(
        self,
        painter,
        writer,
        page: dict,
        hk_rows: list[dict],
        t_supply: float,
        t_return: float,
        ap_rows: list[list[str]],
        cable_rows: list[list[str]],
        export_data: dict | None = None,
    ) -> None:
        ptype = str(page.get("type", "plan")).strip().lower()
        title = str(page.get("title") or "Seite").strip() or "Seite"

        if ptype == "lengths":
            rows = [
                [
                    str(r.get("id", "")),
                    str(r.get("name", "")),
                    f"{float(r.get('area_m2', 0.0)):.2f} m²",
                    f"{float(r.get('route_m', 0.0)):.2f} m",
                    f"{float(r.get('supply_m', 0.0)):.2f} m",
                    f"{float(r.get('total_m', 0.0)):.2f} m",
                ]
                for r in hk_rows
            ]
            self._draw_pdf_table(
                painter,
                writer,
                title,
                ["ID", "Name", "Fläche", "Rohr", "Zuleitung", "Gesamt"],
                rows,
                col_widths=[0.8, 1.6, 1.0, 1.0, 1.0, 1.0],
            )
            return

        if ptype == "hydraulics":
            rows = [
                [
                    str(r.get("id", "")),
                    str(r.get("name", "")),
                    f"{float(r.get('power_w', 0.0)):.0f} W",
                    f"{float(r.get('volume_flow_lmin', 0.0)):.2f} l/min",
                    f"{float(r.get('pressure_drop_mbar', 0.0)):.0f} mbar",
                ]
                for r in hk_rows
            ]
            self._draw_pdf_table(
                painter,
                writer,
                f"{title} (Vorlauf {t_supply:.1f}°C / Rücklauf {t_return:.1f}°C)",
                ["ID", "Name", "Leistung", "Volumenstrom", "Druckverlust"],
                rows,
                col_widths=[0.9, 1.6, 1.0, 1.1, 1.1],
            )
            return

        if ptype == "elektro_room":
            room_ids = [str(v) for v in (page.get("room_ids") or []) if str(v).strip()]
            selected_ap_ids, selected_cable_ids, room_ap_rows, room_cable_rows = self._collect_pdf_elektro_room_rows(room_ids)
            plan_title = f"{title} – Plan"
            ap_title = f"{title} – Anschlusspunkte"
            cable_title = f"{title} – Kabelverbindungen"

            page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
            _, content_rect = self._draw_pdf_title(painter, page_rect, plan_title)

            if not room_ids:
                painter.drawText(
                    content_rect,
                    Qt.AlignCenter | Qt.TextWordWrap,
                    "Keine Elektro-Räume ausgewählt.",
                )
                return

            source_rect = self._room_focus_source_rect(
                room_ids,
                self._effective_pdf_source_rect(page),
            )
            image_rect = QRectF(
                content_rect.x(),
                content_rect.y(),
                max(1.0, content_rect.width()),
                max(1.0, content_rect.height()),
            )

            # Limit visibility to selected rooms and their AP/cables for this page.
            for rid in self.canvas._elec_room_polygons:
                self.canvas._elec_room_visible[rid] = rid in set(room_ids)
            for pid in self.canvas._elec_points:
                self.canvas._elec_visible[pid] = pid in selected_ap_ids
            for cid in self.canvas._elec_cables:
                self.canvas._elec_visible[cid] = cid in selected_cable_ids

            img = self.canvas.render_for_export(
                source_rect=source_rect,
                output_w=max(1, int(image_rect.width())),
                output_h=max(1, int(image_rect.height())),
            )
            painter.drawImage(image_rect, img)

            self._pdf_new_page(painter, writer)
            self._draw_pdf_table(
                painter,
                writer,
                ap_title,
                ["Name", "Symbol", "Position", "Höhe", "Notiz"],
                room_ap_rows,
                col_widths=[1.2, 1.1, 0.9, 0.8, 2.0],
            )

            self._pdf_new_page(painter, writer)
            self._draw_pdf_table(
                painter,
                writer,
                cable_title,
                ["Name", "Typ", "Start", "Ende", "Länge"],
                room_cable_rows,
                col_widths=[1.6, 1.1, 1.2, 1.2, 0.8],
            )
            return

        if ptype == "heating_circuit":
            circuit_ids = [str(v) for v in (page.get("circuit_ids") or []) if str(v).strip()]
            selected_circuit_ids, detail_rows, metric_rows = self._collect_pdf_heating_circuit_rows(circuit_ids)
            plan_title = f"{title} – Plan"
            detail_title = f"{title} – Heizkreisdetails"
            metric_title = f"{title} – Verlegungsdaten"

            page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
            _, content_rect = self._draw_pdf_title(painter, page_rect, plan_title)

            if not selected_circuit_ids:
                painter.drawText(
                    content_rect,
                    Qt.AlignCenter | Qt.TextWordWrap,
                    "Keine Heizkreise ausgewählt.",
                )
                return

            source_rect = self._heating_circuit_focus_source_rect(
                list(selected_circuit_ids),
                self._effective_pdf_source_rect(page),
            )
            image_rect = QRectF(
                content_rect.x(),
                content_rect.y(),
                max(1.0, content_rect.width()),
                max(1.0, content_rect.height()),
            )

            for cid in list(self.canvas._polygons) + list(self.canvas._manual_routes) + list(self.canvas._supply_lines):
                self.canvas._circuit_visible[cid] = cid in selected_circuit_ids

            img = self.canvas.render_for_export(
                source_rect=source_rect,
                output_w=max(1, int(image_rect.width())),
                output_h=max(1, int(image_rect.height())),
            )
            painter.drawImage(image_rect, img)

            self._pdf_new_page(painter, writer)
            self._draw_pdf_table(
                painter,
                writer,
                detail_title,
                ["ID", "Name", "Raumtemp.", "Boden", "Ø", "Abstand", "Wandabst."],
                detail_rows,
                col_widths=[0.9, 1.5, 0.9, 1.2, 0.8, 0.9, 1.0],
            )

            self._pdf_new_page(painter, writer)
            self._draw_pdf_table(
                painter,
                writer,
                metric_title,
                ["ID", "Name", "Fläche", "Rohr", "Zuleitung", "Gesamt", "Leistung", "Δp"],
                metric_rows,
                col_widths=[0.8, 1.3, 0.9, 0.9, 0.9, 0.9, 0.9, 0.8],
            )
            return

        page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
        _, content_rect = self._draw_pdf_title(painter, page_rect, title)
        source_rect = self._effective_pdf_source_rect(page)
        image_side_margin = max(28.0, content_rect.width() * 0.03)
        image_top_bottom_margin = max(8.0, content_rect.height() * 0.01)
        image_rect = content_rect.adjusted(
            image_side_margin,
            image_top_bottom_margin,
            -image_side_margin,
            -image_top_bottom_margin,
        )

        img = self.canvas.render_for_export(
            source_rect=source_rect,
            output_w=max(1, int(image_rect.width())),
            output_h=max(1, int(image_rect.height())),
        )
        painter.drawImage(image_rect, img)

        sections = set(page.get("table_sections") or [])
        if ptype == "heating":
            if "hk_lengths" in sections and hk_rows:
                self._pdf_new_page(painter, writer)
                rows = [
                    [
                        str(r.get("id", "")),
                        str(r.get("name", "")),
                        f"{float(r.get('total_m', 0.0)):.2f} m",
                    ]
                    for r in hk_rows
                ]
                self._draw_pdf_table(painter, writer, "Heizkreise – Einzellängen", ["ID", "Name", "Gesamt"], rows)
            if "hk_hydraulics" in sections and hk_rows:
                self._pdf_new_page(painter, writer)
                rows = [
                    [
                        str(r.get("id", "")),
                        str(r.get("name", "")),
                        f"{float(r.get('power_w', 0.0)):.0f} W",
                        f"{float(r.get('volume_flow_lmin', 0.0)):.2f} l/min",
                        f"{float(r.get('pressure_drop_mbar', 0.0)):.0f} mbar",
                    ]
                    for r in hk_rows
                ]
                self._draw_pdf_table(painter, writer, "Hydraulische Übersicht", ["ID", "Name", "Leistung", "Vol.-Strom", "Δp"], rows)
            if "hk_hkv_lines" in sections and export_data and export_data.get("hl_rows"):
                self._pdf_new_page(painter, writer)
                rows = [
                    [
                        str(r.get("name", "")),
                        str(r.get("type", "")),
                        str(r.get("start_hkv", "")),
                        str(r.get("end_hkv", "")),
                        f"{float(r.get('length_m', 0.0)):.2f} m",
                    ]
                    for r in export_data.get("hl_rows", [])
                ]
                self._draw_pdf_table(
                    painter,
                    writer,
                    "HKV-Leitungen",
                    ["Name", "Typ", "Start-HKV", "End-HKV", "Länge"],
                    rows,
                    col_widths=[1.4, 1.2, 1.3, 1.3, 0.8],
                )

        if ptype == "elektro":
            if "el_ap_infos" in sections and ap_rows:
                self._pdf_new_page(painter, writer)
                self._draw_pdf_table(
                    painter,
                    writer,
                    "Elektro – Anschlusspunkte",
                    ["Name", "Symbol", "Position", "Höhe", "Notiz"],
                    ap_rows,
                )
            if "el_kabel" in sections and cable_rows:
                self._pdf_new_page(painter, writer)
                self._draw_pdf_table(
                    painter,
                    writer,
                    "Elektro – Kabelverbindungen",
                    ["Name", "Typ", "Start", "Ende", "Länge"],
                    cable_rows,
                    col_widths=[1.6, 1.2, 1.4, 1.4, 0.9],
                )
            if export_data:
                if "el_ap_types" in sections and export_data.get("ap_type_counts"):
                    self._pdf_new_page(painter, writer)
                    rows = [
                        [str(k), str(v)]
                        for k, v in sorted(export_data.get("ap_type_counts", {}).items(), key=lambda item: str(item[0]).lower())
                    ]
                    self._draw_pdf_table(
                        painter,
                        writer,
                        "Anschlusspunkt-Typen",
                        ["Typ", "Anzahl"],
                        rows,
                    )

                if "el_ap_connections" in sections and export_data.get("ap_cables"):
                    self._pdf_new_page(painter, writer)
                    ap_rows_ext: list[list[str]] = []
                    for ap_name in sorted(export_data.get("ap_cables", {}).keys()):
                        for conn in export_data["ap_cables"][ap_name]:
                            ap_rows_ext.append(
                                [
                                    ap_name,
                                    str(conn.get("cable", "")),
                                    str(conn.get("type", "")),
                                    str(conn.get("role", "")),
                                    str(conn.get("ap_device", "")),
                                    str(conn.get("ap_device_color", "")),
                                    str(conn.get("ap_note", "")),
                                    str(conn.get("cable_note", "")),
                                    f"{float(conn.get('length_m', 0.0)):.2f} m",
                                ]
                            )
                    self._draw_pdf_table(
                        painter,
                        writer,
                        "Anschlusspunkte – Kabelzuordnung",
                        ["AP", "Kabel", "Typ", "Anschluss", "Gerät", "Farbe", "AP-Notiz", "Kabel-Notiz", "Länge"],
                        ap_rows_ext,
                        col_widths=[1.0, 1.0, 0.8, 0.8, 1.0, 0.7, 1.4, 1.4, 0.8],
                    )

                if "el_rooms" in sections and export_data.get("room_ap_connections"):
                    self._pdf_new_page(painter, writer)
                    rows = [
                        [
                            str(r.get("room", "")),
                            str(r.get("ap", "")),
                            str(r.get("ap_device", "")),
                            str(r.get("ap_device_color", "")),
                            str(r.get("ap_note", "")),
                            str(r.get("cable", "")),
                            str(r.get("type", "")),
                            str(r.get("cable_note", "")),
                            str(r.get("target_ap", "")),
                            f"{float(r.get('length_m', 0.0)):.2f} m",
                        ]
                        for r in export_data.get("room_ap_connections", [])
                    ]
                    self._draw_pdf_table(
                        painter,
                        writer,
                        "AP-Zuordnung nach Räumen",
                        ["Raum", "AP", "Gerät", "Farbe", "AP-Notiz", "Kabel", "Typ", "Kabel-Notiz", "Ziel-AP", "Länge"],
                        rows,
                        col_widths=[1.0, 0.9, 0.9, 0.7, 1.1, 0.9, 0.8, 1.1, 0.9, 0.7],
                    )

                if "el_uv" in sections and export_data.get("uv_rows"):
                    self._pdf_new_page(painter, writer)
                    rows = [
                        [
                            str(r.get("ap", "")),
                            str(r.get("room", "")),
                            f"{r.get('rows', 0)}x{r.get('modules_per_row', 0)}",
                            str(r.get("row", "")),
                            str(r.get("slot", "")),
                            str(r.get("device_type", "")),
                            str(r.get("spec", "")),
                            str(r.get("label", "")),
                            str(r.get("assignment", "")),
                            str(r.get("note", "")),
                        ]
                        for r in export_data.get("uv_rows", [])
                    ]
                    self._draw_pdf_table(
                        painter,
                        writer,
                        "Unterverteilungen (UV)",
                        ["UV", "Raum", "Raster", "Reihe", "TE", "Belegung", "Kennz.", "Bezeichnung", "Kabel/Stromkreis", "Notiz"],
                        rows,
                        col_widths=[1.1, 1.0, 0.8, 0.6, 0.6, 1.0, 0.9, 1.2, 1.2, 1.2],
                    )

                if "el_up_distribution" in sections and export_data.get("up_distribution_rows"):
                    self._pdf_new_page(painter, writer)
                    rows = [
                        [
                            str(r.get("ap", "")),
                            str(r.get("room", "")),
                            str(r.get("incoming_cable", "")),
                            str(r.get("outgoing_cables", "")),
                            str(r.get("from_conductor", "")),
                            str(r.get("to_cable", "")),
                            str(r.get("to_conductor", "")),
                            str(r.get("mapping_note", "")),
                            str(r.get("distribution_note", "")),
                        ]
                        for r in export_data.get("up_distribution_rows", [])
                    ]
                    self._draw_pdf_table(
                        painter,
                        writer,
                        "Unterputz-Verteilungen",
                        ["AP", "Raum", "Zuleitung", "Abgänge", "Ader (Zul.)", "Abgehendes Kabel", "Ader (Abg.)", "Zuordn.-Notiz", "Verteilungs-Notiz"],
                        rows,
                        col_widths=[0.9, 0.9, 1.1, 1.2, 0.8, 1.1, 0.8, 1.2, 1.2],
                    )

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
                        for row in export_data.get(key, []) or []:
                            note = ""
                            if key == "uv_device_bom_rows":
                                note = f"TE: {row.get('te_total', 0)}"
                            elif key == "uv_busbar_bom_rows":
                                note = f"TE {row.get('te_start', '')}-{row.get('te_end', '')}; UV: {row.get('uv_names', '')}"
                            else:
                                note = str(row.get("note", "") or "")
                            bom_rows.append(
                                [
                                    section_name,
                                    str(row.get("description", "")),
                                    str(row.get("manufacturer", "")),
                                    str(row.get("article_number", "")),
                                    str(row.get("unit", "")),
                                    f"{float(row.get('quantity', 0.0) or 0.0):.2f}",
                                    note,
                                ]
                            )
                    if bom_rows:
                        self._pdf_new_page(painter, writer)
                        self._draw_pdf_table(
                            painter,
                            writer,
                            "Stückliste",
                            ["Bereich", "Artikel", "Hersteller", "Artikelnummer", "Einheit", "Menge", "Notiz"],
                            bom_rows,
                            col_widths=[1.0, 1.6, 1.1, 1.2, 0.7, 0.7, 1.2],
                        )

                if "el_uv_busbars" in sections and export_data.get("uv_busbar_bom_rows"):
                    self._pdf_new_page(painter, writer)
                    rows = [
                        [
                            str(r.get("phase", "")),
                            str(r.get("te_start", "")),
                            str(r.get("te_end", "")),
                            f"{float(r.get('quantity', 0.0) or 0.0):.2f}",
                            str(r.get("uv_names", "")),
                        ]
                        for r in export_data.get("uv_busbar_bom_rows", [])
                    ]
                    self._draw_pdf_table(
                        painter,
                        writer,
                        "UV-Phasenschienen",
                        ["Phase", "TE Start", "TE Ende", "Menge (TE)", "UV"],
                        rows,
                        col_widths=[0.8, 0.9, 0.9, 1.0, 2.0],
                    )

                if any(key in sections for key in {"schaltplan_uv", "schaltplan_stromkreise", "schaltplan_hierarchie"}):
                    ap_nodes, cable_edges, room_map = self._build_schema_data()
                    ap_nodes_dict = {node.point_id: node for node in ap_nodes}
                    cable_edges_dict = {edge.cable_id: edge for edge in cable_edges}
                    uv_data = export_data.get("uv_data", [])

                    if "schaltplan_uv" in sections and uv_data:
                        self._pdf_new_page(painter, writer)
                        rows = [
                            [
                                str(uv.get("ap_name", "")),
                                str(uv.get("room", "")),
                                f"{uv.get('rows', 0)}x{uv.get('modules_per_row', 0)}",
                                str(len(uv.get("slots", []) or [])),
                            ]
                            for uv in uv_data
                        ]
                        self._draw_pdf_table(
                            painter,
                            writer,
                            "Schaltplan – UV-Übersicht",
                            ["UV", "Raum", "Raster", "Anzahl Slots"],
                            rows,
                        )

                    if "schaltplan_stromkreise" in sections and uv_data:
                        circuits_rows: list[list[str]] = []
                        for uv in uv_data:
                            uv_id = str(uv.get("ap_id", "") or "")
                            uv_name = str(uv.get("ap_name", uv_id) or uv_id)
                            circuits = get_uv_circuits(uv_id, ap_nodes_dict, cable_edges_dict, room_map)
                            for circuit in circuits:
                                circuits_rows.append(
                                    [
                                        uv_name,
                                        str(circuit.get("row", "")),
                                        str(circuit.get("slot", "")),
                                        str(circuit.get("device_type", "")),
                                        str(circuit.get("spec", "")),
                                        str(circuit.get("label", "")),
                                        str(circuit.get("cable_id", "")),
                                        str(circuit.get("end_ap_name", "")),
                                        str(circuit.get("end_ap_room", "")),
                                        str(circuit.get("note", "")),
                                    ]
                                )
                        if circuits_rows:
                            self._pdf_new_page(painter, writer)
                            self._draw_pdf_table(
                                painter,
                                writer,
                                "Schaltplan – Stromkreise",
                                ["UV", "Reihe", "TE", "Gerät", "Kennz.", "Bezeichnung", "Kabel", "Verbraucher", "Raum", "Notiz"],
                                circuits_rows,
                            )

                    if "schaltplan_hierarchie" in sections:
                        hierarchy = build_uv_hierarchy(ap_nodes_dict, cable_edges_dict)

                        def _flatten_edges(nodes: list[dict], parent: dict | None = None, out: list[list[str]] | None = None) -> list[list[str]]:
                            out = out if out is not None else []
                            for node in nodes:
                                if parent is not None:
                                    out.append(
                                        [
                                            str(parent.get("ap_type", "")).upper(),
                                            str(parent.get("name", "")),
                                            str(node.get("ap_type", "")).upper(),
                                            str(node.get("name", "")),
                                        ]
                                    )
                                _flatten_edges(node.get("children", []), node, out)
                            return out

                        rows = _flatten_edges(hierarchy)
                        self._pdf_new_page(painter, writer)
                        self._draw_pdf_table(
                            painter,
                            writer,
                            "Schaltplan – Hierarchie",
                            ["Typ Quelle", "Quelle", "Typ Ziel", "Ziel"],
                            rows or [["", "Keine Hierarchie-Verbindungen vorhanden.", "", ""]],
                        )

    def _continue_export_pdf(self, pages: list[dict], export_meta: dict[str, str]) -> None:
        enabled_pages = [p for p in pages if p.get("enabled", True)]
        if not enabled_pages:
            QMessageBox.information(self, "PDF-Export", "Keine aktive Exportseite ausgewählt.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Als PDF exportieren",
            self._default_pdf_export_path(),
            "PDF (*.pdf)",
        )
        if not path:
            return
        self._remember_pdf_export_path(Path(path))

        from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter  # noqa: PLC0415

        writer = QPdfWriter(path)
        writer.setResolution(150)
        writer.setPageSize(QPageSize(QPageSize.A4))
        writer.setPageOrientation(QPageLayout.Landscape)

        progress = QProgressDialog("PDF wird exportiert…", "Abbrechen", 0, len(enabled_pages), self)
        progress.setWindowTitle("PDF-Export")
        progress.setMinimumDuration(0)
        progress.setModal(True)
        progress.setValue(0)
        QApplication.processEvents()

        painter = QPainter()
        if not painter.begin(writer):
            progress.close()
            QMessageBox.critical(self, "PDF-Export", "PDF konnte nicht erstellt werden.")
            return

        saved_vis = self._save_all_visibility()
        cancelled = False
        self._pdf_prepare_footer()
        try:
            export_data = self._collect_export_data()
            hk_rows = export_data.get("hk_rows", [])
            t_supply = float(export_data.get("t_supply", self._document.settings.get("t_supply", 35.0)))
            t_return = float(export_data.get("t_return", self._document.settings.get("t_return", 30.0)))
            ap_rows, cable_rows = self._collect_pdf_electro_rows()

            meta = self._normalize_pdf_export_meta(export_meta, pages)
            total_pages = self._estimate_pdf_total_pages(
                writer,
                enabled_pages,
                hk_rows,
                t_supply,
                t_return,
                ap_rows,
                cable_rows,
                export_data,
            )
            meta["page_count"] = str(total_pages)

            # Reset footer state after dry-run counting so the real export starts at page 1.
            self._pdf_prepare_footer()
            self._draw_pdf_cover_page(painter, writer, meta)
            for idx, page in enumerate(enabled_pages):
                if progress.wasCanceled():
                    cancelled = True
                    break
                self._pdf_new_page(painter, writer)
                self._apply_page_visibility(page)
                self._render_pdf_export_page(
                    painter,
                    writer,
                    page,
                    hk_rows,
                    t_supply,
                    t_return,
                    ap_rows,
                    cable_rows,
                    export_data,
                )
                progress.setValue(idx + 1)
                QApplication.processEvents()
        finally:
            try:
                self._pdf_finalize_footer(painter, writer)
            except Exception:
                pass
            try:
                painter.end()
            finally:
                self._restore_all_visibility(saved_vis)
                self.canvas.update()
                progress.close()

        if cancelled:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            self.statusBar().showMessage("PDF-Export abgebrochen.", 3000)
            return

        self._pdf_export_pages = pages
        self._pdf_export_meta = self._normalize_pdf_export_meta(export_meta, pages)
        self._mark_dirty()
        self.log.success(f"PDF exportiert: {path}")
        self.statusBar().showMessage(f"PDF exportiert: {path}", 4000)

    def _export_pdf(self) -> None:
        config = self._open_pdf_export_config_dialog()
        if config is None:
            return
        if isinstance(config, tuple):
            pages = config[0] if len(config) >= 1 else []
            export_meta = config[1] if len(config) >= 2 else self._pdf_export_meta
        else:
            pages = config
            export_meta = self._pdf_export_meta
        pages = self._normalize_pdf_export_pages(pages)
        export_meta = self._normalize_pdf_export_meta(export_meta, pages)

        # Persist configuration immediately after dialog acceptance,
        # even if the subsequent file dialog is canceled.
        changed = (pages != self._pdf_export_pages) or (export_meta != self._pdf_export_meta)
        self._pdf_export_pages = pages
        self._pdf_export_meta = export_meta
        if changed:
            self._mark_dirty()

        self._continue_export_pdf(pages, export_meta)

    def _collect_length_overview_rows(self) -> tuple[list[dict], float, float]:
        """Collect heating rows for the length / hydraulics overview."""
        from model.computed import heating_length_overview  # noqa: PLC0415

        return heating_length_overview(self._document)

    def _export_lengths(self) -> None:
        """Längen- und Hydraulik-Übersicht für alle Heizkreise."""
        from PySide6.QtWidgets import (  # noqa: PLC0415
            QDialog, QLabel, QScrollArea, QVBoxLayout, QDialogButtonBox,
        )

        hk_rows, t_supply, t_return = self._collect_length_overview_rows()
        if not hk_rows:
            QMessageBox.information(self, "Längenexport", "Keine Heizkreise im Projekt.")
            return

        rows: list[str] = [
            "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>",
            "<tr><th>ID</th><th>Name</th><th>Fläche m²</th>"
            "<th>Rohrlänge m</th><th>Zuleitung m</th><th>Gesamt m</th>"
            "<th>Leistung W</th><th>Volumenstrom l/min</th><th>Druckverlust mbar</th></tr>",
        ]
        total_power = 0.0
        total_route = 0.0
        total_supply = 0.0
        total_length = 0.0
        for row in hk_rows:
            total_power += row["power_w"]
            total_route += row["route_m"]
            total_supply += row["supply_m"]
            total_length += row["total_m"]
            rows.append(
                f"<tr><td>{row['id']}</td><td>{row['name']}</td>"
                f"<td>{row['area_m2']:.2f}</td><td>{row['route_m']:.2f}</td>"
                f"<td>{row['supply_m']:.2f}</td><td>{row['total_m']:.2f}</td>"
                f"<td>{row['power_w']:.0f}</td><td>{row['volume_flow_lmin']:.2f}</td><td>{row['pressure_drop_mbar']:.0f}</td></tr>"
            )

        rows.append(
            f"<tr><td colspan='3'><b>Gesamt</b></td>"
            f"<td><b>{total_route:.2f}</b></td>"
            f"<td><b>{total_supply:.2f}</b></td>"
            f"<td><b>{total_length:.2f}</b></td>"
            f"<td><b>{total_power:.0f}</b></td><td></td><td></td></tr>"
        )
        rows.append("</table>")
        rows.append(
            "<p><i>Die Gesamtlänge berücksichtigt die Zuleitung doppelt, "
            "passend zur Druckverlustberechnung.</i></p>"
        )
        html = "\n".join(rows)

        dialog = QDialog(self)
        dialog.setWindowTitle("Längen & Hydraulik")
        dialog.resize(800, 500)
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setWordWrap(True)
        label.setText(html)
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dialog.reject)
        layout.addWidget(bb)
        dialog.exec()

    def _import_kicad_cables(self) -> None:
        start_dir = str(self._project_path.parent) if self._project_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "KiCad Root-Schaltplan wählen",
            start_dir,
            "KiCad Schaltplan (*.kicad_sch);;Alle Dateien (*)",
        )
        if not path:
            return

        try:
            scan_result = scan_kicad_project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "KiCad-Import", f"Scan fehlgeschlagen:\n{exc}")
            return

        if hasattr(self, "_update_elec_point_room_assignments"):
            self._update_elec_point_room_assignments()

        if (
            not scan_result.ap_group_candidates
            and not scan_result.kbl_bus_candidates
            and not any(
                str(getattr(c, "pin_name_raw", "") or "").strip().upper().startswith("KBL_")
                and str(getattr(c, "spec_raw", "") or "").strip()
                for c in scan_result.candidates.values()
            )
        ):
            QMessageBox.information(self, "KiCad-Import", "Keine importierbaren Kabelkandidaten gefunden.")
            return

        previews = build_import_preview(
            scan_result,
            existing_cables=self._kicad_existing_cables_payload(),
            elec_points=self._kicad_elec_points_payload(),
        )

        ap_previews = self._build_kicad_ap_phase_previews(previews)
        approved_ap_names = {preview.cable_name.strip().casefold() for preview in ap_previews if preview.cable_name.strip()}
        if ap_previews:
            ap_dialog = KiCadImportDialog(
                scan_result,
                ap_previews,
                phase="aps",
                floorplan_choices=self._collect_floorplan_choices(),
                room_choices_by_floorplan=self._collect_room_choices_by_floorplan(),
                initial_ap_assignments=self._build_initial_textfield_ap_assignments(scan_result, ap_previews),
                parent=self,
            )
            if ap_dialog.exec() != QDialog.Accepted:
                return
            approved_ap_keys = set(ap_dialog.selected_keys())
            valid_ap_keys = {str(preview.candidate_key) for preview in ap_previews}
            approved_ap_keys &= valid_ap_keys
            if not approved_ap_keys and valid_ap_keys:
                # Backward compatibility for non-phase-aware dialog stubs in tests.
                approved_ap_keys = set(valid_ap_keys)
            approved_ap_names = {
                preview.cable_name.strip().casefold()
                for preview in ap_previews
                if preview.candidate_key in approved_ap_keys and preview.cable_name.strip()
            }
            self._push_undo()
            selected_ap_assignments = {}
            if hasattr(ap_dialog, "selected_ap_assignments"):
                selected_ap_assignments = ap_dialog.selected_ap_assignments() or {}
            ap_summary = self._apply_kicad_ap_import(
                scan_result,
                approved_ap_keys,
                selected_ap_assignments,
            )
        else:
            approved_ap_keys = set()
            self._push_undo()
            ap_summary = {
                "created": 0,
                "reused": 0,
                "approved_ap_names": approved_ap_names,
            }

        phase_two_previews = build_import_preview(
            scan_result,
            existing_cables=self._kicad_existing_cables_payload(),
            elec_points=self._kicad_elec_points_payload(),
        )

        # Cable phase: KBL buses and KBL hierarchical labels with type definition
        cable_previews: list[object] = [
            p for p in phase_two_previews if p.source in {"kbl_bus", "kbl_label"}
        ]
        cable_warnings: list[str] = []
        for p in cable_previews:
            if p.start_ap_status == "matched" and p.end_ap_status == "matched":
                continue
            details: list[str] = []
            start_diag = str(getattr(p, "start_ap_diagnostic", "") or "").strip()
            end_diag = str(getattr(p, "end_ap_diagnostic", "") or "").strip()
            if start_diag:
                details.append(f"Start: {start_diag}")
            if end_diag:
                details.append(f"Ziel: {end_diag}")
            suffix = f" Details: {' | '.join(details)}" if details else ""
            cable_warnings.append(
                f"KBL-Kabel '{p.cable_name}': Start/Ziel-AP nicht eindeutig erkannt, bitte manuell wählen.{suffix}"
            )

        if not cable_previews:
            typed_candidates = [
                c
                for c in scan_result.candidates.values()
                if str(getattr(c, "spec_raw", "") or "").strip()
            ]
            sample_names = sorted(
                {
                    str(getattr(c, "base_name", "") or getattr(c, "pin_name_raw", "") or "").strip()
                    for c in typed_candidates
                    if str(getattr(c, "base_name", "") or getattr(c, "pin_name_raw", "") or "").strip()
                }
            )
            sample_text = ", ".join(sample_names[:5])
            detail_lines = ["Keine KBL-Kabel mit Typdefinition für Schritt 2 verfügbar."]
            if not scan_result.kbl_bus_candidates:
                detail_lines.append("In der Datei wurden keine KBL_-Busgruppen erkannt.")
            if typed_candidates:
                detail_lines.append(
                    (
                        f"Hinweis: {len(typed_candidates)} klassische Pin/Label-Kandidaten mit Typ wurden erkannt"
                        " (z. B. "
                        f"{sample_text})."
                    )
                )
                detail_lines.append("Schritt 2 importiert aktuell nur KBL_-Busgruppen.")
            QMessageBox.information(
                self,
                "KiCad-Import",
                "\n".join(detail_lines),
            )
            return

        dialog = KiCadImportDialog(
            scan_result,
            cable_previews,
            phase="cables",
            extra_warnings=cable_warnings,
            cable_ap_choices=self._kicad_elec_point_choices_with_rooms(),
            cable_type_choices=self._kicad_existing_cable_types(),
            initial_cable_endpoints={
                str(p.candidate_key): {
                    "start_ap_id": str(getattr(p, "start_ap_id", "") or ""),
                    "end_ap_id": str(getattr(p, "end_ap_id", "") or ""),
                }
                for p in cable_previews
            },
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_keys = dialog.selected_keys()
        if not selected_keys:
            QMessageBox.information(self, "KiCad-Import", "Keine Kabelkandidaten ausgewählt.")
            return

        preview_by_key = {str(p.candidate_key): p for p in cable_previews}

        # Apply user edits (name, type, start/end AP) to previews
        cable_data = dialog.selected_cable_data() if hasattr(dialog, "selected_cable_data") else {}
        for key, edits in cable_data.items():
            preview = preview_by_key.get(str(key))
            if preview is None:
                continue
            if edits.get("name"):
                preview.cable_name = edits["name"]
            if edits.get("type") is not None:
                preview.cable_type = edits["type"]
            preview.start_ap_id = str(edits.get("start_ap_id") or "")
            preview.end_ap_id = str(edits.get("end_ap_id") or "")

        summary = self._apply_kicad_cable_import(
            scan_result,
            selected_keys,
            selected_preview_map=preview_by_key,
            prepare_textfield_aps=False,
        )
        self.statusBar().showMessage(
            (
                f"KiCad-Import: AP neu {ap_summary['created']}, AP wiederverwendet {ap_summary['reused']}, "
                f"Kabel neu {summary['created']}, Kabel aktualisiert {summary['updated']}, "
                f"übersprungen {summary['skipped']}"
            ),
            5000,
        )
        QMessageBox.information(
            self,
            "KiCad-Import",
            (
                f"AP neu angelegt: {ap_summary['created']}\n"
                f"AP wiederverwendet: {ap_summary['reused']}\n"
                f"Neue Kabel: {summary['created']}\n"
                f"Aktualisiert: {summary['updated']}\n"
                f"Übersprungen: {summary['skipped']}"
            ),
        )

    def _add_textfield_candidates_to_scan(self, scan_result: KiCadScanResult) -> None:
        """Extract text field annotations from HRouting document and add them as candidates."""
        from logic.kicad_import import build_textfield_candidate_from_scan

        for text_id, text_elem in self._document.elements.get("text_annotations", {}).items():
            if text_elem is None or not isinstance(text_elem, TextAnnotation):
                continue
            content = text_elem.content
            if not content or not content.strip():
                continue

            # Resolve floor_plan_name from floor_plan_id
            floor_plan_name = ""
            floor_plan_id = text_elem.floor_plan_id
            if floor_plan_id:
                floorplan_elem = self._document.floorplans.get(floor_plan_id)
                if floorplan_elem:
                    floor_plan_name = str(floorplan_elem.name or "")

            tf_candidate = build_textfield_candidate_from_scan(
                text_id=text_id,
                content=content,
                candidates=scan_result.candidates,
                floor_plan_name=floor_plan_name,
            )
            if tf_candidate:
                scan_result.textfield_candidates[text_id] = tf_candidate

    def _apply_kicad_cable_import(
        self,
        scan_result: KiCadScanResult,
        selected_keys: list[str],
        selected_preview_map: dict[str, object] | None = None,
        prepare_textfield_aps: bool = True,
    ) -> dict[str, int]:
        created = 0
        updated = 0
        skipped = 0
        ap_created = 0
        ap_reused = 0
        existing_by_sync_key = {
            str(cable.kicad_cable_key or "").strip(): cable_id
            for cable_id, cable in self._document.elements["elec_cables"].items()
            if str(cable.kicad_cable_key or "").strip()
        }
        default_floor_plan_id = self._active_floorplan_id()
        first_touched_id = ""
        preview_by_key = dict(selected_preview_map or {})

        # Optional safety net: prepare/reuse APs for selected text fields
        ap_assignments: dict[str, tuple[str, str]] = {}
        for key in selected_keys:
            tf_candidate = scan_result.textfield_candidates.get(key)
            if tf_candidate is None:
                continue
            if prepare_textfield_aps:
                text_ap_id, text_floor_plan_id, status = self._resolve_or_create_textfield_ap(tf_candidate)
                if status == "created":
                    ap_created += 1
                elif status == "reused":
                    ap_reused += 1
            else:
                text_ap_id, text_floor_plan_id = self._resolve_existing_textfield_ap(tf_candidate)
                status = "reused" if text_ap_id else "none"
            if text_ap_id:
                ap_assignments[key] = (text_ap_id, text_floor_plan_id)

        # Phase 2: Import/update cables
        for key in selected_keys:
            preview = preview_by_key.get(key)
            # Try sheet pin candidate first
            candidate = scan_result.candidates.get(key)
            if candidate:
                sync_key = str(getattr(preview, "sync_key", "") or "") or self._build_kicad_cable_key(
                    scan_result.project_uuid, candidate
                )
                preferred_pin = self._preferred_kicad_pin_ref(candidate)
                is_kbl_label = str(getattr(preview, "source", "") or "") == "kbl_label"
                if is_kbl_label:
                    cable_name = str(getattr(preview, "cable_name", "") or "").strip() or (
                        candidate.base_name or candidate.pin_name_raw
                    )
                    cable_type = str(getattr(preview, "cable_type", "") or "").strip() or (
                        candidate.normalized_spec or candidate.spec_raw
                    )
                    resolved_start_ap_id = str(getattr(preview, "start_ap_id", "") or "")
                    resolved_end_ap_id = str(getattr(preview, "end_ap_id", "") or "")
                else:
                    cable_type = candidate.normalized_spec or candidate.spec_raw
                    cable_name = candidate.base_name or candidate.pin_name_raw
                    resolved_start_ap_id, resolved_end_ap_id = self._resolve_sheet_candidate_ap_pair(candidate)
                existing_id = existing_by_sync_key.get(sync_key)

                if not resolved_start_ap_id and not existing_id:
                    skipped += 1
                    continue

                if existing_id:
                    cable = self._document.elements["elec_cables"].get(existing_id)
                    if cable is None:
                        skipped += 1
                        continue
                    cable.name = cable_name
                    cable.data["type"] = cable_type
                    if resolved_start_ap_id and not cable.start_ap:
                        cable.start_ap = resolved_start_ap_id
                    if resolved_end_ap_id and not cable.end_ap:
                        cable.end_ap = resolved_end_ap_id
                    if cable.start_ap and cable.end_ap:
                        points = self._build_cable_points_from_ap_anchors(cable.start_ap, cable.end_ap)
                        if len(points) >= 2:
                            cable.geom["elec_cables"] = points
                    self._apply_kicad_sync_metadata(
                        cable.data, scan_result.project_uuid, preferred_pin, candidate, sync_key
                    )
                    self._document.element_changed.emit(existing_id)
                    updated += 1
                    first_touched_id = first_touched_id or existing_id
                    continue

                eid = self._document.new_id(ElecCable)
                cable = ElecCable.create(
                    eid,
                    floor_plan_id=default_floor_plan_id,
                    name=cable_name,
                    color="#ffb300",
                    visible=True,
                    label_visible=True,
                    label_size=12.0,
                    type=cable_type,
                    comment="",
                    start_ap=resolved_start_ap_id,
                    end_ap=resolved_end_ap_id,
                )
                if cable.start_ap and cable.end_ap:
                    points = self._build_cable_points_from_ap_anchors(cable.start_ap, cable.end_ap)
                    if len(points) >= 2:
                        cable.geom["elec_cables"] = points
                self._apply_kicad_sync_metadata(
                    cable.data, scan_result.project_uuid, preferred_pin, candidate, sync_key
                )
                self._document.add(cable)
                self.canvas.register_element(eid)
                existing_by_sync_key[sync_key] = eid
                created += 1
                first_touched_id = first_touched_id or eid
                continue

            # Try KBL bus candidate
            kbl_candidate = scan_result.kbl_bus_candidates.get(key)
            if kbl_candidate:
                sync_key = build_kicad_bus_cable_key(scan_result.project_uuid, kbl_candidate)
                preview = preview_by_key.get(key)
                # Name and type may have been edited by the user in the dialog
                cable_name = str(getattr(preview, "cable_name", "") or "").strip() or (
                    kbl_candidate.base_name or kbl_candidate.group_name_raw
                )
                cable_type = str(getattr(preview, "cable_type", "") or "").strip() or (
                    kbl_candidate.normalized_spec or kbl_candidate.spec_raw
                )
                resolved_start_ap_id = str(getattr(preview, "start_ap_id", "") or "")
                resolved_end_ap_id = str(getattr(preview, "end_ap_id", "") or "")
                existing_id = existing_by_sync_key.get(sync_key)

                if existing_id:
                    cable = self._document.elements["elec_cables"].get(existing_id)
                    if cable is None:
                        skipped += 1
                        continue
                    cable.name = cable_name
                    cable.data["type"] = cable_type
                    if resolved_start_ap_id:
                        cable.start_ap = resolved_start_ap_id
                    if resolved_end_ap_id:
                        cable.end_ap = resolved_end_ap_id
                    if cable.start_ap and cable.end_ap:
                        points = self._build_cable_points_from_ap_anchors(cable.start_ap, cable.end_ap)
                        if len(points) >= 2:
                            cable.geom["elec_cables"] = points
                    self._apply_kicad_bus_sync_metadata(cable.data, scan_result.project_uuid, kbl_candidate, sync_key)
                    self._document.element_changed.emit(existing_id)
                    updated += 1
                    first_touched_id = first_touched_id or existing_id
                    continue

                eid = self._document.new_id(ElecCable)
                cable = ElecCable.create(
                    eid,
                    floor_plan_id=default_floor_plan_id,
                    name=cable_name,
                    color="#ffb300",
                    visible=True,
                    label_visible=True,
                    label_size=12.0,
                    type=cable_type,
                    comment="",
                    start_ap=resolved_start_ap_id,
                    end_ap=resolved_end_ap_id,
                )
                if cable.start_ap and cable.end_ap:
                    points = self._build_cable_points_from_ap_anchors(cable.start_ap, cable.end_ap)
                    if len(points) >= 2:
                        cable.geom["elec_cables"] = points
                self._apply_kicad_bus_sync_metadata(cable.data, scan_result.project_uuid, kbl_candidate, sync_key)
                self._document.add(cable)
                self.canvas.register_element(eid)
                existing_by_sync_key[sync_key] = eid
                created += 1
                first_touched_id = first_touched_id or eid
                continue

            # Try text field candidate
            tf_candidate = scan_result.textfield_candidates.get(key)
            if tf_candidate:
                sync_key = f"{scan_result.project_uuid}::text_field::{tf_candidate.cable_name}"
                cable_type = tf_candidate.matched_spec
                cable_name = tf_candidate.cable_name
                text_ap_id, text_floor_plan_id = ap_assignments.get(key, ("", ""))
                cable_floor_plan_id = text_floor_plan_id or default_floor_plan_id

                existing_id = existing_by_sync_key.get(sync_key)
                if existing_id:
                    cable = self._document.elements["elec_cables"].get(existing_id)
                    if cable is None:
                        skipped += 1
                        continue
                    cable.name = cable_name
                    cable.data["type"] = cable_type
                    cable.data["kicad_cable_key"] = sync_key
                    if cable_floor_plan_id:
                        cable.floor_plan_id = cable_floor_plan_id
                    if text_ap_id and not cable.start_ap and not cable.end_ap:
                        cable.start_ap = text_ap_id
                    self._document.element_changed.emit(existing_id)
                    updated += 1
                    first_touched_id = first_touched_id or existing_id
                    continue

                eid = self._document.new_id(ElecCable)
                cable = ElecCable.create(
                    eid,
                    floor_plan_id=cable_floor_plan_id,
                    name=cable_name,
                    color="#ffb300",
                    visible=True,
                    label_visible=True,
                    label_size=12.0,
                    type=cable_type,
                    comment="",
                    start_ap=text_ap_id,
                    end_ap="",
                )
                cable.data["kicad_cable_key"] = sync_key
                self._document.add(cable)
                self.canvas.register_element(eid)
                existing_by_sync_key[sync_key] = eid
                created += 1
                first_touched_id = first_touched_id or eid
                continue

            # Key not found in either candidates or textfield_candidates
            skipped += 1

        if created or updated or ap_created or ap_reused:
            self._emit_structure_changed()
            if first_touched_id:
                self.navigator.select(first_touched_id)
                self.properties.show_element(first_touched_id)
                self.canvas.set_selected_item(first_touched_id)
            self._mark_dirty()

        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "ap_created": ap_created,
            "ap_reused": ap_reused,
        }

    def _kicad_existing_cables_payload(self) -> list[dict[str, str]]:
        return [
            {
                "id": cable_id,
                "name": str(cable.name or ""),
                "type": str(cable.data.get("type", "") or ""),
                "kicad_cable_key": str(cable.kicad_cable_key or ""),
            }
            for cable_id, cable in self._document.elements["elec_cables"].items()
        ]

    def _kicad_elec_points_payload(self, extra_points: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
        points = [
            {
                "id": point_id,
                "name": str(point.name or ""),
                "floor_plan_id": str(point.floor_plan_id or ""),
            }
            for point_id, point in self._document.elements["elec_points"].items()
        ]
        if extra_points:
            points.extend(extra_points)
        return points

    def _kicad_elec_point_choices(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        for point_id, point in self._document.elements["elec_points"].items():
            point_name = str(point.name or "").strip() or point_id
            floor_plan_id = str(point.floor_plan_id or "").strip()
            if floor_plan_id:
                label = f"{point_name} [{floor_plan_id}] ({point_id})"
            else:
                label = f"{point_name} ({point_id})"
            choices.append((point_id, label))
        choices.sort(key=lambda item: item[1].lower())
        return choices

    @staticmethod
    def _build_kicad_ap_phase_previews(previews: list[object]) -> list[object]:
        return [
            preview for preview in previews if getattr(preview, "source", "") == "ap_group"
        ]

    def _kicad_elec_point_choices_with_rooms(self) -> list[tuple[str, str]]:
        room_names: dict[str, str] = {
            rid: str(room.name or rid)
            for rid, room in self._document.elements["elec_rooms"].items()
        }
        choices: list[tuple[str, str]] = []
        for point_id, point in self._document.elements["elec_points"].items():
            point_name = str(point.name or "").strip() or point_id
            room_id = self._resolve_existing_ap_room_id(point)
            room_name = room_names.get(room_id, room_id) if room_id else ""
            label = (
                f"{room_name} / {point_name} ({point_id})"
                if room_name
                else f"(kein Raum) / {point_name} ({point_id})"
            )
            choices.append((point_id, label))
        choices.sort(key=lambda item: item[1].lower())
        return choices

    def _kicad_existing_cable_types(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for cable in self._document.elements["elec_cables"].values():
            t = str(cable.data.get("type", "") or "").strip()
            if t and t not in seen:
                seen.add(t)
                result.append(t)
        return sorted(result)

    def _apply_kicad_ap_import(
        self,
        scan_result: KiCadScanResult,
        selected_ap_keys: set[str],
        ap_assignments: dict[str, dict[str, str]],
    ) -> dict[str, object]:
        created = 0
        reused = 0
        approved_ap_names: set[str] = set()
        first_touched_id = ""

        for key in selected_ap_keys:
            ap_group_candidate = scan_result.ap_group_candidates.get(key)
            if ap_group_candidate is not None:
                ap_name = self._ap_import_name_from_group_name(ap_group_candidate.group_name)
                assignment = ap_assignments.get(key, {})
                selected_floor_plan_id = str(assignment.get("floor_plan_id", "") or "")
                selected_room_id = str(assignment.get("room_id", "") or "")
                frame_bounds = ap_group_candidate.frame_bounds
                center_x = (frame_bounds[0] + frame_bounds[2]) / 2.0
                center_y = (frame_bounds[1] + frame_bounds[3]) / 2.0
                ap_id, _floor_plan_id, status = self._resolve_or_create_ap_by_name(
                    ap_name,
                    selected_floor_plan_id or self._active_floorplan_id(),
                    [center_x, center_y],
                    room_id=selected_room_id,
                    kicad_sync={
                        "kicad_project_uuid": str(scan_result.project_uuid or ""),
                        "kicad_group_uuid": str(ap_group_candidate.group_uuid or ""),
                        "kicad_frame_uuid": str(ap_group_candidate.frame_uuid or ""),
                        "kicad_sheet_path": str(
                            ap_group_candidate.bus_hits[0].sheet_file if ap_group_candidate.bus_hits else ""
                        ),
                        "kicad_last_import_hash": "",
                        "kicad_last_imported_at": "",
                    },
                )
                if status == "created":
                    created += 1
                elif status == "reused":
                    reused += 1
                if ap_id:
                    approved_ap_names.add(str(ap_name or "").strip().casefold())
                    first_touched_id = first_touched_id or ap_id
                continue

            tf_candidate = scan_result.textfield_candidates.get(key)
            if tf_candidate is not None:
                assignment = ap_assignments.get(key, {})
                ap_id, _floor_plan_id, status = self._resolve_or_create_textfield_ap(
                    tf_candidate,
                    selected_floor_plan_id=str(assignment.get("floor_plan_id", "") or ""),
                    selected_room_id=str(assignment.get("room_id", "") or ""),
                )
                if status == "created":
                    created += 1
                elif status == "reused":
                    reused += 1
                if ap_id:
                    approved_ap_names.add(str(tf_candidate.cable_name or "").strip().casefold())
                    first_touched_id = first_touched_id or ap_id
                continue

        if created or reused:
            self._emit_structure_changed()
            if first_touched_id:
                self.navigator.select(first_touched_id)
                self.properties.show_element(first_touched_id)
                self.canvas.set_selected_item(first_touched_id)
            self._mark_dirty()

        return {
            "created": created,
            "reused": reused,
            "approved_ap_names": approved_ap_names,
        }

    def _build_initial_textfield_ap_assignments(
        self,
        scan_result: KiCadScanResult,
        ap_previews: list[object],
    ) -> dict[str, dict[str, str]]:
        assignments: dict[str, dict[str, str]] = {}
        room_choices_by_floorplan = self._collect_room_choices_by_floorplan()
        for preview in ap_previews:
            key = str(getattr(preview, "candidate_key", "") or "")
            ap_group_candidate = scan_result.ap_group_candidates.get(key)
            if ap_group_candidate is not None:
                floor_plan_id = self._active_floorplan_id()
                room_id = ""

                existing_ap = self._find_existing_ap_by_kicad_group_uuid(
                    {
                        "kicad_project_uuid": str(scan_result.project_uuid or ""),
                        "kicad_group_uuid": str(ap_group_candidate.group_uuid or ""),
                    }
                )
                if existing_ap is None:
                    existing_ap = self._find_existing_ap_by_name(str(ap_group_candidate.group_name or ""))
                if existing_ap is not None:
                    floor_plan_id = str(existing_ap.floor_plan_id or floor_plan_id or "")
                    room_id = self._resolve_existing_ap_room_id(existing_ap)

                if not floor_plan_id:
                    floor_plan_id = self._active_floorplan_id()
                assignments[key] = {
                    "floor_plan_id": floor_plan_id,
                    "room_id": room_id,
                    "room_locked": existing_ap is not None,
                }
                continue

            tf_candidate = scan_result.textfield_candidates.get(str(getattr(preview, "candidate_key", "") or ""))
            if tf_candidate is None:
                continue
            source_metadata = tf_candidate.source_metadata
            floor_plan_id = str(source_metadata.floor_plan_id or "")
            if not floor_plan_id:
                text_elem = self._document.elements.get("text_annotations", {}).get(source_metadata.text_id)
                floor_plan_id = str(getattr(text_elem, "floor_plan_id", "") or "") if text_elem else ""
            room_id = self._find_room_id_by_name(str(source_metadata.room or ""), floor_plan_id)
            if not floor_plan_id and room_id:
                room = self._document.elements["elec_rooms"].get(room_id)
                floor_plan_id = str(getattr(room, "floor_plan_id", "") or "") if room else ""
            if not floor_plan_id:
                floor_plan_id = self._active_floorplan_id()
            if not room_id and floor_plan_id:
                choices = room_choices_by_floorplan.get(floor_plan_id, [])
                if len(choices) == 1:
                    room_id = choices[0][0]
            assignments[key] = {
                "floor_plan_id": floor_plan_id,
                "room_id": room_id,
            }
        return assignments

    def _resolve_existing_ap_room_id(self, point: ElecPoint) -> str:
        # Prefer the active navigator mapping first.
        room_map = getattr(self, "_elec_point_room_map", None)
        if isinstance(room_map, dict):
            mapped_room_id = str(room_map.get(point.id, "") or "").strip()
            if mapped_room_id:
                return mapped_room_id

        explicit_room_id = str(point.data.get("room_id", "") or "").strip()
        if explicit_room_id:
            return explicit_room_id

        return self._find_room_id_for_point(point)

    def _find_existing_ap_by_name(self, ap_name: str) -> ElecPoint | None:
        target = self._normalize_ap_lookup_name(ap_name)
        if not target:
            return None
        matches: list[ElecPoint] = []
        for point in self._document.elements["elec_points"].values():
            point_name = self._normalize_ap_lookup_name(point.name)
            if point_name == target:
                matches.append(point)
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _normalize_ap_lookup_name(value: object) -> str:
        text = str(value or "").strip()
        if text[:3].upper() == "AP_":
            text = text[3:]
        return text.casefold()

    def _find_room_id_for_point(self, point: ElecPoint) -> str:
        pos = getattr(point, "pos", None)
        if not (isinstance(pos, (list, tuple)) and len(pos) >= 2):
            return ""
        floor_plan_id = str(point.floor_plan_id or "")
        point_xy = [float(pos[0]), float(pos[1])]
        for room_id, room in self._document.elements["elec_rooms"].items():
            if floor_plan_id and str(room.floor_plan_id or "") != floor_plan_id:
                continue
            polygon = room.geom.get("elec_rooms") or room.geom.get("elec_room_polygons")
            if isinstance(polygon, list) and self._poly_contains(point_xy, polygon):
                return room_id
        return ""

    def _resolve_or_create_textfield_ap(
        self,
        tf_candidate: object,
        selected_floor_plan_id: str = "",
        selected_room_id: str = "",
    ) -> tuple[str, str, str]:
        source_metadata = getattr(tf_candidate, "source_metadata", None)
        if source_metadata is None:
            return "", "", "none"

        ap_name = str(getattr(source_metadata, "ap_name", "") or "").strip()
        text_id = str(getattr(source_metadata, "text_id", "") or "").strip()
        if not ap_name:
            return "", "", "none"

        text_elem = self._document.elements.get("text_annotations", {}).get(text_id)
        floor_plan_id = selected_floor_plan_id or (
            str(getattr(text_elem, "floor_plan_id", "") or "") if text_elem else ""
        )
        if not floor_plan_id and selected_room_id:
            room = self._document.elements["elec_rooms"].get(selected_room_id)
            floor_plan_id = str(getattr(room, "floor_plan_id", "") or "") if room else ""
        if not floor_plan_id:
            floor_plan_id = self._active_floorplan_id()

        return self._resolve_or_create_ap_by_name(
            ap_name,
            floor_plan_id,
            getattr(text_elem, "pos", None),
            room_id=selected_room_id,
        )

    def _resolve_existing_textfield_ap(self, tf_candidate: object) -> tuple[str, str]:
        source_metadata = getattr(tf_candidate, "source_metadata", None)
        if source_metadata is None:
            return "", ""
        ap_name = str(getattr(source_metadata, "ap_name", "") or "").strip()
        text_id = str(getattr(source_metadata, "text_id", "") or "").strip()
        if not ap_name:
            return "", ""
        text_elem = self._document.elements.get("text_annotations", {}).get(text_id)
        floor_plan_id = str(getattr(text_elem, "floor_plan_id", "") or "") if text_elem else ""
        matches = self._find_existing_ap_matches(ap_name, floor_plan_id)
        if matches[0]:
            return matches[0][0].id, floor_plan_id
        if matches[1]:
            point = matches[1][0]
            return point.id, str(point.floor_plan_id or floor_plan_id or "")
        return "", floor_plan_id

    def _find_existing_ap_matches(self, ap_name: str, floor_plan_id: str) -> tuple[list[ElecPoint], list[ElecPoint]]:
        candidate_name = self._normalize_ap_lookup_name(ap_name)
        exact_matches: list[ElecPoint] = []
        same_floorplan_matches: list[ElecPoint] = []
        for point in self._document.elements["elec_points"].values():
            point_name = self._normalize_ap_lookup_name(point.name)
            if not point_name or point_name != candidate_name:
                continue
            exact_matches.append(point)
            if floor_plan_id and str(point.floor_plan_id or "") == floor_plan_id:
                same_floorplan_matches.append(point)
        return same_floorplan_matches, exact_matches

    @staticmethod
    def _ap_import_name_from_group_name(group_name: str) -> str:
        text = str(group_name or "").strip()
        if text[:3].upper() == "AP_":
            rest = text[3:]
            # Keep AP_ for technical ID-style names like AP_1 / AP_1_NEU.
            if rest[:1].isdigit():
                return text
            return rest
        return text

    def _resolve_or_create_ap_by_name(
        self,
        ap_name: str,
        floor_plan_id: str,
        pos: object,
        room_id: str = "",
        kicad_sync: dict[str, str] | None = None,
    ) -> tuple[str, str, str]:
        if kicad_sync:
            existing_by_uuid = self._find_existing_ap_by_kicad_group_uuid(kicad_sync)
            if existing_by_uuid is not None:
                if ap_name and str(existing_by_uuid.name or "") != ap_name:
                    existing_by_uuid.name = ap_name
                if room_id:
                    existing_by_uuid.data["room_id"] = room_id
                self._apply_kicad_ap_sync_metadata(existing_by_uuid.data, kicad_sync)
                self._document.element_changed.emit(existing_by_uuid.id)
                return existing_by_uuid.id, str(existing_by_uuid.floor_plan_id or floor_plan_id or ""), "reused"

        same_floorplan_matches, exact_matches = self._find_existing_ap_matches(ap_name, floor_plan_id)
        if same_floorplan_matches:
            if kicad_sync:
                if room_id:
                    same_floorplan_matches[0].data["room_id"] = room_id
                self._apply_kicad_ap_sync_metadata(same_floorplan_matches[0].data, kicad_sync)
                self._document.element_changed.emit(same_floorplan_matches[0].id)
            elif room_id:
                same_floorplan_matches[0].data["room_id"] = room_id
                self._document.element_changed.emit(same_floorplan_matches[0].id)
            return same_floorplan_matches[0].id, floor_plan_id, "reused"
        if exact_matches:
            if kicad_sync:
                if room_id:
                    exact_matches[0].data["room_id"] = room_id
                self._apply_kicad_ap_sync_metadata(exact_matches[0].data, kicad_sync)
                self._document.element_changed.emit(exact_matches[0].id)
            elif room_id:
                exact_matches[0].data["room_id"] = room_id
                self._document.element_changed.emit(exact_matches[0].id)
            return exact_matches[0].id, str(exact_matches[0].floor_plan_id or floor_plan_id or ""), "reused"

        from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415

        point_id = self._document.new_id(ElecPoint)
        point = ElecPoint.create(
            point_id,
            floor_plan_id=floor_plan_id,
            name=ap_name,
            color="#4fc3f7",
            width=30.0,
            height=30.0,
            icon_path=str(BUILTIN_SYMBOLS.get("Steckdose", "") or ""),
            builtin_symbol="Steckdose",
            visible=True,
            label_visible=True,
            label_size=12.0,
            position="Wand",
            height_from_floor=30.0,
            smarthome_device="",
            smarthome_device_color="",
            note="",
            ap_type="standard",
            uv_config={},
            up_distribution_config={},
            hak_config={},
            zaehler_config={},
        )

        room_centroid = self._room_centroid(room_id)
        if room_centroid is not None:
            point.geom["elec_points"] = room_centroid
        elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
            point.geom["elec_points"] = [float(pos[0]), float(pos[1])]
        else:
            point.geom["elec_points"] = [0.0, 0.0]
        point.geom["elec_point_size_px"] = [30.0, 30.0]
        point.geom["elec_visible"] = True
        point.data["room_id"] = room_id
        if kicad_sync:
            self._apply_kicad_ap_sync_metadata(point.data, kicad_sync)

        self._document.add(point)
        self.canvas.register_element(point_id, True)
        self.canvas.set_elec_point_icon(point_id, point.data.get("icon_path", ""))
        self.canvas.set_color(point_id, point.color)
        self._document.element_changed.emit(point_id)
        return point_id, floor_plan_id, "created"

    def _find_existing_ap_by_kicad_group_uuid(self, sync: dict[str, str]) -> ElecPoint | None:
        project_uuid = str(sync.get("kicad_project_uuid", "") or "").strip()
        group_uuid = str(sync.get("kicad_group_uuid", "") or "").strip()
        if not project_uuid or not group_uuid:
            return None

        for point in self._document.elements["elec_points"].values():
            point_project_uuid = str(point.data.get("kicad_project_uuid", "") or "").strip()
            point_group_uuid = str(point.data.get("kicad_group_uuid", "") or "").strip()
            if point_project_uuid == project_uuid and point_group_uuid == group_uuid:
                return point
        return None

    def _find_room_id_by_name(self, room_name: str, floor_plan_id: str) -> str:
        room_name_norm = str(room_name or "").strip().casefold()
        if not room_name_norm:
            return ""
        for room_id, room in self._document.elements["elec_rooms"].items():
            if floor_plan_id and str(room.floor_plan_id or "") != floor_plan_id:
                continue
            if str(room.name or "").strip().casefold() == room_name_norm:
                return room_id
        return ""

    def _room_centroid(self, room_id: str) -> list[float] | None:
        if not room_id:
            return None
        room = self._document.elements["elec_rooms"].get(room_id)
        if room is None:
            return None
        polygon = room.geom.get("elec_rooms") or room.geom.get("elec_room_polygons")
        if not isinstance(polygon, list) or not polygon:
            return None
        cx = sum(float(point[0]) for point in polygon) / len(polygon)
        cy = sum(float(point[1]) for point in polygon) / len(polygon)
        return [cx, cy]

    @staticmethod
    def _apply_kicad_ap_sync_metadata(data: dict, sync: dict[str, str]) -> None:
        data["kicad_project_uuid"] = str(sync.get("kicad_project_uuid", "") or "")
        data["kicad_group_uuid"] = str(sync.get("kicad_group_uuid", "") or "")
        data["kicad_frame_uuid"] = str(sync.get("kicad_frame_uuid", "") or "")
        data["kicad_sheet_path"] = str(sync.get("kicad_sheet_path", "") or "")
        data["kicad_last_import_hash"] = str(sync.get("kicad_last_import_hash", "") or "")
        data["kicad_last_imported_at"] = str(sync.get("kicad_last_imported_at", "") or "")

    def _resolve_sheet_candidate_ap_pair(self, candidate: KiCadCableCandidate) -> tuple[str, str]:
        matches = suggest_ap_matches(candidate, self._kicad_elec_points_payload())
        if not matches:
            return "", ""
        top = matches[0]
        if len(matches) == 1:
            return top.point_id, ""
        if top.score >= 90 and top.score - matches[1].score >= 15:
            return top.point_id, ""
        second = matches[1]
        if top.point_id != second.point_id:
            return top.point_id, second.point_id
        return top.point_id, ""

    def _build_cable_points_from_ap_anchors(self, start_ap_id: str, end_ap_id: str) -> list[list[float]]:
        if not start_ap_id or not end_ap_id:
            return []
        start_ap = self._document.elements["elec_points"].get(start_ap_id)
        end_ap = self._document.elements["elec_points"].get(end_ap_id)
        if start_ap is None or end_ap is None:
            return []
        start_pos = getattr(start_ap, "pos", None)
        end_pos = getattr(end_ap, "pos", None)
        if not (
            isinstance(start_pos, (list, tuple))
            and len(start_pos) >= 2
            and isinstance(end_pos, (list, tuple))
            and len(end_pos) >= 2
        ):
            return []
        return [
            [float(start_pos[0]), float(start_pos[1])],
            [float(end_pos[0]), float(end_pos[1])],
        ]

    @staticmethod
    def _preferred_kicad_pin_ref(candidate: KiCadCableCandidate) -> KiCadSheetPinRef | None:
        for ref in candidate.pin_refs:
            if ref.pin_direction == "output":
                return ref
        return candidate.pin_refs[0] if candidate.pin_refs else None

    @staticmethod
    def _build_kicad_cable_key(project_uuid: str, candidate: KiCadCableCandidate) -> str:
        preferred = AppWindow._preferred_kicad_pin_ref(candidate)
        if preferred is None:
            return f"{project_uuid}::{candidate.pin_name_raw}"
        return f"{project_uuid}::{preferred.sheet_uuid}::{candidate.pin_name_raw}"

    @staticmethod
    def _apply_kicad_sync_metadata(
        data: dict,
        project_uuid: str,
        preferred_pin: KiCadSheetPinRef | None,
        candidate: KiCadCableCandidate,
        sync_key: str,
    ) -> None:
        data["kicad_project_uuid"] = project_uuid
        data["kicad_sheet_uuid"] = preferred_pin.sheet_uuid if preferred_pin else ""
        data["kicad_pin_uuid"] = preferred_pin.pin_uuid if preferred_pin else ""
        data["kicad_pin_name"] = candidate.pin_name_raw
        data["kicad_sheet_path"] = preferred_pin.sheet_file if preferred_pin else ""
        data["kicad_cable_key"] = sync_key
        data["kicad_last_import_hash"] = ""
        data["kicad_last_imported_at"] = ""

    @staticmethod
    def _apply_kicad_bus_sync_metadata(
        data: dict,
        project_uuid: str,
        candidate: KiCadBusCableCandidate,
        sync_key: str,
    ) -> None:
        data["kicad_project_uuid"] = project_uuid
        data["kicad_group_uuid"] = candidate.group_uuid
        data["kicad_bus_uuid"] = candidate.bus_uuid
        data["kicad_sheet_path"] = candidate.sheet_file
        data["kicad_pin_name"] = candidate.group_name_raw
        data["kicad_cable_key"] = sync_key
        data["kicad_last_import_hash"] = ""
        data["kicad_last_imported_at"] = ""

    def _show_about(self) -> None:
        QMessageBox.about(self, "Über HRouting", "HRouting – Fußbodenheizung und Kabel Planer")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _restore_layout(self) -> None:
        if not layout_store.restore_geometry(self):
            self.resize(1500, 950)

    def _reset_layout(self) -> None:
        layout_store.reset_layout()
        for dock_id, dock in self._docks.items():
            dock.setFloating(False)
            dock.setVisible(dock_id in self._workspace.default_docks)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.tools)
        self.addDockWidget(Qt.RightDockWidgetArea, self.navigator)
        self.addDockWidget(Qt.RightDockWidgetArea, self.properties)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log)
        self.resize(1500, 950)
        self.statusBar().showMessage("Layout zurückgesetzt", 3000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-API
        if not self._confirm_discard():
            event.ignore()
            return
        layout_store.save_workspace_state(self, self._workspace.id)
        layout_store.save_geometry(self)
        layout_store.save_last_workspace(self._workspace.id)
        event.accept()

