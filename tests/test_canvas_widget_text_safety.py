from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_to_dict_ignores_text_without_position(app):
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas._text_annotations["TEXT-1"] = None
        canvas._text_contents["TEXT-1"] = "Test"
        canvas._text_visible["TEXT-1"] = True

        data = canvas.to_dict()
        assert "TEXT-1" not in data.get("text_annotations", {})
    finally:
        canvas.deleteLater()
