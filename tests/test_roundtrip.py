"""Golden-Master-Tests: .hrp laden -> Document -> speichern muss verlustfrei sein."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.document import Document  # noqa: E402
from storage.hrp_io import load_raw, repair_and_save_hrp, save_raw  # noqa: E402
from storage.migration import migrate_raw  # noqa: E402

EXAMPLES = sorted((ROOT / "examples").glob("*.hrp"))


def _normalize(value):
    """Tupel/Listen angleichen, damit JSON-Roundtrips vergleichbar sind."""
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_document_roundtrip_is_lossless(example: Path):
    raw = load_raw(example)
    doc = Document.from_dict(raw)
    result = doc.to_dict()
    assert _normalize(result) == _normalize(raw)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_document_roundtrip_is_stable(example: Path):
    """Zweiter Durchlauf darf nichts mehr verändern."""
    doc = Document.from_dict(load_raw(example))
    first = doc.to_dict()
    second = Document.from_dict(first).to_dict()
    assert _normalize(first) == _normalize(second)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_saved_file_is_valid(example: Path, tmp_path: Path):
    from validate_hrp import (  # noqa: PLC0415
        _load_hrp,
        _load_schema,
        validate_schema,
        validate_semantic,
    )

    doc = Document.from_dict(load_raw(example))
    target = tmp_path / example.name
    save_raw(doc.to_dict(), target)

    data = _load_hrp(target)
    schema = _load_schema(ROOT / "hrp_schema.json")
    errors = validate_schema(data, schema)
    semantic_errors, _warnings = validate_semantic(data)
    assert not errors, errors
    assert not semantic_errors, semantic_errors


def test_migration_drops_legacy_ui_state():
    raw = migrate_raw({"params": {"_ui_state": {"main_window": {}}, "circuits": {}}})
    assert "_ui_state" not in raw["params"]


def test_migration_moves_global_helper_lines():
    raw = migrate_raw(
        {
            "canvas": {
                "global_helper_lines": {"H-1": [[0, 0], [1, 1]]},
                "floor_plans": [{"fp_id": "grundriss-1"}],
            }
        }
    )
    assert "global_helper_lines" not in raw["canvas"]
    assert raw["canvas"]["floor_helper_lines"]["grundriss-1"]["H-1"] == [[0, 0], [1, 1]]


def test_migration_normalizes_absolute_icon_paths():
    raw = migrate_raw(
        {
            "params": {
                "elec_points": {
                    "AP-1": {
                        "icon_path": r"C:\\Users\\demo\\AppData\\Local\\Temp\\_MEI123\\icons\\Steckdose.png"
                    },
                    "AP-2": {"icon_path": r"icons\\LAN_2fach.png"},
                },
                "hkv_points": {
                    "HKV-1": {"icon_path": r"\\\\server\\share\\icons\\Heizkreisverteiler.png"}
                },
            }
        }
    )
    assert raw["params"]["elec_points"]["AP-1"]["icon_path"] == "icons/Steckdose.png"
    assert raw["params"]["elec_points"]["AP-2"]["icon_path"] == "icons/LAN_2fach.png"
    assert raw["params"]["hkv_points"]["HKV-1"]["icon_path"] == "icons/Heizkreisverteiler.png"


def test_load_raw_planung_linda_has_no_absolute_icon_paths():
    example = ROOT / "examples" / "Planung_Linda.hrp"
    if not example.exists():
        pytest.skip("Planung_Linda.hrp fehlt")

    absolute_re = re.compile(r"^(?:[a-zA-Z]:[\\/]|\\\\|/)")
    raw = load_raw(example)
    params = raw.get("params") or {}

    for bucket_name in ("elec_points", "hkv_points"):
        bucket = params.get(bucket_name) or {}
        for entry in bucket.values():
            icon_path = str((entry or {}).get("icon_path", "") or "")
            assert not absolute_re.match(icon_path), icon_path


def test_migration_maps_legacy_ids_and_ap_name_refs():
    raw = migrate_raw(
        {
            "canvas": {
                "elec_cables": {"KV-1": [[1, 2], [3, 4]]},
                "cable_start_ap": {"KV-1": "Steckdose Küche"},
                "cable_end_ap": {"KV-1": "AP-2"},
                "elec_cable_notes": {"KV-1": "legacy"},
                "elec_rooms": {"R-1": [[0, 0], [1, 0], [1, 1]]},
                "elec_room_visible": {"R-1": True},
            },
            "params": {
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "name": "Steckdose Küche"},
                    "AP-2": {"point_id": "AP-2", "name": "Leuchte"},
                    "AP-UV": {
                        "point_id": "AP-UV",
                        "name": "UV",
                        "uv_config": {"slots": [{"slot": 1, "cable": "KV-1"}]},
                        "up_distribution_config": {
                            "incoming_cable_id": "KV-1",
                            "outgoing_cable_ids": ["KV-1"],
                            "mappings": [{"to_cable_id": "KV-1", "from": "L1", "to": "L1"}],
                        },
                    },
                },
                "elec_cables": {
                    "KV-1": {
                        "cable_id": "KV-1",
                        "start_ap": "Steckdose Küche",
                        "end_ap": "AP-2",
                    }
                },
                "elec_rooms": {"R-1": {"room_id": "R-1", "name": "Küche"}},
            },
        }
    )

    assert "EK-1" in raw["params"]["elec_cables"]
    assert raw["params"]["elec_cables"]["EK-1"]["cable_id"] == "EK-1"
    assert raw["params"]["elec_cables"]["EK-1"]["start_ap"] == "AP-1"
    assert raw["params"]["elec_cables"]["EK-1"]["end_ap"] == "AP-2"

    assert "ER-1" in raw["params"]["elec_rooms"]
    assert raw["params"]["elec_rooms"]["ER-1"]["room_id"] == "ER-1"

    assert "EK-1" in raw["canvas"]["elec_cables"]
    assert "EK-1" in raw["canvas"]["cable_start_ap"]
    assert raw["canvas"]["cable_start_ap"]["EK-1"] == "AP-1"
    assert raw["canvas"]["cable_end_ap"]["EK-1"] == "AP-2"
    assert "ER-1" in raw["canvas"]["elec_rooms"]
    assert "ER-1" in raw["canvas"]["elec_room_visible"]

    uv = raw["params"]["elec_points"]["AP-UV"]["uv_config"]
    up = raw["params"]["elec_points"]["AP-UV"]["up_distribution_config"]
    assert uv["slots"][0]["cable"] == "EK-1"
    assert up["incoming_cable_id"] == "EK-1"
    assert up["outgoing_cable_ids"] == ["EK-1"]
    assert up["mappings"][0]["to_cable_id"] == "EK-1"


def test_migration_resolves_duplicate_ap_names_by_cable_geometry():
    raw = migrate_raw(
        {
            "canvas": {
                "elec_points": {
                    "AP-1": [0, 0],
                    "AP-2": [100, 0],
                    "AP-3": [200, 0],
                },
                "elec_cables": {"KV-1": [[95, 0], [205, 0]]},
            },
            "params": {
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "name": "Dose", "floor_plan_id": "grundriss-1"},
                    "AP-2": {"point_id": "AP-2", "name": "Dose", "floor_plan_id": "grundriss-1"},
                    "AP-3": {"point_id": "AP-3", "name": "Leuchte", "floor_plan_id": "grundriss-1"},
                },
                "elec_cables": {
                    "KV-1": {
                        "cable_id": "KV-1",
                        "floor_plan_id": "grundriss-1",
                        "start_ap": "Dose",
                        "end_ap": "Leuchte",
                    }
                },
            },
        }
    )

    cable = raw["params"]["elec_cables"]["EK-1"]
    assert cable["start_ap"] == "AP-2"
    assert cable["end_ap"] == "AP-3"


def test_id_allocator_continues_after_existing_ids():
    from model.ids import IdAllocator  # noqa: PLC0415

    alloc = IdAllocator()
    alloc.observe_all(["HK-1", "HK-7", "AP-3"])
    assert alloc.next_id("HK") == "HK-8"
    assert alloc.next_id("AP") == "AP-4"
    assert alloc.next_id("EK") == "EK-1"


def test_layers_in_use_only_reports_existing_elements():
    from model.layers import LayerId  # noqa: PLC0415

    doc = Document.from_dict(
        {
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "circuits": {
                    "HK-1": {"circuit_id": "HK-1", "floor_plan_id": "grundriss-1"}
                },
            },
        }
    )
    used = doc.layers_in_use("grundriss-1")
    assert LayerId.HEATING in used
    assert LayerId.ELECTRICAL not in used


def test_measurements_roundtrip_is_lossless():
    raw = {
        "canvas": {
            "floor_plans": [{"fp_id": "grundriss-1"}],
            "distance_measurements": {
                "MSRD-1": [[10.0, 20.0], [210.0, 20.0]],
            },
            "distance_label_positions": {
                "MSRD-1": [220.0, 12.0],
            },
            "angle_measurements": {
                "MSRA-1": [[20.0, 20.0], [80.0, 20.0], [80.0, 90.0]],
            },
            "angle_label_positions": {
                "MSRA-1": [92.0, 84.0],
            },
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG"}},
            "distance_measurements": {
                "MSRD-1": {
                    "measurement_id": "MSRD-1",
                    "floor_plan_id": "grundriss-1",
                    "name": "Abstand Tür",
                    "color": "#00e5ff",
                    "stroke_width": 2.0,
                    "line_style": "dashdot",
                    "text_size": 10.0,
                    "auto_label_pos": False,
                    "visible": True,
                }
            },
            "angle_measurements": {
                "MSRA-1": {
                    "measurement_id": "MSRA-1",
                    "floor_plan_id": "grundriss-1",
                    "name": "Winkel Nische",
                    "color": "#00e5ff",
                    "stroke_width": 2.0,
                    "line_style": "dashdot",
                    "text_size": 10.0,
                    "auto_label_pos": True,
                    "visible": True,
                }
            },
        },
    }

    doc = Document.from_dict(raw)
    result = doc.to_dict()
    canvas = result.get("canvas", {})
    params = result.get("params", {})

    assert _normalize(canvas.get("distance_measurements", {})) == _normalize(
        raw["canvas"]["distance_measurements"]
    )
    assert _normalize(canvas.get("distance_label_positions", {})) == _normalize(
        raw["canvas"]["distance_label_positions"]
    )
    assert _normalize(canvas.get("angle_measurements", {})) == _normalize(
        raw["canvas"]["angle_measurements"]
    )
    assert _normalize(canvas.get("angle_label_positions", {})) == _normalize(
        raw["canvas"]["angle_label_positions"]
    )

    assert _normalize(params.get("distance_measurements", {})) == _normalize(
        raw["params"]["distance_measurements"]
    )
    assert _normalize(params.get("angle_measurements", {})) == _normalize(
        raw["params"]["angle_measurements"]
    )


def test_repair_and_save_hrp_creates_backup_and_cleans_orphans(tmp_path: Path):
    raw = {
        "canvas": {
            "floor_plans": [{"fp_id": "grundriss-1"}],
            "polygons": {
                "HK-1": [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]],
                "HK-999": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]],
            },
            "supply_hkv": {"HK-999": "HKV-1"},
            "cable_start_ap": {"EK-1": "AP-404"},
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
            "floorplans_order": ["grundriss-404", "grundriss-1"],
            "circuits": {
                "HK-1": {
                    "circuit_id": "HK-1",
                    "floor_plan_id": "grundriss-1",
                    "name": "Wohnzimmer",
                }
            },
            "elec_points": {
                "AP-1": {"point_id": "AP-1", "floor_plan_id": "grundriss-1", "name": "Dose"}
            },
            "elec_cables": {
                "EK-1": {
                    "cable_id": "EK-1",
                    "floor_plan_id": "grundriss-1",
                    "start_ap": "AP-404",
                    "end_ap": "AP-1",
                }
            },
            "hkv_points": {"HKV-1": {"hkv_id": "HKV-1", "floor_plan_id": "grundriss-404"}},
            "hkv_lines": {},
            "elec_rooms": {},
            "text_annotations": {},
            "furniture": {},
        },
    }

    source = tmp_path / "broken.hrp"
    save_raw(raw, source)
    original = json.loads(source.read_text(encoding="utf-8"))

    repaired, changes, backup_path, written = repair_and_save_hrp(source)

    assert written == source
    assert backup_path is not None and backup_path.exists()
    backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
    assert _normalize(backup_data) == _normalize(original)

    assert "HK-999" not in repaired["canvas"]["polygons"]
    assert "HK-999" not in repaired["canvas"].get("supply_hkv", {})
    assert repaired["params"]["elec_cables"]["EK-1"]["start_ap"] == ""
    assert repaired["canvas"]["cable_start_ap"]["EK-1"] == ""
    assert repaired["params"]["floorplans_order"] == ["grundriss-1"]
    assert changes


def test_repair_and_save_hrp_writes_to_output_path(tmp_path: Path):
    raw = {
        "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "visible": True, "file_path": ""}},
            "circuits": {},
            "elec_points": {},
            "elec_rooms": {},
            "elec_cables": {},
            "hkv_points": {},
            "hkv_lines": {},
            "text_annotations": {},
            "furniture": {},
        },
    }
    source = tmp_path / "input.hrp"
    target = tmp_path / "output.hrp"
    save_raw(raw, source)

    _repaired, _changes, backup_path, written = repair_and_save_hrp(
        source,
        output_path=target,
        backup=True,
    )

    assert written == target
    assert target.exists()
    assert backup_path is not None and backup_path.exists()
    assert source.exists()


def test_json_serializable():
    doc = Document.from_dict(load_raw(EXAMPLES[0])) if EXAMPLES else Document()
    json.dumps(doc.to_dict(), ensure_ascii=False)


def test_project_overview_data_planung_linda():
    from model.computed import project_overview_data  # noqa: PLC0415

    example = ROOT / "examples" / "Planung_Linda.hrp"
    if not example.exists():
        pytest.skip("Planung_Linda.hrp fehlt")

    doc = Document.from_dict(load_raw(example))
    data = project_overview_data(doc)

    assert "general" in data
    assert "heating_rows" in data
    assert "hkv_rows" in data
    assert "materials" in data

    rows = data["heating_rows"]
    assert len(rows) > 0, "Keine Heizkreise gefunden"

    required_keys = {
        "id", "name", "route_m", "supply_m", "total_m",
        "area_m2", "power_w", "volume_flow_lmin", "pressure_drop_mbar",
        "kv_value",
    }
    for row in rows:
        missing = required_keys - row.keys()
        assert not missing, f"Fehlende Schlüssel in Zeile: {missing}"

    materials = data["materials"]
    assert "circuit_count" in materials
    assert materials["circuit_count"] == len(rows)
    assert materials.get("valve_count", 0) == materials["circuit_count"]

    # Summen müssen sinnvoll sein (Planung_Linda hat Heizkreise mit Polygonen)
    total_power = sum(r.get("power_w", 0.0) for r in rows)
    assert total_power >= 0.0
