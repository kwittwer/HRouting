from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from logic.kicad_import import KiCadImportPreview, KiCadScanResult


class KiCadImportDialog(QDialog):
    """Two-phase KiCad import dialog.

    phase='aps'    – AP-group import; room dropdown locked when AP already exists.
    phase='cables' – KBL-Bus cable import; editable name, type, start AP, end AP.
    """
    def __init__(
        self,
        scan_result: KiCadScanResult,
        previews: list[KiCadImportPreview],
        phase: str = "cables",
        extra_warnings: list[str] | None = None,
        floorplan_choices: list[tuple[str, str]] | None = None,
        room_choices_by_floorplan: dict[str, list[tuple[str, str]]] | None = None,
        initial_ap_assignments: dict[str, dict[str, str]] | None = None,
        cable_ap_choices: list[tuple[str, str]] | None = None,
        initial_cable_endpoints: dict[str, dict[str, str]] | None = None,
        cable_type_choices: list[str] | None = None,
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
        self._cable_ap_choices = list(cable_ap_choices or [])
        self._initial_cable_endpoints = dict(initial_cable_endpoints or {})
        self._cable_type_choices = list(cable_type_choices or [])

        # Widget registries
        self._ap_target_combos: dict[str, QComboBox] = {}
        # cable_widgets: key -> (name_edit, type_combo, start_combo, end_combo)
        self._cable_widgets: dict[str, tuple[QLineEdit, QComboBox, QComboBox, QComboBox]] = {}
        # kept for backward-compat
        self._cable_endpoint_combos: dict[str, tuple[QComboBox, QComboBox]] = {}

        is_ap_phase = phase == "aps"
        self.setWindowTitle("KiCad-APs importieren" if is_ap_phase else "KiCad-Kabel importieren")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.resize(1200, 680)

        root = QVBoxLayout(self)

        summary = QLabel(
            f"Quelle: {scan_result.root_path.name}   |   "
            f"Projekt-UUID: {scan_result.project_uuid or 'unbekannt'}   |   "
            f"AP-Gruppen: {len(scan_result.ap_group_candidates)}   |   "
            f"KBL-Busse: {len(scan_result.kbl_bus_candidates)}"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        content = QHBoxLayout()
        root.addLayout(content, stretch=1)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(6)
        if is_ap_phase:
            self.tree.setHeaderLabels(
                ["Import", "Status", "AP-Name", "Quelle", "Raum", "Platzierung"]
            )
        else:
            self.tree.setHeaderLabels(
                ["Import", "Status", "Name", "Typ", "Start AP", "Ziel AP"]
            )

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)

        self.tree.itemSelectionChanged.connect(self._update_detail)
        content.addWidget(self.tree, stretch=3)

        self.detail_box = QTextEdit(self)
        self.detail_box.setReadOnly(True)
        content.addWidget(self.detail_box, stretch=2)

        self.warning_box = QTextEdit(self)
        self.warning_box.setReadOnly(True)
        self.warning_box.setPlaceholderText("Keine Warnungen")
        self.warning_box.setMaximumHeight(100)

        bulk_toggle_row = QHBoxLayout()
        select_all_btn = QPushButton("Alle auswählen", self)
        deselect_all_btn = QPushButton("Alle abwählen", self)
        select_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        deselect_all_btn.clicked.connect(lambda: self._set_all_checks(False))
        bulk_toggle_row.addWidget(select_all_btn)
        bulk_toggle_row.addWidget(deselect_all_btn)
        bulk_toggle_row.addStretch(1)
        root.addLayout(bulk_toggle_row)

        root.addWidget(self.warning_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal,
            self,
        )
        buttons.button(QDialogButtonBox.Ok).setText(
            "Importieren" if is_ap_phase else "Kabel importieren"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

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

    def selected_cable_data(self) -> dict[str, dict[str, str]]:
        """Return edited cable fields (name, type, start_ap_id, end_ap_id) per key."""
        result: dict[str, dict[str, str]] = {}
        for key, (name_edit, type_combo, start_combo, end_combo) in self._cable_widgets.items():
            result[key] = {
                "name": name_edit.text().strip(),
                "type": type_combo.currentText().strip(),
                "start_ap_id": str(start_combo.currentData() or ""),
                "end_ap_id": str(end_combo.currentData() or ""),
            }
        return result

    def selected_cable_endpoints(self) -> dict[str, dict[str, str]]:
        """Backward-compat alias – delegates to selected_cable_data."""
        return {
            key: {"start_ap_id": v["start_ap_id"], "end_ap_id": v["end_ap_id"]}
            for key, v in self.selected_cable_data().items()
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def accept(self) -> None:  # noqa: D401
        if self._phase == "cables":
            missing: list[str] = []
            for index in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(index)
                if item.checkState(0) != Qt.Checked:
                    continue
                key = str(item.data(0, Qt.UserRole) or "")
                widgets = self._cable_widgets.get(key)
                if widgets is None:
                    continue
                _name_edit, _type_combo, start_combo, end_combo = widgets
                if not start_combo.currentData() or not end_combo.currentData():
                    preview = self._previews.get(key)
                    missing.append(preview.cable_name if preview else key)
            if missing:
                QMessageBox.warning(
                    self,
                    "Start/Ziel-AP erforderlich",
                    "Für folgende Kabel müssen Start- und Ziel-AP gesetzt werden:\n"
                    + "\n".join(f"- {n}" for n in missing),
                )
                return
        super().accept()

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _populate(self) -> None:
        is_ap_phase = self._phase == "aps"
        sorted_previews = sorted(
            self._previews.values(),
            key=lambda p: (p.cable_name.lower(), p.candidate_key.lower()),
        )

        if is_ap_phase:
            for preview in sorted_previews:
                if preview.source != "ap_group":
                    continue
                item = QTreeWidgetItem(self.tree)
                item.setData(0, Qt.UserRole, preview.candidate_key)
                item.setCheckState(
                    0, Qt.Unchecked if preview.status == "unchanged" else Qt.Checked
                )
                ap_confidence = self._ap_phase_confidence_label(preview)
                item.setText(1, f"{self._ap_phase_status_label(preview)} | {ap_confidence}")
                item.setForeground(1, QBrush(self._endpoint_confidence_color(ap_confidence)))
                item.setToolTip(1, self._ap_phase_confidence_tooltip(preview))
                item.setText(2, preview.cable_name)
                item.setText(3, "AP Group")
                self._attach_ap_assignment_widgets(item, preview)
        else:
            for preview in sorted_previews:
                if preview.source not in {"kbl_bus", "kbl_label"}:
                    continue
                item = QTreeWidgetItem(self.tree)
                item.setData(0, Qt.UserRole, preview.candidate_key)
                item.setCheckState(
                    0, Qt.Unchecked if preview.status == "unchanged" else Qt.Checked
                )
                confidence = self._endpoint_confidence_label(preview)
                item.setText(1, f"{preview.status} | {confidence}")
                item.setForeground(1, QBrush(self._endpoint_confidence_color(confidence)))
                item.setToolTip(1, self._endpoint_confidence_tooltip(preview))
                self._attach_cable_widgets(item, preview)

        warnings = [w.message for w in self._scan_result.warnings]
        warnings.extend(self._extra_warnings)
        self.warning_box.setPlainText("\n".join(warnings))
        if self.tree.topLevelItemCount() > 0:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self._update_detail()

    # ------------------------------------------------------------------
    # Widget attachment – AP phase
    # ------------------------------------------------------------------

    def _attach_ap_assignment_widgets(
        self, item: QTreeWidgetItem, preview: KiCadImportPreview
    ) -> None:
        key = str(preview.candidate_key or "")
        assignment = self._initial_ap_assignments.get(key, {})
        floor_plan_id = str(assignment.get("floor_plan_id", "") or "")
        room_id = str(assignment.get("room_id", "") or "")
        room_locked = bool(assignment.get("room_locked", False))

        floorplan_choices = list(self._floorplan_choices)
        known_fp_ids = {fp_id for fp_id, _ in floorplan_choices}
        if floor_plan_id and floor_plan_id not in known_fp_ids:
            floorplan_choices.append((floor_plan_id, floor_plan_id))

        target_combo = QComboBox(self.tree)
        target_combo.addItem("-", {"floor_plan_id": "", "room_id": ""})

        selected_index = 0
        for floor_index, (fp_id, fp_name) in enumerate(floorplan_choices):
            if floor_index > 0:
                target_combo.insertSeparator(target_combo.count())
            target_combo.addItem("(kein Raum)", {"floor_plan_id": fp_id, "room_id": ""})
            if floor_plan_id == fp_id and not room_id:
                selected_index = target_combo.count() - 1
            room_choices = list(self._room_choices_by_floorplan.get(fp_id, []))
            known_room_ids = {rid for rid, _ in room_choices}
            if room_id and fp_id == floor_plan_id and room_id not in known_room_ids:
                room_choices.append((room_id, room_id))
            for current_room_id, room_name in room_choices:
                target_combo.addItem(
                    str(room_name or current_room_id),
                    {"floor_plan_id": fp_id, "room_id": current_room_id},
                )
                if floor_plan_id == fp_id and room_id == current_room_id:
                    selected_index = target_combo.count() - 1

        target_combo.setCurrentIndex(selected_index)
        target_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        target_combo.setMinimumContentsLength(28)
        target_combo.view().setMinimumWidth(480)

        if room_locked:
            target_combo.setEnabled(False)
            target_combo.setToolTip(
                "Raum gesperrt – AP bereits im Projekt vorhanden.\n"
                "Raumzuordnung im Navigator ändern."
            )
        else:
            target_combo.currentIndexChanged.connect(lambda _idx: self._update_detail())

        self.tree.setItemWidget(item, 4, target_combo)
        self._ap_target_combos[key] = target_combo

        def _set_placement(combo: QComboBox, row: QTreeWidgetItem) -> None:
            d = combo.currentData()
            rid = str(d.get("room_id", "") or "") if isinstance(d, dict) else ""
            row.setText(5, "Raumzentrum" if rid else "Textfeldposition")

        _set_placement(target_combo, item)
        if not room_locked:
            target_combo.currentIndexChanged.connect(
                lambda _idx, c=target_combo, r=item: _set_placement(c, r)
            )

    # ------------------------------------------------------------------
    # Widget attachment – Cable phase
    # ------------------------------------------------------------------

    def _attach_cable_widgets(
        self, item: QTreeWidgetItem, preview: KiCadImportPreview
    ) -> None:
        key = str(preview.candidate_key or "")
        initial = self._initial_cable_endpoints.get(key, {})
        start_default = str(initial.get("start_ap_id", "") or preview.start_ap_id or "")
        end_default = str(initial.get("end_ap_id", "") or preview.end_ap_id or "")

        # Col 2: editable name
        name_edit = QLineEdit(self.tree)
        name_edit.setText(preview.cable_name or "")
        name_edit.setPlaceholderText("Kabelname")
        self.tree.setItemWidget(item, 2, name_edit)

        # Col 3: editable type
        type_combo = QComboBox(self.tree)
        type_combo.setEditable(True)
        type_combo.setInsertPolicy(QComboBox.InsertAtTop)
        cable_type = preview.cable_type or ""
        if cable_type and cable_type not in self._cable_type_choices:
            type_combo.addItem(cable_type)
        for t in self._cable_type_choices:
            if t != cable_type:
                type_combo.addItem(t)
        idx = type_combo.findText(cable_type)
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        elif cable_type:
            type_combo.setCurrentText(cable_type)
        type_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        type_combo.setMinimumContentsLength(10)
        self.tree.setItemWidget(item, 3, type_combo)

        # Col 4: Start AP
        start_combo = QComboBox(self.tree)
        start_combo.addItem("Start AP wählen …", "")
        # Col 5: End AP
        end_combo = QComboBox(self.tree)
        end_combo.addItem("Ziel AP wählen …", "")

        start_index = 0
        end_index = 0
        for i, (point_id, label) in enumerate(self._cable_ap_choices, start=1):
            start_combo.addItem(label, point_id)
            end_combo.addItem(label, point_id)
            if point_id == start_default:
                start_index = i
            if point_id == end_default:
                end_index = i

        start_combo.setCurrentIndex(start_index)
        end_combo.setCurrentIndex(end_index)
        for combo in (start_combo, end_combo):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            combo.setMinimumContentsLength(20)
            combo.view().setMinimumWidth(360)
            combo.currentIndexChanged.connect(lambda _idx: self._update_detail())

        self.tree.setItemWidget(item, 4, start_combo)
        self.tree.setItemWidget(item, 5, end_combo)

        self._cable_widgets[key] = (name_edit, type_combo, start_combo, end_combo)
        self._cable_endpoint_combos[key] = (start_combo, end_combo)  # backward-compat

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------

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

        ap_group = self._scan_result.ap_group_candidates.get(key)
        kbl_bus = self._scan_result.kbl_bus_candidates.get(key)
        kbl_label = self._scan_result.candidates.get(key)

        lines: list[str] = [
            f"Quelle: {preview.source}",
            f"Status: {preview.status}",
            f"Sync-Key: {preview.sync_key}",
        ]

        if self._phase == "cables":
            lines.append(f"Auto-Erkennung: {self._endpoint_confidence_label(preview)}")

        if preview.diffs:
            lines += ["", "Änderungen:"]
            for diff in preview.diffs:
                marker = "geändert" if diff.changed else "gleich"
                lines.append(
                    f"  {diff.field}: '{diff.current_value}' → '{diff.imported_value}' ({marker})"
                )

        if self._phase == "aps":
            combo = self._ap_target_combos.get(key)
            if combo is not None:
                lines += ["", f"Raum: {combo.currentText() or '-'}"]
                d = combo.currentData()
                rid = str(d.get("room_id", "") or "") if isinstance(d, dict) else ""
                lines.append("Platzierung: " + ("Raumzentrum" if rid else "Textfeldposition"))
                if not combo.isEnabled():
                    lines.append("(Raum gesperrt – AP bereits vorhanden)")

        if self._phase == "cables":
            widgets = self._cable_widgets.get(key)
            if widgets is not None:
                _n, _t, sc, ec = widgets
                lines += ["", f"Start AP: {sc.currentText()}", f"Ziel AP: {ec.currentText()}"]

        if ap_group:
            lines += [
                "",
                "AP-Gruppen-Metadaten:",
                f"  Name: {ap_group.group_name}",
                f"  Group UUID: {ap_group.group_uuid}",
            ]
            x1, y1, x2, y2 = ap_group.frame_bounds
            lines.append(f"  Rahmen: x=[{x1:.1f}…{x2:.1f}], y=[{y1:.1f}…{y2:.1f}]")
            if ap_group.bus_hits:
                lines.append(f"  Bus-Treffer: {len(ap_group.bus_hits)}")

        if kbl_bus:
            lines += [
                "",
                "KBL-Bus-Metadaten:",
                f"  Gruppe: {kbl_bus.group_name_raw}",
                f"  Group UUID: {kbl_bus.group_uuid}",
                f"  Bus UUID: {kbl_bus.bus_uuid}",
                f"  Seite: {kbl_bus.sheet_file}",
            ]

        if preview.source == "kbl_label" and kbl_label is not None:
            preferred = kbl_label.pin_refs[0] if kbl_label.pin_refs else None
            lines += [
                "",
                "KBL-Label-Metadaten:",
                f"  Pin/Label: {kbl_label.pin_name_raw}",
                f"  Basisname: {kbl_label.base_name}",
                f"  Typ: {kbl_label.normalized_spec or kbl_label.spec_raw}",
            ]
            if preferred is not None:
                lines.append(f"  Seite: {preferred.sheet_file}")
                lines.append(f"  Hierarchie: {' / '.join(preferred.hierarchy_path) or '-'}")
            if kbl_bus and kbl_bus.points:
                s, e = kbl_bus.points[0], kbl_bus.points[-1]
                lines += [
                    f"  Startpunkt: ({s[0]:.1f}, {s[1]:.1f})",
                    f"  Endpunkt: ({e[0]:.1f}, {e[1]:.1f})",
                ]
            lines += [
                f"  Auto Start-AP: {preview.start_ap_group or '-'} ({preview.start_ap_status or 'unmatched'})",
                f"  Auto Ziel-AP: {preview.end_ap_group or '-'} ({preview.end_ap_status or 'unmatched'})",
            ]
            if preview.start_ap_diagnostic:
                lines.append(f"  Diagnose Start: {preview.start_ap_diagnostic}")
            if preview.end_ap_diagnostic:
                lines.append(f"  Diagnose Ziel: {preview.end_ap_diagnostic}")

        self.detail_box.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # Static helpers (kept for backward compat)
    # ------------------------------------------------------------------

    @staticmethod
    def _split_target_label(label: str) -> tuple[str, str]:
        left, sep, right = str(label or "").partition(": ")
        if not sep:
            return str(label or ""), ""
        return left, right

    @staticmethod
    def _endpoint_confidence_label(preview: KiCadImportPreview) -> str:
        start_status = str(preview.start_ap_status or "").strip().lower()
        end_status = str(preview.end_ap_status or "").strip().lower()

        if start_status == "matched" and end_status == "matched":
            return "hoch"
        if "ambiguous" in (start_status, end_status):
            return "niedrig"
        if "matched" in (start_status, end_status):
            return "mittel"
        return "niedrig"

    @staticmethod
    def _endpoint_confidence_color(confidence: str) -> QColor:
        normalized = str(confidence or "").strip().lower()
        if normalized == "hoch":
            return QColor("#2e7d32")
        if normalized == "mittel":
            return QColor("#b26a00")
        return QColor("#c62828")

    @staticmethod
    def _endpoint_confidence_tooltip(preview: KiCadImportPreview) -> str:
        start_status = str(preview.start_ap_status or "unmatched")
        end_status = str(preview.end_ap_status or "unmatched")
        start_diag = str(preview.start_ap_diagnostic or "-")
        end_diag = str(preview.end_ap_diagnostic or "-")
        return (
            f"Start: {start_status}\n"
            f"Ziel: {end_status}\n"
            f"Diagnose Start: {start_diag}\n"
            f"Diagnose Ziel: {end_diag}"
        )

    @staticmethod
    def _ap_summary_text(preview: KiCadImportPreview) -> str:
        if not preview.ap_matches:
            return "keiner"
        top = preview.ap_matches[0]
        if preview.ap_match_status == "matched":
            return f"{top.point_id} ({top.score})"
        return f"mehrdeutig ({len(preview.ap_matches)})"

    @staticmethod
    def _ap_phase_status_label(preview: KiCadImportPreview) -> str:
        status = str(preview.ap_match_status or "").strip().lower()
        if status == "matched":
            return "reused"
        if status == "ambiguous":
            return "prüfen"
        if status == "unmatched":
            return "create"
        return str(preview.status or "create")

    @staticmethod
    def _ap_phase_confidence_label(preview: KiCadImportPreview) -> str:
        status = str(preview.ap_match_status or "").strip().lower()
        if status == "matched":
            return "hoch"
        if status == "ambiguous":
            return "mittel"
        return "niedrig"

    @staticmethod
    def _ap_phase_confidence_tooltip(preview: KiCadImportPreview) -> str:
        status = str(preview.ap_match_status or "unmatched")
        action = str(preview.ap_import_action or "-")
        if preview.ap_matches:
            top = preview.ap_matches[0]
            top_text = (
                f"Top-Kandidat: {top.point_name} "
                f"({top.reason}, Score {top.score}, Kandidaten {len(preview.ap_matches)})"
            )
        else:
            top_text = "Top-Kandidat: -"
        return (
            f"AP-Match: {status}\n"
            f"Aktion: {action}\n"
            f"{top_text}"
        )

    def _set_all_checks(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            item.setCheckState(0, state)

    @staticmethod
    def _format_hierarchy_path(path: tuple[str, ...]) -> str:
        if not path:
            return "(root)"
        return " > ".join(str(part or "?") for part in path)