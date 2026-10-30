import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication

from gui.photo_documentation_window import PhotoDocumentationWindow
from model.document import Document
from model.elements import ElecPoint, ElecRoom
from model.photo_documentation import ensure_surfaces, get_config, validate_config
from storage.photo_assets import import_photo


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_open_and_save_do_not_mutate_document(app):
    document = Document()
    before = deepcopy(document.to_dict())
    window = PhotoDocumentationWindow()
    changes = []
    window.config_changed.connect(changes.append)
    window.set_document(document)
    assert window.save_settings()
    assert changes == []
    assert document.to_dict() == before
    assert window.windowTitle() == "Fotodokumentation"
    window.close()


def test_preview_coordinates_and_interaction_are_canonical(app, monkeypatch):
    import numpy as np
    from gui.photo_measure_view import PhotoMeasureView

    monkeypatch.setattr("gui.photo_measure_view.rectify_photo", lambda image, region:
                        (image.copy(), [[3, 0, 7], [0, 3, 9], [0, 0, 1]]))
    view = PhotoMeasureView()
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    region = {"H": [[2, 0, 5], [0, -2, 200], [0, 0, 1]]}
    view.set_photo(image, region)
    assert view.set_rectified(True)
    points = [[12.0, 25.0], [91.0, 60.0]]
    assert np.allclose(view.preview_to_canonical(view.canonical_to_preview(points)), points)
    received = []
    view.measurement_created.connect(received.append)
    view.set_mode("distance")
    view.accept_canonical_point(points[0])
    assert received == []
    view.measurement_enabled = True
    for point in points:
        view.accept_canonical_point(point)
    assert received == [points]
    assert view.set_rectified(False)
    assert view.canonical_to_preview(points) == points
    assert not image.any()


def room_document():
    document = Document()
    room = ElecRoom("ER-1", {"name": "Zimmer"})
    room.polygon = [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]
    document.add(room)
    point = ElecPoint("AP-1", {"name": "Steckdose", "position": "Wand", "room_id": room.id})
    point.pos = [1000, 20]
    document.add(point)
    ambiguous = ElecPoint("AP-2", {"name": "Ecke", "position": "Wand", "room_id": room.id})
    ambiguous.pos = [0, 0]
    document.add(ambiguous)
    return document


def analysis(config, regions=True):
    return {"width": 400, "height": 300, "detections": [], "warnings": [], "regions": [{
        "surface_id": "SURF-1", "H": [[10, 0, 0], [0, -10, 3000], [0, 0, 1]],
        "source_polygon": deepcopy(config["surfaces"]["SURF-1"]["source_polygon"]),
        "absolute_reference": True, "marker_ids": [], "warnings": ["uncalibrated_lens", "unchecked_control_distance"],
        "quality": {"status": "limited"},
    }] if regions else []}


@pytest.fixture
def photo_window(app, tmp_path):
    from PIL import Image

    document = room_document()
    window = PhotoDocumentationWindow()
    project = tmp_path / "project.hrp"
    project.touch()
    source = tmp_path / "original.jpg"
    Image.new("RGB", (400, 300), (180, 170, 140)).save(source)
    window.set_document(document, project)
    window.prepare_surfaces()
    meta = import_photo(source, project, window.config["project_uuid"])
    window.apply_analysis_results(window._generation, [{"meta": meta, "analysis": analysis(window.config)}])
    assert window._photo_id == "PHOTO-1"
    assert window._region_id == "REGION-1"
    yield window, document, source
    window.close()


def test_explicit_setup_assignment_and_reference_conversion(app, monkeypatch):
    from gui.photo_documentation_window import ReferenceDialog

    document = room_document()
    before = deepcopy(document.to_dict())
    window = PhotoDocumentationWindow()
    changes = []
    window.config_changed.connect(changes.append)
    window.set_document(document)
    assert window.config["surfaces"] == {}
    window.prepare_surfaces()
    assert len(window.config["surfaces"]) == 6
    assert window.config["assignments"] == {}
    assert window._surface_combo(suggestion="SURF-1").currentData() == ""
    window.assign_points({"AP-1": "SURF-1"})
    window.config["markers"]["MARK-1"] = {"id": "MARK-1", "point_id": "AP-1", "surface_id": "SURF-1", "side_mm": 80}
    window.set_reference("MARK-1", 75, 120, 90, False, 120, "Rohboden")
    assert "reference" not in window.config["markers"]["MARK-1"]
    window.set_reference("MARK-1", 75, 120, 90, True, 120, "Rohboden")
    reference = window.config["markers"]["MARK-1"]["reference"]
    assert reference["origin_mm"] == [750, 1080]
    assert reference["rotation_deg"] == 90
    dialog = ReferenceDialog(window, "SURF-1")
    assert dialog.measured.isChecked()
    dialog.reject()
    window.assign_points({"AP-1": "SURF-2"})
    assert window.config["markers"]["MARK-1"]["surface_id"] == "SURF-2"
    assert "reference" not in window.config["markers"]["MARK-1"]
    assert document.to_dict() == before
    changes[-1]["surfaces"].clear()
    assert window.config["surfaces"]
    window.close()


def test_import_commit_dedup_unassigned_metadata_and_rebind(photo_window):
    window, document, source = photo_window
    saved = []
    window.config_changed.connect(saved.append)
    meta = deepcopy(window.config["photos"]["PHOTO-1"])
    assert window.apply_analysis_results(window._generation, [{"meta": meta, "analysis": analysis(window.config)}]) == 0
    assert not saved
    window.edit_photo("PHOTO-1", "Wand offen", "Kabel sichtbar", "Rohbau")
    assert saved[-1]["photos"]["PHOTO-1"]["construction_state"] == "Rohbau"
    assert "Rohbau" in window.photo_list.item(0).text()
    generation = window._generation
    window.set_document(Document(), window.project_path)
    assert window.apply_analysis_results(generation, [{"meta": meta, "analysis": {}}]) == 0
    assert window.config["photos"] == {}
    assert window.view.image is None
    assert not document.settings.get("photo_documentation")


def test_no_marker_import_is_visible_and_manual_assignment_is_not_calibration(photo_window):
    window, document, source = photo_window
    meta = deepcopy(window.config["photos"]["PHOTO-1"])
    window.delete_photo()
    window.apply_analysis_results(window._generation, [{"meta": meta, "analysis": analysis(window.config, False)}])
    assert window.photo_list.count() == 1
    assert window.config["photos"][window._photo_id]["analysis_status"] == "no_markers"
    window.assign_photo(window._photo_id, "SURF-2")
    assert window.config["regions"] == {}
    assert not window.view.measurement_enabled
    assert not window.rectify_action.isEnabled()
    assert "nur dokumentiert" in window.quality_status.text()


def test_measurement_mask_persistence_control_and_delete(photo_window):
    window, document, source = photo_window
    assert not window.view.measurement_enabled
    assert "Messbereich" in window.region_error()
    assert window.set_region_mask([[0, 0], [399, 0], [399, 299], [0, 299]])
    assert window.view.measurement_enabled
    points = [[50, 100], [150, 100]]
    measurement_id = window.create_measurement(points, name="Leitung")
    record = window.config["measurements"][measurement_id]
    assert record["value_mm"] == pytest.approx(1000)
    assert record["points"] == points
    assert record["photo_sha256"] == window.config["photos"]["PHOTO-1"]["sha256"]
    assert record["calibration_H"] == window.config["regions"]["REGION-1"]["H"]
    window.rename_measurement(measurement_id, "Leitung links")
    assert "Leitung links" in window.measurement_list.item(0).text()
    assert window.set_control_distance(points, 100)
    assert window.config["regions"]["REGION-1"]["quality"]["status"] == "validated"
    assert "keine Genauigkeitsgarantie" in window.quality_status.text()
    assert not window.set_control_distance(points, 110)
    window.measurement_list.setCurrentRow(0)
    window.delete_measurement()
    assert not window.config["measurements"]
    assert source.exists()


@pytest.mark.parametrize("mode,points,expected", [
    ("horizontal", [[50, 100], [150, 150]], 1000),
    ("vertical", [[50, 100], [150, 150]], 500),
    ("floor", [[50, 100]], 2000),
    ("origin", [[50, 100]], 500),
])
def test_surface_axis_measurements(photo_window, mode, points, expected):
    window, document, source = photo_window
    window.config["regions"]["REGION-1"]["H"][1][2] = 2900
    window.set_region_mask([[0, 0], [399, 0], [399, 299], [0, 299]])
    measurement_id = window.create_measurement(points, mode)
    adjusted = 1900 if mode == "floor" else expected
    assert window.config["measurements"][measurement_id]["value_mm"] == pytest.approx(adjusted)
    window.config["regions"]["REGION-1"]["absolute_reference"] = False
    if mode in ("floor", "origin"):
        with pytest.raises(ValueError, match="absoluten"):
            window.create_measurement(points, mode)


def test_invalid_mask_and_outside_measurement_rejected(photo_window):
    window, document, source = photo_window
    assert not window.set_region_mask([[0, 0], [200, 200], [0, 200], [200, 0]])
    assert window.set_region_mask([[10, 10], [200, 10], [200, 200], [10, 200]])
    with pytest.raises(ValueError, match="Messbereich"):
        window.create_measurement([[20, 20], [250, 20]])
    assert not window.config["measurements"]


@pytest.mark.parametrize("problem", ["stale", "hash", "H", "changed", "missing", "geometry"])
def test_stale_changed_and_missing_disable_measurements(photo_window, problem):
    from storage.photo_assets import resolve_photo_path

    window, document, source = photo_window
    window.set_region_mask([[0, 0], [399, 0], [399, 299], [0, 299]])
    region = window.config["regions"]["REGION-1"]
    photo = window.config["photos"]["PHOTO-1"]
    path = resolve_photo_path(window.project_path, photo["path"])
    if problem == "stale":
        region["status"] = "stale"
    elif problem == "hash":
        region["photo_sha256"] = "0" * 64
    elif problem == "H":
        region["H"] = [[0] * 3] * 3
    elif problem == "geometry":
        document.get("ER-1").polygon[1][0] += 100
    elif problem == "missing":
        photo["path"] = photo["path"].replace(".jpg", "-missing.jpg")
    else:
        path.write_bytes(b"not an image")
    window._refresh()
    assert not window.view.measurement_enabled
    assert not window.rectify_action.isEnabled()
    with pytest.raises(ValueError):
        window.create_measurement([[20, 20], [100, 100]])


def test_reference_and_assignment_changes_stale_existing_regions(photo_window):
    window, document, source = photo_window
    window.assign_points({"AP-1": "SURF-1"})
    window.config["markers"]["MARK-1"] = {"id": "MARK-1", "point_id": "AP-1", "surface_id": "SURF-1", "side_mm": 80}
    window.config["regions"]["REGION-1"]["status"] = "current"
    window.set_reference("MARK-1", 100, 150, 0, True)
    assert window.config["regions"]["REGION-1"]["status"] == "stale"
    assert not window.view.measurement_enabled
    window.config["regions"]["REGION-1"]["status"] = "current"
    window.correct_marker_surfaces({"MARK-1": "SURF-2"})
    assert window.config["assignments"]["AP-1"] == "SURF-2"
    assert "reference" not in window.config["markers"]["MARK-1"]
    assert window.config["regions"]["REGION-1"]["status"] == "stale"


def test_worker_stages_metadata_and_uses_canonical_dimensions(photo_window, monkeypatch):
    from gui.photo_documentation_window import _PhotoJob
    import threading

    window, document, source = photo_window
    expected = analysis(window.config, False)
    monkeypatch.setattr("gui.photo_documentation_window.analyze_photo", lambda *args: deepcopy(expected))
    job = _PhotoJob(42, [source], window.project_path, window.config, threading.Event())
    completed = []
    job.signals.finished.connect(lambda generation, payload: completed.append((generation, payload)))
    before = deepcopy(window.config)
    job.run()
    generation, payload = completed[0]
    assert generation == 42
    assert not payload["errors"]
    assert payload["items"][0]["meta"]["width"] == 400
    assert window.config == before
    job.abort.set()
    job.run()
    assert completed[-1][1]["cancelled"]
    assert not completed[-1][1]["items"]


def test_worker_result_cannot_cross_cancel_or_document_rebind(photo_window):
    window, document, source = photo_window
    generation = window._generation
    item = {"meta": deepcopy(window.config["photos"]["PHOTO-1"]), "analysis": analysis(window.config)}
    window.cancel_import()
    window._job_finished(generation, {"items": [item], "errors": []})
    assert len(window.config["photos"]) == 1
    window.set_document(room_document(), window.project_path)
    window._job_finished(generation, {"items": [item], "errors": []})
    assert not window.config["photos"]


def test_real_queued_worker_runs_off_gui_thread(photo_window, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    import threading

    window, document, source = photo_window
    window.delete_photo()
    main_thread = threading.get_ident()
    seen = []
    result = analysis(window.config, False)

    def analyze(*args):
        seen.append(threading.get_ident())
        return result

    monkeypatch.setattr("gui.photo_documentation_window.analyze_photo", analyze)
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    window.config_changed.connect(loop.quit)
    assert window.import_files([str(source)])
    timer.start(8000)
    loop.exec()
    timer.stop()
    assert window._pool.waitForDone(8000)
    assert seen and seen[0] != main_thread
    assert len(window.config["photos"]) == 1
    assert not window._jobs


def test_reanalysis_retains_old_measurement_history(photo_window):
    window, document, source = photo_window
    window.set_region_mask([[0, 0], [399, 0], [399, 299], [0, 299]])
    measurement_id = window.create_measurement([[20, 20], [100, 100]])
    meta = deepcopy(window.config["photos"]["PHOTO-1"])
    window.apply_analysis_results(window._generation, [{"meta": meta, "photo_id": "PHOTO-1", "analysis": analysis(window.config)}])
    assert window.config["regions"]["REGION-1"]["status"] == "stale"
    assert window.config["measurements"][measurement_id]["region_id"] == "REGION-1"
    assert window.config["regions"]["REGION-2"]["status"] == "current"
    assert window._region_id == "REGION-2"


def test_identical_relink_retains_metadata_and_rejects_replacement(photo_window, tmp_path):
    from storage.photo_assets import resolve_photo_path

    window, document, source = photo_window
    window.edit_photo("PHOTO-1", "Wand", "Notiz", "Rohbau")
    photo = window.config["photos"]["PHOTO-1"]
    path = resolve_photo_path(window.project_path, photo["path"])
    path.unlink()
    window._refresh()
    assert window._asset_error
    wrong = tmp_path / "wrong.png"
    wrong.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="byte-identisches"):
        window.relink_photo("PHOTO-1", wrong)
    window.relink_photo("PHOTO-1", source)
    assert window.config["photos"]["PHOTO-1"]["note"] == "Notiz"
    assert not window._asset_error
    assert path.exists()


def test_invalid_room_geometry_disables_without_crashing(photo_window):
    window, document, source = photo_window
    document.get("ER-1").polygon = [[0, 0], [1, 1], [2, 2]]
    window._refresh()
    assert not window.view.measurement_enabled
    window.invalidate()
    assert window.config["regions"]["REGION-1"]["status"] == "stale"


def test_correction_is_one_discrete_commit(photo_window):
    window, document, source = photo_window
    window.assign_points({"AP-1": "SURF-1"})
    window.config["markers"]["MARK-1"] = {"id": "MARK-1", "point_id": "AP-1", "surface_id": "SURF-1", "side_mm": 80}
    window.save_settings()
    changes = []
    window.config_changed.connect(changes.append)
    window.correct_marker_surfaces({"MARK-1": "SURF-2"})
    assert len(changes) == 1
    assert changes[0]["regions"]["REGION-1"]["status"] == "stale"


def test_raw_reference_heights_follow_room_ffb_change(photo_window):
    window, document, source = photo_window
    for number in (1, 2):
        window.config["markers"][f"MARK-{number}"] = {
            "id": f"MARK-{number}", "surface_id": "SURF-1", "side_mm": 80,
        }
    window.set_reference("MARK-1", 100, 120, 0, True, 100, "Rohboden")
    window.set_reference("MARK-2", 200, 150, 0, True, 100, "Rohboden")
    window.set_reference("MARK-2", 200, 150, 0, True, 200, "Rohboden")
    assert window.config["markers"]["MARK-1"]["reference"]["origin_mm"][1] == 1000
    assert window.config["markers"]["MARK-2"]["reference"]["origin_mm"][1] == 1300


def test_save_preserves_rectification_and_zoom(photo_window, monkeypatch):
    import numpy as np

    window, document, source = photo_window
    monkeypatch.setattr("gui.photo_measure_view.rectify_photo", lambda image, region:
                        (image.copy(), [[1, 0, 0], [0, 1, 0], [0, 0, 1]]))
    window.set_region_mask([[0, 0], [399, 0], [399, 299], [0, 299]])
    window.rectify_action.trigger()
    window.view.scale(1.7, 1.7)
    transform = window.view.transform()
    window.create_measurement([[50, 50], [100, 50]])
    assert window.view.rectified
    assert window.rectify_action.isChecked()
    assert window.view.transform() == transform


def test_ap_anchor_is_projective_marker_center(photo_window):
    window, document, source = photo_window
    window.config["markers"]["MARK-1"] = {
        "id": "MARK-1", "point_id": "AP-1", "surface_id": "SURF-1", "side_mm": 80,
    }
    window.config["photos"]["PHOTO-1"]["detections"] = [{
        "marker_id": "MARK-1", "corners": [[90, 90], [110, 90], [110, 110], [90, 110]], "warnings": [],
    }]
    window.set_region_mask([[0, 0], [399, 0], [399, 299], [0, 299]])
    measurement_id = window.create_measurement([[50, 100]], "ap", marker_id="MARK-1")
    record = window.config["measurements"][measurement_id]
    assert record["value_mm"] == pytest.approx(500)
    assert record["points"][1] == pytest.approx([100, 100])
    assert record["name"] == "AP-Markermitte"


def test_analysis_failure_still_imports_documentation(photo_window, monkeypatch):
    import threading
    from gui.photo_documentation_window import _PhotoJob

    window, document, source = photo_window

    def fail(*args):
        raise RuntimeError("CV nicht verfuegbar")

    monkeypatch.setattr("gui.photo_documentation_window.analyze_photo", fail)
    job = _PhotoJob(1, [source], window.project_path, window.config, threading.Event())
    completed = []
    job.signals.finished.connect(lambda generation, payload: completed.append(payload))
    job.run()
    assert len(completed[0]["items"]) == 1
    assert completed[0]["items"][0]["analysis"]["warnings"] == ["analysis_failed: CV nicht verfuegbar"]


def test_identical_relink_repairs_corruption_without_erasing_history(photo_window):
    from storage.photo_assets import resolve_photo_path, verify_photo

    window, document, source = photo_window
    window.set_region_mask([[0, 0], [399, 0], [399, 299], [0, 299]])
    measurement_id = window.create_measurement([[50, 50], [100, 50]])
    photo = window.config["photos"]["PHOTO-1"]
    expected_path = photo["path"]
    path = resolve_photo_path(window.project_path, expected_path)
    path.write_bytes(b"corrupt image")
    window._refresh()
    assert not window.view.measurement_enabled
    window.relink_photo("PHOTO-1", source)
    assert verify_photo(window.project_path, photo) == path
    assert photo["path"] == expected_path
    assert window.view.measurement_enabled
    assert measurement_id in window.config["measurements"]
    backups = list(path.parent.glob(path.name + ".corrupt-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"corrupt image"


def test_invalidate_preserves_pending_config_edits(photo_window):
    window, document, source = photo_window
    window.config["photos"]["PHOTO-1"]["note"] = "Noch nicht gesendet"
    changes = []
    window.config_changed.connect(changes.append)
    window.invalidate()
    assert changes[-1]["photos"]["PHOTO-1"]["note"] == "Noch nicht gesendet"
    assert window.config["photos"]["PHOTO-1"]["note"] == "Noch nicht gesendet"


def test_reanalysis_does_not_duplicate_original(photo_window, monkeypatch):
    window, document, source = photo_window
    started = []
    monkeypatch.setattr(window, "_start_job", lambda paths, photo_id="": started.append((paths, photo_id)))
    window.reanalyze_photo()
    assert started[0][1] == "PHOTO-1"
    assert len(window.config["photos"]) == 1


def test_reference_registration_and_marker_export_emit_in_order(app, monkeypatch):
    import gui.photo_documentation_window as module

    window = PhotoDocumentationWindow()
    window.set_document(room_document())
    window.prepare_surfaces()
    events = []
    window.config_changed.connect(lambda config: events.append("commit"))
    window.markers_requested.connect(lambda: events.append("export"))

    def register(config, surface_id):
        marker = {"id": "MARK-1", "surface_id": surface_id, "side_mm": 120}
        config["markers"][marker["id"]] = marker
        return marker

    monkeypatch.setattr(module, "add_reference_marker", register)
    assert window.register_reference("SURF-1") == "MARK-1"
    assert events == ["commit"]
    assert "reference" not in window.config["markers"]["MARK-1"]
    monkeypatch.setattr(module, "ensure_markers", lambda document, config: config)
    window.request_markers()
    assert events[-1] == "export"
    window.close()


def test_toolbar_icons_and_compact_window(app):
    from PySide6.QtWidgets import QToolBar

    window = PhotoDocumentationWindow()
    window.set_document(room_document())
    window.prepare_surfaces()
    window.resize(960, 680)
    window.show()
    app.processEvents()
    assert window.width() == 960
    assert window.view.width() >= 260
    window._surface_id = "SURF-1"
    window._refresh()
    app.processEvents()
    for label, point in window.plan._labels:
        position = window.plan.mapFromScene(label.pos())
        assert position.x() >= 0
        assert position.x() + label.boundingRect().width() <= window.plan.viewport().width() + 1
    for toolbar in window.findChildren(QToolBar):
        for action in toolbar.actions():
            if not action.isSeparator():
                assert not action.icon().isNull()
                assert action.toolTip()
    assert "abgebrochen" not in window.statusBar().currentMessage()
    window.close()


def test_real_marker_import_rectification_and_metadata_validation(app, tmp_path):
    pytest.importorskip("cv2")
    import numpy as np
    from PIL import Image
    from gui.photo_documentation_window import _PhotoJob
    from logic.photo_markers import add_reference_marker, marker_image
    import threading

    document = room_document()
    window = PhotoDocumentationWindow()
    project = tmp_path / "project.hrp"
    project.touch()
    window.set_document(document, project)
    window.prepare_surfaces()
    marker = add_reference_marker(window.config, "SURF-1", side_mm=100)
    window.set_reference(marker["id"], 10, 20, measured=True)
    image = np.full((600, 800), 255, np.uint8)
    image[200:400, 200:400] = marker_image(window.config, marker["id"], 200)
    source = tmp_path / "real-marker.jpg"
    Image.fromarray(image).save(source, quality=100)
    job = _PhotoJob(window._generation, [source], project, window.config, threading.Event())
    results = []
    job.signals.finished.connect(lambda generation, payload: results.append(payload))
    job.run()
    assert not results[0]["errors"]
    assert results[0]["items"][0]["analysis"]["regions"]
    window.apply_analysis_results(window._generation, results[0]["items"])
    window.set_region_mask([[0, 0], [799, 0], [799, 599], [0, 599]])
    points = [[250, 250], [350, 250]]
    first = window.create_measurement(points)
    assert window.config["measurements"][first]["value_mm"] == pytest.approx(50, abs=1)
    assert window.view.set_rectified(True)
    mapped = window.view.preview_to_canonical(window.view.canonical_to_preview(points))
    second = window.create_measurement(mapped)
    assert window.config["measurements"][second]["value_mm"] == pytest.approx(window.config["measurements"][first]["value_mm"])
    assert validate_config(window.config) == []
    document.settings["photo_documentation"] = deepcopy(window.config)
    restored = Document.from_dict(document.to_dict())
    assert get_config(restored) == window.config
    window.close()


def test_default_reference_is_not_measured(app):
    from gui.photo_documentation_window import ReferenceDialog

    window = PhotoDocumentationWindow()
    window.set_document(room_document())
    window.prepare_surfaces()
    window.config["markers"]["MARK-1"] = {"id": "MARK-1", "surface_id": "SURF-1", "side_mm": 120}
    dialog = ReferenceDialog(window, "SURF-1")
    assert not dialog.measured.isChecked()
    dialog.accept()
    assert "reference" not in window.config["markers"]["MARK-1"]
    window.close()