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
from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
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
from model.elements import Circuit, ElecPoint, ElecRoom, ElecCable, FloorPlan, Hkv, HkvLine, TextAnnotation
from storage.hrp_io import load_document, save_document
from .elec_schema_window import ApNode, CableEdge, ElecSchemaWindow
from .schaltplan_window import SchaltplanWindow

from . import layout_store
from .canvas_widget import CanvasWidget, ToolMode
from .docks import LogDock, NavigatorDock, PropertiesDock, ToolsDock
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

        self.addDockWidget(Qt.LeftDockWidgetArea, self.tools)
        self.addDockWidget(Qt.RightDockWidgetArea, self.navigator)
        self.addDockWidget(Qt.RightDockWidgetArea, self.properties)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log)
        self.log.hide()

        self._docks = {
            DockId.NAVIGATOR: self.navigator,
            DockId.PROPERTIES: self.properties,
            DockId.TOOLS: self.tools,
            DockId.LOG: self.log,
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
        self._add_action(file_menu, "Beenden", self.close, QKeySequence.Quit)

        edit_menu = bar.addMenu("&Bearbeiten")
        self._undo_action = self._add_action(edit_menu, "Rückgängig", self._undo, QKeySequence.Undo)
        self._redo_action = self._add_action(edit_menu, "Wiederherstellen", self._redo, QKeySequence.Redo)
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Kopieren", self._copy_selected, QKeySequence.Copy)
        self._add_action(edit_menu, "Einfügen", self._paste_copied, QKeySequence.Paste)
        self._add_action(edit_menu, "Duplizieren", self._duplicate_selected, QKeySequence("Ctrl+D"))
        self._add_action(edit_menu, "Löschen", self._delete_selected, QKeySequence.Delete)
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Anschlusspunkte durchnummerieren", self._renumber_elec_points)

        add_menu = bar.addMenu("&Einfügen")
        self._add_action(add_menu, "Grundriss hinzufügen…", self._add_floorplan)
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
        self._add_action(self.export_menu, "KiCad exportieren…", self._export_kicad)
        self._add_action(self.export_menu, "QElectroTech exportieren…", self._export_qet)
        self.export_menu.addSeparator()
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
        self.tools.tool_activated.connect(self._on_tool_activated)
        self.canvas.object_clicked.connect(self._on_canvas_object_clicked)
        self.canvas.document_data_changed.connect(self._on_document_data_changed)
        self.canvas.polygon_finished.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_room_polygon_finished.connect(self._on_canvas_mutation_signal)
        self.canvas.ref_line_set.connect(self._on_canvas_mutation_signal)
        self.canvas.start_point_moved.connect(self._on_canvas_mutation_signal)
        self.canvas.route_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.polygon_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_room_polygon_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_point_placed.connect(self._on_canvas_mutation_signal)
        self.canvas.elec_cable_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.hkv_placed.connect(self._on_canvas_mutation_signal)
        self.canvas.hkv_line_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.text_placed.connect(self._on_canvas_mutation_signal)
        self.canvas.floor_plan_transform_updated.connect(self._on_canvas_mutation_signal)
        self.canvas.floor_plan_polygon_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.export_frame_drawn.connect(self._on_canvas_mutation_signal)
        self.canvas.helper_lines_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.label_moved.connect(self._on_canvas_mutation_signal)
        self.canvas.measure_changed.connect(self._on_canvas_mutation_signal)
        self.canvas.multi_objects_moved.connect(self._on_canvas_mutation_signal)
        self.canvas.will_move_multi_objects.connect(self._push_undo)
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

        # Globale Ansichtsdaten (Zoom, Raster, Grundriss-Transformationen,
        # Hilfslinien, Messungen) in den Canvas übertragen …
        raw = document.to_dict()
        self.canvas.from_dict(raw.get("canvas", {}))
        # … und danach die Elementdaten an das Dokument binden. Ab hier ist
        # das Dokument die einzige Datenquelle: Zeichnen im Canvas verändert
        # unmittelbar das Projekt.
        self.canvas.set_document(document)

        self._load_floor_plan_images(document)
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
            "draw_route": lambda eid: self.canvas.start_route_drawing(eid),
            "edit_route": lambda eid: self.canvas.start_edit_route(eid),
            "draw_supply": lambda eid: self.canvas.start_draw_supply_line(eid),
            "edit_supply": lambda eid: self.canvas.start_edit_supply_line(eid),
            "draw_cable": lambda eid: self.canvas.start_draw_elec_cable(eid),
            "edit_cable": lambda eid: self.canvas.start_edit_elec_cable(eid),
            "draw_line": lambda eid: self.canvas.start_draw_hkv_line(eid),
            "edit_line": lambda eid: self.canvas.start_edit_hkv_line(eid),
            "place": self._action_place,
            "draw_ref_line": lambda eid: self.canvas.start_ref_line_for_floor(eid),
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
            "draw_polygon", "edit_polygon", "draw_route", "edit_route",
            "draw_supply", "edit_supply", "draw_cable", "edit_cable",
            "draw_line", "edit_line", "place", "draw_ref_line",
            "configure_uv", "configure_up", "configure_hak", "configure_zaehler",
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

    def _action_draw_polygon(self, element_id: str) -> None:
        if element_id in self._document.elements["elec_rooms"]:
            self.canvas.start_draw_elec_room(element_id)
        else:
            self.canvas.start_drawing(element_id)

    def _action_edit_polygon(self, element_id: str) -> None:
        self.canvas.start_edit_polygon(element_id)

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

    def _action_delete(self, element_id: str) -> None:
        self._delete_element_with_confirm(element_id)

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
                    mappings = up.get("mappings") or []
                    for mapping in mappings:
                        if isinstance(mapping, dict) and (mapping.get("cable_id") or "") == element_id:
                            mapping["cable_id"] = ""
                            changed = True
                if changed:
                    document.element_changed.emit(point.id)

    def _duplicate_element(self, source_id: str) -> str | None:
        source = self._document.get(source_id)
        if source is None:
            return None

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
        for key in offset_keys:
            if key in geom:
                value = geom[key]
                if key == "start_points":
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

    def _on_navigator_context(self, element_id: str, kind: str, global_pos) -> None:
        menu = QMenu(self)
        if kind == "floorplan":
            activate_action = menu.addAction("Als aktiv setzen")
            menu.addSeparator()
        else:
            activate_action = None

        copy_action = menu.addAction("Kopieren")
        paste_action = menu.addAction("Einfügen")
        duplicate_action = menu.addAction("Duplizieren")
        menu.addSeparator()
        delete_action = menu.addAction("Löschen")

        has_element = self._document.get(element_id) is not None
        copy_action.setEnabled(has_element)
        duplicate_action.setEnabled(has_element)
        delete_action.setEnabled(has_element)
        paste_action.setEnabled(self._copy_buffer is not None)

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if activate_action is not None and chosen == activate_action:
            self._on_floorplan_activated(element_id)
            return
        if chosen == copy_action:
            self.navigator.select(element_id)
            self.properties.show_element(element_id)
            self.canvas.set_selected_item(element_id)
            self._copy_selected()
            return
        if chosen == paste_action:
            self._paste_copied()
            return
        if chosen == duplicate_action:
            self.navigator.select(element_id)
            self.properties.show_element(element_id)
            self.canvas.set_selected_item(element_id)
            self._duplicate_selected()
            return
        if chosen == delete_action:
            self._delete_element_with_confirm(element_id)

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


    def _on_element_selected(self, element_id: str) -> None:
        self.canvas.set_selected_item(element_id)
        self.properties.show_element(element_id)

    def _on_canvas_object_clicked(self, _obj_type: str, obj_id: str) -> None:
        if not obj_id:
            return
        self.navigator.select(obj_id)
        self.properties.show_element(obj_id)

    def _on_floorplan_activated(self, fp_id: str) -> None:
        self._document.active_floorplan_id = fp_id
        self.canvas.set_active_helper_floor(fp_id)
        self.log.info(f"Aktiver Grundriss: {fp_id}")

    def _on_visibility_changed(self, element_id: str, visible: bool) -> None:
        if not self._document.set_visible(element_id, visible):
            return
        self._apply_canvas_visibility(element_id, visible)
        self._dirty = True
        self._update_title()

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

        id_map: dict[str, str] = {}
        new_ids: list[str] = []

        for source_ap_id in selected_ap_ids:
            new_ap_id = self._duplicate_element(source_ap_id)
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
            new_cable_id = self._duplicate_element(source_cable_id)
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

    def _export_pdf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Als PDF exportieren", "projektbericht.pdf", "PDF (*.pdf)"
        )
        if not path:
            return

        from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter  # noqa: PLC0415
        from PySide6.QtCore import QRectF  # noqa: PLC0415

        writer = QPdfWriter(path)
        writer.setResolution(150)
        writer.setPageSize(QPageSize(QPageSize.A4))
        writer.setPageOrientation(QPageLayout.Landscape)

        painter = QPainter()
        if not painter.begin(writer):
            QMessageBox.critical(self, "PDF-Export", "PDF konnte nicht erstellt werden.")
            return

        try:
            page_rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
            img = self.canvas.render_for_export(
                output_w=int(page_rect.width()),
                output_h=int(page_rect.height()),
            )
            painter.drawImage(page_rect, img)
        finally:
            painter.end()

        self.log.success(f"PDF exportiert: {path}")
        self.statusBar().showMessage(f"PDF exportiert: {path}", 4000)

    def _export_kicad(self) -> None:
        if self._project_path is None:
            QMessageBox.warning(
                self,
                "Projekt nicht gespeichert",
                "Bitte speichern Sie das Projekt zuerst.",
            )
            return

        suggested = self._project_path.with_suffix(".kicad_sch").name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "KiCad-Export",
            str(self._project_path.parent / suggested),
            "KiCad Schaltplan (*.kicad_sch);;Alle Dateien (*)",
        )
        if not path:
            return

        try:
            from logic.kicad_export import export_project_to_kicad  # noqa: PLC0415
        except ImportError as exc:
            QMessageBox.critical(self, "KiCad-Export", f"Modul nicht gefunden: {exc}")
            return

        project_dict = self._collect_project_dict()
        success, message = export_project_to_kicad(project_dict, path)
        if success:
            self.log.success(f"KiCad exportiert: {path}")
            self.statusBar().showMessage(f"KiCad exportiert: {path}", 4000)
        else:
            QMessageBox.critical(self, "KiCad-Export fehlgeschlagen", message)
            self.log.error(message)

    def _export_qet(self) -> None:
        if self._project_path is None:
            QMessageBox.warning(
                self,
                "Projekt nicht gespeichert",
                "Bitte speichern Sie das Projekt zuerst.",
            )
            return

        suggested = self._project_path.with_suffix(".qet").name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "QElectroTech-Export",
            str(self._project_path.parent / suggested),
            "QElectroTech (*.qet);;Alle Dateien (*)",
        )
        if not path:
            return

        try:
            from logic.qet_export import QETExporter  # noqa: PLC0415
        except ImportError as exc:
            QMessageBox.critical(self, "QET-Export", f"Modul nicht gefunden: {exc}")
            return

        project_dict = self._collect_project_dict()
        try:
            exporter = QETExporter(project_dict)
            exporter.export_to_file(path)
            self.log.success(f"QET exportiert: {path}")
            self.statusBar().showMessage(f"QET exportiert: {path}", 4000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "QET-Export fehlgeschlagen", str(exc))
            self.log.error(str(exc))

    def _export_lengths(self) -> None:
        """Längen- und Hydraulik-Übersicht für alle Heizkreise."""
        from PySide6.QtWidgets import (  # noqa: PLC0415
            QDialog, QLabel, QScrollArea, QVBoxLayout, QDialogButtonBox,
        )

        try:
            from logic.heating_calc import calc_circuit, FLOOR_COVERINGS  # noqa: PLC0415
        except ImportError as exc:
            QMessageBox.warning(self, "Längenexport", f"Modul nicht verfügbar: {exc}")
            return

        circuits = self._document.elements.get("circuits", {})
        if not circuits:
            QMessageBox.information(self, "Längenexport", "Keine Heizkreise im Projekt.")
            return

        t_supply = float(self._document.settings.get("t_supply", 35.0))
        t_return = float(self._document.settings.get("t_return", 30.0))

        # Find a representative mm_per_px from the first floor plan
        mm_per_px = 1.0
        for fp in self._document.floorplans.values():
            mpp = float(fp.mm_per_px)
            if mpp > 0:
                mm_per_px = mpp
                break

        rows: list[str] = [
            "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>",
            "<tr><th>ID</th><th>Name</th><th>Fläche m²</th>"
            "<th>Rohrlänge m</th><th>Leistung W</th>"
            "<th>Volumenstrom l/min</th><th>Druckverlust mbar</th></tr>",
        ]
        total_power = 0.0
        for cid, circuit in sorted(circuits.items()):
            polygon = circuit.geom.get("polygons")
            route = circuit.geom.get("manual_routes")

            area_m2 = 0.0
            if polygon and len(polygon) >= 3:
                # Shoelace formula (polygon in canvas pixels → mm²)
                pts = polygon
                n = len(pts)
                area_px2 = abs(
                    sum(
                        pts[i][0] * pts[(i + 1) % n][1]
                        - pts[(i + 1) % n][0] * pts[i][1]
                        for i in range(n)
                    )
                ) / 2.0
                area_m2 = area_px2 * (mm_per_px ** 2) / 1_000_000.0

            pipe_length_m = 0.0
            if route and len(route) >= 2:
                length_px = sum(
                    ((route[i + 1][0] - route[i][0]) ** 2
                     + (route[i + 1][1] - route[i][1]) ** 2) ** 0.5
                    for i in range(len(route) - 1)
                )
                pipe_length_m = length_px * mm_per_px / 1000.0

            floor_name = circuit.data.get("floor_covering", "Fliesen / Keramik")
            r_lambda = FLOOR_COVERINGS.get(floor_name, 0.01)
            spacing_cm = float(circuit.data.get("spacing", 150.0)) / 10.0
            room_temp = float(circuit.data.get("room_temp", 20.0))
            diameter_mm = float(circuit.data.get("diameter", 16.0))
            try:
                hc = calc_circuit(
                    t_supply=t_supply,
                    t_return=t_return,
                    t_room=room_temp,
                    spacing_cm=spacing_cm,
                    r_lambda_b=r_lambda,
                    area_m2=area_m2,
                    pipe_length_m=pipe_length_m,
                    outer_diameter_mm=diameter_mm,
                    total_pipe_length_m=pipe_length_m,
                )
                power = hc.get("power_w", 0.0)
                vol = hc.get("volume_flow_lmin", 0.0)
                dp = hc.get("pressure_drop_mbar", 0.0)
            except Exception:  # noqa: BLE001
                power = vol = dp = 0.0
            total_power += power
            name = circuit.name or cid
            rows.append(
                f"<tr><td>{cid}</td><td>{name}</td>"
                f"<td>{area_m2:.2f}</td><td>{pipe_length_m:.1f}</td>"
                f"<td>{power:.0f}</td><td>{vol:.2f}</td><td>{dp:.0f}</td></tr>"
            )

        rows.append(
            f"<tr><td colspan='4'><b>Gesamt</b></td>"
            f"<td><b>{total_power:.0f}</b></td><td></td><td></td></tr>"
        )
        rows.append("</table>")
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
