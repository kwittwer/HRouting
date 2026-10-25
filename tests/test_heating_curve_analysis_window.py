import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTabWidget

from gui.heating_curve_analysis_window import HeatingCurveAnalysisWindow
from model.document import Document


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def document():
    return Document.from_dict({"canvas": {
        "floor_plans": [{"fp_id": "grundriss-1", "mm_per_px": 100}],
        "polygons": {"HK-1": [[0, 0], [40, 0], [40, 30], [0, 30]],
                     "HK-2": [[0, 0], [40, 0], [40, 30], [0, 30]]}},
        "params": {"t_supply": 35, "t_return": 30, "t_norm_outdoor": -12,
                   "floorplans": {"grundriss-1": {"name": "EG"}},
                   "circuits": {key: {"name": key, "floor_plan_id": "grundriss-1", "room_temp": room,
                                        "spacing": 150, "floor_covering": "Fliesen / Keramik"}
                                for key, room in (("HK-1", 20), ("HK-2", 24))}}})


def fill_reference(window, row=0, load="50"):
    for column, value in ((2, load), (3, "20"), (4, "-13")):
        window.inputs.item(row, column).setText(value)


def finish(window):
    window.start_analysis()
    while window._job is not None:
        window._advance()


def test_simple_inputs_calculate_select_and_csv(app, tmp_path):
    window = HeatingCurveAnalysisWindow()
    try:
        doc = document()
        before = doc.to_dict()
        window.set_document(doc)
        assert window.inputs.columnCount() == 5
        assert not window.findChildren(QTabWidget)
        for column in (2, 3, 4):
            assert window.inputs.item(0, column).flags() & Qt.ItemIsEditable
        assert not window.inputs.item(0, 0).flags() & Qt.ItemIsEditable
        fill_reference(window)
        fill_reference(window, 1, "80")
        finish(window)
        assert len(window.result["HK-1"]) == 28
        assert window.result["HK-2"][0]["supply"] > window.result["HK-1"][0]["supply"]
        assert window.curve_plot.series[0][2][0][1] == window.result["HK-1"][0]["supply"]
        result = window.result
        window.circuit_choice.setCurrentIndex(1)
        assert window.result is result
        assert window.curve_plot.series[0][2][0][1] == window.result["HK-2"][0]["supply"]
        assert "Soll innen 24 C" in window.curve_status.text()
        path = tmp_path / "selected.csv"
        window.export_csv(path)
        text = path.read_text(encoding="utf-8-sig")
        assert "HK-2" in text and "HK-1" not in text
        assert "Notwendiger Vorlauf" in text
        assert doc.to_dict() == before
        window.resize(760, 720)
        window.show()
        app.processEvents()
        assert window.width() <= 760
        window.set_document(Document())
        assert window.result is None
        assert window.inputs.rowCount() == 0
    finally:
        window._changed = False
        window.close()


def test_unknown_other_circuit_does_not_block_and_project_spread_used(app):
    window = HeatingCurveAnalysisWindow()
    try:
        doc = document()
        doc.settings.update(t_supply=40, t_return=33)
        window.set_document(doc)
        fill_reference(window)
        finish(window)
        assert window.result["HK-1"][0]["spread"] == 7
        assert window.result["HK-1"][0]["supply"] is not None
        window.circuit_choice.setCurrentIndex(1)
        assert "Referenzwerte fehlen" in window.curve_status.text()
        assert all(value is None for _, value in window.curve_plot.series[0][2])
        window.inputs.item(1, 2).setText("invalid")
        finish(window)
        assert window.result["HK-1"][0]["supply"] is not None
        assert "ungueltig" in window.result["HK-2"][0]["status"]
        doc.settings["t_return"] = 40
        window.invalidate()
        finish(window)
        assert window.result is None
        assert "Projektspreizung" in window.statusBar().currentMessage()
    finally:
        window._changed = False
        window.close()


def test_cancel_invalid_input_close_and_no_default_writes(app):
    window = HeatingCurveAnalysisWindow()
    saved = []
    window.config_changed.connect(lambda *args: saved.append(args))
    window.set_document(document())
    assert window.save_settings()
    assert not saved
    fill_reference(window)
    window.start_analysis()
    window.cancel_analysis()
    assert window._job is None
    window.inputs.item(0, 2).setText("invalid")
    assert not window.save_settings()
    window.show()
    assert not window.close()
    window._changed = False
    window.close()


def test_disk_save_undo_reload_and_legacy_preservation(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from gui.app_window import AppWindow
    from storage.hrp_io import load_document

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kwargs: default)
    monkeypatch.setattr(QSettings, "setValue", lambda *args: None)
    monkeypatch.setattr(AppWindow, "_auto_load_last_project", lambda self: None)
    window = AppWindow()
    try:
        doc = document()
        from logic.heating_curve_analysis import default_config
        config = default_config()
        config["circuits"]["HK-1"] = {"heat_load_w": 600, "reference_area_m2": 100, "max_flow": 1}
        doc.settings["heating_curve_analysis"] = config
        window._set_document(doc)
        window._dirty = False
        window._open_heating_curve_window()
        analysis = window._heating_curve_window
        assert not window._dirty
        fill_reference(analysis)
        window._project_path = tmp_path / "curves.hrp"
        assert window._save_project()
        loaded = load_document(window._project_path)
        settings = loaded.settings["heating_curve_analysis"]["circuits"]["HK-1"]
        assert settings["specific_heat_load_wm2"] == 50
        assert settings["heat_load_w"] == 600
        assert settings["reference_area_m2"] == 100
        finish(analysis)
        assert analysis.result["HK-1"][0]["q_needed"] == pytest.approx(50 * 32 / 33)
        analysis.inputs.item(0, 2).setText("60")
        window._undo()
        assert analysis.inputs.item(0, 2).text() == "50.0"
        window._redo()
        assert analysis.inputs.item(0, 2).text() == "60.0"
        window._set_document(loaded)
        assert analysis.inputs.item(0, 2).text() == "50.0"
        assert loaded.settings["t_norm_outdoor"] == -12
        analysis.inputs.item(0, 2).setText("75")
        analysis.inputs.item(1, 2).setText("bad")
        window._emit_structure_changed()
        assert analysis.inputs.item(0, 2).text() == "75"
        assert analysis.inputs.item(1, 2).text() == "bad"
    finally:
        window._heating_curve_window._changed = False
        window._dirty = False
        window.close()


def test_plot_keeps_whole_outdoor_range_with_unreachable_points(app):
    from gui.heating_curve_analysis_window import PlotView

    plot = PlotView()
    plot.set_series([("HK-1", "#c84242", [(-20, None), (0, 30), (10, 25), (15, None)])])
    labels = [item.toPlainText() for item in plot.scene().items() if hasattr(item, "toPlainText")]
    assert "-20.0" in labels
    assert "15.0" in labels
    plot.close()


def test_all_circuits_colors_legend_selection_and_export(app, tmp_path):
    window = HeatingCurveAnalysisWindow()
    try:
        window.set_document(document())
        fill_reference(window)
        fill_reference(window, 1, "80")
        finish(window)
        first_color = window.curve_plot.series[0][1]
        result = window.result
        window.circuit_choice.setCurrentIndex(window.circuit_choice.findData("__all__"))
        assert window.result is result
        assert len(window.curve_plot.series) == 2
        assert len({color for _, color, _ in window.curve_plot.series}) == 2
        assert window.curve_plot.series[0][1] == first_color
        assert "2/2 mit Vorlaufkurve" in window.curve_status.text()
        labels = [item for item in window.curve_plot.scene().items() if hasattr(item, "toPlainText") and item.toolTip()]
        assert len(labels) == 2
        assert not labels[0].sceneBoundingRect().intersects(labels[1].sceneBoundingRect())
        path = tmp_path / "all.csv"
        window.export_csv(path)
        text = path.read_text(encoding="utf-8-sig")
        assert "HK-1" in text and "HK-2" in text
        window.circuit_choice.setCurrentIndex(1)
        assert len(window.curve_plot.series) == 1
        assert window.curve_plot.sceneRect().height() == 350
        window.set_document(document())
        fill_reference(window)
        finish(window)
        window.circuit_choice.setCurrentIndex(window.circuit_choice.findData("__all__"))
        assert "1/2 mit Vorlaufkurve" in window.curve_status.text()
        assert "HK-2" in window.curve_status.text()
    finally:
        window._changed = False
        window.close()