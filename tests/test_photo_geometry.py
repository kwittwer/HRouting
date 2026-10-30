from copy import deepcopy

import numpy as np
import pytest

from logic.photo_geometry import (
    _marker_surface_corners, _polygon_points, analyze_photo, measurement,
    rectify_photo, transform_points,
)
from model.photo_documentation import default_config


@pytest.mark.parametrize("matrix", [None, [], np.zeros((3, 3)), np.eye(2),
                                   [[1, 0, 0], [0, float("nan"), 0], [0, 0, 1]],
                                   [[1, 0, 0], [0, 1, 0], [0, 0, 0]]])
def test_invalid_homography_is_rejected(matrix):
    with pytest.raises(ValueError):
        transform_points([[1, 2]], matrix)
    with pytest.raises(ValueError):
        measurement({"H": matrix}, [[1, 2], [3, 4]])


@pytest.mark.parametrize("polygon", [[], [[0, 0], [1, 1], [2, 2]],
                                      [[0, 0], [3, 3], [0, 3], [3, 0]],
                                      [[0, 0], [2, 0], [2, 2], [0, 0], [0, 2]],
                                      [[0, 0], [4, 0], [4, 4], [2, 0], [0, 4]]])
def test_invalid_polygons_are_rejected(polygon):
    with pytest.raises(ValueError):
        _polygon_points(polygon)


def test_transform_and_signed_measurement_use_y_up():
    region = {"H": [[2, 0, 10], [0, -2, 20], [0, 0, 1]], "absolute_reference": True}
    assert transform_points([[10, 5]], region["H"]) == [[30, 10]]
    result = measurement(region, [[10, 10], [13, 6]])
    assert result["distance_mm"] == pytest.approx(10)
    assert result["dx_mm"] == pytest.approx(6)
    assert result["dy_mm"] == pytest.approx(8)
    assert "unchecked_control_distance" in result["warnings"]


def test_horizon_rejects_points_segments_and_rectification():
    matrix = [[1, 0, 0], [0, 1, 0], [0.01, 0, -1]]
    with pytest.raises(ValueError, match="horizon"):
        transform_points([[100, 1]], matrix)
    with pytest.raises(ValueError, match="horizon"):
        measurement({"H": matrix}, [[20, 1], [200, 1]])
    with pytest.raises(ValueError, match="horizon"):
        rectify_photo(np.zeros((200, 200, 3), np.uint8), {"H": matrix})


def test_reference_center_and_rotation_are_not_top_left():
    marker = {"side_mm": 80, "reference": {"origin_mm": [500, 300], "rotation_deg": 90}}
    assert np.allclose(_marker_surface_corners(marker, True),
                       [[460, 260], [460, 340], [540, 340], [540, 260]])
    assert _marker_surface_corners(marker, False) == [[-40, 40], [40, 40], [40, -40], [-40, -40]]


def synthetic_analysis(monkeypatch, markers, detections):
    from logic import photo_markers

    config = default_config()
    config["markers"] = markers
    config["surfaces"] = {surface_id: {"id": surface_id, "status": "current"}
                          for surface_id in {marker.get("surface_id") for marker in markers.values()} if surface_id}
    monkeypatch.setattr(photo_markers, "detect_markers", lambda image, config: deepcopy(detections))
    return analyze_photo(np.full((800, 1000, 3), 255, np.uint8), config, "PHOTO-1")


def detection(marker_id, corners):
    return {"marker_id": marker_id, "aruco_id": 0, "corners": corners, "warnings": []}


def test_independent_markers_never_share_invented_coordinates(monkeypatch):
    markers = {"MARK-1": {"side_mm": 80, "surface_id": "SURF-1"},
               "MARK-2": {"side_mm": 80, "surface_id": "SURF-1"}}
    detections = [detection("MARK-1", [[100, 100], [260, 100], [260, 260], [100, 260]]),
                  detection("MARK-2", [[500, 100], [580, 100], [580, 180], [500, 180]])]
    result = synthetic_analysis(monkeypatch, markers, detections)
    region = result["regions"][0]
    assert region["calibration_marker_ids"] == ["MARK-1"]
    assert not region["absolute_reference"]
    assert region["local_origin_marker_id"] == "MARK-1"
    assert np.allclose(transform_points([[180, 180]], region["H"]), [[0, 0]])
    assert "single_marker_accuracy_not_verified" in region["warnings"]
    assert "independent_markers_not_jointly_fitted" in region["warnings"]
    assert region["quality"]["status"] == "limited"


def test_only_measured_references_are_jointly_fitted(monkeypatch):
    marker = {"side_mm": 80, "surface_id": "SURF-1",
              "reference": {"origin_mm": [100, 200], "rotation_deg": 0}}
    second = deepcopy(marker)
    second["reference"]["origin_mm"] = [300, 200]
    markers = {"MARK-1": marker, "MARK-2": second,
               "MARK-3": {"side_mm": 80, "surface_id": "SURF-1", "point_id": "AP-99"}}
    detections = [detection("MARK-1", [[120, 120], [280, 120], [280, 280], [120, 280]]),
                  detection("MARK-2", [[520, 120], [680, 120], [680, 280], [520, 280]]),
                  detection("MARK-3", [[100, 450], [200, 450], [200, 550], [100, 550]])]
    region = synthetic_analysis(monkeypatch, markers, detections)["regions"][0]
    assert region["absolute_reference"]
    assert region["calibration_marker_ids"] == ["MARK-1", "MARK-2"]
    assert np.allclose(transform_points([[200, 200], [600, 200]], region["H"]), [[100, 200], [300, 200]])
    assert measurement(region, [[200, 200], [600, 200]])["distance_mm"] == pytest.approx(200)
    assert "single_marker_accuracy_not_verified" not in region["warnings"]
    assert "unchecked_control_distance" in region["warnings"]


def test_two_surfaces_are_separated_and_unassigned_aps_still_recognized(monkeypatch):
    markers = {"MARK-1": {"side_mm": 80, "surface_id": "SURF-1"},
               "MARK-2": {"side_mm": 120, "surface_id": "SURF-2"},
               "MARK-3": {"side_mm": 80, "surface_id": "", "point_id": "AP-3"}}
    detections = [detection("MARK-1", [[100, 100], [260, 100], [260, 260], [100, 260]]),
                  detection("MARK-2", [[500, 100], [620, 120], [600, 240], [480, 220]]),
                  detection("MARK-3", [[100, 500], [200, 500], [200, 600], [100, 600]])]
    result = synthetic_analysis(monkeypatch, markers, detections)
    assert len(result["regions"]) == 2
    assert {region["surface_id"] for region in result["regions"]} == {"SURF-1", "SURF-2"}
    assert result["detections"][2]["point_id"] == "AP-3"
    assert "unassigned_surface" in result["detections"][2]["warnings"]


def test_duplicate_detection_never_calibrates(monkeypatch):
    markers = {"MARK-1": {"side_mm": 80, "surface_id": "SURF-1"}}
    detected = detection("MARK-1", [[0, 0], [100, 0], [100, 100], [0, 100]])
    detected["warnings"] = ["duplicate_marker_id"]
    assert synthetic_analysis(monkeypatch, markers, [detected])["regions"] == []


def test_rectified_preview_mapping_preserves_measurement_and_contains_photo():
    image = np.full((300, 400, 3), 255, np.uint8)
    image[90:200, 100:300] = [0, 0, 255]
    region = {"H": [[0.7, 0.05, -100], [0.03, -0.8, 200], [0.0002, 0.0001, 1]]}
    points = [[120, 110], [280, 190]]
    preview, photo_to_preview = rectify_photo(image, region, max_size=600)
    assert max(preview.shape[:2]) <= 600
    assert np.any(preview[:, :, 0] < 100)
    preview_points = transform_points(points, photo_to_preview)
    recovered = transform_points(preview_points, np.linalg.inv(photo_to_preview))
    assert np.allclose(recovered, points)
    assert measurement(region, recovered)["distance_mm"] == pytest.approx(measurement(region, points)["distance_mm"])
    corners = transform_points([[0, 0], [399, 0], [399, 299], [0, 299]], photo_to_preview)
    assert min(min(point) for point in corners) >= -1e-7
    assert max(point[0] for point in corners) <= preview.shape[1]
    assert max(point[1] for point in corners) <= preview.shape[0]


def test_measurement_warns_when_extrapolating():
    region = {"H": np.eye(3).tolist(), "reference_polygon_px": [[0, 0], [10, 0], [10, 10], [0, 10]]}
    result = measurement(region, [[5, 5], [50, 50]])
    assert "measurement_extrapolates_reference_area" in result["warnings"]


def test_projective_scaling_of_matrix_does_not_change_results():
    matrix = np.asarray([[2, 0, 40], [0, -2, 60], [0.0002, 0, 1]])
    assert np.allclose(transform_points([[5, 10]], matrix), transform_points([[5, 10]], matrix * 1e-20))


def test_conflicting_assignment_is_not_automatically_calibrated(monkeypatch):
    from logic import photo_markers

    config = default_config()
    config["markers"]["MARK-1"] = {"side_mm": 80, "surface_id": "SURF-1", "point_id": "AP-1"}
    config["assignments"]["AP-1"] = "SURF-2"
    config["surfaces"] = {"SURF-1": {"status": "current"}, "SURF-2": {"status": "current"}}
    detected = detection("MARK-1", [[100, 100], [200, 100], [200, 200], [100, 200]])
    monkeypatch.setattr(photo_markers, "detect_markers", lambda image, config: [deepcopy(detected)])
    result = analyze_photo(np.zeros((400, 400), np.uint8), config)
    assert result["regions"] == []
    assert "assignment_surface_conflict" in result["detections"][0]["warnings"]


def test_analysis_geometry_is_detached_from_config(monkeypatch):
    from logic import photo_markers

    config = default_config()
    polygon = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
    config["surfaces"]["SURF-1"] = {"status": "current", "source_polygon": polygon}
    config["markers"]["MARK-1"] = {"side_mm": 80, "surface_id": "SURF-1"}
    detected = detection("MARK-1", [[100, 100], [200, 100], [200, 200], [100, 200]])
    monkeypatch.setattr(photo_markers, "detect_markers", lambda image, config: [deepcopy(detected)])
    result = analyze_photo(np.zeros((400, 400), np.uint8), config)
    result["regions"][0]["source_polygon"][0][0] = 999
    assert polygon[0][0] == 0


def test_conflicting_measured_references_cannot_claim_good_calibration(monkeypatch):
    marker = {"side_mm": 80, "surface_id": "SURF-1",
              "reference": {"origin_mm": [0, 0], "rotation_deg": 0}}
    detections = [detection("MARK-1", [[100, 100], [260, 100], [260, 260], [100, 260]]),
                  detection("MARK-2", [[500, 100], [660, 100], [660, 260], [500, 260]])]
    result = synthetic_analysis(monkeypatch, {"MARK-1": marker, "MARK-2": deepcopy(marker)}, detections)
    if result["regions"]:
        region = result["regions"][0]
        assert "conflicting_measured_references" in region["warnings"]
        assert region["quality"]["calibration_marker_count"] == 1
        assert region["quality"]["status"] == "limited"
    else:
        assert any("calibration_failed" in warning for warning in result["warnings"])