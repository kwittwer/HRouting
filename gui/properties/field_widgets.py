"""Eingabewidgets für die schema-getriebenen Eigenschaften-Editoren.

Jedes Widget kapselt genau einen :class:`~model.schema.FieldSpec` und meldet
Änderungen über ``value_changed``. Beim programmatischen Setzen wird das Signal
unterdrückt, damit kein Rückkopplungskreis entsteht.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
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
    QSizePolicy,
    QWidget,
)

from model.schema import ChoiceOption, FieldKind, FieldSpec


class SafeDoubleSpinBox(QDoubleSpinBox):
    """SpinBox that ignores mouse-wheel adjustments."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class SafeComboBox(QComboBox):
    """ComboBox that ignores mouse-wheel adjustments."""

    def wheelEvent(self, event) -> None:
        event.ignore()


def _option_parts(option: ChoiceOption) -> tuple[str, str]:
    if isinstance(option, tuple):
        return str(option[0]), str(option[1])
    text = str(option)
    return text, text


# Freitext in editierbaren Auswahlfeldern wird erst nach einer Tippause
# übernommen. Jedes Zeichen sofort zu melden löst die komplette
# Dokument-Aktualisierung aus (Undo-Snapshot, Schema-Neuaufbau, Canvas).
EDITABLE_CHOICE_COMMIT_IDLE_MS = 400


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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(self)
        self._edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._edit.editingFinished.connect(
            lambda: self._emit(self._edit.text())
        )
        layout.addWidget(self._edit, 1)

    def value(self) -> Any:
        return self._edit.text()

    def set_value(self, value: Any) -> None:
        self._edit.setText("" if value is None else str(value))


class MultilineFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QPlainTextEdit(self)
        self._edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._edit.setMaximumHeight(64)
        self._edit.focusOutEvent = self._wrap_focus_out(self._edit.focusOutEvent)
        layout.addWidget(self._edit, 1)

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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._spin = SafeDoubleSpinBox(self)
        self._spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._spin.setRange(spec.minimum, spec.maximum)
        self._spin.setSingleStep(spec.step)
        self._spin.setDecimals(spec.decimals)
        self._spin.setKeyboardTracking(False)
        if spec.unit:
            self._spin.setSuffix(f" {spec.unit}")
        self._spin.valueChanged.connect(self._emit)
        layout.addWidget(self._spin, 1)

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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._box = QCheckBox(self)
        self._box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._color = str(spec.default or "#ffffff")
        self._button = QPushButton(self)
        self._button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._button.setFixedHeight(22)
        self._button.clicked.connect(self._pick_color)
        layout.addWidget(self._button, 1)
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
        options: tuple[ChoiceOption, ...] | None = None,
    ) -> None:
        super().__init__(spec, parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._combo = SafeComboBox(self)
        self._combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._combo.setMinimumWidth(100)
        self._combo.setEditable(editable)
        self._commit_timer: QTimer | None = None
        self._pending_edit = False
        self._committed_text: str = ""
        self._set_combo_options(options if options is not None else spec.resolve_options())
        if editable:
            self._commit_timer = QTimer(self)
            self._commit_timer.setSingleShot(True)
            self._commit_timer.setInterval(EDITABLE_CHOICE_COMMIT_IDLE_MS)
            self._commit_timer.timeout.connect(self.commit_pending_edit)
            self._committed_text = self._combo.currentText()
            self._combo.editTextChanged.connect(self._on_edit_text_changed)
            self._combo.activated.connect(lambda _index: self._commit_text(force=True))
            line_edit = self._combo.lineEdit()
            if line_edit is not None:
                line_edit.editingFinished.connect(self.commit_pending_edit)
        else:
            self._combo.currentIndexChanged.connect(lambda _index: self._emit(self.value()))
        layout.addWidget(self._combo, 1)

    # -- Commit-Steuerung für editierbare Felder -------------------------
    def _on_edit_text_changed(self, _text: str) -> None:
        if self._updating or self._commit_timer is None:
            return
        self._pending_edit = True
        self._commit_timer.start()

    def has_pending_edit(self) -> bool:
        """True, solange eine Freitexteingabe noch nicht übernommen wurde."""
        return self._pending_edit

    def commit_pending_edit(self) -> None:
        """Übernimmt eine offene Freitexteingabe (Tippause, Enter, Fokusverlust)."""
        self._commit_text(force=False)

    def _commit_text(self, *, force: bool) -> None:
        if self._commit_timer is None:
            return
        self._commit_timer.stop()
        pending = self._pending_edit
        self._pending_edit = False
        if self._updating or (not pending and not force):
            return
        text = self._combo.currentText()
        if text == self._committed_text:
            return
        self._committed_text = text
        self._emit(text)

    def _set_combo_options(self, options: tuple[ChoiceOption, ...]) -> None:
        self._combo.clear()
        for option in options:
            value, label = _option_parts(option)
            self._combo.addItem(label, value)

    def set_options(self, options: tuple[ChoiceOption, ...]) -> None:
        """Tauscht die Auswahlliste aus und hält den aktuellen Wert."""
        # Compare actual entries: set_value() may have inserted legacy values
        # that must still be removed even when the source options are unchanged.
        if self._combo.count() == len(options) and all(
            (self._combo.itemData(index), self._combo.itemText(index))
            == _option_parts(option)
            for index, option in enumerate(options)
        ):
            return

        current = self.value()
        was_updating = self._updating
        self._updating = True
        try:
            self._set_combo_options(options)
            # For non-editable combos we must not re-insert stale values that
            # were removed from the source options (e.g. deleted distributors).
            if self._combo.isEditable():
                # Direkt anwenden: eine laufende Eingabe bleibt sichtbar und
                # ihr ausstehender Commit behält den richtigen Vergleichswert.
                self._apply_text("" if current is None else str(current))
            else:
                index = self._combo.findData("" if current is None else str(current))
                if index >= 0:
                    self._combo.setCurrentIndex(index)
                elif self._combo.count() > 0:
                    self._combo.setCurrentIndex(0)
        finally:
            self._updating = was_updating

    def value(self) -> Any:
        if self._combo.isEditable():
            return self._combo.currentText()
        data = self._combo.currentData()
        return self._combo.currentText() if data is None else data

    def set_value(self, value: Any) -> None:
        if self._pending_edit:
            if self._combo.hasFocus():
                # Laufende Eingabe nicht überschreiben; sie meldet sich selbst.
                return
            # Ohne Fokus gewinnt der programmatische Wert.
            self._pending_edit = False
            if self._commit_timer is not None:
                self._commit_timer.stop()
        text = "" if value is None else str(value)
        self._committed_text = text
        self._apply_text(text)

    def _apply_text(self, text: str) -> None:
        index = self._combo.findData(text)
        if index < 0:
            index = self._combo.findText(text)
        if index >= 0:
            self._combo.setCurrentIndex(index)
        elif self._combo.isEditable():
            self._combo.setEditText(text)
        elif text:
            self._combo.addItem(text, text)
            self._combo.setCurrentIndex(self._combo.count() - 1)


class FileFieldWidget(FieldWidget):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._edit = QLineEdit(self)
        self._edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("–", self)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._label, 1)

    def value(self) -> Any:
        return self._label.text()

    def set_value(self, value: Any) -> None:
        self._label.setText("–" if value in (None, "") else str(value))


_FACTORY = {
    FieldKind.TEXT: lambda spec, parent, options: TextFieldWidget(spec, parent),
    FieldKind.MULTILINE: lambda spec, parent, options: MultilineFieldWidget(spec, parent),
    FieldKind.NUMBER: lambda spec, parent, options: NumberFieldWidget(spec, parent),
    FieldKind.BOOL: lambda spec, parent, options: BoolFieldWidget(spec, parent),
    FieldKind.COLOR: lambda spec, parent, options: ColorFieldWidget(spec, parent),
    FieldKind.CHOICE: lambda spec, parent, options: ChoiceFieldWidget(
        spec, False, parent, options
    ),
    FieldKind.EDITABLE_CHOICE: lambda spec, parent, options: ChoiceFieldWidget(
        spec, True, parent, options
    ),
    FieldKind.FILE: lambda spec, parent, options: FileFieldWidget(spec, parent),
    FieldKind.READONLY: lambda spec, parent, options: ReadOnlyFieldWidget(spec, parent),
}


def create_field_widget(
    spec: FieldSpec,
    parent: QWidget | None = None,
    options: tuple[ChoiceOption, ...] | None = None,
) -> FieldWidget:
    """Erzeugt das zum Feldtyp passende Widget.

    ``options`` überschreibt die Auswahlliste des Schemas – nötig für Felder,
    deren Werte vom Projektinhalt abhängen.
    """
    factory = _FACTORY.get(spec.kind, _FACTORY[FieldKind.TEXT])
    return factory(spec, parent, options)
