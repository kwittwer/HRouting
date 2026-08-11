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

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer
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
from model.elements import (
    AngleMeasurement,
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
from storage.hrp_io import load_document, repair_and_save_hrp, save_document
from .elec_schema_window import ApNode, CableEdge, ElecSchemaWindow
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
        self.navigator.element_selected.connect(self._on_element_selected)
        self.navigator.floorplan_activated.connect(self._on_floorplan_activated)
        self.navigator.visibility_changed.connect(self._on_visibility_changed)
        self.navigator.context_requested.connect(self._on_navigator_context)
        self.navigator.reassign_floorplan.connect(self._on_reassign_floorplan)
        self.tools.tool_activated.connect(self._on_tool_activated)
        self.canvas.object_clicked.connect(self._on_canvas_object_clicked)
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
        self.canvas.multi_objects_moved.connect(self._on_canvas_mutation_signal)
        self.canvas.will_move_multi_objects.connect(self._push_undo)
        self.canvas.ref_line_set.connect(self._on_ref_line_set)
        self.canvas.route_changed.connect(self._on_route_changed)
        self.canvas.supply_line_changed.connect(self._on_supply_line_changed)
        self.canvas.hkv_line_changed.connect(self._on_hkv_line_changed)
        self.properties.field_changed.connect(self._on_property_changed)
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
        self._apply_property_side_effects(element_id, key)
        self._document.element_changed.emit(element_id)
        self.canvas.update()
        self._refresh_schema_windows()
        self._mark_dirty()

    def _apply_property_side_effects(self, element_id: str, key: str) -> None:
        """Sorgt dafür, dass Änderungen sofort im Canvas sichtbar werden."""
        element = self._document.get(element_id)
        if element is None:
            return

        if key == "color" and element_id not in self._document.floorplans:
            color_value = str(element.data.get("color") or "").strip()
            if color_value:
                self.canvas.set_color(element_id, QColor(color_value))

        if key == "name":
            name = str(element.data.get("name") or "").strip()
            self.canvas._label_map[element_id] = name if name else element_id
            self.canvas.update()
            self.navigator.set_document(self._document)

        if key in ("builtin_symbol", "icon_path") and element_id in self._document.elements.get("elec_points", {}):
            from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415
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
                self.canvas.update()

        if key == "visible":
            self.canvas.set_element_visible(element_id, bool(element.visible))
        elif key in ("width", "height") and element_id in self.canvas._elec_points:
            self.canvas.update_elec_point_size(
                element_id, float(element.data.get("width", 30.0)),
                float(element.data.get("height", 30.0))
            )
        elif key == "file_path" and element_id in self._document.floorplans:
            path = (element.data.get("file_path") or "").strip()
            if path:
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
        """Synchronisiert Property-Ansicht und Undo bei Drag-Transformationen."""
        # Keep per-floor scale synchronized with the current reference line.
        self._recompute_floorplan_scale_from_reference(fp_id)
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
        """Berechnet ``mm_per_px`` aus Referenzlinie und Referenzlänge."""
        layer = self.canvas._floor_plans.get(fp_id)
        floor = self._document.floorplans.get(fp_id)
        if layer is None or floor is None:
            return False
        if layer.ref_p1 is None or layer.ref_p2 is None:
            return False

        px_len = math.hypot(layer.ref_p2.x() - layer.ref_p1.x(), layer.ref_p2.y() - layer.ref_p1.y())
        if px_len <= 1e-9:
            return False

        ref_length_mm = float(layer.ref_length_mm or floor.data.get("ref_length_mm", 0.0) or 0.0)
        if ref_length_mm <= 0.0:
            return False

        old_global_mpp = float(self.canvas._mm_per_px or 1.0)
        old_layer_mpp = float(layer.mm_per_px or old_global_mpp)
        old_render_size = self.canvas._layer_render_size_for_scale(
            layer,
            old_global_mpp,
            layer_mm_per_px=old_layer_mpp,
        )
        new_mpp = ref_length_mm / px_len

        # Globaler Bildschirmmaßstab bleibt konstant – nur Layer skaliert.
        # Die Referenzlinie wird mit dem Bild mit-skaliert (remap).
        new_render_size = self.canvas._layer_render_size_for_scale(
            layer,
            old_global_mpp,
            layer_mm_per_px=new_mpp,
        )

        # Referenzlinie mit dem Bild mitumrechnen: von alter zu neuer Render-Größe
        self.canvas.remap_layer_ref_points(fp_id, old_render_size, new_render_size)

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
        if tool_id == "furn.polygon":
            target_id = self._selected_floor_like_id()
            if not target_id:
                self.statusBar().showMessage("Kein Einrichtungselement ausgewählt", 2500)
                return
            self.canvas.start_draw_floor_plan_polygon(target_id)
            self.statusBar().showMessage(tool.tooltip or tool.label, 3000)
            return
        if tool_id == "ann.text":
            self._add_text()
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

        mode = getattr(ToolMode, tool.tool_mode, ToolMode.NONE)
        self.canvas.set_tool_mode(mode)
        self.statusBar().showMessage(tool.tooltip or tool.label, 3000)

    # ------------------------------------------------------------------
    # Dokument
    # ------------------------------------------------------------------
    def _set_document(self, document: Document) -> None:
        self._document = document
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
            "draw_line": lambda eid: self.canvas.start_draw_hkv_line(eid),
            "edit_line": lambda eid: self.canvas.start_edit_hkv_line(eid),
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

    def _action_edit_polygon(self, element_id: str) -> None:
        self.canvas.start_edit_polygon(element_id)

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
            prefix, idx = meas_ref
            kind_label = "Distanzmessung" if prefix == "MSRD" else "Winkelmessung"
            answer = QMessageBox.question(
                self,
                f"{kind_label} löschen",
                f"{kind_label} '{element_id}' wirklich löschen?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            self._push_undo()
            if prefix == "MSRD":
                self.canvas.delete_measurement_at(idx)
            else:
                self.canvas.delete_angle_measurement_at(idx)
            # measure_changed wurde bereits von der canvas-Methode emittiert;
            # _on_measure_changed synchronisiert die Elements und triggert den Navigator.
            self._mark_dirty()
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
        if element_id:
            self.navigator.select(element_id)
            self.canvas.set_selected_item(element_id)
            if self._document.get(element_id) is not None:
                self.properties.show_element(element_id)

        if action_id == "activate":
            self._on_floorplan_activated(element_id)
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

    def _on_canvas_object_clicked(self, obj_type: str, obj_id: str) -> None:
        if not obj_id:
            return
        if obj_type == "helper_line":
            helper_floor = self.canvas._helper_selected_floor_id or self._active_floorplan_id()
            obj_id = f"{_HELPER_NAV_ID_PREFIX}{helper_floor}::{obj_id}"
        self._select_element_everywhere(obj_id, update_navigator=True)

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
            self, "Als SVG exportieren", "heizplan.svg", "SVG (*.svg)"
        )
        if not path:
            return
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

    def _normalize_pdf_export_pages(self, pages: list[dict] | None) -> list[dict]:
        if not pages:
            return self._default_pdf_export_pages()
        normalized: list[dict] = []
        for index, src in enumerate(pages):
            if not isinstance(src, dict):
                continue
            ptype = str(src.get("type", "plan")).strip().lower()
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
                vis = self._default_pdf_element_visibility()
                vis_src = src.get("element_visibility")
                if isinstance(vis_src, dict):
                    for key in vis:
                        vis[key] = bool(vis_src.get(key, vis[key]))
                page["element_visibility"] = vis
                page["table_sections"] = list(src.get("table_sections") or self._default_pdf_table_sections(ptype))
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

    def _open_pdf_export_config_dialog(self) -> list[dict] | None:
        dialog = PdfExportConfigDialog(
            pages=self._normalize_pdf_export_pages(self._pdf_export_pages),
            floor_plans=self._current_floor_plans_for_export_dialog(),
            svg_size=self.canvas._svg_size,
            canvas=self.canvas,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return None
        return self._normalize_pdf_export_pages(dialog.get_pages())

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
        for fid in self.canvas._floor_plan_order:
            layer = self.canvas._floor_plans.get(fid)
            if layer is None:
                continue
            layer.visible = bool(vis.get("background", True))

        show_hk = bool(vis.get("hk", True))
        for cid in list(self.canvas._polygons) + list(self.canvas._manual_routes) + list(self.canvas._supply_lines):
            self.canvas._circuit_visible[cid] = show_hk

        show_hkv = bool(vis.get("hkv", True))
        for hid in self.canvas._hkv_points:
            self.canvas._hkv_visible[hid] = show_hkv

        show_hkv_line = bool(vis.get("hkv_line", True))
        for lid in self.canvas._hkv_lines:
            self.canvas._hkv_line_visible[lid] = show_hkv_line

        show_ap = bool(vis.get("ap", True))
        for pid in self.canvas._elec_points:
            self.canvas._elec_visible[pid] = show_ap

        show_room = bool(vis.get("room", True))
        for rid in self.canvas._elec_room_polygons:
            self.canvas._elec_room_visible[rid] = show_room

        show_kv = bool(vis.get("kv", True))
        for kid in self.canvas._elec_cables:
            self.canvas._elec_visible[kid] = show_kv

        show_text = bool(vis.get("text", True))
        for tid in self.canvas._text_annotations:
            self.canvas._text_visible[tid] = show_text

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

        title_font = QFont(painter.font())
        title_font.setPointSizeF(max(10.0, title_font.pointSizeF() + 4.0))
        painter.setFont(title_font)
        title_h = max(36.0, page_rect.height() * 0.06)
        title_rect = QRectF(page_rect.x(), page_rect.y(), page_rect.width(), title_h)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, title)
        content_rect = QRectF(page_rect.x(), title_rect.bottom() + 8.0, page_rect.width(), max(1.0, page_rect.bottom() - (title_rect.bottom() + 8.0)))
        return title_rect, content_rect

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
        body_font = QFont(painter.font())
        body_font.setPointSizeF(max(7.0, body_font.pointSizeF() - 1.0))
        painter.setFont(body_font)

        n_cols = max(1, len(headers))
        if col_widths and len(col_widths) == n_cols and sum(col_widths) > 0:
            total_w = float(sum(col_widths))
            widths = [content_rect.width() * (w / total_w) for w in col_widths]
        else:
            widths = [content_rect.width() / n_cols] * n_cols

        header_h = max(24.0, page_rect.height() * 0.036)
        min_row_h = max(20.0, page_rect.height() * 0.030)
        cell_pad = 4.0
        y = content_rect.y() + 6.0
        bottom_margin = 6.0
        fm = painter.fontMetrics()

        def draw_header(_y: float):
            x = content_rect.x()
            painter.save()
            painter.setFont(QFont(body_font.family(), body_font.pointSize() + 1, QFont.Bold))
            painter.setPen(QPen(Qt.black, 1.0))
            for idx, header in enumerate(headers):
                cell = QRectF(x, _y, widths[idx], header_h)
                painter.fillRect(cell, QBrush(QColor("#e0e0e0")))
                painter.drawRect(cell)
                painter.drawText(
                    cell.adjusted(cell_pad, cell_pad, -cell_pad, -cell_pad),
                    Qt.AlignCenter | Qt.TextWordWrap,
                    header,
                )
                x += widths[idx]
            painter.restore()

        def row_height(values: list[str]) -> float:
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
            if y + rh > content_rect.bottom() - bottom_margin:
                writer.newPage()
                page_rect2 = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
                _, content_rect2 = self._draw_pdf_title(painter, page_rect2, f"{title} (Fortsetzung)")
                content_rect = content_rect2
                y = content_rect.y() + 6.0
                if col_widths and len(col_widths) == n_cols and sum(col_widths) > 0:
                    total_w = float(sum(col_widths))
                    widths = [content_rect.width() * (w / total_w) for w in col_widths]
                else:
                    widths = [content_rect.width() / n_cols] * n_cols
                painter.setFont(body_font)
                draw_header(y)
                y += header_h

            x = content_rect.x()
            if row_index % 2 == 1:
                painter.fillRect(QRectF(content_rect.x(), y, content_rect.width(), rh), QBrush(QColor("#f5f5f5")))
            for idx, value in enumerate(data_row):
                cell = QRectF(x, y, widths[idx], rh)
                painter.drawRect(cell)
                align = (Qt.AlignRight | Qt.AlignTop) if idx >= 2 else (Qt.AlignLeft | Qt.AlignTop)
                painter.drawText(
                    cell.adjusted(cell_pad, cell_pad, -cell_pad, -cell_pad),
                    align | Qt.TextWordWrap,
                    value,
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

        page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
        _, content_rect = self._draw_pdf_title(painter, page_rect, title)
        source_rect = self._effective_pdf_source_rect(page)

        img = self.canvas.render_for_export(
            source_rect=source_rect,
            output_w=int(content_rect.width()),
            output_h=int(content_rect.height()),
        )
        painter.drawImage(content_rect, img)

        sections = set(page.get("table_sections") or [])
        if ptype == "heating":
            if "hk_lengths" in sections and hk_rows:
                writer.newPage()
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
                writer.newPage()
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
                writer.newPage()
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
                writer.newPage()
                self._draw_pdf_table(
                    painter,
                    writer,
                    "Elektro – Anschlusspunkte",
                    ["Name", "Symbol", "Position", "Höhe", "Notiz"],
                    ap_rows,
                )
            if "el_kabel" in sections and cable_rows:
                writer.newPage()
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
                    writer.newPage()
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
                    writer.newPage()
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
                    writer.newPage()
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
                    writer.newPage()
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
                    writer.newPage()
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
                        writer.newPage()
                        self._draw_pdf_table(
                            painter,
                            writer,
                            "Stückliste",
                            ["Bereich", "Artikel", "Hersteller", "Artikelnummer", "Einheit", "Menge", "Notiz"],
                            bom_rows,
                            col_widths=[1.0, 1.6, 1.1, 1.2, 0.7, 0.7, 1.2],
                        )

                if "el_uv_busbars" in sections and export_data.get("uv_busbar_bom_rows"):
                    writer.newPage()
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
                        writer.newPage()
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
                            writer.newPage()
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
                        writer.newPage()
                        self._draw_pdf_table(
                            painter,
                            writer,
                            "Schaltplan – Hierarchie",
                            ["Typ Quelle", "Quelle", "Typ Ziel", "Ziel"],
                            rows or [["", "Keine Hierarchie-Verbindungen vorhanden.", "", ""]],
                        )

    def _continue_export_pdf(self, pages: list[dict]) -> None:
        enabled_pages = [p for p in pages if p.get("enabled", True)]
        if not enabled_pages:
            QMessageBox.information(self, "PDF-Export", "Keine aktive Exportseite ausgewählt.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Als PDF exportieren", "projektbericht.pdf", "PDF (*.pdf)"
        )
        if not path:
            return

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
        try:
            export_data = self._collect_export_data()
            hk_rows = export_data.get("hk_rows", [])
            t_supply = float(export_data.get("t_supply", self._document.settings.get("t_supply", 35.0)))
            t_return = float(export_data.get("t_return", self._document.settings.get("t_return", 30.0)))
            ap_rows, cable_rows = self._collect_pdf_electro_rows()
            for idx, page in enumerate(enabled_pages):
                if progress.wasCanceled():
                    cancelled = True
                    break
                if idx > 0:
                    writer.newPage()
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
            painter.end()
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
        self._mark_dirty()
        self.log.success(f"PDF exportiert: {path}")
        self.statusBar().showMessage(f"PDF exportiert: {path}", 4000)

    def _export_pdf(self) -> None:
        pages = self._open_pdf_export_config_dialog()
        if pages is None:
            return
        self._continue_export_pdf(pages)

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
