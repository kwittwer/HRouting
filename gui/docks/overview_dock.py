"""Projektübersicht-Dock mit drei Tabs (Allgemein / Heizung / Elektro)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDockWidget,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
    """Zeigt berechnete Projektübersicht in drei Tabs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Projektübersicht", parent)
        self.setObjectName("dock_overview")
        self.setMinimumWidth(360)

        self._document: Document | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._do_refresh)

        # ── Tab-Widget ──────────────────────────────────────────────
        self._tabs = QTabWidget()
        self.setWidget(self._tabs)

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

        # Tab Heizung
        self._heating_scroll = QScrollArea()
        self._heating_scroll.setWidgetResizable(True)
        self._heating_inner = QWidget()
        self._heating_layout = QVBoxLayout(self._heating_inner)
        self._heating_layout.setContentsMargins(4, 4, 4, 4)
        self._heating_layout.setSpacing(6)
        self._heating_scroll.setWidget(self._heating_inner)
        self._tabs.addTab(self._heating_scroll, "Heizung")

        # Heizkreis-Tabelle
        self._hk_section = _CollapsibleSection("Heizkreise", expanded=True)
        self._hk_table = self._build_hk_table()
        hk_inner_layout = QVBoxLayout()
        hk_inner_layout.setContentsMargins(0, 0, 0, 0)
        hk_inner_layout.addWidget(self._hk_table)
        self._hk_section.set_content_layout(hk_inner_layout)
        self._heating_layout.addWidget(self._hk_section)

        # HKV-Tabelle
        self._hkv_section = _CollapsibleSection("Heizkreisverteiler", expanded=True)
        self._hkv_table = self._build_hkv_table()
        hkv_inner_layout = QVBoxLayout()
        hkv_inner_layout.setContentsMargins(0, 0, 0, 0)
        hkv_inner_layout.addWidget(self._hkv_table)
        self._hkv_section.set_content_layout(hkv_inner_layout)
        self._heating_layout.addWidget(self._hkv_section)

        # Materialliste
        self._mat_section = _CollapsibleSection("Materialliste", expanded=True)
        self._mat_form = QFormLayout()
        self._mat_form.setContentsMargins(8, 4, 4, 4)
        self._mat_section.set_content_layout(self._mat_form)
        self._heating_layout.addWidget(self._mat_section)
        self._heating_layout.addStretch()

        # Tab Elektro (Platzhalter)
        elec_placeholder = QWidget()
        elec_layout = QVBoxLayout(elec_placeholder)
        elec_layout.addWidget(QLabel("Elektro-Übersicht (in Vorbereitung)"))
        elec_layout.addStretch()
        self._tabs.addTab(elec_placeholder, "Elektro")

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

    def _clear_all(self) -> None:
        self._hk_table.setRowCount(0)
        self._hkv_table.setRowCount(0)
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
