"""Dock zur Auswahl und Planung einer Unterverteilung (UV)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QDockWidget, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from model.document import Document
from model.elements import ElecPoint


class UvPlanningDock(QDockWidget):
    """Dock mit Auswahl der aktiven UV und eingebettetem UV-Editor."""

    # Emitted after a live edit has been persisted into point.data["uv_config"],
    # so hosts (AppWindow) can mark the project as dirty.
    config_saved = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Unterverteilungen", parent)
        self.setObjectName("dock_uv_planning")

        self._document: Document | None = None
        self._point_id = ""
        self._saving = False
        self._selector = QComboBox(self)
        self._selector.currentIndexChanged.connect(self._on_selector_changed)

        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        selector_row = QWidget(self._content)
        selector_layout = QHBoxLayout(selector_row)
        selector_layout.setContentsMargins(6, 4, 6, 4)
        selector_layout.setSpacing(6)
        selector_layout.addWidget(QLabel("UV:", selector_row))
        selector_layout.addWidget(self._selector, 1)
        self._content_layout.addWidget(selector_row)
        self._placeholder = QLabel("Keine Unterverteilung ausgewählt")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #a0a0a0; padding: 12px;")
        self._content_layout.addWidget(self._placeholder)
        self.setWidget(self._content)

        self._editor = None

    def select_point(self, point_id: str) -> None:
        if self._document is None:
            return
        uv_point_ids = [
            point.id
            for point in self._document.all_elements()
            if isinstance(point, ElecPoint) and str(point.ap_type or "").strip() == "uv"
        ]
        if point_id in uv_point_ids:
            self._point_id = point_id
        index = self._selector.findData(self._point_id)
        if index >= 0:
            self._selector.setCurrentIndex(index)
        else:
            self._selector.setCurrentIndex(0)
        self.show()
        self.raise_()

    def set_document(self, document: Document | None) -> None:
        self._document = document
        self._point_id = ""
        self._sync_selector()
        self._refresh_editor()

    def active_point_id(self) -> str:
        return self._point_id

    def get_config(self) -> dict:
        point = self._document.get(self._point_id) if self._document is not None else None
        if point is None:
            return {}
        return dict(point.data.get("uv_config") or {})

    def _sync_selector(self) -> None:
        if self._document is None:
            self._selector.blockSignals(True)
            self._selector.clear()
            self._selector.addItem("Keine UV vorhanden")
            self._selector.blockSignals(False)
            return

        uv_point_ids = [
            point.id
            for point in self._document.all_elements()
            if isinstance(point, ElecPoint) and str(point.ap_type or "").strip() == "uv"
        ]
        uv_point_ids = sorted(set(uv_point_ids), key=lambda pid: self._document.get(pid).name.lower() if self._document.get(pid) else pid.lower())

        self._selector.blockSignals(True)
        self._selector.clear()
        if not uv_point_ids:
            self._selector.addItem("Keine UV vorhanden")
            self._selector.setEnabled(False)
            self._point_id = ""
        else:
            self._selector.setEnabled(True)
            for point_id in uv_point_ids:
                point = self._document.get(point_id)
                label = point.name or point_id if point is not None else point_id
                self._selector.addItem(f"{label} ({point_id})", point_id)
            if self._point_id not in uv_point_ids:
                self._point_id = uv_point_ids[0]
            index = self._selector.findData(self._point_id)
            if index >= 0:
                self._selector.setCurrentIndex(index)
        self._selector.blockSignals(False)

    def _on_selector_changed(self, _index: int) -> None:
        selected_point_id = self._selector.currentData()
        if not isinstance(selected_point_id, str):
            return
        self._point_id = selected_point_id
        self._refresh_editor()

    def _refresh_editor(self) -> None:
        if self._document is None:
            self._show_placeholder("Keine Dokumentdaten vorhanden")
            return

        point = self._document.get(self._point_id) if self._point_id else None
        if point is None or str(point.ap_type or "").strip() != "uv":
            self._show_placeholder("Keine Unterverteilung ausgewählt")
            return

        self._show_editor(point)

    def _show_placeholder(self, text: str) -> None:
        if self._editor is not None:
            self._editor.deleteLater()
            self._editor = None
        self._placeholder.setText(text)
        self._placeholder.show()

    def _cable_choices_for_point(self, point: ElecPoint) -> list[str]:
        if self._document is None:
            return []
        point_id = str(point.id or "").strip()
        choices: list[str] = []
        for cable in self._document.elements.get("elec_cables", {}).values():
            start_ap = str(getattr(cable, "start_ap", "") or "").strip()
            end_ap = str(getattr(cable, "end_ap", "") or "").strip()
            if point_id not in {start_ap, end_ap}:
                continue
            name = (cable.name or cable.id or "").strip()
            if name and name not in choices:
                choices.append(name)
        return choices

    def _show_editor(self, point: ElecPoint) -> None:
        if self._editor is not None:
            self._editor.deleteLater()
            self._editor = None

        from gui.parameter_panel import UvConfigDialog  # noqa: PLC0415

        cable_choices = self._cable_choices_for_point(point)

        try:
            self._editor = UvConfigDialog(
                config=point.data.get("uv_config") or {},
                cable_choices=cable_choices,
                parent=self,
                show_buttons=False,
            )
        except TypeError:
            self._editor = UvConfigDialog(
                config=point.data.get("uv_config") or {},
                cable_choices=cable_choices,
                parent=self,
            )
        if hasattr(self._editor, "setWindowFlag"):
            self._editor.setWindowFlag(Qt.Dialog, False)
        if hasattr(self._editor, "setWindowModality"):
            self._editor.setWindowModality(Qt.NonModal)
        if hasattr(self._editor, "setAttribute"):
            self._editor.setAttribute(Qt.WA_DeleteOnClose, False)
        if hasattr(self._editor, "accepted"):
            self._editor.accepted.connect(self._save_editor)
        if hasattr(self._editor, "rejected"):
            self._editor.rejected.connect(self._refresh_editor)
        # The embedded editor is shown with show_buttons=False (no OK/Cancel),
        # so "accepted" never fires. Persist live on every edit instead, so
        # slot/busbar changes made in this dock are not silently lost (they
        # would otherwise only exist inside the QDialog widget's own state).
        if hasattr(self._editor, "config_changed"):
            self._editor.config_changed.connect(self._save_editor)

        self._placeholder.hide()
        if isinstance(self._editor, QWidget):
            self._content_layout.addWidget(self._editor)

    def _save_editor(self) -> None:
        # Re-entrancy guard: get_config() internally normalizes busbars, which
        # calls _refresh_visual() -> config_changed -> _save_editor() again.
        if self._saving:
            return
        if self._document is None or not self._point_id:
            return
        point = self._document.get(self._point_id)
        if point is None:
            return
        if self._editor is None:
            return
        self._saving = True
        try:
            point.data["uv_config"] = self._editor.get_config()
        finally:
            self._saving = False
        self._document.element_changed.emit(self._point_id)
        self.config_saved.emit(self._point_id)
