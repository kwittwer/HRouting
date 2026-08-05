"""Neues Hauptfenster von HRouting.

Aufbau:

* zentrales Widget: die Zeichenfläche (eine Instanz für alle Workspaces)
* Workspace-Tabs oberhalb der Zeichenfläche
* alle weiteren Panels sind andockbare, frei verschiebbare QDockWidgets
* Fenster- und Docklayout wird global in QSettings gespeichert
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QInputDialog,
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
        self._add_action(edit_menu, "Rückgängig", self._undo, QKeySequence.Undo)
        self._add_action(edit_menu, "Wiederherstellen", self._redo, QKeySequence.Redo)

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
        self._add_action(self.export_menu, "PDF exportieren…", self._not_implemented)
        self._add_action(self.export_menu, "SVG exportieren…", self._not_implemented)
        self._add_action(self.export_menu, "KiCad exportieren…", self._not_implemented)
        self._add_action(self.export_menu, "QElectroTech exportieren…", self._not_implemented)
        self.export_menu.addSeparator()
        self._add_action(self.export_menu, "Längen & Stückliste…", self._not_implemented)

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
        self.tools.tool_activated.connect(self._on_tool_activated)
        self.canvas.object_clicked.connect(self._on_canvas_object_clicked)
        self.canvas.document_data_changed.connect(self._on_document_data_changed)
        self.properties.field_changed.connect(self._on_property_changed)
        self.properties.action_triggered.connect(self._on_property_action)
        self.properties.setting_changed.connect(self._on_global_setting_changed)

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
            "delete": self._action_delete,
        }

        handler = handlers.get(action_id)
        if handler is None:
            self.statusBar().showMessage("Aktion noch nicht verfügbar", 3000)
            return

        try:
            handler(element_id)
        except Exception as exc:  # noqa: BLE001 - Aktion darf die UI nicht abbrechen
            self.log.error(f"Aktion '{action_id}' fehlgeschlagen: {exc}")

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
        self._document.remove(element_id)
        self.properties.forget_element(element_id)
        self._emit_structure_changed()
        self.canvas.update()
        self._mark_dirty()
        self.log.info(f"Gelöscht: {element_id}")

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
    def _undo(self) -> None:
        self._not_implemented()

    def _redo(self) -> None:
        self._not_implemented()

    def _not_implemented(self) -> None:
        self.statusBar().showMessage("Noch nicht portiert", 3000)

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
