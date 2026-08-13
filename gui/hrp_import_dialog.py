from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from logic.hrp_import import HrpImportCandidate, HrpImportSelection, resolve_import_selection
from model.document import Document
from model.elements import FloorPlan, Furniture


class HrpImportDialog(QDialog):
    def __init__(
        self,
        source_document: Document,
        candidates: list[HrpImportCandidate],
        *,
        source_label: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source_document = source_document
        self._candidates = list(candidates)
        self._candidate_by_key = {candidate.key: candidate for candidate in self._candidates}
        self._direct_keys: set[str] = set()
        self._current_selection = HrpImportSelection((), (), ())
        self._item_by_key: dict[str, QTreeWidgetItem] = {}
        self._group_items: list[QTreeWidgetItem] = []
        self._group_item_by_label: dict[str, QTreeWidgetItem] = {}
        self._syncing = False
        self._source_summary = f"Quelle: {source_label or 'HRP-Quelle'}"

        self.setWindowTitle("Teile aus HRP importieren")
        self.resize(960, 680)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel(
            f"{self._source_summary}\n"
            "Haken Sie die gewünschten Elemente an. Benötigte Abhängigkeiten werden automatisch mit importiert."
        )
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        action_row = QHBoxLayout()
        self.select_all_button = QPushButton("Alle wählen", self)
        self.select_all_button.clicked.connect(self._select_all)
        action_row.addWidget(self.select_all_button)
        self.clear_all_button = QPushButton("Alles abwählen", self)
        self.clear_all_button.clicked.connect(self._clear_all)
        action_row.addWidget(self.clear_all_button)
        self.select_category_button = QPushButton("Kategorie wählen", self)
        self.select_category_button.clicked.connect(self._select_current_category)
        action_row.addWidget(self.select_category_button)
        self.clear_category_button = QPushButton("Kategorie leeren", self)
        self.clear_category_button.clicked.connect(self._clear_current_category)
        action_row.addWidget(self.clear_category_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Import", "Status", "Name", "ID", "Grundriss"])
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._update_detail)
        layout.addWidget(self.tree, stretch=1)

        self.detail_box = QTextEdit(self)
        self.detail_box.setReadOnly(True)
        self.detail_box.setPlaceholderText("Details zum ausgewählten Element")
        self.detail_box.setMaximumHeight(180)
        layout.addWidget(self.detail_box)

        self.warning_box = QTextEdit(self)
        self.warning_box.setReadOnly(True)
        self.warning_box.setMaximumHeight(110)
        layout.addWidget(self.warning_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        buttons.button(QDialogButtonBox.Ok).setText("Importieren")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate()
        self._refresh_selection_state()

    def selected_keys(self) -> list[str]:
        return list(self._current_selection.selected_keys)

    def resolved_selection(self) -> HrpImportSelection:
        return self._current_selection

    def _populate(self) -> None:
        grouped: dict[str, list[HrpImportCandidate]] = defaultdict(list)
        for candidate in self._candidates:
            grouped[candidate.category_label].append(candidate)

        for category_label in sorted(grouped):
            group_item = QTreeWidgetItem(self.tree)
            group_item.setText(1, "Kategorie")
            group_item.setText(2, category_label)
            group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            group_item.setCheckState(0, Qt.Unchecked)
            self._group_items.append(group_item)
            self._group_item_by_label[category_label] = group_item

            for candidate in sorted(grouped[category_label], key=lambda item: (item.name.lower(), item.element_id)):
                item = QTreeWidgetItem(group_item)
                item.setData(0, Qt.UserRole, candidate.key)
                item.setText(2, candidate.name)
                item.setText(3, candidate.element_id)
                item.setText(4, self._floorplan_label(candidate.floor_plan_id))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setCheckState(0, Qt.Unchecked)
                self._item_by_key[candidate.key] = item

        self.tree.expandAll()
        if self.tree.topLevelItemCount() > 0:
            first_group = self.tree.topLevelItem(0)
            if first_group.childCount() > 0:
                self.tree.setCurrentItem(first_group.child(0))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._syncing or column != 0:
            return

        key = item.data(0, Qt.UserRole)
        if key:
            if item.checkState(0) == Qt.Checked:
                self._direct_keys.add(str(key))
            else:
                self._direct_keys.discard(str(key))
            self._refresh_selection_state()
            return

        desired_checked = item.checkState(0) == Qt.Checked
        for index in range(item.childCount()):
            child = item.child(index)
            child_key = child.data(0, Qt.UserRole)
            if not child_key:
                continue
            if desired_checked:
                self._direct_keys.add(str(child_key))
            else:
                self._direct_keys.discard(str(child_key))
        self._refresh_selection_state()

    def _refresh_selection_state(self) -> None:
        self._current_selection = resolve_import_selection(
            self._source_document,
            sorted(self._direct_keys),
        )
        direct_set = set(self._current_selection.selected_keys)
        auto_set = set(self._current_selection.auto_included_keys)

        self._syncing = True
        for key, item in self._item_by_key.items():
            flags = item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled
            if key in direct_set:
                item.setCheckState(0, Qt.Checked)
                item.setText(1, "ausgewählt")
                item.setFlags(flags | Qt.ItemIsUserCheckable)
            elif key in auto_set:
                item.setCheckState(0, Qt.Checked)
                item.setText(1, "automatisch")
                item.setFlags(flags & ~Qt.ItemIsUserCheckable)
            else:
                item.setCheckState(0, Qt.Unchecked)
                item.setText(1, "")
                item.setFlags(flags | Qt.ItemIsUserCheckable)

        for group_item in self._group_items:
            checked = 0
            partial = 0
            for index in range(group_item.childCount()):
                child_state = group_item.child(index).checkState(0)
                if child_state == Qt.Checked:
                    checked += 1
                elif child_state == Qt.PartiallyChecked:
                    partial += 1
            if checked == 0 and partial == 0:
                group_item.setCheckState(0, Qt.Unchecked)
            elif checked == group_item.childCount() and partial == 0:
                group_item.setCheckState(0, Qt.Checked)
            else:
                group_item.setCheckState(0, Qt.PartiallyChecked)
        self._syncing = False

        direct_count = len(self._current_selection.selected_keys)
        auto_count = len(self._current_selection.auto_included_keys)
        total_count = len(self._current_selection.ordered_keys)
        self.summary_label.setText(
            f"{self._source_summary}\n"
            "Haken Sie die gewünschten Elemente an. Benötigte Abhängigkeiten werden automatisch mit importiert.\n"
            f"Direkt ausgewählt: {direct_count} | automatisch ergänzt: {auto_count} | Gesamtimport: {total_count}"
        )

        auto_lines = self._grouped_candidate_lines(self._current_selection.auto_included_keys)
        self.warning_box.setPlainText(
            "Automatisch mit importiert:\n" + "\n".join(auto_lines)
            if auto_lines
            else "Keine automatischen Abhängigkeiten ausgewählt."
        )
        self._update_detail()

    def _update_detail(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            self.detail_box.clear()
            return
        key = item.data(0, Qt.UserRole)
        if not key:
            self.detail_box.setPlainText(item.text(2) or "Kategorie")
            return

        candidate = self._candidate_by_key.get(str(key))
        if candidate is None:
            self.detail_box.clear()
            return

        source_element = self._source_document.get(candidate.element_id)
        lines = [
            f"Kategorie: {candidate.category_label}",
            f"Name: {candidate.name}",
            f"ID: {candidate.element_id}",
            f"Grundriss: {self._floorplan_label(candidate.floor_plan_id)}",
            f"Status: {item.text(1) or 'nicht ausgewählt'}",
        ]
        dependencies = self._dependency_lines_for_candidate(candidate)
        if dependencies:
            lines.append("")
            lines.append("Automatisch benötigte Abhängigkeiten:")
            lines.extend(dependencies)
        if source_element is not None:
            if isinstance(source_element, FloorPlan):
                lines.append(f"Bildpfad: {source_element.file_path or '-'}")
            elif isinstance(source_element, Furniture):
                lines.append(f"Eltern-Grundriss: {source_element.data.get('parent_fp_id', '-') or '-'}")
            else:
                lines.append(f"Sichtbar: {'ja' if bool(source_element.visible) else 'nein'}")
        self.detail_box.setPlainText("\n".join(lines))

    def _floorplan_label(self, floor_plan_id: str) -> str:
        floor_id = str(floor_plan_id or "")
        if not floor_id:
            return "-"
        floor = self._source_document.floorplans.get(floor_id)
        if floor is not None:
            return floor.name or floor.id
        furniture = self._source_document.furniture.get(floor_id)
        if furniture is not None:
            return furniture.name or furniture.id
        return floor_id

    @staticmethod
    def _format_candidate_line(candidate: HrpImportCandidate) -> str:
        return f"- {candidate.category_label}: {candidate.name} ({candidate.element_id})"

    def _grouped_candidate_lines(self, keys: tuple[str, ...]) -> list[str]:
        grouped: dict[str, list[HrpImportCandidate]] = defaultdict(list)
        for key in keys:
            candidate = self._candidate_by_key.get(key)
            if candidate is not None:
                grouped[candidate.category_label].append(candidate)

        lines: list[str] = []
        for category_label in sorted(grouped):
            candidates = sorted(grouped[category_label], key=lambda item: (item.name.lower(), item.element_id))
            preview_names = ", ".join(candidate.name for candidate in candidates[:4])
            if len(candidates) > 4:
                preview_names += f", +{len(candidates) - 4} weitere"
            lines.append(f"- {category_label} ({len(candidates)}): {preview_names}")
        return lines

    def _dependency_lines_for_candidate(self, candidate: HrpImportCandidate) -> list[str]:
        selection = resolve_import_selection(self._source_document, [candidate.key])
        dependency_keys = tuple(key for key in selection.auto_included_keys if key in self._candidate_by_key)
        return self._grouped_candidate_lines(dependency_keys)

    def _current_group_item(self) -> QTreeWidgetItem | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        key = item.data(0, Qt.UserRole)
        if key:
            return item.parent()
        return item

    def _set_group_selected(self, group_item: QTreeWidgetItem | None, selected: bool) -> None:
        if group_item is None:
            return
        for index in range(group_item.childCount()):
            child = group_item.child(index)
            child_key = child.data(0, Qt.UserRole)
            if not child_key:
                continue
            if selected:
                self._direct_keys.add(str(child_key))
            else:
                self._direct_keys.discard(str(child_key))
        self._refresh_selection_state()

    def _select_all(self) -> None:
        self._direct_keys = set(self._item_by_key)
        self._refresh_selection_state()

    def _clear_all(self) -> None:
        self._direct_keys.clear()
        self._refresh_selection_state()

    def _select_current_category(self) -> None:
        self._set_group_selected(self._current_group_item(), True)

    def _clear_current_category(self) -> None:
        self._set_group_selected(self._current_group_item(), False)