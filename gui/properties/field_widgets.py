"""Eingabewidgets für die schema-getriebenen Eigenschaften-Editoren.

Jedes Widget kapselt genau einen :class:`~model.schema.FieldSpec` und meldet
Änderungen über ``value_changed``. Beim programmatischen Setzen wird das Signal
unterdrückt, damit kein Rückkopplungskreis entsteht.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from model.schema import FieldKind, FieldSpec


class FieldWidget(QWidget):
    """Basisklasse aller Feld-Widgets."""

    value_changed = Signal(str, object)  # (field key, neuer Wert)

    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._updating = False
        if spec.tooltip:
            self.setToolTip(spec.tooltip)

    # -- von Unterklassen zu implementieren ------------------------------
    def value(self) -> Any:  # pragma: no cover - abstrakt
        raise NotImplementedError

    def set_value(self, value: Any) -> None:  # pragma: no cover - abstrakt
        raise NotImplementedError

    # -- Hilfen ----------------------------------------------------------
    def _emit(self, value: Any) -> None:
        if not self._updating:
            self.value_changed.emit(self.spec.key, value)

    def update_silently(self, value: Any) -> None:
        """Setzt den Wert, ohne ``value_changed`` auszulösen."""
        self._updating = True
        try:
            self.set_value(value)
        finally:
            self._updating = False


class TextFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(self)
        self._edit.editingFinished.connect(
            lambda: self._emit(self._edit.text())
        )
        layout.addWidget(self._edit)

    def value(self) -> Any:
        return self._edit.text()

    def set_value(self, value: Any) -> None:
        self._edit.setText("" if value is None else str(value))


class MultilineFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QPlainTextEdit(self)
        self._edit.setMaximumHeight(64)
        self._edit.focusOutEvent = self._wrap_focus_out(self._edit.focusOutEvent)
        layout.addWidget(self._edit)

    def _wrap_focus_out(self, original):
        def handler(event):
            original(event)
            self._emit(self._edit.toPlainText())

        return handler

    def value(self) -> Any:
        return self._edit.toPlainText()

    def set_value(self, value: Any) -> None:
        self._edit.setPlainText("" if value is None else str(value))


class NumberFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._spin = QDoubleSpinBox(self)
        self._spin.setRange(spec.minimum, spec.maximum)
        self._spin.setSingleStep(spec.step)
        self._spin.setDecimals(spec.decimals)
        self._spin.setKeyboardTracking(False)
        if spec.unit:
            self._spin.setSuffix(f" {spec.unit}")
        self._spin.valueChanged.connect(self._emit)
        layout.addWidget(self._spin)

    def value(self) -> Any:
        return self._spin.value()

    def set_value(self, value: Any) -> None:
        try:
            self._spin.setValue(float(value))
        except (TypeError, ValueError):
            self._spin.setValue(float(self.spec.default or 0.0))


class BoolFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._box = QCheckBox(self)
        self._box.toggled.connect(self._emit)
        layout.addWidget(self._box)
        layout.addStretch(1)

    def value(self) -> Any:
        return self._box.isChecked()

    def set_value(self, value: Any) -> None:
        self._box.setChecked(bool(value))


class ColorFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._color = str(spec.default or "#ffffff")
        self._button = QPushButton(self)
        self._button.setFixedHeight(22)
        self._button.clicked.connect(self._pick_color)
        layout.addWidget(self._button)
        self._apply_style()

    def _apply_style(self) -> None:
        self._button.setText(self._color)
        self._button.setStyleSheet(
            f"background-color: {self._color}; color: "
            f"{'#000000' if QColor(self._color).lightness() > 128 else '#ffffff'};"
        )

    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Farbe wählen")
        if chosen.isValid():
            self._color = chosen.name()
            self._apply_style()
            self._emit(self._color)

    def value(self) -> Any:
        return self._color

    def set_value(self, value: Any) -> None:
        self._color = str(value or self.spec.default or "#ffffff")
        self._apply_style()


class ChoiceFieldWidget(FieldWidget):
    def __init__(
        self,
        spec: FieldSpec,
        editable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._combo = QComboBox(self)
        self._combo.setEditable(editable)
        self._combo.addItems(list(spec.resolve_options()))
        if editable:
            self._combo.editTextChanged.connect(self._emit)
        else:
            self._combo.currentTextChanged.connect(self._emit)
        layout.addWidget(self._combo)

    def value(self) -> Any:
        return self._combo.currentText()

    def set_value(self, value: Any) -> None:
        text = "" if value is None else str(value)
        index = self._combo.findText(text)
        if index >= 0:
            self._combo.setCurrentIndex(index)
        elif self._combo.isEditable():
            self._combo.setEditText(text)
        elif text:
            self._combo.addItem(text)
            self._combo.setCurrentIndex(self._combo.count() - 1)


class FileFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._edit = QLineEdit(self)
        self._edit.editingFinished.connect(lambda: self._emit(self._edit.text()))
        self._button = QPushButton("…", self)
        self._button.setFixedWidth(28)
        self._button.clicked.connect(self._browse)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._button)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.spec.label, "", self.spec.file_filter or "Alle Dateien (*)"
        )
        if path:
            self._edit.setText(path)
            self._emit(path)

    def value(self) -> Any:
        return self._edit.text()

    def set_value(self, value: Any) -> None:
        self._edit.setText("" if value is None else str(value))


class ReadOnlyFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("–", self)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._label)

    def value(self) -> Any:
        return self._label.text()

    def set_value(self, value: Any) -> None:
        self._label.setText("–" if value in (None, "") else str(value))


_FACTORY = {
    FieldKind.TEXT: lambda spec, parent: TextFieldWidget(spec, parent),
    FieldKind.MULTILINE: lambda spec, parent: MultilineFieldWidget(spec, parent),
    FieldKind.NUMBER: lambda spec, parent: NumberFieldWidget(spec, parent),
    FieldKind.BOOL: lambda spec, parent: BoolFieldWidget(spec, parent),
    FieldKind.COLOR: lambda spec, parent: ColorFieldWidget(spec, parent),
    FieldKind.CHOICE: lambda spec, parent: ChoiceFieldWidget(spec, False, parent),
    FieldKind.EDITABLE_CHOICE: lambda spec, parent: ChoiceFieldWidget(spec, True, parent),
    FieldKind.FILE: lambda spec, parent: FileFieldWidget(spec, parent),
    FieldKind.READONLY: lambda spec, parent: ReadOnlyFieldWidget(spec, parent),
}


def create_field_widget(spec: FieldSpec, parent: QWidget | None = None) -> FieldWidget:
    """Erzeugt das zum Feldtyp passende Widget."""
    factory = _FACTORY.get(spec.kind, _FACTORY[FieldKind.TEXT])
    return factory(spec, parent)
