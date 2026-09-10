"""Projektübersicht-Dock mit drei Tabs (Allgemein / Heizung / Elektro)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, Qt, QTimer, QSettings, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from model.document import Document


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsklasse: einklappbare Sektion
# ─────────────────────────────────────────────────────────────────────────────

class _CollapsibleSection(QWidget):
    """Sektion mit Toggle-Button (▶ / ▼) und ein-/ausklappbarem Inhalt."""

    toggled = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None, expanded: bool = True) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._btn = QPushButton(title)
        self._btn.setCheckable(True)
        self._btn.setChecked(expanded)
        self._btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 4px 6px; border: none; "
            "font-weight: bold; background: #3a3a3a; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        self._btn.clicked.connect(self._on_toggle)
        layout.addWidget(self._btn)

        self._content = QWidget()
        self._content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self._content)
        self._content.setVisible(expanded)
        self._update_btn_text(expanded)

    @property
    def content_widget(self) -> QWidget:
        return self._content

    def set_content_layout(self, layout) -> None:
        self._content.setLayout(layout)

    def _on_toggle(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._update_btn_text(checked)
        self.toggled.emit(checked)

    def set_expanded(self, expanded: bool) -> None:
        self._btn.setChecked(expanded)
        self._content.setVisible(expanded)
        self._update_btn_text(expanded)

    def is_expanded(self) -> bool:
        return self._btn.isChecked()

    def _update_btn_text(self, expanded: bool) -> None:
        arrow = "▼" if expanded else "▶"
        base = self._btn.text()
        # Strip existing arrow
        for prefix in ("▼ ", "▶ "):
            if base.startswith(prefix):
                base = base[len(prefix):]
        self._btn.setText(f"{arrow} {base}")


# ─────────────────────────────────────────────────────────────────────────────
# Sortierbare Tabelle
# ─────────────────────────────────────────────────────────────────────────────

class _ReadOnlyTableItem(QTableWidgetItem):
    """Nicht editierbares Tabellen-Item."""

    def __init__(self, text: str = "", numeric: float | None = None) -> None:
        super().__init__(text)
        self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if numeric is not None:
            self.setData(Qt.UserRole, numeric)


def _num_item(value: float, fmt: str = ".1f", unit: str = "") -> _ReadOnlyTableItem:
    text = f"{value:{fmt}}"
    if unit:
        text += f" {unit}"
    return _ReadOnlyTableItem(text, numeric=value)


def _str_item(text: str) -> _ReadOnlyTableItem:
    return _ReadOnlyTableItem(str(text or "–"))


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Dock
# ─────────────────────────────────────────────────────────────────────────────

class ProjectOverviewDock(QDockWidget):
    """Zeigt berechnete Projektübersicht mit optionaler Tab-Einschränkung."""

    pre_change = Signal()
    element_field_changed = Signal(str, str, object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Projektübersicht",
        object_name: str = "dock_overview",
        visible_tabs: tuple[str, ...] | None = None,
        visible_electro_sections: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.setObjectName(object_name)
        self.setMinimumWidth(360)

        self._document: Document | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._do_refresh)

        # ── Tab-Widget ──────────────────────────────────────────────
        self._tabs = QTabWidget()
        self.setWidget(self._tabs)
        self._tab_names: list[str] = []
        self._last_electro_data: dict = {}
        self._updating_electro_tables = False
        self._elec_room_row_ap_ids: dict[int, str] = {}
        self._elec_cable_row_ids: dict[int, str] = {}
        if visible_electro_sections is None:
            self._visible_electro_sections = {"materials", "rooms", "cables"}
        else:
            self._visible_electro_sections = {
                str(section).strip().lower() for section in visible_electro_sections if str(section).strip()
            }

        # Tab Allgemein
        self._general_scroll = QScrollArea()
        self._general_scroll.setWidgetResizable(True)
        self._general_inner = QWidget()
        self._general_layout = QVBoxLayout(self._general_inner)
        self._general_layout.setContentsMargins(4, 4, 4, 4)
        self._general_layout.setSpacing(4)
        self._general_layout.addStretch()
        self._general_scroll.setWidget(self._general_inner)
        self._tabs.addTab(self._general_scroll, "Allgemein")
        self._tab_names.append("Allgemein")

        # Tab Heizung
        self._heating_scroll = QScrollArea()
        self._heating_scroll.setWidgetResizable(True)
        self._heating_inner = QWidget()
        self._heating_layout = QVBoxLayout(self._heating_inner)
        self._heating_layout.setContentsMargins(4, 4, 4, 4)
        self._heating_layout.setSpacing(6)
        self._heating_splitter = QSplitter(Qt.Vertical)
        self._heating_splitter.setChildrenCollapsible(False)
        self._heating_layout.addWidget(self._heating_splitter)
        self._heating_scroll.setWidget(self._heating_inner)
        self._tabs.addTab(self._heating_scroll, "Heizung")
        self._tab_names.append("Heizung")

        # Heizkreis-Tabelle
        self._hk_section = _CollapsibleSection("Heizkreise", expanded=True)
        self._hk_table = self._build_hk_table()
        hk_inner_layout = QVBoxLayout()
        hk_inner_layout.setContentsMargins(0, 0, 0, 0)
        hk_inner_layout.addWidget(self._hk_table, 1)
        self._hk_section.set_content_layout(hk_inner_layout)
        self._heating_splitter.addWidget(self._hk_section)

        # HKV-Tabelle
        self._hkv_section = _CollapsibleSection("Heizkreisverteiler", expanded=True)
        self._hkv_table = self._build_hkv_table()
        hkv_inner_layout = QVBoxLayout()
        hkv_inner_layout.setContentsMargins(0, 0, 0, 0)
        hkv_inner_layout.addWidget(self._hkv_table, 1)
        self._hkv_section.set_content_layout(hkv_inner_layout)
        self._heating_splitter.addWidget(self._hkv_section)

        # Materialliste
        self._mat_section = _CollapsibleSection("Materialliste", expanded=True)
        self._mat_form = QFormLayout()
        self._mat_form.setContentsMargins(8, 4, 4, 4)
        self._mat_section.set_content_layout(self._mat_form)
        self._heating_splitter.addWidget(self._mat_section)

        # Tab Elektro
        self._elec_scroll = QScrollArea()
        self._elec_scroll.setWidgetResizable(True)
        self._elec_inner = QWidget()
        self._elec_layout = QVBoxLayout(self._elec_inner)
        self._elec_layout.setContentsMargins(4, 4, 4, 4)
        self._elec_layout.setSpacing(6)
        self._elec_splitter = QSplitter(Qt.Vertical)
        self._elec_splitter.setChildrenCollapsible(False)
        self._elec_layout.addWidget(self._elec_splitter)
        self._elec_scroll.setWidget(self._elec_inner)
        self._tabs.addTab(self._elec_scroll, "Elektro")
        self._tab_names.append("Elektro")

        # Elektro: Materialliste
        self._elec_mat_section = _CollapsibleSection("Materialliste", expanded=True)
        elec_mat_layout = QVBoxLayout()
        elec_mat_layout.setContentsMargins(4, 4, 4, 4)
        elec_mat_layout.setSpacing(4)
        elec_mat_layout.addWidget(QLabel("Gesamtlänge Kabel nach Typ"))
        self._elec_cable_mat_table = self._build_elec_cable_material_table()
        elec_mat_layout.addWidget(self._elec_cable_mat_table, 1)
        self._elec_cable_total_label = QLabel("Gesamt: 0.00 m")
        elec_mat_layout.addWidget(self._elec_cable_total_label)
        elec_mat_layout.addWidget(QLabel("Anzahl APs nach Typ"))
        self._elec_ap_mat_table = self._build_elec_ap_material_table()
        elec_mat_layout.addWidget(self._elec_ap_mat_table, 1)
        self._elec_mat_section.set_content_layout(elec_mat_layout)
        if "materials" in self._visible_electro_sections:
            self._elec_splitter.addWidget(self._elec_mat_section)

        # Elektro: Raumliste
        self._elec_room_section = _CollapsibleSection("Raumliste", expanded=True)
        elec_room_layout = QVBoxLayout()
        elec_room_layout.setContentsMargins(0, 0, 0, 0)
        self._elec_room_grouped = True
        self._elec_room_grouped_checkbox = QCheckBox("Nach Raum gruppieren")
        self._elec_room_grouped_checkbox.setChecked(True)
        self._elec_room_grouped_checkbox.toggled.connect(self._on_elec_room_grouped_toggled)
        elec_room_layout.addWidget(self._elec_room_grouped_checkbox)
        self._elec_room_table = self._build_elec_room_table()
        elec_room_layout.addWidget(self._elec_room_table, 1)
        self._elec_room_section.set_content_layout(elec_room_layout)
        if "rooms" in self._visible_electro_sections:
            self._elec_splitter.addWidget(self._elec_room_section)
        self._elec_room_table.itemChanged.connect(self._on_elec_room_item_changed)

        # Elektro: Kabelliste
        self._elec_cable_section = _CollapsibleSection("Kabelliste", expanded=True)
        elec_cable_layout = QVBoxLayout()
        elec_cable_layout.setContentsMargins(0, 0, 0, 0)
        self._elec_cable_table = self._build_elec_cable_table()
        elec_cable_layout.addWidget(self._elec_cable_table, 1)
        self._elec_cable_section.set_content_layout(elec_cable_layout)
        if "cables" in self._visible_electro_sections:
            self._elec_splitter.addWidget(self._elec_cable_section)
        self._elec_cable_table.itemChanged.connect(self._on_elec_cable_item_changed)

        self._heating_sections = (self._hk_section, self._hkv_section, self._mat_section)
        self._electro_sections = tuple(
            section
            for section, key in (
                (self._elec_mat_section, "materials"),
                (self._elec_room_section, "rooms"),
                (self._elec_cable_section, "cables"),
            )
            if key in self._visible_electro_sections
        )

        for splitter in (self._heating_splitter, self._elec_splitter):
            for idx in range(splitter.count()):
                splitter.setStretchFactor(idx, 1)
            splitter.splitterMoved.connect(self._save_ui_state)

        self._hk_section.toggled.connect(self._on_section_visibility_changed)
        self._hkv_section.toggled.connect(self._on_section_visibility_changed)
        self._mat_section.toggled.connect(self._on_section_visibility_changed)
        self._elec_mat_section.toggled.connect(self._on_section_visibility_changed)
        self._elec_room_section.toggled.connect(self._on_section_visibility_changed)
        self._elec_cable_section.toggled.connect(self._on_section_visibility_changed)

        if visible_tabs is not None:
            self._restrict_tabs(visible_tabs)

        self._restore_ui_state()
        self._rebalance_splitter(self._heating_splitter, self._heating_sections)
        self._rebalance_splitter(self._elec_splitter, self._electro_sections)

    def _restrict_tabs(self, visible_tabs: tuple[str, ...]) -> None:
        visible = {name.strip() for name in visible_tabs}
        for idx in reversed(range(self._tabs.count())):
            label = self._tabs.tabText(idx)
            if label not in visible:
                self._tabs.removeTab(idx)
        if self._tabs.count() <= 1:
            self._tabs.tabBar().hide()

    def _settings_prefix(self) -> str:
        return f"overview/{self.objectName()}"

    def _restore_bool(self, key: str, default: bool) -> bool:
        raw = QSettings().value(key, default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)

    def _restore_ui_state(self) -> None:
        s = QSettings()
        base = self._settings_prefix()
        for section, key in (
            (self._hk_section, "hk_expanded"),
            (self._hkv_section, "hkv_expanded"),
            (self._mat_section, "mat_expanded"),
            (self._elec_mat_section, "elec_mat_expanded"),
            (self._elec_room_section, "elec_room_expanded"),
            (self._elec_cable_section, "elec_cable_expanded"),
        ):
            section.set_expanded(self._restore_bool(f"{base}/{key}", section.is_expanded()))

        grouped = self._restore_bool(f"{base}/elec_room_grouped", True)
        self._elec_room_grouped_checkbox.blockSignals(True)
        self._elec_room_grouped_checkbox.setChecked(grouped)
        self._elec_room_grouped_checkbox.blockSignals(False)
        self._elec_room_grouped = grouped

        for splitter, key in (
            (self._heating_splitter, "heating_splitter"),
            (self._elec_splitter, "elec_splitter"),
        ):
            raw = s.value(f"{base}/{key}")
            if isinstance(raw, QByteArray):
                splitter.restoreState(raw)

    def _save_ui_state(self, *_args) -> None:
        s = QSettings()
        base = self._settings_prefix()
        s.setValue(f"{base}/hk_expanded", self._hk_section.is_expanded())
        s.setValue(f"{base}/hkv_expanded", self._hkv_section.is_expanded())
        s.setValue(f"{base}/mat_expanded", self._mat_section.is_expanded())
        s.setValue(f"{base}/elec_mat_expanded", self._elec_mat_section.is_expanded())
        s.setValue(f"{base}/elec_room_expanded", self._elec_room_section.is_expanded())
        s.setValue(f"{base}/elec_cable_expanded", self._elec_cable_section.is_expanded())
        s.setValue(f"{base}/elec_room_grouped", bool(self._elec_room_grouped))
        s.setValue(f"{base}/heating_splitter", self._heating_splitter.saveState())
        s.setValue(f"{base}/elec_splitter", self._elec_splitter.saveState())

    def _rebalance_splitter(self, splitter: QSplitter, sections: tuple[_CollapsibleSection, ...]) -> None:
        if not sections:
            return
        sizes = []
        for section in sections:
            sizes.append(120 if section.is_expanded() else max(24, section.sizeHint().height()))
        splitter.setSizes(sizes)

    def _on_section_visibility_changed(self, *_args) -> None:
        self._rebalance_splitter(self._heating_splitter, self._heating_sections)
        self._rebalance_splitter(self._elec_splitter, self._electro_sections)
        self._save_ui_state()

    # ── Tabellen-Builder ─────────────────────────────────────────────

    def _build_hk_table(self) -> QTableWidget:
        columns = [
            "Name", "HKV", "Gesamt [m]", "Im Raum [m]", "Zuleitung [m]",
            "T-Soll [°C]", "Spreizung [K]", "Durchfluss\n[l/min]",
            "Leistung [W]", "Druckverl.\n[mbar]", "Kv [m³/h]",
        ]
        tbl = QTableWidget(0, len(columns))
        tbl.setHorizontalHeaderLabels(columns)
        tbl.setSortingEnabled(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        tbl.verticalHeader().hide()
        return tbl

    def _build_hkv_table(self) -> QTableWidget:
        columns = [
            "Verteiler", "Gesamtdurchfluss\n[l/min]",
            "Gesamtleistung [W]", "Anzahl Kreise",
            "Durchfluss/Kreis\n[l/min]",
        ]
        tbl = QTableWidget(0, len(columns))
        tbl.setHorizontalHeaderLabels(columns)
        tbl.setSortingEnabled(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        tbl.verticalHeader().hide()
        return tbl

    def _build_elec_cable_material_table(self) -> QTableWidget:
        tbl = QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(["Typ", "Gesamtlänge [m]"])
        tbl.setSortingEnabled(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        tbl.verticalHeader().hide()
        return tbl

    def _build_elec_ap_material_table(self) -> QTableWidget:
        tbl = QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(["AP-Typ", "Anzahl"])
        tbl.setSortingEnabled(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        tbl.verticalHeader().hide()
        return tbl

    def _build_elec_room_table(self) -> QTableWidget:
        tbl = QTableWidget(0, 5)
        tbl.setHorizontalHeaderLabels(["Raum", "AP", "AP-Typ", "Höhe über FB [cm]", "Kabel"])
        # Grouped rows should keep a deterministic room -> AP order.
        tbl.setSortingEnabled(False)
        tbl.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
        )
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        tbl.verticalHeader().hide()
        return tbl

    def _build_elec_cable_table(self) -> QTableWidget:
        tbl = QTableWidget(0, 5)
        tbl.setHorizontalHeaderLabels(["Name", "Typ", "Länge [m]", "Start AP", "End AP"])
        tbl.setSortingEnabled(True)
        tbl.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
        )
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        tbl.verticalHeader().hide()
        return tbl

    @staticmethod
    def _set_item_editable(item: QTableWidgetItem, editable: bool) -> None:
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if editable:
            flags |= Qt.ItemIsEditable
        item.setFlags(flags)

    @staticmethod
    def _parse_float_text(text: str) -> float | None:
        raw = str(text or "").strip().replace("cm", "").strip().replace(",", ".")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @staticmethod
    def _elec_ap_symbol_options() -> list[str]:
        try:
            from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415

            values = [str(v) for v in BUILTIN_SYMBOLS.keys() if str(v).strip()]
            return sorted(set(values), key=str.lower)
        except Exception:
            return []

    # ── Document-Anbindung ───────────────────────────────────────────

    def set_document(self, document: Document | None) -> None:
        if self._document is not None:
            try:
                self._document.structure_changed.disconnect(self._schedule_refresh)
                self._document.element_changed.disconnect(self._schedule_refresh)
            except RuntimeError:
                pass
        self._document = document
        if document is not None:
            document.structure_changed.connect(self._schedule_refresh)
            document.element_changed.connect(self._schedule_refresh)
        self._do_refresh()

    def _schedule_refresh(self, *_args) -> None:
        self._refresh_timer.start()

    # ── Refresh ──────────────────────────────────────────────────────

    def _do_refresh(self) -> None:
        if self._document is None:
            self._clear_all()
            return
        try:
            from model.computed import project_overview_data  # noqa: PLC0415
            data = project_overview_data(self._document)
        except Exception:  # pragma: no cover
            return

        self._fill_general(data.get("general", {}))
        heating_rows = data.get("heating_rows", [])
        t_supply = data.get("t_supply", 35.0)
        t_return = data.get("t_return", 30.0)
        self._fill_hk_table(heating_rows, t_supply, t_return)
        self._fill_hkv_table(data.get("hkv_rows", []))
        self._fill_materials(data.get("materials", {}))
        self._fill_electro(data.get("electro", {}))

    def _clear_all(self) -> None:
        self._hk_table.setRowCount(0)
        self._hkv_table.setRowCount(0)
        self._elec_cable_mat_table.setRowCount(0)
        self._elec_ap_mat_table.setRowCount(0)
        self._elec_room_table.setRowCount(0)
        self._elec_cable_table.setRowCount(0)
        self._elec_cable_total_label.setText("Gesamt: 0.00 m")
        for i in reversed(range(self._mat_form.rowCount())):
            self._mat_form.removeRow(i)
        # Clear general sections
        while self._general_layout.count() > 1:
            item = self._general_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Tab Allgemein ─────────────────────────────────────────────────

    def _fill_general(self, general: dict[str, int]) -> None:
        # Remove existing sections (keep stretch at end)
        while self._general_layout.count() > 1:
            item = self._general_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not general:
            lbl = QLabel("Kein Projekt geladen.")
            self._general_layout.insertWidget(0, lbl)
            return

        section = _CollapsibleSection("Elemente", expanded=True)
        form = QFormLayout()
        form.setContentsMargins(8, 4, 4, 4)
        for label, count in sorted(general.items(), key=lambda x: x[0]):
            form.addRow(f"{label}:", QLabel(str(count)))
        section.set_content_layout(form)
        self._general_layout.insertWidget(0, section)

    # ── Tab Heizung: Heizkreis-Tabelle ────────────────────────────────

    def _fill_hk_table(
        self,
        rows: list[dict],
        t_supply: float,
        t_return: float,
    ) -> None:
        spreizung = t_supply - t_return
        tbl = self._hk_table
        tbl.setSortingEnabled(False)
        tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            tbl.setItem(r, 0, _str_item(row.get("name", "")))
            tbl.setItem(r, 1, _str_item(row.get("distributor", "")))
            tbl.setItem(r, 2, _num_item(row.get("total_m", 0.0), ".2f", "m"))
            tbl.setItem(r, 3, _num_item(row.get("route_m", 0.0), ".2f", "m"))
            tbl.setItem(r, 4, _num_item(row.get("supply_m", 0.0), ".2f", "m"))
            tbl.setItem(r, 5, _num_item(row.get("room_temp", 20.0), ".1f", "°C"))
            tbl.setItem(r, 6, _num_item(spreizung, ".1f", "K"))
            tbl.setItem(r, 7, _num_item(row.get("volume_flow_lmin", 0.0), ".2f", "l/min"))
            tbl.setItem(r, 8, _num_item(row.get("power_w", 0.0), ".0f", "W"))
            tbl.setItem(r, 9, _num_item(row.get("pressure_drop_mbar", 0.0), ".1f", "mbar"))
            tbl.setItem(r, 10, _num_item(row.get("kv_value", 0.0), ".4f", "m³/h"))
        tbl.setSortingEnabled(True)
        tbl.resizeColumnsToContents()

    # ── Tab Heizung: HKV-Tabelle ──────────────────────────────────────

    def _fill_hkv_table(self, hkv_rows: list[dict]) -> None:
        tbl = self._hkv_table
        tbl.setSortingEnabled(False)
        tbl.setRowCount(len(hkv_rows))
        for r, row in enumerate(hkv_rows):
            tbl.setItem(r, 0, _str_item(row.get("name", "")))
            total_flow = row.get("total_flow_lmin", 0.0)
            count = max(1, row.get("circuit_count", 1))
            tbl.setItem(r, 1, _num_item(total_flow, ".2f", "l/min"))
            tbl.setItem(r, 2, _num_item(row.get("total_power_w", 0.0), ".0f", "W"))
            tbl.setItem(r, 3, _num_item(count, ".0f"))
            tbl.setItem(r, 4, _num_item(total_flow / count, ".2f", "l/min"))
        tbl.setSortingEnabled(True)
        tbl.resizeColumnsToContents()

    # ── Tab Heizung: Materialliste ────────────────────────────────────

    def _fill_materials(self, materials: dict) -> None:
        form = self._mat_form
        while form.rowCount():
            form.removeRow(0)

        pipe_by_diam = materials.get("pipe_by_diameter_m", {})
        if pipe_by_diam:
            for diam, length in sorted(pipe_by_diam.items()):
                form.addRow(f"Rohr {diam}:", QLabel(f"{length:.1f} m"))
        else:
            form.addRow("Rohr:", QLabel("–"))

        form.addRow("Heizkreise:", QLabel(str(materials.get("circuit_count", 0))))
        form.addRow("HKV-Kästen:", QLabel(str(materials.get("hkv_count", 0))))
        form.addRow("Stellventile:", QLabel(str(materials.get("valve_count", 0))))
        form.addRow("Fittings:", QLabel(str(materials.get("fitting_count", 0))))

    # ── Tab Elektro ────────────────────────────────────────────────────

    def _fill_electro(self, electro: dict) -> None:
        self._last_electro_data = electro if isinstance(electro, dict) else {}
        materials = electro.get("materials", {}) if isinstance(electro, dict) else {}
        self._fill_elec_material_tables(materials)
        self._fill_elec_room_table(electro.get("rooms", []))
        self._fill_elec_cable_table(electro.get("cables", []))

    def _on_elec_room_grouped_toggled(self, checked: bool) -> None:
        self._elec_room_grouped = bool(checked)
        electro = getattr(self, "_last_electro_data", {})
        self._fill_elec_room_table(electro.get("rooms", []))
        self._save_ui_state()

    def _format_cable_refs(self, cables: list[str], max_items: int = 3) -> str:
        unique_sorted = sorted({str(c) for c in cables if str(c).strip()})
        if not unique_sorted:
            return "–"
        shown = unique_sorted[:max_items]
        text = ", ".join(shown)
        rest = len(unique_sorted) - len(shown)
        if rest > 0:
            text += f" (+{rest} weitere)"
        return text

    def _fill_elec_material_tables(self, materials: dict) -> None:
        cable_map = materials.get("cable_length_by_type_m", {}) if isinstance(materials, dict) else {}
        ap_map = materials.get("ap_count_by_type", {}) if isinstance(materials, dict) else {}

        cable_tbl = self._elec_cable_mat_table
        cable_tbl.setSortingEnabled(False)
        total_length_m = sum(float(v or 0.0) for v in cable_map.values())
        cable_tbl.setRowCount(len(cable_map) + (1 if cable_map else 0))
        row_index = 0
        for cable_type, length_m in sorted(cable_map.items()):
            r = row_index
            cable_tbl.setItem(r, 0, _str_item(cable_type))
            cable_tbl.setItem(r, 1, _num_item(float(length_m or 0.0), ".2f", "m"))
            row_index += 1
        if cable_map:
            cable_tbl.setItem(row_index, 0, _str_item("Summe"))
            cable_tbl.setItem(row_index, 1, _num_item(total_length_m, ".2f", "m"))
        cable_tbl.setSortingEnabled(True)
        cable_tbl.resizeColumnsToContents()
        self._elec_cable_total_label.setText(f"Gesamt: {total_length_m:.2f} m")

        ap_tbl = self._elec_ap_mat_table
        ap_tbl.setSortingEnabled(False)
        ap_tbl.setRowCount(len(ap_map))
        for r, (ap_type, count) in enumerate(sorted(ap_map.items())):
            ap_tbl.setItem(r, 0, _str_item(ap_type))
            ap_tbl.setItem(r, 1, _num_item(float(count or 0), ".0f"))
        ap_tbl.setSortingEnabled(True)
        ap_tbl.resizeColumnsToContents()

    def _fill_elec_room_table(self, rooms: list[dict]) -> None:
        self._updating_electro_tables = True
        rows: list[dict] = []
        self._elec_room_row_ap_ids = {}
        sorted_rooms = sorted(
            rooms or [],
            key=lambda room: str(room.get("room_name") or "Ohne Raum").lower(),
        )
        for room in sorted_rooms:
            room_name = str(room.get("room_name") or "Ohne Raum")
            aps = sorted(
                room.get("aps", []) or [],
                key=lambda ap: str(ap.get("name") or ap.get("point_id") or "").lower(),
            )
            if self._elec_room_grouped:
                rows.append(
                    {
                        "room": room_name,
                        "ap": f"{len(aps)} AP",
                        "ap_type": "",
                        "ap_height_cm": "",
                        "point_id": "",
                        "cables": "",
                        "full_cables": "",
                        "is_group": True,
                    }
                )
                for ap in aps:
                    cable_refs = [str(c) for c in (ap.get("cables", []) or []) if str(c).strip()]
                    full_cables = ", ".join(sorted(set(cable_refs))) if cable_refs else "–"
                    rows.append(
                        {
                            "room": "",
                            "ap": str(ap.get("name") or ap.get("point_id") or "–"),
                            "ap_type": str(ap.get("ap_type") or "Unbekannt"),
                            "ap_height_cm": ap.get("height_from_floor_cm"),
                            "point_id": str(ap.get("point_id") or ""),
                            "cables": self._format_cable_refs(cable_refs),
                            "full_cables": full_cables,
                            "is_group": False,
                        }
                    )
                continue

            if not aps:
                rows.append(
                    {
                        "room": room_name,
                        "ap": "–",
                        "ap_type": "–",
                        "ap_height_cm": "–",
                        "point_id": "",
                        "cables": "–",
                        "full_cables": "–",
                        "is_group": False,
                    }
                )
                continue
            for ap in aps:
                cable_refs = [str(c) for c in (ap.get("cables", []) or []) if str(c).strip()]
                full_cables = ", ".join(sorted(set(cable_refs))) if cable_refs else "–"
                rows.append(
                    {
                        "room": room_name,
                        "ap": str(ap.get("name") or ap.get("point_id") or "–"),
                        "ap_type": str(ap.get("ap_type") or "Unbekannt"),
                        "ap_height_cm": ap.get("height_from_floor_cm"),
                        "point_id": str(ap.get("point_id") or ""),
                        "cables": self._format_cable_refs(cable_refs),
                        "full_cables": full_cables,
                        "is_group": False,
                    }
                )

        def _style_group_row(row_index: int) -> None:
            for col in range(tbl.columnCount()):
                item = tbl.item(row_index, col)
                if item is None:
                    continue
                item.setBackground(QBrush(QColor("#2f3b46")))
                item.setForeground(QBrush(QColor("#e8edf2")))
                fnt = item.font()
                fnt.setBold(True)
                item.setFont(fnt)

        tbl = self._elec_room_table
        tbl.setSortingEnabled(False)
        tbl.blockSignals(True)
        tbl.setRowCount(len(rows))
        symbol_options = self._elec_ap_symbol_options()
        for r, row in enumerate(rows):
            point_id = str(row.get("point_id") or "")
            self._elec_room_row_ap_ids[r] = point_id

            room_item = _str_item(row.get("room", ""))
            self._set_item_editable(room_item, False)
            tbl.setItem(r, 0, room_item)

            ap_item = _str_item(row.get("ap", ""))
            self._set_item_editable(ap_item, bool(point_id))
            tbl.setItem(r, 1, ap_item)

            if point_id:
                combo = QComboBox(tbl)
                combo.addItems(symbol_options)
                current_type = str(row.get("ap_type", "") or "")
                if current_type and combo.findText(current_type) < 0:
                    combo.addItem(current_type)
                combo.setCurrentText(current_type)
                combo.currentTextChanged.connect(
                    lambda value, pid=point_id: self._on_elec_room_ap_type_changed(pid, value)
                )
                tbl.setCellWidget(r, 2, combo)
            else:
                tbl.setCellWidget(r, 2, None)
                type_item = _str_item(row.get("ap_type", ""))
                self._set_item_editable(type_item, False)
                tbl.setItem(r, 2, type_item)

            ap_height = row.get("ap_height_cm")
            if isinstance(ap_height, (int, float)):
                height_item = _num_item(float(ap_height), ".1f", "cm")
                self._set_item_editable(height_item, bool(point_id))
                tbl.setItem(r, 3, height_item)
            else:
                height_item = _str_item(str(ap_height or ""))
                self._set_item_editable(height_item, bool(point_id))
                tbl.setItem(r, 3, height_item)
            cable_item = _str_item(row.get("cables", ""))
            self._set_item_editable(cable_item, False)
            cable_item.setToolTip(str(row.get("full_cables", "")))
            tbl.setItem(r, 4, cable_item)
            if row.get("is_group"):
                _style_group_row(r)
        tbl.blockSignals(False)
        self._updating_electro_tables = False
        tbl.resizeColumnsToContents()

    def _fill_elec_cable_table(self, cables: list[dict]) -> None:
        self._updating_electro_tables = True
        tbl = self._elec_cable_table
        tbl.setSortingEnabled(False)
        tbl.blockSignals(True)
        tbl.setRowCount(len(cables or []))
        self._elec_cable_row_ids = {}
        cable_type_options: list[str] = []
        try:
            from model.schema import CABLE_TYPES  # noqa: PLC0415

            cable_type_options = [str(v) for v in CABLE_TYPES if str(v).strip()]
        except Exception:
            cable_type_options = []
        for r, cable in enumerate(cables or []):
            cable_id = str(cable.get("id") or "")
            self._elec_cable_row_ids[r] = cable_id

            name_item = _str_item(cable.get("name", ""))
            self._set_item_editable(name_item, bool(cable_id))
            tbl.setItem(r, 0, name_item)

            current_type = str(cable.get("type", "") or "")
            if cable_id:
                combo = QComboBox(tbl)
                combo.setEditable(True)
                combo.addItems(cable_type_options)
                if current_type and combo.findText(current_type) < 0:
                    combo.addItem(current_type)
                combo.setCurrentText(current_type)
                combo.currentTextChanged.connect(
                    lambda value, cid=cable_id: self._on_elec_cable_type_changed(cid, value)
                )
                tbl.setCellWidget(r, 1, combo)
            else:
                tbl.setCellWidget(r, 1, None)
                type_item = _str_item(current_type)
                self._set_item_editable(type_item, False)
                tbl.setItem(r, 1, type_item)

            length_item = _num_item(float(cable.get("length_m", 0.0) or 0.0), ".2f", "m")
            self._set_item_editable(length_item, False)
            tbl.setItem(r, 2, length_item)

            start_item = _str_item(cable.get("start_ap_name", ""))
            self._set_item_editable(start_item, False)
            tbl.setItem(r, 3, start_item)

            end_item = _str_item(cable.get("end_ap_name", ""))
            self._set_item_editable(end_item, False)
            tbl.setItem(r, 4, end_item)
        tbl.blockSignals(False)
        self._updating_electro_tables = False
        tbl.setSortingEnabled(True)
        tbl.resizeColumnsToContents()

    def _on_elec_room_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_electro_tables or item is None:
            return
        row = item.row()
        col = item.column()
        point_id = str(self._elec_room_row_ap_ids.get(row, "") or "").strip()
        if not point_id:
            return
        if col == 1:
            self.pre_change.emit()
            self.element_field_changed.emit(point_id, "name", str(item.text() or "").strip())
            return
        if col == 3:
            value_cm = self._parse_float_text(item.text())
            if value_cm is None:
                self._do_refresh()
                return
            self.pre_change.emit()
            self.element_field_changed.emit(point_id, "height_from_floor", float(value_cm))

    def _on_elec_room_ap_type_changed(self, point_id: str, value: str) -> None:
        if self._updating_electro_tables:
            return
        point_id = str(point_id or "").strip()
        if not point_id:
            return
        self.pre_change.emit()
        self.element_field_changed.emit(point_id, "builtin_symbol", str(value or "").strip())

    def _on_elec_cable_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_electro_tables or item is None:
            return
        row = item.row()
        col = item.column()
        cable_id = str(self._elec_cable_row_ids.get(row, "") or "").strip()
        if not cable_id:
            return
        if col == 0:
            self.pre_change.emit()
            self.element_field_changed.emit(cable_id, "name", str(item.text() or "").strip())

    def _on_elec_cable_type_changed(self, cable_id: str, value: str) -> None:
        if self._updating_electro_tables:
            return
        cable_id = str(cable_id or "").strip()
        if not cable_id:
            return
        self.pre_change.emit()
        self.element_field_changed.emit(cable_id, "type", str(value or "").strip())
