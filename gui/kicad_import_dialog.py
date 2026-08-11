from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from logic.kicad_import import KiCadImportPreview, KiCadScanResult


class KiCadImportDialog(QDialog):
    def __init__(
        self,
        scan_result: KiCadScanResult,
        previews: list[KiCadImportPreview],
        phase: str = "cables",
        extra_warnings: list[str] | None = None,
        floorplan_choices: list[tuple[str, str]] | None = None,
        room_choices_by_floorplan: dict[str, list[tuple[str, str]]] | None = None,
        initial_ap_assignments: dict[str, dict[str, str]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._scan_result = scan_result
        self._previews = {preview.candidate_key: preview for preview in previews}
        self._phase = phase
        self._extra_warnings = list(extra_warnings or [])
        self._floorplan_choices = list(floorplan_choices or [])
        self._room_choices_by_floorplan = dict(room_choices_by_floorplan or {})
        self._initial_ap_assignments = dict(initial_ap_assignments or {})
        self._ap_target_combos: dict[str, QComboBox] = {}
        is_ap_phase = phase == "aps"
        self.setWindowTitle("KiCad-APs vorbereiten" if is_ap_phase else "KiCad-Kabel importieren")
        self.resize(1080, 620)

        root = QVBoxLayout(self)

        summary = QLabel(
            f"Quelle: {scan_result.root_path.name}\n"
            f"Projekt-UUID: {scan_result.project_uuid or 'unbekannt'}\n"
            f"Gefundene Kabelkandidaten: {len(scan_result.candidates)}\n"
            f"Gefundene Textfeld-Kandidaten: {len(scan_result.textfield_candidates)}"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        content = QHBoxLayout()
        root.addLayout(content, stretch=1)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(
            [
                "Import",
                "Status",
                "AP" if is_ap_phase else "Kabel",
                "Spezifikation",
                "Quelle",
                "Ziel" if is_ap_phase else "Bestehend",
                "Platzierung" if is_ap_phase else "AP-Import",
                "AP-Import" if is_ap_phase else "AP-Vorschlag",
            ]
        )
        self.tree.itemSelectionChanged.connect(self._update_detail)
        content.addWidget(self.tree, stretch=3)

        self.detail_box = QTextEdit(self)
        self.detail_box.setReadOnly(True)
        content.addWidget(self.detail_box, stretch=2)

        self.warning_box = QTextEdit(self)
        self.warning_box.setReadOnly(True)
        self.warning_box.setPlaceholderText("Keine Warnungen")
        self.warning_box.setMaximumHeight(120)
        root.addWidget(self.warning_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal,
            self,
        )
        buttons.button(QDialogButtonBox.Ok).setText("Weiter" if is_ap_phase else "Importieren")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate()

    def selected_keys(self) -> list[str]:
        selected: list[str] = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.checkState(0) == Qt.Checked:
                key = item.data(0, Qt.UserRole)
                if key:
                    selected.append(str(key))
        return selected

    def selected_ap_assignments(self) -> dict[str, dict[str, str]]:
        assignments: dict[str, dict[str, str]] = {}
        for key, combo in self._ap_target_combos.items():
            data = combo.currentData()
            if not isinstance(data, dict):
                data = {}
            assignments[key] = {
                "floor_plan_id": str(data.get("floor_plan_id", "") or ""),
                "room_id": str(data.get("room_id", "") or ""),
            }
        return assignments

    def _populate(self) -> None:
        is_ap_phase = self._phase == "aps"

        if is_ap_phase:
            phase_previews = sorted(
                self._previews.values(),
                key=lambda preview: (preview.cable_name.lower(), preview.source, preview.candidate_key.lower()),
            )
            for preview in phase_previews:
                item = QTreeWidgetItem(self.tree)
                item.setData(0, Qt.UserRole, preview.candidate_key)
                item.setCheckState(0, Qt.Unchecked if preview.status == "unchanged" else Qt.Checked)
                item.setText(1, preview.status)
                item.setText(2, preview.cable_name)
                item.setText(3, preview.cable_type or "-")
                if preview.source == "ap_group":
                    item.setText(4, "AP Group")
                else:
                    item.setText(4, "Text Field")
                item.setText(7, preview.ap_import_action or "-")
                self._attach_ap_assignment_widgets(item, preview)
        else:
            for candidate in self._scan_result.candidates.values():
                preview = self._previews.get(candidate.key)
                if preview is None:
                    continue
                item = QTreeWidgetItem(self.tree)
                item.setData(0, Qt.UserRole, candidate.key)
                item.setCheckState(0, Qt.Unchecked if preview.status == "unchanged" else Qt.Checked)
                item.setText(1, preview.status)
                item.setText(2, candidate.base_name or candidate.pin_name_raw)
                item.setText(3, candidate.normalized_spec or candidate.spec_raw)
                item.setText(4, "Sheet Pin")
                item.setText(5, preview.existing_cable_id or "-")
                item.setText(6, preview.ap_import_action or "-")
                item.setText(7, self._ap_summary_text(preview))

            for tf_candidate in self._scan_result.textfield_candidates.values():
                preview = self._previews.get(tf_candidate.key)
                if preview is None:
                    continue
                item = QTreeWidgetItem(self.tree)
                item.setData(0, Qt.UserRole, tf_candidate.key)
                item.setCheckState(0, Qt.Unchecked if preview.status == "unchanged" else Qt.Checked)
                item.setText(1, preview.status)
                item.setText(2, tf_candidate.cable_name)
                item.setText(3, tf_candidate.matched_spec or "-")
                item.setText(4, "Text Field")
                item.setText(5, preview.existing_cable_id or "-")
                item.setText(6, preview.ap_import_action or "-")
                item.setText(7, self._ap_summary_text(preview))

        warnings = [warning.message for warning in self._scan_result.warnings]
        warnings.extend(self._extra_warnings)
        self.warning_box.setPlainText("\n".join(warnings))
        if self.tree.topLevelItemCount() > 0:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self._update_detail()

    def _update_detail(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            self.detail_box.clear()
            return
        key = str(item.data(0, Qt.UserRole) or "")
        preview = self._previews.get(key)
        if preview is None:
            self.detail_box.clear()
            return

        # Check if this is a sheet pin candidate or text field candidate
        candidate = self._scan_result.candidates.get(key)
        tf_candidate = self._scan_result.textfield_candidates.get(key)
        ap_group_candidate = self._scan_result.ap_group_candidates.get(key)

        lines = [
            f"Quelle: {preview.source}",
            f"Status: {preview.status}",
            f"Sync-Key: {preview.sync_key}",
            "",
            "Feld-Diffs:",
        ]
        for diff in preview.diffs:
            marker = "geändert" if diff.changed else "gleich"
            lines.append(
                f"- {diff.field}: aktuell='{diff.current_value}' -> import='{diff.imported_value}' ({marker})"
            )

        lines.append("")
        lines.append(f"AP-Matching: {preview.ap_match_status}")
        if preview.ap_import_action:
            lines.append(f"AP-Import: {preview.ap_import_action}")
        if self._phase == "aps":
            target_combo = self._ap_target_combos.get(key)
            if target_combo is not None:
                target_data = target_combo.currentData()
                if not isinstance(target_data, dict):
                    target_data = {}
                floorplan_label, room_label = self._split_target_label(target_combo.currentText() or "-")
                lines.append(f"Ziel: {target_combo.currentText() or '-'}")
                if floorplan_label:
                    lines.append(f"Grundriss: {floorplan_label}")
                if room_label and room_label != "-":
                    lines.append(f"Raum: {room_label}")
                room_id = str(target_data.get("room_id", "") or "")
                if room_id:
                    lines.append("Platzierung: Raumzentrum")
                else:
                    lines.append("Platzierung: Textfeldposition")
        if preview.ap_matches:
            for match in preview.ap_matches[:5]:
                label = match.point_name or match.point_id
                if match.floor_plan_id:
                    label = f"{label} [{match.floor_plan_id}]"
                lines.append(
                    f"- {match.point_id}: {label} | Score {match.score} | {match.reason}"
                )
        else:
            lines.append("- keine Vorschläge")

        if candidate:
            lines.append("")
            lines.append("Verbindungen (Sheet Pins):")
            lines.extend(
                (
                    f"- {self._format_hierarchy_path(ref.hierarchy_path)}"
                    f" | {ref.sheet_name} | {ref.sheet_file} | {ref.pin_direction}"
                )
                for ref in candidate.pin_refs
            )
            if candidate.local_labels:
                lines.append("")
                lines.append("Lokale Labels:")
                lines.extend(f"- {label}" for label in sorted(candidate.local_labels))

        if tf_candidate:
            lines.append("")
            lines.append("Textfeld-Metadata:")
            lines.append(f"- AP_NAME: {tf_candidate.source_metadata.ap_name}")
            if tf_candidate.source_metadata.room:
                lines.append(f"- ROOM: {tf_candidate.source_metadata.room}")
            if tf_candidate.best_matched_candidate:
                lines.append(f"- Auto-matched Cable: {tf_candidate.best_matched_candidate.base_name}")

        if ap_group_candidate:
            lines.append("")
            lines.append("AP-Gruppen-Metadata:")
            lines.append(f"- Name: {ap_group_candidate.group_name}")
            lines.append(f"- Group UUID: {ap_group_candidate.group_uuid}")
            lines.append(f"- Frame UUID: {ap_group_candidate.frame_uuid}")
            x_min, y_min, x_max, y_max = ap_group_candidate.frame_bounds
            lines.append(f"- Rahmen: x=[{x_min:.3f}, {x_max:.3f}], y=[{y_min:.3f}, {y_max:.3f}]")
            if ap_group_candidate.bus_hits:
                lines.append(f"- Bus-Treffer: {len(ap_group_candidate.bus_hits)}")
                for bus in ap_group_candidate.bus_hits[:10]:
                    lines.append(f"  - {bus.uuid} ({bus.sheet_file})")
            else:
                lines.append("- Bus-Treffer: 0")

        self.detail_box.setPlainText("\n".join(lines))

    def _attach_ap_assignment_widgets(self, item: QTreeWidgetItem, preview: KiCadImportPreview) -> None:
        key = str(preview.candidate_key or "")
        assignment = self._initial_ap_assignments.get(key, {})
        floor_plan_id = str(assignment.get("floor_plan_id", "") or "")
        room_id = str(assignment.get("room_id", "") or "")

        target_combo = QComboBox(self.tree)
        target_combo.addItem("-", {"floor_plan_id": "", "room_id": ""})

        selected_index = 0
        next_index = 1
        for floor_index, (fp_id, fp_name) in enumerate(self._floorplan_choices):
            if floor_index > 0:
                target_combo.insertSeparator(target_combo.count())
            floor_data = {"floor_plan_id": fp_id, "room_id": ""}
            target_combo.addItem(f"{fp_name}: -", floor_data)
            if floor_plan_id == fp_id and not room_id:
                selected_index = next_index
            next_index += 1
            for current_room_id, room_name in self._room_choices_by_floorplan.get(fp_id, []):
                room_data = {"floor_plan_id": fp_id, "room_id": current_room_id}
                target_combo.addItem(f"{fp_name}: {room_name}", room_data)
                if floor_plan_id == fp_id and room_id == current_room_id:
                    selected_index = next_index
                next_index += 1

        target_combo.setCurrentIndex(selected_index)
        target_combo.currentIndexChanged.connect(lambda _idx: self._update_detail())
        self.tree.setItemWidget(item, 5, target_combo)
        self._ap_target_combos[key] = target_combo

        target_data = target_combo.currentData()
        if not isinstance(target_data, dict):
            target_data = {}
        item.setText(6, "Raumzentrum" if str(target_data.get("room_id", "") or "") else "Textfeldposition")
        target_combo.currentIndexChanged.connect(
            lambda _idx, row=item, combo=target_combo: row.setText(
                6,
                "Raumzentrum"
                if isinstance(combo.currentData(), dict) and str(combo.currentData().get("room_id", "") or "")
                else "Textfeldposition",
            )
        )

    @staticmethod
    def _split_target_label(label: str) -> tuple[str, str]:
        left, sep, right = str(label or "").partition(": ")
        if not sep:
            return str(label or ""), ""
        return left, right

    @staticmethod
    def _ap_summary_text(preview: KiCadImportPreview) -> str:
        if not preview.ap_matches:
            return "keiner"
        top = preview.ap_matches[0]
        if preview.ap_match_status == "matched":
            return f"{top.point_id} ({top.score})"
        return f"mehrdeutig ({len(preview.ap_matches)})"

    @staticmethod
    def _format_hierarchy_path(path: tuple[str, ...]) -> str:
        if not path:
            return "(root)"
        return " > ".join(str(part or "?") for part in path)

    @classmethod
    def _hierarchy_summary_text(cls, candidate) -> str:
        paths = {
            cls._format_hierarchy_path(ref.hierarchy_path)
            for ref in candidate.pin_refs
        }
        return " | ".join(sorted(paths))