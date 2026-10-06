"""Copy selected table rows as locale-aware, Excel-friendly TSV."""

from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QMenu,
    QTableWidget,
    QWidget,
)

from gui.properties.field_widgets import ColorFieldWidget, ChoiceFieldWidget, NumberFieldWidget

_NUMBER_TEXT = re.compile(r"^[+-]?\d+(?:[.,](\d+))?$")
_NUMBER_WITH_UNIT = re.compile(r"^\s*[+-]?\d+(?:[.,](\d+))?(?:\s+\D.*)?$")


def _format_number(value: float, decimals: int) -> str:
    return QLocale().toString(value, "f", decimals)


def _cell_text(table: QTableWidget, row: int, column: int, widget: QWidget | None) -> str:
    if isinstance(widget, QComboBox):
        value = widget.currentText()
    elif isinstance(widget, NumberFieldWidget):
        return _format_number(float(widget.value()), widget.spec.decimals)
    elif isinstance(widget, (ColorFieldWidget, ChoiceFieldWidget)):
        value = widget.value()
    else:
        item = table.item(row, column)
        if item is None:
            return ""
        value = item.text()
        raw_value = item.data(Qt.UserRole)
        if isinstance(raw_value, (float, int)) and not isinstance(raw_value, bool):
            match = _NUMBER_WITH_UNIT.fullmatch(value)
            decimals = len(match.group(1)) if match and match.group(1) else 0
            value = _format_number(float(raw_value), decimals)
        else:
            match = _NUMBER_TEXT.fullmatch(str(value).strip())
            if match:
                decimals = len(match.group(1)) if match.group(1) else 0
                value = _format_number(float(str(value).replace(",", ".")), decimals)

    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def copy_selected_table_rows(
    table: QTableWidget,
    *,
    column_count: int | None = None,
    cell_widget_resolver: Callable[[int, int], QWidget | None] | None = None,
) -> bool:
    rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
    if not rows:
        return False

    columns = table.columnCount() if column_count is None else min(column_count, table.columnCount())
    headers = []
    for column in range(columns):
        header = table.horizontalHeaderItem(column)
        text = header.text() if header is not None else ""
        headers.append(text.replace("\t", " ").replace("\r", " ").replace("\n", " "))
    copied_rows = ["\t".join(headers)]
    for row in rows:
        values = []
        for column in range(columns):
            widget = (
                cell_widget_resolver(row, column)
                if cell_widget_resolver is not None
                else table.cellWidget(row, column)
            )
            values.append(_cell_text(table, row, column, widget))
        copied_rows.append("\t".join(values))

    QApplication.clipboard().setText("\r\n".join(copied_rows))
    return True


def enable_table_row_copy(
    table: QTableWidget,
    *,
    copy_handler: Callable[[], bool] | None = None,
    copy_shortcut: bool = True,
) -> None:
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    table.setContextMenuPolicy(Qt.CustomContextMenu)
    copy_rows = copy_handler or (lambda: copy_selected_table_rows(table))

    def show_context_menu(position) -> None:
        menu = QMenu(table)
        action = menu.addAction("Auswahl kopieren")
        action.setEnabled(bool(table.selectionModel().selectedRows()))
        action.triggered.connect(copy_rows)
        menu.exec(table.viewport().mapToGlobal(position))

    table.customContextMenuRequested.connect(show_context_menu)
    if copy_shortcut:
        action = QAction("Auswahl kopieren", table)
        action.setShortcut(QKeySequence.Copy)
        action.setShortcutContext(Qt.WidgetShortcut)
        action.triggered.connect(copy_rows)
        table.addAction(action)