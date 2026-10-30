"""Optional photo namespace schema, semantic references, and legacy isolation."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.document import Document
from model.photo_documentation import default_config, ensure_surfaces
from validate_hrp import validate_schema, validate_semantic


@pytest.fixture(scope="module")
def schema():
    jsonschema = pytest.importorskip("jsonschema")
    result = json.loads((ROOT / "hrp_schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(result)
    return result


def _raw():
    document = Document.from_dict({
        "canvas": {"elec_rooms": {"ER-1": [[0, 0], [100, 0], [100, 100], [0, 100]]},
                   "elec_points": {"AP-1": [50, 0]}},
        "params": {"elec_rooms": {"ER-1": {"room_id": "ER-1", "name": "Room"}},
                   "elec_points": {"AP-1": {"point_id": "AP-1", "name": "Socket"}}},
    })
    config = ensure_surfaces(document, default_config())
    config["photos"]["PHOTO-1"] = {
        "path": f"photos/{config['project_uuid']}/{'a' * 64}.jpg", "sha256": "a" * 64,
        "width": 1200, "height": 800, "exif_orientation": 1,
    }
    config["assignments"]["AP-1"] = "SURF-1"
    config["regions"]["REGION-1"] = {
        "photo_id": "PHOTO-1", "surface_id": "SURF-1", "marker_ids": [],
        "H": [[1, 0, 0], [0, -1, 0], [0, 0, 1]], "absolute_reference": False,
        "source_polygon": deepcopy(config["surfaces"]["SURF-1"]["source_polygon"]),
    }
    config["measurements"]["PM-1"] = {"region_id": "REGION-1", "points": [[10, 20], [30, 40]]}
    document.settings["photo_documentation"] = config
    return document.to_dict()


def test_complete_roundtrip_and_optional_export_fields(schema):
    raw = _raw()
    raw["pdf_export_pages"] = [{"id": "stickers", "type": "ap_stickers", "title": "Markers",
                                "documentation_mode": True, "include_references": False}]
    raw["params"]["photo_documentation"]["future_metadata"] = {"revision": 9}
    assert validate_schema(raw, schema) == []
    assert validate_semantic(raw) == ([], [])
    restored = Document.from_dict(json.loads(json.dumps(raw)))
    assert restored.settings["photo_documentation"] == raw["params"]["photo_documentation"]
    assert "photo_documentation" not in restored.view
    assert restored.to_dict().get("format_version") == raw.get("format_version")


@pytest.mark.parametrize("path", ["../escape.jpg", "/absolute.jpg", "C:/escape.jpg", "photos/../escape.jpg",
                                  "photos\\escape.jpg", "photos/./escape.jpg", "photos//escape.jpg", ""])
def test_schema_rejects_unsafe_photo_paths(schema, path):
    raw = _raw()
    raw["params"]["photo_documentation"]["photos"]["PHOTO-1"]["path"] = path
    assert any("path" in issue for issue in validate_schema(raw, schema))


@pytest.mark.parametrize("digest", ["bad", "g" * 64, "a" * 63, "a" * 65])
def test_schema_and_semantics_reject_invalid_hash(schema, digest):
    raw = _raw()
    raw["params"]["photo_documentation"]["photos"]["PHOTO-1"]["sha256"] = digest
    assert validate_schema(raw, schema)
    assert any("sha256" in issue for issue in validate_semantic(raw)[0])


@pytest.mark.parametrize("matrix", [[[1, 0], [0, 1]], [[0, 0, 0]] * 3,
                                     [[1, 0, 0], [0, 1, 0], [0, 0, float("nan")]],
                                     [[1, 0, 0], [0, 1, 0], [0, 0, float("inf")]]])
def test_invalid_homography_is_error(matrix):
    raw = _raw()
    raw["params"]["photo_documentation"]["regions"]["REGION-1"]["H"] = matrix
    assert validate_semantic(raw)[0]


@pytest.mark.parametrize("section,key,field,value", [
    ("assignments", "AP-1", None, "SURF-999"),
    ("regions", "REGION-1", "surface_id", "SURF-999"),
    ("regions", "REGION-1", "photo_id", "PHOTO-999"),
    ("regions", "REGION-1", "marker_ids", ["MARK-999"]),
    ("measurements", "PM-1", "region_id", "REGION-999"),
    ("surfaces", "SURF-1", "room_id", "ER-999"),
])
def test_unknown_references_are_errors(section, key, field, value):
    raw = _raw()
    entries = raw["params"]["photo_documentation"][section]
    if field is None:
        entries[key] = value
    else:
        entries[key][field] = value
    assert validate_semantic(raw)[0]


def test_stale_geometry_and_deleted_ap_assignment_only_warn(schema):
    raw = _raw()
    config = raw["params"]["photo_documentation"]
    config["surfaces"]["SURF-1"]["status"] = "stale"
    config["assignments"]["AP-999"] = "SURF-1"
    assert validate_schema(raw, schema) == []
    errors, warnings = validate_semantic(raw)
    assert errors == []
    assert any("stale" in warning for warning in warnings)
    assert any("AP-999" in warning and "deleted AP" in warning for warning in warnings)
    del raw["params"]["elec_rooms"]["ER-1"]
    for surface in config["surfaces"].values():
        surface["status"] = "stale"
    assert validate_semantic(raw)[0] == []


def test_historical_marker_for_deleted_ap_remains_valid(schema):
    raw = _raw()
    config = raw["params"]["photo_documentation"]
    config["dictionary"] = {"version": 1, "marker_size": 5, "max_correction_bits": 2,
                            "bytes_list": [[[0] * 4 for _row in range(4)]]}
    config["markers"]["MARK-1"] = {"id": "MARK-1", "point_id": "AP-999", "surface_id": "SURF-1",
                                   "aruco_id": 0, "side_mm": 80, "print_version": 1}
    assert validate_schema(raw, schema) == []
    assert validate_semantic(raw) == ([], [])
    config["markers"]["MARK-1"]["side_mm"] = 0
    assert validate_schema(raw, schema)
    assert any("side_mm" in issue for issue in validate_semantic(raw)[0])
    config["markers"]["MARK-1"]["side_mm"] = 80
    config["dictionary"]["bytes_list"] = [[[0] * 4 for _row in range(5)]]
    assert any("bytes_list" in issue for issue in validate_semantic(raw)[0])


def test_legacy_validator_does_not_import_optional_photo_dependencies():
    script = """
import sys
class NoPhotoDependencies:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'cv2', 'numpy'}:
            raise AssertionError('optional dependency imported: ' + fullname)
sys.meta_path.insert(0, NoPhotoDependencies())
from validate_hrp import validate_semantic
assert validate_semantic({'canvas': {}, 'params': {}}) == ([], [])
assert 'model.photo_documentation' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_photo_validation_does_not_import_cv2():
    script = """
import sys
class NoOpenCV:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'cv2':
            raise AssertionError('OpenCV imported during metadata validation')
sys.meta_path.insert(0, NoOpenCV())
from validate_hrp import validate_semantic
from model.photo_documentation import default_config
assert validate_semantic({'canvas': {}, 'params': {'photo_documentation': default_config()}}) == ([], [])
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_build_uses_targeted_photo_hooks(monkeypatch, tmp_path):
    import build
    from types import SimpleNamespace

    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "DIST", tmp_path / "dist")
    monkeypatch.setattr(build, "MAIN_PY", tmp_path / "main.py")
    monkeypatch.setattr(build.subprocess, "run", run)
    build.build_exe("photo-test")
    command = commands[0]
    assert any(command[index:index + 2] == ["--hidden-import", "cv2"] for index in range(len(command)))
    assert any(command[index:index + 2] == ["--hidden-import", "PIL.ImageOps"] for index in range(len(command)))
    assert not any(command[index:index + 2] == ["--collect-submodules", "cv2"] for index in range(len(command)))