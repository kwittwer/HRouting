import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui.heating_curve_analysis_window import HeatingCurveAnalysisWindow
from test_heating_curve_analysis_window import document, fill_reference, finish


def test_project_changes_invalidate_without_erasing_reference_inputs():
    app = QApplication.instance() or QApplication([])
    window = HeatingCurveAnalysisWindow()
    try:
        doc = document()
        window.set_document(doc)
        fill_reference(window)
        finish(window)
        before = window.result["HK-1"][0]["supply"]
        doc.elements["circuits"]["HK-1"].room_temp = 24
        window.invalidate()
        assert window.result is None
        assert window.inputs.item(0, 2).text() == "50"
        finish(window)
        assert window.result["HK-1"][0]["supply"] > before
    finally:
        window._changed = False
        window.close()