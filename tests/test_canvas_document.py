"""Tests für Phase A: Document als einzige Datenquelle des Canvas."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas_widget import CanvasWidget  # noqa: E402
from storage.hrp_io import load_raw  # noqa: E402
from model.document import Document  # noqa: E402

EXAMPLE = ROOT / "examples" / "minimal.hrp"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def bound(app):
    document = Document.from_dict(load_raw(EXAMPLE))
    canvas = CanvasWidget()
    canvas.set_document(document)
    yield canvas, document
    canvas.deleteLater()


def test_canvas_reads_geometry_from_document(bound):
    canvas, _document = bound
    polygon = canvas._polygons["HK-1"]
    assert [(p.x(), p.y()) for p in polygon] == [
        (100.0, 100.0),
        (500.0, 100.0),
        (500.0, 400.0),
        (100.0, 400.0),
    ]
    assert isinstance(canvas._elec_points["AP-1"], QPointF)


def test_canvas_write_lands_in_document(bound):
    canvas, document = bound
    canvas._elec_points["AP-1"] = QPointF(11.0, 22.0)
    canvas._polygons["HK-1"] = [QPointF(0, 0), QPointF(5, 0), QPointF(5, 5)]

    saved = document.to_dict()["canvas"]
    assert saved["elec_points"]["AP-1"] == [11.0, 22.0]
    assert saved["polygons"]["HK-1"] == [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0]]


def test_nested_text_fields_are_independent(bound):
    canvas, document = bound
    canvas._text_contents["TEXT-1"] = "Neuer Text"
    entry = document.to_dict()["canvas"]["text_annotations"]["TEXT-1"]
    assert entry["content"] == "Neuer Text"
    assert entry["pos"] == [300, 50]  # Position unverändert


def test_visibility_maps_write_to_params(bound):
    canvas, document = bound
    canvas._circuit_visible["HK-1"] = False
    assert document.to_dict()["params"]["circuits"]["HK-1"]["visible"] is False


def test_read_only_access_does_not_modify_document(app):
    document = Document.from_dict(load_raw(EXAMPLE))
    before = json.dumps(document.to_dict(), sort_keys=True)

    canvas = CanvasWidget()
    canvas.set_document(document)
    try:
        for cid in list(canvas._polygons.keys()):
            _ = canvas._polygons[cid]
        for pid in list(canvas._elec_points.keys()):
            _ = canvas._elec_points[pid]
        for kid in list(canvas._elec_cables.keys()):
            _ = canvas._elec_cables[kid]
        _ = dict(canvas._circuit_visible)
        _ = dict(canvas._cable_start_ap)
    finally:
        canvas.deleteLater()

    assert json.dumps(document.to_dict(), sort_keys=True) == before


def test_document_data_changed_signal_fires(bound):
    canvas, _document = bound
    seen: list[str] = []
    canvas.document_data_changed.connect(seen.append)
    canvas._elec_points["AP-1"] = QPointF(1.0, 1.0)
    assert seen == ["AP-1"]


def test_app_window_persists_canvas_edits(app, tmp_path, monkeypatch):
    """Kernfall: Eine Canvas-Änderung muss Speichern und Laden überleben."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from storage.hrp_io import load_document  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)

        window.canvas._elec_points["AP-1"] = QPointF(777.0, 888.0)
        assert window._dirty is True

        target = tmp_path / "roundtrip.hrp"
        window._project_path = target
        assert window._save_project()

        reloaded = load_document(target)
        assert reloaded.to_dict()["canvas"]["elec_points"]["AP-1"] == [777.0, 888.0]
    finally:
        window.deleteLater()


def test_app_window_preserves_view_settings(app, tmp_path, monkeypatch):
    """Globale Ansichtsdaten (Raster, Zoom) dürfen nicht verloren gehen."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415
    from storage.hrp_io import load_document  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window.canvas._grid_spacing_mm = 250.0

        target = tmp_path / "view.hrp"
        window._project_path = target
        assert window._save_project()

        reloaded = load_document(target)
        assert reloaded.to_dict()["canvas"]["grid_spacing_mm"] == 250.0
        # Grundrisse müssen erhalten bleiben
        assert reloaded.to_dict()["params"]["floorplans"]
    finally:
        window.deleteLater()
