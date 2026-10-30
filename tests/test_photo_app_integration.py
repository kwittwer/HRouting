import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from gui.app_window import AppWindow
from model.document import Document
from model.photo_documentation import default_config


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kwargs: default)
    monkeypatch.setattr(QSettings, "setValue", lambda *args: None)
    instance = AppWindow()
    instance._project_path = tmp_path / "project.hrp"
    yield instance
    instance._dirty = False
    if instance._photo_documentation_window is not None:
        instance._photo_documentation_window.cancel_import()
        instance._photo_documentation_window.close()
    instance.deleteLater()
    app.processEvents()


def test_photo_window_commit_undo_redo_and_rebind(window):
    window._open_photo_documentation_window()
    editor = window._photo_documentation_window
    assert editor is not None
    assert "photo_documentation" not in window._document.settings
    config = default_config()
    window._save_photo_documentation(config)
    assert window._dirty
    assert window._document.settings["photo_documentation"] == config
    config["rooms"]["ER-1"] = {"ffb_offset_mm": 100}
    window._save_photo_documentation(config)
    window._undo()
    assert window._document.settings["photo_documentation"]["rooms"] == {}
    window._redo()
    assert editor.config["rooms"]["ER-1"]["ffb_offset_mm"] == 100
    replacement = Document()
    window._set_document(replacement)
    assert editor.config["photos"] == {}


def test_photo_noop_and_save_as_failure_preserve_state(window, monkeypatch, tmp_path):
    config = default_config()
    window._save_photo_documentation(config)
    count = len(window._undo_stack)
    window._save_photo_documentation(deepcopy(config))
    assert len(window._undo_stack) == count
    previous = window._project_path
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "other.hrp"), ""))
    monkeypatch.setattr(window, "_save_project", lambda: False)
    assert window._save_project_as() is False
    assert window._project_path == previous


def test_documentation_export_registered_saved_and_detectable(window, tmp_path):
    import fitz
    import numpy as np
    from logic.photo_markers import detect_markers
    from storage.hrp_io import load_document

    window._set_document(Document.from_dict({
        "canvas": {"elec_points": {"AP-1": [10, 10]}},
        "params": {"elec_points": {"AP-1": {"name": "Socket"}}},
    }))
    page = {"id": "stickers", "type": "ap_stickers", "enabled": True,
            "documentation_mode": True, "include_references": False}
    normalized = window._normalize_pdf_export_pages([page])
    assert normalized[0]["documentation_mode"] is True
    output = tmp_path / "markers.pdf"
    result = window._continue_export_pdf(normalized, {}, output_path=str(output), show_progress=False)
    assert result["status"] == "ok"
    saved = load_document(window._project_path).settings["photo_documentation"]
    assert saved["markers"]["MARK-1"]["issued"]
    pdf = fitz.open(output)
    assert len(pdf) == 2
    bitmap = pdf[1].get_pixmap(matrix=fitz.Matrix(2, 2))
    image = np.frombuffer(bitmap.samples, np.uint8).reshape(bitmap.height, bitmap.width, 3)[:, :, ::-1].copy()
    assert detect_markers(image, saved)[0]["marker_id"] == "MARK-1"
    window._undo()
    retained = window._document.settings["photo_documentation"]
    assert retained["markers"]["MARK-1"]["issued"]
    assert retained["dictionary"] == saved["dictionary"]


def test_save_as_cancels_import_before_project_path_changes(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    window._open_photo_documentation_window()
    editor = window._photo_documentation_window
    previous_path = window._project_path
    cancelled_at = []
    monkeypatch.setattr(editor, "cancel_import", lambda: cancelled_at.append(window._project_path))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "other.hrp"), ""))
    monkeypatch.setattr(window, "_save_project", lambda: True)
    assert window._save_project_as()
    assert cancelled_at == [previous_path]


def test_real_png_marker_import_measure_save_reload(window, tmp_path):
    import json
    from pathlib import Path
    import numpy as np
    from PIL import Image
    from gui.photo_documentation_window import _PhotoJob
    from logic.photo_markers import ensure_markers, marker_image
    from model.photo_documentation import ensure_surfaces, validate_config
    from storage.hrp_io import load_document
    from validate_hrp import validate_schema, validate_semantic
    import threading

    document = Document.from_dict({
        "canvas": {"elec_rooms": {"ER-1": [[0, 0], [3000, 0], [3000, 2500], [0, 2500]]},
                   "elec_points": {"AP-1": [1000, 0]}},
        "params": {"elec_rooms": {"ER-1": {"room_id": "ER-1", "name": "Room"}},
               "elec_points": {"AP-1": {"point_id": "AP-1", "name": "Socket", "room_id": "ER-1", "position": "Wand"}}},
        "pdf_export_pages": [{"id": "plan", "type": "plan", "title": "Plan"}],
    })
    window._set_document(document)
    config = ensure_surfaces(document, default_config())
    config["assignments"]["AP-1"] = "SURF-1"
    ensure_markers(document, config)
    config["markers"]["MARK-1"]["reference"] = {"origin_mm": [500, 300], "rotation_deg": 0}
    window._save_photo_documentation(config)
    assert window._save_project()
    window._open_photo_documentation_window()
    editor = window._photo_documentation_window
    image = np.full((800, 1000), 255, np.uint8)
    image[200:600, 300:700] = marker_image(config, "MARK-1", 400)
    source = tmp_path / "construction.png"
    Image.fromarray(image).save(source)
    completed = []
    job = _PhotoJob(editor._generation, [str(source)], window._project_path, config, threading.Event())
    job.signals.finished.connect(lambda generation, payload: completed.append((generation, payload)))
    job.run()
    generation, payload = completed[0]
    assert payload["errors"] == []
    assert len(payload["items"][0]["analysis"]["regions"]) == 1
    editor._job_finished(generation, payload)
    assert editor.config["photos"]["PHOTO-1"]["detections"][0]["point_id"] == "AP-1"
    assert editor.set_region_mask([[50, 50], [950, 50], [950, 750], [50, 750]])
    measurement_id = editor.create_measurement([[350, 350], [600, 350]])
    value = editor.config["measurements"][measurement_id]["value_mm"]
    assert value == pytest.approx(50, abs=1)
    assert validate_config(editor.config) == []
    assert window._save_project()
    reloaded = load_document(window._project_path)
    saved = reloaded.settings["photo_documentation"]
    assert saved["measurements"][measurement_id]["value_mm"] == value
    assert not saved["photos"]["PHOTO-1"]["path"].startswith("data:")
    schema = json.loads((Path(__file__).resolve().parents[1] / "hrp_schema.json").read_text(encoding="utf-8"))
    assert validate_schema(reloaded.to_dict(), schema) == []
    assert validate_semantic(reloaded.to_dict())[0] == []
    document.get("ER-1").polygon = [[0, 0], [3100, 0], [3100, 2500], [0, 2500]]
    assert "veraltet" in editor.region_error()