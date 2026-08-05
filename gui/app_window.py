"""Neues Hauptfenster von HRouting.

Aufbau:

* zentrales Widget: die Zeichenfläche (eine Instanz für alle Workspaces)
* Workspace-Tabs oberhalb der Zeichenfläche
* alle weiteren Panels sind andockbare, frei verschiebbare QDockWidgets
* Fenster- und Docklayout wird global in QSettings gespeichert
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QMenu,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from model.document import Document
from model.elements import Circuit, ElecPoint, ElecRoom, ElecCable, FloorPlan, Hkv, HkvLine, TextAnnotation
from storage.hrp_io import load_document, save_document

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
        self._undo_action: QAction | None = None
        self._redo_action: QAction | None = None

        self._build_central()
        self._build_docks()
        self._build_menus()
        self._connect_signals()

        self._restore_layout()
        self._apply_workspace(layout_store.last_workspace(DEFAULT_WORKSPACE_ID))
        self._set_document(self._document)

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
        self.properties.field_changed.connect(self._on_property_changed)
        self.properties.action_triggered.connect(self._on_property_action)
        self.properties.setting_changed.connect(self._on_global_setting_changed)
        self.properties.pre_change.connect(self._push_undo)

    def _on_property_changed(self, element_id: str, key: str, _value) -> None:
        """Ein Feld im Eigenschaften-Dock wurde geändert."""
        self._apply_property_side_effects(element_id, key)
        self._document.element_changed.emit(element_id)
        self.canvas.update()
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
        self.properties.refresh_element(element_id)
        if not self._dirty:
            self._dirty = True
            self._update_title()

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------
    def _push_undo(self) -> None:
        """Nimmt einen Snapshot des aktuellen Zustands auf den Undo-Stack."""
        snapshot = self._document.snapshot()
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > _MAX_UNDO_STEPS:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_state()

    def _undo(self) -> None:
        if not self._undo_stack:
            self.statusBar().showMessage("Nichts zum Rückgängigmachen", 2000)
            return
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
        """
        self._document.restore(snapshot)
        # Canvas: erst rohe Ansichtsdaten übertragen, dann Views neu binden.
        raw_canvas = snapshot.get("canvas", {})
        self.canvas.from_dict(raw_canvas)
        self.canvas.set_document(self._document)
        self._load_floor_plan_images(self._document)
        # Eigenschaften-Dock: alle Editor-Caches invalidieren.
        current_id = self.properties._current_id
        self.properties.set_document(self._document)
        if current_id and self._document.get(current_id):
            self.properties.show_element(current_id)

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
        self._update_title()

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

        from PySide6.QtGui import QImage  # noqa: PLC0415
        from PySide6.QtPrintSupport import QPrinter  # noqa: PLC0415
        from PySide6.QtGui import QPainter  # noqa: PLC0415
        from PySide6.QtCore import QRectF  # noqa: PLC0415

        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageOrientation(
            __import__("PySide6.QtGui", fromlist=["QPageLayout"]).QPageLayout.Landscape
        )
        printer.setPageSize(
            __import__("PySide6.QtGui", fromlist=["QPageSize"]).QPageSize(
                __import__("PySide6.QtGui", fromlist=["QPageSize"]).QPageSize.A4
            )
        )

        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.critical(self, "PDF-Export", "PDF konnte nicht erstellt werden.")
            return

        try:
            page_rect = QRectF(printer.pageRect(QPrinter.DevicePixel))
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
