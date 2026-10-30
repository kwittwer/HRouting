from copy import deepcopy
import json

import pytest

from model.document import Document
from model.photo_documentation import default_config, get_config

from model.elements import ElecPoint, ElecRoom
from model.photo_documentation import ensure_surfaces, validate_config
from logic.photo_geometry import nearest_surface


def test_read_does_not_mutate_and_metadata_roundtrips():
    document = Document()
    before = deepcopy(document.to_dict())
    config = get_config(document)
    assert document.to_dict() == before
    assert config["dictionary"] == {}
    config["photos"]["PHOTO-1"] = {"path": "photos/example.jpg", "sha256": "a" * 64}
    document.settings["photo_documentation"] = config
    restored = Document.from_dict(json.loads(json.dumps(document.to_dict())))
    assert get_config(restored) == config
    detached = get_config(restored)
    detached["photos"].clear()
    assert get_config(restored) == config


def test_default_configs_do_not_share_project_identity_or_containers():
    first = default_config()
    second = default_config()
    assert first["project_uuid"] != second["project_uuid"]
    first["markers"]["MARK-1"] = {}
    assert second["markers"] == {}


def room_document():
    document = Document()
    room = ElecRoom("ER-1", {"name": "Room", "floor_plan_id": ""})
    room.polygon = [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]
    document.add(room)
    return document, room


def test_surfaces_are_stable_but_changed_sources_create_new_ids():
    document, room = room_document()
    config = default_config()
    assert ensure_surfaces(document, config) is config
    original = deepcopy(config["surfaces"])
    assert len(original) == 6
    ensure_surfaces(document, config)
    assert config["surfaces"] == original
    room.polygon[1][0] += 100
    ensure_surfaces(document, config)
    assert len(config["surfaces"]) == 12
    for surface_id, surface in original.items():
        assert config["surfaces"][surface_id]["status"] == "stale"
        assert config["surfaces"][surface_id]["plan_points"] == surface["plan_points"]
    document.remove("ER-1")
    ensure_surfaces(document, config)
    assert all(value["status"] == "stale" for value in config["surfaces"].values())
    assert any("stale" in issue for issue in validate_config(config))


def test_nearest_surface_never_guesses_corner_or_distant_wall():
    document, room = room_document()
    config = ensure_surfaces(document, default_config())
    point = ElecPoint("AP-123", {"position": "Wand", "room_id": room.id})
    document.add(point)
    point.pos = [1000, 40]
    assert nearest_surface(document, config, point.id) == "SURF-1"
    point.pos = [20, 20]
    assert nearest_surface(document, config, point.id) == ""
    point.pos = [2000, 1500]
    assert nearest_surface(document, config, point.id) == ""
    point.position = "Boden"
    assert nearest_surface(document, config, point.id) == "SURF-5"
    point.position = "Decke"
    assert nearest_surface(document, config, point.id) == "SURF-6"


def test_polygon_room_resolution_is_ambiguous_on_shared_wall():
    document, room = room_document()
    other = ElecRoom("ER-2")
    other.polygon = [[4000, 0], [6000, 0], [6000, 3000], [4000, 3000]]
    document.add(other)
    point = ElecPoint("AP-1", {"position": "Wand"})
    point.pos = [4000, 1000]
    document.add(point)
    config = ensure_surfaces(document, default_config())
    assert nearest_surface(document, config, point.id) == ""
    point.room_id = room.id
    assert nearest_surface(document, config, point.id) == "SURF-2"


def test_invalid_room_polygon_does_not_create_surfaces():
    document, room = room_document()
    room.polygon = [[0, 0], [200, 200], [0, 200], [200, 0]]
    assert ensure_surfaces(document, default_config())["surfaces"] == {}


def test_validation_handles_invalid_references_and_nonfinite_values():
    document, room = room_document()
    config = ensure_surfaces(document, default_config())
    assert validate_config(config) == []
    config["assignments"]["AP-1"] = "SURF-999"
    config["rooms"][room.id]["ffb_offset_mm"] = float("nan")
    issues = validate_config(config)
    assert any("assignment" in issue for issue in issues)
    assert any("finite" in issue for issue in issues)


@pytest.mark.parametrize("field,value", [
    ("surfaces", {"SURF-1": {"kind": [], "source_polygon": None}}),
    ("regions", {"REGION-1": {"photo_id": [], "surface_id": [], "marker_ids": {}}}),
    ("measurements", {"PM-1": {"region_id": [], "points": None}}),
    ("markers", {"MARK-1": {"surface_id": [], "reference": 5}}),
    ("dictionary", [1, 2, 3]),
    ("photos", {"PHOTO-1": {"path": "../escape.jpg", "sha256": "bad"}}),
])
def test_validator_reports_malformed_payloads_without_crashing(field, value):
    config = default_config()
    config[field] = value
    assert validate_config(config)


def test_complete_photo_payload_roundtrip():
    document, _room = room_document()
    config = ensure_surfaces(document, default_config())
    config["photos"]["PHOTO-1"] = {
        "path": "photos/original.jpg", "original_name": "original.jpg",
        "sha256": "a" * 64, "width": 1200, "height": 800,
    }
    config["regions"]["REGION-1"] = {
        "photo_id": "PHOTO-1", "surface_id": "SURF-1", "marker_ids": [],
        "H": [[1, 0, 0], [0, -1, 0], [0, 0, 1]], "absolute_reference": False,
        "source_polygon": deepcopy(config["surfaces"]["SURF-1"]["source_polygon"]),
    }
    config["measurements"]["PM-1"] = {
        "region_id": "REGION-1", "name": "Cable", "points": [[10, 20], [30, 40]],
    }
    document.settings["photo_documentation"] = config
    restored = Document.from_dict(json.loads(json.dumps(document.to_dict())))
    assert get_config(restored) == config
    assert validate_config(config) == []
    assert "photo_documentation" not in restored.view


def test_nearest_surface_requires_current_geometry_even_before_refresh():
    document, room = room_document()
    config = ensure_surfaces(document, default_config())
    point = ElecPoint("AP-1", {"position": "Wand", "room_id": room.id})
    point.pos = [1000, 20]
    document.add(point)
    room.polygon[1][0] += 100
    assert nearest_surface(document, config, point.id) == ""


def test_validator_rejects_unrepresentable_numbers_without_overflow():
    config = default_config()
    config["markers"]["MARK-1"] = {"id": "MARK-1", "aruco_id": 0, "side_mm": 10 ** 400}
    assert any("finite" in issue for issue in validate_config(config))