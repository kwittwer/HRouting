from copy import deepcopy
import json
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from model.document import Document
from model.elements import ElecPoint
from model.photo_documentation import default_config, validate_config
from logic.photo_markers import (
    add_reference_marker, detect_markers, ensure_markers, marker_image, read_photo,
)


@pytest.fixture
def registered():
    cv2 = pytest.importorskip("cv2")
    if not hasattr(cv2, "aruco") or not hasattr(cv2.aruco, "extendDictionary"):
        pytest.skip("OpenCV >=4.10 with ArUco is not installed yet")
    document = Document()
    document.add(ElecPoint("AP-947"))
    config = default_config()
    config["project_uuid"] = "00000000-0000-4000-8000-000000000001"
    ensure_markers(document, config)
    return document, config


def test_registry_reprint_roundtrip_and_deleted_ap_retention(registered):
    document, config = registered
    original = marker_image(config, "MARK-1")
    saved = json.loads(json.dumps(config))
    assert np.array_equal(original, marker_image(saved, "MARK-1"))
    assert config["markers"]["MARK-1"]["aruco_id"] == 0
    assert config["markers"]["MARK-1"]["point_id"] == "AP-947"
    assert "reference" not in config["markers"]["MARK-1"]
    ensure_markers(document, config)
    assert config == saved
    document.remove("AP-947")
    document.add(ElecPoint("AP-948"))
    ensure_markers(document, config)
    assert config["markers"]["MARK-1"] == saved["markers"]["MARK-1"]
    assert config["markers"]["MARK-2"]["aruco_id"] == 1
    assert validate_config(config) == []


def test_dictionary_extension_preserves_all_original_bytes(registered):
    document, config = registered
    saved = deepcopy(config["dictionary"]["bytes_list"])
    original = marker_image(config, "MARK-1")
    for number in range(1, 66):
        document.add(ElecPoint(f"AP-{number}"))
    ensure_markers(document, config)
    assert config["dictionary"]["bytes_list"][:len(saved)] == saved
    assert len(config["dictionary"]["bytes_list"]) >= 66
    assert np.array_equal(original, marker_image(config, "MARK-1"))


def test_new_physical_size_appends_registration(registered):
    document, config = registered
    original = deepcopy(config["markers"]["MARK-1"])
    ensure_markers(document, config, ["AP-947"], side_mm=100)
    assert config["markers"]["MARK-1"] == original
    assert config["markers"]["MARK-2"]["side_mm"] == 100


def test_no_dictionary_is_generated_on_empty_demand():
    config = default_config()
    ensure_markers(Document(), config)
    assert config["dictionary"] == {}


def test_perspective_marker_detection(registered):
    import cv2

    _document, config = registered
    marker = marker_image(config, "MARK-1", pixels=280)
    target = np.float32([[150, 100], [460, 140], [430, 420], [100, 370]])
    transform = cv2.getPerspectiveTransform(np.float32([[0, 0], [279, 0], [279, 279], [0, 279]]), target)
    photo = cv2.warpPerspective(marker, transform, (650, 550), borderValue=255)
    detections = detect_markers(photo, config)
    assert len(detections) == 1
    assert detections[0]["marker_id"] == "MARK-1"
    assert detections[0]["warnings"] == []
    assert np.max(np.abs(np.asarray(detections[0]["corners"]) - target)) < 3


def test_duplicate_ids_are_not_silently_trusted(registered):
    _document, config = registered
    photo = np.full((400, 800), 255, np.uint8)
    photo[100:300, 100:300] = marker_image(config, "MARK-1", 200)
    photo[100:300, 500:700] = marker_image(config, "MARK-1", 200)
    detections = detect_markers(photo, config)
    assert len(detections) == 2
    assert all("duplicate_marker_id" in item["warnings"] for item in detections)


def test_foreign_project_dictionary_does_not_resolve(registered):
    document, config = registered
    foreign = default_config()
    foreign["project_uuid"] = "00000000-0000-4000-8000-000000000002"
    ensure_markers(document, foreign)
    photo = np.pad(marker_image(foreign, "MARK-1", 200), 60, constant_values=255)
    assert detect_markers(photo, config) == []


def test_unregistered_dictionary_code_has_no_ap_identity(registered):
    import cv2
    from logic.photo_markers import _dictionary

    _document, config = registered
    photo = np.pad(cv2.aruco.generateImageMarker(_dictionary(config), 12, 200), 40, constant_values=255)
    detections = detect_markers(photo, config)
    assert len(detections) == 1
    assert detections[0]["marker_id"] == ""
    assert "unregistered_marker_id" in detections[0]["warnings"]


def test_reference_marker_has_no_invented_measured_position(registered):
    _document, config = registered
    config["surfaces"]["SURF-1"] = {"status": "current"}
    marker = add_reference_marker(config, "SURF-1")
    assert marker["surface_id"] == "SURF-1"
    assert marker["side_mm"] == 120
    assert "reference" not in marker


def test_read_photo_returns_bgr_and_transposes_exif(tmp_path):
    source = tmp_path / "oriented.png"
    image = Image.new("RGB", (40, 20), (255, 0, 0))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    result = read_photo(source)
    assert result.shape == (40, 20, 3)
    assert result.dtype == np.uint8
    assert result[0, 0].tolist() == [0, 0, 255]


def test_read_photo_rejects_resource_limit_and_unknown_format(tmp_path, monkeypatch):
    from logic import photo_markers

    source = tmp_path / "sample.png"
    Image.new("RGB", (20, 20)).save(source)
    monkeypatch.setattr(photo_markers, "MAX_PHOTO_PIXELS", 100)
    with pytest.raises(ValueError, match="megapixel"):
        read_photo(source)
    gif = tmp_path / "sample.gif"
    Image.new("RGB", (2, 2)).save(gif)
    with pytest.raises(ValueError, match="JPEG and PNG"):
        read_photo(gif)


def test_core_imports_do_not_load_opencv():
    completed = subprocess.run(
        [sys.executable, "-c", "import sys; import model.photo_documentation; "
         "import logic.photo_geometry; import logic.photo_markers; assert 'cv2' not in sys.modules"],
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


def test_perspective_detection_to_metric_measurement(registered):
    import cv2
    from logic.photo_geometry import analyze_photo, measurement, transform_points

    _document, config = registered
    config["markers"]["MARK-1"]["surface_id"] = "SURF-1"
    config["surfaces"]["SURF-1"] = {"status": "current"}
    marker = marker_image(config, "MARK-1", pixels=280)
    target = np.float32([[150, 100], [460, 140], [430, 420], [100, 370]])
    matrix = cv2.getPerspectiveTransform(np.float32([[0, 0], [279, 0], [279, 279], [0, 279]]), target)
    photo = cv2.warpPerspective(marker, matrix, (650, 550), borderValue=255)
    regions = analyze_photo(photo, config, "PHOTO-1")["regions"]
    assert len(regions) == 1
    points = transform_points([[80, 140], [200, 140]], matrix)
    assert measurement(regions[0], points)["distance_mm"] == pytest.approx(80 * 120 / 279, abs=1.0)
    assert not regions[0]["absolute_reference"]


def test_rotated_marker_keeps_decoded_corner_order(registered):
    _document, config = registered
    photo = np.rot90(np.pad(marker_image(config, "MARK-1", 200), 80, constant_values=255)).copy()
    detected = detect_markers(photo, config)
    assert len(detected) == 1
    assert np.allclose(detected[0]["corners"], [[80, 279], [80, 80], [279, 80], [279, 279]], atol=2)


def test_six_bit_dictionary_generation_and_detection(registered):
    document, _config = registered
    config = default_config()
    config["project_uuid"] = "00000000-0000-4000-8000-000000000003"
    config["dictionary"] = {"marker_size": 6}
    ensure_markers(document, config)
    assert validate_config(config) == []
    photo = np.pad(marker_image(config, "MARK-1", 240), 60, constant_values=255)
    assert detect_markers(photo, config)[0]["marker_id"] == "MARK-1"