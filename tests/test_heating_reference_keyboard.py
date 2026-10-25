import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from gui.heating_curve_analysis_window import HeatingCurveAnalysisWindow
from test_heating_curve_analysis_window import document


def test_three_reference_fields_accept_keyboard_and_calculate():
    app = QApplication.instance() or QApplication([])
    window = HeatingCurveAnalysisWindow()
    try:
        window.set_document(document())
        window.show()
        app.processEvents()
        for column, text in ((2, "50"), (3, "20"), (4, "-13")):
            item = window.inputs.item(0, column)
            window.inputs.scrollToItem(item)
            window.inputs.setCurrentItem(item)
            window.inputs.setFocus()
            app.processEvents()
            QTest.keyClick(window.inputs, Qt.Key_F2)
            editor = app.focusWidget()
            assert isinstance(editor, QLineEdit)
            QTest.keyClicks(editor, text)
            QTest.keyClick(editor, Qt.Key_Return)
            app.processEvents()
            assert item.text() == text
        QTest.mouseClick(window.compute, Qt.LeftButton)
        while window._job is not None:
            window._advance()
        assert window.result["HK-1"][0]["supply"] is not None
    finally:
        window._changed = False
        window.close()


def test_save_commits_active_cell_without_enter():
    app = QApplication.instance() or QApplication([])
    window = HeatingCurveAnalysisWindow()
    saved = []
    window.config_changed.connect(lambda config, _: saved.append(config))
    try:
        window.set_document(document())
        window.show()
        app.processEvents()
        window.inputs.setCurrentCell(0, 2)
        window.inputs.setFocus()
        QTest.keyClick(window.inputs, Qt.Key_F2)
        editor = app.focusWidget()
        assert isinstance(editor, QLineEdit)
        QTest.keyClicks(editor, "99")
        assert window.inputs.item(0, 2).text() == ""
        assert window.save_settings()
        assert saved[-1]["circuits"]["HK-1"]["specific_heat_load_wm2"] == 99
        assert window.inputs.item(0, 2).text() == "99"
    finally:
        window._changed = False
        window.close()