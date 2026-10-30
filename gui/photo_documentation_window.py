"""Independent photo documentation editor; document mutations belong to its parent."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QSplitter, QStyle, QTableWidget,
    QTableWidgetItem, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from gui.photo_measure_view import PhotoMeasureView
from logic.photo_geometry import (
    _contains, _polygon_points, _quad, analyze_photo, measurement, nearest_surface,
    transform_points,
)
from logic.photo_markers import add_reference_marker, ensure_markers, read_photo
from model.elements import ElecPoint
from model.photo_documentation import _next_id, ensure_surfaces, get_config
from storage.photo_assets import import_photo, resolve_photo_path, verify_photo


WARNINGS = {
    "uncalibrated_lens": "Objektivverzeichnung ungeprueft",
    "unchecked_control_distance": "Kontrollmass fehlt",
    "extrapolation_outside_reference_area": "Ausserhalb der Referenzen: Extrapolation",
    "measurement_extrapolates_reference_area": "Messung ausserhalb der Referenzen",
    "planar_projection_only": "Nur Projektion auf die gewaehlte Ebene; keine Bohrfreigabe",
    "local_reference_only": "Lokaler Markerbezug, kein FFB-/Eckbezug",
    "single_marker_accuracy_not_verified": "Einzelmarker: Genauigkeit nicht nachgewiesen",
    "low_marker_resolution": "Markeraufloesung gering",
    "extreme_perspective": "Sehr schraege Aufnahme",
    "conflicting_measured_references": "Gemessene Referenzen widersprechen sich",
    "independent_markers_not_jointly_fitted": "Unvermessene Marker nicht gemeinsam kalibriert",
    "unknown_reference_support": "Referenzbereich unbekannt",
    "unassigned_surface": "Marker ohne Flaechenzuordnung",
    "assignment_surface_conflict": "Widerspruechliche Flaechenzuordnung",
    "duplicate_marker_id": "Marker mehrfach im Bild; keine eindeutige Kalibrierung",
    "unregistered_marker_id": "Nicht registrierter Marker",
    "ambiguous_registry_id": "Mehrdeutige Markerregistrierung",
    "stale_surface_geometry": "Raumgeometrie geaendert",
}


def warning_text(code):
    return WARNINGS.get(code, "Analysehinweis: " + str(code))


class _JobSignals(QObject):
    progress = Signal(int, int, int, str)
    finished = Signal(int, object)


class _PhotoJob(QRunnable):
    def __init__(self, generation, paths, project_path, config, abort, photo_id=""):
        super().__init__()
        self.generation = generation
        self.paths = list(paths)
        self.project_path = project_path
        self.config = deepcopy(config)
        self.abort = abort
        self.photo_id = photo_id
        self.signals = _JobSignals()

    def run(self):
        items, errors = [], []
        for index, source in enumerate(self.paths):
            if self.abort.is_set():
                break
            try:
                if self.photo_id:
                    meta = deepcopy(self.config["photos"][self.photo_id])
                    path = verify_photo(self.project_path, meta)
                else:
                    meta = import_photo(source, self.project_path, self.config["project_uuid"])
                    path = verify_photo(self.project_path, meta)
                image = read_photo(path)
                meta["height"], meta["width"] = map(int, image.shape[:2])
                try:
                    analysis = analyze_photo(image, self.config, self.photo_id)
                except Exception as error:
                    analysis = {"width": meta["width"], "height": meta["height"],
                                "detections": [], "regions": [], "warnings": [f"analysis_failed: {error}"]}
                items.append({"meta": meta, "analysis": analysis, "photo_id": self.photo_id})
            except Exception as error:
                errors.append(f"{Path(source).name}: {error}")
            self.signals.progress.emit(self.generation, index + 1, len(self.paths), Path(source).name)
        self.signals.finished.emit(self.generation, {
            "items": items, "errors": errors, "cancelled": self.abort.is_set(),
        })


class _PlanView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self._labels = []
        self.setMinimumHeight(155)
        self.setMaximumHeight(240)

    def show_surface(self, surface):
        self.scene().clear()
        self._labels = []
        if not surface:
            return
        polygon = surface.get("source_polygon", [])
        if len(polygon) < 3:
            return
        pen = QPen(QColor("#929292"), 1)
        pen.setCosmetic(True)
        self.scene().addPolygon(QPolygonF([QPointF(*point) for point in polygon]), pen)
        points = surface.get("plan_points", [])
        active = QPen(QColor("#e05248"), 4)
        active.setCosmetic(True)
        if surface.get("kind") == "wall" and len(points) == 2:
            self.scene().addLine(*points[0], *points[1], active)
        elif len(points) >= 3:
            self.scene().addPolygon(QPolygonF([QPointF(*point) for point in points]), active)
            import math

            start, end = points[:2]
            length = math.dist(start, end)
            if length:
                area = sum(polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
                           - polygon[index][1] * polygon[(index + 1) % len(polygon)][0]
                           for index in range(len(polygon)))
                sign = 1 if area >= 0 else -1
                target = [start[0] - sign * (end[1] - start[1]) * 0.3,
                          start[1] + sign * (end[0] - start[0]) * 0.3]
                self.scene().addLine(*start, *target, active)
                text = self.scene().addText("+v")
                text.setFlag(text.GraphicsItemFlag.ItemIgnoresTransformations)
                text.setPos(*target)
        for point, label in zip(points[:2], ("A / u=0", "B / +u")):
            text = self.scene().addText(label)
            text.setFlag(text.GraphicsItemFlag.ItemIgnoresTransformations)
            text.setDefaultTextColor(QColor("#d7473c"))
            text.setPos(*point)
            self._labels.append((text, point))
        bounds = self.scene().itemsBoundingRect()
        margin = max(bounds.width(), bounds.height()) * 0.1
        self.setSceneRect(bounds.adjusted(-margin, -margin, margin, margin))
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self._position_labels()

    def _position_labels(self):
        for text, point in self._labels:
            position = self.mapFromScene(QPointF(*point))
            horizontal = max(4, min(position.x(), self.viewport().width() - text.boundingRect().width() - 4))
            vertical = max(4, min(position.y(), self.viewport().height() - text.boundingRect().height() - 4))
            text.setPos(self.mapToScene(int(horizontal), int(vertical)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self._position_labels()


class ReferenceDialog(QDialog):
    def __init__(self, window, surface_id):
        super().__init__(window)
        self.window = window
        self.surface_id = surface_id
        self.setWindowTitle("Markerreferenz")
        form = QFormLayout(self)
        self.marker = QComboBox()
        form.addRow("Marker", self.marker)
        add = QPushButton("Referenzmarker registrieren")
        add.clicked.connect(self._add_marker)
        form.addRow(add)
        self.u = self._spin(-100000, 100000, " cm")
        self.v = self._spin(-100000, 100000, " cm")
        self.rotation = self._spin(-360, 360, " deg")
        self.offset = self._spin(-10000, 10000, " mm")
        self.source = QComboBox()
        self.source.addItems(["Fertigfussboden", "Rohboden"])
        self.measured = QCheckBox("Tatsaechliche Markermitte und Ausrichtung gemessen")
        form.addRow("Markermitte u ab A", self.u)
        surface = window.config["surfaces"][surface_id]
        self.is_wall = surface.get("kind") == "wall"
        form.addRow("Markermitte v ueber Bezug" if self.is_wall else "Markermitte v ab Ursprung A", self.v)
        form.addRow("Hoehenbezug", self.source)
        self.source.setEnabled(self.is_wall)
        form.addRow("Rohboden bis FFB", self.offset)
        self.offset.setEnabled(self.is_wall)
        form.addRow("Rotation: UP=+v, positiv gegen Uhrzeigersinn", self.rotation)
        form.addRow(self.measured)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        clear = buttons.addButton("Referenz loeschen", QDialogButtonBox.ResetRole)
        clear.clicked.connect(self._clear_reference)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.marker.currentIndexChanged.connect(self._load_marker)
        self._fill_markers()

    def _spin(self, minimum, maximum, suffix):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSuffix(suffix)
        return spin

    def _fill_markers(self, select=""):
        self.marker.blockSignals(True)
        self.marker.clear()
        for marker_id, record in self.window.config.get("markers", {}).items():
            if record.get("surface_id") == self.surface_id:
                self.marker.addItem(f"{marker_id} / {record.get('point_id', 'Referenz')} / {record['side_mm']:g} mm", marker_id)
        if select:
            self.marker.setCurrentIndex(self.marker.findData(select))
        self.marker.blockSignals(False)
        self._load_marker()

    def _load_marker(self):
        record = self.window.config.get("markers", {}).get(self.marker.currentData(), {})
        reference = record.get("reference")
        origin = (reference or {}).get("origin_mm", [0, 0])
        self.u.setValue(origin[0] / 10)
        self.v.setValue((reference or {}).get("v_input_mm", origin[1]) / 10)
        self.rotation.setValue((reference or {}).get("rotation_deg", 0))
        room_id = self.window.config["surfaces"][self.surface_id]["room_id"]
        self.offset.setValue(self.window.config.get("rooms", {}).get(room_id, {}).get("ffb_offset_mm", 0))
        self.source.setCurrentIndex(1 if (reference or {}).get("v_source") == "Rohboden" else 0)
        self.measured.setChecked(reference is not None)
        self.status.setText("Gemessene Referenz" if reference else "Keine gemessene Referenz; nur lokaler metrischer Markerbezug")

    def _add_marker(self):
        try:
            marker_id = self.window.register_reference(self.surface_id)
            self._fill_markers(marker_id)
        except Exception as error:
            self.status.setText(str(error))

    def _clear_reference(self):
        self.measured.setChecked(False)
        self.accept()

    def accept(self):
        if not self.marker.currentData():
            self.status.setText("Kein registrierter Marker ausgewaehlt")
            return
        try:
            self.window.set_reference(
                self.marker.currentData(), self.u.value(), self.v.value(),
                self.rotation.value(), self.measured.isChecked(),
                self.offset.value(), self.source.currentText(),
            )
        except ValueError as error:
            self.status.setText(str(error))
            return
        super().accept()


class PhotoDocumentationWindow(QMainWindow):
    config_changed = Signal(dict)
    save_requested = Signal()
    markers_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fotodokumentation")
        self._document = None
        self.project_path = None
        self.config = {}
        self._generation = 0
        self._abort = None
        self._jobs = {}
        self._pool = QThreadPool.globalInstance()
        self._surface_id = ""
        self._photo_id = ""
        self._region_id = ""
        self._asset_error = ""
        self._committed_config = {}
        self.resize(1240, 780)
        self._build_ui()

    def _action(self, toolbar, name, icon, callback, checkable=False):
        standard_icon = self.style().standardIcon(icon)
        if standard_icon.isNull():
            standard_icon = self.style().standardIcon(QStyle.SP_FileIcon)
        action = QAction(standard_icon, name, self)
        action.setToolTip(name)
        action.setCheckable(checkable)
        action.triggered.connect(callback)
        toolbar.addAction(action)
        return action

    def _build_ui(self):
        toolbar = QToolBar("Fotodokumentation", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(toolbar)
        self.import_action = self._action(toolbar, "Fotos importieren", QStyle.SP_DialogOpenButton, self._import_dialog)
        self.cancel_action = self._action(toolbar, "Import / Analyse abbrechen", QStyle.SP_BrowserStop, self.cancel_import)
        self.cancel_action.setEnabled(False)
        self._action(toolbar, "Flaechen vorbereiten", QStyle.SP_FileDialogNewFolder, self.prepare_surfaces)
        self._action(toolbar, "APs den Flaechen zuordnen", QStyle.SP_FileDialogDetailedView, self._assignment_dialog)
        self._action(toolbar, "Messmarker registrieren und exportieren", QStyle.SP_FileIcon, self.request_markers)
        self._action(toolbar, "Gemessene Markerreferenz / FFB", QStyle.SP_FileDialogContentsView, self._reference_dialog)
        toolbar.addSeparator()
        self._action(toolbar, "Markerzuordnung korrigieren", QStyle.SP_FileDialogListView, self._correction_dialog)
        self._action(toolbar, "Foto erneut analysieren", QStyle.SP_BrowserReload, self.reanalyze_photo)
        self._action(toolbar, "Foto einer Flaeche zuordnen (Dokumentation)", QStyle.SP_DirLinkIcon, self._manual_assignment_dialog)
        self._action(toolbar, "Foto: Name, Notiz, Bauzustand", QStyle.SP_FileDialogInfoView, self._metadata_dialog)
        self._action(toolbar, "Foto entfernen", QStyle.SP_TrashIcon, self.delete_photo)
        self._action(toolbar, "Identisches Original neu verknuepfen", QStyle.SP_DirLinkIcon, self._relink_dialog)
        toolbar.addSeparator()
        self._action(toolbar, "Messbereich: vier Eckpunkte", QStyle.SP_TitleBarShadeButton, self._start_mask)
        self._action(toolbar, "Kontrollmass", QStyle.SP_DialogApplyButton, self._start_control)
        self.rectify_action = self._action(toolbar, "Entzerrt / Original", QStyle.SP_DesktopIcon, self._toggle_rectified, True)
        self._action(toolbar, "Bild einpassen", QStyle.SP_TitleBarMaxButton, lambda: self.view.fit_photo())
        self._action(toolbar, "Projekt speichern", QStyle.SP_DialogSaveButton, self._request_save)
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(4, 4, 4, 4)
        self.surface_tree = QTreeWidget()
        self.surface_tree.setHeaderLabel("Raum / Flaeche")
        self.surface_tree.currentItemChanged.connect(self._surface_selected)
        layout.addWidget(self.surface_tree, 1)
        self.plan = _PlanView()
        layout.addWidget(self.plan)
        splitter.addWidget(left)
        center = QWidget()
        layout = QVBoxLayout(center)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.region_choice = QComboBox()
        self.region_choice.setMinimumContentsLength(12)
        self.region_choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.region_choice.currentIndexChanged.connect(self._region_selected)
        row.addWidget(self.region_choice, 1)
        self.mode_choice = QComboBox()
        for label, mode in (("Verschieben", "pan"), ("Strecke", "distance"), ("Horizontal", "horizontal"),
                            ("Vertikal", "vertical"), ("Boden / FFB", "floor"),
                            ("Wandursprung A", "origin"), ("AP (Markermitte)", "ap")):
            self.mode_choice.addItem(label, mode)
        self.mode_choice.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.mode_choice)
        self.ap_choice = QComboBox()
        self.ap_choice.setToolTip("Bezug ist die erkannte Markermitte, nicht eine versetzte Installation")
        self.ap_choice.setMinimumContentsLength(10)
        self.ap_choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.ap_choice)
        layout.addLayout(row)
        self.view = PhotoMeasureView()
        self.view.measurement_created.connect(self._measurement_points)
        self.view.boundary_created.connect(self.set_region_mask)
        self.view.interaction_error.connect(self._show_error)
        layout.addWidget(self.view, 1)
        self.quality_status = QLabel("Kein Foto ausgewaehlt")
        self.quality_status.setWordWrap(True)
        self.quality_status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.quality_status)
        splitter.addWidget(center)
        right = QSplitter(Qt.Vertical)
        photos_panel = QWidget()
        layout = QVBoxLayout(photos_panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("Fotos / Bauzustand"))
        self.photo_list = QListWidget()
        self.photo_list.currentItemChanged.connect(self._photo_selected)
        layout.addWidget(self.photo_list)
        right.addWidget(photos_panel)
        measurements_panel = QWidget()
        layout = QVBoxLayout(measurements_panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("Messungen [cm]"))
        self.measurement_list = QListWidget()
        layout.addWidget(self.measurement_list)
        tools = QToolBar()
        tools.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._action(tools, "Messung umbenennen", QStyle.SP_FileDialogInfoView, self._rename_measurement_dialog)
        self._action(tools, "Messung loeschen", QStyle.SP_TrashIcon, self.delete_measurement)
        layout.addWidget(tools)
        right.addWidget(measurements_panel)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([225, 740, 260])

    def _show_error(self, message):
        self.statusBar().showMessage(str(message))

    def _safe(self, callback):
        try:
            return callback()
        except Exception as error:
            self._show_error(str(error))
            return None

    def set_document(self, doc, project_path=None):
        self.cancel_import()
        self._document = doc
        self.project_path = project_path
        self.config = get_config(doc) if doc is not None else {}
        self._committed_config = deepcopy(self.config)
        self._surface_id = self._photo_id = self._region_id = ""
        self._refresh()

    def cancel_import(self):
        self._generation += 1
        active = self._abort is not None
        if self._abort is not None:
            self._abort.set()
            self._abort = None
        if hasattr(self, "cancel_action"):
            self.cancel_action.setEnabled(False)
            if active:
                self.statusBar().showMessage("Import / Analyse abgebrochen")

    def save_settings(self) -> bool:
        if self.config != getattr(self, "_committed_config", self.config):
            self.cancel_import()
            self._committed_config = deepcopy(self.config)
            self.config_changed.emit(deepcopy(self.config))
        return True

    def invalidate(self):
        self.cancel_import()
        if self._document is not None and self.config.get("surfaces"):
            ensure_surfaces(self._document, self.config)
            for region in self.config.get("regions", {}).values():
                surface = self.config["surfaces"].get(region.get("surface_id"), {})
                if surface.get("status") != "current":
                    region["status"] = "stale"
            self.save_settings()
        self._refresh()

    def _commit(self):
        self.save_settings()
        self._refresh()

    def _request_save(self):
        if self.save_settings():
            self.save_requested.emit()

    def prepare_surfaces(self):
        if self._document is None:
            return
        ensure_surfaces(self._document, self.config)
        for region in self.config.get("regions", {}).values():
            if self.config["surfaces"].get(region.get("surface_id"), {}).get("status") != "current":
                region["status"] = "stale"
        self._commit()

    def _refresh(self):
        previous_photo, previous_region = self._photo_id, self._region_id
        preview = self.view.rectified
        previous_transform = self.view.transform()
        center = None
        if self.view.image is not None:
            position = self.view.mapToScene(self.view.viewport().rect().center())
            try:
                center = self.view.preview_to_canonical([[position.x(), position.y()]])[0]
            except ValueError:
                pass
        self.surface_tree.blockSignals(True)
        self.surface_tree.clear()
        all_item = QTreeWidgetItem(["Alle Fotos"])
        all_item.setData(0, Qt.UserRole, "")
        self.surface_tree.addTopLevelItem(all_item)
        unassigned = QTreeWidgetItem(["Nicht zugeordnet"])
        unassigned.setData(0, Qt.UserRole, "__unassigned__")
        self.surface_tree.addTopLevelItem(unassigned)
        selected = unassigned if self._surface_id == "__unassigned__" else all_item
        groups = {}
        for surface_id, surface in self.config.get("surfaces", {}).items():
            room_id = surface.get("room_id", "")
            if room_id not in groups:
                room = self._document.get(room_id) if self._document else None
                groups[room_id] = QTreeWidgetItem([room.name if room else room_id])
                self.surface_tree.addTopLevelItem(groups[room_id])
                groups[room_id].setExpanded(True)
            kind = surface.get("kind")
            label = (f"Wand {surface.get('source_edge_index', 0) + 1}" if kind == "wall"
                     else "Boden" if kind == "floor" else "Decke")
            if surface.get("status") != "current":
                label += " (veraltet)"
            item = QTreeWidgetItem(groups[room_id], [label])
            item.setData(0, Qt.UserRole, surface_id)
            item.setToolTip(0, surface.get("name", surface_id))
            if surface_id == self._surface_id:
                selected = item
        self.surface_tree.setCurrentItem(selected)
        self.surface_tree.blockSignals(False)
        self.plan.show_surface(self.config.get("surfaces", {}).get(self._surface_id))
        self._refresh_photos()
        if (center is not None and previous_photo == self._photo_id and previous_region == self._region_id
                and self.view.image is not None):
            if preview and not self.region_error(require_mask=False) and self.view.set_rectified(True):
                self.rectify_action.setChecked(True)
            if self.view.rectified == preview:
                self.view.setTransform(previous_transform)
                mapped = self.view.canonical_to_preview([center])[0]
                self.view.centerOn(*mapped)

    def _surface_selected(self, item, previous=None):
        self._surface_id = item.data(0, Qt.UserRole) or "" if item else ""
        self.plan.show_surface(self.config.get("surfaces", {}).get(self._surface_id))
        self._refresh_photos()

    def _photo_surfaces(self, photo_id):
        photo = self.config["photos"][photo_id]
        surfaces = {region.get("surface_id") for region in self.config.get("regions", {}).values()
                    if region.get("photo_id") == photo_id and region.get("status") != "stale"}
        surfaces.update(self.config.get("markers", {}).get(detection.get("marker_id"), {}).get("surface_id")
                        for detection in photo.get("detections", []))
        if photo.get("manual_surface_id"):
            surfaces.add(photo["manual_surface_id"])
        return surfaces - {None, ""}

    def _refresh_photos(self):
        previous_id = self._photo_id
        self.photo_list.blockSignals(True)
        self.photo_list.clear()
        selected = None
        for photo_id, photo in self.config.get("photos", {}).items():
            surfaces = self._photo_surfaces(photo_id)
            unassigned = not surfaces or any(
                not self.config.get("markers", {}).get(detection.get("marker_id"), {}).get("surface_id")
                for detection in photo.get("detections", []))
            if self._surface_id == "__unassigned__" and not unassigned:
                continue
            if self._surface_id not in ("", "__unassigned__") and self._surface_id not in surfaces and not unassigned:
                continue
            name = photo.get("name") or photo.get("original_name", photo_id)
            state = photo.get("construction_state", "")
            label = name + (f" / {state}" if state else "")
            if not surfaces:
                label += " [nicht zugeordnet]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, photo_id)
            item.setToolTip(photo.get("note", ""))
            self.photo_list.addItem(item)
            if photo_id == previous_id:
                selected = item
        self.photo_list.setCurrentItem(selected or self.photo_list.item(0))
        self.photo_list.blockSignals(False)
        self._photo_selected(self.photo_list.currentItem())

    def select_photo(self, photo_id):
        for index in range(self.photo_list.count()):
            item = self.photo_list.item(index)
            if item.data(Qt.UserRole) == photo_id:
                self.photo_list.setCurrentItem(item)
                return

    def _photo_selected(self, item, previous=None):
        self._photo_id = item.data(Qt.UserRole) if item else ""
        self.view.clear_photo()
        self._asset_error = ""
        self.region_choice.blockSignals(True)
        self.region_choice.clear()
        for region_id, region in self.config.get("regions", {}).items():
            if region.get("photo_id") != self._photo_id:
                continue
            if self._surface_id not in ("", "__unassigned__", region.get("surface_id")):
                continue
            surface = self.config.get("surfaces", {}).get(region.get("surface_id"), {})
            label = surface.get("name", region_id)
            if region.get("status") == "stale":
                label += " [veraltet]"
            self.region_choice.addItem(label, region_id)
        index = self.region_choice.findData(self._region_id)
        if index < 0:
            index = next((index for index in range(self.region_choice.count())
                          if self.config["regions"][self.region_choice.itemData(index)].get("status") != "stale"), 0)
        self.region_choice.setCurrentIndex(index)
        self.region_choice.blockSignals(False)
        self._region_selected()

    def _region_selected(self):
        self._region_id = self.region_choice.currentData() or ""
        self.view.clear_photo()
        self._asset_error = ""
        self.rectify_action.blockSignals(True)
        self.rectify_action.setChecked(False)
        self.rectify_action.blockSignals(False)
        photo = self.config.get("photos", {}).get(self._photo_id)
        region = self.config.get("regions", {}).get(self._region_id)
        self.ap_choice.clear()
        self.measurement_list.clear()
        overlays = []
        for measurement_id, record in self.config.get("measurements", {}).items():
            if record.get("region_id") != self._region_id:
                continue
            stale = not region or region.get("status") == "stale"
            label = f"{record.get('name', measurement_id)}: {record.get('value_mm', 0) / 10:.1f}"
            if stale:
                label += " [veraltet]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, measurement_id)
            self.measurement_list.addItem(item)
            overlays.append({**record, "stale": stale})
        if photo:
            for detection in photo.get("detections", []):
                marker = self.config.get("markers", {}).get(detection.get("marker_id"), {})
                if (region and marker.get("surface_id") == region.get("surface_id")
                        and marker.get("point_id") and not detection.get("warnings")):
                    point = self._document.get(marker["point_id"]) if self._document else None
                    self.ap_choice.addItem(point.name if point else marker["point_id"], detection["marker_id"])
            try:
                if not self.project_path:
                    raise ValueError("Projektpfad fehlt; Foto nicht verfuegbar")
                image = read_photo(verify_photo(self.project_path, photo))
                if image.shape[1] != photo.get("width") or image.shape[0] != photo.get("height"):
                    raise ValueError("Fotogroesse stimmt nicht mit kanonischen Metadaten ueberein")
                self.view.set_photo(image, region, overlays, photo.get("detections", []))
            except Exception as error:
                self._asset_error = f"Foto fehlt oder wurde geaendert: {error}"
        self._sync_quality()
        self._mode_changed()

    def region_error(self, region_id=None, require_mask=True):
        region = self.config.get("regions", {}).get(region_id or self._region_id)
        if not region:
            return "Keine metrische Kalibrierung; Foto ist nur dokumentiert"
        if self._asset_error:
            return self._asset_error
        surface = self.config.get("surfaces", {}).get(region.get("surface_id"), {})
        room = self._document.get(surface.get("room_id", "")) if self._document else None
        try:
            room_polygon = _polygon_points(room.polygon) if room else []
        except ValueError:
            room_polygon = []
        if (region.get("status") == "stale" or surface.get("status") != "current"
                or region.get("quality", {}).get("status") in ("stale", "invalid", "unusable")
                or not room or room_polygon != surface.get("source_polygon")
                or region.get("source_polygon") != surface.get("source_polygon")):
            return "Kalibrierung / Raumgeometrie veraltet; erneut analysieren"
        photo = self.config.get("photos", {}).get(region.get("photo_id"), {})
        if region.get("photo_sha256") != photo.get("sha256"):
            return "Kalibrierung gehoert nicht zum aktuellen Foto-Hash"
        try:
            if not self.project_path:
                return "Projektpfad fehlt"
            verify_photo(self.project_path, photo)
            transform_points([[0, 0]], region.get("H"))
            if require_mask:
                _quad(region.get("mask_px"))
        except (ValueError, OSError, TypeError) as error:
            return "Messbereich fehlt oder Foto / Kalibrierung ungueltig: " + str(error)
        return ""

    def _sync_quality(self):
        photo = self.config.get("photos", {}).get(self._photo_id)
        region = self.config.get("regions", {}).get(self._region_id)
        if not photo:
            self.quality_status.setText("Kein Foto ausgewaehlt")
            self.view.measurement_enabled = False
            self.rectify_action.setEnabled(False)
            return
        error = self.region_error()
        self.view.measurement_enabled = not error
        self.rectify_action.setEnabled(not self.region_error(require_mask=False))
        lines = [self._asset_error] if self._asset_error else []
        if region:
            lines.append("Metrisch / FFB und Ursprung gemessen" if region.get("absolute_reference")
                         else "Metrisch / nur lokaler Markerbezug")
            control = region.get("control")
            if control:
                lines.append(f"Kontrollmass {'geprueft' if control.get('passed') else 'abweichend'}: "
                             f"Abweichung {control['residual_mm'] / 10:.1f} cm; keine Genauigkeitsgarantie")
            if error:
                lines.append(error)
            codes = region.get("warnings", [])
            if control and control.get("passed"):
                codes = [code for code in codes if code != "unchecked_control_distance"]
            lines.extend(warning_text(code) for code in codes)
        else:
            lines.append("Keine Kalibrierung; Foto nur dokumentiert")
        if photo.get("analysis_status") == "no_markers":
            lines.append("Keine Marker erkannt")
        for detection in photo.get("detections", []):
            lines.extend(warning_text(code) for code in detection.get("warnings", []))
        lines.extend(warning_text(code) for code in photo.get("analysis_warnings", []))
        self.quality_status.setText(" · ".join(dict.fromkeys(lines)))

    def _mode_changed(self):
        mode = self.mode_choice.currentData() or "pan"
        self.view.set_mode(mode)
        self.ap_choice.setVisible(mode == "ap")

    def _toggle_rectified(self, enabled):
        if enabled and self.region_error(require_mask=False):
            self.rectify_action.setChecked(False)
            return
        if not self.view.set_rectified(enabled):
            self.rectify_action.blockSignals(True)
            self.rectify_action.setChecked(self.view.rectified)
            self.rectify_action.blockSignals(False)

    def assign_points(self, assignments, *, commit=True):
        changed_surfaces = set()
        for point_id, surface_id in assignments.items():
            if not self._document or not isinstance(self._document.get(point_id), ElecPoint):
                raise ValueError("AP existiert nicht")
            if surface_id and self.config["surfaces"].get(surface_id, {}).get("status") != "current":
                raise ValueError("Flaeche fehlt oder ist veraltet")
        for point_id, surface_id in assignments.items():
            old = self.config.setdefault("assignments", {}).get(point_id, "")
            if old == surface_id and all(marker.get("surface_id", "") == surface_id for marker in
                                       self.config.get("markers", {}).values() if marker.get("point_id") == point_id):
                continue
            changed_surfaces.update((old, surface_id))
            if surface_id:
                self.config["assignments"][point_id] = surface_id
            else:
                self.config["assignments"].pop(point_id, None)
            for marker in self.config.get("markers", {}).values():
                if marker.get("point_id") == point_id:
                    changed_surfaces.add(marker.get("surface_id", ""))
                    marker["surface_id"] = surface_id
                    marker.pop("reference", None)
        self._stale_surfaces(changed_surfaces)
        if commit:
            self._commit()

    def _stale_surfaces(self, surface_ids):
        for region in self.config.get("regions", {}).values():
            if region.get("surface_id") in surface_ids:
                region["status"] = "stale"
                region.pop("control", None)
                region.setdefault("quality", {}).update(control_distance_checked=False, status="stale")

    def register_reference(self, surface_id):
        staged = deepcopy(self.config)
        marker = add_reference_marker(staged, surface_id)
        self.config = staged
        self._commit()
        return marker["id"]

    def set_reference(self, marker_id, u_cm, v_cm, rotation_deg=0, measured=False,
                      ffb_offset_mm=0, v_source="Fertigfussboden"):
        import math

        if not all(math.isfinite(float(value)) for value in (u_cm, v_cm, rotation_deg, ffb_offset_mm)):
            raise ValueError("Referenzwerte muessen endlich sein")
        marker = self.config["markers"][marker_id]
        surface = self.config["surfaces"].get(marker.get("surface_id"), {})
        if surface.get("status") != "current":
            raise ValueError("Marker braucht eine aktuelle Flaeche")
        room_id = surface["room_id"]
        old_offset = self.config.setdefault("rooms", {}).setdefault(room_id, {}).get("ffb_offset_mm", 0)
        offset = float(ffb_offset_mm) if surface.get("kind") == "wall" else old_offset
        vertical = float(v_cm) * 10
        if surface.get("kind") == "wall" and v_source == "Rohboden":
            vertical -= offset
        reference = {"origin_mm": [float(u_cm) * 10, vertical], "rotation_deg": float(rotation_deg),
                 "v_source": v_source if surface.get("kind") == "wall" else "Flaechenursprung",
                 "v_input_mm": float(v_cm) * 10} if measured else None
        changed = marker.get("reference") != reference or old_offset != offset
        if not changed:
            return
        self.config["rooms"][room_id]["ffb_offset_mm"] = offset
        if reference is None:
            marker.pop("reference", None)
        else:
            marker["reference"] = reference
        affected = {marker["surface_id"]}
        if old_offset != offset:
            affected.update(key for key, value in self.config["surfaces"].items() if value.get("room_id") == room_id)
            for other in self.config.get("markers", {}).values():
                other_reference = other.get("reference")
                if (other is not marker and other.get("surface_id") in affected and other_reference
                        and other_reference.get("v_source") == "Rohboden"):
                    other_reference["origin_mm"][1] = other_reference["v_input_mm"] - offset
        self._stale_surfaces(affected)
        self._commit()

    def _surface_combo(self, selected="", suggestion=""):
        combo = QComboBox()
        combo.addItem("Nicht zugeordnet", "")
        for surface_id, surface in self.config.get("surfaces", {}).items():
            if surface.get("status") == "current":
                label = surface["name"] + (" (Vorschlag)" if surface_id == suggestion else "")
                combo.addItem(label, surface_id)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        return combo

    def _assignment_dialog(self):
        if self._document is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("AP-Flaechenzuordnung")
        dialog.resize(640, 430)
        layout = QVBoxLayout(dialog)
        points = self._document.elements_of(ElecPoint)
        table = QTableWidget(len(points), 2)
        table.setHorizontalHeaderLabels(["AP", "Bestaetigte Flaeche"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        choices = {}
        for row, point in enumerate(points):
            table.setItem(row, 0, QTableWidgetItem(f"{point.name} / {point.id}"))
            combo = self._surface_combo(self.config.get("assignments", {}).get(point.id, ""),
                                        nearest_surface(self._document, self.config, point.id))
            table.setCellWidget(row, 1, combo)
            choices[point.id] = combo
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self._safe(lambda: self.assign_points({key: combo.currentData() for key, combo in choices.items()}))

    def _reference_dialog(self):
        if self._surface_id not in self.config.get("surfaces", {}):
            self._show_error("Eine aktuelle Flaeche auswaehlen")
            return
        ReferenceDialog(self, self._surface_id).exec()

    def request_markers(self):
        if self._document is None:
            return
        def export():
            staged = deepcopy(self.config)
            ensure_markers(self._document, staged)
            self.config = staged
            self._commit()
            self.markers_requested.emit()
        self._safe(export)

    def _import_dialog(self):
        if not self.project_path:
            self._show_error("Projekt vor dem Fotoimport speichern")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Fotos importieren", "", "Fotos (*.jpg *.jpeg *.png)")
        if paths:
            self.import_files(paths)

    def import_files(self, paths):
        if not self.project_path or self._document is None:
            self._show_error("Projekt vor dem Fotoimport speichern")
            return False
        return self._start_job(paths)

    def _start_job(self, paths, photo_id=""):
        self.cancel_import()
        self._abort = threading.Event()
        generation = self._generation
        job = _PhotoJob(generation, paths, self.project_path, self.config, self._abort, photo_id)
        job.signals.progress.connect(self._job_progress)
        job.signals.finished.connect(self._job_finished)
        self._jobs[generation] = job
        self.cancel_action.setEnabled(True)
        self.statusBar().showMessage("Fotoanalyse laeuft")
        self._pool.start(job)
        return True

    @Slot(int, int, int, str)
    def _job_progress(self, generation, done, total, name):
        if generation == self._generation:
            self.statusBar().showMessage(f"Fotoanalyse {done}/{total}: {name}")

    @Slot(int, object)
    def _job_finished(self, generation, payload):
        self._jobs.pop(generation, None)
        if generation != self._generation or payload.get("cancelled"):
            return
        self._abort = None
        self.cancel_action.setEnabled(False)
        imported = self.apply_analysis_results(generation, payload.get("items", []))
        errors = payload.get("errors", [])
        self.statusBar().showMessage(f"{imported} Foto(s) uebernommen" + ("; " + " | ".join(errors) if errors else ""))

    def apply_analysis_results(self, generation, items):
        if generation != self._generation or self._document is None:
            return 0
        count = 0
        for item in items:
            meta, analysis = deepcopy(item["meta"]), deepcopy(item["analysis"])
            photo_id = item.get("photo_id", "")
            if not photo_id:
                if any(photo.get("sha256") == meta["sha256"] for photo in self.config["photos"].values()):
                    continue
                photo_id = _next_id(self.config["photos"], "PHOTO")
            else:
                if photo_id not in self.config["photos"] or self.config["photos"][photo_id]["sha256"] != meta["sha256"]:
                    continue
                for region in self.config["regions"].values():
                    if region.get("photo_id") == photo_id:
                        region["status"] = "stale"
            meta.update({"width": int(analysis["width"]), "height": int(analysis["height"]),
                         "detections": analysis.get("detections", []),
                         "analysis_warnings": analysis.get("warnings", []),
                         "analysis_status": "markers_detected" if analysis.get("detections") else "no_markers"})
            self.config["photos"][photo_id] = meta
            first_region_id = ""
            for value in analysis.get("regions", []):
                region_id = _next_id(self.config["regions"], "REGION")
                self.config["regions"][region_id] = {
                    **value, "id": region_id, "photo_id": photo_id, "photo_sha256": meta["sha256"],
                    "status": "current", "revision": 1,
                }
                if not first_region_id:
                    first_region_id = region_id
            self._photo_id = photo_id
            self._region_id = first_region_id
            count += 1
        if count:
            self._commit()
        return count

    def reanalyze_photo(self):
        if not self._photo_id or not self.project_path:
            return False
        photo = self.config["photos"][self._photo_id]
        try:
            path = verify_photo(self.project_path, photo)
        except (ValueError, OSError) as error:
            self._show_error(str(error))
            return False
        return self._start_job([str(path)], self._photo_id)

    def correct_marker_surfaces(self, corrections):
        point_assignments = {}
        for marker_id, surface_id in corrections.items():
            marker = self.config.get("markers", {}).get(marker_id)
            if not marker:
                raise ValueError("Unbekannte Marker koennen nicht als Projektmarker uebernommen werden")
            if surface_id and self.config["surfaces"].get(surface_id, {}).get("status") != "current":
                raise ValueError("Flaeche fehlt oder ist veraltet")
            point_id = marker.get("point_id")
            if point_id and self._document and isinstance(self._document.get(point_id), ElecPoint):
                if point_id in point_assignments and point_assignments[point_id] != surface_id:
                    raise ValueError("Mehrere Marker desselben AP brauchen dieselbe Flaeche")
                point_assignments[point_id] = surface_id
        affected = set()
        for marker_id, surface_id in corrections.items():
            marker = self.config["markers"][marker_id]
            if marker.get("surface_id", "") != surface_id:
                affected.update((marker.get("surface_id", ""), surface_id))
                marker["surface_id"] = surface_id
                marker.pop("reference", None)
        if point_assignments:
            self.assign_points(point_assignments, commit=False)
        self._stale_surfaces(affected)
        self._commit()

    def _correction_dialog(self):
        photo = self.config.get("photos", {}).get(self._photo_id, {})
        detections = photo.get("detections", [])
        if not detections:
            self._show_error("Keine Marker erkannt; Foto manuell zuordnen oder erneut analysieren")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Erkannte Marker / Flaechen")
        form = QFormLayout(dialog)
        combos = {}
        for detection in detections:
            marker_id = detection.get("marker_id", "")
            marker = self.config.get("markers", {}).get(marker_id)
            if not marker:
                form.addRow(QLabel(f"Code {detection.get('aruco_id')}: nicht registriert"))
                continue
            combo = self._surface_combo(marker.get("surface_id", ""))
            form.addRow(f"{marker_id} / {marker.get('point_id', 'Referenz')}", combo)
            combos[marker_id] = combo
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self._safe(lambda: self.correct_marker_surfaces({key: combo.currentData() for key, combo in combos.items()}))
            self.reanalyze_photo()

    def assign_photo(self, photo_id, surface_id):
        if surface_id and self.config["surfaces"].get(surface_id, {}).get("status") != "current":
            raise ValueError("Flaeche fehlt oder ist veraltet")
        self.config["photos"][photo_id]["manual_surface_id"] = surface_id
        self._commit()

    def _manual_assignment_dialog(self):
        if not self._photo_id:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Dokumentationsflaeche")
        form = QFormLayout(dialog)
        combo = self._surface_combo(self.config["photos"][self._photo_id].get("manual_surface_id", ""))
        form.addRow("Flaeche (keine Kalibrierung)", combo)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self._safe(lambda: self.assign_photo(self._photo_id, combo.currentData()))

    def edit_photo(self, photo_id, name, note, construction_state):
        self.config["photos"][photo_id].update(name=name, note=note, construction_state=construction_state)
        self._commit()

    def _metadata_dialog(self):
        if not self._photo_id:
            return
        photo = self.config["photos"][self._photo_id]
        dialog = QDialog(self)
        dialog.setWindowTitle("Fotometadaten")
        form = QFormLayout(dialog)
        fields = []
        for label, value in (("Name", photo.get("name", photo.get("original_name", ""))),
                             ("Notiz", photo.get("note", "")), ("Bauzustand", photo.get("construction_state", ""))):
            widget = QLineEdit(value)
            form.addRow(label, widget)
            fields.append(widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.edit_photo(self._photo_id, *(widget.text() for widget in fields))

    def delete_photo(self):
        if self._photo_id not in self.config.get("photos", {}):
            return
        regions = {key for key, value in self.config["regions"].items() if value.get("photo_id") == self._photo_id}
        self.config["measurements"] = {key: value for key, value in self.config["measurements"].items()
                                       if value.get("region_id") not in regions}
        for region_id in regions:
            del self.config["regions"][region_id]
        del self.config["photos"][self._photo_id]
        self._photo_id = self._region_id = ""
        self._commit()

    def relink_photo(self, photo_id, source_path):
        if not self.project_path:
            raise ValueError("Projektpfad fehlt")
        import hashlib
        import os
        import shutil
        import tempfile
        from uuid import uuid4

        with Path(source_path).open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        photo = self.config["photos"][photo_id]
        if digest != photo["sha256"].lower():
            raise ValueError("Nur ein byte-identisches Original darf neu verknuepft werden")
        image = read_photo(source_path)
        if (image.shape[1], image.shape[0]) != (photo["width"], photo["height"]):
            raise ValueError("Kanonische Fotogroesse stimmt nicht ueberein")
        destination = resolve_photo_path(self.project_path, photo["path"])
        managed_directory = resolve_photo_path(self.project_path, f"photos/{self.config['project_uuid']}/.relink").parent
        damaged = False
        if destination.is_file():
            try:
                verify_photo(self.project_path, photo)
            except ValueError:
                damaged = True
        if damaged and destination.parent == managed_directory:
            with tempfile.NamedTemporaryFile(dir=managed_directory, prefix=".relink-", delete=False) as handle:
                staged = Path(handle.name)
            try:
                shutil.copyfile(source_path, staged)
                root = Path(self.project_path).parent.resolve()
                verify_photo(self.project_path, {**photo, "path": staged.relative_to(root).as_posix()})
                backup = destination.with_name(destination.name + ".corrupt-" + uuid4().hex)
                shutil.copy2(destination, backup)
                os.replace(staged, destination)
            finally:
                staged.unlink(missing_ok=True)
        else:
            meta = import_photo(source_path, self.project_path, self.config["project_uuid"])
            photo["path"] = meta["path"]
        self._commit()

    def _relink_dialog(self):
        if self._photo_id:
            path, _ = QFileDialog.getOpenFileName(self, "Identisches Original", "", "Fotos (*.jpg *.jpeg *.png)")
            if path:
                self._safe(lambda: self.relink_photo(self._photo_id, path))

    def _start_mask(self):
        if not self._region_id or self.view.image is None:
            self._show_error("Zuerst ein Foto mit kalibrierter Region auswaehlen")
            return
        self.view.set_mode("mask")
        self.statusBar().showMessage("Messbereich: vier Eckpunkte der gewaehlten Ebene")

    def set_region_mask(self, points):
        try:
            polygon = _quad(points)
            if self.view.image is None:
                raise ValueError("Kein gueltiges Foto")
            height, width = self.view.image.shape[:2]
            if any(not (0 <= point[0] < width and 0 <= point[1] < height) for point in polygon):
                raise ValueError("Messbereich liegt ausserhalb des Fotos")
            region = self.config["regions"][self._region_id]
            region["mask_px"] = polygon
            self._commit()
            return True
        except (ValueError, KeyError) as error:
            self._show_error(str(error))
            return False

    def _measurement_endpoints(self, points, mode, marker_id=""):
        import numpy as np

        region = self.config["regions"][self._region_id]
        if mode in ("floor", "origin"):
            surface = self.config["surfaces"][region["surface_id"]]
            if not region.get("absolute_reference") or surface.get("kind") != "wall":
                raise ValueError("Boden / Wandursprung erfordern gemessenen absoluten Wandbezug")
            start = transform_points([points[0]], region["H"])[0]
            target = [start[0], 0] if mode == "floor" else [0, start[1]]
            inverse = np.linalg.inv(np.asarray(region["H"], dtype=float)).tolist()
            return [points[0], transform_points([target], inverse)[0]]
        if mode == "ap":
            photo = self.config["photos"][self._photo_id]
            marker = self.config["markers"].get(marker_id, {})
            if not marker.get("point_id") or marker.get("surface_id") != region["surface_id"]:
                raise ValueError("AP-Markermitte muss zur selben Flaeche gehoeren")
            candidates = [value for value in photo.get("detections", []) if value.get("marker_id") == marker_id]
            if len(candidates) != 1 or candidates[0].get("warnings"):
                raise ValueError("AP-Marker nicht eindeutig erkannt")
            corners = transform_points(candidates[0]["corners"], region["H"])
            center = np.mean(corners, axis=0).tolist()
            inverse = np.linalg.inv(np.asarray(region["H"], dtype=float)).tolist()
            return [points[0], transform_points([center], inverse)[0]]
        if len(points) != 2:
            raise ValueError("Zwei Bildpunkte erforderlich")
        return points

    def create_measurement(self, points, mode="distance", name="", marker_id=""):
        labels = {"distance": "Strecke", "horizontal": "Horizontal", "vertical": "Vertikal",
                  "floor": "Boden / FFB", "origin": "Wandursprung A", "ap": "AP-Markermitte"}
        if mode not in labels:
            raise ValueError("Unbekannte Messart")
        error = self.region_error()
        if error:
            raise ValueError(error)
        region = self.config["regions"][self._region_id]
        endpoints = self._measurement_endpoints(points, mode, marker_id)
        if any(not _contains(region["mask_px"], point) for point in endpoints):
            raise ValueError("Beide Messpunkte muessen im gewaehlten Messbereich liegen")
        result = measurement(region, endpoints)
        if region.get("control", {}).get("passed"):
            result["warnings"] = [code for code in result["warnings"] if code != "unchecked_control_distance"]
        value = abs(result["dx_mm"]) if mode in ("horizontal", "origin") else (
            abs(result["dy_mm"]) if mode in ("vertical", "floor") else result["distance_mm"])
        measurement_id = _next_id(self.config["measurements"], "PM")
        self.config["measurements"][measurement_id] = {
            "id": measurement_id, "region_id": self._region_id, "photo_id": self._photo_id,
            "name": name or labels[mode],
            "kind": mode, "points": deepcopy(endpoints), "value_mm": value, "metric": result,
            "marker_id": marker_id, "calibration_H": deepcopy(region["H"]),
            "calibration_revision": region.get("revision", 1), "photo_sha256": region["photo_sha256"],
            "control": deepcopy(region.get("control")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._commit()
        self.statusBar().showMessage(f"{value / 10:.1f} cm · " + " · ".join(warning_text(code) for code in result["warnings"]))
        return measurement_id

    def _measurement_points(self, points):
        if self.view.mode == "control":
            length, accepted = QInputDialog.getDouble(self, "Kontrollmass", "Tatsaechlich gemessene Strecke [cm]", 100, 0.01, 100000, 2)
            if accepted:
                self._safe(lambda: self.set_control_distance(points, length))
            self._mode_changed()
            return
        self._safe(lambda: self.create_measurement(points, self.view.mode, marker_id=self.ap_choice.currentData() or ""))

    def _start_control(self):
        if self.region_error():
            self._show_error(self.region_error())
            return
        self.view.set_mode("control")
        self.statusBar().showMessage("Unabhaengiges Kontrollmass: zwei Punkte")

    def set_control_distance(self, points, actual_cm):
        import math

        if self.region_error():
            raise ValueError(self.region_error())
        if not math.isfinite(actual_cm) or actual_cm <= 0:
            raise ValueError("Kontrollmass muss positiv und endlich sein")
        region = self.config["regions"][self._region_id]
        if any(not _contains(region["mask_px"], point) for point in points):
            raise ValueError("Kontrollmass ausserhalb des Messbereichs")
        predicted = measurement(region, points)["distance_mm"]
        residual = abs(predicted - actual_cm * 10)
        passed = residual <= 20
        region["control"] = {"points": deepcopy(points), "actual_mm": actual_cm * 10,
                             "predicted_mm": predicted, "residual_mm": residual, "passed": passed}
        region.setdefault("quality", {}).update(control_distance_checked=passed, status="validated" if passed else "limited")
        self._commit()
        return passed

    def rename_measurement(self, measurement_id, name):
        self.config["measurements"][measurement_id]["name"] = name.strip() or measurement_id
        self._commit()

    def _rename_measurement_dialog(self):
        item = self.measurement_list.currentItem()
        if item:
            measurement_id = item.data(Qt.UserRole)
            name, accepted = QInputDialog.getText(self, "Messung umbenennen", "Name", text=self.config["measurements"][measurement_id]["name"])
            if accepted:
                self.rename_measurement(measurement_id, name)

    def delete_measurement(self):
        item = self.measurement_list.currentItem()
        if item:
            self.config["measurements"].pop(item.data(Qt.UserRole), None)
            self._commit()

    def closeEvent(self, event):
        self.cancel_import()
        if self.save_settings():
            event.accept()
        else:
            event.ignore()