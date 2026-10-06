"""Dock zur Planung von Aderverbindungen in Unterputz-Verteilerdosen."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QDockWidget, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from model.document import Document
from model.elements import ElecCable, ElecPoint


class UpDistributionDock(QDockWidget):
    """AP-Auswahl und eingebetteter Editor für UP-Verteilungen."""

    config_about_to_save = Signal(str)
    config_saved = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Unterputz-Verteilungen", parent)
        self.setObjectName("dock_up_distribution")

        self._document: Document | None = None
        self._point_id = ""
        self._editor: QWidget | None = None
        self._saving = False
        self._document_callbacks: list[tuple[object, object]] = []

        self._content = QWidget(self)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        selector_row = QWidget(self._content)
        selector_layout = QHBoxLayout(selector_row)
        selector_layout.setContentsMargins(6, 4, 6, 4)
        selector_layout.setSpacing(6)
        selector_layout.addWidget(QLabel("Anschlusspunkt:", selector_row))
        self._selector = QComboBox(selector_row)
        self._selector.currentIndexChanged.connect(self._on_selector_changed)
        selector_layout.addWidget(self._selector, 1)
        layout.addWidget(selector_row)

        self._placeholder = QLabel("Keine Unterputz-Verteilung ausgewählt", self._content)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #777; padding: 12px;")
        layout.addWidget(self._placeholder, 1)
        self.setWidget(self._content)

    def set_document(self, document: Document | None) -> None:
        self._disconnect_document()
        self._document = document
        self._point_id = ""
        if document is not None:
            for emitter in (
                document.element_changed,
                document.element_added,
                document.element_removed,
                document.structure_changed,
            ):
                emitter.connect(self._on_document_changed)
                self._document_callbacks.append((emitter, self._on_document_changed))
        self._sync_selector()
        self._refresh_editor()

    def select_point(self, point_id: str) -> None:
        if self._document is None:
            return
        target_id = point_id if self._is_up_point(point_id) else self._point_id
        if target_id and target_id != self._point_id and not self._editor_is_valid():
            self.show()
            self.raise_()
            return
        index = self._selector.findData(target_id)
        self._selector.setCurrentIndex(index if index >= 0 else 0)
        if target_id and self._point_id != target_id:
            self._point_id = target_id
            self._refresh_editor()
        self.show()
        self.raise_()

    def active_point_id(self) -> str:
        return self._point_id

    def _disconnect_document(self) -> None:
        for emitter, callback in self._document_callbacks:
            emitter.disconnect(callback)
        self._document_callbacks.clear()

    def _is_up_point(self, point_id: str) -> bool:
        if self._document is None:
            return False
        point = self._document.get(point_id)
        return isinstance(point, ElecPoint) and str(point.ap_type or "").strip() == "up_distribution"

    def _sync_selector(self) -> None:
        self._selector.blockSignals(True)
        self._selector.clear()
        points: list[ElecPoint] = []
        if self._document is not None:
            points = [
                point
                for point in self._document.elements.get("elec_points", {}).values()
                if isinstance(point, ElecPoint)
                and str(point.ap_type or "").strip() == "up_distribution"
            ]
        points.sort(key=lambda point: (str(point.name or point.id).casefold(), point.id))
        if not points:
            self._selector.addItem("Keine UP-Verteilungen vorhanden", "")
            self._selector.setEnabled(False)
            self._point_id = ""
        else:
            self._selector.setEnabled(True)
            for point in points:
                self._selector.addItem(f"{point.name or point.id} ({point.id})", point.id)
            if self._point_id not in {point.id for point in points}:
                self._point_id = points[0].id
            index = self._selector.findData(self._point_id)
            if index >= 0:
                self._selector.setCurrentIndex(index)
        self._selector.blockSignals(False)

    def _on_selector_changed(self, _index: int) -> None:
        selected_id = str(self._selector.currentData() or "").strip()
        if selected_id == self._point_id:
            return
        if self._editor is not None and not self._editor_is_valid():
            old_index = self._selector.findData(self._point_id)
            self._selector.blockSignals(True)
            self._selector.setCurrentIndex(max(0, old_index))
            self._selector.blockSignals(False)
            return
        self._point_id = selected_id
        self._refresh_editor()

    def _on_document_changed(self, element_id: str = "") -> None:
        if self._saving or self._document is None:
            return
        element = self._document.get(str(element_id)) if element_id else None
        if not element_id:
            self._sync_selector()
            if not self._is_up_point(self._point_id):
                self._refresh_editor()
            self._refresh_cable_choices()
            return
        if isinstance(element, ElecPoint) or element is None:
            previous_point_id = self._point_id
            self._sync_selector()
            if self._point_id != previous_point_id or not self._is_up_point(self._point_id):
                self._refresh_editor()
            self._refresh_cable_choices()
            return
        if isinstance(element, ElecCable):
            self._refresh_cable_choices()

    def _cable_choices_for_point(self, point: ElecPoint) -> list[tuple[str, str]]:
        if self._document is None:
            return []
        choices: list[tuple[str, str]] = []
        for cable_id, cable in self._document.elements.get("elec_cables", {}).items():
            start_ap = str(cable.start_ap or cable.geom.get("cable_start_ap") or "").strip()
            end_ap = str(cable.end_ap or cable.geom.get("cable_end_ap") or "").strip()
            if point.id in {start_ap, end_ap}:
                choices.append((cable_id, str(cable.name or cable_id)))
        return sorted(choices, key=lambda entry: (entry[1].casefold(), entry[0]))

    def _refresh_cable_choices(self) -> None:
        point = self._document.get(self._point_id) if self._document is not None else None
        if self._editor is None or not isinstance(point, ElecPoint):
            return
        self._editor.set_cable_choices(self._cable_choices_for_point(point))

    def _refresh_editor(self) -> None:
        if self._editor is not None:
            self._editor.deleteLater()
            self._editor = None
        point = self._document.get(self._point_id) if self._document is not None and self._point_id else None
        if not isinstance(point, ElecPoint) or str(point.ap_type or "").strip() != "up_distribution":
            self._placeholder.setText(
                "Keine Dokumentdaten vorhanden" if self._document is None
                else "Keine Unterputz-Verteilung ausgewählt"
            )
            self._placeholder.show()
            return

        from gui.parameter_panel import UpDistributionDialog  # noqa: PLC0415

        editor = UpDistributionDialog(
            config=point.data.get("up_distribution_config") or {},
            cable_choices=self._cable_choices_for_point(point),
            parent=self,
            show_buttons=False,
            strict_cable_choices=True,
        )
        editor.setWindowFlag(Qt.Dialog, False)
        editor.setWindowModality(Qt.NonModal)
        editor.setAttribute(Qt.WA_DeleteOnClose, False)
        editor.config_changed.connect(self._save_editor_if_valid)
        editor.validity_changed.connect(self._on_validity_changed)
        self._editor = editor
        self._placeholder.hide()
        self._content.layout().addWidget(editor)

    def _editor_is_valid(self) -> bool:
        if self._editor is None:
            return True
        error = self._editor._validate_config()
        return not error

    def _on_validity_changed(self, valid: bool, message: str) -> None:
        if not valid:
            self._placeholder.setText(message)
            self._placeholder.setToolTip(message)

    def _save_editor_if_valid(self) -> None:
        if self._saving or self._document is None or not self._point_id or self._editor is None:
            return
        if not self._editor_is_valid():
            return
        point = self._document.get(self._point_id)
        if not isinstance(point, ElecPoint):
            return
        config = self._editor.get_config()
        if point.data.get("up_distribution_config") == config:
            return
        self.config_about_to_save.emit(self._point_id)
        self._saving = True
        try:
            point.data["up_distribution_config"] = config
            self._document.element_changed.emit(self._point_id)
        finally:
            self._saving = False
        self.config_saved.emit(self._point_id)