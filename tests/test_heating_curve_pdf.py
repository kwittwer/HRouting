import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from gui.app_window import AppWindow
from gui.pdf_export_dialog import PdfExportConfigDialog
from test_heating_curve_analysis_window import document
from logic.heating_curve_analysis import default_config


def curve_document():
    doc = document()
    for key, circuit in doc.elements["circuits"].items():
        circuit.data["visible"] = True
        circuit.data["circuit_id"] = key
    settings = default_config()
    settings["circuits"] = {"HK-1": {"specific_heat_load_wm2": 50, "reference_room_temp": 20, "reference_outdoor_temp": -13},
                            "HK-2": {"specific_heat_load_wm2": 80, "reference_room_temp": 20, "reference_outdoor_temp": -13}}
    doc.settings["heating_curve_analysis"] = settings
    return doc


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kwargs: default)
    monkeypatch.setattr(QSettings, "setValue", lambda *args: None)
    monkeypatch.setattr(AppWindow, "_auto_load_last_project", lambda self: None)
    result = AppWindow()
    yield result
    result._dirty = False
    result.close()


def test_curve_page_normalization_and_dialog_selection(window):
    page = {"id": "curve-1", "type": "heating_curve", "title": "Heizkurven", "circuit_ids": ["HK-1", "HK-1", "HK-2"]}
    normalized = window._normalize_pdf_export_pages([page])
    assert normalized[0]["circuit_ids"] == ["HK-1", "HK-2"]
    assert window._normalize_pdf_export_pages([{**page, "circuit_ids": []}])[0]["circuit_ids"] == []
    dialog = PdfExportConfigDialog(normalized, [], (1000, 1000), heating_circuits=[("HK-1", "Bad"), ("HK-2", "Wohnen")])
    try:
        dialog._on_add_heating_curve_page()
        assert dialog._current_page()["type"] == "heating_curve"
        assert dialog._current_page()["circuit_ids"] == ["HK-1", "HK-2"]
        assert not dialog.circuit_group.isHidden()
        assert dialog.plan_group.isHidden()
        assert dialog.table_group.isHidden()
        dialog._circuit_checks["HK-2"].setChecked(False)
        assert dialog._current_page()["circuit_ids"] == ["HK-1"]
    finally:
        dialog.close()


def test_page_schema_and_project_roundtrip(window):
    import json
    from pathlib import Path
    from model.document import Document
    from validate_hrp import validate_schema

    doc = curve_document()
    doc.pdf_export_pages = window._normalize_pdf_export_pages([
        {"id": "curves", "type": "heating_curve", "title": "Heizkurven", "circuit_ids": ["HK-2"]},
    ])
    raw = doc.to_dict()
    schema = json.loads((Path(__file__).resolve().parents[1] / "hrp_schema.json").read_text(encoding="utf-8"))
    assert not validate_schema(raw, schema)
    assert Document.from_dict(raw).to_dict()["pdf_export_pages"] == raw["pdf_export_pages"]


def test_payload_selection_missing_inputs_and_colors(window):
    from gui.pdf_heating_curve import prepare_heating_curve_pages
    from gui.heating_curve_analysis_window import heating_curve_color

    doc = curve_document()
    del doc.settings["heating_curve_analysis"]["circuits"]["HK-1"]
    pages = [
        {"id": "one", "type": "heating_curve", "circuit_ids": ["HK-2"]},
        {"id": "all", "type": "heating_curve", "circuit_ids": ["HK-1", "HK-2", "HK-999"]},
        {"id": "empty", "type": "heating_curve", "circuit_ids": []},
    ]
    before = doc.to_dict()
    data = prepare_heating_curve_pages(doc, pages)
    assert data["one"]["series"][0][1] == heating_curve_color(1)
    assert len(data["all"]["series"]) == 2
    assert "Referenzwerte fehlen" in data["all"]["entries"][0][0]
    assert "nicht vorhanden" in data["all"]["entries"][2][0]
    assert data["empty"]["series"] == []
    assert "Keine Heizkreise" in data["empty"]["note"]
    assert doc.to_dict() == before
    doc.settings.update(t_supply=30, t_return=30)
    assert "Projektspreizung" in prepare_heating_curve_pages(doc, pages)["one"]["note"]


def test_export_blocks_invalid_live_editor_before_creating_pdf(window, monkeypatch, tmp_path):
    monkeypatch.setattr(window, "_flush_live_editor_state", lambda: False)
    path = tmp_path / "invalid.pdf"
    result = window._continue_export_pdf([{"id": "curve", "type": "heating_curve"}], {},
                                          output_path=str(path), show_progress=False)
    assert result["code"] == "INVALID_EDITOR_INPUT"
    assert not path.exists()


def test_visibility_restore_preserves_bound_maps(window):
    window._set_document(curve_document())
    window._document.elements["circuits"]["HK-1"].data["visible"] = False
    binding = window.canvas._circuit_visible
    saved = window._save_all_visibility()
    window._apply_page_visibility({"type": "plan"})
    assert window._document.elements["circuits"]["HK-1"].data["visible"] is True
    window._restore_all_visibility(saved)
    assert window.canvas._circuit_visible is binding
    assert window._document.elements["circuits"]["HK-1"].data["visible"] is False


def test_curve_pdf_without_analysis_window_is_vector_and_selected(window, tmp_path):
    fitz = pytest.importorskip("fitz")
    from PySide6.QtGui import QFontDatabase

    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arialbd.ttf")
    doc = curve_document()
    doc.elements["circuits"]["HK-1"].data["visible"] = False
    window._set_document(doc)
    assert window._heating_curve_window is None
    window._sync_canvas_to_document()
    before = doc.to_dict()
    binding = window.canvas._circuit_visible
    page = {"id": "curves", "type": "heating_curve", "title": "Heizkurven Test", "enabled": True, "circuit_ids": ["HK-1"]}
    path = tmp_path / "curves.pdf"
    result = window._continue_export_pdf([page], {}, output_path=str(path), show_progress=False)
    assert result["status"] == "ok"
    with fitz.open(path) as pdf:
        assert len(pdf) == 2
        text = pdf[1].get_text()
        assert "HK-1" in text
        assert "HK-2" not in text
        assert "Heizkurven Test" in text
        assert "Projektspreizung" in text
        assert "Notwendiger Vorlauf" in text
        assert pdf[1].get_drawings()
        assert not pdf[1].get_images()
    after = doc.to_dict()
    assert after == before, {key: (before["params"].get(key), after["params"].get(key))
                             for key in before["params"].keys() | after["params"].keys()
                             if before["params"].get(key) != after["params"].get(key)}
    assert window._heating_curve_window is None
    assert window.canvas._circuit_visible is binding
    assert doc.elements["circuits"]["HK-1"].data["visible"] is False


def test_many_curves_and_long_names_have_readable_paginated_legend(window, tmp_path):
    from copy import deepcopy
    from model.document import Document
    from PySide6.QtGui import QFontDatabase
    fitz = pytest.importorskip("fitz")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arialbd.ttf")
    raw = curve_document().to_dict()
    template = raw["params"]["circuits"]["HK-1"]
    shape = raw["canvas"]["polygons"]["HK-1"]
    references = raw["params"]["heating_curve_analysis"]["circuits"]["HK-1"]
    ids = []
    for index in range(1, 31):
        key = f"HK-{index}"
        ids.append(key)
        raw["params"]["circuits"][key] = {**deepcopy(template), "circuit_id": key, "name": f"Raumname{index:02} " + "Langer Raumname " * 6}
        raw["canvas"]["polygons"][key] = deepcopy(shape)
        raw["params"]["heating_curve_analysis"]["circuits"][key] = deepcopy(references)
    raw["params"]["circuits"]["HK-1"]["name"] = "Sehr langer Raumname " * 100 + "NAMENSENDE"
    window._set_document(Document.from_dict(raw))
    path = tmp_path / "many_curves.pdf"
    page = {"id": "many", "type": "heating_curve", "title": "Viele Heizkurven", "circuit_ids": ids}
    result = window._continue_export_pdf([page], {}, output_path=str(path), show_progress=False)
    assert result["status"] == "ok"
    with fitz.open(path) as pdf:
        assert len(pdf) > 2
        assert window._pdf_export_meta["page_count"] == str(len(pdf))
        text = "\n".join(page.get_text() for page in pdf)
        for index in range(1, 31):
            if index != 1:
                assert f"Raumname{index:02}" in text
        assert "NAMENSENDE" in text
        for index, page in enumerate(list(pdf)[1:], 2):
            assert f"Seite {index}" in page.get_text()
            assert page.get_drawings()
            assert not page.get_images()