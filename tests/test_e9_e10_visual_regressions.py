"""Regression tests for E-9/E-10 visual issues."""

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


def test_e10_ap_color_uses_element_color(app):
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        ok = window.open_project_file(Path("examples/Planung_Linda.hrp"))
        assert ok

        aps = window._document.elements.get("elec_points", {})
        assert aps, "No electrical points in fixture project"

        ap_id, ap = next(iter(aps.items()))
        window.canvas._ensure_color(ap_id)
        canvas_color = window.canvas._color_map.get(ap_id)

        assert canvas_color is not None
        assert canvas_color.name().lower() == str(ap.color).lower()
    finally:
        window.deleteLater()


def test_e9_legacy_icon_path_resolves_to_existing_file(app):
    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        ok = window.open_project_file(Path("examples/Planung_Linda.hrp"))
        assert ok

        aps = window._document.elements.get("elec_points", {})
        assert aps, "No electrical points in fixture project"

        ap_id, ap = next(iter(aps.items()))
        assert ap.icon_path, "AP has no icon path"

        resolved = window.canvas._resolve_icon_path(ap.icon_path)
        assert Path(resolved).exists(), f"Resolved icon does not exist: {resolved}"

        window.canvas.set_elec_point_icon(ap_id, ap.icon_path)
        icon = window.canvas._elec_point_icons.get(ap_id)
        svg_renderer = window.canvas._elec_point_svgs.get(ap_id)
        assert (icon is not None and not icon.isNull()) or (
            svg_renderer is not None and svg_renderer.isValid()
        ), "Neither pixmap nor SVG renderer loaded for AP icon"
    finally:
        window.deleteLater()
