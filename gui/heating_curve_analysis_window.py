"""Reference inputs and independent outdoor/supply curves for heating circuits."""

from __future__ import annotations

from copy import deepcopy
import csv
from pathlib import Path
import time

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QGraphicsScene,
    QGraphicsView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QLineEdit,
)

from logic.heating_curve_analysis import (
    SETTINGS_KEY, default_config, individual_curve_steps, number, range_values, validate_load_settings,
)
from model.computed import heating_length_overview


REFERENCE_FIELDS = ("specific_heat_load_wm2", "reference_room_temp", "reference_outdoor_temp")


class PlotView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(220)
        self.setSceneRect(0, 0, 850, 350)
        self.series = []

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def set_series(self, series, x_label="Aussentemperatur [C]", y_label="Notwendiger Vorlauf [C]"):
        self.series = series
        scene = self.scene()
        scene.clear()
        legend_rows = (len(series) + 1) // 2 if len(series) > 1 else 0
        self.setSceneRect(0, 0, 850, 350 + legend_rows * 24)
        valid = [(outside, supply) for _, _, points in series for outside, supply in points if supply is not None]
        outside_values = [outside for _, _, points in series for outside, _ in points]
        scene.addText(x_label).setPos(330, 310)
        scene.addText(y_label).setPos(5, 0)
        if not outside_values:
            scene.addText("Keine Kurvenpunkte").setPos(330, 155)
            return
        x_min, x_max = min(outside_values), max(outside_values)
        y_min, y_max = (min(point[1] for point in valid), max(point[1] for point in valid)) if valid else (20, 40)
        x_max = max(x_max, x_min + 1)
        y_min, y_max = y_min - 2, max(y_max + 2, y_min + 4)

        def position(outside, supply):
            return 90 + 710 * (outside - x_min) / (x_max - x_min), 270 - 210 * (supply - y_min) / (y_max - y_min)

        axis_pen = QPen(QColor("#747474"))
        scene.addLine(90, 60, 90, 270, axis_pen)
        scene.addLine(90, 270, 800, 270, axis_pen)
        for index in range(6):
            outside = x_min + (x_max - x_min) * index / 5
            supply = y_min + (y_max - y_min) * index / 5
            horizontal, _ = position(outside, y_min)
            _, vertical = position(x_min, supply)
            scene.addText(f"{outside:.1f}").setPos(horizontal - 15, 274)
            scene.addText(f"{supply:.1f}").setPos(25, vertical - 10)
        for index, (name, color, points) in enumerate(series):
            pen = QPen(QColor(color), 2)
            path = QPainterPath()
            connected = False
            for outside, supply in points:
                if supply is None:
                    connected = False
                    continue
                horizontal, vertical = position(outside, supply)
                if connected:
                    path.lineTo(horizontal, vertical)
                else:
                    path.moveTo(horizontal, vertical)
                connected = True
                marker = scene.addEllipse(horizontal - 2, vertical - 2, 4, 4, pen)
                marker.setToolTip(f"{name}: Aussen {outside:g} C, Vorlauf {supply:.1f} C")
            scene.addPath(path, pen).setToolTip(name)
            if len(series) > 1:
                horizontal, vertical = 90 + (index % 2) * 365, 350 + (index // 2) * 24
                scene.addLine(horizontal, vertical + 10, horizontal + 20, vertical + 10, pen)
                label = scene.addText(QFontMetricsF(scene.font()).elidedText(name, Qt.ElideRight, 325))
                label.setPos(horizontal + 25, vertical)
                label.setToolTip(name)
            else:
                scene.addText(name).setPos(110, 25)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


class HeatingCurveAnalysisWindow(QMainWindow):
    config_changed = Signal(dict, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Heizkurvenanalyse")
        self.resize(1080, 820)
        self._document = None
        self._loading = False
        self._changed = False
        self._loaded_config = {}
        self._job = None
        self.result = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self.save_settings)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._build_ui()

    def _spin(self, minimum, maximum):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.valueChanged.connect(self._edited)
        return spin

    def _build_ui(self):
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)
        self.inputs = QTableWidget(0, 5)
        self.inputs.setHorizontalHeaderLabels([
            "Heizkreis", "Name", "Spezifische Heizlast [W/m2]", "Referenz innen [C]", "Referenz aussen [C]",
        ])
        self.inputs.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
                                    | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.inputs.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.inputs.setMinimumHeight(170)
        self.inputs.itemChanged.connect(self._edited)
        self.inputs.setToolTip("Referenzwerte je Heizkreis. Die Solltemperatur und Spreizung stammen aus dem Projekt.")
        controls = QWidget()
        grid = QGridLayout(controls)
        self.outdoor = [self._spin(-80, 60), self._spin(-80, 60), self._spin(0.01, 50)]
        for column, (label, widget) in enumerate(zip(("Aussen Minimum [C]", "Maximum [C]", "Schritt [K]"), self.outdoor)):
            grid.addWidget(QLabel(label), 0, column)
            grid.addWidget(widget, 1, column)
        self.heating_limit = self._spin(-20, 40)
        grid.addWidget(QLabel("Heizgrenze [C]"), 0, 3)
        grid.addWidget(self.heating_limit, 1, 3)
        self.compute = QPushButton("Berechnen")
        self.compute.clicked.connect(self.start_analysis)
        self.cancel = QPushButton("Abbrechen")
        self.cancel.clicked.connect(self.cancel_analysis)
        self.cancel.setEnabled(False)
        grid.addWidget(self.compute, 3, 0, 1, 2)
        grid.addWidget(self.cancel, 3, 2, 1, 2)
        self.project_info = QLabel()
        self.project_info.setWordWrap(True)
        grid.addWidget(self.project_info, 2, 0, 1, 4)
        self.progress = QProgressBar()
        grid.addWidget(self.progress, 4, 0, 1, 4)
        chart_panel = QWidget()
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        selection = QHBoxLayout()
        selection.addWidget(QLabel("Heizkreis"))
        self.circuit_choice = QComboBox()
        self.circuit_choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.circuit_choice.setMinimumContentsLength(15)
        self.circuit_choice.currentIndexChanged.connect(self._show_curve)
        selection.addWidget(self.circuit_choice, 1)
        self.export = QPushButton("CSV exportieren")
        self.export.clicked.connect(self._export_dialog)
        self.export.setEnabled(False)
        selection.addWidget(self.export)
        chart_layout.addLayout(selection)
        self.curve_plot = PlotView()
        chart_layout.addWidget(self.curve_plot, 1)
        self.curve_status = QLabel("Referenzwerte eingeben und berechnen.")
        self.curve_status.setWordWrap(True)
        chart_layout.addWidget(self.curve_status)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self.inputs)
        top_layout.addWidget(controls)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(chart_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 450])
        layout.addWidget(splitter)
        self.curve_plot.setToolTip("Stationaere Einzelkreis-Heizkurve mit Projektspreizung, Genauigkeit etwa 0.1 C. Modellobergrenze 90 C; keine hydraulische Freigabe oder COP-Prognose.")
        self.curve_plot.set_series([])

    def set_document(self, document):
        self.cancel_analysis()
        self._save_timer.stop()
        self._document = document
        self._loading = True
        try:
            config = default_config(float(document.settings.get("t_norm_outdoor", -12)))
            config.update(deepcopy(document.settings.get(SETTINGS_KEY, {})))
            self._loaded_config = config
            for widget, value in zip(self.outdoor, config["outdoor"]):
                widget.setValue(value)
            self.heating_limit.setValue(config["heating_limit"])
            circuits = document.elements.get("circuits", {})
            self.inputs.setRowCount(len(circuits))
            self.circuit_choice.clear()
            for row, (circuit_id, circuit) in enumerate(sorted(circuits.items())):
                values = config["circuits"].get(circuit_id, {})
                cells = [circuit_id, circuit.name or circuit_id, *(values.get(key) for key in REFERENCE_FIELDS)]
                for column, value in enumerate(cells):
                    item = QTableWidgetItem("" if value is None else str(value))
                    if column < 2:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.inputs.setItem(row, column, item)
                self.circuit_choice.addItem(f"{circuit_id} {circuit.name or circuit_id}", circuit_id)
            if circuits:
                self.circuit_choice.addItem("Alle Heizkreise", "__all__")
            self._changed = False
            self.invalidate()
        finally:
            self._loading = False

    def _commit_editor(self):
        editor = QApplication.focusWidget()
        if isinstance(editor, QLineEdit) and self.inputs.isAncestorOf(editor):
            delegate = self.inputs.itemDelegate()
            delegate.commitData.emit(editor)
            delegate.closeEditor.emit(editor, QAbstractItemDelegate.NoHint)

    def configuration(self, strict=True):
        self._commit_editor()
        config = deepcopy(self._loaded_config)
        config["outdoor"] = [widget.value() for widget in self.outdoor]
        range_values(config["outdoor"])
        config["heating_limit"] = self.heating_limit.value()
        if config["heating_limit"] <= float(self._document.settings.get("t_norm_outdoor", -12)):
            raise ValueError("Die Heizgrenze muss ueber der Projekt-Norm-Aussentemperatur liegen.")
        config["circuits"] = {}
        for row in range(self.inputs.rowCount()):
            circuit_id = self.inputs.item(row, 0).text()
            settings = deepcopy(self._loaded_config.get("circuits", {}).get(circuit_id, {}))
            settings["load_mode"] = "reference_specific"
            for column, key in enumerate(REFERENCE_FIELDS, 2):
                text = self.inputs.item(row, column).text().strip()
                try:
                    settings[key] = number(text.replace(",", ".")) if text else None
                except (ValueError, TypeError, OverflowError) as error:
                    if strict:
                        raise ValueError(f"{circuit_id}: Bitte gueltige Zahlen eingeben.") from error
                    settings[key] = None
                    settings["reference_error"] = "Referenzwerte ungueltig"
            active = {"load_mode": "reference_specific", **{key: settings[key] for key in REFERENCE_FIELDS}}
            if strict:
                validate_load_settings(active)
            config["circuits"][circuit_id] = settings
        return config

    def _edited(self, *_args):
        if self._loading:
            return
        self._changed = True
        self.invalidate()
        self._save_timer.start()

    def save_settings(self):
        self._commit_editor()
        self._save_timer.stop()
        if self._document is None:
            return False
        if not self._changed:
            return True
        try:
            config = self.configuration()
        except (ValueError, TypeError, OverflowError) as error:
            self.statusBar().showMessage(str(error))
            return False
        self.config_changed.emit(config, float(self._document.settings.get("t_norm_outdoor", -12)))
        self._loaded_config = deepcopy(config)
        self._changed = False
        return True

    def invalidate(self, *_args):
        self.cancel_analysis()
        self.result = None
        self.export.setEnabled(False)
        self.curve_plot.set_series([])
        self.curve_status.setText("Referenzwerte eingeben und berechnen.")
        if self._document is not None:
            spread = float(self._document.settings.get("t_supply", 35)) - float(self._document.settings.get("t_return", 30))
            self.project_info.setText(f"Projektspreizung: {spread:g} K | Solltemperatur aus dem jeweiligen Heizkreis")
        self.statusBar().showMessage("Bereit zur Berechnung")

    def start_analysis(self):
        if self._document is None:
            return
        try:
            config = self.configuration(strict=False)
            spread = number(self._document.settings.get("t_supply", 35)) - number(self._document.settings.get("t_return", 30))
            if spread <= 0:
                raise ValueError("Die Projektspreizung muss positiv sein (Vorlauf groesser als Ruecklauf).")
            rows, _, _ = heating_length_overview(self._document)
            if len(rows) * len(range_values(config["outdoor"])) > 250_000:
                raise ValueError("Zu viele Kurvenpunkte. Bitte die Schrittweite erhoehen.")
            self.save_settings()
            self.invalidate()
            self._job = individual_curve_steps(rows, config["circuits"], config["outdoor"], spread, config["heating_limit"])
            self._timer.start(0)
            self.compute.setEnabled(False)
            self.cancel.setEnabled(True)
            self.progress.setValue(0)
        except (ValueError, TypeError, OverflowError) as error:
            self.statusBar().showMessage(str(error))

    def _advance(self):
        deadline = time.monotonic() + 0.012
        try:
            while self._job is not None and time.monotonic() < deadline:
                done, total = next(self._job)
                self.progress.setValue(int(100 * done / max(total, 1)))
        except StopIteration as finished:
            self.result = finished.value
            self.cancel_analysis()
            self.progress.setValue(100)
            self.export.setEnabled(bool(self.result))
            self._show_curve()
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            self.cancel_analysis()
            self.statusBar().showMessage(str(error))

    def cancel_analysis(self):
        self._timer.stop()
        if self._job is not None:
            self._job.close()
        self._job = None
        self.compute.setEnabled(True)
        self.cancel.setEnabled(False)

    def _show_curve(self, *_args):
        if self.result is None:
            return
        circuit_id = self.circuit_choice.currentData()
        series = []
        selected = self._selected_curves()
        for index, (key, rows) in enumerate(self.result.items()):
            if key not in selected:
                continue
            color = QColor.fromHsv((index * 137) % 360, 185, 180).name()
            name = f"{key} {rows[0]['name']}" if rows else key
            series.append((name, color, [(row["outdoor"], row["supply"]) for row in rows]))
        self.curve_plot.set_series(series)
        if circuit_id == "__all__":
            missing = [key for key, rows in selected.items() if not any(row["supply"] is not None for row in rows)]
            self.curve_status.setText(f"Alle Heizkreise: {len(selected) - len(missing)}/{len(selected)} mit Vorlaufkurve"
                                      + (f" | Keine Vorlaufpunkte: {', '.join(missing)}" if missing else ""))
            self.statusBar().showMessage("Alle Einzelkreis-Heizkurven")
            return
        rows = self.result.get(circuit_id, [])
        if rows:
            statuses = ", ".join(sorted({row["status"] for row in rows if row["status"] != "OK"}))
            valid = sum(row["supply"] is not None for row in rows)
            self.curve_status.setText(f"{circuit_id}: {valid}/{len(rows)} Vorlaufpunkte | "
                                      f"Soll innen {rows[0]['room_temp']:g} C | Spreizung {rows[0]['spread']:g} K"
                                      + (f" | {statuses}" if statuses else ""))
        else:
            self.curve_status.setText("Keine Heizkreise vorhanden.")
        self.statusBar().showMessage("Einzelkreis-Heizkurven berechnet")

    def _selected_curves(self):
        if self.result is None:
            return {}
        selected = self.circuit_choice.currentData()
        return self.result if selected == "__all__" else {key: rows for key, rows in self.result.items() if key == selected}

    def export_csv(self, path):
        if self.result is None:
            raise ValueError("Keine aktuellen Ergebnisse.")
        rows = [row for curve in self._selected_curves().values() for row in curve]
        columns = [("id", "Heizkreis"), ("outdoor", "Aussentemperatur [C]"), ("supply", "Notwendiger Vorlauf [C]"),
                   ("return", "Ruecklauf [C]"), ("spread", "Spreizung [K]"), ("q_needed", "Bedarf [W/m2]"),
                   ("demand_w", "Bedarf [W]"), ("room_temp", "Soll innen [C]"),
                   ("specific_heat_load_wm2", "Referenz-Heizlast [W/m2]"),
                   ("reference_room_temp", "Referenz innen [C]"), ("reference_outdoor_temp", "Referenz aussen [C]"),
                   ("status", "Status")]
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow([label for _, label in columns])
            writer.writerows([[row.get(key) for key, _ in columns] for row in rows])

    def _export_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Heizkurve exportieren", "Heizkurve.csv", "CSV (*.csv)")
        if path:
            try:
                self.export_csv(path)
            except OSError as error:
                QMessageBox.warning(self, "CSV-Export", str(error))

    def closeEvent(self, event):
        if not self.save_settings():
            event.ignore()
            return
        self.cancel_analysis()
        super().closeEvent(event)