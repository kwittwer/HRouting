from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QColorDialog  # noqa: E402

from gui.color_dialog_state import capture_custom_colors  # noqa: E402


def test_capture_custom_colors_accepts_qcolor_values(monkeypatch):
    monkeypatch.setattr(QColorDialog, "customCount", staticmethod(lambda: 2))

    values = [QColor("#ff0000"), QColor("#00ff00")]
    monkeypatch.setattr(QColorDialog, "customColor", staticmethod(lambda idx: values[idx]))

    assert capture_custom_colors() == ["#ff0000", "#00ff00"]


def test_capture_custom_colors_accepts_rgba_int_values(monkeypatch):
    monkeypatch.setattr(QColorDialog, "customCount", staticmethod(lambda: 2))

    values = [QColor("#123456").rgba(), QColor("#abcdef").rgba()]
    monkeypatch.setattr(QColorDialog, "customColor", staticmethod(lambda idx: values[idx]))

    assert capture_custom_colors() == ["#123456", "#abcdef"]